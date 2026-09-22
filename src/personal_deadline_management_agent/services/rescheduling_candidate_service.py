"""Deterministic multi-task candidate generation for Phase 11 rescheduling.

Wraps ReschedulingConstraintService to analyze an entire workload and
produce ReschedulingSuggestion / ReschedulingResult.
"""

from __future__ import annotations

from datetime import date
from typing import Sequence

from ..models import TaskPriority, TaskStatus
from ..schemas.workload import (
    ReschedulingResult,
    ReschedulingSuggestion,
    TaskSummary,
)
from .rescheduling_constraint_service import ReschedulingConstraintService
from .workload_analysis_service import WorkloadAnalysisService

# Default budget reused from constraint / workload services
_DEFAULT_BUDGET_MINUTES = 480

_REASON = (
    "Fits within the task feasibility window and remaining daily capacity."
)


def _priority_key(priority: TaskPriority | str) -> int:
    """Map priority to sort key (HIGH=0, MEDIUM=1, LOW=2)."""
    val = priority.value if isinstance(priority, TaskPriority) else str(priority)
    return {"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(val, 99)


def _task_sort_key(task: TaskSummary) -> tuple:
    """Deterministic sort key: deadline ASC, priority DESC, name ASC, id ASC."""
    deadline_key = (task.deadline is None, task.deadline)
    priority_key = _priority_key(task.priority)
    return (deadline_key, priority_key, task.task_name, str(task.id))


class ReschedulingCandidateService:
    """Stateless service that generates rescheduling candidates for a workload.

    Delegates single-task constraint checks to ReschedulingConstraintService.
    Reuses WorkloadAnalysisService only for overloaded-day detection to stay
    consistent with Phase 10 overload semantics.

    No database, no LLM, no side effects.
    """

    def __init__(
        self,
        *,
        constraint_service: ReschedulingConstraintService | None = None,
        workload_service: WorkloadAnalysisService | None = None,
        budget_minutes: int = _DEFAULT_BUDGET_MINUTES,
    ) -> None:
        if not (0 < budget_minutes <= 1440):
            raise ValueError(
                f"budget_minutes must be between 1 and 1440, got {budget_minutes}"
            )
        self._default_budget = budget_minutes
        self._constraint_service = constraint_service or ReschedulingConstraintService(
            budget_minutes=budget_minutes
        )
        self._workload_service = workload_service or WorkloadAnalysisService(
            budget_minutes=budget_minutes
        )

    def generate_candidates(
        self,
        tasks: Sequence[TaskSummary],
        *,
        budget_minutes: int | None = None,
    ) -> ReschedulingResult:
        """Generate deterministic rescheduling candidates for a workload.

        Args:
            tasks: Sequence of TaskSummary to analyze.
            budget_minutes: Budget override; defaults to instance budget.

        Returns:
            ReschedulingResult with deterministic ordering.
        """
        budget = budget_minutes if budget_minutes is not None else self._default_budget

        # Use a fresh constraint service if caller overrides budget
        constraint = (
            self._constraint_service
            if budget == self._default_budget
            else ReschedulingConstraintService(budget_minutes=budget)
        )

        # Handle empty input early
        if not tasks:
            return ReschedulingResult()

        # Deduplicate by id, keep first occurrence (deterministic)
        seen: set = set()
        unique: list[TaskSummary] = []
        for t in tasks:
            if t.id not in seen:
                seen.add(t.id)
                unique.append(t)

        # Filter out structurally invalid tasks before workload analysis
        # (negative/zero durations would violate WorkloadAnalysisResult schema)
        valid_for_workload = [
            t for t in unique
            if t.duration_minutes is None or t.duration_minutes > 0
        ]

        # Overloaded days from Phase-10 workload analysis on valid tasks only
        workload_result = self._workload_service.analyze(valid_for_workload, budget_minutes=budget)
        overloaded_days: list[date] = sorted(w.date for w in workload_result.overload_warnings)

        suggestions: list[ReschedulingSuggestion] = []
        unscheduled: list[TaskSummary] = []

        for task in unique:
            # Only active tasks are eligible; completed/cancelled go to unscheduled
            status_val = task.status.value if isinstance(task.status, TaskStatus) else task.status
            if status_val not in (TaskStatus.TODO.value, TaskStatus.IN_PROGRESS.value):
                # Completed/cancelled tasks are excluded from rescheduling entirely
                continue

            if task.duration_minutes is None or task.deadline is None:
                unscheduled.append(task)
                continue

            if task.duration_minutes <= 0:
                unscheduled.append(task)
                continue

            slots = constraint.find_candidate_slots(
                task, all_tasks=unique, budget_minutes=budget
            )
            if not slots:
                unscheduled.append(task)
                continue

            # MVP: at most one slot per task
            best = sorted(slots, key=lambda s: (s.start, s.end))[0]
            suggestions.append(
                ReschedulingSuggestion(
                    task_id=task.id,
                    task_name=task.task_name,
                    duration_minutes=task.duration_minutes,
                    current_deadline=task.deadline,
                    candidate_slot=best,
                    reason=_REASON,
                )
            )

        # Deterministic ordering
        suggestions.sort(
            key=lambda s: (
                s.current_deadline,
                _priority_key(
                    next((t.priority for t in unique if t.id == s.task_id), TaskPriority.LOW)
                ),
                s.task_name,
                str(s.task_id),
            )
        )
        unscheduled.sort(key=_task_sort_key)

        return ReschedulingResult(
            suggestions=suggestions,
            overloaded_days=overloaded_days,
            unscheduled_tasks=unscheduled,
        )
