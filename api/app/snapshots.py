"""Dated snapshots on disk, with an explicit live pointer.

Snapshots are kept rather than overwritten so a rollback does not require
re-fetching from the source that just broke -- which is exactly when a rollback
is needed.
"""

from __future__ import annotations

import shutil
from pathlib import Path


class SnapshotStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._pointer = self.root / "LIVE"

    def write_candidate(self, tables: dict[str, str], version: str, date: str) -> Path:
        """Write a snapshot without making it live. Nothing served changes here."""
        # `date` names a directory under root, so it has to be a plain segment.
        # Two concrete failures if it is not: an absolute string makes pathlib's
        # `/` discard root entirely and write outside the store, and the literal
        # "LIVE" creates a directory where the pointer file goes, after which
        # every promote() raises IsADirectoryError. Neither is reachable from
        # today's callers -- date is always a server-generated ISO date -- which
        # is exactly when a guard is cheap to add.
        if not date or "/" in date or "\\" in date or date in {".", "..", self._pointer.name}:
            raise ValueError(f"invalid snapshot date {date!r}")
        target = self.root / date
        suffix = 1
        while target.exists():
            suffix += 1
            target = self.root / f"{date}.{suffix}"
        target.mkdir(parents=True)

        for name, text in tables.items():
            (target / f"{name}.csv").write_text(text, encoding="utf-8")

        (target / "SNAPSHOT.txt").write_text(
            "\n".join(
                [
                    f"snapshot: {date}",
                    f"upstream DB.Version: {version}",
                    "source: https://buildstation.app/api/td2/v2/data/mx",
                    f"tables: {len(tables)}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return target

    def promote(self, snapshot: Path) -> None:
        snapshot = Path(snapshot)
        if not snapshot.is_dir():
            raise FileNotFoundError(f"no snapshot at {snapshot}")
        self._pointer.write_text(snapshot.name, encoding="utf-8")

    def live(self) -> Path | None:
        if not self._pointer.is_file():
            return None
        candidate = self.root / self._pointer.read_text(encoding="utf-8").strip()
        return candidate if candidate.is_dir() else None

    def prune(self, keep: int = 10) -> list[Path]:
        """Drop the oldest snapshots, never the live one.

        Snapshots are kept so a rollback does not need the upstream that just
        broke -- but "kept" cannot mean "kept forever". Every promoted refresh
        writes a new directory, so on a timer this grows without bound; four
        manual refreshes during development already produced 2026-08-22 through
        .4 at ~300 KB each.

        The live snapshot is exempt regardless of age. Pruning the directory
        that LIVE points at would leave a dangling pointer, which live() handles
        by returning None -- meaning a tidy-up would silently drop the service
        back to the seed dataset.
        """
        if keep < 1:
            raise ValueError("keep must be at least 1")

        live = self.live()
        candidates = [p for p in self.list() if p != live]
        # Oldest first: the newest `keep` survive, minus the live slot if the
        # live snapshot is not already among them.
        surplus = candidates[: max(0, len(candidates) - keep + (1 if live else 0))]
        for path in surplus:
            shutil.rmtree(path)
        return surplus

    def list(self) -> list[Path]:
        return sorted(p for p in self.root.iterdir() if p.is_dir())
