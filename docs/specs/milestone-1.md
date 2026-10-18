# Milestone 1 Spec — Authenticated Text Assistant

**Status:** Draft, awaiting approval. No code is written until this is approved.
**Source:** `architecture.md` §13 (Milestone 1), §6–§10; `plan.md` Phase 1.

## 1. Goal

An authenticated user (via a registered device) can ask a question, get an LLM answer, and read the conversation history later. Ownership is enforced, provider failures return safe errors, and every call is bounded in size, time, and spend.

**Done when (from the docs):**
- Authenticated question/answer persists and history is retrievable.
- Invalid credentials are rejected.
- Cross-user access is impossible (ownership tests pass).
- Provider errors and timeouts return a safe error, never internals.

## 2. Decisions needed from you

Each has a recommendation; the rest of the spec assumes the recommended option.

| # | Decision | Recommendation | Alternative |
|---|---|---|---|
| D1 | Auth scheme | **Opaque per-device bearer tokens**, stored hashed, revocable instantly | JWT access + refresh tokens, or OIDC (more moving parts; defer until multi-user or phone/web clients need it) |
| D2 | How users/devices are created | **Operator CLI only** (`python -m friday.cli ...`); no public signup endpoint | Self-service signup API (larger attack surface, not needed for a personal assistant) |
| D3 | LLM provider | **Anthropic via official `anthropic` SDK** behind an `LLMProvider` interface; model set by `LLM_MODEL` | OpenAI or other; only the adapter changes |
| D4 | Default model | **`claude-haiku-5-5`** (cheap; plan says cost-aware), override per environment | `claude-sonnet-5-5` for quality |
| D5 | Spend control | **Per-user daily token budget** from `usage_records` (default 200,000) | No budget until a later milestone |
| D6 | Streaming | **Not in M1**; plain request/response | SSE/WebSocket later |

## 3. Scope

**In:** users, devices, conversations, messages, usage records, audit events; token auth; `POST /assistant/ask`; conversation create/read; LLM adapter; stable error envelope; request-ID correlation; operator CLI; migration `0002`; tests; CI stays green.

**Out (deliberately deferred):** tools and tool calling (M2), background tasks (M3), memory and system-prompt personalization (M6), streaming, voice, per-request rate limiting, idempotency keys on `ask`, device-management HTTP endpoints, OAuth/OIDC, conversation list/delete, retention policies.

## 4. Data model (migration `0002`)

UUID v4 primary keys, UTC `timestamptz`, FKs with `ON DELETE CASCADE` only where children are meaningless without the parent.

| Table | Columns | Constraints / indexes |
|---|---|---|
| `users` | id, display_name, timezone (IANA, default `UTC`), created_at | |
| `devices` | id, user_id → users, name, device_type (`mac`/`phone`/`web`/`cli`), token_hash (char 64), created_at, last_seen_at, revoked_at | unique(token_hash); index(user_id) |
| `conversations` | id, user_id → users, title (nullable), created_at, updated_at | index(user_id, updated_at desc) |
| `messages` | id, conversation_id → conversations, sequence (int), role (`user`/`assistant`), content (text), created_at | unique(conversation_id, sequence); check(role); check(length(content) ≥ 1) |
| `usage_records` | id, user_id, conversation_id, message_id (assistant msg), provider, model, input_tokens, output_tokens, created_at | index(user_id, created_at) |
| `audit_events` | id, user_id (nullable), device_id (nullable), action, target_type, target_id, outcome (`success`/`failure`/`denied`), correlation_id, created_at | index(created_at), index(user_id, created_at) |

Notes:
- `messages` has no `user_id`; ownership is always resolved through `conversations.user_id` inside the query (never from client input).
- `audit_events` and logs never contain message content, tokens, or provider keys.
- Estimated cost is derived from tokens later; M1 stores raw token counts only.
- Migration must support `upgrade` and `downgrade` (CI already runs up/down/up).

## 5. Authentication and authorization (D1, D2)

**Token:** `fri_` + 32 random bytes, URL-safe base64 (256-bit entropy). Generated once by the CLI and shown once. Stored only as SHA-256 hex in `devices.token_hash`. Because the token is high-entropy random, a fast hash is appropriate and enables an indexed lookup.

