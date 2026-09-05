# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN --mount=type=secret,id=gitlab_read_token \
    UV_INDEX_GITLAB_USERNAME="__token__" \
    UV_INDEX_GITLAB_PASSWORD="$(cat /run/secrets/gitlab_read_token)" \
    uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

COPY alembic.ini migrations/ ./

EXPOSE 8000

CMD ["uv", "run", "--no-dev", "uvicorn", "personal_deadline_management_agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
