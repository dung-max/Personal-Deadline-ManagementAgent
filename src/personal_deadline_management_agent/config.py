"""Application configuration.

Precedence rule (P0.4):
- If DATABASE_URL is set, it is used verbatim and DB_* fields are ignored.
- Otherwise DatabaseConfig.from_env() + build_connection_string() is used.
These two sources are never merged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from genai_core.genai_shared.database import DatabaseConfig, build_connection_string


@dataclass(frozen=True)
class Settings:
    database_url: str
    environment: str = "development"

    # LLM / Bedrock
    bedrock_model_id: str = ""
    aws_default_region: str = ""

    # Confirmation TTL
    pending_confirmation_ttl_seconds: int = 300

    # Scheduler
    scheduler_tick_seconds: int = 60
    scheduler_batch_size: int = 100

    # TODO(MVP): temporary single-user authorization.
    # Replace with a real user/account + ownership model for multi-user.
    default_user_id: str = "00000000-0000-0000-0000-000000000001"
    single_user_mode: bool = False

    # Daily working budget for workload analysis (minutes)
    daily_working_minutes: int = 480

    def __post_init__(self) -> None:
        if self.scheduler_tick_seconds < 5:
            raise ValueError(
                f"scheduler_tick_seconds must be >= 5, got {self.scheduler_tick_seconds}"
            )
        if not (1 <= self.scheduler_batch_size <= 1000):
            raise ValueError(
                f"scheduler_batch_size must be between 1 and 1000, got {self.scheduler_batch_size}"
            )
        if not (0 < self.daily_working_minutes <= 1440):
            raise ValueError(
                f"daily_working_minutes must be between 1 and 1440 (minutes in a day), got {self.daily_working_minutes}"
            )


def load_config() -> Settings:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        database_url = build_connection_string(DatabaseConfig.from_env())
    return Settings(
        database_url=database_url,
        environment=os.getenv("ENVIRONMENT", "development"),
        bedrock_model_id=os.getenv("BEDROCK_MODEL_ID", ""),
        aws_default_region=os.getenv("AWS_DEFAULT_REGION", ""),
        default_user_id=os.getenv(
            "DEFAULT_USER_ID", "00000000-0000-0000-0000-000000000001"
        ),
        single_user_mode=os.getenv("SINGLE_USER_MODE", "false").lower()
        in {"1", "true", "yes", "on"},
        pending_confirmation_ttl_seconds=int(
            os.getenv("PENDING_CONFIRMATION_TTL_SECONDS", "300")
        ),
        scheduler_tick_seconds=int(
            os.getenv("SCHEDULER_TICK_SECONDS", "60")
        ),
        scheduler_batch_size=int(
            os.getenv("SCHEDULER_BATCH_SIZE", "100")
        ),
        daily_working_minutes=int(
            os.getenv("DAILY_WORKING_MINUTES", "480")
        ),
    )