**Request flow:** `Authorization: Bearer <token>` → hash → look up device where `revoked_at IS NULL` → load user → inject `CurrentUser(user_id, device_id)` via a FastAPI dependency. `last_seen_at` is updated at most once per 5 minutes per device to avoid write amplification.

**Failure behavior:** missing, malformed, unknown, and revoked tokens all return the **same** `401 UNAUTHENTICATED` response with `WWW-Authenticate: Bearer`, so callers cannot tell which case occurred. Failed attempts are logged (no token value) with request ID.

**Authorization:** every conversation/message query includes `user_id = :current_user` in SQL. Accessing another user's resource returns `404 NOT_FOUND` (not 403) so existence is not leaked.

**Public endpoints:** only `GET /api/v1/health`. Everything else requires auth.

**CLI (operator, server-side shell access required):**
- `create-user --name --timezone`
- `register-device --user-id --name --type` → prints token once
- `revoke-device --device-id`
Each writes an `audit_events` row.

## 6. API (all under `/api/v1`)

| Method | Path | Behavior |
|---|---|---|
| `POST` | `/conversations` | Create a conversation. Body: `{title?: str ≤ 200}`. `201` + conversation. |
| `GET` | `/conversations/{id}` | Owned conversation with messages ordered by `sequence`. Query: `limit` (default 100, max 200), `before_sequence` for pagination. |
| `POST` | `/assistant/ask` | Body: `{conversation_id?: uuid, message: str}`. If `conversation_id` is omitted a new conversation is created. Returns `{conversation_id, message_id, answer, usage: {input_tokens, output_tokens}}`. |

**Validation:** `message` is 1–8,000 characters after trimming; unknown fields rejected (`extra="forbid"`); request body capped at 64 KB.

**Error envelope (all non-2xx, including validation and unhandled errors):**
```json
{"code": "PROVIDER_TIMEOUT", "message": "The assistant took too long to respond.", "request_id": "..."}
```
`details` is included only for validation errors (field paths, no echoed values).

| Code | HTTP | When |
|---|---|---|
| `UNAUTHENTICATED` | 401 | Bad/missing/revoked token |
| `VALIDATION_ERROR` | 422 | Schema violation |
| `NOT_FOUND` | 404 | Missing or not owned |
| `BUDGET_EXCEEDED` | 429 | Daily token budget used up (D5) |
| `PROVIDER_TIMEOUT` | 504 | Provider call exceeded timeout |
| `PROVIDER_UNAVAILABLE` | 503 | Provider rate-limited or overloaded |
| `PROVIDER_ERROR` | 502 | Any other provider failure |
| `INTERNAL_ERROR` | 500 | Unexpected; details logged, never returned |

## 7. `ask` flow (no DB transaction held across the LLM call)

1. Authenticate; validate body.
2. **Tx 1 (read):** if `conversation_id` given, load it scoped to the user (404 otherwise) and the last `MAX_HISTORY_MESSAGES` (default 20); check today's usage against the budget.
3. Call `LLMProvider.complete(...)` with static system prompt + history + new message, bounded by `LLM_TIMEOUT_SECONDS` (30) and `LLM_MAX_OUTPUT_TOKENS` (1024).
4. **Tx 2 (write, atomic):** lock the conversation row (`SELECT ... FOR UPDATE`), insert user message and assistant message with the next two `sequence` values, insert `usage_records`, update `conversations.updated_at`, insert `audit_events` (`assistant.ask`, success).
5. On provider failure: nothing is persisted except an `audit_events` failure row (separate short tx); the user message is **not** stored, so history never has an unanswered message. Client retries by resending.

Known trade-off: two concurrent asks in the same conversation may both read the same history; sequences stay consistent because of the row lock, and the second answer simply lacks the first as context. Acceptable for a single-user assistant.

## 8. LLM adapter (D3, D4)

