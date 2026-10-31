# Milestone 7 Spec — Approval Center and One Scoped Integration

**Status:** Implemented (see section 11). Approved by the project owner's standing instruction ("just complete everything", "go ahead"). Decisions below are the recommended defaults.
**Source:** `architecture.md` §4.2 (approval step), §6 (approval policy), §7 (`approvals`, `integration_connections`), §9 (API), §13 (Milestone 7); `security.md` §6 (approval workflow).
**Builds on:** M1–M6 (auth and device scopes, tool policy and audit, durable tasks, memory, schedules).

## 1. Goal

1. **Approval center:** when the assistant wants to do something consequential, it can only *propose* it. The proposal is stored, shown to the user exactly as it would run, and executes only after the user approves that exact action, once, before it expires.
2. **One integration:** a push-notification integration (ntfy-compatible) so the assistant can notify the user's phone, with the connection revocable at any time.

**Done when (from the docs):**
- A consequential action cannot execute without a valid approval (unapproved, rejected, expired, tampered, or replayed proposals never run).
- A revoked integration stops working, including proposals that were already pending.
- Approval is bound to the exact tool and arguments the user saw.

## 2. Decisions (defaults applied)

| # | Decision | Choice |
|---|---|---|
| D1 | Pausing | **Tasks do not pause.** The model's consequential call is turned into a stored proposal and the tool result tells the model it is pending; the answer completes normally. Execution happens when the user decides, in the API request that carries the decision. This keeps the M3 task state machine unchanged. The `awaiting_approval` task state in the architecture is deferred until a workflow needs to resume after approval. |
| D2 | The integration | **ntfy-compatible push** to a fixed host chosen by the operator (`NOTIFY_BASE_URL`, default `https://ntfy.sh`). The user supplies only a topic name. No user-supplied URL is ever fetched, so there is no SSRF surface. The topic works like a password, so it is encrypted at rest. |
| D3 | Credential storage | Fernet (`cryptography`) with `INTEGRATION_KEY` from the environment. Without a key, connecting is refused. Revoking deletes the ciphertext. Key rotation is out of scope and documented. |
| D4 | Binding | A proposal stores the validated arguments, a SHA-256 of their canonical JSON, and the tool name and version. The decision request must send the hash the user saw; a mismatch is `409`. Execution recomputes the hash from the stored arguments and refuses on mismatch. |
| D5 | Expiry and replay | Proposals expire after 1 hour. A claim is a single atomic `UPDATE … WHERE status='pending' AND expires_at > now()`, so it can win once. Execution is **at most once**: a claim that never finishes is marked failed (`interrupted`) and is never retried. |
| D6 | Who may decide | A separate device scope `approvals:decide`. Devices that only talk (for example voice) cannot approve. Reading approvals needs only a valid device. |
| D7 | Lifecycle effects | Revoking the integration rejects its pending proposals. Revoking a device rejects the proposals it created. Re-validation happens again at execution time (connection active, tool enabled, requesting device active). |
| D8 | Limits | At most 10 pending proposals per user; 1 notification proposal per ask and 20 per day; title ≤ 80 and message ≤ 500 characters; decided proposals purged after 30 days. |

## 3. Scope

**In:** `approvals` and `integration_connections` tables (migration `0007`, tool-execution status `pending_approval`); policy `Propose` outcome and proposal sink in the tool loop; `send_notification` tool; `NtfyNotifier` adapter plus fake; approval service (propose, list, get, decide, execute); integration service (connect, list, revoke); `/approvals` and `/integrations` APIs; new device scopes; worker purge; settings, docs, tests.

**Out:** more integrations (email, calendar, git, finance), OAuth flows, key rotation, task pause and resume, push notifications about pending approvals, approving from the voice channel, bulk approve, per-user policy rules such as always-allow.

## 4. Policy and tool loop

- `PolicyContext` gains `can_propose` (default false). When false nothing changes: approval-gated tools stay denied with `approval_required` and are not advertised (all existing behavior and tests).
- When true, a tool with `requires_approval` that passes the enabled, scope, schema, guard and call-limit checks yields `Propose(tool, arguments)` instead of a denial, and is advertised.
- The tool loop sends a `Propose` to a `ProposalSink`, records the execution as `pending_approval` with redacted arguments, and returns the model the result "pending user approval; nothing has been done". The sink may refuse (`ToolError`) when limits are hit, which is recorded as failed like any tool error.
- Only assistant asks supply a sink. Research runs and every other path keep `can_propose=false`.
- The tool is added to the per-ask registry only when the user has an active connection.

## 5. Data

`approvals`: `id`, `user_id` (cascade), `device_id` (requesting, set null), `conversation_id` (set null), `tool_name`, `tool_version`, `risk_class`, `arguments` (jsonb), `arguments_hash`, `summary`, `status` (`pending`, `executing`, `executed`, `failed`, `rejected`, `expired`), `created_at`, `expires_at`, `decided_at`, `decided_by_device_id`, `finished_at`, `error_code`. Index on `(user_id, status, created_at desc)`.

`integration_connections`: `id`, `user_id` (cascade), `provider`, `credential` (text, null once revoked), `status` (`active`, `revoked`), `created_at`, `revoked_at`; unique partial index on `(user_id, provider) WHERE status='active'`.

## 6. API (all authenticated and owner-scoped; other users get 404)

