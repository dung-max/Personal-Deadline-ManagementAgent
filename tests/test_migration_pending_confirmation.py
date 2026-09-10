"""Migration tests for 0002_create_pending_confirmations.

Verifies that the Alembic migration creates the pending_confirmations table
with the correct schema and that upgrade/downgrade round-trips cleanly.
Uses SQLite in-memory so no live PostgreSQL is required.
"""

from __future__ import annotations

import os

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def _alembic_config() -> Config:
    """Return an Alembic Config pointing at the project's alembic.ini."""
    # alembic.ini is at the project root; env.py uses load_config() which
    # needs DATABASE_URL — set a dummy so import succeeds.  The actual
    # engine is injected via attributes below.
    os.environ.setdefault(
        "DATABASE_URL", "postgresql+psycopg2://u:p@localhost:5432/db"
    )
    cfg = Config("alembic.ini")
    return cfg


def _run_upgrade_downgrade_on_sqlite() -> None:
    """Helper: run upgrade head then downgrade to 0001 on SQLite in-memory."""
    engine = create_engine("sqlite:///:memory:")
    cfg = _alembic_config()
    # Inject the SQLite connection so env.py uses it instead of creating
    # a new engine from DATABASE_URL.
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        assert "tasks" in tables
        assert "reminders" in tables
        assert "pending_confirmations" in tables

        # Verify pending_confirmations columns
        cols = {c["name"]: c for c in inspector.get_columns("pending_confirmations")}
        assert "id" in cols
        assert "user_id" in cols
        assert "execution_command" in cols
        assert "created_at" in cols
        assert "updated_at" in cols
        assert "expires_at" in cols
        assert "status" in cols
        # updated_at must be nullable (inherited from Base, unlike tasks/reminders)
        assert cols["updated_at"]["nullable"] is True
        assert cols["execution_command"]["nullable"] is False
        assert cols["expires_at"]["nullable"] is False
        assert cols["status"]["nullable"] is False

        # Verify indexes
        indexes = {idx["name"] for idx in inspector.get_indexes("pending_confirmations")}
        assert "ix_pending_confirmations_user_id" in indexes
        assert "ix_pending_confirmations_status" in indexes
        assert "ix_pending_confirmations_expires_at" in indexes

        # Verify downgrade removes the table.
        # NOTE: a fresh Inspector is required here — SQLAlchemy's Inspector
        # caches get_table_names(), so the one created before the downgrade
        # would still report the dropped table.
        command.downgrade(cfg, "0001")
        tables_after = set(inspect(connection).get_table_names())
        assert "pending_confirmations" not in tables_after
        assert "tasks" in tables_after
        assert "reminders" in tables_after

        # Re-upgrade
        command.upgrade(cfg, "head")
        tables_re = set(inspect(connection).get_table_names())
        assert "pending_confirmations" in tables_re

    engine.dispose()


def test_migration_0002_upgrade_creates_pending_confirmations():
    _run_upgrade_downgrade_on_sqlite()


def test_migration_0002_updated_at_is_nullable():
    """Regression: updated_at must be nullable (Base default), not NOT NULL."""
    engine = create_engine("sqlite:///:memory:")
    cfg = _alembic_config()
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")
        inspector = inspect(connection)
        cols = {c["name"]: c for c in inspector.get_columns("pending_confirmations")}
        assert cols["updated_at"]["nullable"] is True
        # Contrast: tasks.updated_at is NOT NULL (overridden in model)
        task_cols = {c["name"]: c for c in inspector.get_columns("tasks")}
        assert task_cols["updated_at"]["nullable"] is False
    engine.dispose()


def test_migration_0002_alembic_version_is_0002():
    engine = create_engine("sqlite:///:memory:")
    cfg = _alembic_config()
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")
        version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version == "0002"
    engine.dispose()
