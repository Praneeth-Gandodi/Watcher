from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)


def test_health_contract() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "watcher-backend",
        "version": "0.1.0",
    }
