"""The frontend's `lib/types.ts` as executable assertions.

This file is the API's contract with a frontend that already exists. Every
constant below is transcribed from the committed copy at
`docs/superpowers/specs/2026-08-23-frontend-contract-types.ts` -- if an
interface there gains a field, it gains one here, and the endpoint follows.

Two failure modes this suite is built to catch, because both look healthy:

* a *stringified* number. `"damage": "47364.5"` serialises fine, renders fine
  in a table cell, and silently breaks the moment the frontend does arithmetic
  or `.toLocaleString()` on it. Type is asserted, not just presence.
* an *unstable* id. `BuildLoadout` is twelve `string | null` item ids and
  nothing else, so a saved build is only as durable as the ids it stores. An
  id derived from row order survives every test that reads one dataset and
  breaks every saved build on the first upstream reorder.
"""

from __future__ import annotations

import csv
import random
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

# --- the interfaces, transcribed ------------------------------------------

ENVELOPE_KEYS = {"total", "limit", "offset", "results"}

WEAPON_REQUIRED = {
    "name": str, "type": str, "quality": str, "damage": (int, float),
    "rpm": (int, float), "magazine": (int, float), "optimalRange": (int, float),
    "accuracy": (int, float), "stability": (int, float), "handling": (int, float),
    "talents": list,
}
WEAPON_OPTIONAL = {"brand": str, "image": str, "id": str}

GEAR_REQUIRED = {
    "id": str, "name": str, "slot": str, "quality": str, "armor": (int, float),
    "coreAttribute": str, "attributeSlots": (int, float), "mod": bool,
}
GEAR_OPTIONAL = {"brand": str, "gearSet": str, "talent": str, "image": str}

BRAND_REQUIRED = {"id": str, "name": str, "pieces": (int, float), "bonuses": list}
BONUS_REQUIRED = {"count": (int, float), "bonus": str}

SKILL_REQUIRED = {
    "id": str, "name": str, "description": str, "variants": list, "tierStats": list,
}
SKILL_VARIANT_REQUIRED = {"id": str, "name": str, "description": str}
SKILL_TIER_STAT_REQUIRED = {"tier": (int, float), "label": str, "value": str}

SPECIALIZATION_REQUIRED = {
    "id": str, "name": str, "signatureWeapon": str, "description": str, "perks": list,
}

TALENT_REQUIRED = {"id": str, "name": str, "category": str, "description": str}

ATTRIBUTE_REQUIRED = {
    "id": str, "name": str, "category": str, "slot": str, "quality": str,
    "min": (int, float), "max": (int, float), "unit": str,
}

MOD_REQUIRED = {"id": str, "name": str, "category": str, "slot": str, "effect": str}

GEAR_SLOTS = ["mask", "chest", "backpack", "gloves", "holster", "kneepads"]

# Every list endpoint the frontend calls. /api/builds and /api/compute are
# Phase 2 and deliberately absent.
LIST_ENDPOINTS = [
    "/api/weapons",
    "/api/gear",
    *[f"/api/gear/{slot}" for slot in GEAR_SLOTS],
    "/api/gear-sets",
    "/api/brands",
    "/api/skills",
    "/api/specializations",
    "/api/talents/gear",
    "/api/talents/weapon",
    "/api/attributes/gear",
    "/api/attributes/weapon",
    "/api/mods/gear",
    "/api/mods/weapon",
    "/api/mods/skill",
    "/api/tables/brands",
]


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as c:
        yield c


def check_shape(item: dict, required: dict, optional: dict | None = None, where: str = "") -> None:
    """Every required key present and of the declared type; no key outside the
    interface. `bool` is checked before the numerics on purpose -- Python makes
    `isinstance(True, int)` true, so a `mod: true` would satisfy a numeric
    assertion and a stray `1` would satisfy a boolean one."""
    for key, expected in required.items():
        assert key in item, f"{where}: required field {key!r} missing from {sorted(item)}"
        value = item[key]
        if expected is bool:
            assert isinstance(value, bool), f"{where}.{key} is {type(value).__name__}, want bool"
        else:
            assert not isinstance(value, bool), f"{where}.{key} is a bool, want {expected}"
            assert isinstance(value, expected), (
                f"{where}.{key} is {type(value).__name__} ({value!r}), want {expected}"
            )
    known = set(required) | set(optional or {})
    assert set(item) <= known, f"{where}: unexpected fields {sorted(set(item) - known)}"


