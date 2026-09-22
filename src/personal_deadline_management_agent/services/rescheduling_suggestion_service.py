"""PDMA-111: Business-level rescheduling suggestion service.

Provides a clean API for generating rescheduling suggestions for a workload.
Orchestrates PDMA-110 candidate generation and prepares results for consumption
by agent integration, LLM explanation, and confirmation workflows.
"""

from __future__ import annotations

from typing import Sequence

from ..schemas.workload import ReschedulingResult, TaskSummary
from .rescheduling_candidate_service import ReschedulingCandidateService

# Default budget reused from candidate service
_DEFAULT_BUDGET_MINUTES = 480


class ReschedulingSuggestionService:
    """Business service for generating rescheduling suggestions.

    Orchestrates PDMA-110 candidate generation to produce ReschedulingResult.
    This layer provides a business-focused API and can later be extended with
    business rules (e.g., filtering, ranking policies) without changing PDMA-110.

    Stateless, deterministic, no database, no LLM, no side effects.
    """

    def __init__(
        self,
        *,
        candidate_service: ReschedulingCandidateService | None = None,
        budget_minutes: int = _DEFAULT_BUDGET_MINUTES,
    ) -> None:
        """Initialize the suggestion service.

        Args:
            candidate_service: Optional injected candidate service (for testing).
            budget_minutes: Default daily working budget in minutes.
        """
        if not (0 < budget_minutes <= 1440):
            raise ValueError(
                f"budget_minutes must be between 1 and 1440, got {budget_minutes}"
            )
        self._default_budget = budget_minutes
        self._candidate_service = candidate_service or ReschedulingCandidateService(
            budget_minutes=budget_minutes
        )

    def suggest_rescheduling(
        self,
        tasks: Sequence[TaskSummary],
        *,
        budget_minutes: int | None = None,
    ) -> ReschedulingResult:
        """Generate rescheduling suggestions for a workload.

        Analyzes the given tasks and produces deterministic rescheduling
        suggestions with feasible candidate slots. Tasks that cannot be
        scheduled are preserved in unscheduled_tasks.

        Args:
            tasks: Sequence of TaskSummary to analyze.
            budget_minutes: Optional budget override; defaults to instance budget.

        Returns:
            ReschedulingResult containing:
                - suggestions: Valid rescheduling candidates with deterministic reasons
                - unscheduled_tasks: Tasks that cannot receive candidates
                - overloaded_days: Dates exceeding daily capacity

        Note:
            This is an analysis/suggestion service only. It does NOT:
            - Modify task data
            - Access the database
            - Call LLMs
            - Integrate with calendars
            - Actually reschedule tasks
        """
        budget = budget_minutes if budget_minutes is not None else self._default_budget

        # Delegate to PDMA-110 candidate generation
        result = self._candidate_service.generate_candidates(
            tasks, budget_minutes=budget
        )

        # PDMA-111 currently passes through PDMA-110 results unchanged.
        # Future extensions can add business logic here:
        # - Pre-filtering based on business rules
        # - Post-processing suggestions
        # - Additional metadata enrichment
        # - Policy-based candidate selection
        return result
