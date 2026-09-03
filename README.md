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

## Run (local dev)

```bash
uv run uvicorn personal_deadline_management_agent.main:app --reload
```

## Run (Docker)

```bash
docker compose up --build
```

## Test

```bash
uv run pytest
```

## Environment variables

See `.env.example`. `DATABASE_URL` is canonical; `DB_*` fields are only used as a fallback (never merged).
