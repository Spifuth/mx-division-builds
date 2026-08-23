"""Saved builds. The first endpoints here that read and write a disk.

The contract is the frontend's, taken from its own mock handlers in
`app/api/builds/` and from `lib/types.ts`, because the UI is already coded
against it:

    POST   /api/builds        201 {id, edit_token, url, build}
    GET    /api/builds/{id}   the Build; counts a view; 404 when absent
    PATCH  /api/builds/{id}   400 without a token, 403 with a wrong one
    DELETE /api/builds/{id}   same rules; {"success": true}
    GET    /api/builds        {total, limit, offset, results}

Two things are easy to get subtly wrong here and are done deliberately.

Error bodies are `{"error": "..."}`, never FastAPI's `{"detail": ...}`.
lib/fetcher.ts reads `body.error`, so a detail key reaches the user as
"Request failed: 400" with the reason hidden -- the exact case where an error
message would have been worth having. That is also why the bodies are parsed
by hand instead of being declared as a pydantic model: a model would answer
junk with a 422 whose body is `detail`, on the one endpoint anything on the
internet can POST to.

ROUTE ORDER: every literal path must be declared before /{build_id} or FastAPI
matches the literal as an id. There is no literal segment under this prefix
today; there is nothing stopping the next one from being added in the wrong
place.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app import db
from app.auth import current_user
from app.models import Build, BuildCreated, BuildList

router = APIRouter(prefix="/api/builds")

# The mock defaults to 20 and the compare page asks for 50. The ceiling is a
# ceiling on the response, not on the table.
Limit = Query(default=20, ge=1, le=200)
Offset = Query(default=0, ge=0)


def _db_path(request: Request):
    return request.app.state.settings.db_path


def _error(status: int, message: str) -> JSONResponse:
    """`{"error": ...}`. See the module docstring -- this shape is load-bearing
    for the frontend, not a style preference."""
    return JSONResponse(status_code=status, content={"error": message})


async def _json_object(request: Request) -> dict | None:
    """The request body as a dict, or None for anything else.

    `request.json()` on a body that is not JSON raises, and an unhandled raise
    here would be a 500 written by Starlette's error middleware -- a stack
    trace in the logs for someone typing curl badly.
    """
    try:
        parsed = json.loads(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _credentials(body: dict, request: Request) -> tuple[str | None, dict | None]:
    """The edit token, if the caller sent a usable one, and the session, if
    there is one. Either can be None; the route refuses only when both are."""
    raw = body.get("edit_token")
    token = raw if isinstance(raw, str) and raw else None
    return token, current_user(request)


# --- the collection --------------------------------------------------------


@router.get("", response_model=BuildList)
async def list_builds(request: Request, limit: int = Limit, offset: int = Offset) -> BuildList:
    total, results = await db.list_builds(_db_path(request), limit=limit, offset=offset)
    return BuildList(total=total, limit=limit, offset=offset, results=results)


@router.post("", status_code=201, response_model=BuildCreated)
async def create_build(request: Request):
    body = await _json_object(request)
    if body is None:
        return _error(400, "Invalid JSON body")

    # Nothing from `body` is trusted or even read here: db.save_build cleans
    # every field it stores and ignores every field it does not know, so id,
    # views, createdAt and author_id cannot be chosen by the caller.
    build, token = await db.save_build(_db_path(request), body, user=current_user(request))
    return BuildCreated(
        id=build["id"],
        edit_token=token,
        # The page build-creator.tsx pushes into the address bar after a save.
        url=f"/build/{build['id']}",
        build=build,
    )


# --- one build -------------------------------------------------------------


@router.get("/{build_id}", response_model=Build)
async def read_build(request: Request, build_id: str):
    # A reader that is not a visit sends X-No-View-Count. The frontend's build
    # page renders twice per request -- generateMetadata and the page itself --
    # and its mock kept a separate `peekBuild` so the title lookup would not
    # count. Without this every shared link records two views per visitor.
    count_view = "x-no-view-count" not in request.headers
    try:
        return await db.get_build(_db_path(request), build_id, count_view=count_view)
    except db.BuildNotFound:
        return _error(404, "Build not found")


@router.patch("/{build_id}", response_model=Build)
async def patch_build(request: Request, build_id: str):
    body = await _json_object(request) or {}
    token, user = _credentials(body, request)
    if token is None and user is None:
        # The mock's own check, and it comes before the existence lookup so an
        # anonymous caller cannot use this endpoint to probe which ids exist.
        # A logged-in caller skips it: a session is a credential too, and
        # telling its owner they forgot one would be false.
        return _error(400, "edit_token is required")

    try:
        return await db.update_build(
            _db_path(request), build_id, body, edit_token=token, user=user
        )
    except db.BuildNotFound:
        return _error(404, "Build not found")
    except db.NotAuthorised:
        return _error(403, "Invalid edit_token")


@router.delete("/{build_id}")
async def remove_build(request: Request, build_id: str):
    body = await _json_object(request) or {}
    token, user = _credentials(body, request)
    if token is None and user is None:
        return _error(400, "edit_token is required")

    try:
        await db.delete_build(_db_path(request), build_id, edit_token=token, user=user)
    except db.BuildNotFound:
        return _error(404, "Build not found")
    except db.NotAuthorised:
        return _error(403, "Invalid edit_token")
    return {"success": True}
