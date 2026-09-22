"""Deterministic constraint engine for generating valid candidate time slots.

Reuses Phase 10 outputs (feasibility windows, daily pressure) to answer
which time windows a task could fit into. Pure in-memory, no persistence,
no calendar, no LLM.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Sequence

from ..models import TaskPriority, TaskStatus
from ..schemas.workload import (
    CandidateSlot,
    TaskSummary,
)

# Default working hours budget (8 hours). Injected via DI in production.
_DEFAULT_BUDGET_MINUTES = 480


class ReschedulingConstraintService:
    """Deterministic constraint engine for candidate slot generation.

    Stateless except for default budget. Only evaluates whether a candidate
    satisfies all applicable constraints; does NOT decide whether to reschedule.
    """

    def __init__(self, budget_minutes: int = _DEFAULT_BUDGET_MINUTES) -> None:
        """Initialize constraint service.

        Args:
            budget_minutes: Default daily working budget (minutes).
        """
        if not (0 < budget_minutes <= 1440):
            raise ValueError(
                f"budget_minutes must be between 1 and 1440, got {budget_minutes}"
            )
        self._default_budget = budget_minutes

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def find_candidate_slots(
        self,
        task: TaskSummary,
        *,
        all_tasks: Sequence[TaskSummary] | None = None,
        budget_minutes: int | None = None,
    ) -> list[CandidateSlot]:
        """Generate deterministic candidate slots for a single task.

        Constraints enforced:
        - task must have known duration_minutes > 0 and known deadline
        - returned slot start >= feasibility latest_start
        - returned slot end   <= deadline
        - returned slot available_minutes == task duration (fits exactly)
        - slot falls on a day with remaining budget capacity (if workload given)

        For the MVP we produce at most ONE candidate: the canonical slot
        aligned to the feasibility window's earliest valid start that also
        satisfies daily-budget capacity. This provides a deterministic
        yes/no answer without inventing sub-day clock-time availability.

        Args:
            task: Task to schedule.
            all_tasks: Full workload (including this task) for capacity checks.
                       If None/empty, only per-task and feasibility constraints apply.
            budget_minutes: Explicit budget override; defaults to instance value.

        Returns:
            List of CandidateSlot (0 or 1 in MVP). Deterministically ordered.
        """
        budget = budget_minutes if budget_minutes is not None else self._default_budget

        # Unknown duration or deadline -> no slots
        if task.duration_minutes is None or task.deadline is None:
            return []

        # Non-positive durations are structurally invalid (rejected upstream),
        # but treat as no-op here for engine safety.
        if task.duration_minutes <= 0:
            return []

        # Constraint: candidate must end <= deadline
        # Constraint: candidate must start >= latest_start (= deadline - duration)
        latest_start = task.deadline - timedelta(minutes=task.duration_minutes)

        # Canonical slot: the exact feasible window for this task
        candidate_start = latest_start
        candidate_end = task.deadline
        candidate_duration = int((candidate_end - candidate_start).total_seconds() / 60)

        if candidate_duration < task.duration_minutes:
            return []

        # Constraint: slot duration must cover task duration
        if candidate_duration != task.duration_minutes:
            # Means deadline/duration mismatch handled; still produce if fits
            if candidate_duration < task.duration_minutes:
                return []
            candidate_start = task.deadline - timedelta(minutes=task.duration_minutes)
            candidate_end = task.deadline

        # Additional capacity constraint: check daily budget if workload given
        if all_tasks is not None and len(all_tasks) > 0:
            if not self._has_remaining_capacity(task, candidate_start.date(), all_tasks, budget):
                return []

        return [
            CandidateSlot(
                start=candidate_start,
                end=candidate_end,
                available_minutes=task.duration_minutes,
            )
        ]

    def validate_candidate(
        self,
        task: TaskSummary,
        slot: CandidateSlot,
        *,
        all_tasks: Sequence[TaskSummary] | None = None,
        budget_minutes: int | None = None,
    ) -> bool:
        """Validate a concrete candidate slot against all constraints.

        Args:
            task: Task to schedule.
            slot: Candidate slot to validate.
            all_tasks: Full workload for capacity checks.
            budget_minutes: Explicit budget override.

        Returns:
            True if slot satisfies all constraints.
        """
        budget = budget_minutes if budget_minutes is not None else self._default_budget

        # Unknown duration or deadline -> invalid
        if task.duration_minutes is None or task.deadline is None:
            return False

        if task.duration_minutes <= 0:
            return False

        # Constraint: slot duration must be at least task duration
        slot_duration = int((slot.end - slot.start).total_seconds() / 60)
        if slot_duration < task.duration_minutes:
            return False

        # Constraint: slot must end <= deadline
        if slot.end > task.deadline:
            return False

        # Constraint: slot must fall inside feasibility window
        latest_start = task.deadline - timedelta(minutes=task.duration_minutes)
        if slot.start < latest_start:
            return False

        # Constraint: slot must be structurally valid (already validated by schema,
        # but keep for completeness via model validation during slot creation)
        if slot.end <= slot.start:
            return False

        if slot.available_minutes < task.duration_minutes:
            return False

        # Constraint: daily budget capacity
        if all_tasks is not None and len(all_tasks) > 0:
            if not self._has_remaining_capacity(task, slot.start.date(), all_tasks, budget):
                return False

        return True

    # ------------------------------------------------------------------ #
    # Private helpers
    # ------------------------------------------------------------------ #

    def _has_remaining_capacity(
        self,
        task: TaskSummary,
        slot_date: date,
        all_tasks: Sequence[TaskSummary],
        budget: int,
    ) -> bool:
        """Check if slot_date has remaining budget capacity for this task.

        Sums duration of all other tasks with deadlines on slot_date.
        Task itself is excluded from the sum (it would replace its current slot).
        """
        total_on_day = 0
        for other in all_tasks:
            if other.id == task.id:
                continue
            if other.deadline is None or other.duration_minutes is None:
                continue
            if other.deadline.date() != slot_date:
                continue
            # Only active tasks count (TODO/IN_PROGRESS)
            status_val = other.status.value if hasattr(other.status, "value") else other.status
            if status_val not in (TaskStatus.TODO.value, TaskStatus.IN_PROGRESS.value):
                continue
            total_on_day += other.duration_minutes

        return total_on_day + task.duration_minutes <= budget
