import csv
import io
from types import SimpleNamespace

import pytest

from app.loader import load_dataset
from app.refresh import run_refresh, validate_candidate
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
