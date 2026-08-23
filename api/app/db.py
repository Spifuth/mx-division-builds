"""Saved builds, on disk.

The first module in this service that touches one. Every reference-data
handler reads from memory by design, and that boundary is worth keeping: if
something here is slow or locked, nothing that serves the dataset should
notice.

Raw SQL over aiosqlite, following Heolstor/shared/database.py -- WAL, a
busy_timeout, an explicit column-list constant so SELECT order cannot drift
away from the code that unpacks it, and secrets.token_urlsafe ids. Two things
are deliberately NOT copied from it:

  * its `try: ALTER TABLE / except OperationalError: pass` migration idiom,
    which cannot tell "column already exists" from a disk error or a corrupt
    file, so every real failure is swallowed at boot. Schema state is tracked
    in PRAGMA user_version instead and each step runs exactly once.
  * its INTEGER author_id. Discord snowflakes are past JavaScript's 2**53-1
    safe integer; Heolstor stored them as int in its bot half and str in its
    web half and needed a coercion shim to stop the two disagreeing about who
    owned a row. Here the column is TEXT and the value is a string everywhere.

The connection model is one connection per operation. This is a low-traffic
endpoint behind a single-process uvicorn, WAL lets readers run while a writer
holds the file, and a module-level shared connection would have to be created
on the right event loop -- which the test suite, with its ~250 create_app()
calls, does not provide.

SANITISING LIVES HERE, not in the route handlers. A browser is not the only
thing that can POST to /api/builds, and putting the cleaning next to the write
means a second caller cannot be added later that forgets to call it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import secrets
import sqlite3
from collections.abc import Mapping
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

log = logging.getLogger("td2-api.db")

SCHEMA_VERSION = 1
BUSY_TIMEOUT_MS = 5000

# --- the shape, copied from the frontend's types.ts ------------------------

# SHD_NODES. Copied, not invented: shd-perks-dialog.tsx indexes every one of
# these keys directly, so a stat this table spells differently renders as NaN
# in the UI with nothing in any log to say why.
SHD_NODES: dict[str, tuple[str, ...]] = {
    "offense": ("weaponDamage", "headshotDamage", "criticalHitChance", "criticalHitDamage"),
    "defense": ("totalHealth", "totalArmor", "hazardProtection", "explosiveResistance"),
    "handling": ("accuracy", "stability", "ammoCapacity", "reloadSpeed"),
    "utility": ("skillRepair", "skillDamage", "skillDuration", "skillHaste"),
}
SHD_STAT_MAX = 50

# BUILD_SLOT_KEYS. The values are item NAMES, not ids: use-loadout-lookup.ts
# resolves every slot with `x.name === name` and item-picker-dialog.tsx passes
# `onSelect(item.name)`. An id stored here renders an empty slot silently.
BUILD_SLOT_KEYS: tuple[str, ...] = (
    "Mask", "Backpack", "Chest", "Gloves", "Holster", "Kneepads",
    "Primary", "Secondary", "SideArm", "Specialization", "Skill1", "Skill2",
)

DEFAULT_NAME = "Unnamed Build"
SHD_LEVEL_DEFAULT = 1

# Ceilings, not game rules. /api/builds is anonymous-writable by anyone who can
# reach the service and SQLite TEXT is unbounded, so without these three
# numbers a single POST is a disk-filling primitive. Generous enough that no
# real build touches them: the longest item name in the dataset is under 40
# characters and the longest plausible note is a few paragraphs.
NAME_MAX_LEN = 120
NOTES_MAX_LEN = 20_000
SLOT_VALUE_MAX_LEN = 120
SHD_LEVEL_MAX = 100_000

# --- schema ----------------------------------------------------------------

# What a read may see. The edit token's hash is NOT in this list and must never
# be: every SELECT that produces a Build uses exactly these columns in exactly
# this order, so a field added to the Build shape later cannot drag the
# credential out with it. models.Build is the second, independent guard.
BUILD_PUBLIC_COLUMNS: tuple[str, ...] = (
    "id", "name", "notes", "shd_level", "shd_perks", "loadout",
    "views", "created_at", "updated_at",
)
_PUBLIC_SQL = ", ".join(BUILD_PUBLIC_COLUMNS)

BUILD_COLUMNS: tuple[str, ...] = BUILD_PUBLIC_COLUMNS + (
    "edit_token_hash", "author_id", "author_name",
)

# Keyed by the user_version each step LEAVES BEHIND, applied in order from
# whatever version the file is at. Step 1 is the baseline and is written to be
# safe to re-run against a file that predates version tracking; every later
# step will not be, and must not be, because a migration that cannot fail is a
# migration that cannot be trusted.
MIGRATIONS: dict[int, tuple[str, ...]] = {
    1: (
        """
        CREATE TABLE IF NOT EXISTS builds (
            id              TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            notes           TEXT NOT NULL DEFAULT '',
            shd_level       INTEGER NOT NULL DEFAULT 1,
            shd_perks       TEXT NOT NULL,
            loadout         TEXT NOT NULL,
            views           INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            edit_token_hash TEXT NOT NULL,
            author_id       TEXT,
            author_name     TEXT
        )
        """,
        # The list endpoint's only ordering, and the cutover query's only
        # filter (WHERE author_id LIKE 'mock:%').
        "CREATE INDEX IF NOT EXISTS idx_builds_created_at ON builds (created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_builds_author_id ON builds (author_id)",
    ),
}


class SchemaTooNew(RuntimeError):
    """The database was written by a newer version of this service.

    Fatal at boot on purpose. Opening it read-write would let this build insert
    rows the newer schema's constraints were meant to reject, and the damage is
    only visible after the rollback is rolled forward again.
    """


class BuildNotFound(LookupError):
    """No build with that id. A 404, never a 403 -- see the route."""


class NotAuthorised(PermissionError):
    """Neither the edit token nor the session owns this build."""


def init_db(path: Path) -> None:
    """Create or migrate the database. Synchronous: it runs once, at boot,
    before anything is serving, and sqlite3 is already in the stdlib."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path)
    try:
        # WAL is a property of the FILE and survives; busy_timeout is a
        # property of the CONNECTION and is set again in _connect() for every
        # async operation.
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")

        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise SchemaTooNew(
                f"{path} is at schema version {version}; this build only knows "
                f"{SCHEMA_VERSION}. Refusing to open it read-write."
            )

        for target in range(version + 1, SCHEMA_VERSION + 1):
            for statement in MIGRATIONS[target]:
                conn.execute(statement)
            # Not parameterisable -- PRAGMA takes a literal. `target` is an int
            # from range() over this module's own dict, never from input.
            conn.execute(f"PRAGMA user_version = {target}")
            log.info("builds database migrated to schema version %d", target)
        conn.commit()
    finally:
        conn.close()

    log.info("builds database ready at %s (schema v%d)", path, SCHEMA_VERSION)


