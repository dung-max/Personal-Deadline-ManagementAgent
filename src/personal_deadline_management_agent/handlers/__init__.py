"""HTTP handlers (application boundary)."""

from .health import router as health_router
from .reminder_handler import router as reminder_router
from .task_handler import router as task_router

__all__ = ["health_router", "reminder_router", "task_router"]
