from fastapi.testclient import TestClient

from personal_deadline_management_agent.config import Settings
from personal_deadline_management_agent.main import create_app


def test_health_ok():
    app = create_app(Settings(database_url="sqlite:///:memory:"))
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