@asynccontextmanager
async def _connect(path: Path):
    async with aiosqlite.connect(str(path)) as conn:
        await conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        yield conn


# --- the edit token --------------------------------------------------------


def new_edit_token() -> str:
    return secrets.token_urlsafe(16)


def hash_token(token: str) -> str:
    """SHA-256, unsalted, and that is the right call here rather than a
    password KDF.

    A KDF exists to make guessing a LOW-entropy secret expensive. This token is
    16 bytes straight out of secrets.token_urlsafe -- 128 bits with no
    dictionary behind it -- so there is nothing to guess and the only threat a
    hash defends against is someone reading the database file: a backup, a
    stray `docker cp`, the restic repo on B2. Storing the token itself would
    hand that reader edit rights over every build in it.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _authorised(stored_hash: str | None, author_id: str | None, token: str | None, user: Mapping | None) -> bool:
    """Two paths, deliberately, and neither is a fallback for the other.

    The token always works: the frontend has no login and stores it in
    localStorage. The session works additionally, so that the person who made a
    build still owns it after clearing site data or moving to another device --
    the case the token alone cannot cover once Discord login lands.
    """
    if token and stored_hash and secrets.compare_digest(hash_token(token), stored_hash):
        return True
    # `author_id` is NULL for an anonymous build. Without this guard a
    # logged-in stranger would match every one of them.
    if user is not None and author_id:
        return str(user.get("id") or "") == author_id
    return False


# --- sanitising ------------------------------------------------------------


def _number(value: object) -> int | float | None:
    """JavaScript's `Number()`, near enough, or None for its NaN.

    Mirrors lib/types.ts sanitizeShdPerks, which does `Number(raw)` and then
    `Number.isFinite`. Bools are not special-cased because `Number(true)` is 1
    and Python agrees. Ints are returned unconverted: `float(10**400)` raises
    OverflowError, and a hostile payload can carry an integer that large.
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if value is None:
        return 0
    if isinstance(value, str):
        try:
            parsed = float(value.strip() or "0")
        except (ValueError, OverflowError):
            return None
        return parsed if math.isfinite(parsed) else None
    return None


