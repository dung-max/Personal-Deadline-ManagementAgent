"""Tests for scheduler worker (run_once / run_forever) and scheduler config.

Covers:
- run_once: fresh session per invocation, calls SchedulerModule once,
  returns TickResult, closes session on success/exception, does not
  commit/rollback itself, does not reuse Session, passes now/limit.
- Config: defaults, env overrides, validation.
- run_forever: first tick immediate, sleep between ticks, correct interval,
  UTC time per tick, batch size from settings, shutdown stops ticks,
  engine not recreated per tick, engine disposal, infra failure continues.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

import personal_deadline_management_agent.config as config
from personal_deadline_management_agent.services.scheduler_service import TickResult


# ---------------------------------------------------------------------------
# Helpers — fakes for run_once / run_forever
# ---------------------------------------------------------------------------


class FakeSession:
    def __init__(self) -> None:
        self.closed = False
        self.close_count = 0

    def close(self) -> None:
        self.closed = True
        self.close_count += 1


class _StopLoop(Exception):
    """Raised from a mocked sleep to end run_forever deterministically."""


def _tick_result(**overrides) -> TickResult:
    defaults = {
        "sent_count": 0,
        "failed_count": 0,
        "skipped_count": 0,
        "due_count": 0,
    }
    defaults.update(overrides)
    return TickResult(**defaults)


# ---------------------------------------------------------------------------
# run_once tests
# ---------------------------------------------------------------------------


def test_run_once_creates_fresh_session_per_invocation():
    from personal_deadline_management_agent.scheduler import run_once

    sessions: list[FakeSession] = []

    def session_factory():
        s = FakeSession()
        sessions.append(s)
        return s

    provider = MagicMock()
    now = datetime.now(timezone.utc)
    fake_result = _tick_result(sent_count=1, due_count=1)

    with patch(
        "personal_deadline_management_agent.scheduler.SchedulerModule"
    ) as MockModule:
        MockModule.return_value.tick.return_value = fake_result

        run_once(
            session_factory=session_factory,
            notification_provider=provider,
            now=now,
            limit=100,
        )
        run_once(
            session_factory=session_factory,
            notification_provider=provider,
            now=now,
            limit=100,
        )

    assert len(sessions) == 2
    assert sessions[0] is not sessions[1]


def test_run_once_calls_scheduler_module_once():
    from personal_deadline_management_agent.scheduler import run_once

    provider = MagicMock()
    now = datetime.now(timezone.utc)
    fake_result = _tick_result()

    with patch(
        "personal_deadline_management_agent.scheduler.SchedulerModule"
    ) as MockModule:
        MockModule.return_value.tick.return_value = fake_result

        run_once(
            session_factory=FakeSession,
            notification_provider=provider,
            now=now,
            limit=50,
        )

        MockModule.return_value.tick.assert_called_once_with(now=now, limit=50)


def test_run_once_returns_tick_result():
    from personal_deadline_management_agent.scheduler import run_once

    provider = MagicMock()
    now = datetime.now(timezone.utc)
    fake_result = _tick_result(sent_count=2, failed_count=1, due_count=3)

    with patch(
        "personal_deadline_management_agent.scheduler.SchedulerModule"
    ) as MockModule:
        MockModule.return_value.tick.return_value = fake_result

        result = run_once(
            session_factory=FakeSession,
            notification_provider=provider,
            now=now,
            limit=100,
        )

    assert result == fake_result


def test_run_once_closes_session_after_success():
    from personal_deadline_management_agent.scheduler import run_once

    session = FakeSession()
    provider = MagicMock()
    now = datetime.now(timezone.utc)
    fake_result = _tick_result()

    with patch(
        "personal_deadline_management_agent.scheduler.SchedulerModule"
    ) as MockModule:
        MockModule.return_value.tick.return_value = fake_result

        run_once(
            session_factory=lambda: session,
            notification_provider=provider,
            now=now,
            limit=100,
        )

    assert session.closed is True
    assert session.close_count == 1


def test_run_once_closes_session_after_exception():
    from personal_deadline_management_agent.scheduler import run_once

    session = FakeSession()
    provider = MagicMock()
    now = datetime.now(timezone.utc)

    with patch(
        "personal_deadline_management_agent.scheduler.SchedulerModule"
    ) as MockModule:
        MockModule.return_value.tick.side_effect = RuntimeError("DB failure")

        with pytest.raises(RuntimeError, match="DB failure"):
            run_once(
                session_factory=lambda: session,
                notification_provider=provider,
                now=now,
                limit=100,
            )

    assert session.closed is True
    assert session.close_count == 1


def test_run_once_does_not_commit_or_rollback_itself():
    """Worker owns lifecycle only — commit/rollback is Module's job."""
    from personal_deadline_management_agent.scheduler import run_once

    session = FakeSession()
    session.commit = MagicMock()  # type: ignore[attr-defined]
    session.rollback = MagicMock()  # type: ignore[attr-defined]

    provider = MagicMock()
    now = datetime.now(timezone.utc)
    fake_result = _tick_result(sent_count=1, due_count=1)

    with patch(
        "personal_deadline_management_agent.scheduler.SchedulerModule"
    ) as MockModule:
        MockModule.return_value.tick.return_value = fake_result

        run_once(
            session_factory=lambda: session,
            notification_provider=provider,
            now=now,
            limit=100,
        )

    session.commit.assert_not_called()  # type: ignore[union-attr]
    session.rollback.assert_not_called()  # type: ignore[union-attr]


