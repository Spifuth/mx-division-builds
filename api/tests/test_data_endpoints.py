"""Endpoint behaviour: filtering, paging, and the two joins that shipped broken.

Shape is `tests/test_contract.py`'s job. What is asserted here is that the rows
coming back are the *right* rows -- a distinction worth keeping apart, because
a filter that ignores its argument and a filter that returns the wrong shape
fail for entirely different reasons and want different fixes.
"""

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
    assert body["results"][0]["name"]


def test_weapons_filter_by_type(client):
    body = client.get("/api/weapons", params={"type": "Rifle"}).json()
    assert body["total"] > 0
    assert {r["type"] for r in body["results"]} == {"Rifle"}


def test_weapons_search_is_case_insensitive(client):
    body = client.get("/api/weapons", params={"q": "police"}).json()
    assert body["total"] > 0


def test_weapon_by_name(client):
    listed = client.get("/api/weapons", params={"limit": 1}).json()["results"][0]
    got = client.get(f"/api/weapons/{listed['name']}").json()
    assert got["name"] == listed["name"]


def test_unknown_weapon_is_404(client):
    assert client.get("/api/weapons/definitely-not-a-gun").status_code == 404


@pytest.mark.parametrize("slot", ["mask", "chest", "backpack", "gloves", "holster", "kneepads"])
def test_every_gear_slot_serves(client, slot):
    body = client.get(f"/api/gear/{slot}").json()
    assert body["total"] > 0
    assert body["results"][0]["name"]


def test_unknown_gear_slot_is_404(client):
    assert client.get("/api/gear/hat").status_code == 404


# Exactly the two pseudo-brands that have no set bonuses in the game.
BRANDS_WITHOUT_BONUSES = {"Exotic", "Crafted"}


def test_brands_are_joined_with_their_set_bonuses(client):
    results = client.get("/api/brands").json()["results"]
    assert results[0]["name"]
    # NON-EMPTY. `assert "bonuses" in results[0]` is true of an empty list, and
    # this endpoint shipped joining on a key that matched 0 of 66 brands while
    # that assertion passed. Same trap the skills test carries a warning about.
    assert results[0]["bonuses"], "the brandsetBonuses join produced nothing"


def test_every_brand_but_the_two_without_bonuses_joins(client):
    """All of them, not a sample, and the exceptions are named rather than
    tolerated.

    brandsetBonuses appends the tier digit to the brand name with no separator
    ("5.11 Tactical0"), so a raw-string join silently yields [] everywhere.
    Asserting a specific expected-empty set means a regression that empties one
    more brand fails here instead of looking like more of the same.

    39, not the 66 rows in brands.csv: the 27 rows whose Type is "Gearset" moved
    to /api/gear-sets, because types.ts calls gear sets "distinct from civilian
    Brands" and the compare view concatenates the two lists.
    """
    results = client.get("/api/brands", params={"limit": 5000}).json()["results"]
    assert len(results) == 39
    empty = {r["name"] for r in results if not r["bonuses"]}
    assert empty == BRANDS_WITHOUT_BONUSES

    sets = client.get("/api/gear-sets", params={"limit": 5000}).json()["results"]
    assert len(sets) == 27
    assert all(s["bonuses"] for s in sets), "every gear set has a bonus ladder"


def test_brand_bonuses_carry_their_piece_count_in_order(client):
    """The stripped digit is the bonus tier, not noise, and `count` is that
    tier as the piece count the UI renders and looks bonuses up by.

    It is not uniformly 0-based: civilian brands number 0/1/2 and gear sets
    number 1/2/3, which is tier = pieces - 1 in both cases. Dropping the digit
    would lose which bonus is which; keeping it raw would put every gear set's
    ladder one row too high.
    """
    brands = client.get("/api/brands", params={"limit": 5000}).json()["results"]
    row = next(r for r in brands if r["name"] not in BRANDS_WITHOUT_BONUSES)
    counts = [b["count"] for b in row["bonuses"]]
    assert counts == sorted(counts)
    assert counts[0] == 1, "a civilian brand's ladder starts at its 1-piece bonus"

    sets = client.get("/api/gear-sets", params={"limit": 5000}).json()["results"]
    assert [b["count"] for b in sets[0]["bonuses"]][0] == 2, (
        "a gear set's ladder starts at its 2-piece bonus"
    )


def test_skills_and_one_skill(client):
    results = client.get("/api/skills").json()["results"]
    assert results[0]["id"]
    one = client.get(f"/api/skills/{results[0]['id']}").json()
    assert one["id"] == results[0]["id"]
    # NON-EMPTY, deliberately. `assert "tierStats" in one` passes on a join that
    # matches nothing, which is exactly what an earlier draft of this endpoint
    # did -- it keyed on Variant alone and matched 0 of 43 rows.
    assert one["tierStats"], "the skillStats join produced nothing"
    assert one["tierStats"][0]["label"]


