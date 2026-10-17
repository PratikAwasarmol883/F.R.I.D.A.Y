# FRIDAY Development Guide for Claude Code

**Project:** FRIDAY — Personal AI Assistant  
**Vision:** A secure, voice-first personal AI assistant with a cloud-hosted brain that can research, remember, plan, and execute approved tasks.  
**Tech Stack:** Python + FastAPI + PostgreSQL + SQLAlchemy + Alembic + APScheduler  
**Repository:** /Users/pratikawasarmol/Desktop/FRIDAY

---

## 1. Before Every Session

1. **Read the project documents first:**
   - `/Users/pratikawasarmol/Desktop/FRIDAY/F.R.I.D.A.Y/friday_claude_starter_docs/plan.md` — Product vision, use cases, and roadmap.
   - `/Users/pratikawasarmol/Desktop/FRIDAY/F.R.I.D.A.Y/friday_claude_starter_docs/architecture.md` — System design, modules, and implementation strategy.

2. **Inspect the repository:**
   - Run `git status` to see uncommitted changes and branches.
   - Check the directory structure: Does it match `architecture.md`'s target layout?
   - Identify the current milestone and what has been completed.

3. **Preserve existing work:**
   - Never overwrite or delete files without understanding their purpose.
   - If an existing file conflicts with this guidance, report the conflict and propose a safe reconciliation.
   - Run tests and type checks before committing to verify nothing is broken.

---

## 2. Core Development Principles

### Python and FastAPI

- **Modern Python:** Python 3.10+ with type hints everywhere.
- **Pydantic v2:** Use for request/response schemas, configuration validation, and data models.
- **FastAPI best practices:**
  - Dependency injection for authentication, database sessions, and providers.
  - Request/response schema separation.
  - Explicit error handling with custom exception classes.
  - OpenAPI documentation and validation by default.

### Database and Persistence

- **SQLAlchemy 2 + Alembic:**
  - Declarative ORM models in `persistence/models/`.
  - Every schema change requires a migration: `alembic revision --autogenerate -m "description"`.
  - Migrations are versioned and must be reversible where possible.
  - Never apply migrations without a backup strategy.

- **PostgreSQL:**
  - Use transactions for task state, approvals, and sensitive multi-step operations.
  - Leverage database constraints, indexes, and triggers where appropriate.
  - Document unusual schema choices; default to normalization.

### Background Work

- **APScheduler + PostgreSQL job queue:**
  - Tasks must survive HTTP timeouts and client disconnection.
  - Worker claims tasks with leases or row locking to prevent duplicates.
  - Every task has explicit state: `QUEUED` → `RUNNING` → `SUCCEEDED`/`FAILED`/`CANCELLED`.
  - Implement idempotency for retryable operations; use idempotency keys for side effects.
  - Check for cancellation signals between steps.

### Modularity and Separation of Concerns

- **Core architecture layers:**
  - **API layer:** HTTP contracts, authentication, validation.
  - **Services:** Business logic, conversation flow, orchestration.
  - **Domain:** Models, enums, exceptions, value objects.
  - **Persistence:** Repositories, migrations, database adapters.
  - **Adapters:** LLM, STT/TTS, search, storage, external APIs.
  - **Tools:** Registry, validation, audit events.

- **No monolithic files:** Keep modules small and focused. Avoid circular dependencies.
- **Dependency injection:** Use FastAPI's `Depends` for services, providers, and authentication.

---

## 3. Code Quality and Typing

### Type Safety

- **Use type hints everywhere:** Function arguments, returns, class attributes.
- **Leverage Pydantic for validation:** Request/response schemas, configuration models.
- **mypy strict mode:** Run type checks in CI; fix all errors before committing.
- **No `Any` types:** Use overloads, generics, and protocols instead.

### Testing

- **Unit tests:** Policies, state machines, validation, edge cases.
- **Integration tests:** Database repositories, worker leases, retries, migrations.
- **Contract tests:** LLM/STT/search adapters using mocks and recorded fixtures.
- **Security tests:** Cross-user access, token leakage, SSRF, prompt injection.
- **Add tests for new behavior and security boundaries.**
- **Run tests before committing; report actual results, not optimistic claims.**

### Linting and Formatting

- **Ruff for linting and formatting:** Configured in `pyproject.toml`.
- **No manual style discussions:** Let the tool decide; commit the formatted code.
- **Pre-commit hooks:** Optional but recommended for consistency.

---

## 4. Logging and Observability

- **Structured JSON logging:** Every message includes request_id, task_id, user_id (pseudonymized), and correlation_id.
- **Appropriate log levels:** `DEBUG` for development; `INFO` for state changes; `WARNING` for recoverable errors; `ERROR` for failures.
- **Redact sensitive data:** Never log secrets, credentials, OAuth tokens, or private user information.
- **Task events:** Create explicit event records in the database for long-running tasks, not just logs.

