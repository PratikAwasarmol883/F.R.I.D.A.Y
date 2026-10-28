# Milestone 3 Spec — Durable Task Worker

**Status:** Implemented (see section 16 for deviations and what was not verified).
**Source:** `architecture.md` §3 (topology), §4.2 (long-running flow), §6 (worker), §9 (task lifecycle), §11, §13 (Milestone 3); `plan.md` Phase 4.
**Builds on:** Milestones 1–2 (auth and device scopes, `ask`, tool loop, budget, audit, `tool_executions`).

## 1. Goal

A user can submit work that outlives the HTTP request. A separate worker process claims it, runs it with bounded time and retries, survives crashes and restarts, honors cancellation, and records progress. The client can disconnect and fetch the result later. Submitting the same request twice does not run it twice.

The first (and only) task type is `assistant.ask`: the existing Milestone 1–2 assistant flow run in the background. The framework is generic so Milestone 4 (research) plugs in as a new handler, but no other handler is built now.

**Done when (from the docs):**
- A long task continues after the client disconnects, and its result is retrievable later.
- Task state survives a worker restart or crash (recovery is tested).
- Duplicate submission is idempotent; duplicate *execution* cannot produce duplicate effects.
- Cancellation, retries, and timeouts behave per the state machine and are visible in task events.
- The synchronous `POST /assistant/ask` behaves exactly as in Milestone 2.

## 2. Decisions needed from you

Each has a recommendation; the rest of the spec assumes the recommended option.