# --- the envelope ---------------------------------------------------------


@pytest.mark.parametrize("path", LIST_ENDPOINTS)
def test_every_list_endpoint_returns_the_envelope(client, path):
    """`{total, limit, offset, results}` and nothing else -- lib/fetcher.ts's
    ListResponse<T> reads all four, and the old `{count, total, rows}` gave it
    none of them."""
    body = client.get(path).json()
    assert set(body) == ENVELOPE_KEYS, f"{path} returned {sorted(body)}"
    assert isinstance(body["results"], list)
    assert body["total"] > 0, path


@pytest.mark.parametrize("path", LIST_ENDPOINTS)
def test_the_envelope_defaults_to_a_hundred_at_offset_zero(client, path):
    body = client.get(path).json()
    assert body["limit"] == 100, path
    assert body["offset"] == 0, path
    assert len(body["results"]) <= 100, path


def test_the_envelope_echoes_what_was_asked_for(client):
    body = client.get("/api/weapons", params={"limit": 7, "offset": 3}).json()
    assert body["limit"] == 7
    assert body["offset"] == 3
    assert len(body["results"]) == 7
    assert body["total"] > 7, "total is the match count, not the page length"


# --- the entities ---------------------------------------------------------


def test_weapons_match_the_weapon_interface(client):
    results = client.get("/api/weapons", params={"limit": 5000}).json()["results"]
    assert len(results) > 100
    for w in results:
        check_shape(w, WEAPON_REQUIRED, WEAPON_OPTIONAL, where=f"weapon {w.get('name')!r}")
        assert w["name"], "a nameless weapon cannot be picked, saved or looked up"
        assert all(isinstance(t, str) for t in w["talents"])


def test_weapon_numbers_are_numbers_not_strings(client):
    """The whole point of this task. `"Base Damage": "47364.5"` was the old
    shape and it type-checks as a string everywhere it is rendered."""
    results = client.get("/api/weapons", params={"limit": 5000}).json()["results"]
    assert any(isinstance(w["damage"], float) for w in results), (
        "the dataset has fractional damage (47364.5); if none survived as a float "
        "the coercion is truncating real data"
    )
    assert any(isinstance(w["damage"], int) for w in results), (
        "and whole numbers must stay whole: 57218, not 57218.0"
    )
    # Pinned rather than `> 0`. Exactly one weapon really does ship with
    # "Base Damage" of 0 -- Cooler, a named LMG -- so a blanket "every damage is
    # positive" would be asserting a fact about the dataset, and the moment it
    # failed the honest fix would be to weaken it. What must hold is that no
    # OTHER weapon reads as zero, which is what a coercion returning 0 on every
    # unparsed cell would look like.
    zeroed = {w["name"] for w in results if w["damage"] == 0}
    assert zeroed == {"Cooler"}, f"weapons whose damage coerced to 0: {sorted(zeroed)}"
    assert all(w["rpm"] >= 0 for w in results)


@pytest.mark.parametrize("slot", GEAR_SLOTS)
def test_each_gear_slot_matches_the_gear_piece_interface(client, slot):
    results = client.get(f"/api/gear/{slot}", params={"limit": 5000}).json()["results"]
    assert len(results) > 0
    for g in results:
        check_shape(g, GEAR_REQUIRED, GEAR_OPTIONAL, where=f"{slot} {g.get('name')!r}")
        assert g["slot"] == slot
        assert g["attributeSlots"] >= 0


def test_gear_serves_every_slot_at_once(client):
    """New endpoint. The compare and lookup views need the whole catalogue in
    one call -- `/api/gear?limit=300` in use-loadout-lookup.ts."""
    everything = client.get("/api/gear", params={"limit": 5000}).json()
    per_slot = sum(
        client.get(f"/api/gear/{s}", params={"limit": 5000}).json()["total"] for s in GEAR_SLOTS
    )
    assert everything["total"] == per_slot
    assert {g["slot"] for g in everything["results"]} == set(GEAR_SLOTS)


