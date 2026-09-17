"""Agent response generation schemas.

Output schema for LLM-generated natural-language responses.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError


class AgentResponseOutput(BaseModel):
    """LLM-generated natural-language response for execution results.

    Used by ``AgentResponseGenerator`` to convert structured execution results
    (especially ``WorkloadAnalysisResult``) into user-friendly conversational
    messages.  The response field is constrained to prevent LLM verbosity.
    """

    response: str = Field(
        min_length=1,
        max_length=1000,
        description="Natural-language response explaining the execution result to the user",
    )

    @field_validator("response", mode="after")
    @classmethod
    def validate_not_whitespace_only(cls, v: str) -> str:
        """Reject whitespace-only responses."""
        if not v.strip():
            raise PydanticCustomError(
                "value_error",
                "response cannot be empty or whitespace-only",
            )
        return v

    model_config = ConfigDict(populate_by_name=True)
