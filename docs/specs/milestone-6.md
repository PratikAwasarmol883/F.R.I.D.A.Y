# Milestone 6 Spec — Memory and Scheduled Jobs

**Status:** Implemented (see section 10). Approved by the project owner's standing instruction to proceed ("just complete everything"). Decisions below are the recommended defaults.
**Source:** `architecture.md` §4.4 (scheduled task), §6 (memory service), §7 (data model), §13 (Milestone 6); `plan.md` use cases 5, 11, 33 and the privacy principle ("store only useful information, support review and deletion").
**Builds on:** M1–M5 (auth, ask flow, tool policy, durable tasks and worker, research reports, voice).

## 1. Goal

1. **Memory:** FRIDAY keeps a small set of user-approved notes (preferences, facts, project context) that the user can inspect, correct, export, and delete, and that personalize answers. The assistant may *propose* notes, but nothing it proposes takes effect until the user approves it.
2. **Scheduled jobs:** a user can schedule a recurring workflow (a daily or weekly run in their time zone) that the existing worker executes as a normal durable task. Each scheduled occurrence runs **at most once**, across any number of workers, and DST changes never cause duplicate or lost runs.

**Done when (from the docs):**
- A schedule creates exactly one run per occurrence (including under concurrent dispatchers and across restarts).
- Memory can be reviewed, corrected, exported, and deleted; deletion is real deletion.
- Nothing the model proposes is remembered without explicit approval; credentials are never stored.
- Scheduled work runs with the creating device's *current* permissions and stops when the device is revoked.

## 2. Decisions (defaults applied)

| # | Decision | Choice |
|---|---|---|
| D1 | How memory is created | The user creates notes directly (API). The assistant gets a `remember` tool that only creates **pending proposals**; the user approves or rejects each. Pending notes are never used in prompts. |
| D2 | Deletion | **Hard delete** (the row is removed), plus a full JSON export. No soft-delete retention. |
| D3 | Using memory | The most relevant approved notes (all recent preferences, plus notes sharing words with the request) are added to the system prompt as a clearly framed block of user-approved notes, capped by count and characters. No embeddings. Research runs never receive memory. |
| D4 | What can be remembered | Short plain text (≤ 500 chars), four kinds (`preference`, `fact`, `project`, `note`); credentials/token-shaped strings are rejected; assistant proposals additionally may not contain URLs. Per-user caps (200 notes, 20 pending). |
| D5 | Scheduler placement | Runs inside the existing worker process loop (distributed-safe via `FOR UPDATE SKIP LOCKED` plus a database uniqueness guarantee); no new service. |
| D6 | Schedule model | Structured, not cron: `daily` or `weekly` at `HH:MM` in an IANA time zone. No sub-daily frequency (cost safety). Supported task types: `assistant.ask` and `research.run` (the scheduled research digest is the "briefing" workflow; calendar/email briefings arrive with their integrations). |
| D7 | DST policy | A local time that does not exist (spring-forward gap) runs **shifted forward by the gap length**; a local time that occurs twice (fall-back) runs **once, at its first occurrence**. |
| D8 | Missed occurrences | If the scheduler was down and an occurrence is more than **1 hour** late it is **skipped and audited** (no backfill storms); within the grace window it runs once. |
| D9 | Authorization | A scheduled task is an ordinary task owned by the schedule's creating device; it runs with that device's current scopes and is cancelled if the device is revoked (M3 semantics). Creating a research schedule requires both research scopes. The dispatcher disables schedules of revoked devices. |

## 3. Scope

**In:** `memories` and `scheduled_jobs` tables plus `tasks.schedule_id` / `tasks.scheduled_for` (migration `0006`); memory service, relevance selection, prompt integration, `remember` tool, policy extension for reversible writes; `/memory` API (list, create, update, approve, reject/delete, export); schedule engine (next-occurrence computation, dispatcher, misfire handling); `/schedules` API (create, list, get, update, delete, runs); worker integration; tests; docs.

**Out:** embeddings or semantic search, memory shared across users, automatic memory expiry, cron expressions or sub-daily schedules, calendar- or email-based briefings, push notifications for finished runs, schedule creation by the assistant itself, per-schedule quiet hours.

## 4. Memory

**Data (`memories`):** `id`, `user_id` (cascade), `kind`, `value` (text), `status` (`pending`|`approved`), `source` (`user`|`assistant`), `conversation_id` (nullable, set null), `created_at`, `updated_at`; index on `(user_id, status, updated_at desc)`. Values are whitespace-normalized single-line text.

