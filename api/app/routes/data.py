"""Reference-data endpoints. Every one reads from memory; none touches disk.

Each handler does the same three things in the same order: pick the rows,
normalise them into the shape `types.ts` declares, then window the result. The
normalising happens *before* the search so `q` only ever matches text the caller
can actually see -- searching the raw CSV would let a query hit a column that
never appears in the response, which reads as a broken filter.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app import normalise
from app.loader import TABLE_NAMES, Dataset
from app.models import ListResponse, Meta

router = APIRouter(prefix="/api")

GEAR_SLOTS = list(normalise.GEAR_SLOTS)

TALENT_TABLES = {"gear": "gearTalents", "weapon": "weaponTalents"}
ATTRIBUTE_TABLES = {"gear": "gearAttributes", "weapon": "weaponAttributes"}
MOD_TABLES = {"gear": "gearMods", "weapon": "weaponMods", "skill": "skillMods"}


def dataset(request: Request) -> Dataset:
    return request.app.state.dataset


# The frontend's mock handlers all default to these two, and it reads both back
# off the envelope. `limit=0` still means "no ceiling" for the raw escape hatch
# and for internal callers; the frontend never sends it.
Limit = Query(default=100, ge=0, le=5000)
Offset = Query(default=0, ge=0)


def _window(rows: list[dict], q: str | None, limit: int, offset: int) -> ListResponse:
    """Search, then page.

    `total` is the number of matches, not the page size -- reporting the page
    length there would silently make every result set look like one page.
    """
    if q:
        needle = q.casefold()
        rows = [r for r in rows if _matches(r, needle)]
    total = len(rows)
    window = rows[offset : offset + limit] if limit else rows[offset:]
    return ListResponse(total=total, limit=limit, offset=offset, results=window)


# `id` is synthetic and prefixed by kind and slot -- "mask-coyote-s-mask",
# "weapon-the-bighorn". Searching it means typing "mask" into the gear box
# matches all 76 masks and "weapon" matches all 323 weapons, with nothing to
# say the filter did nothing. It is also the one field the caller never sees.
# `slot` joins it for the same reason: on /api/gear/mask every row's slot IS
# "mask", so the term the user is most likely to type matches the whole table.
# Losing "search by slot name" on /api/gear costs nothing -- the UI picks a slot
# with tabs, not with the search box.
_UNSEARCHABLE = frozenset({"id", "slot"})


def _matches(row: dict, needle: str) -> bool:
    for key, value in row.items():
        if key in _UNSEARCHABLE:
            continue
        if isinstance(value, str):
            if needle in value.casefold():
                return True
        elif isinstance(value, list):
            if any(isinstance(v, str) and needle in v.casefold() for v in value):
                return True
    return False


def _gear_set_names(data: Dataset) -> frozenset[str]:
    """The 27 brands.csv rows whose Type is "Gearset".

    A gear piece's Brand column holds either a civilian brand or a gear set
    name with nothing to tell them apart, and `types.ts` keeps the two in
    different fields, so the split has to come from brands.csv.
    """
    return frozenset(
        r.get("Brand", "") for r in data.tables["brands"] if r.get("Type", "") == "Gearset"
    )


# --- meta and the raw escape hatch ----------------------------------------


@router.get("/meta", response_model=Meta)
def meta(request: Request) -> Meta:
    data = dataset(request)
    return Meta(
        datasetVersion=data.version,
        tables=[{"name": name, "rows": count} for name, count in sorted(data.counts.items())],
        snapshotDate=data.snapshot_date,
        source=data.source,
        tableCount=len(data.tables),
        counts=data.counts,
        unsupportedFields=normalise.UNSUPPORTED_FIELDS,
        lastRefresh=getattr(request.app.state, "last_refresh", None),
        degraded=getattr(request.app.state, "degraded", None),
    )


@router.get("/tables/{name}", response_model=ListResponse)
def raw_table(
    request: Request,
    name: str,
    limit: int = Limit,
    offset: int = Offset,
) -> ListResponse:
    """The unnormalised rows, CSV headers and all.

    The one endpoint that deliberately does not adapt: it exists so a column
    that has no home in `types.ts` yet is still reachable without a code change.
    """
    if name not in TABLE_NAMES:
        raise HTTPException(status_code=404, detail=f"unknown table {name!r}")
    return _window([dict(r) for r in dataset(request).tables[name]], None, limit, offset)


# --- weapons --------------------------------------------------------------


@router.get("/weapons", response_model=ListResponse)
def weapons(
    request: Request,
    type: str | None = None,
    quality: str | None = None,
    q: str | None = None,
    limit: int = Limit,
    offset: int = Offset,
) -> ListResponse:
    rows = [normalise.weapon(r) for r in dataset(request).tables["weapon"]]
    # Filtered on the normalised values, not the raw ones: the frontend only
    # ever knows the names it was served, so `?quality=Standard` has to match
    # the rows the list called Standard.
    if type:
        rows = [r for r in rows if r["type"].casefold() == type.casefold()]
    if quality:
        rows = [r for r in rows if r["quality"].casefold() == quality.casefold()]
    return _window(rows, q, limit, offset)


@router.get("/weapons/{name}")
def weapon(request: Request, name: str) -> dict:
    for row in dataset(request).tables["weapon"]:
        if row.get("Name", "").casefold() == name.casefold():
            return normalise.weapon(row)
    raise HTTPException(status_code=404, detail=f"unknown weapon {name!r}")


# --- gear -----------------------------------------------------------------


def _gear_rows(data: Dataset, slots: list[str]) -> list[dict]:
    sets = _gear_set_names(data)
    return [normalise.gear(r, slot, sets) for slot in slots for r in data.tables[slot]]


@router.get("/gear", response_model=ListResponse)
def all_gear(
    request: Request,
    quality: str | None = None,
    brand: str | None = None,
    q: str | None = None,
    limit: int = Limit,
    offset: int = Offset,
) -> ListResponse:
    """Every slot at once, for the compare and lookup views that need the whole
    catalogue in one call rather than six."""
    return _filter_gear(_gear_rows(dataset(request), GEAR_SLOTS), quality, brand, q, limit, offset)


@router.get("/gear/{slot}", response_model=ListResponse)
def gear(
    request: Request,
    slot: str,
    quality: str | None = None,
    brand: str | None = None,
    q: str | None = None,
    limit: int = Limit,
    offset: int = Offset,
) -> ListResponse:
    if slot not in GEAR_SLOTS:
        raise HTTPException(status_code=404, detail=f"unknown gear slot {slot!r}")
    return _filter_gear(_gear_rows(dataset(request), [slot]), quality, brand, q, limit, offset)


def _filter_gear(
    rows: list[dict],
    quality: str | None,
    brand: str | None,
    q: str | None,
    limit: int,
    offset: int,
) -> ListResponse:
    if quality:
        rows = [r for r in rows if r["quality"].casefold() == quality.casefold()]
    if brand:
        # Matches either field. One CSV column feeds both `brand` and
        # `gearSet`, so `?brand=True Patriot` naming a gear set is a reasonable
        # thing for a caller to do and returning nothing would look broken.
        needle = brand.casefold()
        rows = [
            r
            for r in rows
            if r.get("brand", "").casefold() == needle or r.get("gearSet", "").casefold() == needle
        ]
    return _window(rows, q, limit, offset)


# --- brands and gear sets -------------------------------------------------


@router.get("/brands", response_model=ListResponse)
def brands(
    request: Request,
    q: str | None = None,
    limit: int = Limit,
    offset: int = Offset,
) -> ListResponse:
    """Civilian brands only.

    types.ts calls gear sets "distinct from civilian Brands", and
    sets-compare.tsx concatenates this list with /api/gear-sets -- anything in
    both is rendered twice, once under each heading.

    "Exotic" and "Crafted" are pseudo-brands with no set bonuses at all, so an
    empty ladder is correct for exactly those two and wrong for any other.
    """
    data = dataset(request)
    bonuses = normalise.group_bonuses(data.tables["brandsetBonuses"])
    rows = [
        normalise.brand(b, bonuses.get(b.get("Brand", ""), []))
        for b in data.tables["brands"]
        if b.get("Type", "") != "Gearset"
    ]
    return _window(rows, q, limit, offset)


@router.get("/gear-sets", response_model=ListResponse)
def gear_sets(
    request: Request,
    q: str | None = None,
    limit: int = Limit,
    offset: int = Offset,
) -> ListResponse:
    data = dataset(request)
    bonuses = normalise.group_bonuses(data.tables["brandsetBonuses"])
    rows = [
        normalise.gear_set(b, bonuses.get(b.get("Brand", ""), []))
        for b in data.tables["brands"]
        if b.get("Type", "") == "Gearset"
    ]
    return _window(rows, q, limit, offset)


# --- skills ---------------------------------------------------------------


def _skills(data: Dataset) -> list[dict]:
    """The 43 variant rows folded into the 12 base skills they belong to.

    Grouping order follows the CSV so a refresh that appends rows does not
    reshuffle the list; ids do not depend on it either way.
    """
    stats: dict[str, list[dict]] = {}
    for row in data.tables["skillStats"]:
        stats.setdefault(row.get("Skill Variant Name", "").strip().casefold(), []).append(row)

    grouped: dict[str, list[dict]] = {}
    for row in data.tables["skill"]:
        grouped.setdefault(row.get("Item Name", ""), []).append(row)
    return [normalise.skill(rows, stats) for rows in grouped.values()]


@router.get("/skills", response_model=ListResponse)
def skills(
    request: Request,
    q: str | None = None,
    limit: int = Limit,
    offset: int = Offset,
) -> ListResponse:
    return _window(_skills(dataset(request)), q, limit, offset)


@router.get("/skills/{skill_id}")
def skill(request: Request, skill_id: str) -> dict:
    """Addressed by the `id` the list serves, not by skill.csv's Skill ID.

    Skill ID numbers the 43 *variants*, and a Skill is one of the 12 base
    skills that own them, so there is no Skill ID that names one. The frontend
    looks a skill up by the id it was handed anyway.
    """
    wanted = skill_id.casefold()
    for entity in _skills(dataset(request)):
        if entity["id"].casefold() == wanted:
            return entity
    raise HTTPException(status_code=404, detail=f"unknown skill {skill_id!r}")


# --- specializations, talents, attributes, mods ---------------------------


@router.get("/specializations", response_model=ListResponse)
def specializations(
    request: Request,
    q: str | None = None,
    limit: int = Limit,
    offset: int = Offset,
) -> ListResponse:
    """specialization.csv's 22 rows are 7 specs' stat lines, one row per stat,
    so the entity is the distinct Name rather than the row."""
    seen: list[str] = []
    for row in dataset(request).tables["specialization"]:
        name = row.get("Name", "")
        if name and name not in seen:
            seen.append(name)
    return _window([normalise.specialization(n) for n in seen], q, limit, offset)


@router.get("/talents/{kind}", response_model=ListResponse)
def talents(
    request: Request,
    kind: str,
    q: str | None = None,
    limit: int = Limit,
    offset: int = Offset,
) -> ListResponse:
    if kind not in TALENT_TABLES:
        raise HTTPException(status_code=404, detail=f"unknown talent kind {kind!r}")
    rows = [normalise.talent(r, kind) for r in dataset(request).tables[TALENT_TABLES[kind]]]
    return _window(rows, q, limit, offset)


@router.get("/attributes/{kind}", response_model=ListResponse)
def attributes(
    request: Request,
    kind: str,
    q: str | None = None,
    limit: int = Limit,
    offset: int = Offset,
) -> ListResponse:
    if kind not in ATTRIBUTE_TABLES:
        raise HTTPException(status_code=404, detail=f"unknown attribute kind {kind!r}")
    rows = [normalise.attribute(r, kind) for r in dataset(request).tables[ATTRIBUTE_TABLES[kind]]]
    return _window(rows, q, limit, offset)


@router.get("/mods/{kind}", response_model=ListResponse)
def mods(
    request: Request,
    kind: str,
    q: str | None = None,
    limit: int = Limit,
    offset: int = Offset,
) -> ListResponse:
    if kind not in MOD_TABLES:
        raise HTTPException(status_code=404, detail=f"unknown mod kind {kind!r}")
    rows = [normalise.mod(r, kind) for r in dataset(request).tables[MOD_TABLES[kind]]]
    return _window(rows, q, limit, offset)
