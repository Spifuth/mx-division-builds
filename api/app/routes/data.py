"""Reference-data endpoints. Every one reads from memory; none touches disk."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.loader import TABLE_NAMES, Dataset
from app.models import Meta, RawTable, RowList

router = APIRouter(prefix="/api")


def dataset(request: Request) -> Dataset:
    return request.app.state.dataset


@router.get("/meta", response_model=Meta)
def meta(request: Request) -> Meta:
    data = dataset(request)
    return Meta(
        version=data.version,
        snapshot_date=data.snapshot_date,
        source=data.source,
        table_count=len(data.tables),
        counts=data.counts,
        tables=sorted(data.tables),
    )


@router.get("/tables/{name}", response_model=RawTable)
def raw_table(
    request: Request,
    name: str,
    limit: int = Query(default=0, ge=0, le=5000),
    offset: int = Query(default=0, ge=0),
) -> RawTable:
    if name not in TABLE_NAMES:
        raise HTTPException(status_code=404, detail=f"unknown table {name!r}")
    rows = dataset(request).tables[name]
    window = rows[offset : offset + limit] if limit else rows[offset:]
    return RawTable(name=name, count=len(window), rows=window)


GEAR_SLOTS = ["mask", "chest", "backpack", "gloves", "holster", "kneepads"]

TALENT_TABLES = {"gear": "gearTalents", "weapon": "weaponTalents"}
ATTRIBUTE_TABLES = {"gear": "gearAttributes", "weapon": "weaponAttributes"}
MOD_TABLES = {"gear": "gearMods", "weapon": "weaponMods", "skill": "skillMods"}


def _window(rows: list[dict], q: str | None, limit: int, offset: int) -> RowList:
    """Search, then page. `total` is the number of matches, not the page size --
    a frontend needs it to render pagination, and reporting the page length
    there would silently make every result set look like one page."""
    if q:
        needle = q.casefold()
        rows = [r for r in rows if any(needle in str(v).casefold() for v in r.values())]
    total = len(rows)
    window = rows[offset : offset + limit] if limit else rows[offset:]
    return RowList(count=len(window), total=total, rows=window)


@router.get("/weapons", response_model=RowList)
def weapons(
    request: Request,
    type: str | None = None,
    quality: str | None = None,
    q: str | None = None,
    limit: int = Query(default=0, ge=0, le=5000),
    offset: int = Query(default=0, ge=0),
) -> RowList:
    rows = dataset(request).tables["weapon"]
    if type:
        rows = [r for r in rows if r.get("Weapon Type", "").casefold() == type.casefold()]
    if quality:
        rows = [r for r in rows if r.get("Quality", "").casefold() == quality.casefold()]
    return _window(rows, q, limit, offset)


@router.get("/weapons/{name}")
def weapon(request: Request, name: str) -> dict:
    for row in dataset(request).tables["weapon"]:
        if row.get("Name", "").casefold() == name.casefold():
            # Copy. This was the one endpoint handing a caller the shared row
            # object itself; Dataset is only shallow-frozen, so anything that
            # later wrote into the response would corrupt it for every request
            # that followed. Its siblings all build fresh dicts already.
            return dict(row)
    raise HTTPException(status_code=404, detail=f"unknown weapon {name!r}")


@router.get("/gear/{slot}", response_model=RowList)
def gear(
    request: Request,
    slot: str,
    quality: str | None = None,
    brand: str | None = None,
    q: str | None = None,
    limit: int = Query(default=0, ge=0, le=5000),
    offset: int = Query(default=0, ge=0),
) -> RowList:
    if slot not in GEAR_SLOTS:
        raise HTTPException(status_code=404, detail=f"unknown gear slot {slot!r}")
    rows = dataset(request).tables[slot]
    if quality:
        rows = [r for r in rows if r.get("Quality", "").casefold() == quality.casefold()]
    if brand:
        rows = [r for r in rows if r.get("Brand", "").casefold() == brand.casefold()]
    return _window(rows, q, limit, offset)


# brandsetBonuses keys on the brand name with the bonus TIER appended directly,
# no separator: "5.11 Tactical0", "5.11 Tactical1", "5.11 Tactical2" against
# brands.csv's plain "5.11 Tactical". Joining on the raw string matches 0 of 66
# -- measured, this endpoint shipped that way and returned "bonuses": [] for
# every brand with a 200 and a plausible total.
#
# The digit is the tier (0 = 1-piece, 1 = 2-piece, 2 = 3-piece), so it is data,
# not noise: it is stripped for the join and kept as `tier` on each bonus.
_BRAND_TIER = re.compile(r"^(?P<brand>.*?)(?P<tier>\d+)$")


def _brand_key(raw: str) -> tuple[str, int | None]:
    match = _BRAND_TIER.match(raw)
    if not match:
        return raw, None
    return match.group("brand"), int(match.group("tier"))


@router.get("/brands", response_model=RowList)
def brands(request: Request, q: str | None = None) -> RowList:
    data = dataset(request)
    bonuses: dict[str, list[dict[str, Any]]] = {}
    for row in data.tables["brandsetBonuses"]:
        brand, tier = _brand_key(row.get("Brand", ""))
        bonuses.setdefault(brand, []).append({**row, "tier": tier})
    for entries in bonuses.values():
        entries.sort(key=lambda e: (e["tier"] is None, e["tier"]))

    # "Exotic" and "Crafted" are pseudo-brands with no set bonuses at all, so an
    # empty list is correct for exactly those two and wrong for any other.
    rows = [{**b, "bonuses": bonuses.get(b.get("Brand", ""), [])} for b in data.tables["brands"]]
    return _window(rows, q, 0, 0)


@router.get("/skills", response_model=RowList)
def skills(request: Request, q: str | None = None) -> RowList:
    return _window(dataset(request).tables["skill"], q, 0, 0)


@router.get("/skills/{skill_id}")
def skill(request: Request, skill_id: str) -> dict:
    data = dataset(request)
    for row in data.tables["skill"]:
        if row.get("Skill ID", "").casefold() == skill_id.casefold():
            # skillStats keys on a display name, not on Skill ID or Variant.
            # "Sticky Bomb" + "Burn" -> "Burn Sticky Bomb". Verified: this form
            # matches 43/43 rows, joining on Variant alone matches 0, and
            # joining Skill ID to "Skill Stat ID" appears to match 43/43 but is
            # a coincidence -- Skill Stat ID is a row counter, so it would pair
            # Sticky Bomb with Achilles Pulse. statsService.js:624 builds the
            # same key: `${skill.variant} ${skill.itemName}`.
            key = f"{row.get('Variant', '')} {row.get('Item Name', '')}".casefold()
            stats = [
                s for s in data.tables["skillStats"]
                if s.get("Skill Variant Name", "").casefold() == key
            ]
            return {**row, "stats": stats}
    raise HTTPException(status_code=404, detail=f"unknown skill {skill_id!r}")


@router.get("/specializations", response_model=RowList)
def specializations(request: Request) -> RowList:
    return _window(dataset(request).tables["specialization"], None, 0, 0)


@router.get("/talents/{kind}", response_model=RowList)
def talents(request: Request, kind: str, q: str | None = None) -> RowList:
    if kind not in TALENT_TABLES:
        raise HTTPException(status_code=404, detail=f"unknown talent kind {kind!r}")
    return _window(dataset(request).tables[TALENT_TABLES[kind]], q, 0, 0)


@router.get("/attributes/{kind}", response_model=RowList)
def attributes(request: Request, kind: str) -> RowList:
    if kind not in ATTRIBUTE_TABLES:
        raise HTTPException(status_code=404, detail=f"unknown attribute kind {kind!r}")
    return _window(dataset(request).tables[ATTRIBUTE_TABLES[kind]], None, 0, 0)


@router.get("/mods/{kind}", response_model=RowList)
def mods(request: Request, kind: str, q: str | None = None) -> RowList:
    if kind not in MOD_TABLES:
        raise HTTPException(status_code=404, detail=f"unknown mod kind {kind!r}")
    return _window(dataset(request).tables[MOD_TABLES[kind]], q, 0, 0)
