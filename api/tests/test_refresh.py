import csv
import io
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.loader import load_dataset
from app.main import create_app
from app.refresh import run_refresh, validate_candidate, version_rejection
from app.snapshots import SnapshotStore

SEED_VERSION = "26.0-mdb"  # what seed_dir's SNAPSHOT.txt carries


def _good(seed_dir) -> dict[str, str]:
    return {p.stem: p.read_text(encoding="utf-8") for p in seed_dir.glob("*.csv")}


def _truncate(text: str, rows: int) -> str:
    """The header plus the first `rows` data rows, re-serialised through csv so
    a quoted field containing a newline cannot make the row count wrong."""
    reader = csv.reader(io.StringIO(text))
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(next(reader))
    for row in list(reader)[:rows]:
        writer.writerow(row)
    return out.getvalue()


def _rows(text: str) -> int:
    return len(list(csv.DictReader(io.StringIO(text))))


def _without_column(text: str, column: str) -> str:
    """The table minus one column, the way a game update would drop one."""
    reader = csv.DictReader(io.StringIO(text))
    fields = [f for f in reader.fieldnames if f != column]
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(reader)
    return out.getvalue()


def test_a_good_fetch_validates(seed_dir):
    assert validate_candidate(_good(seed_dir), previous_counts=None) == []


def test_html_served_as_200_is_rejected(seed_dir):
    """Upstream answering an error page with status 200 is the exact failure
    check-data-sources.mjs was rewritten to catch. It must not reach the live
    snapshot.

    This test does NOT pin the markup branch, despite reading as though it
    does: the one-line sample below parses to zero data rows, so it is the
    "parsed to zero rows" branch that rejects it and the test stays green with
    the markup check deleted. The test that actually pins the markup branch is
    test_a_realistic_spa_fallback_is_rejected_as_markup_not_by_accident below;
    this one is kept for the shape of the input, not for the branch it lands
    on."""
    tables = _good(seed_dir)
    tables["weapon"] = "<!DOCTYPE html><html><body>502 Bad Gateway</body></html>"
    reasons = validate_candidate(tables, previous_counts=None)
    assert any("weapon" in r for r in reasons)


def test_a_missing_table_is_rejected(seed_dir):
    tables = _good(seed_dir)
    del tables["brands"]
    assert any("brands" in r for r in validate_candidate(tables, previous_counts=None))


def test_a_dropped_column_is_rejected(seed_dir):
    tables = _good(seed_dir)
    tables["weapon"] = "Name,Quality\nFoo,High End\n"
    assert any("weapon" in r for r in validate_candidate(tables, previous_counts=None))


def test_a_collapsed_row_count_is_rejected(seed_dir):
    """A table going from 300 rows to 2 is a bad fetch, not a game update.
    Without this, a truncated response silently replaces good data."""
    tables = _good(seed_dir)
    header = tables["weapon"].splitlines()[0]
    body = tables["weapon"].splitlines()[1]
    tables["weapon"] = f"{header}\n{body}\n"
    reasons = validate_candidate(tables, previous_counts={"weapon": 300})
    assert any("row count" in r for r in reasons)


def test_an_empty_fetch_is_rejected(seed_dir):
    assert validate_candidate({}, previous_counts=None) != []


def test_a_realistic_spa_fallback_is_rejected_as_markup_not_by_accident(seed_dir):
    """test_html_served_as_200_is_rejected above uses a one-line HTML sample,
    which csv.DictReader parses to zero data rows -- so that test would still
    pass even with the markup guard deleted, because "parsed to zero rows"
    catches it instead. Proven by mutation: commenting out the markup branch
    left all 6 tests green.

    A real SPA fallback (what Vite actually serves for an unmatched path, per
    check-data-sources.mjs) is multi-line, and csv.DictReader turns each line
    into a one-column pseudo-row -- 10 pseudo-rows for the 11-line document
    below. weaponAttributes has 17 real rows and no entry in
    loader.REQUIRED_COLUMNS, so it has no column safety net, and 10 clears the
    MIN_ROW_RATIO floor of 8.5. Without the markup guard specifically, this
    candidate would be silently accepted -- verified by re-running this test
    against that same mutation, where it fails.
    """
    tables = _good(seed_dir)
    tables["weaponAttributes"] = (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="UTF-8" />\n'
        "<title>mx-division-builds</title>\n"
        "</head>\n"
        "<body>\n"
        '<div id="root"></div>\n'
        '<script type="module" src="/src/main.tsx"></script>\n'
        "</body>\n"
        "</html>\n"
    )
    reasons = validate_candidate(tables, previous_counts={"weaponAttributes": 17})
    assert any("weaponAttributes" in r for r in reasons)


