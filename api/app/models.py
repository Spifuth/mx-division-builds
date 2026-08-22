"""Response models. These shapes are the API's contract with a frontend that is
being written in parallel -- changing one is a breaking change, not a tidy-up."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class Meta(BaseModel):
    version: str
    snapshot_date: str
    source: str
    table_count: int
    counts: dict[str, int]
    tables: list[str]
    last_refresh: dict | None = None
    # Non-null only when the promoted snapshot could not be loaded at boot and
    # the API fell back to the seed dataset. Additive and defaulted, so it is
    # not a breaking change for the frontend being written in parallel -- but
    # it is the only place that situation is visible without shell access to
    # the container's logs.
    degraded: str | None = None


class RawTable(BaseModel):
    name: str
    count: int
    rows: list[dict[str, str]]


class RowList(BaseModel):
    count: int
    total: int
    # Values are `Any`, not `str`: every table-backed endpoint only ever puts
    # plain CSV strings here, but /brands joins in a nested `bonuses` list per
    # row, and `dict[str, str]` rejects that -- 66/66 brand rows failed
    # response_model validation before this widened. Existing endpoints are
    # unaffected: they still only ever emit strings.
    rows: list[dict[str, Any]]
