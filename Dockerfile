FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY friday/ ./friday/
RUN pip install --no-cache-dir .

COPY alembic.ini ./
COPY migrations/ ./migrations/

RUN useradd -m -u 1000 friday && chown -R friday:friday /app
USER friday

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fs http://localhost:8000/api/v1/health || exit 1

CMD ["uvicorn", "friday.app:app", "--host", "0.0.0.0", "--port", "8000"]
