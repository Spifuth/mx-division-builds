import pytest

from app.loader import TABLE_NAMES, DatasetError, load_dataset


def test_loads_all_twenty_tables(tmp_path, seed_dir):
    dataset = load_dataset(seed_dir)
    assert sorted(dataset.tables) == sorted(TABLE_NAMES)
    assert len(TABLE_NAMES) == 20


def test_rows_are_dicts_keyed_by_header(seed_dir):
    dataset = load_dataset(seed_dir)
    weapon = dataset.tables["weapon"][0]
    assert weapon["Name"]
    assert weapon["Weapon Type"]


def test_a_missing_table_fails_the_boot(tmp_path, seed_dir):
    """Serving 19 of 20 tables is worse than not starting: the frontend gets a
    partial dataset and no error."""
    import shutil

    shutil.copytree(seed_dir, tmp_path / "data")
    (tmp_path / "data" / "weapon.csv").unlink()
    with pytest.raises(DatasetError, match="weapon"):
        load_dataset(tmp_path / "data")


def test_an_empty_table_fails_the_boot(tmp_path, seed_dir):
    import shutil

    shutil.copytree(seed_dir, tmp_path / "data")
    (tmp_path / "data" / "brands.csv").write_text("Brand,Type,Icon\n")
    with pytest.raises(DatasetError, match="brands"):
        load_dataset(tmp_path / "data")


def test_version_comes_from_the_snapshot_marker(seed_dir):
    dataset = load_dataset(seed_dir)
    assert dataset.version == "26.0-mdb"


def test_a_lost_column_fails_the_boot(tmp_path, seed_dir):
    """The third DatasetError path, which shipped without a test.

    A table that is present and non-empty but has lost a column the API's own
    endpoints read is the quietest of the three failures: the service starts,
    /api/meta looks healthy, and the frontend gets rows with a field missing.
    Nothing else in the suite covers loader.py's REQUIRED_COLUMNS check, so a
    refactor of _read_table could remove it silently.
    """
    import csv
    import shutil

    shutil.copytree(seed_dir, tmp_path / "data")
    target = tmp_path / "data" / "mask.csv"

    with target.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    fields = [f for f in rows[0] if f and f != "Talent"]

    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    with pytest.raises(DatasetError, match="Talent"):
        load_dataset(tmp_path / "data")


def test_a_missing_snapshot_marker_degrades_rather_than_raising(tmp_path, seed_dir):
    """Deliberately NOT a boot failure, unlike the three above.

    A candidate snapshot fetched in Task 5 has no SNAPSHOT.txt yet, so the
    marker must degrade. Asserted here because "this one is allowed to be
    absent" is exactly the kind of intent that gets refactored away later.
    """
    import shutil

    shutil.copytree(seed_dir, tmp_path / "data")
    (tmp_path / "data" / "SNAPSHOT.txt").unlink()

    dataset = load_dataset(tmp_path / "data")
    assert dataset.version == "unknown"
    assert dataset.snapshot_date == "unknown"
    assert len(dataset.tables) == 20, "the tables must still load"