def test_every_skill_joins_to_its_stats(client):
    """All 12, not just the first. A join that works for one row and silently
    fails for the rest is the shape this project keeps shipping.

    The join itself is still per *variant* -- "Burn" + "Sticky Bomb" ->
    "Burn Sticky Bomb", 43 of 43 -- so this also pins that every variant inside
    every skill found its stats, not merely that each skill found some.
    """
    results = client.get("/api/skills", params={"limit": 5000}).json()["results"]
    assert len(results) == 12
    empty = [s["id"] for s in results if not client.get(f"/api/skills/{s['id']}").json()["tierStats"]]
    assert not empty, f"skills with no stats: {empty}"

    # Every variant is represented, so a skill that silently lost one variant's
    # stats while keeping its siblings' still fails here. Turret has four
    # variants and each contributes a "Damage" row -- without the label prefix
    # those four are indistinguishable and this assertion is what pins it.
    for skill in results:
        labelled = {t["label"].split(": ", 1)[0] for t in skill["tierStats"]}
        assert labelled == {v["name"].removesuffix(f" {skill['name']}") for v in skill["variants"]}


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
    assert len(first["results"]) == 5
    assert first["results"][0] != second["results"][0]
    assert first["total"] == second["total"]  # total is the match count, not the page


def test_total_is_the_match_count_not_the_page_length(client):
    """The previous test cannot catch this on its own: both its calls use
    limit=5, so a regression to `total = len(window)` would report 5 and 5, and
    `5 == 5` still passes. This pins total against a value that is known to
    exceed the page size."""
    unlimited = client.get("/api/weapons", params={"limit": 5000}).json()
    paged = client.get("/api/weapons", params={"limit": 5}).json()
    assert paged["total"] == unlimited["total"]
    assert paged["total"] > len(paged["results"])


def test_quality_and_brand_filters(client):
    """Implemented in the first draft but never exercised -- the same coverage
    gap that let the brands join ship broken.

    "Standard" rather than the CSV's "High End": WeaponQuality and GearQuality
    declare the vocabulary the frontend knows, and the filter has to speak the
    same one it answers in, or `?quality=` matches nothing the list ever showed.
    """
    everything = client.get("/api/weapons").json()["total"]
    standard = client.get("/api/weapons", params={"quality": "Standard", "limit": 5000}).json()
    assert 0 < standard["total"] < everything
    assert {r["quality"] for r in standard["results"]} == {"Standard"}
    assert standard["total"] == 176, "the 176 rows whose CSV Quality is High End"

    # Case-insensitive, like every other filter here.
    assert client.get("/api/weapons", params={"quality": "standard"}).json()["total"] == standard["total"]

    masks = client.get("/api/gear/mask").json()["total"]
    branded = client.get("/api/gear/mask", params={"brand": "5.11 Tactical", "limit": 5000}).json()
    assert 0 < branded["total"] < masks
    assert {r["brand"] for r in branded["results"]} == {"5.11 Tactical"}

    # A gear set reaches the same filter through `gearSet`, not `brand`: one
    # CSV column feeds both fields, and answering nothing here would look like
    # the set has no pieces rather than like the caller used the wrong name.
    patriot = client.get("/api/gear/mask", params={"brand": "True Patriot", "limit": 5000}).json()
    assert patriot["total"] > 0
    assert {r["gearSet"] for r in patriot["results"]} == {"True Patriot"}


def test_a_filter_that_matches_nothing_is_an_empty_result_not_everything(client):
    """A filter silently ignoring its argument returns the whole table and looks
    healthy. Assert the negative case explicitly."""
    body = client.get("/api/weapons", params={"type": "Trebuchet"}).json()
    assert body["total"] == 0
    assert body["results"] == []


def test_the_raw_csv_quality_no_longer_matches_a_normalised_filter(client):
    """The other half of the rename, and the half that can rot silently.

    If the filter ever went back to comparing against the raw column, both
    "Standard" and "High End" would work and no assertion above would notice.
    """
    assert client.get("/api/weapons", params={"quality": "High End"}).json()["total"] == 0


def test_search_does_not_match_the_synthetic_id(client):
    """Typing "mask" into the gear search must not return every mask.

    ids are built as kind-plus-slug, so searching them made the prefix match the
    whole table: /api/gear/mask?q=mask returned 76 of 76, /api/weapons?q=weapon
    323 of 323, with nothing to indicate the filter had done nothing. id is also
    the one field the user never sees.
    """
    everything = client.get("/api/gear/mask").json()["total"]
    by_prefix = client.get("/api/gear/mask", params={"q": "mask"}).json()

    assert by_prefix["total"] < everything, "a structural field still matches every row"
    assert by_prefix["total"] > 0, "and it must still find the masks actually named 'mask'"
    assert all("mask" in r["name"].casefold() for r in by_prefix["results"])

    weapons = client.get("/api/weapons").json()["total"]
    assert client.get("/api/weapons", params={"q": "weapon"}).json()["total"] < weapons