- `GET /api/v1/approvals?status&limit`, `GET /api/v1/approvals/{id}`: tool, summary, arguments, hash, status, expiry. Pending proposals past their expiry are reported and stored as `expired`.
- `POST /api/v1/approvals/{id}/decision` `{decision: approve|reject, arguments_hash}`: needs `approvals:decide`. Approve claims atomically, re-validates, executes once, and returns the final record. Errors: `409 APPROVAL_CHANGED` (hash mismatch), `409 APPROVAL_NOT_PENDING`, `410 APPROVAL_EXPIRED`, `403 FORBIDDEN`.
- `POST /api/v1/integrations/ntfy/connect` `{topic}` (needs `integrations:manage`; replaces an existing active connection), `GET /api/v1/integrations`, `DELETE /api/v1/integrations/ntfy`. Responses never contain the credential. `503 INTEGRATION_UNAVAILABLE` without a key.

## 7. Security

- The model can only propose; approval needs a human-controlled endpoint and a dedicated scope. Prompt-injected content can create at most a visible, expiring, capped proposal.
- Arguments are shown to the user exactly as stored; execution uses the stored arguments, never anything from the decision request.
- Notification text is sent as the request body with a plain header title (control characters rejected); the topic is validated against a strict pattern; the provider host is operator configuration, `https` unless localhost, with no redirects.
- The credential never appears in logs, audit rows, tool-execution rows, API responses or errors.
- Audit rows record ids and outcomes only: `approval.request`, `approval.approve`, `approval.reject`, `approval.execute`, `approval.expire`, `integration.connect`, `integration.revoke`.

## 8. Tests

| Area | Cases |
|---|---|
| Policy | `Propose` only with `can_propose`; denial unchanged without it; scope, schema, guard and limit checks still apply first |
| Proposals | model call creates a pending row and nothing is sent; execution row is `pending_approval`; tool absent without a connection or scope; caps; research cannot propose |
| Decisions | approve executes exactly once; reject never executes; wrong hash `409`; expired `410`; double approve and concurrent approvals send once; missing scope `403`; other user `404` |
| Binding | tampering with stored arguments after the fact is refused at execution; decision body cannot alter the action |
| Revocation | revoking the integration rejects pending proposals and later approvals do not send; revoked requesting device cannot have proposals executed; revoked deciding device is `401` |
| Failure | provider error or timeout marks the approval failed with a stable code; a stuck `executing` claim becomes failed and is never retried |
| Integration | connect, replace, list without credential, revoke deletes ciphertext, no key `503`, topic validation, encryption round trip, nothing secret in logs or audit |
| Notifier | request shape, topic in path, no redirects, error mapping, no body or topic leakage |
| Injection | hostile search content causing a notification proposal results only in a pending proposal |
| Migration | `0007` up/down/up |
| Regression | all M1–M6 behavior unchanged |

**Verification before done:** `ruff`, `ruff format --check`, `mypy`, `pytest`, migration up/down/up, mutation checks (approval gate, hash binding, expiry, single-claim, scope check, revocation effects), Docker walkthrough, CI green.

## 9. Risks

- **Approval fatigue and blind approval:** the approval shows the exact message; caps and a one-hour expiry limit pile-up. No always-allow rule exists.
- **Notification as a data channel:** a prompt-injected proposal could try to smuggle private data into a message. The user sees the full text before it sends, and the provider is a single operator-chosen host.
- **Topic secrecy:** ntfy topics are shared secrets; anyone who learns one can read the user's notifications. Encryption at rest protects the stored value only.
- **At-most-once means a crash can lose one notification.** That is the safe side for consequential actions.
- **No live provider verification here** (no network); the adapter is tested against a mock transport only.

## 10. Implementation order (each step ends green)

1. Migration `0007`, models, repositories, settings, crypto helper, new scopes.
2. Policy `Propose`, tool loop sink, `pending_approval` status.
3. Notifier adapter, `send_notification` tool, integration service and API.
4. Approval service, assistant wiring, `/approvals` API, purge.
5. Docs, compose/env, mutation checks, Docker walkthrough, commit, CI.

## 11. Implementation notes and what was not verified

**Refinements**
- Publishing uses ntfy's JSON API (a POST of `{topic, title, message}` to the base URL), so titles and messages need no header encoding. Any non-2xx status, including a redirect, is a failure and redirects are never followed.
- A claim happens first and is committed before anything is sent, so a crash can lose one notification but can never send two. Claims older than `APPROVAL_EXEC_STALE_SECONDS` are failed as `interrupted` the next time the user's approvals are read.
- Expiry is enforced twice: the atomic claim refuses overdue approvals, and reads move overdue pending approvals to `expired`.
- `send_notification` is registered per ask only when the server has a key and the user has an active connection; `PolicyContext.can_propose` is set only by the assistant service. Research and every other path keep gated tools denied.
- The tool-execution audit row masks the title and message as `[N chars]`; the full text exists only on the approval the user reviews, and is purged with it after `APPROVAL_RETENTION_DAYS`.
- Device scopes are no longer limited to `tool:<name>`; `approvals:decide` and `integrations:manage` are the two platform scopes.

**Verified:** policy proposals and fail-closed behavior without a sink; proposal creation, offering rules, guards and limits (per ask, daily, pending); hostile web content producing only a pending proposal; approve-once, reject, concurrent approvals (six at once, one send), replay, wrong hash, extra fields, tampered stored arguments, expiry, stale claims; scope separation, owner scoping, revoked integration (pending and already claimed), revoked requesting and deciding devices, disabled tool, missing server key; credential encryption, no leakage into responses, audit rows or logs; notifier request shape, status mapping, no redirects; migration `0007` up/down/up; and a Docker walkthrough through the real HTTP adapter against a local mock ntfy server (send once, replay refused, revoke rejects pending, credential deleted, nothing sensitive in logs). Mutation checks failed the suite for every safeguard removed; one gap found (an empty server key) was closed with a new test.

**Not verified:** a live ntfy server (no network), and a real model choosing to call `send_notification` (no model key); in the walkthrough the proposal was created through the service rather than by a model turn. Key rotation and push alerts about pending approvals do not exist.
