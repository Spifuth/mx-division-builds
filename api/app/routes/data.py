"""Reference-data endpoints. Every one reads from memory; none touches disk."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.loader import TABLE_NAMES, Dataset
from app.models import Meta, RawTable

router = APIRouter(prefix="/api")


def dataset(request: Request) -> Dataset:
    return request.app.state.dataset


@router.get("/meta", response_model=Meta)
def meta(request: Request) -> Meta:
    data = dataset(request)
    return Meta(
        version=data.version,
        snapshot_date=data.snapshot_date,
        source=data.source,
        table_count=len(data.tables),
        counts=data.counts,
        tables=sorted(data.tables),
    )


@router.get("/tables/{name}", response_model=RawTable)
def raw_table(
    request: Request,
    name: str,
    limit: int = Query(default=0, ge=0, le=5000),
    offset: int = Query(default=0, ge=0),
) -> RawTable:
    if name not in TABLE_NAMES:
        raise HTTPException(status_code=404, detail=f"unknown table {name!r}")
    rows = dataset(request).tables[name]
    window = rows[offset : offset + limit] if limit else rows[offset:]
    return RawTable(name=name, count=len(window), rows=window)
