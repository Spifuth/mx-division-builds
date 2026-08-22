from fastapi.testclient import TestClient

from app.main import create_app


def test_meta_reports_the_dataset():
    with TestClient(create_app()) as client:
        body = client.get("/api/meta").json()
    assert body["version"] == "26.0-mdb"
    assert body["table_count"] == 20
    assert body["counts"]["weapon"] > 100


def test_raw_table_escape_hatch():
    with TestClient(create_app()) as client:
        body = client.get("/api/tables/brands").json()
    assert body["name"] == "brands"
    assert body["rows"][0]["Brand"]


def test_unknown_table_is_404_not_500():
    with TestClient(create_app()) as client:
        assert client.get("/api/tables/nope").status_code == 404
