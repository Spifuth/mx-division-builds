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


class Build(BaseModel):
    """`Build` from types.ts, and the one shape in this file worth declaring
    twice.

    The other entity shapes are built in normalise.py from CSV rows and are
    asserted field by field in tests/test_contract.py, so a pydantic class for
    each of them would only be a second place for the contract to drift. This
    one is different in two ways: the API *produces* it rather than adapting
    it, so there is no CSV to check it against -- and it is the record that a
    secret is stored beside.

    FastAPI serialises a response through this model and drops anything not
    declared on it, which makes `edit_token`/`edit_token_hash` unable to reach
    a client even if some future SELECT starts fetching the column. db.py's
    BUILD_PUBLIC_COLUMNS is the first guard; this is the independent second.
    """

    id: str
    name: str
    notes: str
    shdLevel: int
    # Node key -> stat key -> level, 0-50. Deliberately not a nested model per
    # node: SHD_NODES in db.py is the single definition of which keys exist,
    # and repeating it as pydantic classes would let the two disagree.
    shdPerks: dict[str, dict[str, int]]
    loadout: dict[str, str | None]
    views: int
    createdAt: str
    updatedAt: str


class BuildCreated(BaseModel):
    """The 201 body. `edit_token` appears here and on no other response in the
    API -- it is minted, returned once, and only its hash is kept."""

    id: str
    edit_token: str
    url: str
    build: Build


class BuildList(ListResponse):
    """ListResponse<Build>.

    The frontend's own mock handler returns a bare `{results}`, but
    builds-compare.tsx declares the response as `ListResponse<Build>`, which
    reads all four fields -- so the mock is the thing that disagrees with the
    frontend's type. Matching the type is safe: SWR only reads `.results`.

    `results` narrows the base class's `list[dict[str, Any]]` to `list[Build]`
    on purpose. A bare dict list would pass any key straight through, including
    one named edit_token.
    """

    results: list[Build]
