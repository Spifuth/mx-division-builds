"""Raw CSV rows -> the shapes the frontend is built against.

The dataset ships as buildstation.app's export: headers with spaces in them
("Base Damage", "Mag Size"), every value a string, and no identity column. The
frontend that consumes this API was written first and declares what it needs in
`lib/types.ts`, committed here as
`docs/superpowers/specs/2026-08-23-frontend-contract-types.ts`. This module is
the adapter between the two, and `types.ts` is the authority: where the CSV and
the interface disagree about a name, the interface wins.

Three rules hold everywhere in this file.

1. **Never mutate the row.** `app.state.dataset` is shared by every request and
   `Dataset` is only shallow-frozen, so a row written through here is corrupted
   for every request that follows. Every function builds a new dict.
2. **Never invent a number.** A cell that will not parse becomes 0 rather than
   raising -- a blank `Mag Size` must not 500 an endpoint -- but any field with
   no source column at all is also listed in `UNSUPPORTED_FIELDS`, which
   /api/meta serves. A confident 0 the caller cannot tell from real data is the
   same class of defect as a check that cannot fail.
3. **Never derive an id from position.** `BuildLoadout` is twelve
   `string | null` item ids and nothing else, so a saved build is exactly as
   durable as the ids in it. Ids come from stable content -- kind and name --
   and survive upstream reordering a table.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

# --- primitives -----------------------------------------------------------

_NOT_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    return _NOT_SLUG.sub("-", str(text).casefold()).strip("-")


def entity_id(kind: str, *parts: str) -> str:
    """`mask` + `Providence Vigilance Mask` -> `mask-providence-vigilance-mask`.

    Blank parts are dropped rather than collapsed into a double hyphen, so a
    column that is empty today and populated tomorrow does not change the ids
    of the rows where it stays empty.
    """
    pieces = [slugify(kind)] + [slugify(p) for p in parts]
    return "-".join(p for p in pieces if p)


_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def to_number(value: Any) -> int | float:
    """A CSV cell as a number, or 0.

    Returns `int` when the value is integral so the frontend gets `30` rather
    than `30.0`, and `float` only when the data really is fractional -- weapon
    damage genuinely carries values like 47364.5 and rounding it would be a
    silent edit to the dataset.
    """
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        match = _NUMBER.search(str(value or "").replace(",", ""))
        if match is None:
            return 0
        number = float(match.group())
    return int(number) if number.is_integer() else number


def _signed(value: str, label: str) -> str:
    """"30" + "Accuracy" -> "+30 Accuracy"; "-25" keeps its own sign.

    No unit is appended. Nothing in the dataset records whether a given stat is
    a percentage or a flat value, and "+18935 Armor on Kill" sitting next to
    "+6 Critical Hit Chance" is at least true, where "+18935%" would not be.
    """
    value, label = str(value or "").strip(), str(label or "").strip()
    if not label:
        return ""
    if not value:
        return label
    sign = "" if value.startswith("-") else "+"
    return f"{sign}{value} {label}"


# --- vocabulary -----------------------------------------------------------

GEAR_SLOTS = ("mask", "chest", "backpack", "gloves", "holster", "kneepads")

# The CSV and `types.ts` name the same tiers differently. This is a rename, not
# a judgement: `WeaponQuality` and `GearQuality` declare the target vocabulary
# and the frontend's palette (QUALITY_STYLES, QUALITY_TINT) is keyed by it, so
# "High End" renders unstyled while "Standard" renders correctly. Values not
# listed pass through unchanged -- notably "Exotic", which `GearQuality` omits
# but 32 real gear pieces are, and which the frontend styles anyway.
WEAPON_QUALITY = {"High End": "Standard"}
GEAR_QUALITY = {"High End": "Standard", "Gearset": "Gear Set"}

# Fields that `types.ts` requires and the dataset cannot supply. Declared once,
# here, and served by /api/meta so the UI can grey them out instead of drawing
# a confident zero. Keys carry a `:category` suffix where one interface is
# served by several endpoints whose backing tables differ -- gearMods.csv has
# no slot column while weaponMods.csv and skillMods.csv both do, and reporting
# a single `mod: ["slot"]` would libel two thirds of the mods.
UNSUPPORTED_FIELDS: dict[str, list[str]] = {
    # weapon.csv has no accuracy, stability, handling or brand column, and its
    # Icon is a bare filename with no asset behind it anywhere in this repo.
    "weapon": ["accuracy", "stability", "handling", "brand", "image"],
    # No gear table carries an armour value; Icon is a bare filename as above.
    "gear": ["armor", "image"],
    "brand": [],
    "gearSet": [],
    # skill.csv's Desc is per *variant* and fills SkillVariant.description in
    # full (43 of 43 populated). There is no row and no column describing the
    # base skill, so Skill.description has no source.
    "skill": ["description"],
    # specialization.csv is Name,Stat,Val. Its Stat/Val pairs are the spec's
    # passive attribute bonuses, not the named perk nodes `perks` means, so
    # filling from them would put real data under the wrong label.
    "specialization": ["signatureWeapon", "description", "perks"],
    "talent:gear": [],
    "talent:weapon": [],
    # Both attribute tables carry Max and nothing else: no minimum, no unit.
    # gearAttributes.csv has no slot column at all; weaponAttributes.csv has a
    # Weapon Type column that is empty in all 18 rows.
    "attribute:gear": ["min", "unit", "slot"],
    "attribute:weapon": ["min", "unit", "slot"],
    "mod:gear": ["slot"],
    "mod:weapon": [],
    "mod:skill": [],
}


# --- weapons --------------------------------------------------------------


def weapon(row: Mapping[str, str]) -> dict[str, Any]:
    name = row.get("Name", "")
    built: dict[str, Any] = {
        "id": entity_id("weapon", name),
        "name": name,
        "type": row.get("Weapon Type", ""),
        "quality": WEAPON_QUALITY.get(row.get("Quality", ""), row.get("Quality", "")),
        "damage": to_number(row.get("Base Damage")),
        "rpm": to_number(row.get("RPM")),
        "magazine": to_number(row.get("Mag Size")),
        "optimalRange": to_number(row.get("Optimal Range")),
        # No source column. See UNSUPPORTED_FIELDS.
        "accuracy": 0,
        "stability": 0,
        "handling": 0,
        # A list because `Weapon.talents` is one; the table holds a single
        # Talent per weapon today, so it is a one-element list, not a split.
        "talents": [t for t in (row.get("Talent", "").strip(),) if t],
    }
    return built


# --- gear -----------------------------------------------------------------

_ATTRIBUTE_COLUMNS = ("Attribute 1", "Attribute 2", "Attribute 3")


def gear(
    row: Mapping[str, str],
    slot: str,
    gear_set_names: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """One gear row as a `GearPiece`.

    `gear_set_names` is the set of brands whose `Type` in brands.csv is
    "Gearset" -- 27 of the 66. A piece's Brand column holds either a civilian
    brand or a gear set name, and `types.ts` keeps the two apart
    (`GearPiece.brand` vs `GearPiece.gearSet`), so the caller passes in the
    lookup rather than this function guessing from the name.
    """
    name = row.get("Item Name", "")
    brand_name = row.get("Brand", "").strip()
    built: dict[str, Any] = {
        "id": entity_id(slot, name),
        "name": name,
        "slot": slot,
        "quality": GEAR_QUALITY.get(row.get("Quality", ""), row.get("Quality", "")),
        # No source column. See UNSUPPORTED_FIELDS.
        "armor": 0,
        "coreAttribute": row.get("Core", "").strip(),
        "attributeSlots": sum(1 for c in _ATTRIBUTE_COLUMNS if row.get(c, "").strip()),
        "mod": bool(row.get("Mod", "").strip()),
    }
    if brand_name:
        key = "gearSet" if brand_name in gear_set_names else "brand"
        built[key] = brand_name
    talent = row.get("Talent", "").strip()
    if talent:
        built["talent"] = talent
    return built


# --- brands and gear sets -------------------------------------------------

# brandsetBonuses keys on the brand name with the tier digit appended directly,
# no separator: "5.11 Tactical0"/"1"/"2" against brands.csv's "5.11 Tactical".
# Joining on the raw string matches 0 of 66 -- this endpoint shipped that way
# and returned "bonuses": [] for every brand, with a 200 and a plausible total.
_BRAND_TIER = re.compile(r"^(?P<brand>.*?)(?P<tier>\d+)$")


def brand_key(raw: str) -> tuple[str, int | None]:
    match = _BRAND_TIER.match(raw)
    if not match:
        return raw, None
    return match.group("brand"), int(match.group("tier"))


def _bonus_text(row: Mapping[str, str]) -> str:
    """A bonus row as the single string `BrandSetBonus.bonus` wants.

    Three shapes in the data: one stat, two stats (16 rows carry stat1/val1),
    and a gear set's top tier, where `stat` is the literal "Talent" and the
    text lives in the Talent column.
    """
    talent = row.get("Talent", "").strip()
    if talent:
        return talent
    parts = [
        _signed(row.get("val", ""), row.get("stat", "")),
        _signed(row.get("val1", ""), row.get("stat1", "")),
    ]
    return ", ".join(p for p in parts if p)


def group_bonuses(rows: Iterable[Mapping[str, str]]) -> dict[str, list[dict[str, Any]]]:
    """brandsetBonuses -> brand name -> its bonus ladder, ordered by piece count.

    The stripped digit is the tier and it is *not* uniformly 0-based: civilian
    brands run 0/1/2 and gear sets run 1/2/3 (measured, all 64 groups). It is
    the piece count minus one in both cases, so `count = tier + 1` gives a
    brand's 1/2/3-piece bonuses and a gear set's 2/3/4-piece ladder. The UI
    looks bonuses up by `count`, so an off-by-one blanks the whole comparison
    table while every row still returns 200.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        name, tier = brand_key(row.get("Brand", ""))
        if tier is None:
            continue
        grouped.setdefault(name, []).append({"count": tier + 1, "bonus": _bonus_text(row)})
    for entries in grouped.values():
        entries.sort(key=lambda e: e["count"])
    return grouped


