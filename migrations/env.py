"""Alembic migration environment (synchronous).

Database configuration is sourced from the application's own configuration
(``personal_deadline_management_agent.config.load_config``) so that credentials
are never hardcoded in ``alembic.ini``.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from genai_core.genai_shared.database import Base
from personal_deadline_management_agent import models  # noqa: F401  (register tables)
from personal_deadline_management_agent.config import load_config

config = context.config

# Configure Alembic's own logging from alembic.ini — but only in production
# mode.  When a connection is injected (tests), fileConfig() would call
# logging.config.fileConfig() with disable_existing_loggers=True, which
# disables every logger not listed in alembic.ini (including the
# application's loggers) and breaks pytest's caplog for later tests.
if config.config_file_name is not None and "connection" not in config.attributes:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = load_config().database_url
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    # Tests may inject an existing connection (e.g. SQLite in-memory) via
    # ``config.attributes["connection"]``; production always builds its own
    # engine from the application configuration.
    connectable = config.attributes.get("connection", None)
    if connectable is None:
        configuration = config.get_section(config.config_ini_section, {})
        configuration["sqlalchemy.url"] = load_config().database_url
        connectable = engine_from_config(
            configuration,
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )

    # connectable may already be a Connection (test inject) or an Engine.
    from sqlalchemy.engine import Connection as SAConnection
    if isinstance(connectable, SAConnection):
        context.configure(connection=connectable, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()