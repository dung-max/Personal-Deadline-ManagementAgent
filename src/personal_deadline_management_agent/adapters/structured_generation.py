"""Structured generation port and Bedrock adapter.

The port (``StructuredGenerationPort``) is the application-level interface
that higher layers depend on. The adapter (``GenaiCoreBedrockAdapter``) is
the only module that imports ``genai_core``.

Sync/async decision: The genai-core-bedrock-llm package is 100% synchronous
(synchronous boto3 client, no async anywhere). The application's DB stack is
also synchronous. No async complexity is introduced.
"""

from __future__ import annotations

import json
import logging
from typing import Protocol, TypeVar

from pydantic import BaseModel

from ..exceptions.llm import LLMGenerationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------------------
# Port (application-level interface)
# ---------------------------------------------------------------------------


class StructuredGenerationPort(Protocol[T]):
    """Application-level interface for structured LLM generation.

    Higher layers (modules, services) depend on this protocol.
    Concrete adapters are injected via constructor.
    """

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        output_type: type[T],
    ) -> T: ...


# ---------------------------------------------------------------------------
# Adapter (genai-core implementation)
# ---------------------------------------------------------------------------


class GenaiCoreBedrockAdapter:
    """Concrete adapter using genai-core-bedrock-llm for structured generation.

    This is the only module that imports ``genai_core``. All other application
    layers depend on ``StructuredGenerationPort``.
    """

    def __init__(self, *, execution_id: str = "") -> None:
        from genai_core.bedrock_llm.aws.bedrock import Bedrock

        try:
            self._bedrock = Bedrock(execution_id=execution_id)
        except ValueError as exc:
            raise LLMGenerationError(str(exc)) from exc

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        output_type: type[T],
    ) -> T:
        """Generate structured output from the LLM.

        Converts the Pydantic ``output_type`` to a JSON Schema, invokes
        Bedrock Converse with constrained output, and validates the response.
        """
        try:
            schema = output_type.model_json_schema()
            response = self._bedrock.converse(
                system_prompt=system_prompt,
                input_text=user_prompt,
                output_schema=schema,
            )
            raw_text = response["output"]["message"]["content"][0]["text"]
            return output_type.model_validate_json(raw_text)
        except LLMGenerationError:
            raise
        except Exception as exc:
            logger.error("LLM generation failed: %s", exc)
            raise LLMGenerationError(str(exc)) from exc
