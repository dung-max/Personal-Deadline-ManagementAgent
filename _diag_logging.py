"""Quick diagnostic: check logging state after alembic migration."""
import logging
import os
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://u:p@localhost:5432/db")

# Import the notification logger BEFORE running migrations (like pytest collection)
from personal_deadline_management_agent.adapters import notification
notif_logger = logging.getLogger("personal_deadline_management_agent.adapters.notification")
print("BEFORE migration:")
print(f"  notif logger disabled: {notif_logger.disabled}  level: {notif_logger.level}")
print(f"  root level: {logging.getLogger().level}  handlers: {logging.getLogger().handlers}")
print(f"  root manager disableExistingLoggers attr: {getattr(logging.getLogger().manager, 'disable', 'N/A')}")

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

engine = create_engine("sqlite:///:memory:")
cfg = Config("alembic.ini")
with engine.begin() as connection:
    cfg.attributes["connection"] = connection
    command.upgrade(cfg, "head")

print("\nAFTER migration:")
print(f"  notif logger disabled: {notif_logger.disabled}  level: {notif_logger.level}")
print(f"  root level: {logging.getLogger().level}  handlers: {logging.getLogger().handlers}")
engine.dispose()
