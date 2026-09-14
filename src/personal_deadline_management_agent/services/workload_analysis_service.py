"""Workload analysis service.

Stateless, deterministic service that analyzes a list of tasks and produces:
- total active task count
- deadline collisions (2+ active HIGH-priority tasks with exact same deadline)
- busy-day warnings (>5 active tasks on same UTC calendar date)
- recommended task order (deadline ASC, priority DESC, task_name ASC)
- deterministic explanation

This service does NOT:
- query the database
- call an LLM
- mutate tasks
- reschedule deadlines
- estimate task duration
- detect calendar conflicts
- perform autonomous planning
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Sequence

from ..models import Task, TaskPriority, TaskStatus
from ..schemas.workload import (
    BusyDayWarning,
    DeadlineCollision,
    TaskSummary,
    WorkloadAnalysisResult,
)


class WorkloadAnalysisService:
    """Analyzes task workload and produces structured insights."""

    def analyze(
        self,
        tasks: Sequence[Task | TaskSummary],
    ) -> WorkloadAnalysisResult:
        """Analyze a list of tasks and return workload insights.

        Args:
            tasks: Sequence of Task or TaskSummary objects to analyze.

        Returns:
            WorkloadAnalysisResult with analysis findings.
        """
        # Filter to active tasks only
        active_tasks = self._filter_active_tasks(tasks)

        # Convert to TaskSummary for consistent processing
        summaries = [self._to_summary(t) for t in active_tasks]

        # Perform analysis
        total_tasks = len(summaries)
        deadline_collisions = self._detect_deadline_collisions(summaries)
        busy_days = self._detect_busy_days(summaries)
        recommended_order = self._recommend_order(summaries)
        explanation = self._generate_explanation(
            total_tasks, deadline_collisions, busy_days, recommended_order
        )

        return WorkloadAnalysisResult(
            total_tasks=total_tasks,
            deadline_collisions=deadline_collisions,
            busy_days=busy_days,
            recommended_order=recommended_order,
            explanation=explanation,
        )

    def _filter_active_tasks(
        self, tasks: Sequence[Task | TaskSummary]
    ) -> list[Task | TaskSummary]:
        """Filter to active tasks only (TODO, IN_PROGRESS)."""
        active_statuses = {TaskStatus.TODO.value, TaskStatus.IN_PROGRESS.value}
        result = []

        for task in tasks:
            if isinstance(task, TaskSummary):
                status_val = task.status.value if isinstance(task.status, TaskStatus) else task.status
            else:
                status_val = task.status

            if status_val in active_statuses:
                result.append(task)

        return result

    def _to_summary(self, task: Task | TaskSummary) -> TaskSummary:
        """Convert Task or TaskSummary to TaskSummary."""
        if isinstance(task, TaskSummary):
            return task

        # Convert Task to TaskSummary
        priority_enum = TaskPriority(task.priority) if isinstance(task.priority, str) else task.priority
        status_enum = TaskStatus(task.status) if isinstance(task.status, str) else task.status

        return TaskSummary(
            id=task.id,
            task_name=task.task_name,
            priority=priority_enum,
            status=status_enum,
            deadline=task.deadline,
        )

    def _detect_deadline_collisions(
        self, summaries: list[TaskSummary]
    ) -> list[DeadlineCollision]:
        """Detect deadline collisions: 2+ active HIGH-priority tasks with exact same deadline."""
        # Group HIGH-priority tasks by deadline
        high_tasks_by_deadline: dict[datetime, list[TaskSummary]] = defaultdict(list)

        for summary in summaries:
            # Only HIGH priority
            priority_val = summary.priority.value if isinstance(summary.priority, TaskPriority) else summary.priority
            if priority_val != TaskPriority.HIGH.value:
                continue

            # Must have a deadline
            if summary.deadline is None:
                continue

            high_tasks_by_deadline[summary.deadline].append(summary)

        # Find groups with 2+ tasks
        collisions = []
        for deadline, tasks in high_tasks_by_deadline.items():
            if len(tasks) >= 2:
                # Sort tasks deterministically
                sorted_tasks = self._sort_tasks(tasks)
                collisions.append(DeadlineCollision(deadline=deadline, tasks=sorted_tasks))

        # Sort collision groups by deadline ASC
        collisions.sort(key=lambda c: c.deadline)

        return collisions

    def _detect_busy_days(self, summaries: list[TaskSummary]) -> list[BusyDayWarning]:
        """Detect busy days: >5 active tasks on same UTC calendar date."""
        # Group tasks by UTC date
        tasks_by_date: dict[date, list[TaskSummary]] = defaultdict(list)

        for summary in summaries:
            if summary.deadline is None:
                continue

            utc_date = summary.deadline.date()
            tasks_by_date[utc_date].append(summary)

        # Find days with >5 tasks
        warnings = []
        for day, tasks in tasks_by_date.items():
            if len(tasks) > 5:
                # Sort tasks deterministically
                sorted_tasks = self._sort_tasks(tasks)
                warnings.append(
                    BusyDayWarning(
                        date=day,
                        task_count=len(tasks),
                        tasks=sorted_tasks,
                    )
                )

        # Sort warnings by date ASC
        warnings.sort(key=lambda w: w.date)

        return warnings

    def _recommend_order(self, summaries: list[TaskSummary]) -> list[TaskSummary]:
        """Return recommended task order (deterministic sort)."""
        # Don't mutate the original list
        return self._sort_tasks(summaries)

    def _sort_tasks(self, tasks: list[TaskSummary]) -> list[TaskSummary]:
        """Sort tasks deterministically: deadline ASC, priority DESC, task_name ASC.

        Tasks with no deadline are placed after tasks with deadlines.
        """
        def sort_key(task: TaskSummary) -> tuple:
            # Deadline: None sorts last
            deadline_key = (task.deadline is None, task.deadline)

            # Priority: HIGH < MEDIUM < LOW (for descending sort)
            priority_val = task.priority.value if isinstance(task.priority, TaskPriority) else task.priority
            priority_order = {
                TaskPriority.HIGH.value: 0,
                TaskPriority.MEDIUM.value: 1,
                TaskPriority.LOW.value: 2,
            }
            priority_key = priority_order.get(priority_val, 999)

            # Task name: alphabetical
            name_key = task.task_name

            return (deadline_key, priority_key, name_key)

        return sorted(tasks, key=sort_key)

    def _generate_explanation(
        self,
        total_tasks: int,
        collisions: list[DeadlineCollision],
        busy_days: list[BusyDayWarning],
        recommended_order: list[TaskSummary],
    ) -> str:
        """Generate deterministic explanation of the workload analysis."""
        if total_tasks == 0:
            return "You have no active tasks in the selected date range."

        lines = [f"You have {total_tasks} active task{'s' if total_tasks != 1 else ''}."]

        if collisions:
            lines.append("")
            lines.append("Deadline Collisions:")
            for collision in collisions:
                task_names = ", ".join(t.task_name for t in collision.tasks)
                deadline_str = collision.deadline.strftime("%Y-%m-%d %H:%M UTC")
                lines.append(f"- {deadline_str}: {task_names}")

        if busy_days:
            lines.append("")
            lines.append("Busy Days:")
            for warning in busy_days:
                date_str = warning.date.strftime("%Y-%m-%d")
                lines.append(f"- {date_str}: {warning.task_count} tasks")

        if recommended_order:
            lines.append("")
            lines.append("Recommended Order:")
            for i, task in enumerate(recommended_order[:10], 1):  # Show top 10
                priority_val = task.priority.value if isinstance(task.priority, TaskPriority) else task.priority
                if task.deadline:
                    deadline_str = task.deadline.strftime("%Y-%m-%d %H:%M UTC")
                    lines.append(f"{i}. {task.task_name} — {priority_val} — {deadline_str}")
                else:
                    lines.append(f"{i}. {task.task_name} — {priority_val} — no deadline")

            if len(recommended_order) > 10:
                lines.append(f"... and {len(recommended_order) - 10} more tasks")

        lines.append("")
        lines.append(
            "Note: This analysis is based on task count and deadline data only. "
            "It does not include task duration, dependencies, calendar availability, "
            "or workload capacity."
        )

        return "\n".join(lines)
