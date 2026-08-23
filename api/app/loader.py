"""CSV -> in-memory dataset. Startup only; no request handler calls this.

Everything is loaded eagerly and validated before the app serves, so a bad
table is a boot failure rather than a 500 halfway through someone's session.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

TABLE_NAMES = [
    "backpack", "brands", "brandsetBonuses", "chest", "gearAttributes",
    "gearMods", "gearTalents", "gloves", "holster", "kneepads", "mask",
    "skill", "skillMods", "skillStats", "specialization", "statsMapping",
    "weapon", "weaponAttributes", "weaponMods", "weaponTalents",
]

# Columns the API's own endpoints depend on. Same contracts as
# scripts/check-data-sources.mjs -- kept in step deliberately, because the two
# guard the same failure from different sides.
GEAR_COLUMNS = ["Quality", "Item Name", "Type", "Brand", "Core", "Attribute 1", "Talent", "Icon"]
REQUIRED_COLUMNS: dict[str, list[str]] = {
    "mask": GEAR_COLUMNS, "chest": GEAR_COLUMNS, "gloves": GEAR_COLUMNS,
    "holster": GEAR_COLUMNS, "kneepads": GEAR_COLUMNS, "backpack": GEAR_COLUMNS,
    "weapon": [
        "Name", "Quality", "RPM", "Base Damage", "Mag Size", "Optimal Range",
        "Reload Speed (ms)", "HSD", "Core 1", "Core 1 Max", "Core 2",
        "Core 2 Max", "Weapon Type", "Variant", "Talent",
    ],
    "skill": [
        "Skill ID", "Item Name", "Icon", "Variant", "Quality",
        "Expertise Bonus", "Mod 1", "Mod 2", "Mod 3", "Desc",
    ],
    "brands": ["Brand", "Type", "Icon"],

    # Added after the Task 3b review. Everything above guarded the tables the
    # ENDPOINTS read; normalise.py then grew ~30 more column dependencies across
    # the tables below and none were validated at boot. Refresh only requires a
    # 50% header overlap, so an upstream rename of `val` to `Val` would promote
    # a snapshot in which all 192 brand set bonuses silently degrade to bare
    # labels -- 200 OK, nothing in the logs, no test red, because the bonus-text
    # assertions run against the seed. That is the same shape as the brands join
    # that already shipped broken once in this project.
    "brandsetBonuses": ["Brand", "stat", "val", "stat1", "val1", "Talent"],
    "skillStats": [
        "Skill Variant Name", "Stat", "Val",
        "Tier 0", "Tier 1", "Tier 2", "Tier 3", "Tier 4", "Tier 5", "Tier 6",
    ],
    "gearTalents": ["Quality", "Slot", "Talent", "Desc"],
    "weaponTalents": ["Quality", "Name", "Desc"],
    "gearAttributes": ["Quality", "Type", "Stat", "Max"],
    "weaponAttributes": ["Quality", "Type", "Stat", "Max"],
    "gearMods": ["Quality", "Type", "Stat", "Max"],
    "weaponMods": ["Slot", "Type", "Name", "valPos", "pos", "valNeg", "neg"],
    "skillMods": ["Skill Mod ID", "Skill Type", "Skill Mod Slot", "Mod Attribute"],
    "specialization": ["Name", "Stat", "Val"],
}


class DatasetError(RuntimeError):
    """A table is missing, empty, or has lost a column the API depends on."""


@dataclass(frozen=True)
class Dataset:
    tables: dict[str, list[dict[str, str]]]
    version: str
    snapshot_date: str
    source: str
    counts: dict[str, int] = field(default_factory=dict)
    # The header of each live table, taken from the first row. Carried
    # alongside `counts` and for the same reason: refresh.py validates a
    # candidate against what is currently being served, and shape is as much a
    # part of "currently being served" as size. REQUIRED_COLUMNS covers only 7
    # of the 20 tables, so for the other 13 this is the only record of what
    # their columns are supposed to be.
    headers: dict[str, list[str]] = field(default_factory=dict)


def _read_table(path: Path, name: str) -> list[dict[str, str]]:
    if not path.is_file():
        raise DatasetError(f"table {name!r} is missing at {path}")

    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = [{k: (v or "") for k, v in row.items() if k} for row in csv.DictReader(handle)]

    if not rows:
        raise DatasetError(f"table {name!r} has a header but no rows")

    missing = [c for c in REQUIRED_COLUMNS.get(name, []) if c not in rows[0]]
    if missing:
        raise DatasetError(f"table {name!r} lost columns: {', '.join(missing)}")

    return rows


def _read_marker(data_dir: Path) -> tuple[str, str, str]:
    """Version, snapshot date and source, from SNAPSHOT.txt.

    Absent in a freshly fetched candidate, so all three degrade to "unknown"
    rather than raising -- the caller stamps a candidate itself.
    """
    marker = data_dir / "SNAPSHOT.txt"
    values = {"snapshot": "unknown", "upstream DB.Version": "unknown", "source": "unknown"}
    if marker.is_file():
        for line in marker.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition(":")
            if key.strip() in values and value.strip():
                values[key.strip()] = value.strip()
    return values["upstream DB.Version"], values["snapshot"], values["source"]


def load_dataset(data_dir: Path) -> Dataset:
    tables = {name: _read_table(data_dir / f"{name}.csv", name) for name in TABLE_NAMES}
    version, snapshot_date, source = _read_marker(data_dir)
    return Dataset(
        tables=tables,
        version=version,
        snapshot_date=snapshot_date,
        source=source,
        counts={name: len(rows) for name, rows in tables.items()},
        headers={name: list(rows[0]) for name, rows in tables.items() if rows},
    )