def test_a_good_fetch_validates_against_the_live_dataset(seed_dir):
    """The contrast case for the two checks that compare against what is
    already being served. Without it, a header or row-count rule strict enough
    to reject the real upstream's own data would still look correct here."""
    live = load_dataset(seed_dir)
    reasons = validate_candidate(
        _good(seed_dir), previous_counts=live.counts, previous_headers=live.headers
    )
    assert reasons == []


def test_a_header_only_table_is_rejected(seed_dir):
    """The "parsed to zero rows" branch, which shipped with nothing reaching
    it -- the markup tests stop at the markup branch, the missing-table test at
    the missing branch, and the column and row-count tests both arrive with a
    row in hand. Deleting the branch left all 11 tests green.

    It is not decoration. Every check after it indexes rows[0], so without it a
    header-only body raises IndexError inside run_refresh, which has no guard:
    upstream serving a bare header becomes an HTTP 500 rather than a rejection.
    weapon is used deliberately -- it has a REQUIRED_COLUMNS entry, so the
    IndexError is reachable rather than skipped by an empty comprehension.
    """
    tables = _good(seed_dir)
    tables["weapon"] = tables["weapon"].splitlines()[0] + "\n"
    assert _rows(tables["weapon"]) == 0

    reasons = validate_candidate(tables, previous_counts=None)

    assert reasons == ["weapon: parsed to zero rows"]


def test_another_tables_body_is_rejected_where_there_are_no_required_columns(seed_dir):
    """13 of the 20 tables have no REQUIRED_COLUMNS entry, so for them the
    validator reduced to "present, not markup, non-empty, not collapsed" --
    nothing that says the body is *this table*.

    gearMods is the measured case: 16 rows, so a MIN_ROW_RATIO floor of 8.
    Upstream answering it with a different collection's CSV -- one path rename
    away -- is not markup, parses fine, and 9 rows clears the floor. Only the
    header tells the two apart, and skillMods shares exactly one column name
    with gearMods out of four.
    """
    live = load_dataset(seed_dir)
    tables = _good(seed_dir)
    tables["gearMods"] = _truncate(tables["skillMods"], 9)
    assert _rows(tables["gearMods"]) == 9 > live.counts["gearMods"] * 0.5

    reasons = validate_candidate(
        tables, previous_counts=live.counts, previous_headers=live.headers
    )

    assert len(reasons) == 1
    assert "gearMods" in reasons[0] and "header" in reasons[0]


def test_a_json_error_document_is_rejected(seed_dir):
    """sources/buildstation.py documents this very upstream answering
    {"message":"Unknown collection"} for a path it does not know. JSON is not
    markup, so the "<" guard does not apply to it, and a pretty-printed error
    long enough to clear the row floor passes everything except the header."""
    live = load_dataset(seed_dir)
    tables = _good(seed_dir)
    tables["gearMods"] = (
        "{\n"
        '  "statusCode": 404,\n'
        '  "error": "Not Found",\n'
        '  "message": "Unknown collection",\n'
        '  "collection": "gearMods",\n'
        '  "requestId": "9f3c1a",\n'
        '  "timestamp": "2026-08-23T00:00:00Z",\n'
        '  "path": "/api/td2/v2/data/mx/gearMods",\n'
        '  "docs": "https://buildstation.app",\n'
        '  "hint": "check the collection name",\n'
        "}\n"
    )
    assert _rows(tables["gearMods"]) >= live.counts["gearMods"] * 0.5

    reasons = validate_candidate(
        tables, previous_counts=live.counts, previous_headers=live.headers
    )

    assert len(reasons) == 1
    assert "gearMods" in reasons[0]


