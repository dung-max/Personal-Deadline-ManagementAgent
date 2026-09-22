"""FastAPI dependency wiring (request-scoped)."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Request
from sqlalchemy.orm import Session, sessionmaker

from .adapters.structured_generation import (
    GenaiCoreBedrockAdapter,
    StructuredGenerationPort,
)
from .config import Settings
from .guardrails.decision_service import DecisionService
from .guardrails.safety_policy import SafetyPolicy
from .modules.pending_confirmation_module import PendingConfirmationModule
from .modules.reminder_module import ReminderModule
from .modules.task_module import TaskModule
from .repositories.pending_confirmation_repository import (
    PendingConfirmationRepository,
)
from .repositories.reminder_repository import ReminderRepository
from .repositories.task_repository import TaskRepository
from .services.action_executor import ActionExecutor
from .services.action_validator import ActionValidator
from .services.agent_interpreter import AgentInterpreter
from .services.agent_response_generator import AgentResponseGenerator
from .services.authorization_service import AuthorizationService
from .services.resource_resolver import ResourceResolver
from .services.workload_analysis_service import WorkloadAnalysisService
from .uow import UnitOfWork


# --- App-level settings ------------------------------------------------------


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


# --- Database ----------------------------------------------------------------


def get_session_factory(request: Request) -> sessionmaker[Session]:
    return request.app.state.session_factory


def get_uow(
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
) -> Iterator[UnitOfWork]:
    uow = UnitOfWork(session_factory())
    try:
        yield uow
    finally:
        uow.close()


# --- Repositories ------------------------------------------------------------


def get_task_repository(
    uow: UnitOfWork = Depends(get_uow),
) -> TaskRepository:
    return uow.tasks


def get_reminder_repository(
    uow: UnitOfWork = Depends(get_uow),
) -> ReminderRepository:
    return uow.reminders


def get_pending_confirmation_repository(
    uow: UnitOfWork = Depends(get_uow),
) -> PendingConfirmationRepository:
    return uow.pending_confirmations


# --- Modules (transaction boundary) -----------------------------------------


def get_task_module(
    uow: UnitOfWork = Depends(get_uow),
) -> TaskModule:
    return TaskModule(uow)


def get_reminder_module(
    uow: UnitOfWork = Depends(get_uow),
) -> ReminderModule:
    return ReminderModule(uow)


def get_pending_confirmation_module(
    uow: UnitOfWork = Depends(get_uow),
) -> PendingConfirmationModule:
    return PendingConfirmationModule(uow)


# --- LLM / Interpreter ------------------------------------------------------


def get_llm() -> StructuredGenerationPort:
    return GenaiCoreBedrockAdapter()


def get_agent_interpreter(
    llm: StructuredGenerationPort = Depends(get_llm),
) -> AgentInterpreter:
    return AgentInterpreter(llm)


def get_agent_response_generator(
    llm: StructuredGenerationPort = Depends(get_llm),
) -> AgentResponseGenerator:
    return AgentResponseGenerator(llm=llm, enable_llm=True)


# --- Validation / Resolution -------------------------------------------------


def get_action_validator() -> ActionValidator:
    return ActionValidator()


def get_resource_resolver(
    task_repository: TaskRepository = Depends(get_task_repository),
    reminder_repository: ReminderRepository = Depends(get_reminder_repository),
) -> ResourceResolver:
    return ResourceResolver(
        task_repository=task_repository,
        reminder_repository=reminder_repository,
    )


# --- Decision (authorization + safety) --------------------------------------


def get_authorization_service(
    settings: Settings = Depends(get_settings),
) -> AuthorizationService:
    return AuthorizationService(
        default_user_id=settings.default_user_id,
        single_user_mode=settings.single_user_mode,
    )


def get_safety_policy() -> SafetyPolicy:
    return SafetyPolicy()


def get_decision_service(
    authorization_service: AuthorizationService = Depends(get_authorization_service),
    safety_policy: SafetyPolicy = Depends(get_safety_policy),
) -> DecisionService:
    return DecisionService(
        authorization_service=authorization_service,
        safety_policy=safety_policy,
    )


# --- Execution ---------------------------------------------------------------


def get_workload_analysis_service(
    settings: Settings = Depends(get_settings),
) -> WorkloadAnalysisService:
    return WorkloadAnalysisService(budget_minutes=settings.daily_working_minutes)


def get_action_executor(
    task_module: TaskModule = Depends(get_task_module),
    reminder_module: ReminderModule = Depends(get_reminder_module),
    workload_analysis_service: WorkloadAnalysisService = Depends(get_workload_analysis_service),
) -> ActionExecutor:
    return ActionExecutor(
        task_module=task_module,
        reminder_module=reminder_module,
        workload_analysis_service=workload_analysis_service,
    )
