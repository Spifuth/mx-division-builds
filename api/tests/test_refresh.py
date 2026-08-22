import csv
import io
from types import SimpleNamespace

import pytest

from app.loader import load_dataset
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
    async def fetch_version(self) -> str:
        return NEW_VERSION

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
