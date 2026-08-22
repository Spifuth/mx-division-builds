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