def test_a_body_that_is_exactly_another_tables_is_rejected(seed_dir):
    """The overlap floor is not enough on its own. mask fed chest's body keeps
    11 of mask's 12 columns and brings 92 rows against a floor of 38, and every
    column REQUIRED_COLUMNS asks of mask is a column chest has too -- so it
    clears every other check in this function.

    Measured over the seed dataset: 68 of the 380 possible table-for-table
    swaps cleared the overlap floor. Rejecting a header that is exactly some
    other live table's decides 61 of them. The 7 that remain are pairs whose
    live schemas are identical (chest/gloves/kneepads, gearAttributes/
    gearMods), which no check on header shape can separate.
    """
    live = load_dataset(seed_dir)
    tables = _good(seed_dir)
    tables["mask"] = tables["chest"]

    reasons = validate_candidate(
        tables, previous_counts=live.counts, previous_headers=live.headers
    )

    assert len(reasons) == 1
    assert reasons[0].startswith("mask: ")
    # chest, gloves and kneepads share one schema, so any of the three names it.
    assert any(twin in reasons[0] for twin in ("chest", "gloves", "kneepads"))


def test_a_dropped_column_still_validates_when_most_of_the_header_survives(seed_dir):
    """The header check must not be a stricter REQUIRED_COLUMNS. A game update
    that drops one column of statsMapping's two is the tightest legitimate case
    in the dataset, and it has to pass -- a validator that rejects real updates
    stops the data ever moving, which is its own failure."""
    live = load_dataset(seed_dir)
    tables = _good(seed_dir)
    assert set(live.headers["statsMapping"]) == {"Type", "Stat"}
    tables["statsMapping"] = _without_column(tables["statsMapping"], "Stat")

    reasons = validate_candidate(
        tables, previous_counts=live.counts, previous_headers=live.headers
    )

    assert reasons == []


def test_the_row_count_floor_sits_exactly_at_half(seed_dir):
    """MIN_ROW_RATIO's value was unpinned: anything in roughly (0.004, 1.0]
    left every test green. Both asserts below are needed -- the first fails if
    the ratio rises above 0.5, the second if it falls below -- and together
    they also pin the `<` semantics, which accept exactly half.
    """
    tables = _good(seed_dir)
    tables["weaponAttributes"] = _truncate(tables["weaponAttributes"], 8)
    assert _rows(tables["weaponAttributes"]) == 8

    at_the_floor = validate_candidate(tables, previous_counts={"weaponAttributes": 16})
    assert at_the_floor == [], "exactly half the previous rows is accepted"

    below = validate_candidate(tables, previous_counts={"weaponAttributes": 17})
    assert any("row count" in r for r in below), "one row under the floor is rejected"


@pytest.mark.parametrize(
    "token",
    [
        "",
        "   ",
        "\n\n",
        "26.0-mdb\nsource: https://not-buildstation.example/mx",
        "26.0 mdb",
        "<!DOCTYPE html>",
        "x" * 65,
    ],
)
def test_a_malformed_version_token_is_rejected(token):
    """The token got no validation at all while the tables got five, even
    though it is the value that decides whether a refresh happens and the one
    interpolated into SNAPSHOT.txt. raise_for_status() does not cover a 200
    with a wrong body -- the same threat model this whole module exists for."""
    assert version_rejection(token.strip()) is not None


def test_the_real_version_token_is_accepted():
    """The contrast case: the shape check has to let the actual upstream value
    through, or the refresh never runs again."""
    assert version_rejection(SEED_VERSION) is None
    assert version_rejection("27.1") is None
    assert version_rejection("2026.08.23-rc1+build2") is None


# --- run_refresh: proof the live dataset survives every failure path -------
#
# The tests above pin down validate_candidate as a pure function. The ones
# below pin down run_refresh itself: that a rejected or failed refresh cannot
# swap app.state.dataset out from under an in-flight request. Fakes stand in
# for BuildstationSource, matching the DataSource protocol, so this stays
# offline -- what's being proven is run_refresh's own control flow, not
# whether the network happens to be up.

NEW_VERSION = "27.0-mdb"  # seed_dir's SNAPSHOT.txt carries 26.0-mdb


