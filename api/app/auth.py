"""Discord OAuth, with a mock provider for building the frontend first.

Mirrors Heolstor/web/auth.py: itsdangerous-signed session cookies, OAuth state
with a short expiry, and an exact-match redirect URI because that is how Discord
compares it. When the owner registers the Discord app, two environment variables
change and nothing in the frontend does.

Two things are taken from Heolstor's scar tissue up front. Snowflakes are kept
as STRINGS everywhere -- they exceed JavaScript's safe-integer limit, and
Heolstor needed a coercion shim because its web and bot halves disagreed on the
type. And mock identities carry a prefix no real snowflake can have, so every
mock-owned row is findable in one query at cutover; Heolstor grew
adopt_orphan_builds because that question arrived unannounced.

The design constraint above all the others: a mock that is left on must be
impossible to miss. Four things say so at once -- a WARNING at boot, an
X-Auth-Mode header on every response (exposed through CORS so the browser can
read it too), the mode in every /api/auth/me body, and the mode recorded inside
each session so a mock identity cannot outlive the cutover to real Discord.
"""

from __future__ import annotations

import logging
import re
import secrets
import time
from collections.abc import Mapping
from urllib.parse import urlencode

from itsdangerous import BadData, URLSafeTimedSerializer

from app.config import Settings

log = logging.getLogger("td2-api.auth")

MOCK_ID_PREFIX = "mock:"
SESSION_COOKIE = "td2_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 7
OAUTH_STATE_MAX_AGE = 300

# /api/auth/login is unauthenticated and every call parks a state for five
# minutes, so the dict needs a ceiling or it is a memory-growth vector for
# anyone who can reach the service. Far above any real number of logins in
# flight at once.
MAX_PENDING_STATES = 1024

DISCORD_AUTHORIZE = "https://discord.com/api/v10/oauth2/authorize"
DISCORD_TOKEN = "https://discord.com/api/v10/oauth2/token"
DISCORD_USER = "https://discord.com/api/v10/users/@me"

# The mock's display name comes straight off a query string. It ends up inside
# a signed cookie that the browser then sends on every request, so it is bounded
# and stripped to characters that cannot be mistaken for structure.
_UNSAFE_IN_A_MOCK_NAME = re.compile(r"[^A-Za-z0-9 _.-]")
MOCK_NAME_MAX_LEN = 32


class AuthConfigError(RuntimeError):
    """The auth configuration is unsafe or incomplete. Always fatal at boot."""


def is_mock_id(value: object) -> bool:
    """True for identities minted by the mock provider.

    This is the cutover query. A Discord snowflake is decimal digits only, so
    no real id can begin with a prefix whose first character is not a digit --
    every mock-owned row is findable with one `LIKE 'mock:%'` and no guessing.
    """
    return isinstance(value, str) and value.startswith(MOCK_ID_PREFIX)


def session_from_discord_user(data: Mapping) -> dict:
    """The session payload for a real Discord identity.

    The only place a Discord id enters the system, and the only place it is
    coerced. `str` is not decoration: snowflakes are 64-bit and every one of
    them is past JavaScript's 2**53-1 safe integer, so a single hop through
    JSON.parse -- or through a bot half that stored them as ints -- rounds the
    id to a different user's. Heolstor needed a shim to reconcile the two.

    Called by the Discord callback when it lands. It is written and tested now,
    without credentials, because it is the half of the exchange that can be.
    """
    raw = data.get("id")
    if raw is None or str(raw).strip() == "":
        raise ValueError("Discord user payload has no id")
    name = data.get("global_name") or data.get("username") or str(raw)
    return {"id": str(raw), "name": str(name), "mode": "discord"}


class MockProvider:
    """Every identity here is a fabrication. That is the whole point, and it is
    why build_provider() will not construct one without two separate switches."""

    mode = "mock"

    def login_target(self, name: str | None = None) -> tuple[str, dict | None]:
        clean = _UNSAFE_IN_A_MOCK_NAME.sub("", (name or "").strip())[:MOCK_NAME_MAX_LEN].strip()
        clean = clean or "tester"
        user = {"id": f"{MOCK_ID_PREFIX}{clean}", "name": clean, "mode": self.mode}
        return "/api/auth/me", user


