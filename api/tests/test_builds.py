"""Saved builds: the first endpoints in this service that touch a disk.

Everything here is written against the frontend's committed contract
(`docs/superpowers/specs/2026-08-23-frontend-contract-types.ts`) and against
its own mock handlers in `app/api/builds/`, which is what the UI is coded to
today. Where the two disagree the type wins, and the one place they do is
called out at the list test.

Three properties are load-bearing and are each asserted more than one way,
because a build page is anonymous-writable by anyone who can reach it:

  * the edit token is returned exactly once and appears on no read path,
  * a wrong token cannot mutate anything,
  * nothing the client sends reaches the database unsanitised.

A browser is not the only thing that can POST here.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth import MOCK_ID_PREFIX, SESSION_COOKIE, serializer
from app.config import Settings
from app.main import create_app

# The four nodes and sixteen stats from SHD_NODES in types.ts, copied rather
# than invented. If the API and this list ever disagree, the UI renders a perk
# dialog full of undefined.
SHD_STATS = {
    "offense": ["weaponDamage", "headshotDamage", "criticalHitChance", "criticalHitDamage"],
    "defense": ["totalHealth", "totalArmor", "hazardProtection", "explosiveResistance"],
    "handling": ["accuracy", "stability", "ammoCapacity", "reloadSpeed"],
    "utility": ["skillRepair", "skillDamage", "skillDuration", "skillHaste"],
}

SLOT_KEYS = [
    "Mask", "Backpack", "Chest", "Gloves", "Holster", "Kneepads",
    "Primary", "Secondary", "SideArm", "Specialization", "Skill1", "Skill2",
]


@pytest.fixture
def db_file(tmp_path, monkeypatch) -> Path:
    """One database per test.

    Same reasoning as conftest's isolated_snapshot_dir, one level finer: these
    tests count rows and views, so one test's leftover build is the next one's
    wrong total.
    """
    path = tmp_path / "builds.db"
    monkeypatch.setenv("TD2_DB_PATH", str(path))
    monkeypatch.setenv("TD2_AUTH_MODE", "mock")
    monkeypatch.setenv("TD2_ALLOW_MOCK_AUTH", "true")
    monkeypatch.setenv("TD2_SESSION_SECRET", "test-secret")
    return path


@pytest.fixture
def client(db_file) -> TestClient:
    # base_url must be https. The session cookie is Secure and TestClient's
    # default http://testserver silently drops it, which would turn every
    # session-ownership test below into a test of the anonymous path.
    with TestClient(create_app(), base_url="https://testserver") as c:
        yield c


def _perks(**nodes) -> dict:
    """A full, well-formed ShdPerks with the named stats overridden."""
    perks = {node: {stat: 0 for stat in stats} for node, stats in SHD_STATS.items()}
    for node, levels in nodes.items():
        perks.setdefault(node, {}).update(levels)
    return perks


def _loadout(**slots) -> dict:
    loadout = {key: None for key in SLOT_KEYS}
    loadout.update(slots)
    return loadout


def _create(client: TestClient, **overrides) -> dict:
    body = {
        "name": "Chatterbox Status",
        "notes": "hold the doorway",
        "shdLevel": 2100,
        "shdPerks": _perks(offense={"weaponDamage": 50}),
        "loadout": _loadout(Primary="Chatterbox", Mask="Ceska Vyroba Mask"),
    }
    body.update(overrides)
    response = client.post("/api/builds", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _login(client: TestClient, name: str) -> None:
    client.get("/api/auth/login", params={"as": name}, follow_redirects=False)


# ------------------------------------------------------------- the round trip


def test_create_returns_the_four_keys_the_frontend_destructures(client):
    """build-creator.tsx reads data.id and data.edit_token off the POST, and
    nothing else tells it where the build went."""
    created = _create(client)
    assert set(created) == {"id", "edit_token", "url", "build"}
    assert created["url"] == f"/build/{created['id']}"
    assert created["build"]["id"] == created["id"]
    assert created["build"]["views"] == 0


def test_create_then_load_round_trip(client):
    created = _create(client)
    loaded = client.get(f"/api/builds/{created['id']}")
    assert loaded.status_code == 200
    build = loaded.json()
    assert set(build) == {
        "id", "name", "notes", "shdLevel", "shdPerks", "loadout",
        "views", "createdAt", "updatedAt",
    }
    assert build["name"] == "Chatterbox Status"
    assert build["notes"] == "hold the doorway"
    assert build["shdLevel"] == 2100
    assert build["shdPerks"]["offense"]["weaponDamage"] == 50
    assert build["loadout"]["Primary"] == "Chatterbox"
    assert build["loadout"]["Secondary"] is None


def test_a_loadout_stores_names_not_ids(client):
    """use-loadout-lookup.ts resolves every slot with `x.name === name`. An id
    here would render as an empty slot with no error anywhere."""
    created = _create(client, loadout=_loadout(Primary="Chatterbox"))
    assert created["build"]["loadout"]["Primary"] == "Chatterbox"


def test_a_build_outlives_the_process(db_file):
    """The whole point of the task. A dict would pass every other test here."""
    with TestClient(create_app(), base_url="https://testserver") as first:
        created = _create(first)
    with TestClient(create_app(), base_url="https://testserver") as second:
        assert second.get(f"/api/builds/{created['id']}").status_code == 200


def test_timestamps_are_the_format_javascript_parses(client):
    created = _create(client)["build"]
    for field in ("createdAt", "updatedAt"):
        assert created[field].endswith("Z"), created[field]
        assert "T" in created[field]


# -------------------------------------------------------------------- views


def test_views_increment_on_every_read(client):
    created = _create(client)
    seen = [client.get(f"/api/builds/{created['id']}").json()["views"] for _ in range(3)]
    assert seen == [1, 2, 3]


def test_listing_builds_is_not_a_view(client):
    """The compare page lists fifty builds on every render. Counting those as
    views would make the number meaningless within a day."""
    created = _create(client)
    client.get("/api/builds?limit=50")
    assert client.get(f"/api/builds/{created['id']}").json()["views"] == 1


# ---------------------------------------------------------------- ownership


def test_a_wrong_token_cannot_edit(client):
    created = _create(client)
    response = client.patch(
        f"/api/builds/{created['id']}",
        json={"edit_token": "not-the-token", "name": "Stolen"},
    )
    assert response.status_code == 403
    assert response.json() == {"error": "Invalid edit_token"}
    assert client.get(f"/api/builds/{created['id']}").json()["name"] == "Chatterbox Status"


def test_a_wrong_token_cannot_delete(client):
    created = _create(client)
    response = client.request(
        "DELETE", f"/api/builds/{created['id']}", json={"edit_token": "not-the-token"}
    )
    assert response.status_code == 403
    assert client.get(f"/api/builds/{created['id']}").status_code == 200


def test_the_right_token_can_edit_and_delete(client):
    created = _create(client)
    token = created["edit_token"]
    patched = client.patch(
        f"/api/builds/{created['id']}", json={"edit_token": token, "name": "Renamed"}
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "Renamed"

    deleted = client.request("DELETE", f"/api/builds/{created['id']}", json={"edit_token": token})
    assert deleted.status_code == 200
    assert deleted.json() == {"success": True}
    assert client.get(f"/api/builds/{created['id']}").status_code == 404


def test_a_missing_token_is_a_400_that_says_so(client):
    """`{"error": ...}`, not FastAPI's `{"detail": ...}` -- lib/fetcher.ts reads
    body.error, and a detail key surfaces as "Request failed: 400" with the
    reason hidden from the person who has to act on it."""
    created = _create(client)
    for response in (
        client.patch(f"/api/builds/{created['id']}", json={"name": "x"}),
        client.request("DELETE", f"/api/builds/{created['id']}", json={}),
        # A non-string token is a missing token: `typeof body.edit_token !==
        # 'string'` is the mock's own check.
        client.patch(f"/api/builds/{created['id']}", json={"edit_token": 12345}),
    ):
        assert response.status_code == 400, response.text
        assert response.json() == {"error": "edit_token is required"}
        assert "detail" not in response.json()


def test_an_unknown_id_is_404_on_every_verb(client):
    body = {"edit_token": "anything"}
    for response in (
        client.get("/api/builds/does-not-exist"),
        client.patch("/api/builds/does-not-exist", json=body),
        client.request("DELETE", "/api/builds/does-not-exist", json=body),
    ):
        assert response.status_code == 404, response.text
        assert response.json() == {"error": "Build not found"}


def test_the_session_owner_can_edit_without_a_usable_token(client):
    """The second ownership path, and the reason there are two.

    The frontend keeps edit tokens in localStorage. Clearing site data, or
    opening the build on a second device, loses them -- and once Discord login
    lands, the person who made the build is still the person who made it.
    """
    _login(client, "spifuth")
    created = _create(client)
    response = client.patch(
        f"/api/builds/{created['id']}", json={"edit_token": "lost-it", "name": "Still Mine"}
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Still Mine"


def test_another_session_is_not_the_owner(client):
    """The path only works for the person who made it, or it is not ownership."""
    _login(client, "spifuth")
    created = _create(client)
    _login(client, "someoneelse")
    response = client.patch(
        f"/api/builds/{created['id']}", json={"edit_token": "guessing", "name": "Stolen"}
    )
    assert response.status_code == 403
    assert client.get(f"/api/builds/{created['id']}").json()["name"] == "Chatterbox Status"


def test_an_anonymous_build_is_not_owned_by_the_next_person_to_log_in(client):
    """author_id is NULL on an anonymous build. A NULL that compares equal to a
    logged-out session -- or to another NULL -- would hand every anonymous
    build to whoever asked first."""
    created = _create(client)
    _login(client, "opportunist")
    response = client.patch(
        f"/api/builds/{created['id']}", json={"edit_token": "nope", "name": "Stolen"}
    )
    assert response.status_code == 403


def test_a_session_with_no_id_owns_nothing():
    """The guard that makes the test above hold, exercised directly.

    An anonymous build has author_id NULL, and a session with no usable id
    stringifies to "". Compare those two loosely and "" == "" is True: one
    malformed session would own every anonymous build in the table. read_session
    will not produce such a session today -- it requires three non-empty
    strings -- but _authorised is a plain function and read_session is not the
    only thing that can reach it. Written after a mutation run showed the
    endpoint tests could not tell this guard from its absence.
    """
    from app import db

    stored = db.hash_token("the-real-token")
    assert db._authorised(stored, None, None, {"name": "nobody"}) is False
    assert db._authorised(stored, "", None, {"id": "", "name": "nobody"}) is False
    # ... and the control: the same call with a real pair does match.
    assert db._authorised(stored, "mock:tester", None, {"id": "mock:tester"}) is True


def test_the_token_still_works_for_the_owner_after_they_log_in(client):
    """The frontend has no login today and will have one tomorrow. Adding the
    session path must not take the token path away."""
    created = _create(client)
    _login(client, "spifuth")
    response = client.patch(
        f"/api/builds/{created['id']}",
        json={"edit_token": created["edit_token"], "name": "Adopted"},
    )
    assert response.status_code == 200


def test_author_id_is_stored_as_text(db_file, client):
    """Discord snowflakes are past JavaScript's 2**53-1 safe integer. Heolstor
    stored them as INTEGER in one half and TEXT in the other and needed a
    coercion shim to stop the two disagreeing about who owned a build."""
    snowflake = "164936124170752000"
    session = serializer(Settings(session_secret="test-secret")).dumps(
        {"id": snowflake, "name": "spifuth", "mode": "mock"}
    )
    response = client.post(
        "/api/builds",
        json={"name": "Owned", "notes": "", "shdLevel": 1, "shdPerks": {}, "loadout": {}},
        headers={"Cookie": f"{SESSION_COOKIE}={session}"},
    )
    assert response.status_code == 201
    with sqlite3.connect(db_file) as conn:
        row = conn.execute(
            "SELECT author_id, typeof(author_id) FROM builds WHERE id = ?",
            (response.json()["id"],),
        ).fetchone()
    assert row == (snowflake, "text")
    assert int(row[0]) > 2**53 - 1


def test_a_mock_owned_build_is_findable_in_one_query(db_file, client):
    """The cutover query. Every row a fake identity wrote must be findable
    without guessing when real Discord lands."""
    _login(client, "tester")
    _create(client)
    with sqlite3.connect(db_file) as conn:
        rows = conn.execute(
            "SELECT count(*) FROM builds WHERE author_id LIKE ?", (f"{MOCK_ID_PREFIX}%",)
        ).fetchone()
    assert rows[0] == 1


# -------------------------------------------------------- the token is a secret


def _every_key(node) -> set[str]:
    """Every key anywhere in a JSON document, however deeply nested."""
    if isinstance(node, dict):
        return set(node) | {k for v in node.values() for k in _every_key(v)}
    if isinstance(node, list):
        return {k for item in node for k in _every_key(item)}
    return set()


def test_the_edit_token_appears_on_no_read_path(client):
    """Returned exactly once, at creation. A credential that leaks on read is
    not a credential -- and every build id is public by design.

    Checked two ways: the token's own value must appear in no response text,
    and no response may carry a field called edit_token at any depth. The
    second is what catches a token handed back under a nested key; the string
    check alone cannot, because "Invalid edit_token" is the mock's own 403
    message and contains the word.
    """
    created = _create(client)
    token = created["edit_token"]

    reads = [
        client.get(f"/api/builds/{created['id']}"),
        client.get("/api/builds?limit=50"),
        client.get("/api/builds/does-not-exist"),
        client.patch(f"/api/builds/{created['id']}", json={"edit_token": "wrong"}),
        client.patch(f"/api/builds/{created['id']}", json={}),
        client.request("DELETE", "/api/builds/does-not-exist", json={"edit_token": "wrong"}),
    ]
    for response in reads:
        assert token not in response.text, response.text
        leaked = _every_key(response.json()) & {
            "edit_token", "editToken", "edit_token_hash", "author_id", "author_name",
        }
        assert not leaked, f"{leaked} reached a read path: {response.text}"


def test_the_leak_check_is_looking_at_something(client):
    """The control for the test above. Every one of its assertions is an
    absence, and an empty response body satisfies all of them -- so this one
    proves the same helper finds the key when it IS there."""
    created = _create(client)
    assert "edit_token" in _every_key(created)
    assert _every_key(created) >= {"id", "url", "build", "loadout", "shdPerks"}


def test_a_patch_response_does_not_hand_the_token_back(client):
    created = _create(client)
    response = client.patch(
        f"/api/builds/{created['id']}",
        json={"edit_token": created["edit_token"], "name": "Renamed"},
    )
    assert created["edit_token"] not in response.text
    assert "edit_token" not in response.json()


def test_the_database_stores_a_hash_not_the_token(db_file, client):
    """Same reasoning as a password. Anyone who reads the volume -- a backup, a
    stray `docker cp`, the restic repo -- would otherwise hold edit rights over
    every build in it."""
    created = _create(client)
    token = created["edit_token"]

    with sqlite3.connect(db_file) as conn:
        stored = conn.execute(
            "SELECT edit_token_hash FROM builds WHERE id = ?", (created["id"],)
        ).fetchone()[0]
    assert stored != token
    assert token not in stored

    # Not just the row: WAL means the plaintext could still be sitting in a
    # sidecar file that the SELECT above cannot see.
    for path in db_file.parent.iterdir():
        assert token.encode() not in path.read_bytes(), f"{path.name} holds the token"


def test_the_hash_column_is_not_in_the_public_column_list(client):
    """Structural, not behavioural. The read paths select a fixed list of
    columns that does not contain the hash, so a future field added to the
    Build shape cannot pull it along by accident."""
    from app import db

    assert "edit_token_hash" not in db.BUILD_PUBLIC_COLUMNS
    assert "edit_token_hash" in db.BUILD_COLUMNS


# ---------------------------------------------------------------- sanitising


def test_a_perk_level_of_999_is_clamped(client):
    created = _create(client, shdPerks=_perks(offense={"weaponDamage": 999}))
    assert created["build"]["shdPerks"]["offense"]["weaponDamage"] == 50


def test_a_negative_perk_level_becomes_zero(client):
    created = _create(client, shdPerks=_perks(defense={"totalArmor": -5}))
    assert created["build"]["shdPerks"]["defense"]["totalArmor"] == 0


def test_an_unknown_perk_node_is_dropped(client):
    perks = _perks()
    perks["wallhack"] = {"seeThroughWalls": 50}
    created = _create(client, shdPerks=perks)
    assert set(created["build"]["shdPerks"]) == set(SHD_STATS)


def test_an_unknown_perk_stat_is_dropped(client):
    perks = _perks()
    perks["offense"]["aimbot"] = 50
    created = _create(client, shdPerks=perks)
    assert set(created["build"]["shdPerks"]["offense"]) == set(SHD_STATS["offense"])


def test_missing_perks_default_to_zero(client):
    """createDefaultShdPerks()'s shape. The perk dialog indexes every stat key
    directly; a missing one renders NaN."""
    created = _create(client, shdPerks={})
    perks = created["build"]["shdPerks"]
    for node, stats in SHD_STATS.items():
        assert perks[node] == {stat: 0 for stat in stats}


def test_a_perk_level_that_is_not_a_number_becomes_zero(client):
    created = _create(
        client, shdPerks=_perks(utility={"skillHaste": "50; DROP TABLE builds"})
    )
    assert created["build"]["shdPerks"]["utility"]["skillHaste"] == 0
    assert client.get("/api/builds?limit=50").json()["total"] == 1


def test_a_fractional_perk_level_is_rounded(client):
    created = _create(client, shdPerks=_perks(handling={"accuracy": 12.5}))
    assert created["build"]["shdPerks"]["handling"]["accuracy"] == 13


def test_a_bogus_loadout_slot_is_dropped(client):
    created = _create(client, loadout={"Primary": "Chatterbox", "Wallhack": "yes"})
    assert set(created["build"]["loadout"]) == set(SLOT_KEYS)


def test_a_non_string_loadout_value_becomes_null(client):
    created = _create(client, loadout={"Primary": {"$ne": None}, "Chest": 7, "Gloves": True})
    loadout = created["build"]["loadout"]
    assert loadout["Primary"] is None
    assert loadout["Chest"] is None
    assert loadout["Gloves"] is None


def test_the_client_cannot_choose_its_own_id_views_or_author(client):
    """Mass assignment. Everything the server owns is server-owned."""
    created = _create(
        client,
        id="chosen-by-me",
        views=9999,
        createdAt="1999-01-01T00:00:00.000Z",
        author_id="164936124170752000",
        edit_token_hash="deadbeef",
    )
    assert created["id"] != "chosen-by-me"
    assert created["build"]["views"] == 0
    assert not created["build"]["createdAt"].startswith("1999")


def test_a_blank_name_becomes_unnamed_build(client):
    assert _create(client, name="   ")["build"]["name"] == "Unnamed Build"
    assert _create(client, name=None)["build"]["name"] == "Unnamed Build"


def test_absurd_text_is_bounded_before_it_reaches_the_disk(client):
    """/api/builds is anonymous-writable by anyone who can reach the service.
    An unbounded TEXT column behind that is a disk-filling primitive."""
    created = _create(client, name="A" * 10_000, notes="B" * 500_000,
                      loadout=_loadout(Primary="C" * 10_000))
    build = created["build"]
    assert len(build["name"]) < 1_000
    assert len(build["notes"]) < 100_000
    assert len(build["loadout"]["Primary"]) < 1_000


def test_an_absurd_shd_level_is_bounded(client):
    assert _create(client, shdLevel=-5)["build"]["shdLevel"] == 0
    assert _create(client, shdLevel=10**12)["build"]["shdLevel"] < 10**12
    assert _create(client, shdLevel="not a number")["build"]["shdLevel"] == 1


def test_malformed_json_is_a_400_with_an_error_key(client):
    response = client.post(
        "/api/builds", content=b"{not json", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400
    assert response.json() == {"error": "Invalid JSON body"}


def test_a_json_body_that_is_not_an_object_is_refused(client):
    for junk in ("[]", '"a string"', "null", "12"):
        response = client.post(
            "/api/builds", content=junk.encode(), headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 400, junk


# --------------------------------------------------------------------- patch


def test_a_patch_changes_only_the_fields_it_names(client):
    created = _create(client)
    response = client.patch(
        f"/api/builds/{created['id']}",
        json={"edit_token": created["edit_token"], "notes": "swapped to CQB"},
    )
    build = response.json()
    assert build["notes"] == "swapped to CQB"
    assert build["name"] == "Chatterbox Status"
    assert build["loadout"]["Primary"] == "Chatterbox"
    assert build["shdPerks"]["offense"]["weaponDamage"] == 50


def test_a_patch_sanitises_too(client):
    """The mutation path is a second front door, and it is the one an attacker
    reaches with a token they were handed legitimately."""
    created = _create(client)
    response = client.patch(
        f"/api/builds/{created['id']}",
        json={
            "edit_token": created["edit_token"],
            "shdPerks": {"offense": {"weaponDamage": 999}, "wallhack": {"x": 1}},
            "loadout": {"Primary": 7, "Wallhack": "yes"},
        },
    )
    build = response.json()
    assert build["shdPerks"]["offense"]["weaponDamage"] == 50
    assert set(build["shdPerks"]) == set(SHD_STATS)
    assert build["loadout"]["Primary"] is None
    assert set(build["loadout"]) == set(SLOT_KEYS)


def test_a_patch_moves_updated_at_and_leaves_created_at(client):
    created = _create(client)
    build = client.patch(
        f"/api/builds/{created['id']}",
        json={"edit_token": created["edit_token"], "name": "Renamed"},
    ).json()
    assert build["createdAt"] == created["build"]["createdAt"]
    assert build["updatedAt"] >= created["build"]["updatedAt"]


def test_a_patch_does_not_reset_the_view_count(client):
    created = _create(client)
    client.get(f"/api/builds/{created['id']}")
    build = client.patch(
        f"/api/builds/{created['id']}",
        json={"edit_token": created["edit_token"], "name": "Renamed"},
    ).json()
    assert build["views"] == 1


def test_a_delete_really_deletes(db_file, client):
    created = _create(client)
    client.request("DELETE", f"/api/builds/{created['id']}",
                   json={"edit_token": created["edit_token"]})
    with sqlite3.connect(db_file) as conn:
        assert conn.execute("SELECT count(*) FROM builds").fetchone()[0] == 0


# ---------------------------------------------------------------------- list


def test_the_list_envelope_has_all_four_keys(client):
    """builds-compare.tsx declares the response as ListResponse<Build>, which
    reads all four. The frontend's own mock returns a bare {results} -- the
    mock is the thing that disagrees with the frontend's type, so the type is
    what this matches."""
    for _ in range(3):
        _create(client)
    body = client.get("/api/builds?limit=2").json()
    assert set(body) == {"total", "limit", "offset", "results"}
    assert body["total"] == 3
    assert body["limit"] == 2
    assert body["offset"] == 0
    assert len(body["results"]) == 2


def test_the_list_is_newest_first(client):
    names = ["first", "second", "third"]
    for name in names:
        _create(client, name=name)
    listed = [b["name"] for b in client.get("/api/builds?limit=50").json()["results"]]
    assert listed == list(reversed(names))


def test_the_list_holds_whole_builds(client):
    """The compare page renders loadout slots and perk totals straight off the
    list; a summary row would render an empty grid."""
    _create(client)
    build = client.get("/api/builds?limit=50").json()["results"][0]
    assert build["loadout"]["Primary"] == "Chatterbox"
    assert build["shdPerks"]["offense"]["weaponDamage"] == 50


def test_the_list_pages(client):
    for index in range(5):
        _create(client, name=f"build-{index}")
    page = client.get("/api/builds?limit=2&offset=2").json()
    assert page["total"] == 5
    assert page["offset"] == 2
    assert [b["name"] for b in page["results"]] == ["build-2", "build-1"]


def test_an_empty_list_is_still_the_envelope(client):
    body = client.get("/api/builds").json()
    assert body == {"total": 0, "limit": body["limit"], "offset": 0, "results": []}
