# FRIDAY Builder Skill

---
name: friday-builder
description: Implement FRIDAY's next milestone safely—inspect, plan, code, test, and report
---

## Purpose

The FRIDAY Builder Skill guides Claude Code through implementing FRIDAY's architecture consistently, safely, and milestone by milestone. Use this skill when:

- Implementing the next feature or milestone.
- Debugging failing tests or architecture issues.
- Adding a new agent tool or integration.
- Continuing development from the current repository state.
- Reviewing or understanding the existing implementation.

---

## Workflow: Inspect → Propose → Implement → Verify → Report

### Phase 1: Inspect the Repository

1. **Git status:**
   ```bash
   cd /Users/pratikawasarmol/Desktop/FRIDAY && git status
   ```
   Identify uncommitted changes, current branch, and recent commits.

2. **Read the documentation:**
   - `/Users/pratikawasarmol/Desktop/FRIDAY/F.R.I.D.A.Y/friday_claude_starter_docs/plan.md`
   - `/Users/pratikawasarmol/Desktop/FRIDAY/F.R.I.D.A.Y/friday_claude_starter_docs/architecture.md`

3. **Survey the codebase:**
   - What directories exist? Do they match the target architecture?
   - What has been implemented? Run `git log --oneline | head -20` to see recent work.
   - Are there tests? Run them: `pytest --collect-only` to see what exists.

4. **Identify the current milestone:**
   - Refer to Section 13 of `architecture.md`: "Implementation sequence."
   - Has Milestone 0 (repository foundation) been completed?
   - What is the next incomplete milestone?

---

### Phase 2: Propose the Smallest Coherent Change

Before writing code, **clearly state:**

1. **The milestone:** Which phase are you implementing? (e.g., "Milestone 1: Authenticated text assistant")

2. **What will change:**
   - Which files or directories will be created, modified, or deleted?
   - Which migrations, models, or routes will be added?

3. **What will NOT change:**
   - What existing functionality should remain untouched?
   - What future features are intentionally deferred?

4. **Acceptance tests:**
   - How will you verify the milestone is complete?
   - What commands will you run? What output should you see?

5. **Assumptions:**
   - What does the code assume about the Python version, FastAPI version, database, etc.?

**Example:**

```
Milestone: Milestone 0 — Repository and engineering baseline

Changes:
- Create directory structure: friday/, tests/, migrations/
- Add pyproject.toml, Dockerfile, docker-compose.yml
- Implement health endpoint and logging
- Add pytest configuration and first test

No changes to:
- Plan or architecture (unless conflicts are discovered)
- Existing code (none exists yet)

Acceptance:
- App starts: `docker compose up` brings up API and PostgreSQL
- Health check: `curl http://localhost:8000/health` returns 200
- Tests pass: `pytest -v` shows all tests passing
- No secrets committed: `git log --all -p | grep -i "password\|key\|secret"` returns nothing
```

---

### Phase 3: Implement the Milestone

1. **Set up the environment (first-time only):**
   ```bash
   cd /Users/pratikawasarmol/Desktop/FRIDAY
   python3 -m venv venv
   source venv/bin/activate
   pip install -e ".[dev]"
   ```

2. **Create or modify files according to the proposal.**
   - Follow the architecture: separate API, services, persistence, adapters.
   - Use type hints and Pydantic schemas everywhere.
   - Write tests alongside features (test-driven or immediately after).

3. **Use the rules:**
   - `.claude/rules/engineering.md` for code structure, testing, dependencies.
   - `.claude/rules/security.md` for auth, SSRF, prompt injection, approval gates.
   - `.claude/CLAUDE.md` for development practices.

4. **Keep it focused:**
   - One milestone, not the whole product.
   - Do not scaffold future integrations prematurely.
   - Do not refactor unrelated code.

---

### Phase 4: Verify and Test

1. **Run tests:**
   ```bash
   pytest -v
   pytest --cov=friday/  # Coverage report (optional)
   mypy friday/           # Type checking
   ruff check .           # Linting
   ```

2. **Manual testing (if applicable):**
   - Start the app: `docker compose up`
   - Test the happy path: `curl -X POST http://localhost:8000/api/v1/assistant/ask -d '...'`
   - Test error cases: invalid input, missing auth, etc.
   - Check logs for errors or warnings.

