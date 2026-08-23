"""Phase 2 lands here: the damage math, ported from src/utils/statsService.js.

501 rather than 404 on purpose -- the frontend wires the call now, and a 404
would be indistinguishable from a wrong path. When Phase 2 lands, no frontend
change is needed.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api")


@router.post("/compute")
def compute(payload: dict) -> dict:
    raise HTTPException(
        status_code=501,
        detail="Damage computation is Phase 2 and is not implemented yet.",
    )