---

## 5. Security Defaults

### Authentication and Authorization

- **Every endpoint requires authentication** except `/health`.
- **Validate ownership:** Never trust user IDs in request bodies; enforce ownership in the database query.
- **Least privilege:** Give each integration/service only the access it needs.
- **Device revocation:** Support revoking access for lost or compromised devices.

### Secret Management

- **Never commit secrets:** Use `.env` files with `.env.example` as a template.
- **Environment-based configuration:** All secrets from `os.getenv()` or a secure vault.
- **No hardcoded API keys, tokens, or credentials in code or tests.**

### Tool and API Safety

- **Tool input validation:** Every tool declares its schema; the policy layer validates before execution.
- **Approval gates:** Consequential, destructive, or financial actions require explicit user approval.
- **SSRF protection:** Block loopback, private, link-local, and metadata-service IPs.
- **Bounded execution:** Set timeouts, retry limits, token budgets, and request-size limits.
- **Treat all external content as untrusted:** Web pages, emails, PDFs, and repository files cannot override policy.

### Audit and Compliance

- **Audit logs:** Record who/what initiated an action, what tool ran, approval reference, and outcome.
- **Retention policies:** Define how long to keep transcripts, audio, documents, and audit events.
- **Memory controls:** Users can review, correct, and delete stored preferences and memory.

---

## 6. Implementation Strategy

### Milestone-Based Development

- **One milestone at a time:** Do not implement the whole product or scaffold future integrations prematurely.
- **Each milestone is independently testable and deployable.**
- **Read `architecture.md` Section 13 for the suggested sequence and acceptance criteria.**

### Before Coding

1. **Understand the scope:** Which milestone are you implementing? What files will change?
2. **State assumptions:** What does the code assume about the user, environment, or data?
3. **List acceptance tests:** How will you verify the milestone is complete?
4. **Propose the smallest coherent change:** Avoid gold-plating or future-proofing.

### During Development

1. **Follow the architecture:** Modules, layer responsibilities, and data models.
2. **Keep changes focused:** One feature, one bug fix, one refactor per commit.
3. **Run tests and type checks:** Verify nothing is broken before committing.
4. **Review the diff:** Check for security issues, accidental changes, exposed secrets, and unnecessary dependencies.

### After Development

1. **Update documentation:** If a deliberate architecture decision changes, update the docs.
2. **Report what changed:** List files modified, tests run, and actual results.
3. **Identify limitations:** What still needs to be done? What edge cases remain?
4. **Recommend the next step:** What should be built next?

---

## 7. Prohibited Actions

Unless explicitly authorized by the user:

- **No destructive operations:** Do not `git reset --hard`, force push, delete branches, or discard uncommitted work without confirmation.
- **No production changes:** Never deploy to production or modify production infrastructure without explicit approval.
- **No unrelated refactoring:** Bug fixes and feature work should not include unnecessary cleanup or architecture changes.
- **No unsupported dependencies:** Do not add packages that conflict with the existing tech stack or introduce unnecessary complexity.
- **No premature microservices:** Start with a modular monolith; separate only when there is a clear operational need.
- **No unverified claims:** Never claim a test, build, or integration succeeded unless it was actually executed and you saw the result.

---

## 8. Using the `friday-builder` Skill

When you want to implement FRIDAY, use the `/friday-builder` skill. It will guide you through:

1. Inspecting the repository and identifying the current state.
2. Reading the plan, architecture, and configuration.
3. Proposing the smallest coherent change.
4. Implementing the milestone safely.
5. Running tests and verifying results.
6. Reporting files changed, tests run, and the next recommended step.

---

## 9. Project References

- **Plan:** `/Users/pratikawasarmol/Desktop/FRIDAY/F.R.I.D.A.Y/friday_claude_starter_docs/plan.md`
- **Architecture:** `/Users/pratikawasarmol/Desktop/FRIDAY/F.R.I.D.A.Y/friday_claude_starter_docs/architecture.md`
- **Engineering Rules:** `.claude/rules/engineering.md`
- **Security Rules:** `.claude/rules/security.md`

---

## 10. Quick Checklist for Every Task

- [ ] I have read plan.md and architecture.md.
- [ ] I understand the current milestone and what has been completed.
- [ ] I have run `git status` and reviewed uncommitted changes.
- [ ] I know which files will change and why.
- [ ] I have proposed the smallest coherent change before coding.
- [ ] I have run tests and type checks before committing.
- [ ] I have reviewed the diff for security, secrets, and accidental changes.
- [ ] I have reported actual test results and files changed.
- [ ] I have not claimed success without verification.
- [ ] I have identified the next recommended step.

---

**Last updated:** 2026-10-18