3. **Security review:**
   - No hardcoded secrets in code or `.env.example`.
   - No sensitive data in logs.
   - Approval gates in place for consequential actions.
   - SSRF protection for external fetches.

4. **Report actual results:**
   - Show pytest output, coverage, test count.
   - Show mypy/ruff output (should be clean).
   - Do not claim success without verification.

---

### Phase 5: Review and Commit

1. **Review the diff:**
   ```bash
   git diff --stat          # Summary of files changed
   git diff friday/         # Review changes
   ```

   Look for:
   - Accidental changes (leftover debug code, unrelated refactoring).
   - Exposed secrets (API keys, tokens, passwords).
   - Missing tests for new behavior.
   - Type errors or linting issues.

2. **Update documentation:**
   - If a deliberate architecture decision changed, update the docs.
   - Add docstrings and comments for non-obvious code.
   - Update `README.md` if setup steps changed.

3. **Commit:**
   ```bash
   git add friday/ migrations/ tests/ pyproject.toml docker-compose.yml .env.example
   git commit -m "Implement Milestone X: description"
   ```

   Commit message format:
   ```
   Implement Milestone N: [feature or component]

   - Brief description of what was added/changed.
   - How to verify (e.g., "run pytest", "check /health endpoint").

   Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>
   ```

---

## FRIDAY's Architectural Boundaries

Understanding these boundaries prevents premature design:

### 1. API Layer
- **Responsibility:** HTTP contracts, authentication, request validation, response serialization.
- **Does:** Validate input, enforce auth, rate-limit, create tasks, return status.
- **Does NOT:** Long-running work, LLM calls, external API calls (delegates to services/workers).

### 2. Application/Orchestration Layer
- **Responsibility:** Business logic, tool selection, workflow execution.
- **Does:** Plan multi-step tasks, choose tools, validate results, compose responses.
- **Does NOT:** Directly manipulate database (uses repositories), manage HTTP details.

### 3. Domain Layer
- **Responsibility:** Core concepts, state machines, validation logic.
- **Examples:** `Task`, `Conversation`, `Memory`, `Approval`, `ToolExecution`.
- **No:** HTTP, database-specific code. These are pure Python objects.

### 4. Tool Registry and Policy Enforcement
- **Responsibility:** Declare tools, validate inputs, check permissions, enforce approval gates.
- **Each tool declares:**
  - Input/output schema.
  - Required scopes (permissions).
  - Timeout and retry policy.
  - Whether approval is required.
  - Audit and redaction rules.

### 5. Infrastructure/Provider Adapters
- **Responsibility:** Isolate external service SDKs (LLM, speech, search, storage).
- **Examples:** `LLMProvider`, `TranscriptionProvider`, `ObjectStore`.
- **Does:** Translate domain models to provider APIs, handle provider-specific errors.

### 6. Background Workers and Scheduler
- **Responsibility:** Execute long-running tasks, handle retries and failures.
- **Does:** Claim tasks, persist progress, support cancellation, verify results.
- **Does NOT:** Make policy decisions (policy is in services).

### 7. Device and Voice Clients
- **Responsibility:** Local audio, wake-word detection, user interaction.
- **Devices:** Mac and phone clients, Bluetooth earpiece (audio peripheral only).
- **Cloud responsibilities:** Transcription (STT), reasoning, generation, response synthesis (TTS).

---

## For Every Agent Tool Requirement

When adding a new agent tool (e.g., send email, run code, fetch data), ensure:

1. **Validated inputs:** Tool declares Pydantic input schema; framework validates before dispatch.
2. **Server-side authorization:** Check user permissions and scopes before execution.
3. **Bounded execution:** Timeouts (e.g., 30 seconds), retry limits, resource budgets.
4. **Structured results:** Tool returns a typed result; framework captures and persists it.
5. **Appropriate audit records:** Log tool name, arguments (redacted), outcome, user, and timestamp.
6. **Approval gate for consequential actions:**
   - Read-only tools: Execute with audit.
   - Reversible writes: Execute with audit; log how to undo.
   - Consequential/destructive: Require explicit approval before execution.