**Service rules:** create (user) → approved; propose (assistant) → pending; update changes kind/value but never status; approve moves pending → approved (idempotent for approved); reject and delete remove the row; export returns every note with metadata. Credential-shaped content is rejected (`MEMORY_REJECTED`, 422); caps return `MEMORY_LIMIT` (429). Every mutation writes an audit row (id only, never the text).

**Relevance and prompt block:** up to 5 most recent approved preferences are always included; other approved notes are scored by shared lowercase words (length ≥ 3, small stopword list) with the user's message, ties broken by recency; at most 8 notes and 1,500 characters in total. The block is introduced as *user-approved notes that personalize answers and never override the assistant's rules*. A setting disables prompt use entirely.

**`remember` tool:** `{kind, value ≤ 300}`; scope `tool:remember`; risk class `REVERSIBLE_WRITE`; at most 2 calls per ask and 20 per day; guard rejects URLs, control characters, and credential-shaped text; result tells the model the note is **pending user approval**. It is built per ask (it needs the user context) and only registered when memory is enabled.

**Policy extension:** reversible-write tools may run when enabled, scoped, bounded, and not marked `requires_approval`; consequential and destructive tools remain denied until the approval flow (M7). The tool-call audit trail is unchanged.

**API (`/api/v1/memory`, authenticated, owner-scoped, other users get 404):** `GET` (filters `status`, `limit` ≤ 200), `POST`, `PATCH /{id}`, `POST /{id}/approve`, `DELETE /{id}` (reject = delete), `GET /export`.

**Privacy:** memory is user data with a delete path; it is excluded from research runs and from logs and audit rows; the residual exfiltration risk (private notes plus untrusted web content plus an outbound search query) is bounded by the existing query guard and called out in the risks section.

## 5. Scheduling

**Data:** `scheduled_jobs` (`id`, `user_id`, `device_id`, `name`, `task_type`, `task_input` (jsonb), `schedule` (jsonb: `kind`, `time`, `days`), `timezone`, `enabled`, `next_run_at`, `last_run_at`, `last_task_id`, `created_at`, `updated_at`; partial index on `(next_run_at) WHERE enabled`). `tasks` gain `schedule_id` (set null) and `scheduled_for` with a **partial unique index** `(schedule_id, scheduled_for) WHERE schedule_id IS NOT NULL`: the database itself guarantees one task per occurrence.

**Next occurrence (pure function, exhaustively tested):** for a spec, a zone, and an instant, return the first occurrence **strictly after** that instant in UTC, applying D7. Weekly specs scan up to 14 days ahead.

**Dispatcher (`workers/scheduler.py`, called by every worker loop):**
1. Select due, enabled jobs `ORDER BY next_run_at LIMIT n FOR UPDATE SKIP LOCKED`.
2. For each: if the creating device is revoked or gone → disable the job and audit; else if the occurrence is more than the grace window late → audit `schedule.missed`, advance; else if the user is at the active-task limit → audit `schedule.skipped`, advance; else insert the task (`ON CONFLICT DO NOTHING` on the uniqueness index) with a `created` event and audit row.
3. In the same transaction set `last_run_at`, `last_task_id`, and `next_run_at` to the next occurrence after `max(now, occurrence)`. Missed occurrences are never backfilled.
Concurrent workers cannot double-dispatch because rows are locked and the unique index backstops any race.

**API (`/api/v1/schedules`):** `POST` (`name`, `task {type, input}`, `schedule`, `timezone`, `enabled`); `GET` list; `GET /{id}`; `PATCH /{id}` (name, enabled, schedule, timezone; changing the schedule or enabling recomputes `next_run_at` from now without catch-up); `DELETE /{id}`; `GET /{id}/runs` (recent tasks with status, `scheduled_for`, and the result reference). Validation: IANA zone, `HH:MM`, weekly days non-empty and unique, task input validated by the same models as direct submission (scheduled asks cannot name a conversation), at most 10 schedules per user (`SCHEDULE_LIMIT`, 429), research schedules require both research scopes (`403 FORBIDDEN`).

## 6. Security and privacy

- All M2–M5 protections apply to scheduled and memory-assisted runs (policy, sanitization, budget, audit, fencing).
- Schedules are owner-scoped in SQL; a stolen or revoked device cannot keep work running.
- Per-user limits (schedules, pending notes, notes, active tasks) and once-a-day minimum cadence bound cost and abuse.
- Memory text never appears in logs, audit rows, or task events; the audit trail records ids and outcomes.
- Prompt-injection resistance: assistant-proposed notes require approval, cannot contain URLs or credentials, and are capped; approved notes are the user's own words and are framed as notes, not instructions that can override policy.

