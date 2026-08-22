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


# Exactly the two pseudo-brands that have no set bonuses in the game.
BRANDS_WITHOUT_BONUSES = {"Exotic", "Crafted"}


def test_brands_are_joined_with_their_set_bonuses(client):
    rows = client.get("/api/brands").json()["rows"]
    assert rows[0]["Brand"]
    # NON-EMPTY. `assert "bonuses" in rows[0]` is true of an empty list, and
    # this endpoint shipped joining on a key that matched 0 of 66 brands while
    # that assertion passed. Same trap the skills test carries a warning about.
    assert rows[0]["bonuses"], "the brandsetBonuses join produced nothing"


def test_every_brand_but_the_two_without_bonuses_joins(client):
    """All 66, not a sample, and the exceptions are named rather than tolerated.

    brandsetBonuses appends the tier digit to the brand name with no separator
    ("5.11 Tactical0"), so a raw-string join silently yields [] everywhere.
    Asserting a specific expected-empty set means a regression that empties one
    more brand fails here instead of looking like more of the same.
    """
    rows = client.get("/api/brands").json()["rows"]
    assert len(rows) == 66
    empty = {r["Brand"] for r in rows if not r["bonuses"]}
    assert empty == BRANDS_WITHOUT_BONUSES


def test_brand_bonuses_carry_their_tier_in_order(client):
    """The stripped digit is the bonus tier, not noise -- 0/1/2 is the
    1-, 2- and 3-piece bonus. Dropping it would lose which bonus is which."""
    rows = client.get("/api/brands").json()["rows"]
    row = next(r for r in rows if r["Brand"] not in BRANDS_WITHOUT_BONUSES)
    tiers = [b["tier"] for b in row["bonuses"]]
    assert tiers == sorted(tiers)
    assert tiers[0] == 0


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


def test_total_is_the_match_count_not_the_page_length(client):
    """The previous test cannot catch this on its own: both its calls use
    limit=5, so a regression to `total = len(window)` would report 5 and 5, and
    `5 == 5` still passes. This pins total against a value that is known to
    exceed the page size."""
    unlimited = client.get("/api/weapons").json()
    paged = client.get("/api/weapons", params={"limit": 5}).json()
    assert paged["total"] == unlimited["total"]
    assert paged["total"] > len(paged["rows"])


def test_quality_and_brand_filters(client):
    """Implemented in the first draft but never exercised -- the same coverage
    gap that let the brands join ship broken."""
    everything = client.get("/api/weapons").json()["total"]
    high_end = client.get("/api/weapons", params={"quality": "High End"}).json()
    assert 0 < high_end["total"] < everything
    assert {r["Quality"] for r in high_end["rows"]} == {"High End"}

    # Case-insensitive, like every other filter here.
    assert client.get("/api/weapons", params={"quality": "high end"}).json()["total"] == high_end["total"]

    masks = client.get("/api/gear/mask").json()["total"]
    branded = client.get("/api/gear/mask", params={"brand": "5.11 Tactical"}).json()
    assert 0 < branded["total"] < masks
    assert {r["Brand"] for r in branded["rows"]} == {"5.11 Tactical"}


def test_a_filter_that_matches_nothing_is_an_empty_result_not_everything(client):
    """A filter silently ignoring its argument returns the whole table and looks
    healthy. Assert the negative case explicitly."""
    body = client.get("/api/weapons", params={"type": "Trebuchet"}).json()
    assert body["total"] == 0
    assert body["rows"] == []
