# F.R.I.D.A.Y

Personal AI assistant. See `docs/plan.md` and `docs/architecture.md`.

## Local setup (Milestone 0)

```bash
cp .env.example .env
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

docker compose up -d friday-db      # Postgres on 127.0.0.1:5433 (override with POSTGRES_HOST_PORT)
alembic upgrade head
uvicorn friday.app:app --reload     # or: docker compose up --build
curl http://127.0.0.1:8000/api/v1/health
```

## Using the API (Milestone 1)

There is no public signup. An operator creates users and devices on the server:

```bash
python -m friday.cli create-user --name "Pratik" --timezone Asia/Kolkata
python -m friday.cli register-device --user-id <user_id> --name macbook --type mac   # prints the token once
python -m friday.cli revoke-device --device-id <device_id>
# with Docker Compose: docker compose exec friday-api python -m friday.cli ...
```

Set `ANTHROPIC_API_KEY` in `.env` to enable the assistant, then:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/assistant/ask \
  -H "Authorization: Bearer <token>" -H "content-type: application/json" \
  -d '{"message": "What is 2+2?"}'
curl http://127.0.0.1:8000/api/v1/conversations/<conversation_id> -H "Authorization: Bearer <token>"
```

### Tools (Milestone 2)

The assistant can call a read-only `web_search` tool. It is available to a device only when all of
these hold: `SEARCH_API_KEY` is set (Tavily), `web_search` is in `ENABLED_TOOLS`, and the device was
granted the scope:

```bash
python -m friday.cli register-device --user-id <user_id> --name macbook --type mac --scopes tool:web_search
python -m friday.cli set-device-scopes --device-id <device_id> --scopes tool:web_search   # replaces; empty clears
python -m friday.cli tool-log --user-id <user_id> --limit 20                                # audit view
```

`POST /assistant/ask` then also returns `sources` (URLs actually retrieved this turn) and `tools_used`.
Search results are treated as untrusted data; every tool call is validated, scoped, bounded, and
recorded in `tool_executions` (arguments redacted, result bodies not stored).

### Background tasks (Milestone 3)

Long-running work runs in a separate worker process that survives client disconnects and crashes.

```bash
docker compose up -d --build      # API + worker + database (or run `python -m friday.worker` yourself)

curl -X POST http://127.0.0.1:8000/api/v1/tasks \
  -H "Authorization: Bearer <token>" -H "Idempotency-Key: my-request-1" -H "content-type: application/json" \
  -d '{"type": "assistant.ask", "input": {"message": "Summarize FastAPI lifespan events"}}'   # 202 + task id
curl http://127.0.0.1:8000/api/v1/tasks/<task_id>          -H "Authorization: Bearer <token>"   # status/result
curl http://127.0.0.1:8000/api/v1/tasks/<task_id>/events   -H "Authorization: Bearer <token>"   # progress
curl -X POST http://127.0.0.1:8000/api/v1/tasks/<task_id>/cancel -H "Authorization: Bearer <token>"
```

- Repeating a request with the same `Idempotency-Key` returns the same task (200); a different payload with that key is `409`.
- Tasks are retried with backoff only for provider outages; a worker that dies is recovered when its lease expires.
- Tasks run with the creating device's *current* scopes; revoking the device cancels its queued work.
- Operators: `python -m friday.cli tasks --user-id <id> [--status failed]` and `requeue-task --task-id <id>`.

### Research reports (Milestone 4)

Research runs as a background task and produces a cited report. The device needs both tool scopes:

```bash
python -m friday.cli register-device --user-id <id> --name macbook --type mac --scopes tool:web_search,tool:web_fetch
curl -X POST http://127.0.0.1:8000/api/v1/research -H "Authorization: Bearer <token>" \
  -H "content-type: application/json" -d '{"question": "What changed in Python 3.14?", "max_sources": 4}'   # 202 + task
curl http://127.0.0.1:8000/api/v1/tasks/<task_id>    -H "Authorization: Bearer <token>"   # result.report_id when done
curl http://127.0.0.1:8000/api/v1/reports/<report_id> -H "Authorization: Bearer <token>"   # JSON + markdown
curl -X DELETE http://127.0.0.1:8000/api/v1/reports/<report_id> -H "Authorization: Bearer <token>"
```

Every finding cites only pages that were actually fetched; findings without a valid citation are marked
`uncertain`, and sources carry retrieval times and content hashes. The server cannot verify that a source
truly supports a claim, and says so in the report's limitations. Page fetching is SSRF-protected (public
addresses only, connection pinned to the validated IP, redirects re-validated); the model may only fetch URLs
from search results or from the question. Real-network fetching is opt-in tested: `FRIDAY_LIVE_FETCH=1 pytest tests/test_fetch_live.py`.

Tests use a separate `friday_test` database on the same Postgres server (created automatically), so
`docker compose up -d friday-db` must be running. `tests/test_provider_smoke.py` and `tests/test_tools_smoke.py` call real
services only when `ANTHROPIC_API_KEY` (and `SEARCH_API_KEY` for the tools test) are set.

## Checks

```bash
ruff check friday tests migrations
ruff format --check friday tests migrations
mypy friday
pytest
```

The compose credentials are local-development defaults only; override via environment for anything else.
