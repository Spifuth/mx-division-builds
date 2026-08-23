"""Fetch, validate, promote.

A fetch is never trusted. The live snapshot is only replaced by a candidate
that passes every check below, so when upstream is down, truncated, or serving
an error page, the API keeps answering with the last known-good data. That is
the point of the design: the source is a third party nobody here controls.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass
from datetime import date

from app.loader import REQUIRED_COLUMNS, TABLE_NAMES

log = logging.getLogger("td2-api.refresh")

# Below this fraction of the previous row count, treat the table as truncated
# rather than legitimately shrunk. Real balance patches remove a handful of
# rows; a bad fetch removes most of them.
#
# The comparison is `len(rows) < previous * MIN_ROW_RATIO`, so exactly half is
# ACCEPTED and one row below the floor is rejected. Pinned from both sides by
# test_the_row_count_floor_sits_exactly_at_half.
MIN_ROW_RATIO = 0.5

# Below this fraction of the live table's columns still present in the
# candidate's header, the body is a different table rather than an updated one.
# Same reasoning as MIN_ROW_RATIO, applied to shape instead of size: a game
# update adds or drops a column, it does not replace the header row.
#
# This is the only check that a body is *this table* at all for the 13 tables
# with no REQUIRED_COLUMNS entry. 0.5 is deliberately loose rather than strict:
# statsMapping has just 2 columns, so anything above half would reject a
# legitimate single-column drop there, while every realistic wrong-body case
# (another collection's CSV after an upstream path rename, a JSON error
# document) scores 0. Same `<` semantics as MIN_ROW_RATIO: exactly half passes.
MIN_HEADER_OVERLAP = 0.5

# The upstream version token as it actually looks: "26.0-mdb". Nothing else is
# a version, and this value is worth checking as hard as the tables are,
# because it both decides whether a refresh runs at all and is interpolated
# verbatim into a snapshot's SNAPSHOT.txt.
VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
MAX_VERSION_LENGTH = 64

# Errors that mean this code is wrong, not that upstream is. `except Exception`
# around the source calls reported a local TypeError as "upstream is down",
# which sends whoever reads /api/meta to the wrong place entirely.
PROGRAMMING_ERRORS = (AssertionError, AttributeError, ImportError, NameError, TypeError)

# An absolute filesystem path, when not preceded by a word character or a colon
# -- so "'/srv/snapshots/2026-08-23'" from an OSError matches, while the path
# component of "https://buildstation.app/api/td2/v2/data/mx" does not.
_ABS_PATH = re.compile(r"(?<![\w:/])/(?:[\w.\-]+/)+[\w.\-]*")

_MAX_REASON_LENGTH = 160


@dataclass
class RefreshResult:
    changed: bool
    version: str | None
    reasons: list[str]
    snapshot: str | None = None


def describe_error(exc: BaseException) -> str:
    """What an exception is allowed to contribute to a caller-visible reason.

    `reasons` is served by /api/meta, and CORS deliberately opens that to
    v0.dev and *.vercel.app -- third-party browser origins. Exception text is
    either upstream-controlled or, for OSError, full of local filesystem
    paths, so it is redacted and length-bounded rather than echoed. The
    unredacted original still goes to the server log, where it belongs.
    """
    text = _ABS_PATH.sub("<path>", " ".join(str(exc).split()))
    if len(text) > _MAX_REASON_LENGTH:
        text = text[: _MAX_REASON_LENGTH - 3] + "..."
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def version_rejection(version: str) -> str | None:
    """Why this version token cannot be acted on, or None if it is fine.

    The token gets no free pass just because raise_for_status() liked the
    response: a 200 with the wrong body is the same threat model the table
    checks exist for. Two concrete failures this closes:

    * An empty or whitespace token never equals the live version, so every
      poll would run a full 20-table fetch and promote. _read_marker discards
      an empty value, so the promoted snapshot reads back as "unknown", the
      next poll compares "" to "unknown", and it loops forever -- with
      write_candidate's collision suffix minting .2, .3, .4 and nothing
      pruning them.
    * A multi-line token is interpolated into SNAPSHOT.txt, so its extra lines
      become marker lines. Measured, because the obvious version of this claim
      is wrong: an injected "source:" line is written but then overridden by
      the legitimate one below it, since _read_marker takes the last value for
      a key and write_candidate emits source AFTER the version. "snapshot:" is
      emitted BEFORE it and is therefore injectable -- a token of
      "26.0-mdb\nsnapshot: 1999-01-01" made _read_marker report a snapshot
      date of 1999-01-01, which /api/meta then serves as fact.
    """
    if not version:
        return "version token is empty"
    if len(version) > MAX_VERSION_LENGTH:
        return (
            f"version token is {len(version)} characters, "
            f"over the {MAX_VERSION_LENGTH} limit"
        )
    if not VERSION_PATTERN.match(version):
        # The token itself is never echoed: it is third-party input and this
        # reason is served to browser origins.
        return "version token is not a single plain version string"
    return None


def validate_candidate(
    tables: dict[str, str],
    previous_counts: dict[str, int] | None,
    previous_headers: dict[str, list[str]] | None = None,
) -> list[str]:
    """Return the reasons a candidate is unacceptable. Empty means acceptable."""
    reasons: list[str] = []

    for name in TABLE_NAMES:
        text = tables.get(name)
        if text is None:
            reasons.append(f"{name}: missing from the fetch")
            continue

        stripped = text.lstrip()
        if stripped.startswith("<"):
            # An SPA fallback or an error page. Content-type cannot be trusted
            # here either -- this has been served as 200 text/html before.
            reasons.append(f"{name}: response is markup, not CSV")
            continue

        rows = list(csv.DictReader(io.StringIO(text)))
        if not rows:
            # A bare header row, or nothing at all. Not merely tidiness: every
            # check below indexes rows[0], so without this branch a header-only
            # body raises IndexError inside run_refresh, which has no guard --
            # turning a bad fetch into an HTTP 500 instead of a rejection.
            reasons.append(f"{name}: parsed to zero rows")
            continue

        missing = [c for c in REQUIRED_COLUMNS.get(name, []) if c not in rows[0]]
        if missing:
            reasons.append(f"{name}: lost columns {', '.join(missing)}")
            continue

        previous_header = set(previous_headers.get(name, [])) if previous_headers else set()
        if previous_header:
            # Skipped when the live dataset has no header recorded for this
            # table, which only happens before anything has been served.
            candidate_header = {c for c in rows[0] if c is not None}
            kept = previous_header & candidate_header
            if len(kept) / len(previous_header) < MIN_HEADER_OVERLAP:
                reasons.append(
                    f"{name}: header is not this table's "
                    f"({len(kept)} of {len(previous_header)} columns kept)"
                )
                continue

            # Overlap alone cannot separate tables that share a schema family:
            # the six gear slots all carry GEAR_COLUMNS, and statsMapping's two
            # columns are a subset of four other tables'. Measured over the
            # seed, 68 of the 380 possible table-for-table swaps still cleared
            # the floor above. But if a candidate's header is *exactly* another
            # live table's and not this one's, it is that table -- which decides
            # 61 of those 68. The 7 left over are pairs whose live schemas are
            # genuinely identical (chest/gloves/kneepads, gearAttributes/
            # gearMods); no check on header shape can tell those apart, and
            # separating them would need a content discriminator.
            if candidate_header != previous_header:
                twin = next(
                    (
                        other
                        for other, cols in previous_headers.items()
                        if other != name and set(cols) == candidate_header
                    ),
                    None,
                )
                if twin is not None:
                    reasons.append(f"{name}: header is exactly {twin}'s, not its own")
                    continue

        if previous_counts and name in previous_counts:
            floor = previous_counts[name] * MIN_ROW_RATIO
            if len(rows) < floor:
                reasons.append(
                    f"{name}: row count collapsed {previous_counts[name]} -> {len(rows)}"
                )

    return reasons


async def run_refresh(app, force: bool = False) -> RefreshResult:
    source = app.state.source
    store = app.state.snapshots
    current = app.state.dataset

    def finish(result: RefreshResult) -> RefreshResult:
        # Recorded on every path, not just success: /api/meta surfaces this,
        # and an admin looking at it after upstream has been down for a week
        # needs to see the rejection reason, not a stale "changed": true from
        # the last time a refresh actually worked.
        app.state.last_refresh = {
            "changed": result.changed,
            "version": result.version,
            "reasons": result.reasons,
        }
        return result

    try:
        raw_version = await source.fetch_version()
    except PROGRAMMING_ERRORS:
        raise
    except Exception as exc:  # noqa: BLE001 - upstream is a third party
        log.warning("version poll failed: %s", exc)
        return finish(
            RefreshResult(
                changed=False,
                version=None,
                reasons=[f"version poll failed: {describe_error(exc)}"],
            )
        )

    version = str(raw_version).strip() if raw_version is not None else ""
    rejection = version_rejection(version)
    if rejection:
        # Reported, not raised: a bad token is upstream misbehaving, exactly
        # like a bad table, and skipping a refresh is always safe.
        log.warning("version token rejected: %s", rejection)
        return finish(RefreshResult(changed=False, version=None, reasons=[rejection]))

    if version == current.version and not force:
        return finish(RefreshResult(changed=False, version=version, reasons=[]))

    try:
        tables = await source.fetch_tables()
    except PROGRAMMING_ERRORS:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("fetch failed: %s", exc)
        return finish(
            RefreshResult(
                changed=False,
                version=version,
                reasons=[f"fetch failed: {describe_error(exc)}"],
            )
        )

    # `force` reaches here and no further. It buys a caller one thing -- the
    # right to re-fetch a version that has not moved -- and never the right to
    # skip a check. The route that sets it is unauthenticated.
    reasons = validate_candidate(
        tables,
        previous_counts=current.counts,
        previous_headers=current.headers,
    )
    if reasons:
        log.warning("candidate rejected: %s", "; ".join(reasons))
        return finish(RefreshResult(changed=False, version=version, reasons=reasons))

    from app.loader import load_dataset

    try:
        candidate = store.write_candidate(tables, version, date.today().isoformat())
        # Loaded BEFORE it is promoted. write_candidate is not atomic -- mkdir,
        # then 20 separate writes -- so a full disk or a kill leaves a partial
        # directory behind. Loading first means such a directory can never
        # become LIVE and take the next boot down with it.
        dataset = load_dataset(candidate)
        store.promote(candidate)
        # Bounded here, not just bounded in principle. prune() shipped with a
        # test and no caller, so the store grew on every refresh anyway --
        # six directories from six development refreshes before this was spotted.
        pruned = store.prune()
        if pruned:
            log.info("pruned %d old snapshot(s)", len(pruned))
    except PROGRAMMING_ERRORS:
        raise
    except Exception as exc:  # noqa: BLE001 - disk, permissions, a partial write
        # Wrapped for the reason finish()'s comment gives: without this the
        # route 500s and app.state.last_refresh still advertises the previous
        # success as though it were this attempt. That is not hypothetical --
        # a PermissionError on /srv/snapshots did exactly this.
        log.exception("could not promote a validated candidate")
        return finish(
            RefreshResult(
                changed=False,
                version=version,
                reasons=[f"promotion failed: {describe_error(exc)}"],
            )
        )

    app.state.dataset = dataset
    # A successful promotion means the degraded banner is no longer true. It was
    # set at boot and never cleared, so /api/meta kept reporting "serving the
    # seed dataset instead" after a refresh had already fixed it -- the one
    # operator-visible signal that the data is not what it claims, lying.
    app.state.degraded = None
    log.info("promoted snapshot %s (version %s)", candidate.name, version)
    return finish(
        RefreshResult(changed=True, version=version, reasons=[], snapshot=candidate.name)
    )