def _set_like(kind: str, row: Mapping[str, str], bonuses: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    name = row.get("Brand", "")
    return {
        "id": entity_id(kind, name),
        "name": name,
        # The ladder's top rung, not its length: a gear set has three bonus
        # rows numbered 2, 3 and 4, and the UI renders rows 1..pieces.
        "pieces": max((b["count"] for b in bonuses), default=0),
        "bonuses": [dict(b) for b in bonuses],
    }


def brand(row: Mapping[str, str], bonuses: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return _set_like("brand", row, bonuses)


def gear_set(row: Mapping[str, str], bonuses: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return _set_like("gear-set", row, bonuses)


# --- skills ---------------------------------------------------------------

_TIER_COLUMNS = tuple(f"Tier {i}" for i in range(7))


def skill_stat_key(row: Mapping[str, str]) -> str:
    """The join key skillStats uses: "Burn" + "Sticky Bomb" -> "Burn Sticky Bomb".

    Verified: this form matches 43 of 43 variants. Joining on Variant alone
    matches 0, and joining Skill ID to "Skill Stat ID" appears to match 43 of
    43 but is a coincidence -- Skill Stat ID is a row counter, so it pairs
    Sticky Bomb with Achilles Pulse. statsService.js:624 builds the same key.
    """
    return f"{row.get('Variant', '')} {row.get('Item Name', '')}".strip().casefold()


def skill(
    rows: Sequence[Mapping[str, str]],
    stats: Mapping[str, Sequence[Mapping[str, str]]],
) -> dict[str, Any]:
    """One base skill as a `Skill`.

    Takes the *group* of variant rows sharing an `Item Name`, not a single row:
    skill.csv's 43 rows are 43 variants of 12 skills, and `types.ts` models a
    Skill as one entity owning a `variants` list -- skills-table.tsx renders
    the variants as badges under a single skill card and item-picker-dialog.tsx
    joins their names with " · ". One Skill per CSV row would show "Sticky
    Bomb" three times, each with a one-element list.

    `stats` maps `skill_stat_key(row)` to that variant's skillStats rows.
    """
    name = rows[0].get("Item Name", "")
    variants = [
        {
            "id": entity_id("skill", name, row.get("Variant", "")),
            "name": f"{row.get('Variant', '')} {name}".strip(),
            "description": row.get("Desc", "").strip(),
        }
        for row in rows
    ]

    tier_stats: list[dict[str, Any]] = []
    for row in rows:
        variant = row.get("Variant", "").strip()
        for stat_row in stats.get(skill_stat_key(row), ()):
            label = stat_row.get("Stat", "").strip()
            for index, column in enumerate(_TIER_COLUMNS):
                value = stat_row.get(column, "").strip()
                if not value:
                    continue
                # The variant leads the label because a Skill groups several:
                # without it a Turret's four "Damage" rows at tier 3 are four
                # indistinguishable numbers. A prefix rather than a suffix
                # because some stats are already parenthesised -- "Average
                # Damage (Depends on weapon type)" would otherwise end up
                # wearing two sets of brackets.
                tier_stats.append(
                    {
                        "tier": index,
                        "label": f"{variant}: {label}" if variant else label,
                        "value": value,
                    }
                )

    return {
        "id": entity_id("skill", name),
        "name": name,
        # No source column. See UNSUPPORTED_FIELDS.
        "description": "",
        "variants": variants,
        "tierStats": tier_stats,
    }


# --- specializations ------------------------------------------------------


def specialization(name: str) -> dict[str, Any]:
    """specialization.csv is `Name,Stat,Val` and nothing else.

    Takes the name rather than a row because the 22 rows are 7 specs' stat
    lines, and none of the three fields `Specialization` requires beyond the
    name has a column anywhere in the dataset. See UNSUPPORTED_FIELDS.
    """
    return {
        "id": entity_id("spec", name),
        "name": name,
        "signatureWeapon": "",
        "description": "",
        "perks": [],
    }


# --- talents --------------------------------------------------------------


def talent(row: Mapping[str, str], category: str) -> dict[str, Any]:
    """gearTalents names the column `Talent`, weaponTalents names it `Name`."""
    name = (row.get("Talent") if category == "gear" else row.get("Name")) or ""
    return {
        "id": entity_id("talent", category, name),
        "name": name,
        "category": category,
        "description": row.get("Desc", "").strip(),
    }


# --- attributes -----------------------------------------------------------


def attribute(row: Mapping[str, str], category: str) -> dict[str, Any]:
    """One attribute cap row as an `AttributeCap`.

    `quality` passes the CSV's own code through ("A"/"N"/"E") rather than
    decoding it. The dataset ships no legend for those letters, and the same
    "A" appears in gearTalents' *Slot* column where the other values are
    Chest/Backpack/Mask -- so it plausibly means "All" rather than a rarity. A
    guess rendered as "High End" would be indistinguishable from a fact.
    """
    name = row.get("Stat", "")
    return {
        "id": entity_id(
            "attr", category, row.get("Quality", ""), row.get("Type", ""), name, row.get("Max", "")
        ),
        "name": name,
        "category": category,
        # weaponAttributes.csv has a Weapon Type column (empty in all 18 rows
        # today); gearAttributes.csv has no slot column at all.
        "slot": row.get("Weapon Type", "").strip(),
        "quality": row.get("Quality", "").strip(),
        # No source column. See UNSUPPORTED_FIELDS.
        "min": 0,
        "unit": "",
        "max": to_number(row.get("Max")),
    }


# --- mods -----------------------------------------------------------------


def mod(row: Mapping[str, str], category: str) -> dict[str, Any]:
    """One mod row as a `Mod`. The three mod tables share no columns at all."""
    if category == "weapon":
        name = row.get("Name", "")
        parts = [
            _signed(row.get("valPos", ""), row.get("pos", "")),
            _signed(row.get("valNeg", ""), row.get("neg", "")),
        ]
        # 3 of 249 rows carry no stat change (Pistol Flashlight, and two
        # exotic-specific underbarrels). Their Type is what they are, and a
        # blank Effect cell reads as a missing join rather than as a mod that
        # does not change a stat.
        effect = ", ".join(p for p in parts if p) or row.get("Type", "").strip()
        return {
            "id": entity_id(
                "mod", category, row.get("Slot", ""), row.get("Type", ""), name, row.get("Spec", "")
            ),
            "name": name,
            "category": category,
            "slot": row.get("Slot", "").strip(),
            "effect": effect,
        }

    if category == "gear":
        name = row.get("Stat", "")
        return {
            "id": entity_id("mod", category, row.get("Quality", ""), row.get("Type", ""), name),
            "name": name,
            "category": category,
            # No source column. See UNSUPPORTED_FIELDS.
            "slot": "",
            "effect": _signed(row.get("Max", ""), name),
        }

    name = row.get("Mod Attribute", "")
    skill_type = row.get("Skill Type", "").strip()
    spec = row.get("Specialization Mod", "").strip()
    # The attribute name alone does not say which skill the mod fits -- eleven
    # (skill type, slot, attribute) triples repeat across the table, and
    # "Blast Radius" is offered by four different skills.
    effect = _signed(row.get("Max", ""), name)
    if skill_type:
        effect = f"{effect} · {skill_type}" + (f" ({spec})" if spec else "")
    return {
        "id": entity_id("mod", category, skill_type, row.get("Skill Mod Slot", ""), name, spec),
        "name": name,
        "category": category,
        "slot": row.get("Skill Mod Slot", "").strip(),
        "effect": effect,
    }