class _VersionPollFails:
    async def fetch_version(self) -> str:
        raise RuntimeError("upstream unreachable")

    async def fetch_tables(self) -> dict[str, str]:
        raise AssertionError("fetch_tables must not run when the version poll fails")


class _FetchFails:
    def __init__(self, version: str = NEW_VERSION) -> None:
        self._version = version

    async def fetch_version(self) -> str:
        return self._version

    async def fetch_tables(self) -> dict[str, str]:
        raise RuntimeError("connection reset")


class _VersionOnly:
    """Answers the cheap poll and asserts the expensive one never happens."""

    def __init__(self, version: str) -> None:
        self._version = version

    async def fetch_version(self) -> str:
        return self._version

    async def fetch_tables(self) -> dict[str, str]:
        raise AssertionError("a rejected version token must not trigger a 20-table fetch")


class _StoreWriteFails:
    """A SnapshotStore that fails the way a full disk or a bad mount does.

    The message carries a filesystem path on purpose: /api/meta serves
    `reasons` to the browser origins CORS opens to.
    """

    def write_candidate(self, tables, version, date):
        raise OSError(28, "No space left on device", "/srv/snapshots/2026-08-23")

    def promote(self, snapshot) -> None:
        raise AssertionError("nothing may be promoted after the write failed")

    def live(self):
        return None


class _FetchesTables:
    def __init__(self, tables: dict[str, str], version: str = NEW_VERSION) -> None:
        self._tables = tables
        self._version = version

    async def fetch_version(self) -> str:
        return self._version

    async def fetch_tables(self) -> dict[str, str]:
        return self._tables


def _app(tmp_path, seed_dir, source) -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(
            dataset=load_dataset(seed_dir),
            snapshots=SnapshotStore(tmp_path),
            source=source,
        )
    )


async def test_a_failed_version_poll_leaves_the_live_dataset_untouched(tmp_path, seed_dir):
    app = _app(tmp_path, seed_dir, _VersionPollFails())
    before = app.state.dataset
    result = await run_refresh(app)
    assert app.state.dataset is before, "a version-poll failure must not touch app.state.dataset"
    assert result.changed is False
    assert result.version is None


async def test_a_failed_fetch_leaves_the_live_dataset_untouched(tmp_path, seed_dir):
    app = _app(tmp_path, seed_dir, _FetchFails())
    before = app.state.dataset
    result = await run_refresh(app)
    assert app.state.dataset is before, "a fetch failure must not touch app.state.dataset"
    assert result.changed is False
    assert result.version == NEW_VERSION


async def test_a_rejected_candidate_leaves_the_live_dataset_untouched(tmp_path, seed_dir):
    tables = _good(seed_dir)
    del tables["brands"]
    app = _app(tmp_path, seed_dir, _FetchesTables(tables))
    before = app.state.dataset

    result = await run_refresh(app)

    assert app.state.dataset is before, "a rejected candidate must not touch app.state.dataset"
    assert result.changed is False
    assert any("brands" in r for r in result.reasons)
    assert app.state.snapshots.live() is None, "a rejected candidate must not be promoted either"


async def test_a_validated_candidate_does_replace_the_live_dataset(tmp_path, seed_dir):
    """The contrast case. Without it, the three tests above would still pass
    if run_refresh never swapped app.state.dataset under any circumstance --
    "untouched on every failure path" would then be true for the wrong
    reason: not because failures are handled safely, but because nothing
    exercises the success path at all."""
    tables = _good(seed_dir)
    app = _app(tmp_path, seed_dir, _FetchesTables(tables))
    before = app.state.dataset

    result = await run_refresh(app)

    assert app.state.dataset is not before
    assert result.changed is True
    assert app.state.dataset.version == NEW_VERSION
    assert app.state.snapshots.live() is not None


# --- force: the one caller-controllable input to the whole feature ---------


async def test_force_refetches_a_version_that_has_not_moved(tmp_path, seed_dir):
    """What force is for. Without this the two tests below could both pass with
    force wired to nothing at all."""
    app = _app(tmp_path, seed_dir, _FetchesTables(_good(seed_dir), version=SEED_VERSION))

    unforced = await run_refresh(app)
    assert unforced.changed is False, "an unmoved version is a no-op without force"

    forced = await run_refresh(app, force=True)
    assert forced.changed is True