def test_run_once_does_not_reuse_session_between_calls():
    from personal_deadline_management_agent.scheduler import run_once

    created_ids: list[int] = []

    def session_factory():
        s = FakeSession()
        created_ids.append(id(s))
        return s

    provider = MagicMock()
    now = datetime.now(timezone.utc)
    fake_result = _tick_result()

    with patch(
        "personal_deadline_management_agent.scheduler.SchedulerModule"
    ) as MockModule:
        MockModule.return_value.tick.return_value = fake_result

        run_once(
            session_factory=session_factory,
            notification_provider=provider,
            now=now,
            limit=100,
        )
        run_once(
            session_factory=session_factory,
            notification_provider=provider,
            now=now,
            limit=100,
        )

    assert created_ids[0] != created_ids[1]


def test_run_once_passes_now_and_limit():
    from personal_deadline_management_agent.scheduler import run_once

    provider = MagicMock()
    now = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)
    fake_result = _tick_result()

    with patch(
        "personal_deadline_management_agent.scheduler.SchedulerModule"
    ) as MockModule:
        MockModule.return_value.tick.return_value = fake_result

        run_once(
            session_factory=FakeSession,
            notification_provider=provider,
            now=now,
            limit=42,
        )

        MockModule.return_value.tick.assert_called_once_with(now=now, limit=42)


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


def test_scheduler_tick_seconds_default():
    settings = config.Settings(database_url="sqlite:///x")
    assert settings.scheduler_tick_seconds == 60


def test_scheduler_batch_size_default():
    settings = config.Settings(database_url="sqlite:///x")
    assert settings.scheduler_batch_size == 100


