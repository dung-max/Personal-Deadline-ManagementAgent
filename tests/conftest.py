"""Pytest session setup.

Sets a default DATABASE_URL before test modules are imported so that
`main.py`'s module-level `app = create_app()` can resolve configuration.
Engine creation is lazy, so no live database is required for these tests.
"""

import os
from datetime import datetime, timezone
from sqlalchemy import types
from sqlalchemy.dialects.sqlite.base import DATETIME
from sqlalchemy.dialects.sqlite.pysqlite import SQLiteDialect_pysqlite

os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg2://u:p@localhost:5432/db"
)


class SQLiteUTCDateTime(DATETIME):
    """Ensure SQLite test engine loads datetimes as UTC timezone-aware."""

    def result_processor(self, dialect, coltype):
        base_proc = super().result_processor(dialect, coltype)

        def process(value):
            if value is None:
                return None
            dt = base_proc(value) if base_proc else value
            if isinstance(dt, datetime) and dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt

        return process


SQLiteDialect_pysqlite.colspecs[types.DateTime] = SQLiteUTCDateTime
SQLiteDialect_pysqlite.colspecs[DATETIME] = SQLiteUTCDateTime
