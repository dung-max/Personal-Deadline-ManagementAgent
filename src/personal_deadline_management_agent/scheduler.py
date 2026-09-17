"""Scheduler worker — standalone process entrypoint.

Owns engine/session lifecycle (one engine, one session factory, one fresh
Session/UoW per tick).  Transaction ownership stays with ``SchedulerModule``.

Architecture::

    Scheduler Worker (this module — lifecycle/composition only)
          ↓
    SchedulerModule  (commit/rollback)
          ↓
    SchedulerService (tick logic)
    
          ↓
    Repository / NotificationProvider
          ↓
    Database / Adapter
"""

from __future__ import annotations

import logging
import signal
import time
from datetime import datetime, timezone
from types import FrameType
from typing import Callable

from sqlalchemy.orm import Session

from .adapters.notification import ConsoleNotificationProvider, NotificationProvider
from .config import Settings, load_config
from .db import create_engine_from_url, create_session_factory
from .modules.scheduler_module import SchedulerModule
from .services.scheduler_service import TickResult
from .uow import UnitOfWork

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# run_once — single tick, testable without loop/sleep
# ---------------------------------------------------------------------------


def run_once(
    *,
    session_factory: Callable[[], Session],
    notification_provider: NotificationProvider,
    now: datetime,
    limit: int,
) -> TickResult:
    """Execute exactly one scheduler tick.

    Creates a fresh Session/UoW for this tick, delegates to
    ``SchedulerModule.tick()``, and closes the session afterwards (including
    on exception).  Does NOT commit or rollback itself — that is owned by
    the Module.

    Args:
        session_factory: Callable returning a new SQLAlchemy Session.
        notification_provider: Injected notification adapter.
        now: UTC-aware tick timestamp.
        limit: Maximum reminders to process.

    Returns:
        TickResult from the module.
    """
    session = session_factory()
    try:
        uow = UnitOfWork(session)
        module = SchedulerModule(uow, notification_provider)
        result = module.tick(now=now, limit=limit)
        return result
    finally:
        try:
            session.close()
        except Exception:
            logger.warning("Failed to close scheduler session", exc_info=True)


# ---------------------------------------------------------------------------
# run_forever — long-running loop with signal handling
# ---------------------------------------------------------------------------


def run_forever(
    *,
    settings: Settings,
    session_factory: Callable[[], Session],
    notification_provider: NotificationProvider,
) -> None:
    """Run the scheduler loop until SIGINT/SIGTERM.

    - First tick executes immediately.
    - Sleeps ``settings.scheduler_tick_seconds`` between ticks.
    - Uses UTC-aware ``datetime.now(timezone.utc)`` per tick.
    - Uses ``settings.scheduler_batch_size`` as the per-tick limit.
    - Logs TickResult after each successful tick.
    - Logs infrastructure exceptions with traceback and continues.
    - Handles SIGINT/SIGTERM gracefully: finishes current tick, stops
      scheduling new ticks, returns.

    The engine/session_factory are reused across ticks; a fresh Session/UoW
    is created per tick via ``run_once``.
    """
    shutdown_requested = False

    def _handle_signal(signum: int, _frame: FrameType | None) -> None:
        nonlocal shutdown_requested
        logger.info("Scheduler received signal %s — shutting down", signum)
        shutdown_requested = True

    # Register signal handlers (best-effort — not available on all platforms
    # or when running outside the main thread, e.g. in tests).
    try:
        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)
    except (ValueError, OSError) as exc:
        logger.debug("Could not register signal handlers: %s", exc)

    logger.info(
        "Scheduler started (tick_seconds=%s, batch_size=%s)",
        settings.scheduler_tick_seconds,
        settings.scheduler_batch_size,
    )

    while not shutdown_requested:
        try:
            now = datetime.now(timezone.utc)
            result = run_once(
                session_factory=session_factory,
                notification_provider=notification_provider,
                now=now,
                limit=settings.scheduler_batch_size,
            )
            logger.info(
                "Scheduler tick: due=%s sent=%s failed=%s skipped=%s",
                result.due_count,
                result.sent_count,
                result.failed_count,
                result.skipped_count,
            )
        except Exception:
            logger.exception("Scheduler tick failed (infrastructure error)")

        if shutdown_requested:
            break

        # Interruptible sleep — check shutdown flag in small slices so
        # SIGTERM during sleep is responsive without busy-waiting.
        # Use a simple loop with short sleeps; total sleep equals tick interval.
        remaining = float(settings.scheduler_tick_seconds)
        # Sleep in 0.5s slices to remain responsive to signals.
        slice_seconds = 0.5
        while remaining > 0 and not shutdown_requested:
            chunk = slice_seconds if remaining > slice_seconds else remaining
            time.sleep(chunk)
            remaining -= chunk

    logger.info("Scheduler stopping")


# ---------------------------------------------------------------------------
# Entrypoint — ``python -m personal_deadline_management_agent.scheduler``
# ---------------------------------------------------------------------------


def main() -> None:
    """Load config, create engine/factory/provider, run forever, dispose."""
    settings = load_config()
    engine = create_engine_from_url(settings.database_url)
    session_factory = create_session_factory(engine)
    provider = ConsoleNotificationProvider()
    try:
        run_forever(
            settings=settings,
            session_factory=session_factory,
            notification_provider=provider,
        )
    finally:
        try:
            engine.dispose()
        except Exception:
            logger.warning("Failed to dispose scheduler engine", exc_info=True)


if __name__ == "__main__":
    main()