def test_gear_sets_are_separated_from_brands(client):
    """types.ts: "Dedicated named Gear Sets (distinct from civilian Brands)".
    sets-compare.tsx concatenates both lists, so an entry in both renders twice."""
    brands = client.get("/api/brands", params={"limit": 5000}).json()["results"]
    sets = client.get("/api/gear-sets", params={"limit": 5000}).json()["results"]
    assert len(sets) == 27, "brands.csv has 27 rows with Type=Gearset"
    assert not {b["name"] for b in brands} & {s["name"] for s in sets}

    for entity, where in ((brands, "brand"), (sets, "gear set")):
        for e in entity:
            check_shape(e, BRAND_REQUIRED, where=f"{where} {e.get('name')!r}")
            for bonus in e["bonuses"]:
                check_shape(bonus, BONUS_REQUIRED, where=f"{where} {e['name']!r} bonus")


def test_brand_bonuses_are_joined_and_counted_by_piece(client):
    """brandsetBonuses appends the tier digit with no separator, and the digit
    is 0-based for brands but 1-based for gear sets -- 5.11 Tactical is 0/1/2,
    True Patriot is 1/2/3. `count` is the piece count the UI renders as "Npc",
    and sets-compare.tsx looks bonuses up by it, so an off-by-one blanks the
    whole ladder while every row still returns 200."""
    brands = client.get("/api/brands", params={"limit": 5000}).json()["results"]
    sets = client.get("/api/gear-sets", params={"limit": 5000}).json()["results"]

    brand = next(b for b in brands if b["name"] == "5.11 Tactical")
    assert [x["count"] for x in brand["bonuses"]] == [1, 2, 3]
    assert brand["pieces"] == 3

    patriot = next(s for s in sets if s["name"] == "True Patriot")
    assert [x["count"] for x in patriot["bonuses"]] == [2, 3, 4]
    assert patriot["pieces"] == 4, "a gear set's ladder tops out at its 4-piece talent"
    assert all(x["bonus"] for x in patriot["bonuses"]), "an empty bonus string renders as nothing"

    # Named, not tolerated: exactly two pseudo-brands have no set bonuses.
    assert {b["name"] for b in brands if not b["bonuses"]} == {"Exotic", "Crafted"}
    assert all(s["bonuses"] for s in sets)


def test_skills_match_the_skill_interface(client):
    skills = client.get("/api/skills", params={"limit": 5000}).json()["results"]
    assert len(skills) == 12, "skill.csv has 43 variant rows across 12 base skills"
    for s in skills:
        check_shape(s, SKILL_REQUIRED, where=f"skill {s.get('name')!r}")
        assert s["variants"], f"{s['name']} has no variants"
        for v in s["variants"]:
            check_shape(v, SKILL_VARIANT_REQUIRED, where=f"{s['name']} variant")
            assert v["description"], "skill.csv's Desc is populated for all 43 rows"
        assert s["tierStats"], f"{s['name']}: the skillStats join produced nothing"
        for t in s["tierStats"]:
            check_shape(t, SKILL_TIER_STAT_REQUIRED, where=f"{s['name']} tierStat")

    assert sum(len(s["variants"]) for s in skills) == 43


def test_specializations_match_the_interface(client):
    results = client.get("/api/specializations", params={"limit": 5000}).json()["results"]
    assert len(results) == 7, "specialization.csv names 7 specs across its Stat/Val rows"
    for s in results:
        check_shape(s, SPECIALIZATION_REQUIRED, where=f"spec {s.get('name')!r}")
        assert all(isinstance(p, str) for p in s["perks"])


@pytest.mark.parametrize("category", ["gear", "weapon"])
def test_talents_match_the_interface(client, category):
    results = client.get(f"/api/talents/{category}", params={"limit": 5000}).json()["results"]
    assert len(results) > 100
    for t in results:
        check_shape(t, TALENT_REQUIRED, where=f"{category} talent {t.get('name')!r}")
        assert t["category"] == category
        assert t["name"]


