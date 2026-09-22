"""Workload analysis service.

Stateless, deterministic service that analyzes a list of tasks and produces:
- total active task count
- deadline collisions (2+ active HIGH-priority tasks with exact same deadline)
- busy-day warnings (>5 active tasks on same UTC calendar date)
- recommended task order (deadline ASC, priority DESC, task_name ASC)
- deterministic explanation
- feasibility windows (latest start times for tasks with known duration)
- total planned duration
- daily pressure (utilization by date)
- overload warnings (days exceeding budget)
- scheduling pressure (overlapping feasibility windows)

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
from datetime import date, datetime, timedelta
from typing import Sequence

from ..models import Task, TaskPriority, TaskStatus
from ..schemas.workload import (
    BusyDayWarning,
    DailyPressure,
    DeadlineCollision,
    FeasibilityWindow,
    OverloadWarning,
    SchedulingPressure,
    TaskSummary,
    WorkloadAnalysisResult,
)


# Default working hours budget (8 hours). Will be injected via DI in PDMA-106.
_DEFAULT_BUDGET_MINUTES = 480


class WorkloadAnalysisService:
    """Analyzes task workload and produces structured insights."""

    def __init__(self, budget_minutes: int = _DEFAULT_BUDGET_MINUTES) -> None:
        """Initialize workload analysis service.

        Args:
            budget_minutes: Default working hours budget per day in minutes (default: 480).
        """
        if not (0 < budget_minutes <= 1440):
            raise ValueError(
                f"budget_minutes must be between 1 and 1440 (minutes in a day), got {budget_minutes}"
            )
        self._default_budget_minutes = budget_minutes

    def analyze(
        self,
        tasks: Sequence[Task | TaskSummary],
        budget_minutes: int | None = None,
    ) -> WorkloadAnalysisResult:
        """Analyze a list of tasks and return workload insights.

        Args:
            tasks: Sequence of Task or TaskSummary objects to analyze.
            budget_minutes: Working hours budget per day in minutes. If None, uses the instance default.

        Returns:
            WorkloadAnalysisResult with analysis findings.
        """
        # Use explicit override or instance default
        effective_budget = budget_minutes if budget_minutes is not None else self._default_budget_minutes
        # Filter to active tasks only
        active_tasks = self._filter_active_tasks(tasks)

        # Convert to TaskSummary for consistent processing
        summaries = [self._to_summary(t) for t in active_tasks]

        # Phase 7 analysis (preserved)
        total_tasks = len(summaries)
        deadline_collisions = self._detect_deadline_collisions(summaries)
        busy_days = self._detect_busy_days(summaries)
        recommended_order = self._recommend_order(summaries)

        # Phase 10 analysis (new)
        feasibility_windows = self._calculate_feasibility_windows(summaries)
        total_planned_minutes = self._calculate_total_planned_minutes(summaries)
        daily_pressure = self._calculate_daily_pressure(summaries, effective_budget)
        overload_warnings = self._calculate_overload_warnings(daily_pressure)
        scheduling_pressure_pairs = self._detect_scheduling_pressure(feasibility_windows)
        scheduling_pressure = self._convert_scheduling_pressure(scheduling_pressure_pairs)

        # Generate explanation including new insights
        explanation = self._generate_explanation(
            total_tasks,
            deadline_collisions,
            busy_days,
            recommended_order,
            total_planned_minutes,
            overload_warnings,
            scheduling_pressure_pairs,
        )

        return WorkloadAnalysisResult(
            total_tasks=total_tasks,
            deadline_collisions=deadline_collisions,
            busy_days=busy_days,
            recommended_order=recommended_order,
            explanation=explanation,
            total_planned_minutes=total_planned_minutes,
            feasibility_windows=feasibility_windows,
            daily_pressure=daily_pressure,
            overload_warnings=overload_warnings,
            scheduling_pressure=scheduling_pressure,
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
            duration_minutes=task.duration_minutes,
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
        total_planned_minutes: int = 0,
        overload_warnings: list[OverloadWarning] | None = None,
        scheduling_pressure: list[tuple[FeasibilityWindow, FeasibilityWindow]] | None = None,
    ) -> str:
        """Generate deterministic explanation of the workload analysis."""
        overload_warnings = overload_warnings or []
        scheduling_pressure = scheduling_pressure or []

        if total_tasks == 0:
            return "You have no active tasks in the selected date range."

        lines = [f"You have {total_tasks} active task{'s' if total_tasks != 1 else ''}."]

        if total_planned_minutes > 0:
            hours = total_planned_minutes / 60
            lines.append(f"Total planned duration: {total_planned_minutes} minutes ({hours:.1f} hours).")

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

        if overload_warnings:
            lines.append("")
            lines.append("Overload Warnings:")
            for warning in overload_warnings:
                date_str = warning.date.strftime("%Y-%m-%d")
                lines.append(
                    f"- {date_str}: {warning.planned_minutes} min planned "
                    f"vs {warning.budget_minutes} min budget "
                    f"(overload by {warning.excess_minutes} min)"
                )

        if scheduling_pressure:
            lines.append("")
            lines.append("Scheduling Pressure:")
            for window_a, window_b in scheduling_pressure:
                lines.append(
                    f"- {window_a.task_name} and {window_b.task_name}: "
                    f"feasibility-window overlap indicates scheduling pressure"
                )

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

        # Include duration-based note when applicable
        has_time_pressure = total_planned_minutes > 0 or overload_warnings or scheduling_pressure
        lines.append("")
        if has_time_pressure:
            lines.append(
                "Note: Duration-based metrics show workload pressure but do not represent "
                "an actual schedule. This analysis does not include dependencies, "
                "calendar availability, or task placement."
            )
        else:
            lines.append(
                "Note: This analysis is based on task count and deadline data only. "
                "It does not include task duration, dependencies, calendar availability, "
                "or workload capacity."
            )

        return "\n".join(lines)

    def _calculate_feasibility_windows(
        self, summaries: list[TaskSummary]
    ) -> list[FeasibilityWindow]:
        """Calculate feasibility windows for tasks with known duration and deadline.

        A feasibility window shows the latest possible start time for a task
        given its deadline and duration.

        Args:
            summaries: List of TaskSummary objects.

        Returns:
            List of FeasibilityWindow sorted by deadline ASC, task_name ASC.
        """
        windows = []

        for summary in summaries:
            # Skip tasks without deadline or duration
            if summary.deadline is None or summary.duration_minutes is None:
                continue

            # Calculate latest start
            latest_start = summary.deadline - timedelta(minutes=summary.duration_minutes)

            windows.append(
                FeasibilityWindow(
                    task_id=summary.id,
                    task_name=summary.task_name,
                    deadline=summary.deadline,
                    duration_minutes=summary.duration_minutes,
                    latest_start=latest_start,
                )
            )

        # Sort deterministically: deadline ASC, task_name ASC
        windows.sort(key=lambda w: (w.deadline, w.task_name))

        return windows

    def _calculate_total_planned_minutes(self, summaries: list[TaskSummary]) -> int:
        """Calculate total planned duration from tasks with known duration.

        Args:
            summaries: List of TaskSummary objects.

        Returns:
            Total duration in minutes (0 if no tasks have known duration).
        """
        total = 0
        for summary in summaries:
            if summary.duration_minutes is not None:
                total += summary.duration_minutes
        return total

    def _calculate_daily_pressure(
        self, summaries: list[TaskSummary], budget_minutes: int
    ) -> list[DailyPressure]:
        """Calculate daily workload pressure grouped by UTC deadline date.

        Args:
            summaries: List of TaskSummary objects.
            budget_minutes: Working hours budget per day in minutes.

        Returns:
            List of DailyPressure sorted by date ASC.
        """
        # Group tasks with known duration by UTC deadline date
        tasks_by_date: dict[date, list[TaskSummary]] = defaultdict(list)

        for summary in summaries:
            # Skip tasks without deadline or duration
            if summary.deadline is None or summary.duration_minutes is None:
                continue

            utc_date = summary.deadline.date()
            tasks_by_date[utc_date].append(summary)

        # Calculate pressure for each date
        pressures = []
        for day, tasks in tasks_by_date.items():
            # Sum planned duration
            planned_minutes = sum(t.duration_minutes for t in tasks if t.duration_minutes is not None)

            # Calculate utilization percentage
            utilization_pct = (planned_minutes / budget_minutes * 100) if budget_minutes > 0 else 0

            # Sort tasks deterministically
            sorted_tasks = self._sort_tasks(tasks)

            pressures.append(
                DailyPressure(
                    date=day,
                    planned_minutes=planned_minutes,
                    budget_minutes=budget_minutes,
                    utilization_pct=utilization_pct,
                    tasks=sorted_tasks,
                )
            )

        # Sort by date ASC
        pressures.sort(key=lambda p: p.date)

        return pressures

    def _calculate_overload_warnings(
        self, daily_pressure: list[DailyPressure]
    ) -> list[OverloadWarning]:
        """Calculate overload warnings from daily pressure analysis.

        Args:
            daily_pressure: List of DailyPressure objects.

        Returns:
            List of OverloadWarning sorted by date ASC.
        """
        warnings = []

        for pressure in daily_pressure:
            if pressure.planned_minutes > pressure.budget_minutes:
                excess_minutes = pressure.planned_minutes - pressure.budget_minutes

                warnings.append(
                    OverloadWarning(
                        date=pressure.date,
                        planned_minutes=pressure.planned_minutes,
                        budget_minutes=pressure.budget_minutes,
                        excess_minutes=excess_minutes,
                        tasks=pressure.tasks,
                    )
                )

        # Already sorted by date since input is sorted
        return warnings

    def _detect_scheduling_pressure(
        self, feasibility_windows: list[FeasibilityWindow]
    ) -> list[tuple[FeasibilityWindow, FeasibilityWindow]]:
        """Detect overlapping feasibility windows indicating scheduling pressure.

        Two windows overlap when:
            max(latest_start_A, latest_start_B) < min(deadline_A, deadline_B)

        Args:
            feasibility_windows: List of FeasibilityWindow objects.

        Returns:
            List of overlapping window pairs sorted deterministically.
        """
        overlaps = []

        # Pairwise comparison
        for i in range(len(feasibility_windows)):
            for j in range(i + 1, len(feasibility_windows)):
                window_a = feasibility_windows[i]
                window_b = feasibility_windows[j]

                # Check for overlap
                max_start = max(window_a.latest_start, window_b.latest_start)
                min_deadline = min(window_a.deadline, window_b.deadline)

                if max_start < min_deadline:
                    # Overlap detected - store in deterministic order
                    pair = (window_a, window_b) if window_a.task_name < window_b.task_name else (window_b, window_a)
                    overlaps.append(pair)

        # Sort pairs deterministically by first task name, then second task name
        overlaps.sort(key=lambda pair: (pair[0].task_name, pair[1].task_name))

        return overlaps

    def _convert_scheduling_pressure(
        self, pressure_pairs: list[tuple[FeasibilityWindow, FeasibilityWindow]]
    ) -> list[SchedulingPressure]:
        """Convert internal pressure pairs to structured SchedulingPressure schema.

        Args:
            pressure_pairs: List of overlapping FeasibilityWindow pairs.

        Returns:
            List of SchedulingPressure objects with overlap boundaries.
        """
        result = []
        for window_a, window_b in pressure_pairs:
            overlap_start = max(window_a.latest_start, window_b.latest_start)
            overlap_end = min(window_a.deadline, window_b.deadline)

            result.append(
                SchedulingPressure(
                    task_a_id=window_a.task_id,
                    task_a_name=window_a.task_name,
                    task_b_id=window_b.task_id,
                    task_b_name=window_b.task_name,
                    overlap_start=overlap_start,
                    overlap_end=overlap_end,
                )
            )
        return result
