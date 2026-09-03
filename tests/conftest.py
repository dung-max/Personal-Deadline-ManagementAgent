"""Pytest session setup.

Sets a default DATABASE_URL before test modules are imported so that
`main.py`'s module-level `app = create_app()` can resolve configuration.
Engine creation is lazy, so no live database is required for these tests.
"""

import os

os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg2://u:p@localhost:5432/db"
)