**Example: Safe Email Tool**

```python
# Tool contract
class SendEmailInput(BaseModel):
    to: EmailStr
    subject: str
    body: str

class SendEmailOutput(BaseModel):
    sent_at: datetime
    message_id: str

tool_registry.register(
    name="send_email",
    input_schema=SendEmailInput,
    output_schema=SendEmailOutput,
    is_read_only=False,
    requires_approval=True,  # Consequential action
    required_scopes=["email:send"],
    timeout_seconds=30,
)

# Tool implementation
async def send_email(input: SendEmailInput, user: User) -> SendEmailOutput:
    # Input already validated by Pydantic
    # Policy layer has checked user has email:send scope
    # Task has approval (checked before calling)
    
    message = EmailMessage(
        subject=input.subject,
        body=input.body,
        from_email=settings.SMTP_FROM,
        to=[input.to],
    )
    message.send()
    
    # Audit
    audit_tool_execution(
        tool="send_email",
        args={"to": input.to, "subject": input.subject},  # Redacted
        result=SendEmailOutput(...),
        user_id=user.id,
    )
    
    return SendEmailOutput(...)
```

---

## Milestone Checklist

### Milestone 0: Repository and Engineering Baseline

**Expected artifacts:**
- Repository structure matching `architecture.md` Section 5.
- `pyproject.toml` with dependencies (FastAPI, SQLAlchemy, pytest, etc.).
- `docker-compose.yml` for local development (API, PostgreSQL, optional MinIO).
- `.env.example` with placeholders.
- Health endpoint at `/api/v1/health`.
- Structured logging with correlation IDs.
- First test suite (at least `test_health.py`).

**Acceptance:** 
```bash
docker compose up
# Wait for services to start
curl http://localhost:8000/api/v1/health
# Should return: {"status": "healthy", ...}

pytest -v
# Should pass all tests, 0 failures
```

**Next:** Milestone 1 — Authenticated text assistant.

---

### Milestone 1: Authenticated Text Assistant

**Expected artifacts:**
- PostgreSQL migrations (users, devices, conversations, messages).
- User and device models.
- OAuth2 or JWT authentication.
- Conversation service and repository.
- `/api/v1/assistant/ask` endpoint (create message, get response).
- Provider adapter for LLM (e.g., OpenAI).
- Tests for auth, ownership, provider failures.

**Acceptance:**
```bash
# Create a user and device
# Ask a question with valid credentials
curl -X POST http://localhost:8000/api/v1/assistant/ask \
  -H "Authorization: Bearer <token>" \
  -d '{"conversation_id": "...", "message": "What is 2+2?"}'

# Should return: {"response": "4", "task_id": "..."}

# Verify ownership: asking as a different user should not see the conversation

pytest -v
# All tests pass, including auth and ownership tests
```

**Next:** Milestone 2 — Safe tool calling.

---

### Milestone 2: Safe Tool Calling

**Expected artifacts:**
- Tool registry and contracts.
- Input validation (Pydantic schemas).
- Policy layer (checks permissions, approval gates).
- One read-only web search tool.
- Audit logging for tool executions.
- Tests for invalid arguments, permission denial, prompt injection defense.

**Acceptance:**
```bash
# Ask a question that triggers tool use
curl -X POST http://localhost:8000/api/v1/assistant/ask \
  -H "Authorization: Bearer <token>" \
  -d '{"message": "Search for Python framework comparisons"}'

# Should execute search tool, return results with audit

# Try invalid tool arguments: should be rejected
# Try tool not in allowlist: should be rejected
# Try to trick model with prompt injection in web page: should not execute

pytest -v
# All tool tests pass, including security tests
```

**Next:** Milestone 3 — Durable task worker.

---

### Later Milestones