def _round_half_up(value: int | float) -> int:
    """Math.round(), not Python's round().

    Python rounds halves to even -- round(2.5) is 2 -- and JavaScript rounds
    them up. The two sanitisers would then disagree on exactly the values a
    fuzzer sends, and the disagreement would only show as a build that changes
    by one point when it is saved.
    """
    if isinstance(value, int):
        return value
    return math.floor(value + 0.5)


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def sanitise_shd_perks(value: object) -> dict[str, dict[str, int]]:
    """sanitizeShdPerks() from lib/types.ts, server-side.

    Clamps to 0-50, rounds, drops unknown node and stat keys, defaults every
    missing one to 0. The frontend has the same function and this is not
    redundant with it: the frontend's copy protects the frontend's own state,
    and anything at all can send this endpoint a body.
    """
    source = value if isinstance(value, Mapping) else {}
    perks: dict[str, dict[str, int]] = {}
    for node, stats in SHD_NODES.items():
        raw_node = source.get(node)
        levels = raw_node if isinstance(raw_node, Mapping) else {}
        perks[node] = {}
        for stat in stats:
            number = _number(levels.get(stat))
            perks[node][stat] = 0 if number is None else _clamp(_round_half_up(number), 0, SHD_STAT_MAX)
    return perks


def sanitise_loadout(value: object) -> dict[str, str | None]:
    """The twelve BUILD_SLOT_KEYS and nothing else; anything that is not a
    string becomes null, exactly as the mock handler does."""
    source = value if isinstance(value, Mapping) else {}
    loadout: dict[str, str | None] = {}
    for key in BUILD_SLOT_KEYS:
        raw = source.get(key)
        loadout[key] = raw[:SLOT_VALUE_MAX_LEN] if isinstance(raw, str) else None
    return loadout


def _clean_name(value: object) -> str:
    text = value.strip() if isinstance(value, str) else ""
    return (text or DEFAULT_NAME)[:NAME_MAX_LEN]


def _clean_notes(value: object) -> str:
    return value[:NOTES_MAX_LEN] if isinstance(value, str) else ""


def _clean_shd_level(value: object) -> int:
    # `typeof body.shdLevel === 'number' ? body.shdLevel : 1` -- a value that
    # is not a number falls back to the default rather than to zero, so a
    # client sending junk does not silently reset a build to SHD 0.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return SHD_LEVEL_DEFAULT
    if isinstance(value, float) and not math.isfinite(value):
        return SHD_LEVEL_DEFAULT
    return _clamp(_round_half_up(value), 0, SHD_LEVEL_MAX)


