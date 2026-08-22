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
from dataclasses import dataclass
from datetime import date

from app.loader import REQUIRED_COLUMNS, TABLE_NAMES

log = logging.getLogger("td2-api.refresh")

# Below this fraction of the previous row count, treat the table as truncated
# rather than legitimately shrunk. Real balance patches remove a handful of
# rows; a bad fetch removes most of them.
MIN_ROW_RATIO = 0.5


@dataclass
class RefreshResult:
    changed: bool
    version: str | None
    reasons: list[str]
    snapshot: str | None = None


def validate_candidate(
    tables: dict[str, str], previous_counts: dict[str, int] | None
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
            reasons.append(f"{name}: parsed to zero rows")
            continue

        missing = [c for c in REQUIRED_COLUMNS.get(name, []) if c not in rows[0]]
        if missing:
            reasons.append(f"{name}: lost columns {', '.join(missing)}")
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
        version = await source.fetch_version()
    except Exception as exc:  # noqa: BLE001 - upstream is a third party
        log.warning("version poll failed: %s", exc)
        return finish(
            RefreshResult(changed=False, version=None, reasons=[f"version poll failed: {exc}"])
        )

    if version == current.version and not force:
        return finish(RefreshResult(changed=False, version=version, reasons=[]))

    try:
        tables = await source.fetch_tables()
    except Exception as exc:  # noqa: BLE001
        log.warning("fetch failed: %s", exc)
        return finish(
            RefreshResult(changed=False, version=version, reasons=[f"fetch failed: {exc}"])
        )

    reasons = validate_candidate(tables, previous_counts=current.counts)
    if reasons:
        log.warning("candidate rejected: %s", "; ".join(reasons))
        return finish(RefreshResult(changed=False, version=version, reasons=reasons))

    candidate = store.write_candidate(tables, version, date.today().isoformat())
    store.promote(candidate)

    from app.loader import load_dataset

    app.state.dataset = load_dataset(candidate)
    log.info("promoted snapshot %s (version %s)", candidate.name, version)
    return finish(
        RefreshResult(changed=True, version=version, reasons=[], snapshot=candidate.name)
    )
