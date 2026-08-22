"""Dated snapshots on disk, with an explicit live pointer.

Snapshots are kept rather than overwritten so a rollback does not require
re-fetching from the source that just broke -- which is exactly when a rollback
is needed.
"""

from __future__ import annotations

from pathlib import Path


class SnapshotStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._pointer = self.root / "LIVE"

    def write_candidate(self, tables: dict[str, str], version: str, date: str) -> Path:
        """Write a snapshot without making it live. Nothing served changes here."""
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

    def list(self) -> list[Path]:
        return sorted(p for p in self.root.iterdir() if p.is_dir())
