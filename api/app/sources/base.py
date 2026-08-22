"""What a data source has to provide.

The Division 2 has no official API, and buildstation.app is the only source
today. The owner intends to replace it. Keeping the surface this small is what
makes that a sibling module and one config value rather than a rewrite.
"""

from __future__ import annotations

from typing import Protocol


class DataSource(Protocol):
    name: str

    async def fetch_version(self) -> str:
        """A cheap token that changes when the game data changes.

        Polled often; must not require pulling the whole dataset.
        """

    async def fetch_tables(self) -> dict[str, str]:
        """Every table, as raw CSV text keyed by table name.

        Raw text, not parsed rows: the bytes as served are the authoritative
        artefact, and parsing is the validator's job, not the source's.
        """
