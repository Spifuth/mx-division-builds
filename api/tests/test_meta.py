from fastapi.testclient import TestClient

from app.main import create_app


def test_meta_reports_the_dataset():
    with TestClient(create_app()) as client:
        body = client.get("/api/meta").json()
    assert body["datasetVersion"] == "26.0-mdb"
    assert body["tableCount"] == 20
    assert body["counts"]["weapon"] > 100
    # `tables` is the shape the UI reads -- Meta in types.ts is exactly
    # {datasetVersion, tables: {name, rows}[]} -- and it has to agree with the
    # counts served beside it, not be assembled from a separate source.
    assert {t["name"]: t["rows"] for t in body["tables"]} == body["counts"]


def test_raw_table_escape_hatch():
    """The one endpoint that does not adapt its rows.

    It used to answer `{name, count, rows}`; it now answers the same envelope
    as every other list endpoint, so the table's name lives only in the URL.
    What is worth asserting is unchanged and is the reason the endpoint exists:
    the rows come back with their CSV headers intact, spaces and all, so a
    column with no home in types.ts is still reachable without a code change.
    """
    with TestClient(create_app()) as client:
        body = client.get("/api/tables/brands").json()
    assert set(body) == {"total", "limit", "offset", "results"}
    assert body["results"][0]["Brand"]
    assert "Type" in body["results"][0], "raw means raw: no renaming, no dropping"


def test_unknown_table_is_404_not_500():
    with TestClient(create_app()) as client:
        assert client.get("/api/tables/nope").status_code == 404
