"""Agent response generation service.

Converts structured execution results (especially ``WorkloadAnalysisResult``)
into natural-language messages using LLM-based generation with deterministic
template fallback.  The generator does NOT recalculate workload facts — it
only explains the structured analysis result already computed by
``WorkloadAnalysisService``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ..adapters.structured_generation import StructuredGenerationPort
from ..exceptions.llm import LLMGenerationError
from ..schemas.agent import ActionType
from ..schemas.agent_response_generation import AgentResponseOutput
from .execution_result import ExecutionResult

logger = logging.getLogger(__name__)


class AgentResponseGenerator:
    """Generates natural-language responses from structured execution results.

    Depends on ``StructuredGenerationPort`` for LLM access.  Falls back to
    deterministic templates when LLM generation fails or is disabled.  Only
    generates conversational responses for ``ANALYZE_WORKLOAD``; other actions
    use their existing messages.
    """

    # Regex for Vietnamese diacritical marks
    _VIETNAMESE_CHARS = re.compile(
        r"[àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ]",
        re.IGNORECASE,
    )

    def __init__(
        self,
        llm: StructuredGenerationPort,  # type: ignore[type-arg]
        enable_llm: bool = True,
    ) -> None:
        """Initialize the response generator.

        Args:
            llm: LLM adapter for natural-language generation
            enable_llm: If False, always use deterministic templates
        """
        self._llm = llm
        self._enable_llm = enable_llm

    def generate_response(
        self,
        *,
        execution_result: ExecutionResult,
        user_message: str,
        language_hint: str | None = None,
    ) -> str:
        """Generate natural-language response for an execution result.

        Args:
            execution_result: Structured result from ActionExecutor
            user_message: Original user message (for language detection/context)
            language_hint: Optional explicit language ("en" or "vi")

        Returns:
            Natural-language response string. Falls back to deterministic
            template if LLM generation fails or is disabled.
        """
        # Only generate for ANALYZE_WORKLOAD and SUGGEST_RESCHEDULING
        if execution_result.action_type not in {
            ActionType.ANALYZE_WORKLOAD,
            ActionType.SUGGEST_RESCHEDULING,
        }:
            return execution_result.message

        # Skip LLM if disabled or no result_payload
        if not self._enable_llm or not execution_result.result_payload:
            return self._fallback_template(execution_result)

        try:
            language = self._detect_language(user_message, language_hint)
            system_prompt = self._build_system_prompt(
                language, execution_result.result_payload, execution_result.action_type
            )

            output: AgentResponseOutput = self._llm.generate(
                system_prompt=system_prompt,
                user_prompt=user_message,
                output_type=AgentResponseOutput,
            )
            return output.response

        except LLMGenerationError as exc:
            logger.warning(
                "Response generation failed (action already executed), using fallback: %s",
                exc,
                extra={"action_type": execution_result.action_type},
            )
            return self._fallback_template(execution_result)

    def _detect_language(self, user_message: str, hint: str | None) -> str:
        """Detect language from user message.

        Args:
            user_message: Original user message
            hint: Optional explicit language hint

        Returns:
            "en" or "vi"
        """
        if hint:
            return hint.lower()

        # Vietnamese text contains distinctive diacritical marks
        if self._VIETNAMESE_CHARS.search(user_message):
            return "vi"
        return "en"

    def _build_system_prompt(
        self, language: str, result_payload: dict[str, Any], action_type: ActionType
    ) -> str:
        """Build system prompt with structured facts from result_payload.

        Args:
            language: Detected language ("en" or "vi")
            result_payload: WorkloadAnalysisResult or ReschedulingResult as dict
            action_type: The action type being responded to

        Returns:
            System prompt string with instructions and structured facts
        """
        if action_type == ActionType.SUGGEST_RESCHEDULING:
            return self._build_rescheduling_prompt(language, result_payload)
        return self._build_workload_prompt(language, result_payload)

    def _build_workload_prompt(
        self, language: str, result_payload: dict[str, Any]
    ) -> str:
        """Build system prompt for workload analysis response.

        Args:
            language: Detected language ("en" or "vi")
            result_payload: WorkloadAnalysisResult as dict

        Returns:
            System prompt string with instructions and structured facts
        """
        language_name = "English" if language == "en" else "Vietnamese"

        # Extract key facts from workload result (camelCase API contract)
        total_tasks = result_payload.get("totalTasks", 0)
        collision_count = len(result_payload.get("deadlineCollisions", []))
        busy_day_count = len(result_payload.get("busyDays", []))
        has_recommended_order = len(result_payload.get("recommendedOrder", [])) > 0
        scheduling_pressure = result_payload.get("schedulingPressure", [])

        # Build structured facts section
        facts = [
            f"- Total active tasks: {total_tasks}",
        ]

        if collision_count > 0:
            facts.append(f"- Deadline collisions detected: {collision_count}")
            collisions = result_payload.get("deadlineCollisions", [])
            for collision in collisions[:3]:  # Show up to 3
                task_names = [t.get("taskName", "") for t in collision.get("tasks", [])]
                facts.append(f"  - Same deadline: {', '.join(task_names)}")

        if busy_day_count > 0:
            facts.append(f"- Busy days detected: {busy_day_count}")
            busy_days = result_payload.get("busyDays", [])
            for day in busy_days[:3]:  # Show up to 3
                date_str = day.get("date", "")
                task_count = day.get("taskCount", 0)
                facts.append(f"  - {date_str}: {task_count} tasks")

        if has_recommended_order:
            recommended = result_payload.get("recommendedOrder", [])
            facts.append(f"- Recommended order available: {len(recommended)} tasks prioritized")

        if scheduling_pressure:
            facts.append(f"- Scheduling pressure detected: {len(scheduling_pressure)} overlapping feasibility window pair(s)")
            for sp in scheduling_pressure[:3]:  # Show up to 3
                a = sp.get("taskAName", "")
                b = sp.get("taskBName", "")
                overlap_start = sp.get("overlapStart", "")
                overlap_end = sp.get("overlapEnd", "")
                facts.append(f"  - {a} and {b}: overlap {overlap_start} — {overlap_end}")

        facts_text = "\n".join(facts)

        return f"""You are a response generator for a personal task management assistant.

