# Engineering Rules for FRIDAY

**Purpose:** Ensure FRIDAY's codebase remains maintainable, reliable, and testable as it grows.

---

## 1. Modular Code and Separation of Concerns

### Layer Structure

FRIDAY is organized into logical layers. Keep each layer focused:

- **API Layer** (`api/`)
  - HTTP contracts, request validation, response serialization.
  - Authentication and authorization dependencies.
  - Rate limiting and correlation IDs.
  - Short-lived request handling; long work delegates to services/workers.

- **Services** (`services/`)
  - Business logic: conversation flow, task orchestration, memory management.
  - Depend on repositories and adapters, not HTTP details.
  - Pure logic where possible; unit testable.

- **Domain** (`domain/`)
  - Models, enums, exceptions, value objects.
  - Represent concepts independent of storage or transport.
  - Include validation logic that crosses domain boundaries.

- **Persistence** (`persistence/`)
  - Database models (SQLAlchemy), repositories, migrations (Alembic).
  - Encapsulate query logic; expose only what services need.
  - All schema changes go through migrations.

- **Adapters** (`adapters/`)
  - Abstract external services: LLM, STT/TTS, search, storage, notifications.
  - Provider-specific SDKs stay isolated behind narrow interfaces.
  - Support dependency injection for testing.

- **Tools** (`tools/`, `orchestration/`)
  - Tool registry, input validation, execution, audit.
  - No tool can execute arbitrary code without policy checks.
  - Every tool declares scope, timeout, retry policy, and approval requirement.

- **Workers** (`workers/`)
  - Background task execution, scheduling, retries.
  - Long-running work is persistent and resumable.
  - Worker claims tasks safely; detects duplicate execution.

### File Organization

- Keep modules small and focused; avoid god classes.
- Group related functionality: separate read-only from write operations.
- Use `__init__.py` to expose a module's public API; hide implementation details.
- Circular dependencies are a code smell; refactor to dependency injection.

---

## 2. API Validation and Stable Error Responses

### Request Validation

- **Pydantic schemas:** Define strict request and response schemas for every endpoint.
- **Automatic validation:** FastAPI validates against the schema; invalid requests are rejected with 422.
- **Custom validators:** Use Pydantic field validators for complex rules (e.g., URL syntax, allowed ranges).
- **Request limits:** Set `max_body_size` and request timeouts; reject oversized payloads.

### Response Contracts

- **Consistent error structure:** All errors return a stable JSON schema with `code`, `message`, and optional `details`.
- **Error codes:** Use semantic codes (e.g., `INVALID_INPUT`, `NOT_FOUND`, `UNAUTHORIZED`) instead of HTTP status only.
- **Safe error messages:** User-facing messages are clear but do not leak internal details.
- **No exception stacks in responses:** Log full tracebacks; return only safe summaries to clients.

### Example Error Response

```json
{
  "code": "APPROVAL_REQUIRED",
  "message": "This action requires approval before execution.",
  "details": {
    "approval_id": "req_123",
    "expires_at": "2026-10-25T10:00:00Z"
  }
}
```

---

## 3. Database Migrations and Transaction Handling

### Migrations

- **Every schema change requires a migration.**
  - Use `alembic revision --autogenerate -m "description"` to scaffold.
  - Review the generated SQL; hand-edit if needed.
  - Never edit applied migrations; create a new one instead.

- **Idempotent migrations:** Migrations can be re-run safely if a deployment fails.
  - Use `IF NOT EXISTS`, `IF EXISTS` clauses.
  - Test migrations both up and down (where reversible).

- **No production schema changes without a plan:**
  - Back up before applying migrations on production data.
  - Document the expected migration time and any downtime.
  - Coordinate with deployment; do not apply during traffic peaks if locking is expected.

### Transactions

- **Use transactions for atomic multi-step operations:**
  - Task creation, state changes, approval workflows.
  - Memory updates, usage accounting.
  - Always specify isolation level if needed (default is read-committed).

- **Avoid long-held transactions:**
  - Release database connections promptly.
  - Do not keep a transaction open while waiting for an external API.

---

## 4. Background Task Reliability

### Task State Machine

```
QUEUED → RUNNING → SUCCEEDED
              ├ → FAILED
              └ → AWAITING_APPROVAL → running/rejected/cancelled
CANCELLED (from any state)
TIMED_OUT (from RUNNING)
```

Every state transition is explicit and persistent. Validate transitions in code.

### Idempotency and Retries

- **Idempotency keys:** Side effects (e.g., sending notifications, updating external systems) use idempotency keys.
- **Retryable vs. non-retryable failures:**
  - Retryable: network timeouts, temporary service outages, transient database locks.
  - Non-retryable: invalid input, permission denied, resource not found, conflict.
- **Exponential backoff:** Start at 1s, cap at 30s, add jitter to avoid thundering herd.
- **Maximum retries:** Typically 3–5; configure per-task type.

### Worker Execution

- **Safe task claiming:**
  - Use a lease or row-level lock to prevent two workers claiming the same task.
  - A worker that crashes releases its lease (expiration-based).

