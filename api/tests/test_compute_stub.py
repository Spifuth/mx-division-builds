from fastapi.testclient import TestClient

from app.main import create_app


def test_compute_is_501_until_phase_two():
    """Not 404. The frontend wires this call now and it must be distinguishable
    from a typo in the path."""
    with TestClient(create_app()) as client:
        response = client.post("/api/compute", json={"slots": {}})
    assert response.status_code == 501
    assert "phase" in response.json()["detail"].lower()
