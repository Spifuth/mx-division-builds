from app.snapshots import SnapshotStore


def test_a_candidate_is_not_live_until_promoted(tmp_path):
    store = SnapshotStore(tmp_path)
    assert store.live() is None
    candidate = store.write_candidate({"brands": "Brand,Type,Icon\na,b,c\n"}, "27.0-mdb", "2026-09-01")
    assert store.live() is None, "writing a candidate must not change what is served"
    store.promote(candidate)
    assert store.live() == candidate


def test_promotion_is_reversible(tmp_path):
    store = SnapshotStore(tmp_path)
    first = store.write_candidate({"brands": "Brand\na\n"}, "26.0-mdb", "2026-08-22")
    store.promote(first)
    second = store.write_candidate({"brands": "Brand\nb\n"}, "27.0-mdb", "2026-09-01")
    store.promote(second)
    assert store.live() == second
    store.promote(first)
    assert store.live() == first, "rolling back must not require re-fetching upstream"


def test_a_candidate_carries_its_own_snapshot_marker(tmp_path):
    store = SnapshotStore(tmp_path)
    candidate = store.write_candidate({"brands": "Brand\na\n"}, "27.0-mdb", "2026-09-01")
    marker = (candidate / "SNAPSHOT.txt").read_text()
    assert "27.0-mdb" in marker
    assert "2026-09-01" in marker


def test_rolling_back_restores_the_CONTENT_not_just_the_pointer(tmp_path):
    """The reason snapshots are kept is data, not pointer plumbing.

    The reversibility test above only compares Paths, so it cannot tell "rolled
    back correctly" from "pointer moved but the data behind it was clobbered" --
    and a rollback is wanted precisely when the upstream you would re-fetch from
    is the thing that broke.
    """
    store = SnapshotStore(tmp_path)
    first = store.write_candidate({"brands": "Brand\nold\n"}, "26.0-mdb", "2026-08-22")
    store.promote(first)
    second = store.write_candidate({"brands": "Brand\nnew\n"}, "27.0-mdb", "2026-09-01")
    store.promote(second)

    store.promote(first)
    assert (store.live() / "brands.csv").read_text() == "Brand\nold\n"
    assert (second / "brands.csv").read_text() == "Brand\nnew\n", "the other snapshot must survive too"


def test_live_returns_none_when_the_promoted_snapshot_is_gone(tmp_path):
    """snapshots.py handles a dangling pointer explicitly; nothing asserted it.

    A regression here hands callers a path to data that is not there, which
    surfaces as a confusing loader error rather than "there is no live snapshot".
    """
    import shutil

    store = SnapshotStore(tmp_path)
    candidate = store.write_candidate({"brands": "Brand\na\n"}, "26.0-mdb", "2026-08-22")
    store.promote(candidate)
    assert store.live() == candidate

    shutil.rmtree(candidate)
    assert store.live() is None


def test_the_marker_a_candidate_writes_is_the_one_the_loader_reads(tmp_path, seed_dir):
    """The contract between the two halves, which Task 5 depends on.

    write_candidate writes SNAPSHOT.txt and load_dataset parses it, but the
    committed test only substring-checked the text. _read_marker degrades to
    "unknown" instead of raising when it cannot parse, so a format drift here
    would be silent -- the version would just quietly become "unknown" and the
    refresh job would re-fetch forever.
    """
    from app.loader import load_dataset

    tables = {p.stem: p.read_text(encoding="utf-8") for p in seed_dir.glob("*.csv")}
    store = SnapshotStore(tmp_path)
    candidate = store.write_candidate(tables, "27.0-mdb", "2026-09-01")

    dataset = load_dataset(candidate)
    assert dataset.version == "27.0-mdb"
    assert dataset.snapshot_date == "2026-09-01"
    assert dataset.source.startswith("https://")


def test_a_date_that_would_escape_or_shadow_the_store_is_refused(tmp_path):
    import pytest

    store = SnapshotStore(tmp_path)
    for bad in ("LIVE", "/etc", "../escape", "", "."):
        with pytest.raises(ValueError):
            store.write_candidate({"brands": "Brand\na\n"}, "26.0-mdb", bad)


def test_prune_keeps_the_newest_and_never_the_live_one(tmp_path):
    """Snapshots are kept for rollback, but "kept" cannot mean "forever".

    Every promoted refresh writes a directory; on a timer that is unbounded.
    The live snapshot is exempt regardless of age -- pruning what LIVE points at
    leaves a dangling pointer, and live() answers that with None, which would
    silently drop the service back to the seed dataset.
    """
    store = SnapshotStore(tmp_path)
    made = [
        store.write_candidate({"brands": "Brand\n%d\n" % i}, "26.0-mdb", "2026-08-%02d" % (i + 1))
        for i in range(6)
    ]
    store.promote(made[0])  # the OLDEST is live, so age alone must not remove it

    dropped = store.prune(keep=3)

    survivors = set(store.list())
    assert made[0] in survivors, "the live snapshot was pruned"
    assert store.live() == made[0]
    assert (store.live() / "brands.csv").read_text() == "Brand\n0\n"
    assert made[-1] in survivors, "the newest snapshot was pruned"
    assert len(survivors) == 3
    assert set(dropped).isdisjoint(survivors)


def test_prune_refuses_to_keep_nothing(tmp_path):
    import pytest

    store = SnapshotStore(tmp_path)
    with pytest.raises(ValueError):
        store.prune(keep=0)
