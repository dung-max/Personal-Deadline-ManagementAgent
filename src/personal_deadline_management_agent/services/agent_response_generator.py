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
        # Only generate for ANALYZE_WORKLOAD
        if execution_result.action_type != ActionType.ANALYZE_WORKLOAD:
            return execution_result.message

        # Skip LLM if disabled or no result_payload
        if not self._enable_llm or not execution_result.result_payload:
            return self._fallback_template(execution_result)

        try:
            language = self._detect_language(user_message, language_hint)
            system_prompt = self._build_system_prompt(
                language, execution_result.result_payload
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

    def _build_system_prompt(self, language: str, result_payload: dict[str, Any]) -> str:
        """Build system prompt with structured facts from result_payload.

        Args:
            language: Detected language ("en" or "vi")
            result_payload: WorkloadAnalysisResult as dict

        Returns:
            System prompt string with instructions and structured facts
        """
        language_name = "English" if language == "en" else "Vietnamese"

        # Extract key facts from workload result
        total_tasks = result_payload.get("total_tasks", 0)
        collision_count = len(result_payload.get("deadline_collisions", []))
        busy_day_count = len(result_payload.get("busy_days", []))
        has_recommended_order = len(result_payload.get("recommended_order", [])) > 0

        # Build structured facts section
        facts = [
            f"- Total active tasks: {total_tasks}",
        ]

        if collision_count > 0:
            facts.append(f"- Deadline collisions detected: {collision_count}")
            collisions = result_payload.get("deadline_collisions", [])
            for collision in collisions[:3]:  # Show up to 3
                task_names = [t.get("task_name", "") for t in collision.get("tasks", [])]
                facts.append(f"  - Same deadline: {', '.join(task_names)}")

        if busy_day_count > 0:
            facts.append(f"- Busy days detected: {busy_day_count}")
            busy_days = result_payload.get("busy_days", [])
            for day in busy_days[:3]:  # Show up to 3
                date_str = day.get("date", "")
                task_count = day.get("task_count", 0)
                facts.append(f"  - {date_str}: {task_count} tasks")

        if has_recommended_order:
            recommended = result_payload.get("recommended_order", [])
            facts.append(f"- Recommended order available: {len(recommended)} tasks prioritized")

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

    def _fallback_template(self, execution_result: ExecutionResult) -> str:
        """Deterministic template when LLM unavailable.

        Args:
            execution_result: Execution result with result_payload

        Returns:
            Deterministic message string
        """
        if execution_result.result_payload:
            # Use WorkloadAnalysisService.explanation field
            explanation = execution_result.result_payload.get("explanation", "")
            if explanation:
                return explanation

        # Final fallback
        return execution_result.message
