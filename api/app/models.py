"""Response models. These shapes are the API's contract with a frontend that
already exists -- changing one is a breaking change, not a tidy-up.

The authority is the frontend's own `lib/types.ts`, committed at
`docs/superpowers/specs/2026-08-23-frontend-contract-types.ts`. The models here
are deliberately thin: the per-entity shapes are built in `normalise.py` and
asserted field by field in `tests/test_contract.py`, so declaring each of them
twice as a pydantic class would only give the contract a second place to drift.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class TableCount(BaseModel):
    name: str
    rows: int


class Meta(BaseModel):
    # The two fields the UI actually reads (`Meta` in types.ts). Everything
    # below them is additive: the frontend ignores unknown keys, and dropping
    # them would take away the only view of the dataset's provenance and of a
    # degraded boot that does not require shell access to the container.
    datasetVersion: str
    tables: list[TableCount]

    snapshotDate: str
    source: str
    tableCount: int
    counts: dict[str, int]

    # Which required-by-types.ts fields the dataset cannot fill. Served so the
    # UI can grey them out rather than render an invented zero as fact. Comes
    # straight from normalise.UNSUPPORTED_FIELDS -- one definition, not two.
    unsupportedFields: dict[str, list[str]]

    lastRefresh: dict | None = None
    # Non-null only when the promoted snapshot could not be loaded at boot and
    # the API fell back to the seed dataset.
    degraded: str | None = None


class ListResponse(BaseModel):
    """`lib/fetcher.ts`'s ListResponse<T>, which reads all four fields.

    `total` is the number of matches, not the length of `results`: a frontend
    needs it to render pagination, and reporting the page length there would
    silently make every result set look like one page. `limit` and `offset`
    echo the request back.

    `results` is `list[dict[str, Any]]` rather than a union of the nine entity
    models on purpose. A union would make every response a validation guess
    between shapes that share `id` and `name`, and `/api/tables/{name}` serves
    raw CSV rows through the same envelope, which no entity model accepts.
    """

    total: int
    limit: int
    offset: int
    results: list[dict[str, Any]]