def _is_str(value: object) -> bool:
    return isinstance(value, str)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_mapping(value: object) -> bool:
    return isinstance(value, Mapping)


# json key -> (column, "may this key be applied by a PATCH?", cleaner).
#
# The predicate is the mock handler's own type gate: a PATCH that sends
# `name: 123` must leave the name alone rather than reset it to the default,
# because a partial update with a wrong type is a client bug and losing the
# user's data over it is not an acceptable response to one.
_FIELDS: tuple[tuple[str, str, Any, Any], ...] = (
    ("name", "name", _is_str, _clean_name),
    ("notes", "notes", _is_str, _clean_notes),
    ("shdLevel", "shd_level", _is_number, _clean_shd_level),
    ("shdPerks", "shd_perks", _is_mapping, sanitise_shd_perks),
    ("loadout", "loadout", _is_mapping, sanitise_loadout),
)

_JSON_COLUMNS = frozenset({"shd_perks", "loadout"})


def _stored(column: str, value: Any) -> Any:
    return json.dumps(value, separators=(",", ":")) if column in _JSON_COLUMNS else value


def _now() -> str:
    """`new Date().toISOString()`'s format, which is what the frontend's own
    mock writes and what `new Date(build.createdAt)` in builds-compare.tsx
    parses. isoformat() alone ends in "+00:00" instead of "Z"."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# --- rows in, builds out ---------------------------------------------------


def _row_to_build(row: tuple) -> dict:
    """A BUILD_PUBLIC_COLUMNS row as the frontend's `Build`.

    shdPerks and loadout are re-sanitised on the way out rather than trusted.
    They cost sixteen integer comparisons, and they mean a row written before a
    node was added to SHD_NODES still comes back with every key the perk dialog
    indexes -- instead of rendering NaN for the new one.
    """
    raw = dict(zip(BUILD_PUBLIC_COLUMNS, row))
    return {
        "id": raw["id"],
        "name": raw["name"],
        "notes": raw["notes"],
        "shdLevel": raw["shd_level"],
        "shdPerks": sanitise_shd_perks(_load_json(raw["shd_perks"], raw["id"], "shdPerks")),
        "loadout": sanitise_loadout(_load_json(raw["loadout"], raw["id"], "loadout")),
        "views": raw["views"],
        "createdAt": raw["created_at"],
        "updatedAt": raw["updated_at"],
    }


def _load_json(text: str, build_id: str, field: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        # Only this module writes these columns, so this is corruption rather
        # than input. Said loudly and once, per row: raising instead would take
        # the whole list endpoint down over a single bad row.
        log.error("build %s has unreadable %s; serving the empty default", build_id, field)
        return {}


# --- operations ------------------------------------------------------------


async def save_build(path: Path, payload: Mapping, *, user: Mapping | None = None) -> tuple[dict, str]:
    """Insert a build. Returns (build, edit_token).

    The token is returned here and nowhere else, ever: only its hash is stored,
    so this call is the only moment it exists in a form anyone can use.
    """
    values = {column: cleaner(payload.get(key)) for key, column, _gate, cleaner in _FIELDS}
    token = new_edit_token()
    now = _now()

    columns = (
        "id", *values, "views", "created_at", "updated_at",
        "edit_token_hash", "author_id", "author_name",
    )
    placeholders = ", ".join("?" for _ in columns)

    async with _connect(path) as conn:
        for attempt in range(3):
            build_id = secrets.token_urlsafe(8)
            row = (
                build_id,
                *[_stored(column, value) for column, value in values.items()],
                0,
                now,
                now,
                hash_token(token),
                str(user["id"]) if user else None,
                str(user["name"]) if user else None,
            )
            try:
                cursor = await conn.execute(
                    f"INSERT INTO builds ({', '.join(columns)}) VALUES ({placeholders}) "
                    f"RETURNING {_PUBLIC_SQL}",
                    row,
                )
                inserted = await cursor.fetchone()
            except sqlite3.IntegrityError:
                # 64 bits of id against a table this size makes a collision
                # implausible rather than impossible, and the cost of being
                # wrong is one person's build overwriting another's.
                log.warning("build id collision on attempt %d; retrying", attempt + 1)
                continue
            await conn.commit()
            break
        else:
            raise RuntimeError("could not allocate a unique build id in 3 attempts")

    return _row_to_build(inserted), token


async def get_build(path: Path, build_id: str) -> dict:
    """Read a build and count the read.

    The increment and the read are one statement so two concurrent readers
    cannot both return the same number; the frontend's mock does the same
    thing, and the count is the only signal a shared build has.
    """
    async with _connect(path) as conn:
        cursor = await conn.execute(
            f"UPDATE builds SET views = views + 1 WHERE id = ? RETURNING {_PUBLIC_SQL}",
            (build_id,),
        )
        row = await cursor.fetchone()
        await conn.commit()
    if row is None:
        raise BuildNotFound(build_id)
    return _row_to_build(row)


async def _owner_of(conn, build_id: str) -> tuple[str, str | None]:
    cursor = await conn.execute(
        "SELECT edit_token_hash, author_id FROM builds WHERE id = ?", (build_id,)
    )
    row = await cursor.fetchone()
    if row is None:
        raise BuildNotFound(build_id)
    return row[0], row[1]


async def update_build(
    path: Path,
    build_id: str,
    payload: Mapping,
    *,
    edit_token: str | None,
    user: Mapping | None = None,
) -> dict:
    """Apply the fields the payload names. Raises BuildNotFound / NotAuthorised.

    Absent before forbidden, so the two are told apart the same way the mock
    tells them apart -- a build id is public, so 404-ing an id that exists
    would buy nothing and cost the caller the reason.
    """
    updates = {
        column: cleaner(payload[key])
        for key, column, gate, cleaner in _FIELDS
        if key in payload and gate(payload[key])
    }
    updates["updated_at"] = _now()
    assignments = ", ".join(f"{column} = ?" for column in updates)

    async with _connect(path) as conn:
        stored_hash, author_id = await _owner_of(conn, build_id)
        if not _authorised(stored_hash, author_id, edit_token, user):
            raise NotAuthorised(build_id)
        cursor = await conn.execute(
            f"UPDATE builds SET {assignments} WHERE id = ? RETURNING {_PUBLIC_SQL}",
            (*[_stored(column, value) for column, value in updates.items()], build_id),
        )
        row = await cursor.fetchone()
        await conn.commit()

    if row is None:  # pragma: no cover - _owner_of already proved it is there
        raise BuildNotFound(build_id)
    return _row_to_build(row)


async def delete_build(
    path: Path, build_id: str, *, edit_token: str | None, user: Mapping | None = None
) -> None:
    async with _connect(path) as conn:
        stored_hash, author_id = await _owner_of(conn, build_id)
        if not _authorised(stored_hash, author_id, edit_token, user):
            raise NotAuthorised(build_id)
        await conn.execute("DELETE FROM builds WHERE id = ?", (build_id,))
        await conn.commit()


async def list_builds(path: Path, *, limit: int, offset: int) -> tuple[int, list[dict]]:
    """(total, page). `total` is every match, not the page length -- the
    frontend's ListResponse<T> reads it to render pagination, and reporting the
    page length there makes every result set look like one page.

    rowid breaks ties on created_at: three builds saved inside the same
    millisecond would otherwise come back in whatever order the index felt
    like, and "newest first" would be a claim rather than a guarantee.
    """
    async with _connect(path) as conn:
        cursor = await conn.execute("SELECT count(*) FROM builds")
        total = (await cursor.fetchone())[0]
        cursor = await conn.execute(
            f"SELECT {_PUBLIC_SQL} FROM builds "
            "ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        rows = await cursor.fetchall()
    return total, [_row_to_build(row) for row in rows]
