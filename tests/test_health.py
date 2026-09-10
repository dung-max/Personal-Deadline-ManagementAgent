from fastapi.testclient import TestClient

from personal_deadline_management_agent.config import Settings
from personal_deadline_management_agent.main import create_app


def test_health_ok():
    app = create_app(Settings(database_url="sqlite:///:memory:"))
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_ready_ok():
    app = create_app(Settings(database_url="sqlite:///:memory:"))
    with TestClient(app) as client:
        resp = client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready"}


def test_health_ready_db_unavailable():
    app = create_app(Settings(database_url="sqlite:///:memory:"))

    class _BrokenSession:
        def execute(self, *args, **kwargs):
            raise RuntimeError("db down")

        def close(self):
            pass

    class _BrokenSessionFactory:
        def __call__(self):
            return _BrokenSession()

    with TestClient(app) as client:
        # Replace the session factory with one that always fails.
        client.app.state.session_factory = _BrokenSessionFactory()
        resp = client.get("/health/ready")
    assert resp.status_code == 503
    assert resp.json() == {"status": "not ready"}