"""Response models. These shapes are the API's contract with a frontend that is
being written in parallel -- changing one is a breaking change, not a tidy-up."""

from __future__ import annotations

from pydantic import BaseModel


class Meta(BaseModel):
    version: str
    snapshot_date: str
    source: str
    table_count: int
    counts: dict[str, int]
    tables: list[str]


class RawTable(BaseModel):
    name: str
    count: int
    rows: list[dict[str, str]]


class RowList(BaseModel):
    count: int
    total: int
    rows: list[dict[str, str]]