- **Milestone 3:** Durable task worker + retries.
- **Milestone 4:** Research reports + citations.
- **Milestone 5:** Voice MVP (Mac client).
- **Milestone 6:** Memory + scheduled jobs.
- **Milestone 7:** Approval center + integrations.
- **Milestone 8+:** Phone app, developer tools, security monitoring, etc.

See `architecture.md` Section 13 for detailed criteria.

---

## Common Tasks and How to Handle Them

### Task: "Add a new endpoint to get user memory"

**Workflow:**
1. Inspect: Is memory service already implemented?
2. Propose: "Add `/api/v1/memory` GET endpoint. Service fetches user's memory from database."
3. Implement: Create route in `api/routes/memory.py`, implement `MemoryService.list_memory()`.
4. Test: Add test `test_get_memory_returns_user_memory()` and `test_get_memory_blocks_cross_user_access()`.
5. Verify: `pytest -v`, `mypy`, `ruff`.
6. Commit: "Add GET /api/v1/memory endpoint for memory review."

### Task: "Debug failing test: test_worker_retry"

**Workflow:**
1. Inspect: Run the test with verbose output: `pytest -vv tests/integration/test_worker_retry.py`.
2. Identify: What is the failure? Timeout? Assertion error? Exception?
3. Propose: "The worker is not claiming tasks correctly. I will add logging and check the database transaction."
4. Implement: Add logging, review database schema, check for deadlocks.
5. Verify: Run test again; check logs.
6. Commit: "Fix worker task claiming: ensure lease is acquired before state change."

### Task: "Add email integration safely"

**Workflow:**
1. Inspect: Does email service exist? Are there approval gates?
2. Propose: "Add `send_email` tool with approval requirement. Email sent only after user confirms."
3. Implement:
   - Create `EmailProvider` adapter.
   - Define `SendEmailInput` and `SendEmailOutput` schemas.
   - Register tool with `requires_approval=True`.
   - Implement approval workflow in task service.
4. Test: Test invalid email, missing approval, user cancels, user approves.
5. Verify: No hardcoded SMTP credentials; all secrets from environment.
6. Commit: "Add email tool with mandatory approval gate."

### Task: "Review the architecture"

**Workflow:**
1. Inspect: Read `architecture.md` and the current codebase.
2. Propose: "I will survey which milestones have been completed and identify architectural drift."
3. Analyze: Compare `architecture.md` Section 5 (target structure) to actual `friday/` layout.
4. Report: "Milestones 0–2 complete. Milestone 3 in progress. No architectural drift detected."

---

## Rules and Guardrails

### DO

- ✅ Read documentation before changing code.
- ✅ Inspect Git status and understand the current state.
- ✅ Propose changes before implementing.
- ✅ Write tests for new features and security boundaries.
- ✅ Run all tests and report actual results.
- ✅ Review diffs for secrets, accidental changes, and quality.
- ✅ Follow the architecture: API → Services → Persistence → Adapters.
- ✅ Use type hints, Pydantic schemas, and dependency injection.
- ✅ Log and audit sensitive operations.
- ✅ Enforce approval gates for consequential actions.

### DO NOT

- ❌ Overwrite existing files without understanding their purpose.
- ❌ Implement all milestones at once; focus on one.
- ❌ Commit secrets, API keys, or credentials.
- ❌ Claim success without running tests or verification.
- ❌ Skip security testing; include SSRF, prompt injection, auth tests.
- ❌ Add unnecessary dependencies or premature microservices.
- ❌ Create long functions or god classes; keep modules small.
- ❌ Ignore warnings from type checkers or linters.

---

## Summary

Use this skill to implement FRIDAY systematically:

1. **Inspect:** Understand the current state, read docs, identify the milestone.
2. **Propose:** State what will change, why, and how to verify.
3. **Implement:** Write code, tests, and migrations following the architecture.
4. **Verify:** Run tests, type checks, linting, and manual testing.
5. **Report:** List files changed, test results, and next steps.

The goal is **steady, safe progress toward a complete, maintainable personal AI assistant**.

---

**Last updated:** 2026-10-18  
**For:** Claude Code development of FRIDAY