def test_scheduler_config_env_override(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///x")
    monkeypatch.setenv("SCHEDULER_TICK_SECONDS", "120")
    monkeypatch.setenv("SCHEDULER_BATCH_SIZE", "50")

    settings = config.load_config()

    assert settings.scheduler_tick_seconds == 120
    assert settings.scheduler_batch_size == 50


def test_scheduler_tick_seconds_below_minimum_rejected():
    with pytest.raises(ValueError, match="scheduler_tick_seconds"):
        config.Settings(database_url="sqlite:///x", scheduler_tick_seconds=4)


def test_scheduler_tick_seconds_at_minimum_allowed():
    settings = config.Settings(database_url="sqlite:///x", scheduler_tick_seconds=5)
    assert settings.scheduler_tick_seconds == 5


def test_scheduler_batch_size_below_minimum_rejected():
    with pytest.raises(ValueError, match="scheduler_batch_size"):
        config.Settings(database_url="sqlite:///x", scheduler_batch_size=0)


def test_scheduler_batch_size_above_maximum_rejected():
    with pytest.raises(ValueError, match="scheduler_batch_size"):
        config.Settings(database_url="sqlite:///x", scheduler_batch_size=1001)


def test_scheduler_batch_size_at_boundaries_allowed():
    s1 = config.Settings(database_url="sqlite:///x", scheduler_batch_size=1)
    assert s1.scheduler_batch_size == 1
    s2 = config.Settings(database_url="sqlite:///x", scheduler_batch_size=1000)
    assert s2.scheduler_batch_size == 1000


def test_scheduler_config_env_validation(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///x")
    monkeypatch.setenv("SCHEDULER_TICK_SECONDS", "2")

    with pytest.raises(ValueError, match="scheduler_tick_seconds"):
        config.load_config()


# ---------------------------------------------------------------------------
# run_forever tests — mocked to avoid real loops/sleeps
# ---------------------------------------------------------------------------


def test_run_forever_first_tick_immediate():
    """First tick executes before any sleep."""
    from personal_deadline_management_agent.scheduler import run_forever

    settings = config.Settings(database_url="sqlite:///x")
    provider = MagicMock()

    order: list[str] = []

    def tracking_run_once(**kwargs):  # type: ignore[no-untyped-def]
        order.append("tick")
        return _tick_result()

    def tracking_sleep(seconds):  # type: ignore[no-untyped-def]
        order.append(f"sleep:{seconds}")
        raise _StopLoop

    with patch(
        "personal_deadline_management_agent.scheduler.run_once",
        side_effect=tracking_run_once,
    ):
        with patch(
            "personal_deadline_management_agent.scheduler.time.sleep",
            side_effect=tracking_sleep,
        ):
            with patch("personal_deadline_management_agent.scheduler.signal.signal"):
                with pytest.raises(_StopLoop):
                    run_forever(
                        settings=settings,
                        session_factory=MagicMock(return_value=FakeSession()),
                        notification_provider=provider,
                    )

    assert order[0] == "tick"


def test_run_forever_sleep_between_ticks():
    from personal_deadline_management_agent.scheduler import run_forever

    settings = config.Settings(database_url="sqlite:///x")
    provider = MagicMock()

    sleep_calls: list[float] = []
    tick_count = 0

    def fake_run_once(**kwargs):  # type: ignore[no-untyped-def]
        nonlocal tick_count
        tick_count += 1
        return _tick_result()

    class StopAfterTwoTicks(Exception):
        pass

    def fake_sleep(seconds):  # type: ignore[no-untyped-def]
        sleep_calls.append(seconds)
        if tick_count >= 2:
            raise StopAfterTwoTicks

    with patch(
        "personal_deadline_management_agent.scheduler.run_once",
        side_effect=fake_run_once,
    ):
        with patch(
            "personal_deadline_management_agent.scheduler.time.sleep",
            side_effect=fake_sleep,
        ):
            with patch("personal_deadline_management_agent.scheduler.signal.signal"):
                with pytest.raises(StopAfterTwoTicks):
                    run_forever(
                        settings=settings,
                        session_factory=MagicMock(return_value=FakeSession()),
                        notification_provider=provider,
                    )

    assert len(sleep_calls) >= 1
    assert tick_count == 2


def test_run_forever_correct_interval():
    from personal_deadline_management_agent.scheduler import run_forever

    settings = config.Settings(
        database_url="sqlite:///x", scheduler_tick_seconds=30
    )
    provider = MagicMock()

    sleep_totals: list[float] = []
    tick_count = 0

    def fake_run_once(**kwargs):  # type: ignore[no-untyped-def]
        nonlocal tick_count
        tick_count += 1
        return _tick_result()

    class StopAfterOneInterval(Exception):
        pass

    def fake_sleep(seconds):  # type: ignore[no-untyped-def]
        sleep_totals.append(seconds)
        if sum(sleep_totals) >= 30:
            raise StopAfterOneInterval

    with patch(
        "personal_deadline_management_agent.scheduler.run_once",
        side_effect=fake_run_once,
    ):
        with patch(
            "personal_deadline_management_agent.scheduler.time.sleep",
            side_effect=fake_sleep,
        ):
            with patch("personal_deadline_management_agent.scheduler.signal.signal"):
                with pytest.raises(StopAfterOneInterval):
                    run_forever(
                        settings=settings,
                        session_factory=MagicMock(return_value=FakeSession()),
                        notification_provider=provider,
                    )

    assert sum(sleep_totals) == pytest.approx(30.0, abs=0.01)


def test_run_forever_utc_time_per_tick():
    from personal_deadline_management_agent.scheduler import run_forever

    settings = config.Settings(database_url="sqlite:///x")
    provider = MagicMock()

    captured_nows: list[datetime] = []
    tick_count = 0

    def fake_run_once(*, session_factory, notification_provider, now, limit):  # type: ignore[no-untyped-def]
        nonlocal tick_count
        tick_count += 1
        captured_nows.append(now)
        return _tick_result()

    class StopAfterTwo(Exception):
        pass

    def fake_sleep(seconds):  # type: ignore[no-untyped-def]
        if tick_count >= 2:
            raise StopAfterTwo

    with patch(
        "personal_deadline_management_agent.scheduler.run_once",
        side_effect=fake_run_once,
    ):
        with patch(
            "personal_deadline_management_agent.scheduler.time.sleep",
            side_effect=fake_sleep,
        ):
            with patch("personal_deadline_management_agent.scheduler.signal.signal"):
                with pytest.raises(StopAfterTwo):
                    run_forever(
                        settings=settings,
                        session_factory=MagicMock(return_value=FakeSession()),
                        notification_provider=provider,
                    )

    assert len(captured_nows) == 2
    for dt in captured_nows:
        assert dt.tzinfo is not None
        assert dt.tzinfo == timezone.utc


def test_run_forever_batch_size_from_settings():
    from personal_deadline_management_agent.scheduler import run_forever

    settings = config.Settings(
        database_url="sqlite:///x", scheduler_batch_size=42
    )
    provider = MagicMock()

    captured_limits: list[int] = []

    def fake_run_once(*, session_factory, notification_provider, now, limit):  # type: ignore[no-untyped-def]
        captured_limits.append(limit)
        return _tick_result()

    class StopAfterOne(Exception):
        pass

    def fake_sleep(seconds):  # type: ignore[no-untyped-def]
        raise StopAfterOne

    with patch(
        "personal_deadline_management_agent.scheduler.run_once",
        side_effect=fake_run_once,
    ):
        with patch(
            "personal_deadline_management_agent.scheduler.time.sleep",
            side_effect=fake_sleep,
        ):
            with patch("personal_deadline_management_agent.scheduler.signal.signal"):
                with pytest.raises(StopAfterOne):
                    run_forever(
                        settings=settings,
                        session_factory=MagicMock(return_value=FakeSession()),
                        notification_provider=provider,
                    )

    assert captured_limits[0] == 42


def test_run_forever_shutdown_stops_future_ticks():
    """Shutdown flag prevents scheduling new ticks."""
    from personal_deadline_management_agent.scheduler import run_forever

    settings = config.Settings(database_url="sqlite:///x")
    provider = MagicMock()

    tick_count = 0
    shutdown_handler = None

    def fake_run_once(**kwargs):  # type: ignore[no-untyped-def]
        nonlocal tick_count
        tick_count += 1
        return _tick_result()

    def capture_signal(sig, handler):  # type: ignore[no-untyped-def]
        nonlocal shutdown_handler
        shutdown_handler = handler

    def fake_sleep(seconds):  # type: ignore[no-untyped-def]
        # Simulate SIGTERM arriving during sleep.
        if shutdown_handler is not None:
            shutdown_handler(15, None)

    with patch(
        "personal_deadline_management_agent.scheduler.run_once",
        side_effect=fake_run_once,
    ):
        with patch(
            "personal_deadline_management_agent.scheduler.time.sleep",
            side_effect=fake_sleep,
        ):
            with patch(
                "personal_deadline_management_agent.scheduler.signal.signal",
                side_effect=capture_signal,
            ):
                run_forever(
                    settings=settings,
                    session_factory=MagicMock(return_value=FakeSession()),
                    notification_provider=provider,
                )

    assert tick_count == 1


def test_run_forever_engine_not_recreated_per_tick():
    """Engine/session_factory is reused — not recreated per tick."""
    from personal_deadline_management_agent.scheduler import run_forever

    settings = config.Settings(database_url="sqlite:///x")
    provider = MagicMock()
    factory = MagicMock(return_value=FakeSession())

    tick_count = 0

    def fake_run_once(**kwargs):  # type: ignore[no-untyped-def]
        nonlocal tick_count
        tick_count += 1
        return _tick_result()

    class StopAfterTwo(Exception):
        pass

    def fake_sleep(seconds):  # type: ignore[no-untyped-def]
        if tick_count >= 2:
            raise StopAfterTwo

    with patch(
        "personal_deadline_management_agent.scheduler.run_once",
        side_effect=fake_run_once,
    ):
        with patch(
            "personal_deadline_management_agent.scheduler.time.sleep",
            side_effect=fake_sleep,
        ):
            with patch("personal_deadline_management_agent.scheduler.signal.signal"):
                with pytest.raises(StopAfterTwo):
                    run_forever(
                        settings=settings,
                        session_factory=factory,
                        notification_provider=provider,
                    )

    assert tick_count == 2


def test_run_forever_infra_failure_continues():
    """One infrastructure failure does not prevent the next tick."""
    from personal_deadline_management_agent.scheduler import run_forever

    settings = config.Settings(database_url="sqlite:///x")
    provider = MagicMock()

    tick_count = 0

    def fake_run_once(**kwargs):  # type: ignore[no-untyped-def]
        nonlocal tick_count
        tick_count += 1
        if tick_count == 1:
            raise RuntimeError("DB connection lost")
        return _tick_result(sent_count=1, due_count=1)

    class StopAfterTwo(Exception):
        pass

    def fake_sleep(seconds):  # type: ignore[no-untyped-def]
        if tick_count >= 2:
            raise StopAfterTwo

    with patch(
        "personal_deadline_management_agent.scheduler.run_once",
        side_effect=fake_run_once,
    ):
        with patch(
            "personal_deadline_management_agent.scheduler.time.sleep",
            side_effect=fake_sleep,
        ):
            with patch("personal_deadline_management_agent.scheduler.signal.signal"):
                with pytest.raises(StopAfterTwo):
                    run_forever(
                        settings=settings,
                        session_factory=MagicMock(return_value=FakeSession()),
                        notification_provider=provider,
                    )

    assert tick_count == 2


def test_main_disposes_engine_on_shutdown(monkeypatch):
    """main() disposes the engine after run_forever returns."""
    from personal_deadline_management_agent import scheduler as sched_module

    fake_engine = MagicMock()
    fake_factory = MagicMock()
    fake_settings = config.Settings(database_url="sqlite:///x")

    monkeypatch.setattr(sched_module, "load_config", lambda: fake_settings)
    monkeypatch.setattr(
        sched_module, "create_engine_from_url", lambda url: fake_engine
    )
    monkeypatch.setattr(
        sched_module, "create_session_factory", lambda engine: fake_factory
    )
    monkeypatch.setattr(sched_module, "ConsoleNotificationProvider", MagicMock())
    monkeypatch.setattr(sched_module, "run_forever", MagicMock())

    sched_module.main()

    fake_engine.dispose.assert_called_once()


def test_main_disposes_engine_even_on_exception(monkeypatch):
    """Engine is disposed even if run_forever raises."""
    from personal_deadline_management_agent import scheduler as sched_module

    fake_engine = MagicMock()
    fake_factory = MagicMock()
    fake_settings = config.Settings(database_url="sqlite:///x")

    monkeypatch.setattr(sched_module, "load_config", lambda: fake_settings)
    monkeypatch.setattr(
        sched_module, "create_engine_from_url", lambda url: fake_engine
    )
    monkeypatch.setattr(
        sched_module, "create_session_factory", lambda engine: fake_factory
    )
    monkeypatch.setattr(sched_module, "ConsoleNotificationProvider", MagicMock())

    def failing_run_forever(**kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("unexpected")

    monkeypatch.setattr(sched_module, "run_forever", failing_run_forever)

    with pytest.raises(RuntimeError, match="unexpected"):
        sched_module.main()

    fake_engine.dispose.assert_called_once()