class DiscordProvider:
    mode = "discord"

    def __init__(self, settings: Settings) -> None:
        # Sent verbatim, never derived from the Host header. Heolstor resolves
        # this per-request against an allowlist because one container there
        # serves several hostnames; this service has exactly one, so the
        # allowlist is a single exact string and nothing attacker-controlled
        # reaches it.
        #
        # Stripped, because Discord compares redirect_uri byte for byte and a
        # secret store that hands back a trailing newline produces an
        # "invalid redirect URI" with nothing visible to explain it.
        self.client_id = settings.discord_client_id.strip()
        self.client_secret = settings.discord_client_secret.strip()
        self.redirect_uri = settings.discord_redirect_uri.strip()
        self._states: dict[str, float] = {}

    def login_target(self, name: str | None = None) -> tuple[str, None]:
        now = time.time()
        self._states = {s: t for s, t in self._states.items() if now - t < OAUTH_STATE_MAX_AGE}
        if len(self._states) >= MAX_PENDING_STATES:
            oldest = sorted(self._states, key=self._states.__getitem__)
            for stale in oldest[: len(self._states) - MAX_PENDING_STATES + 1]:
                self._states.pop(stale, None)
        state = secrets.token_urlsafe(32)
        self._states[state] = now
        # urlencode, not an f-string: the configured redirect URI may carry its
        # own query string or a port, and pasting it raw would split the
        # authorize URL's parameters. Discord matches redirect_uri as an exact
        # string, so a mangled one fails with an error page and no explanation.
        query = urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "response_type": "code",
                "scope": "identify",
                "state": state,
            }
        )
        return f"{DISCORD_AUTHORIZE}?{query}", None

    def consume_state(self, state: str) -> bool:
        """Single use. Popped whether or not it turns out to be fresh, so a
        replayed callback cannot be retried until the clock agrees."""
        issued = self._states.pop(state, None)
        return issued is not None and time.time() - issued < OAUTH_STATE_MAX_AGE


def build_provider(settings: Settings):
    """Resolve the provider, or refuse to run.

    Every failure here is fatal on purpose, and it is raised from create_app()
    rather than from the lifespan so the process never reaches a listening
    socket. An auth layer that degrades to something permissive when
    misconfigured is worse than one that will not start, because nothing
    downstream can tell the difference -- and one that starts and then rejects
    everyone is indistinguishable from the outside from one that starts and
    accepts everyone.
    """
    # .strip() throughout: an all-whitespace value is what an unset variable
    # looks like after it has been through a template, a secret store or a
    # shell, and " " passing `if not value` would be a credential check that
    # accepts the absence of a credential.
    if not settings.session_secret.strip():
        raise AuthConfigError("TD2_SESSION_SECRET is required to sign sessions")

    mode = settings.auth_mode.strip().lower()

    if mode == "mock":
        if not settings.allow_mock_auth:
            raise AuthConfigError(
                "AUTH_MODE=mock also requires TD2_ALLOW_MOCK_AUTH=1. Two switches on "
                "purpose: one typo or one copied env file must not be enough to "
                "turn real identity off."
            )
        log.warning(
            "AUTH IS MOCKED - every identity is fake, /api/auth/login trusts a query "
            "string, and every id it mints is prefixed %r. Do not expose this. "
            "Set TD2_AUTH_MODE=discord with real credentials before anyone else "
            "can reach the service.",
            MOCK_ID_PREFIX,
        )
        return MockProvider()

    if mode == "discord":
        missing = [
            name
            for name, value in (
                ("TD2_DISCORD_CLIENT_ID", settings.discord_client_id),
                ("TD2_DISCORD_CLIENT_SECRET", settings.discord_client_secret),
                ("TD2_DISCORD_REDIRECT_URI", settings.discord_redirect_uri),
            )
            if not value.strip()
        ]
        if missing:
            raise AuthConfigError(f"AUTH_MODE=discord requires: {', '.join(missing)}")
        return DiscordProvider(settings)

    raise AuthConfigError(
        f"unknown TD2_AUTH_MODE {settings.auth_mode!r} - expected 'discord' or 'mock'"
    )


def serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt="td2-session")


def read_session(request) -> dict | None:
    """The session cookie, or None. Never raises on a bad cookie.

    Anyone can put anything in a cookie jar, so every rejection path here ends
    in "you are logged out" rather than an exception. BadData is the base class
    on purpose: BadPayload does NOT inherit from BadSignature, so catching only
    BadSignature turns a truncated cookie into a 500.
    """
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    try:
        payload = serializer(request.app.state.settings).loads(token, max_age=SESSION_MAX_AGE)
    except BadData:
        return None

    if not isinstance(payload, Mapping):
        return None
    user_id, name, mode = payload.get("id"), payload.get("name"), payload.get("mode")
    if not all(isinstance(v, str) and v for v in (user_id, name, mode)):
        return None

    running = request.app.state.auth.mode
    if mode != running:
        # The cutover. The signing key does not change when AUTH_MODE does, so
        # every outstanding mock cookie stays cryptographically valid -- and
        # would keep authenticating fake people under AUTH_MODE=discord, with
        # the mode header now reassuringly reading "discord". Drop it loudly.
        log.warning(
            "refusing a %s session while running in %s mode (id %r); the cutover "
            "invalidates every session minted by the other provider",
            mode,
            running,
            user_id,
        )
        return None
    return {"id": user_id, "name": name, "mode": mode}


def current_user(request) -> dict | None:
    """The identity behind this request, or None. Keys: id, name, mode.

    `mode` is the provider that MINTED this identity, not the one running now.
    They can only disagree across a cutover, and read_session refuses the
    session in that case -- so the field exists to make a fake identity
    self-describing wherever it is stored, not to be trusted as current config.
    """
    return read_session(request)
