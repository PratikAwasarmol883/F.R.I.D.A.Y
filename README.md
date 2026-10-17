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

## Checks

```bash
ruff check friday tests migrations
mypy friday
pytest
```

The compose credentials are local-development defaults only; override via environment for anything else.