@pytest.mark.parametrize("category", ["gear", "weapon"])
def test_attributes_match_the_interface(client, category):
    results = client.get(f"/api/attributes/{category}", params={"limit": 5000}).json()["results"]
    assert len(results) > 0
    for a in results:
        check_shape(a, ATTRIBUTE_REQUIRED, where=f"{category} attribute {a.get('name')!r}")
        assert a["category"] == category
        assert a["max"] > 0, "Max is the one number this table actually carries"


@pytest.mark.parametrize("category", ["gear", "weapon", "skill"])
def test_mods_match_the_interface(client, category):
    results = client.get(f"/api/mods/{category}", params={"limit": 5000}).json()["results"]
    assert len(results) > 0
    for m in results:
        check_shape(m, MOD_REQUIRED, where=f"{category} mod {m.get('name')!r}")
        assert m["category"] == category
        assert m["name"]
        assert m["effect"], "an effectless mod is a blank cell in the mods table"


# --- the single-object endpoints ------------------------------------------


def test_one_weapon_is_the_same_object_the_list_serves(client):
    listed = client.get("/api/weapons", params={"limit": 1}).json()["results"][0]
    got = client.get(f"/api/weapons/{listed['name']}").json()
    assert got == listed
    check_shape(got, WEAPON_REQUIRED, WEAPON_OPTIONAL, where="weapon detail")


def test_one_skill_is_addressed_by_the_id_the_list_serves(client):
    """The frontend's /api/skills/[id] route looks the skill up by `s.id` taken
    from the list -- not by any column in the CSV."""
    listed = client.get("/api/skills", params={"limit": 5000}).json()["results"]
    for skill in listed:
        got = client.get(f"/api/skills/{skill['id']}").json()
        assert got == skill, skill["id"]


def test_unknown_single_objects_are_404_not_500(client):
    assert client.get("/api/weapons/definitely-not-a-gun").status_code == 404
    assert client.get("/api/skills/definitely-not-a-skill").status_code == 404


# --- identity -------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/gear",
        *[f"/api/gear/{slot}" for slot in GEAR_SLOTS],
        "/api/gear-sets",
        "/api/brands",
        "/api/skills",
        "/api/specializations",
        "/api/talents/gear",
        "/api/talents/weapon",
        "/api/attributes/gear",
        "/api/attributes/weapon",
        "/api/mods/gear",
        "/api/mods/weapon",
        "/api/mods/skill",
    ],
)
def test_ids_are_unique_within_an_endpoint(client, path):
    """React keys off these. Two rows sharing an id is a silently dropped row
    in the UI, not an error anywhere."""
    results = client.get(path, params={"limit": 5000}).json()["results"]
    ids = [r["id"] for r in results]
    assert all(ids), f"{path}: a blank id"
    duplicated = {i for i in ids if ids.count(i) > 1}
    assert not duplicated, f"{path}: duplicate ids {sorted(duplicated)}"


