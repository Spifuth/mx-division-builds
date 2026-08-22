import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as c:
        yield c


def test_weapons_list(client):
    body = client.get("/api/weapons").json()
    assert body["total"] > 100
    assert body["rows"][0]["Name"]


def test_weapons_filter_by_type(client):
    body = client.get("/api/weapons", params={"type": "Rifle"}).json()
    assert body["total"] > 0
    assert {r["Weapon Type"] for r in body["rows"]} == {"Rifle"}


def test_weapons_search_is_case_insensitive(client):
    body = client.get("/api/weapons", params={"q": "police"}).json()
    assert body["total"] > 0


def test_weapon_by_name(client):
    listed = client.get("/api/weapons", params={"limit": 1}).json()["rows"][0]
    got = client.get(f"/api/weapons/{listed['Name']}").json()
    assert got["Name"] == listed["Name"]


def test_unknown_weapon_is_404(client):
    assert client.get("/api/weapons/definitely-not-a-gun").status_code == 404


@pytest.mark.parametrize("slot", ["mask", "chest", "backpack", "gloves", "holster", "kneepads"])
def test_every_gear_slot_serves(client, slot):
    body = client.get(f"/api/gear/{slot}").json()
    assert body["total"] > 0
    assert body["rows"][0]["Item Name"]


def test_unknown_gear_slot_is_404(client):
    assert client.get("/api/gear/hat").status_code == 404


def test_brands_are_joined_with_their_set_bonuses(client):
    rows = client.get("/api/brands").json()["rows"]
    assert rows[0]["Brand"]
    assert "bonuses" in rows[0]


def test_skills_and_one_skill(client):
    rows = client.get("/api/skills").json()["rows"]
    assert rows[0]["Skill ID"]
    one = client.get(f"/api/skills/{rows[0]['Skill ID']}").json()
    assert one["Skill ID"] == rows[0]["Skill ID"]
    # NON-EMPTY, deliberately. `assert "stats" in one` passes on a join that
    # matches nothing, which is exactly what an earlier draft of this endpoint
    # did -- it keyed on Variant alone and matched 0 of 43 rows.
    assert one["stats"], "the skillStats join produced nothing"
    assert one["stats"][0]["Stat"]


def test_every_skill_joins_to_its_stats(client):
    """All 43, not just the first. A join that works for one row and silently
    fails for the rest is the shape this project keeps shipping."""
    rows = client.get("/api/skills").json()["rows"]
    empty = []
    for row in rows:
        one = client.get(f"/api/skills/{row['Skill ID']}").json()
        if not one["stats"]:
            empty.append(row["Skill ID"])
    assert not empty, f"skills with no stats: {empty}"


def test_the_remaining_reference_endpoints(client):
    for path in (
        "/api/specializations",
        "/api/talents/gear",
        "/api/talents/weapon",
        "/api/attributes/gear",
        "/api/attributes/weapon",
        "/api/mods/gear",
        "/api/mods/weapon",
        "/api/mods/skill",
    ):
        body = client.get(path).json()
        assert body["total"] > 0, path


def test_limit_and_offset_window(client):
    first = client.get("/api/weapons", params={"limit": 5}).json()
    second = client.get("/api/weapons", params={"limit": 5, "offset": 5}).json()
    assert len(first["rows"]) == 5
    assert first["rows"][0] != second["rows"][0]
    assert first["total"] == second["total"]  # total is the match count, not the page
