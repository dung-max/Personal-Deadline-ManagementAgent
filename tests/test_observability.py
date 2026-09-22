from __future__ import annotations

import json
import logging
import time
from uuid import UUID, uuid4

from fastapi import Request
from fastapi.testclient import TestClient

from personal_deadline_management_agent.config import Settings
from personal_deadline_management_agent.main import create_app
from personal_deadline_management_agent.observability import (
    CORRELATION_HEADER,
    ReadableFormatter,
    StructuredFormatter,
    get_correlation_id,
)


def _client() -> TestClient:
    return TestClient(create_app(Settings(database_url="sqlite:///:memory:")))


def test_missing_correlation_id_is_generated_and_returned() -> None:
    with _client() as client:
        response = client.get("/health")

    correlation_id = response.headers[CORRELATION_HEADER]
    assert len(correlation_id) == 36
    assert str(UUID(correlation_id)) == correlation_id


def test_valid_caller_correlation_id_is_preserved() -> None:
    correlation_id = str(uuid4())
    with _client() as client:
        response = client.get("/health", headers={CORRELATION_HEADER: correlation_id})

    assert response.headers[CORRELATION_HEADER] == correlation_id


def test_invalid_correlation_id_is_replaced() -> None:
    with _client() as client:
        response = client.get("/health", headers={CORRELATION_HEADER: "not-a-uuid"})

    generated = response.headers[CORRELATION_HEADER]
    assert generated != "not-a-uuid"
    assert str(UUID(generated)) == generated


def test_overlength_correlation_id_is_replaced() -> None:
    with _client() as client:
        response = client.get("/health", headers={CORRELATION_HEADER: "a" * 100})

    assert str(UUID(response.headers[CORRELATION_HEADER])) == response.headers[CORRELATION_HEADER]


def test_correlation_id_is_available_in_request_state_and_logging_context(caplog) -> None:
    app = create_app(Settings(database_url="sqlite:///:memory:"))
    expected = str(uuid4())

    @app.get("/test-correlation")
    def test_correlation(request: Request) -> dict[str, str]:
        logging.getLogger("test.correlation").info(
            "safe event", extra={"event_name": "test.context"}
        )
        return {"state": request.state.correlation_id, "context": get_correlation_id()}

    with caplog.at_level(logging.INFO, logger="test.correlation"):
        with TestClient(app) as client:
            response = client.get("/test-correlation", headers={CORRELATION_HEADER: expected})

    assert response.json() == {"state": expected, "context": expected}
    record = next(record for record in caplog.records if record.name == "test.correlation")
    assert record.correlation_id == expected
    assert record.event_name == "test.context"


def test_request_log_contains_safe_structured_fields(caplog) -> None:
    correlation_id = str(uuid4())
    with caplog.at_level(logging.INFO, logger="personal_deadline_management_agent.observability"):
        with _client() as client:
            response = client.get("/health", headers={CORRELATION_HEADER: correlation_id})

    record = next(
        record
        for record in caplog.records
        if getattr(record, "event_name", None) == "request.completed"
    )
    assert record.correlation_id == correlation_id
    assert record.http_method == "GET"
    assert record.http_path == "/health"
    assert record.status_code == 200
    assert isinstance(record.duration_ms, float)
    assert record.duration_ms >= 0
    assert response.status_code == 200


def test_request_log_records_duration_and_status(caplog) -> None:
    app = create_app(Settings(database_url="sqlite:///:memory:"))

    @app.get("/slow-test")
    def slow_test() -> dict[str, str]:
        time.sleep(0.001)
        return {"ok": "yes"}

    with caplog.at_level(logging.INFO, logger="personal_deadline_management_agent.observability"):
        with TestClient(app) as client:
            response = client.get("/slow-test")

    record = next(
        record
        for record in caplog.records
        if getattr(record, "event_name", None) == "request.completed"
    )
    assert response.status_code == 200
    assert record.status_code == 200
    assert record.duration_ms >= 1


def test_exception_path_returns_existing_response_and_logs_correlation(caplog) -> None:
    app = create_app(Settings(database_url="sqlite:///:memory:"))
    correlation_id = str(uuid4())

    @app.get("/raises")
    def raises() -> None:
        raise RuntimeError("internal-only detail")

    with caplog.at_level(logging.ERROR):
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/raises", headers={CORRELATION_HEADER: correlation_id})

    assert response.status_code == 500
    assert response.json()["message"] == "Internal server error"
    assert response.headers[CORRELATION_HEADER] == correlation_id
    records = [
        record
        for record in caplog.records
        if record.name == "personal_deadline_management_agent.main"
        and "Unhandled exception" in record.getMessage()
    ]
    assert records
    assert records[-1].correlation_id == correlation_id


def test_validation_error_also_returns_correlation_id() -> None:
    with _client() as client:
        response = client.post("/api/v1/tasks", json={})

    assert response.status_code == 400
    assert str(UUID(response.headers[CORRELATION_HEADER])) == response.headers[CORRELATION_HEADER]
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_structured_formatter_uses_allowlisted_fields() -> None:
    record = logging.LogRecord("test.logger", logging.INFO, __file__, 1, "safe message", (), None)
    record.event_name = "test.event"
    record.correlation_id = str(uuid4())
    record.http_method = "POST"
    record.http_path = "/api/v1/agent/chat"
    record.status_code = 200
    record.duration_ms = 12.5
    record.secret = "must-not-appear"
    record.prompt = "private prompt"

    payload = json.loads(StructuredFormatter().format(record))
    assert payload["event_name"] == "test.event"
    assert payload["correlation_id"] == record.correlation_id
    assert payload["duration_ms"] == 12.5
    assert "secret" not in payload
    assert "prompt" not in payload


def test_structured_formatter_does_not_dump_arbitrary_extra_fields() -> None:
    record = logging.LogRecord("test.logger", logging.INFO, __file__, 1, "safe", (), None)
    record.request_payload = {"message": "do not log"}
    output = StructuredFormatter().format(record)
    assert "request_payload" not in output
    assert "do not log" not in output


def test_readable_formatter_includes_event_and_correlation_id() -> None:
    record = logging.LogRecord("test.logger", logging.INFO, __file__, 1, "safe", (), None)
    record.event_name = "test.event"
    record.correlation_id = str(uuid4())
    output = ReadableFormatter("%(message)s").format(record)
    assert "safe" in output
    assert "event=test.event" in output
    assert f"correlation_id={record.correlation_id}" in output
