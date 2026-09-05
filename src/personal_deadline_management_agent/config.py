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


def load_config() -> Settings:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        database_url = build_connection_string(DatabaseConfig.from_env())
    return Settings(
        database_url=database_url,
        environment=os.getenv("ENVIRONMENT", "development"),
        bedrock_model_id=os.getenv("BEDROCK_MODEL_ID", ""),
        aws_default_region=os.getenv("AWS_DEFAULT_REGION", ""),
    )
