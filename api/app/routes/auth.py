"""The real endpoints from day one; only the identity provider is fake.

/login, /callback, /me and /logout are the shapes the frontend is built
against. Swapping TD2_AUTH_MODE from mock to discord changes which provider
answers /login and nothing else on this side of the wire.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse

from app.auth import SESSION_COOKIE, SESSION_MAX_AGE, current_user, serializer

router = APIRouter(prefix="/api/auth")


@router.get("/login")
def login(
    request: Request,
    as_: str = Query(
        "tester",
        alias="as",
        description="Mock mode only: the identity to fabricate. Ignored by Discord.",
    ),
) -> RedirectResponse:
    provider = request.app.state.auth
    target, user = provider.login_target(as_)

    redirect = RedirectResponse(target, status_code=307)
    if user is None:
        # Discord. Nothing is signed here -- the session is minted at the
        # callback, once the code has been exchanged for a real identity.
        return redirect

    redirect.set_cookie(
        SESSION_COOKIE,
        serializer(request.app.state.settings).dumps(user),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=True,
        path="/",
    )
    return redirect


@router.get("/callback")
async def callback(request: Request):
    """Deliberately unimplemented.

    The token exchange cannot be tested without real credentials, and untested
    OAuth code is worse than an honest 501: it would be written once, never
    exercised, and trusted on the day it first runs. What must not happen while
    it is missing is a callback that returns 200 with a session, so this raises
    rather than falling through to anything.
    """
    provider = request.app.state.auth
    if provider.mode == "mock":
        raise HTTPException(status_code=404, detail="no callback in mock mode")
    raise HTTPException(
        status_code=501,
        detail="the Discord token exchange lands with the real credentials",
    )


@router.get("/me")
def me(request: Request) -> dict:
    """The frontend's one auth call.

    `mode` is what the server is running now; `user.mode` is what minted this
    session. They cannot disagree in a served response -- read_session drops a
    session from the other provider -- but both are reported so a client can
    show "this is a fake login" without inferring it from an id prefix.
    """
    return {"user": current_user(request), "mode": request.app.state.auth.mode}


@router.post("/logout")
def logout() -> Response:
    """POST, not GET: a link-shaped logout is a one-pixel image away from being
    triggered by anyone. Same attributes as the cookie it clears, so a browser
    that scopes on them still matches."""
    response = Response(status_code=204)
    response.delete_cookie(
        SESSION_COOKIE, path="/", httponly=True, samesite="lax", secure=True
    )
    return response
