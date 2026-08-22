from fastapi.testclient import TestClient

from app.main import create_app


def test_health_is_independent_of_everything_else():
    """Liveness must not depend on upstream, the database, or the dataset.

    A health check that goes red when a third party is down cannot be used to
    decide whether to restart the container.
    """
    with TestClient(create_app()) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
