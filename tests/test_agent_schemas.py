"""Unit tests for the Agent contract schemas."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from personal_deadline_management_agent.schemas import (
    ActionProposal,
    ActionType,
    AgentRequest,
    AgentResponse,
    ProposalStatus,
    ResourceReference,
    ResponseType,
)


# --- AgentRequest -----------------------------------------------------------


def test_valid_agent_request():
    req = AgentRequest(message="Create a task to review the report")
    assert req.message == "Create a task to review the report"


def test_agent_request_rejects_empty_message():
    with pytest.raises(ValidationError):
        AgentRequest(message="")


def test_agent_request_missing_message():
    with pytest.raises(ValidationError):
        AgentRequest()


# --- ResourceReference ------------------------------------------------------


def test_resource_reference_with_id():
    rid = uuid4()
    ref = ResourceReference(id=rid)
    assert ref.id == rid
    assert ref.natural_language is None


def test_resource_reference_with_natural_language():
    ref = ResourceReference(natural_language="my math task")
    assert ref.id is None
    assert ref.natural_language == "my math task"


def test_resource_reference_with_both():
    rid = uuid4()
    ref = ResourceReference(id=rid, natural_language="my math task")
    assert ref.id == rid
    assert ref.natural_language == "my math task"


def test_resource_reference_requires_at_least_one():
    with pytest.raises(ValidationError):
        ResourceReference()


def test_resource_reference_rejects_invalid_uuid():
    with pytest.raises(ValidationError):
        ResourceReference(id="not-a-uuid")


# --- ActionProposal ---------------------------------------------------------


@pytest.mark.parametrize(
    "action_type",
    [
        ActionType.CREATE_TASK,
        ActionType.UPDATE_TASK,
        ActionType.DELETE_TASK,
        ActionType.CREATE_REMINDER,
        ActionType.UPDATE_REMINDER,
        ActionType.DELETE_REMINDER,
    ],
)
def test_action_proposal_supports_all_action_types(action_type):
    proposal = ActionProposal(
        action_type=action_type,
        resource=ResourceReference(id=uuid4()),
    )
    assert proposal.action_type == action_type
    assert proposal.status == ProposalStatus.PROPOSED


def test_action_proposal_with_natural_language_resource():
    proposal = ActionProposal(
        action_type=ActionType.UPDATE_TASK,
        resource=ResourceReference(natural_language="the task about the report"),
        parameters={"status": "COMPLETED"},
    )
    assert proposal.resource.natural_language == "the task about the report"
    assert proposal.parameters == {"status": "COMPLETED"}


def test_action_proposal_rejects_invalid_action_type():
    with pytest.raises(ValidationError):
        ActionProposal(
            action_type="CREATE_SPACESHIP",
            resource=ResourceReference(id=uuid4()),
        )


def test_action_proposal_default_parameters_is_empty():
    proposal = ActionProposal(
        action_type=ActionType.DELETE_TASK,
        resource=ResourceReference(id=uuid4()),
    )
    assert proposal.parameters == {}


def test_action_proposal_allows_resource_none():
    """CREATE_TASK has no target resource — resource=None is valid."""
    proposal = ActionProposal(action_type=ActionType.CREATE_TASK, resource=None)
    assert proposal.resource is None
    assert proposal.action_type == ActionType.CREATE_TASK


def test_action_proposal_custom_status():
    proposal = ActionProposal(
        action_type=ActionType.CREATE_TASK,
        resource=ResourceReference(id=uuid4()),
        status=ProposalStatus.NEEDS_CONFIRMATION,
    )
    assert proposal.status == ProposalStatus.NEEDS_CONFIRMATION


# --- ProposalStatus contract -------------------------------------------------


def test_proposal_status_contract_values():
    values = {s.value for s in ProposalStatus}
    assert values == {
        "PROPOSED",
        "NEEDS_CLARIFICATION",
        "NEEDS_CONFIRMATION",
        "VALIDATED",
        "REJECTED",
    }


# --- AgentResponse ----------------------------------------------------------


def test_valid_agent_response_conversational():
    response = AgentResponse(
        response_type=ResponseType.CONVERSATION,
        message="Sure, here's a summary of your tasks.",
    )
    assert response.response_type == ResponseType.CONVERSATION
    assert response.message == "Sure, here's a summary of your tasks."
    assert response.proposal is None


def test_agent_response_with_proposal():
    proposal = ActionProposal(
        action_type=ActionType.CREATE_TASK,
        resource=ResourceReference(id=uuid4()),
    )
    response = AgentResponse(
        response_type=ResponseType.CONFIRMATION_REQUIRED,
        message="Should I create this task?",
        proposal=proposal,
    )
    assert response.proposal is not None
    assert response.proposal.action_type == ActionType.CREATE_TASK


@pytest.mark.parametrize(
    "response_type",
    [
        ResponseType.ACTION_PROPOSED,
        ResponseType.CLARIFICATION_REQUIRED,
        ResponseType.CONFIRMATION_REQUIRED,
        ResponseType.REJECTED,
        ResponseType.CONVERSATION,
    ],
)
def test_agent_response_all_response_types(response_type):
    response = AgentResponse(response_type=response_type, message="ok")
    assert response.response_type == response_type


def test_agent_response_rejects_unknown_response_type():
    with pytest.raises(ValidationError):
        AgentResponse(response_type="DO_SOMETHING", message="ok")


def test_agent_response_missing_message():
    with pytest.raises(ValidationError):
        AgentResponse(response_type=ResponseType.CONVERSATION)


# --- Serialization / deserialization -----------------------------------------


def test_action_proposal_serialization_round_trip():
    proposal = ActionProposal(
        action_type=ActionType.UPDATE_REMINDER,
        resource=ResourceReference(id=uuid4(), natural_language="reminder for math"),
        parameters={"remindAt": "2026-11-28T07:00:00Z"},
        status=ProposalStatus.VALIDATED,
    )
    data = proposal.model_dump()
    restored = ActionProposal.model_validate(data)
    assert restored == proposal


def test_agent_request_round_trip():
    req = AgentRequest(message="Delete the task about the report")
    restored = AgentRequest.model_validate(req.model_dump())
    assert restored == req


def test_agent_response_round_trip():
    response = AgentResponse(
        response_type=ResponseType.ACTION_PROPOSED,
        message="Proposed an action.",
        proposal=ActionProposal(
            action_type=ActionType.DELETE_TASK,
            resource=ResourceReference(natural_language="the report task"),
        ),
    )
    restored = AgentResponse.model_validate(response.model_dump())
    assert restored == response


def test_resource_reference_round_trip():
    ref = ResourceReference(natural_language="math homework")
    restored = ResourceReference.model_validate(ref.model_dump())
    assert restored == ref