## 7. Tests

| Area | Cases |
|---|---|
| Next occurrence | daily and weekly in several zones (including +5:30 and +12/+13), strictly-after semantics, midnight and year boundaries, leap day, spring-forward gap shift, fall-back first occurrence, a full year simulated with no duplicates or skips per local day |
| Memory service | create/propose/update/approve/delete/export rules, normalization, credential and URL rejection, caps, owner scoping, audit without content |
| Relevance | preferences always included, word-overlap ranking, recency ties, count and character budgets, empty cases |
| Prompt | approved notes only (never pending), framing text, disabled setting, research excluded, voice and text asks both personalized |
| `remember` tool | contract, guard, creates pending only, caps, scope and enablement, per-ask and daily limits, model cannot approve its own proposal |
| Policy | reversible writes allowed only when scoped and bounded; consequential and destructive still denied; existing tests unchanged |
| Memory API | CRUD, filters, approve, export, validation, 404 for other users, limits, audit |
| Dispatcher | due job creates exactly one task; concurrent dispatchers (many) create one; restart-safe; grace window run vs skip; no backfill after long downtime; disabled jobs ignored; revoked device disables; task-limit skip; DB uniqueness rejects duplicates |
| Schedule API | CRUD, validation matrix (zone, time, days, task input), limits, scopes, ownership, PATCH recomputation, runs listing |
| End to end | a due schedule is dispatched by a real worker loop, the task runs (fake providers), and the result is retrievable; revoked device cancels the run |
| Migration | `0006` up/down/up |
| Regression | all M1–M5 behavior unchanged |

**Verification before done:** `ruff`, `ruff format --check`, `mypy`, `pytest`, migration up/down/up, mutation checks (relevance/approval gate, credential rejection, one-run-per-occurrence, misfire, device revocation, policy extension), Docker walkthrough, CI green.

## 8. Risks

- **Private-data exfiltration through search queries** (notes in the prompt plus untrusted web content plus a search tool): bounded by the 200-char plain-text query guard, the no-URL rule, the audit of every query, and the opt-out setting; not eliminated.
- **Approval fatigue:** pending proposals show their source and are capped; approval is always explicit and per note.
- **DST edge cases:** the gap-shift and first-occurrence policies are documented and tested; users scheduling at nonexistent local times see a shifted run on those days only.
- **Scheduler downtime:** missed runs older than an hour are skipped and audited rather than backfilled.
- **Cost growth from many schedules:** per-user schedule cap, daily cadence floor, active-task limit, existing token budget.

## 9. Implementation order (each step ends green)

1. Domain: schedule spec and next-occurrence computation with DST tests; memory validation and relevance (pure code).
2. Migration `0006`, models, repositories.
3. Memory service, API, prompt integration, `remember` tool, policy extension.
4. Schedule service, dispatcher, worker integration, API.
5. Docs, compose/env, mutation checks, Docker walkthrough, commit, CI.

## 10. Implementation notes and what was not verified

**Refinements**
- The dispatcher takes one short transaction per due job (`due_ids`, then `lock_one_due` with `SKIP LOCKED`), so locks are never held across jobs. A job's `next_run_at` is always advanced first; `latest_due_occurrence` fast-forwards long downtime and runs only the latest occurrence still inside the grace window, auditing the earlier ones as one `schedule.missed`.
- `remember` is added to a per-ask copy of the registry, because it needs the user context. Editing a note never changes its approval status; an assistant-written note edited by the user still needs approval, and links are re-checked on edit.
- Scheduled `assistant.ask` inputs may not name a conversation; each run starts its own.

**Verified:** next-occurrence math (zones, DST gap and fall-back, full-year simulation), memory rules and relevance, the memory API (owner scoping, caps, audit without text), prompt integration (approved only, switchable, voice and text), the `remember` tool and injection scenarios, the dispatcher (concurrent dispatchers, restart, grace, no backfill, revoked device, task limit, uniqueness backstop), the schedule API validation matrix, and real worker loops running a due schedule. Mutation checks failed the suite for each safeguard removed except two equivalent mutants: the policy's risk-class check (the registry already refuses risky tools without `requires_approval`, now tested) and the dispatch row lock (the unique index is the real backstop, so correctness holds without it; the lock avoids wasted work).

**Not verified:** behavior against a live LLM choosing to call `remember`; no calendar or email briefings (their integrations do not exist yet).