def test_ids_survive_the_table_being_reordered():
    """A saved build stores twelve ids and nothing else. An id that encodes row
    position passes every single-dataset test in this file and breaks every
    saved build the first time upstream sorts a table differently."""
    from app import normalise

    data_dir = Path(__import__("os").getenv("TD2_DATA_DIR", Path(__file__).parents[2] / "public" / "data"))
    with (data_dir / "mask.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = [{k: (v or "") for k, v in r.items() if k} for r in csv.DictReader(handle)]

    shuffled = rows[:]
    random.Random(20260823).shuffle(shuffled)
    assert shuffled != rows, "the shuffle did nothing; this test would prove nothing"

    straight = {r["Item Name"]: normalise.gear(r, "mask")["id"] for r in rows}
    reordered = {r["Item Name"]: normalise.gear(r, "mask")["id"] for r in shuffled}
    assert straight == reordered

    # And deterministic call-to-call, not merely order-independent.
    assert normalise.gear(rows[0], "mask")["id"] == normalise.gear(rows[0], "mask")["id"]


def test_the_documented_id_shape_is_what_ships():
    from app import normalise

    built = normalise.gear({"Item Name": "Providence Vigilance Mask"}, "mask")
    assert built["id"] == "mask-providence-vigilance-mask"


def test_normalising_never_mutates_the_row_it_was_given():
    """Rows in app.state.dataset are shared across every request and the
    Dataset is only shallow-frozen. One endpoint already shipped handing a
    caller the shared row object itself."""
    from app import normalise

    row = {"Name": "Test Gun", "Weapon Type": "Rifle", "Quality": "High End", "Base Damage": "10"}
    before = dict(row)
    normalise.weapon(row)
    assert row == before


# --- /api/meta ------------------------------------------------------------


def test_meta_matches_the_meta_interface(client):
    body = client.get("/api/meta").json()
    assert body["datasetVersion"] == "26.0-mdb"
    assert isinstance(body["tables"], list)
    assert len(body["tables"]) == 20
    for table in body["tables"]:
        assert set(table) == {"name", "rows"}
        assert isinstance(table["rows"], int)
        assert table["rows"] > 0


def test_meta_keeps_the_resilience_fields_task_5_added(client):
    """Extra fields are ignored by the frontend; removing these would undo the
    only way a degraded snapshot is visible without shell access."""
    body = client.get("/api/meta").json()
    assert "lastRefresh" in body
    assert "degraded" in body


def test_meta_declares_the_fields_the_dataset_cannot_fill(client):
    """A confident `0` the frontend cannot tell from real data is the same
    class of defect as a check that cannot fail. Every zero this API invents
    has to be listed here."""
    unsupported = client.get("/api/meta").json()["unsupportedFields"]
    assert set(unsupported["weapon"]) >= {"accuracy", "stability", "handling"}
    assert "armor" in unsupported["gear"]
    assert set(unsupported["specialization"]) >= {"signatureWeapon", "description", "perks"}


def test_every_field_declared_unsupported_is_actually_empty(client):
    """Guards the declaration against drift in the direction that matters: a
    field that quietly starts carrying real data while /api/meta still tells
    the UI to grey it out."""
    unsupported = client.get("/api/meta").json()["unsupportedFields"]
    samples = {
        "weapon": "/api/weapons",
        "gear": "/api/gear",
        "brand": "/api/brands",
        "gearSet": "/api/gear-sets",
        "skill": "/api/skills",
        "specialization": "/api/specializations",
        "talent:gear": "/api/talents/gear",
        "talent:weapon": "/api/talents/weapon",
        "attribute:gear": "/api/attributes/gear",
        "attribute:weapon": "/api/attributes/weapon",
        "mod:gear": "/api/mods/gear",
        "mod:weapon": "/api/mods/weapon",
        "mod:skill": "/api/mods/skill",
    }
    assert set(unsupported) == set(samples), "every entity gets an entry, even an empty one"

    # `image` and `brand` are optional in types.ts, so a normaliser is allowed to
    # omit them entirely. Every other unsupported field is REQUIRED there, which
    # means it must be present and empty -- not absent.
    OPTIONAL_IN_TYPES = {"image", "brand"}

    for entity, path in samples.items():
        results = client.get(path, params={"limit": 5000}).json()["results"]
        assert results, f"{entity}: nothing to check"
        for field in unsupported[entity]:
            if field in OPTIONAL_IN_TYPES:
                values = {repr(r.get(field)) for r in results}
                assert values <= {"0", "''", "[]", "None"}, (
                    f"{entity}.{field} is declared unsupported but carries {sorted(values)[:3]}"
                )
                continue

            # Presence first. Without this the assertion below accepts "None"
            # from .get() on a field that does not exist at all, so a typo in
            # UNSUPPORTED_FIELDS -- "armour" for "armor" -- passes here while
            # /api/meta greys out nothing and the UI draws a confident zero as
            # fact. This is the one check protecting the whole unsupported-field
            # mechanism, and it was the one check that could not fail.
            missing = [r for r in results if field not in r]
            assert not missing, (
                f"{entity}.{field} is declared unsupported but is not a field on "
                f"{len(missing)} of {len(results)} rows -- is it spelled right?"
            )
            values = {repr(r[field]) for r in results}
            assert values <= {"0", "''", "[]"}, (
                f"{entity}.{field} is declared unsupported but carries {sorted(values)[:3]}"
            )