| # | Decision | Recommendation | Alternative |
|---|---|---|---|
| D1 | Queue technology | **PostgreSQL as the queue**: `FOR UPDATE SKIP LOCKED` claims with expiring leases (matches `architecture.md`; one datastore, transactional with results) | Redis + Celery/RQ (new infrastructure); Prefect (architecture reserves it for complex multi-step flows later) |
| D2 | Task types in M3 | **`assistant.ask` only**, behind a handler registry; test-only handlers live in the test suite and are never registered in production | Also ship a demo/sleep task type (dead code in production) |
| D3 | Worker model | **Separate process** (`python -m friday.worker`, its own Compose service), asyncio concurrency 2 by default | In-process background tasks in the API (dies with the API; violates the architecture) |
| D4 | Retries | **3 attempts**, exponential backoff 5 s → 120 s with jitter; retry only `provider_unavailable`, `provider_timeout`, `worker_lost`. A runtime timeout is **terminal** (`timed_out`), not retried | Retry everything / retry timeouts (can repeat expensive runs) |
| D5 | Idempotency | **`Idempotency-Key` header**, unique per user; same key + same payload → return the existing task (200); same key + different payload → `409 IDEMPOTENCY_CONFLICT`. No key → no deduplication | Server-side dedup by payload hash (surprising for legitimate repeats) |
| D6 | Cancellation | **Cooperative plus interrupt**: queued → cancelled immediately; running → `cancel_requested`, worker detects it at boundaries and on each heartbeat and cancels the in-flight handler | Cooperative only (a stuck LLM call blocks cancellation up to its timeout) |
| D7 | Revocation and scopes | **Tasks run with the creating device's *current* state**: a revoked device's queued tasks are cancelled (`device_revoked`); removed scopes take effect immediately | Snapshot scopes at creation (a revoked device's work would keep running) |
| D8 | Retention | **Purge terminal tasks and their events after 30 days** (`TASK_RETENTION_DAYS`); conversations and messages are untouched | Keep forever (task input, including text of failed requests, would persist indefinitely) |

## 3. Scope

**In:** task state machine; `tasks` and `task_events` tables and `tool_executions.task_id` (migration `0004`); leases, heartbeats, fencing, and crash recovery; the worker process; retries with backoff; cooperative-plus-interrupt cancellation; per-task runtime timeout; `Idempotency-Key`; per-user active-task limit; `/tasks` API (create, read, cancel, events); `assistant.ask` handler; operator CLI (`tasks`, `requeue-task`); Compose worker service; retention purge; shared composition root for API and worker; tests; CI stays green.

**Out (deliberately deferred):**
- Scheduler and recurring jobs (M6), research task type and reports (M4), approval pauses and `awaiting_approval` (M7; that state is added with its migration then).
- Streaming (SSE/WebSocket), push notifications, `GET /tasks` listing, priorities or per-user fairness, multiple queues, `LISTEN/NOTIFY` wake-ups (polling is sufficient), metrics and alerting, auto-promoting slow `ask` calls to tasks.
- Recording token usage for failed attempts (see risks).

## 4. State machine (`domain/task_state.py`, pure and fully unit-tested)

```
queued ──claim──▶ running ──▶ succeeded
  ▲                 │ ├────▶ failed            (terminal failure or retries exhausted)
  │                 │ ├────▶ timed_out         (runtime limit exceeded)
  │  retry/requeue  │ └────▶ cancel_requested ──▶ cancelled
  └─────────────────┘
queued ──cancel──▶ cancelled
```
Terminal: `succeeded`, `failed`, `timed_out`, `cancelled`. Transitions are validated by one function used by every writer; an illegal transition is a bug and raises. A retry is `running → queued` with `attempt` preserved and `run_after` in the future (no separate "retrying" state). Operator `requeue-task` may move `failed`/`timed_out` back to `queued` (attempts reset, audited); no other path leaves a terminal state.

## 5. Data model (migration `0004`)

| Table | Columns | Constraints / indexes |
|---|---|---|
| `tasks` | id (uuid), user_id → users, device_id → devices (SET NULL), type, status, input (jsonb), result (jsonb, nullable), error_code (nullable), idempotency_key (nullable), request_fingerprint (char 64), attempt, max_attempts, max_runtime_seconds, run_after, lease_owner (nullable), lease_expires_at (nullable), heartbeat_at (nullable), cancel_requested_at (nullable), started_at, finished_at, event_seq (int, per-task counter), created_at, updated_at | check(status in …), check(attempt ≥ 0), check(max_attempts ≥ 1); **partial index** `(run_after, created_at) WHERE status='queued'` for claims; **partial index** `(lease_expires_at) WHERE status IN ('running','cancel_requested')` for the reaper; index `(user_id, created_at DESC)`; **partial unique** `(user_id, idempotency_key) WHERE idempotency_key IS NOT NULL` |
| `task_events` | id, task_id → tasks (CASCADE), sequence (int), event_type, payload (jsonb, ≤ 1 KB), created_at | unique `(task_id, sequence)` |
| `tool_executions.task_id` | nullable FK → tasks (SET NULL) | index |

- `result` for `assistant.ask` stores references and metadata only: `{conversation_id, message_id, usage, sources, tools_used}`. The answer text stays in `messages`; the API joins it (owner-scoped) when returning a task.
- `input` holds the request (message text); it has the same sensitivity as `messages` and is covered by retention (D8).
- Event `sequence` comes from an atomic `UPDATE tasks SET event_seq = event_seq + 1 … RETURNING`, so concurrent writers never collide.
- All lease timing uses **database time** (`now()`), never worker clocks, to avoid skew.

Event types (payloads contain only IDs, counts, codes, durations, tool names and statuses; never message text, queries, results, or secrets): `created`, `claimed`, `progress`, `retry_scheduled`, `cancel_requested`, `lease_expired_requeued`, `lease_lost`, `requeued_by_operator`, `succeeded`, `failed`, `timed_out`, `cancelled`.

## 6. Claims, leases, and fencing (`persistence/repositories/tasks.py`)

- **Claim:** one statement selects up to *k* rows `WHERE status='queued' AND run_after <= now() ORDER BY run_after, created_at FOR UPDATE SKIP LOCKED`, and sets `status='running'`, `attempt = attempt+1`, `lease_owner = :worker`, `lease_expires_at = now()+lease`, `started_at`, `heartbeat_at`. Concurrent workers never claim the same row.
- **Pre-flight at claim time:** if the creating device is revoked (or the user is gone) the task is cancelled with `device_revoked` instead of running (D7).
- **Heartbeat:** every `TASK_HEARTBEAT_SECONDS` the worker extends `lease_expires_at` *and* reads `cancel_requested_at`, in a single fenced `UPDATE … RETURNING`.
- **Fencing token = `(lease_owner, attempt)`.** Every worker write (heartbeat, event, finish, fail, retry) is conditional on `lease_owner = :me AND attempt = :n AND status IN ('running','cancel_requested')`. Zero rows updated means the lease was lost: the worker cancels its handler, discards its result, and emits `lease_lost`.
- **Effectively-once effects for `assistant.ask`:** the handler's final database transaction (conversation, messages, usage, audit, linking tool executions) also locks and fences the task row and marks it `succeeded` *in the same transaction*. If the lease was lost or the task cancelled, that transaction rolls back, so a stale worker cannot write a second answer after another worker took over.
- **Reaper** (runs in every worker every `TASK_HEARTBEAT_SECONDS`, `SKIP LOCKED`, so multiple reapers are safe): tasks whose lease expired and `cancel_requested_at IS NOT NULL` → `cancelled`; `attempt < max_attempts` → `queued` with backoff and event `lease_expired_requeued`; otherwise → `failed` with `worker_lost`.
- **Retention purge** (same loop, hourly): delete terminal tasks older than `TASK_RETENTION_DAYS`; events cascade.

## 7. Worker (`friday/workers/`, entry `python -m friday.worker`)

- A loop claims up to `TASK_WORKER_CONCURRENCY − running` tasks every `TASK_POLL_INTERVAL_SECONDS` (± jitter), starts one asyncio task per claim, and for each runs: **handler** + **heartbeat/cancel watcher** + **runtime timeout** (`max_runtime_seconds`, default 300).
- Outcome mapping: handler returns → success was already committed atomically by the handler (§6); `TimeoutError` → `timed_out`; cancel detected → `cancelled`; domain errors map to stable codes below.
- **Retry classification:** `provider_unavailable`, `provider_timeout`, and lease expiry (`worker_lost`) are retryable while `attempt < max_attempts`; backoff `min(120, 5·2^(attempt−1)) × jitter(0.5–1.0)` seconds. Everything else is terminal. Unknown exceptions → `failed` / `internal_error` (logged with traceback server-side, never exposed).
- **Stable error codes:** `provider_unavailable`, `provider_timeout`, `provider_error`, `budget_exceeded`, `not_found`, `device_revoked`, `invalid_input`, `timed_out`, `worker_lost`, `internal_error`. Clients get the code plus a fixed safe sentence, never internals.
- **Graceful shutdown (SIGTERM/SIGINT):** stop claiming; wait up to `TASK_SHUTDOWN_GRACE_SECONDS` (30); tasks still running are released back to `queued` with the attempt **not** consumed (a deploy is not a failure), fenced.
- **Event-loop discipline:** handlers must be fully async (a blocking call would starve heartbeats and lose the lease); the worker logs a warning when loop lag exceeds one heartbeat interval.
- The worker builds the same dependencies as the API through a shared composition root (`friday/composition.py`: settings, session factory, LLM provider, tool registry). The API's `Depends` functions delegate to it; behavior is unchanged.

## 8. `assistant.ask` handler and `AssistantService` hooks

- Input model (strict): `{message: 1–8000 chars after trimming, conversation_id?: uuid}`, validated at submission and again when executed.
- The handler reconstructs the caller from the task row (`user_id`, `device_id`, the device's **current** scopes) and calls the existing `AssistantService.ask` with three optional hooks; the synchronous route passes none, so Milestone 1–2 behavior is byte-for-byte the same:
  - `progress(event_type, payload)` → task events (e.g. model call started, tool call finished with name and status).
  - `check_cancelled()` → consulted before every model call and every tool call; raises `TaskCancelled`.
  - `before_commit(session)` → runs inside the final transaction to fence the lease and mark the task `succeeded` atomically (§6).
- Tool executions made by the task carry `task_id`. A retried attempt re-runs read-only tools (extra search quota is possible and visible in `tool_executions`); no user-visible state is written until the atomic finish.
- Provider/budget/not-found errors map to the codes in §7. The user's message is never stored in `messages` for a task that does not succeed (same rule as Milestone 1); it remains only in `tasks.input` until retention purges it.

## 9. API (all under `/api/v1`, authenticated, owner-scoped in SQL, other users get 404)

| Method | Path | Behavior |
|---|---|---|
| `POST` | `/tasks` | Body `{type: "assistant.ask", input: {…}}`; optional `Idempotency-Key` header (1–128 chars `[A-Za-z0-9._:-]`). **202** with the new task; **200** with the existing task on an idempotent replay; `409 IDEMPOTENCY_CONFLICT` if the key was used with a different payload; `429 TASK_LIMIT` when the user already has `MAX_ACTIVE_TASKS_PER_USER` (5) queued or running tasks; `422` for unknown type or invalid input. |
| `GET` | `/tasks/{id}` | Status, attempt, timestamps, `cancel_requested`, and for `succeeded` the result including `answer`, `sources`, `tools_used`, `usage`; for failures `error_code` and a safe message. |
| `POST` | `/tasks/{id}/cancel` | Queued → `cancelled` (200). Running → `cancel_requested` (202). Already `cancel_requested`/`cancelled` → 200 (idempotent). Other terminal states → `409 TASK_NOT_CANCELLABLE`. |
| `GET` | `/tasks/{id}/events` | Ordered events; query `after_sequence` (default 0) and `limit` (default 100, max 200); returns `next_after_sequence`. Polling only. |

- The idempotency fingerprint is SHA-256 over `(type, canonical input)`. Concurrent duplicate submissions are resolved by the partial unique index (`INSERT … ON CONFLICT DO NOTHING`, then read), never by check-then-insert.
- Submission checks the task limit and creates the task and its `created` event plus an audit row in one transaction. Client disconnect never cancels a task.
- Error envelope, request IDs, and auth behavior are unchanged from Milestone 1.

## 10. CLI, Compose, and configuration

- CLI: `tasks --user-id [--status] [--limit]` (failed-task inspection; prints IDs, status, attempt, error code, never input text) and `requeue-task --task-id` (terminal `failed`/`timed_out` only; resets attempts; writes an audit event).
- Compose: new `friday-worker` service (same image, command `python -m friday.worker`, depends on a healthy database, `restart: unless-stopped`, `stop_grace_period` ≥ shutdown grace).
- Settings, all validated at startup (lease must be greater than 2× heartbeat): `TASK_WORKER_CONCURRENCY` (2), `TASK_POLL_INTERVAL_SECONDS` (2), `TASK_LEASE_SECONDS` (60), `TASK_HEARTBEAT_SECONDS` (10), `TASK_MAX_ATTEMPTS` (3), `TASK_MAX_RUNTIME_SECONDS` (300), `TASK_RETRY_BASE_SECONDS` (5), `TASK_RETRY_MAX_SECONDS` (120), `MAX_ACTIVE_TASKS_PER_USER` (5), `TASK_RETENTION_DAYS` (30), `TASK_SHUTDOWN_GRACE_SECONDS` (30). Placeholders only in `.env.example`.

## 11. Security and privacy

- Task endpoints reuse bearer auth; every query includes `user_id`; cross-user access returns 404.
- Tasks never run with more privilege than their creator currently has (D7); scope removal and device revocation apply to queued and retried work.
- Existing protections apply unchanged inside tasks: tool policy, sanitization, query guard, daily token budget, per-ask limits, and audit. Per-user active-task limit and the attempt cap bound worst-case spend from a runaway client.
- Events and audit rows exclude message text, queries, results, tokens, and keys; a test scans for this.
- Failed or cancelled tasks do not leave user text in `messages`; `tasks.input` is bounded by retention (D8).

## 12. Code layout

```
friday/
  domain/        task_state.py (states, transitions, error codes; pure)
  composition.py (settings, sessionmaker, LLM provider, tool registry)
  persistence/   models/{task.py, task_event.py}  repositories/tasks.py
  services/      task_service.py (create/get/cancel/events)  assistant_service.py (hooks)
  workers/       worker.py (loop, claim, heartbeat, reaper, retention, shutdown)
                 runner.py (one task: handler + watcher + timeout)
                 handlers/{base.py, assistant_ask.py, registry.py}
  api/routes/    tasks.py
  worker.py      (entry point)
```
Layering: routes → services → repositories; workers → services/handlers → existing assistant service; the state machine has no I/O.

## 13. Tests (acceptance mapping)

All against real Postgres. Time-dependent behavior is driven by editing lease/`run_after` values in the database or by injecting short intervals, not by sleeping.

| Area | Cases |
|---|---|
| State machine | every legal transition accepted, every illegal one rejected; terminal states stay terminal; operator requeue is the only exit |
| Claims | concurrent workers claim each task exactly once (`gather` over separate connections); `run_after` respected; ordering; capacity limit; revoked device → cancelled `device_revoked` without running |
| Leases and fencing | heartbeat extends lease and reports cancel; expired lease requeued with attempt preserved, then failed `worker_lost` when attempts are exhausted; **stale worker** (lease reaped and task reclaimed) cannot heartbeat, write events, finish, or fail, and its final transaction rolls back so exactly one answer pair exists |
| Crash/restart recovery | a worker is abandoned mid-run (handler never finishes), lease expires, a fresh worker completes it; conversation has exactly one answer; events show the recovery |
| Client disconnect | task created over HTTP and the client goes away; worker completes it; later `GET` returns answer, sources, and usage |
| Idempotency | same key twice → one row, second response 200 with the same id; same key different payload → 409; 10 concurrent duplicates → one row; same key across users independent; no key → separate tasks |
| Cancellation | queued → cancelled immediately and never claimed; running → `cancel_requested` → in-flight handler interrupted → `cancelled`, nothing stored in `messages`; cancel on terminal → 409; repeat cancel → 200; other user → 404; cancel racing with completion has exactly one winner |
| Retries and timeouts | provider unavailable twice then success → `succeeded` on attempt 3 with `retry_scheduled` events and growing `run_after`; exhaustion → `failed` with the last code; non-retryable codes fail at once; runtime timeout → `timed_out`, no retry; budget exceeded → `failed budget_exceeded` |
| Limits | active-task cap → 429 `TASK_LIMIT`; terminal tasks free capacity |
| Ownership and validation | other user's GET/cancel/events → 404; unknown type, bad input, oversized input, bad idempotency key → 422 envelope |
| Events | sequences unique and gapless under concurrent writers; pagination via `after_sequence`; payload ≤ 1 KB; no message text, queries, tokens, or secrets |
| Audit | rows for create, cancel, finish, and requeue; tool executions carry `task_id` |
| Authorization | scope removed after creation → tools not advertised on execution; device revoked → never runs; tasks never exceed the creator's current scopes; M2 injection scenarios pass when run as tasks |
| Shutdown | worker stop within grace lets the task finish; beyond grace the task returns to `queued` with `attempt` not consumed |
| Retention | old terminal tasks purged with their events; active tasks and conversations untouched |
| Regression | synchronous `ask` and all Milestone 1–2 tests unchanged |
| CLI | `tasks` lists without input text; `requeue-task` accepts only failed/timed-out tasks |
| Migration | `0004` up/down/up |
| Opt-in live | with a real Anthropic key, submit an `assistant.ask` task through a running Compose worker (not run in CI; I cannot run it without a key) |

**Commands to pass before done:** `ruff check`, `ruff format --check`, `mypy friday`, `pytest`, migration up/down/up, Docker Compose walkthrough (API + worker + database), CI green. The Compose walkthrough can show submit → retry → failure and a worker kill and restart during backoff without keys; a full success path needs a key.

## 14. Risks and mitigations

- **At-least-once execution:** leases can expire while a worker is slow, so a task may run twice. Mitigation: fencing on every write and the atomic fenced finish, so duplicate *state* effects are impossible; duplicate read-only tool calls are possible, visible, and bounded.
- **Uncounted spend on failed attempts:** usage is recorded only on success (as in Milestones 1–2), so retries can spend tokens the budget does not see. Mitigation here: attempt cap, per-user active-task cap, retry only for provider outages. Recommended follow-up: record usage per model call.
- **Heartbeat starvation / lost leases from blocking code:** async-only handlers, loop-lag warning, lease greater than 2× heartbeat.
- **Poison tasks and head-of-line blocking:** attempt cap, terminal timeout, concurrency greater than 1, operator CLI. No per-user fairness yet.
- **Polling load and latency:** 2 s interval with jitter and a partial index; `LISTEN/NOTIFY` deferred until measured.
- **Privacy of queued input:** same sensitivity as messages; retention purge; failed requests never enter `messages`.
- **Clock skew:** all lease logic uses database time.
- **Scope creep toward scheduling, research, approvals:** explicitly deferred to M6, M4, M7.

## 15. Implementation order (each step ends green on CI)

1. State machine, error-code mapping, settings with validation (pure code and unit tests).
2. Migration `0004`, models, repositories (claim, fence, heartbeat, reap, events, purge) with real-database concurrency tests.
3. `TaskService` and `/tasks` API: create, idempotency, limits, get, cancel, events (no worker yet; tests drive state directly).
4. Composition root; `AssistantService` hooks; `assistant.ask` handler.
5. Worker runtime: claim loop, heartbeat and cancel watcher, timeout, retry, reaper, shutdown; crash, fencing, and cancellation tests.
6. CLI commands, Compose worker service, README and docs, retention, Docker walkthrough, CI.

## 16. Implementation notes, deviations, and what was not verified

**Deviations from this spec**
- The state machine also allows `running -> cancelled`, reachable only through a fenced write by the lease holder; it implements the claim-time pre-flight that cancels a task whose creating device was revoked (`device_revoked`). User cancellation of a running task still goes through `cancel_requested`.
- `AssistantService` takes two hooks instead of the single `before_commit`: `lock_for_finish` (take the task row lock and check fence/cancel before any write) and `finish` (mark success in the same transaction). The early lock is defense in depth: the fenced `finish` at the end of the same transaction already rolls everything back if the lease was lost or a cancel won.
- A lost lease is logged but not written as a `lease_lost` event, because every task write is fenced and a worker that lost the lease is by definition not allowed to write. A `released` event type was added for graceful shutdown.
- Progress events use the `progress` event type with the step name in the payload (`model_call`, `tool_call`).
- A task that is queued for retry keeps the last failure in `error_code` so clients can see why it is waiting.
- Conversation ownership is validated at submission (404) instead of failing later as `not_found`.
- `POST /tasks` input is validated by one shared model (`domain/task_inputs.py`) at submission and again at execution. A handler that returns without marking success is failed as `internal_error` instead of being left running.
- `GET /tasks` (listing) remains deferred; operators use `python -m friday.cli tasks`.

**Verified:** state machine (exhaustive transition table); claims under concurrent workers; fencing (stale workers cannot heartbeat, emit events, finish, fail, retry, or release; a lease stolen or reaped-and-reclaimed during a model call writes nothing); reaper outcomes and concurrent reapers; retry bounds and backoff; runtime timeout; cancellation of queued and running tasks, including cancel-versus-completion races with exactly one winner; graceful shutdown inside and beyond the grace period (attempt not consumed); revoked devices; scope changes after submission; idempotency including 10 concurrent duplicates; per-user limits; owner-scoped reads; event privacy; retention purge; migration backfill; a real crash test in which a worker subprocess is killed with SIGKILL mid-task and a second worker completes it exactly once; and mutation checks showing the suite fails when each safety control is removed.

**Not verified:** a successful end-to-end task with a live Anthropic key through the Compose worker (no key was available; the offline suite uses fake providers), behavior under sustained load or many workers beyond the tested cases, and multi-host clock skew (all lease logic uses database time by design).

**Known limitations (unchanged from the risks section):** token usage of failed attempts is not recorded (retries can spend tokens the budget does not see; bounded by the attempt cap and per-user active-task limit); no per-user fairness; polling rather than `LISTEN/NOTIFY`.