```python
class LLMProvider(Protocol):
    async def complete(self, *, system: str, messages: list[ChatMessage],
                       max_output_tokens: int, timeout_seconds: float) -> LLMResult: ...
```
- `LLMResult`: `text`, `input_tokens`, `output_tokens`, `model`.
- Provider exceptions are translated inside the adapter to `LLMTimeout`, `LLMUnavailable`, `LLMError`; SDK types never leak past `adapters/llm/`.
- SDK retries capped (`max_retries=2`), explicit timeout, no logging of prompts or responses.
- Unit/integration tests use an in-repo `FakeLLMProvider` injected through the FastAPI dependency; **tests never call the real API**. One opt-in smoke test runs only when `ANTHROPIC_API_KEY` is set.
- The system prompt is a static constant: FRIDAY persona, instructs honesty about uncertainty, no tool or memory claims.
- Before implementing, I will confirm SDK usage and the model ID against current Anthropic docs.

## 9. Configuration additions (validated at startup)

`ANTHROPIC_API_KEY` (required, secret), `LLM_MODEL`, `LLM_TIMEOUT_SECONDS`, `LLM_MAX_OUTPUT_TOKENS`, `MAX_HISTORY_MESSAGES`, `DAILY_TOKEN_BUDGET`. Added to `.env.example` with placeholders only. The key is never logged; `Settings` uses `SecretStr`. Tests run without a real key.

## 10. Observability

- Middleware assigns/propagates `X-Request-ID`, stores it in a contextvar, returns it in the response and error envelope, and the JSON log formatter includes it (the field exists today but is not populated).
- Log per ask: request ID, user ID, conversation ID, model, token counts, latency, outcome. No content.

## 11. Code layout (follows `architecture.md` §5; only what M1 needs)

```
friday/
  core/        errors.py  request_context.py  security.py (token gen/hash)
  domain/      enums.py  exceptions.py
  persistence/ models/  repositories/        (migration 0002)
  services/    auth_service  conversation_service  assistant_service  usage_service  audit_service
  adapters/llm/ base.py  anthropic.py  fake.py
  api/         dependencies.py  routes/{conversations,assistant}.py  schemas/
  cli.py
```
Layering rule: routes → services → repositories/adapters. Routes hold no business logic; services never import FastAPI.

## 12. Dependencies

Add: `anthropic` (pinned minor). Nothing else expected. `pytest-asyncio` only if async DB tests need it.

## 13. Tests (acceptance mapping)

M1 needs a **real Postgres** for repository/auth/ownership tests (CI already has one; locally `docker compose up -d friday-db`). Each test runs in a rolled-back transaction.

| Area | Cases |
|---|---|
| Auth | no header, malformed, unknown token, revoked token → identical 401; valid token → 200; `last_seen_at` throttle |
| Token storage | DB contains only hash; plaintext token never appears in logs or responses |
| Ownership | user B gets 404 reading and asking in user A's conversation; SQL-level scoping covered per repository method |
| Ask | creates conversation when omitted; persists user+assistant messages with consecutive sequences; history order and pagination; usage row written; audit row written |
| Validation | empty, whitespace-only, >8,000 chars, unknown field, bad UUID → 422 envelope |
| Provider failures | timeout → 504, unavailable → 503, other → 502; envelope has no provider text; nothing persisted except audit |
| Budget | at/over budget → 429 and provider not called |
| Errors | unhandled exception → 500 envelope with request ID, no traceback |
| Concurrency | two parallel asks in one conversation produce unique, gapless sequences |
| Migration | `0002` up/down/up |
| Security | injected content in message cannot affect auth/ownership (static system prompt, no tools) |
| CLI | create-user, register-device (token shown once, hash stored), revoke-device (subsequent request 401) |

**Commands to pass before done:** `ruff check`, `ruff format --check`, `mypy friday`, `pytest`, `alembic upgrade head && downgrade base && upgrade head`, plus a manual curl walkthrough against Docker Compose.

## 14. Risks and mitigations

- **Cost runaway:** output cap, history cap, message cap, daily budget.
- **Token leakage:** hashed storage, log redaction test, single-display CLI.
- **Provider outage:** bounded timeout, bounded retries, safe error codes.
- **Scope creep toward tools/memory:** explicitly out of scope; static system prompt.

## 15. Implementation order (each step ends green on CI)

1. Error envelope, request-ID middleware, `SecretStr` config additions.
2. Migration `0002` + models + repositories (+ real-DB test fixtures).
3. Token utilities, auth service, auth dependency, CLI.
4. LLM adapter + fake.
5. Conversation service/routes.
6. Assistant service/route with budget, audit, usage.
7. Docs: README walkthrough, update `.env.example`.