async def test_force_does_not_bypass_validation(tmp_path, seed_dir):
    """force=true is unauthenticated and reachable by anyone who can reach the
    service. It may buy a re-fetch of an unmoved version and nothing else: a
    forced refresh that skipped the validation gate would be a way to ask the
    API to replace good data with whatever upstream is serving right now."""
    tables = _good(seed_dir)
    tables["gearMods"] = _truncate(tables["skillMods"], 9)
    app = _app(tmp_path, seed_dir, _FetchesTables(tables, version=SEED_VERSION))
    before = app.state.dataset

    result = await run_refresh(app, force=True)

    assert result.changed is False
    assert app.state.dataset is before, "a forced refresh must not promote a bad candidate"
    assert app.state.snapshots.live() is None
    assert any("gearMods" in r for r in result.reasons)


def test_force_is_wired_through_the_admin_route(tmp_path, seed_dir, monkeypatch):
    """grep -rn force api/tests/ returned nothing before this. The query
    parameter could have been dropped from the call in admin.py and every test
    would have stayed green."""
    monkeypatch.setenv("TD2_SNAPSHOT_DIR", str(tmp_path))
    app = create_app()
    with TestClient(app) as client:
        # Swapped in after startup, so the real BuildstationSource is
        # constructed but never called: the suite stays offline.
        app.state.source = _FetchesTables(_good(seed_dir), version=SEED_VERSION)
        unforced = client.post("/api/admin/refresh").json()
        forced = client.post("/api/admin/refresh?force=true").json()

    assert unforced == {
        "changed": False,
        "version": SEED_VERSION,
        "snapshot": None,
        "reasons": [],
    }
    assert forced["changed"] is True
    assert forced["snapshot"]


# --- the version token, which decides whether any of the above runs --------


async def test_a_rejected_version_token_stops_before_the_expensive_fetch(tmp_path, seed_dir):
    """A multi-line token is interpolated straight into SNAPSHOT.txt, and
    _read_marker honours an injected "source:" line -- so the marker of a
    promoted snapshot would name an upstream nobody chose."""
    injected = "26.0-mdb\nsource: https://not-buildstation.example/mx"
    app = _app(tmp_path, seed_dir, _VersionOnly(injected))
    before = app.state.dataset

    result = await run_refresh(app)

    assert result.changed is False
    assert result.version is None, "an untrusted token must not be echoed back either"
    assert result.reasons
    assert app.state.dataset is before
    assert app.state.snapshots.list() == [], "nothing may be written for a rejected token"


async def test_an_empty_version_token_does_not_promote_on_every_poll(tmp_path, seed_dir):
    """The runaway. "" never equals the live version, so every poll would do a
    full fetch and promote; _read_marker then discards the empty value, so the
    promoted snapshot reads back as "unknown" and the next poll compares "" to
    "unknown" -- forever, with write_candidate's collision suffix minting .2,
    .3, .4 and nothing pruning them."""
    app = _app(tmp_path, seed_dir, _VersionOnly("   "))

    for _ in range(3):
        result = await run_refresh(app)
        assert result.changed is False

    assert app.state.snapshots.list() == []


async def test_a_programming_error_is_not_reported_as_upstream_being_down(tmp_path, seed_dir):
    """`except Exception` around the source calls laundered a local bug into
    "fetch failed: ...", pointing whoever reads /api/meta at a third party."""

    class _Buggy:
        async def fetch_version(self) -> str:
            return NEW_VERSION

        async def fetch_tables(self) -> dict[str, str]:
            raise TypeError("one() takes 1 positional argument but 2 were given")

    app = _app(tmp_path, seed_dir, _Buggy())
    with pytest.raises(TypeError):
        await run_refresh(app)


# --- last_refresh: what /api/meta reports about the attempt just made ------


