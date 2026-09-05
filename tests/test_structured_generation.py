"""Unit tests for the structured generation port and Bedrock adapter.

No real AWS Bedrock calls. Tests use a fake port implementation and mock
the genai-core Bedrock client for adapter-level tests.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel, ValidationError

from personal_deadline_management_agent.adapters.structured_generation import (
    GenaiCoreBedrockAdapter,
    StructuredGenerationPort,
)
from personal_deadline_management_agent.exceptions.llm import LLMGenerationError


# ---------------------------------------------------------------------------
# Test output models
# ---------------------------------------------------------------------------


class FakeAction(BaseModel):
    action: str
    target: str


class FakeActionWithOptional(BaseModel):
    action: str
    target: str | None = None


# ---------------------------------------------------------------------------
# Fake / stub implementations
# ---------------------------------------------------------------------------


class FakeStructuredGenerationAdapter:
    """Fake implementation for port-contract tests."""

    def __init__(self, *, response: Any = None) -> None:
        self._response = response or FakeAction(action="CREATE", target="task")
        self._calls: list[dict[str, Any]] = []

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        output_type: type,
    ) -> Any:
        self._calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "output_type": output_type,
            }
        )
        return self._response


class FailingAdapter:
    """Fake adapter that always raises LLMGenerationError."""

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        output_type: type,
    ) -> Any:
        raise LLMGenerationError("simulated failure")


# ---------------------------------------------------------------------------
# Port-level / contract tests
# ---------------------------------------------------------------------------


class TestStructuredGenerationPort:
    """Contract tests: any StructuredGenerationPort implementation must work."""

    def test_fake_adapter_returns_typed_result(self):
        adapter = FakeStructuredGenerationAdapter()
        result = adapter.generate(
            system_prompt="system",
            user_prompt="create a task",
            output_type=FakeAction,
        )
        assert isinstance(result, FakeAction)
        assert result.action == "CREATE"

    def test_fake_adapter_respects_output_type(self):
        """The adapter must return exactly the requested output_type."""
        adapter = FakeStructuredGenerationAdapter(
            response=FakeAction(action="DELETE", target="reminder")
        )
        result = adapter.generate(
            system_prompt="s",
            user_prompt="u",
            output_type=FakeAction,
        )
        assert type(result) is FakeAction

    def test_fake_adapter_passes_all_arguments(self):
        adapter = FakeStructuredGenerationAdapter()
        adapter.generate(
            system_prompt="you are helpful",
            user_prompt="list tasks",
            output_type=FakeAction,
        )
        assert adapter._calls[0]["system_prompt"] == "you are helpful"
        assert adapter._calls[0]["user_prompt"] == "list tasks"
        assert adapter._calls[0]["output_type"] is FakeAction

    def test_failing_adapter_raises_llm_error(self):
        adapter = FailingAdapter()
        with pytest.raises(LLMGenerationError):
            adapter.generate(
                system_prompt="s",
                user_prompt="u",
                output_type=FakeAction,
            )

    def test_port_protocol_accepts_fake(self):
        """A fake adapter satisfying the protocol is accepted as StructuredGenerationPort."""
        adapter = FakeStructuredGenerationAdapter()

        # Structural subtyping: check the protocol methods exist
        assert hasattr(adapter, "generate")

        # Functional check: calling through the expected interface works
        def use_port(port: StructuredGenerationPort) -> FakeAction:
            return port.generate(
                system_prompt="s",
                user_prompt="u",
                output_type=FakeAction,
            )

        result = use_port(adapter)
        assert isinstance(result, FakeAction)


# ---------------------------------------------------------------------------
# Adapter tests (mocked genai-core)
# ---------------------------------------------------------------------------


class TestGenaiCoreBedrockAdapter:
    """Unit tests for the concrete Bedrock adapter using mocked genai-core."""

    @staticmethod
    def _adapter_with(bedrock: MagicMock) -> GenaiCoreBedrockAdapter:
        """Build an adapter with __init__ bypassed so Bedrock is injected directly."""
        adapter = GenaiCoreBedrockAdapter.__new__(GenaiCoreBedrockAdapter)
        adapter._bedrock = bedrock
        return adapter

    def test_generate_success(self):
        expected = FakeAction(action="CREATE", target="task")
        mock_bedrock = MagicMock()
        mock_bedrock.converse.return_value = {
            "output": {
                "message": {
                    "content": [{"text": expected.model_dump_json()}],
                },
            },
        }

        adapter = self._adapter_with(mock_bedrock)

        result = adapter.generate(
            system_prompt="You are a helpful assistant.",
            user_prompt="Create a task called 'review'",
            output_type=FakeAction,
        )

        assert isinstance(result, FakeAction)
        assert result.action == "CREATE"
        assert result.target == "task"
        mock_bedrock.converse.assert_called_once()

    def test_generate_passes_json_schema(self):
        mock_bedrock = MagicMock()
        mock_bedrock.converse.return_value = {
            "output": {
                "message": {
                    "content": [
                        {"text": FakeAction(action="x", target="y").model_dump_json()}
                    ],
                },
            },
        }

        adapter = self._adapter_with(mock_bedrock)

        adapter.generate(
            system_prompt="s",
            user_prompt="u",
            output_type=FakeAction,
        )

        call_kwargs = mock_bedrock.converse.call_args
        schema_arg = call_kwargs.kwargs.get("output_schema") or call_kwargs[1].get(
            "output_schema"
        )
        assert schema_arg is not None
        assert "properties" in schema_arg
        assert "action" in schema_arg["properties"]
        assert "target" in schema_arg["properties"]

    def test_generate_wraps_bedrock_error(self):
        mock_bedrock = MagicMock()
        mock_bedrock.converse.side_effect = RuntimeError("bedrock timeout")

        adapter = self._adapter_with(mock_bedrock)

        with pytest.raises(LLMGenerationError, match="bedrock timeout"):
            adapter.generate(
                system_prompt="s",
                user_prompt="u",
                output_type=FakeAction,
            )

    def test_generate_wraps_invalid_json_response(self):
        mock_bedrock = MagicMock()
        mock_bedrock.converse.return_value = {
            "output": {
                "message": {
                    "content": [{"text": "not valid json at all"}],
                },
            },
        }

        adapter = self._adapter_with(mock_bedrock)

        with pytest.raises(LLMGenerationError):
            adapter.generate(
                system_prompt="s",
                user_prompt="u",
                output_type=FakeAction,
            )

    def test_generate_wraps_pydantic_validation_error(self):
        """LLM returns valid JSON but wrong schema (missing required field)."""
        mock_bedrock = MagicMock()
        mock_bedrock.converse.return_value = {
            "output": {
                "message": {
                    "content": [{"text": json.dumps({"action": "CREATE"})}],
                },
            },
        }

        adapter = self._adapter_with(mock_bedrock)

        with pytest.raises(LLMGenerationError):
            adapter.generate(
                system_prompt="s",
                user_prompt="u",
                output_type=FakeAction,
            )

    def test_generate_wraps_missing_output_key(self):
        """LLM returns response with unexpected structure."""
        mock_bedrock = MagicMock()
        mock_bedrock.converse.return_value = {"unexpected": "shape"}

        adapter = self._adapter_with(mock_bedrock)

        with pytest.raises(LLMGenerationError):
            adapter.generate(
                system_prompt="s",
                user_prompt="u",
                output_type=FakeAction,
            )

    def test_adapter_wraps_missing_model_id(self):
        """Bedrock.__init__ raises ValueError when BEDROCK_MODEL_ID is not set."""
        with patch(
            "genai_core.bedrock_llm.aws.bedrock.Bedrock"
        ) as MockBedrock:
            MockBedrock.side_effect = ValueError(
                "BEDROCK_MODEL_ID environment variable is not set."
            )
            with pytest.raises(LLMGenerationError, match="BEDROCK_MODEL_ID"):
                GenaiCoreBedrockAdapter()


# ---------------------------------------------------------------------------
# LLMGenerationError tests
# ---------------------------------------------------------------------------


class TestLLMGenerationError:
    def test_default_message(self):
        exc = LLMGenerationError()
        assert str(exc) == "LLM generation failed"
        assert exc.message == "LLM generation failed"

    def test_custom_message(self):
        exc = LLMGenerationError("timeout")
        assert exc.message == "timeout"
        assert str(exc) == "timeout"

    def test_is_exception(self):
        assert issubclass(LLMGenerationError, Exception)