Respond in {language_name}.

Your task is to explain a workload analysis result to the user in a concise, helpful, conversational tone.

IMPORTANT CONSTRAINTS:
- Use ONLY the facts provided below. Never invent tasks, deadlines, or counts.
- Never recalculate metrics. Use the exact numbers given.
- Never execute actions or make changes.
- Never mention internal implementation details.
- Keep your response under 500 characters.
- Be conversational and helpful.
- Focus on what matters most to the user.

STRUCTURED FACTS:
{facts_text}

Generate a natural-language response that explains these facts clearly and helpfully."""

    def _build_rescheduling_prompt(
        self, language: str, result_payload: dict[str, Any]
    ) -> str:
        """Build system prompt for rescheduling suggestions response.

        Args:
            language: Detected language ("en" or "vi")
            result_payload: ReschedulingResult as dict

        Returns:
            System prompt string with instructions and structured facts
        """
        language_name = "English" if language == "en" else "Vietnamese"

        # Extract rescheduling facts
        suggestions = result_payload.get("suggestions", [])
        unscheduled = result_payload.get("unscheduledTasks", [])
        overloaded_days = result_payload.get("overloadedDays", [])

        # Build structured facts with complete details
        facts = [
            f"- Total suggestions generated: {len(suggestions)}",
            f"- Tasks that could not be scheduled: {len(unscheduled)}",
        ]

        if overloaded_days:
            facts.append(f"- Days with capacity constraints: {len(overloaded_days)}")

        # Show detailed suggestion facts
        if suggestions:
            facts.append("\nFEASIBLE CANDIDATE WINDOWS:")
            for i, sug in enumerate(suggestions[:5], 1):
                task_name = sug.get("taskName", "")
                task_id = sug.get("taskId", "")
                duration = sug.get("durationMinutes", 0)
                deadline = sug.get("currentDeadline", "")
                slot = sug.get("candidateSlot", {})
                slot_start = slot.get("start", "")
                slot_end = slot.get("end", "")
                reason = sug.get("reason", "")

                facts.append(
                    f"{i}. {task_name} ({duration} minutes, deadline: {deadline})\n"
                    f"   Candidate: {slot_start} to {slot_end}\n"
                    f"   Reason: {reason}"
                )

            if len(suggestions) > 5:
                facts.append(f"   ... and {len(suggestions) - 5} more suggestions")

        # Show unscheduled task details
        if unscheduled:
            facts.append("\nCOULD NOT SCHEDULE:")
            for i, task in enumerate(unscheduled[:3], 1):
                name = task.get("taskName", "")
                duration = task.get("durationMinutes", "unknown")
                deadline = task.get("deadline", "no deadline")
                facts.append(f"{i}. {name} ({duration} min, {deadline})")

            if len(unscheduled) > 3:
                facts.append(f"   ... and {len(unscheduled) - 3} more")

        facts_text = "\n".join(facts)

        return f"""You are a response generator for a personal deadline management assistant.

