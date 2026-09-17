"""Tests for AgentResponseOutput schema validation."""

import pytest
from pydantic import ValidationError

from personal_deadline_management_agent.schemas.agent_response_generation import (
    AgentResponseOutput,
)


def test_valid_response_is_accepted():
    """Valid response is accepted."""
    response = AgentResponseOutput(response="You have 5 tasks this week.")
    assert response.response == "You have 5 tasks this week."


def test_empty_response_is_rejected():
    """Empty response is rejected."""
    with pytest.raises(ValidationError) as exc_info:
        AgentResponseOutput(response="")

    errors = exc_info.value.errors()
    assert any(
        error["type"] == "string_too_short" and error["loc"] == ("response",)
        for error in errors
    )


def test_whitespace_only_response_is_rejected():
    """Whitespace-only response is rejected."""
    with pytest.raises(ValidationError) as exc_info:
        AgentResponseOutput(response="   ")

    errors = exc_info.value.errors()
    assert any(
        error["type"] == "value_error" and error["loc"] == ("response",)
        for error in errors
    )


def test_response_longer_than_1000_characters_is_rejected():
    """Response longer than 1000 characters is rejected."""
    long_response = "A" * 1001

    with pytest.raises(ValidationError) as exc_info:
        AgentResponseOutput(response=long_response)

    errors = exc_info.value.errors()
    assert any(
        error["type"] == "string_too_long" and error["loc"] == ("response",)
        for error in errors
    )


def test_response_at_max_length_is_accepted():
    """Response at exactly 1000 characters is accepted."""
    max_response = "B" * 1000
    response = AgentResponseOutput(response=max_response)
    assert len(response.response) == 1000


def test_response_with_unicode_is_accepted():
    """Response with Unicode characters is accepted."""
    response = AgentResponseOutput(
        response="Bạn có 5 nhiệm vụ tuần này với 1 xung đột deadline."
    )
    assert "Bạn" in response.response
    assert "nhiệm vụ" in response.response


def test_response_with_newlines_is_accepted():
    """Response with newlines is accepted."""
    response = AgentResponseOutput(
        response="You have 5 tasks this week.\n\n1 deadline collision detected."
    )
    assert "\n" in response.response