async def test_a_failed_promotion_is_recorded_as_this_attempt_not_the_last_success(
    tmp_path, seed_dir
):
    """finish() wrapped the five returned paths but not the write/promote/load
    block, so an exception there 500'd the route and left app.state.last_refresh
    advertising the previous success as though it were this attempt. Measured,
    not imagined: a PermissionError on the snapshot volume did exactly this.
    """
    app = _app(tmp_path, seed_dir, _FetchesTables(_good(seed_dir)))
    app.state.last_refresh = {"changed": True, "version": SEED_VERSION, "reasons": []}
    app.state.snapshots = _StoreWriteFails()
    before = app.state.dataset

    result = await run_refresh(app)

    assert result.changed is False
    assert app.state.dataset is before, "a failed write must not touch the live dataset"
    assert app.state.last_refresh["changed"] is False
    assert app.state.last_refresh["reasons"] == result.reasons
    assert any("promotion failed" in r for r in result.reasons)
    assert not any("/srv" in r for r in result.reasons), (
        "reasons reach browser origins CORS opens to -- no filesystem paths"
    )


async def test_last_refresh_follows_the_latest_attempt(tmp_path, seed_dir):
    """The field had no test at all: deleting finish() or /api/meta's
    passthrough left the suite green."""
    app = _app(tmp_path, seed_dir, _FetchesTables(_good(seed_dir)))

    await run_refresh(app)
    assert app.state.last_refresh == {"changed": True, "version": NEW_VERSION, "reasons": []}

    app.state.source = _FetchFails(version="28.0-mdb")
    await run_refresh(app)
    assert app.state.last_refresh["changed"] is False
    assert app.state.last_refresh["version"] == "28.0-mdb"
    assert any("fetch failed" in r for r in app.state.last_refresh["reasons"])


def test_meta_surfaces_the_last_refresh(tmp_path, seed_dir, monkeypatch):
    monkeypatch.setenv("TD2_SNAPSHOT_DIR", str(tmp_path))
    app = create_app()
    with TestClient(app) as client:
        assert client.get("/api/meta").json()["last_refresh"] is None

        app.state.source = _FetchesTables(_good(seed_dir), version=SEED_VERSION)
        client.post("/api/admin/refresh?force=true")
        body = client.get("/api/meta").json()

    assert body["last_refresh"] == {"changed": True, "version": SEED_VERSION, "reasons": []}


# --- a bad promoted snapshot must not take the service down ----------------


def test_a_broken_promoted_snapshot_falls_back_to_the_seed(tmp_path, seed_dir, monkeypatch):
    """load_dataset(live) ran unguarded inside lifespan, so a DatasetError
    aborted the boot -- and aborted it again on every restart, because LIVE
    still points at the same directory, while the known-good seed loaded one
    line earlier was thrown away. write_candidate is not atomic (mkdir, then 20
    writes), so the half-written directory below is reachable from a full disk
    or a kill.

    This is the inverse of the property the refresh exists for: it trades
    serving last-known-good for having no service at all.
    """
    broken = tmp_path / "2026-08-23"
    broken.mkdir()
    (broken / "weapon.csv").write_text("Name,Quality\n", encoding="utf-8")
    (tmp_path / "LIVE").write_text(broken.name, encoding="utf-8")
    monkeypatch.setenv("TD2_SNAPSHOT_DIR", str(tmp_path))

    with TestClient(create_app()) as client:
        meta = client.get("/api/meta").json()
        assert client.get("/api/health").json() == {"ok": True}
        assert client.get("/api/weapons").json()["total"] > 100

    assert meta["version"] == SEED_VERSION, "the seed must still be served"
    assert meta["table_count"] == 20
    assert meta["degraded"], "a silent fallback is a stale dashboard: /api/meta must say so"
    assert broken.name in meta["degraded"]


def test_a_healthy_promoted_snapshot_is_not_reported_as_degraded(tmp_path, seed_dir, monkeypatch):
    """The contrast case: `degraded` set unconditionally, or never cleared,
    would make the test above pass while crying wolf on every normal boot."""
    monkeypatch.setenv("TD2_SNAPSHOT_DIR", str(tmp_path))
    app = create_app()
    with TestClient(app) as client:
        app.state.source = _FetchesTables(_good(seed_dir), version=SEED_VERSION)
        assert client.post("/api/admin/refresh?force=true").json()["changed"] is True

    with TestClient(create_app()) as client:
        meta = client.get("/api/meta").json()

    assert meta["degraded"] is None
    assert meta["version"] == SEED_VERSION
    assert meta["table_count"] == 20