Respond in {language_name}.

Explain the rescheduling suggestions in a conversational, helpful tone.

CRITICAL CONSTRAINTS - DO NOT VIOLATE:
- Use ONLY the facts provided below. Never invent times, dates, tasks, or availability.
- Do not recalculate anything. Use exact counts and times from the structured facts.
- Do not claim tasks were rescheduled or moved — these are SUGGESTIONS only.
- Do not claim calendar availability — these are CANDIDATE WINDOWS based on constraints.
- Do not recommend actions beyond what the structured facts show.
- Do not mention system internals, algorithms, or implementation details.

TERMINOLOGY:
- Use "candidate window" or "feasible slot" NOT "free time" or "available time"
- Use "suggestion" NOT "scheduled" or "confirmed"
- Use "could not find a feasible candidate" NOT "impossible" or "blocked"

TONE:
- Keep response under 500 characters if possible
- Be conversational and helpful
- Prioritize most urgent or important information
- If many suggestions, summarize rather than listing all

STRUCTURED FACTS:
{facts_text}

Generate a natural-language response explaining these rescheduling suggestions clearly and accurately."""

    def _fallback_template(self, execution_result: ExecutionResult) -> str:
        """Deterministic template when LLM unavailable.

        Args:
            execution_result: Execution result with result_payload

        Returns:
            Deterministic message string
        """
        if execution_result.action_type == ActionType.SUGGEST_RESCHEDULING:
            return self._rescheduling_fallback(execution_result)

        if execution_result.result_payload:
            # Use WorkloadAnalysisService.explanation field
            explanation = execution_result.result_payload.get("explanation", "")
            if explanation:
                return explanation

        # Final fallback
        return execution_result.message

    def _rescheduling_fallback(self, execution_result: ExecutionResult) -> str:
        """Deterministic fallback for SUGGEST_RESCHEDULING.

        Produces a structured summary using only deterministic facts.
        Never invents scheduling facts.
        """
        payload = execution_result.result_payload or {}
        suggestions = payload.get("suggestions", [])
        unscheduled = payload.get("unscheduledTasks", [])
        overloaded_days = payload.get("overloadedDays", [])

        lines: list[str] = []

        if not suggestions and not unscheduled:
            lines.append("No rescheduling analysis needed for the selected range.")

        elif not suggestions:
            lines.append(
                "No feasible candidate windows found for "
                f"{len(unscheduled)} task(s) under current constraints."
            )
        else:
            lines.append(
                f"Found {len(suggestions)} feasible rescheduling suggestion"
                f"{'s' if len(suggestions) != 1 else ''}."
            )
            for sug in suggestions[:5]:
                name = sug.get("taskName", "Task")
                slot = sug.get("candidateSlot", {})
                slot_start = slot.get("start", "")
                slot_end = slot.get("end", "")
                if slot_start and slot_end:
                    lines.append(f"- {name}: candidate window {slot_start} to {slot_end}")

        if unscheduled:
            lines.append(
                f"{len(unscheduled)} task(s) could not be assigned a feasible "
                "candidate under current constraints."
            )

        if overloaded_days:
            day_count = len(overloaded_days)
            lines.append(
                f"{day_count} day(s) have planned work exceeding the daily budget."
            )

        lines.append(
            "\nNote: These are suggestions based on known constraints. "
            "Candidate windows are not confirmed calendar availability."
        )

        return "\n".join(lines)
