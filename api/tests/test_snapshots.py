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
