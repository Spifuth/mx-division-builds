"""Manual refresh trigger.

Deliberately not authenticated: the service is tailnet-only and has no public
route. That stops being acceptable the moment it is exposed publicly, which is
recorded in the spec's out-of-scope section.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.refresh import run_refresh

router = APIRouter(prefix="/api/admin")


@router.post("/refresh")
async def refresh(request: Request, force: bool = False) -> dict:
    result = await run_refresh(request.app, force=force)
    return {
        "changed": result.changed,
        "version": result.version,
        "snapshot": result.snapshot,
        "reasons": result.reasons,
    }
