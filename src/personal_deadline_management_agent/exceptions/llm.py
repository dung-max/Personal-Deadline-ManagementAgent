"""LLM-related application exceptions."""


class LLMGenerationError(Exception):
    """Raised when the LLM provider fails to produce structured output."""

    def __init__(self, message: str = "LLM generation failed") -> None:
        self.message = message
        super().__init__(self.message)
