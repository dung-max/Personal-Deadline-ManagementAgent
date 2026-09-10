# Personal Deadline Management Agent

FastAPI application for a personal deadline management agent.

## Architecture

- **Agent API** (`/agent/messages`) — primary product interaction (Phase 5+).
- **Direct API** (`/tasks`, `/reminders`) — deterministic capability interface (Phase 2+).
- Both share the same application layer: Module → Service → Repository → PostgreSQL.

Layering: `Handler → Module → Service → Repository / External Adapter`.

## Prerequisites

- Python >= 3.12
- `uv`
- Access to the internal GitLab PyPI registry — set the `GITLAB_READ_TOKEN` env var (used to resolve `genai-core` packages at build/sync time).

## Setup

```bash
uv sync
```

## Database migration (Docker)

Migrations run explicitly — never automatically at application startup.

```bash
docker compose up -d db
docker compose run --rm app uv run alembic upgrade head
docker compose up -d app
```

`docker compose run` waits for PostgreSQL to become healthy (via the `app` service's `depends_on: condition: service_healthy`), then applies pending Alembic migrations. Re-running the migration command is safe: Alembic skips already-applied revisions.

## Run (local dev)

```bash
uv run uvicorn personal_deadline_management_agent.main:app --reload
```

## Run (Docker)

```bash
docker compose up --build
```

## Scheduler

The scheduler runs as a separate container using the same application image.
It processes due reminders periodically, marks them as `SENT`, and delivers notifications via the configured provider.

```bash
# Run all services (API + scheduler + DB)
docker compose up --build

# Run scheduler only
docker compose up --build scheduler

# View scheduler logs
docker compose logs -f scheduler

# Restart scheduler independently
docker compose restart scheduler
```

Scheduler configuration is controlled by environment variables (see `.env.example`):

- `SCHEDULER_TICK_SECONDS` — interval between ticks (default: 60, minimum: 5)
- `SCHEDULER_BATCH_SIZE` — max reminders per tick (default: 100, range: 1–1000)

## Test

```bash
uv run pytest
```

## Environment variables

See `.env.example`. `DATABASE_URL` is canonical; `DB_*` fields are only used as a fallback (never merged).