- **Checkpointing:** Save progress between major steps so a restart does not lose state.
- **Cancellation:** Check for cancellation signals between steps; never cancel mid-operation.
- **Timeouts:** Set hard limits on task duration; fail gracefully if exceeded.

---

## 5. Automated Testing and Dependency Management

### Test Categories

- **Unit tests:** Pure functions, policies, state transitions, edge cases.
- **Integration tests:** Database repositories, worker leases, migration verification.
- **Contract tests:** Provider adapters (LLM, STT, search) using mocks and recorded fixtures.
- **Security tests:** Cross-user access, token/secret leakage, SSRF, prompt injection, approval bypass.
- **End-to-end tests:** Full flows (ask → tool → answer, create task → disconnect → retrieve result).

### Test Organization

```
tests/
  unit/
    domain/
    services/
    orchestration/
  integration/
    persistence/
    workers/
    adapters/
  security/
    cross_user_access_test.py
    token_leakage_test.py
    ssrf_test.py
  e2e/
```

### Running Tests

- **Run before committing:** `pytest` for unit and integration, plus type checks.
- **Report actual results:** Show pytest output, coverage, and any failures.
- **Security tests:** Run them regularly; a feature is not done without security validation.

### Dependencies

- **Pin major versions in pyproject.toml.**
- **Avoid unused dependencies:** Every import should serve a purpose.
- **Review new dependencies:** Does it fit the tech stack? Does it introduce maintenance burden?
- **Security scanning:** Use tools like `pip-audit` in CI to detect known vulnerabilities.
- **Do not pin patch versions unnecessarily:** Allow security updates.

---

## 6. Maintainability and Backward Compatibility

### API Versioning

- **Use versioned API paths:** `/api/v1/assistant/ask`, `/api/v1/tasks`.
- **Support old versions:** Deprecate gradually; give clients time to migrate.
- **Document breaking changes:** Include migration guides in release notes.

### Database Migrations

- **Migrations are permanent:** Once applied to production, they are history.
- **Support old versions:**
  - New API versions can read old schemas.
  - Old code does not need to understand new fields (optional fields are safe).

### Feature Flags (Use Sparingly)

- **Only for rollouts and gradual migration:** Not for experimental code.
- **If a feature flag is not removed within 6 months, convert it to permanent code.**

### Documentation

- **Update docstrings when behavior changes.**
- **Keep `README.md` and architecture docs in sync with code.**
- **Document unusual schema choices, non-obvious algorithms, and third-party integration quirks.**

---

## 7. Code Comments

**Rule:** Comments should explain the "why," not the "what." Well-named functions and variables already explain the "what."

- **Do not comment obvious code:** `x = x + 1  # increment x` is noise.
- **Do comment non-obvious decisions:** Workarounds, subtle invariants, security constraints, or hidden performance impacts.
- **Example (good):**
  ```python
  # Fetch with a timeout shorter than the HTTP request timeout to allow graceful degradation
  # if the external service is slow. This prevents the API from hanging.
  response = fetch_with_timeout(url, timeout=5)
  ```
- **Example (bad):**
  ```python
  # Get the user from the database
  user = db.get_user(user_id)
  ```

---

## 8. Performance Considerations

- **Profile before optimizing:** Use tools like `cProfile` to find real bottlenecks.
- **Database indexes:** Add indexes for frequently queried or joined fields.
- **N+1 queries:** Use eager loading (`.joinedload()`) or batch queries.
- **Caching:** Cache immutable data (e.g., user preferences, provider capabilities).
- **Timeouts:** Every network call must have a timeout.
- **Cost limits:** Respect per-user and per-task budgets for model tokens, search results, and API calls.

---

## 9. Python and FastAPI Standards

### Python Style

- **Type hints everywhere:** `def process(task_id: str, limit: int = 10) -> TaskResult:`
- **No bare `except:`** — catch specific exceptions.
- **Docstrings:** One-line for simple functions; multi-line for complex logic.
- **Descriptive variable names:** `start_time`, not `st`.

### FastAPI Patterns

- **Dependency injection for cross-cutting concerns:**
  ```python
  async def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
      ...

  @app.post("/ask")
  async def ask(request: AskRequest, user: User = Depends(get_current_user)):
      ...
  ```

- **Separate request/response schemas:**
  ```python
  class AskRequest(BaseModel):
      conversation_id: str
      message: str

  class AskResponse(BaseModel):
      task_id: str
      preliminary_answer: str | None
  ```

- **Use async/await:** FastAPI is async-native; leverage it.
- **Streaming responses:** Use `StreamingResponse` for large datasets.

---

## 10. Data Validation

- **Validate at boundaries:** API inputs, external APIs, file uploads.
- **Trust internal code:** Do not validate the same data twice.
- **Schemas as validation:** Pydantic models enforce types, ranges, and custom rules.
- **Error clarity:** If validation fails, explain what was wrong and how to fix it.

---

**Last updated:** 2026-10-18
