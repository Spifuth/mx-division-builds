"""Auth: the mock provider must be impossible to leave on unnoticed.

A mock that silently accepts everyone in production is worse than no auth, so
every test here is written to be able to fail. The three safety properties --
boot refuses without Discord credentials, mock needs two switches, and the mode
is stamped on every response -- were each watched red twice: once before the
code existed, and once again with that specific guard deleted from a working
implementation. That second run is the one that matters; four checks have
already shipped in this repo that could not have failed.
"""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app.auth import (
    MOCK_ID_PREFIX,
    OAUTH_STATE_MAX_AGE,
    SESSION_COOKIE,
    AuthConfigError,
    DiscordProvider,
    build_provider,
    is_mock_id,
    serializer,
    session_from_discord_user,
)
from app.config import Settings
from app.main import create_app

AUTH_ENV = (
    "TD2_AUTH_MODE",
    "TD2_ALLOW_MOCK_AUTH",
    "TD2_SESSION_SECRET",
    "TD2_DISCORD_CLIENT_ID",
    "TD2_DISCORD_CLIENT_SECRET",
    "TD2_DISCORD_REDIRECT_URI",
)


@pytest.fixture(autouse=True)
def _no_ambient_auth_env(monkeypatch):
    """Start every test in this file from no auth configuration at all.

    docker-compose.dev.yml sets TD2_AUTH_MODE=mock for the whole suite, because
    the other 170 tests call create_app() and create_app() now refuses to build
    an unconfigured auth layer. Inherited here that ambient value would mask a
    test that forgot to set the mode it claims to exercise: the test would then
    be passing on the environment rather than on the code.
    """
    for name in AUTH_ENV:
        monkeypatch.delenv(name, raising=False)


def _mock_env(monkeypatch) -> None:
    monkeypatch.setenv("TD2_AUTH_MODE", "mock")
    monkeypatch.setenv("TD2_ALLOW_MOCK_AUTH", "true")
    monkeypatch.setenv("TD2_SESSION_SECRET", "test-secret")


def _cookie(token: str) -> dict:
    """Plant a session by header.

    NOT client.cookies.set(name, value, domain="testserver"): measured inside
    the container, httpx's jar accepts that call and then never sends the
    cookie back. Every negative test below asserts "this cookie yields no
    user", which is also exactly what happens when no cookie is sent at all --
    so four of them passed against a deliberately broken implementation until
    a mutation run showed they could not fail. test_a_planted_cookie_actually
    _reaches_the_server is the control that keeps this honest.
    """
    return {"Cookie": f"{SESSION_COOKIE}={token}"}


def _state_of(url: str) -> str:
    return parse_qs(urlsplit(url).query)["state"][0]


def _discord_env(monkeypatch) -> None:
    monkeypatch.setenv("TD2_AUTH_MODE", "discord")
    monkeypatch.setenv("TD2_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("TD2_DISCORD_CLIENT_ID", "1234567890")
    monkeypatch.setenv("TD2_DISCORD_CLIENT_SECRET", "shhh-never-in-a-url")
    # A query string on purpose: it must survive urlencoding intact, because
    # Discord compares redirect_uri as an exact string.
    monkeypatch.setenv("TD2_DISCORD_REDIRECT_URI", "https://example.test/api/auth/callback?v=1")


# ---------------------------------------------------------------- fail closed


def test_discord_without_credentials_refuses_to_boot():
    """Failing closed. A permissive fallback here would mean anyone is anyone,
    with nothing in the logs to say so -- the same reasoning as ${DEV_BIND_IP:?}
    in the compose file."""
    with pytest.raises(AuthConfigError, match="DISCORD"):
        build_provider(Settings(auth_mode="discord", session_secret="s"))


def test_the_error_names_every_missing_variable():
    """"Something is misconfigured" costs an hour; naming the variable costs
    nothing."""
    with pytest.raises(AuthConfigError) as caught:
        build_provider(Settings(auth_mode="discord", session_secret="s"))
    message = str(caught.value)
    assert "TD2_DISCORD_CLIENT_ID" in message
    assert "TD2_DISCORD_CLIENT_SECRET" in message
    assert "TD2_DISCORD_REDIRECT_URI" in message


def test_mock_needs_both_switches():
    with pytest.raises(AuthConfigError, match="ALLOW_MOCK_AUTH"):
        build_provider(Settings(auth_mode="mock", allow_mock_auth=False, session_secret="s"))


def test_allow_mock_auth_alone_does_not_turn_auth_off():
    """The other half of the two-switch rule. ALLOW_MOCK_AUTH left set in an
    environment that is otherwise real must change nothing at all."""
    with pytest.raises(AuthConfigError, match="DISCORD"):
        build_provider(Settings(auth_mode="discord", allow_mock_auth=True, session_secret="s"))


def test_an_unsigned_session_is_not_a_session():
    with pytest.raises(AuthConfigError, match="SESSION_SECRET"):
        build_provider(Settings(auth_mode="mock", allow_mock_auth=True, session_secret=""))


def test_a_blank_credential_is_a_missing_credential():
    """" " is what an unset variable looks like after a template or a shell has
    had it. A check that accepts it is a check that accepts nothing."""
    with pytest.raises(AuthConfigError, match="SESSION_SECRET"):
        build_provider(Settings(auth_mode="mock", allow_mock_auth=True, session_secret="   "))
    with pytest.raises(AuthConfigError, match="TD2_DISCORD_CLIENT_ID"):
        build_provider(
            Settings(
                auth_mode="discord",
                session_secret="s",
                discord_client_id="  ",
                discord_client_secret="s",
                discord_redirect_uri="https://example.test/cb",
            )
        )


def test_credentials_are_stripped_before_they_reach_discord(monkeypatch):
    """Discord matches redirect_uri byte for byte; a trailing newline out of a
    secret store fails with an error page and no explanation."""
    provider = build_provider(
        Settings(
            auth_mode="discord",
            session_secret="s",
            discord_client_id="123\n",
            discord_client_secret=" sh ",
            discord_redirect_uri=" https://example.test/cb\n",
        )
    )
    assert provider.client_id == "123"
    assert provider.redirect_uri == "https://example.test/cb"
    assert "%0A" not in provider.login_target()[0]


def test_an_unknown_mode_is_not_treated_as_open():
    with pytest.raises(AuthConfigError, match="AUTH_MODE"):
        build_provider(Settings(auth_mode="none", session_secret="s"))


def test_creating_the_app_is_what_fails_not_the_first_request(monkeypatch):
    """Fail closed means fail at boot.

    build_provider() is called from create_app(), not from the lifespan. A
    service that starts and then rejects every request looks identical from the
    outside to one that starts and accepts every request, and only one of those
    two is what a missing credential should produce. Put the call in the
    lifespan instead and this test still passes -- but the brief's own manual
    check (`python -c "from app.main import create_app; create_app()"`) exits 0
    and proves nothing, which is how a check that cannot fail gets shipped.
    """
    monkeypatch.setenv("TD2_AUTH_MODE", "discord")
    monkeypatch.setenv("TD2_SESSION_SECRET", "s")
    with pytest.raises(AuthConfigError, match="DISCORD"):
        create_app()


# ------------------------------------------------------------ the mock itself


def test_mock_login_issues_a_session(monkeypatch):
    # base_url must be https: the session cookie is Secure, and TestClient's
    # default http://testserver silently discards it -- every assertion below
    # would then fail for the wrong reason.
    _mock_env(monkeypatch)
    with TestClient(create_app(), base_url="https://testserver") as client:
        assert client.get("/api/auth/me").json()["user"] is None
        client.get("/api/auth/login", params={"as": "tester"}, follow_redirects=False)
        me = client.get("/api/auth/me").json()
        assert me["user"]["name"] == "tester"
        assert me["mode"] == "mock"
        assert me["user"]["id"].startswith(MOCK_ID_PREFIX)


def test_the_session_records_the_mode_that_minted_it(monkeypatch):
    """Carried in the signed payload rather than read from the running config,
    so a session that outlives the cutover still says what it really is."""
    _mock_env(monkeypatch)
    with TestClient(create_app(), base_url="https://testserver") as client:
        client.get("/api/auth/login", params={"as": "tester"}, follow_redirects=False)
        assert client.get("/api/auth/me").json()["user"]["mode"] == "mock"


def test_a_mock_session_does_not_survive_the_cutover(monkeypatch):
    """The switch to real Discord must not leave fake identities logged in.

    The signing secret does not change at cutover, so every outstanding mock
    cookie stays cryptographically valid. Without this check the mock would
    keep authenticating people under AUTH_MODE=discord -- the exact "mock left
    on unnoticed" failure, now invisible because the mode header reads
    "discord".
    """
    _mock_env(monkeypatch)
    with TestClient(create_app(), base_url="https://testserver") as client:
        client.get("/api/auth/login", params={"as": "tester"}, follow_redirects=False)
        stolen = client.cookies[SESSION_COOKIE]
        assert client.get("/api/auth/me").json()["user"] is not None

    _discord_env(monkeypatch)
    monkeypatch.setenv("TD2_SESSION_SECRET", "test-secret")  # same key, on purpose
    with TestClient(create_app(), base_url="https://testserver") as client:
        body = client.get("/api/auth/me", headers=_cookie(stolen)).json()
    assert body["mode"] == "discord"
    assert body["user"] is None


def test_the_mock_name_cannot_put_anything_it_likes_in_the_cookie(monkeypatch):
    _mock_env(monkeypatch)
    with TestClient(create_app(), base_url="https://testserver") as client:
        client.get(
            "/api/auth/login",
            params={"as": "<script>alert(1)</script>" + "A" * 500},
            follow_redirects=False,
        )
        user = client.get("/api/auth/me").json()["user"]
    assert "<" not in user["name"] and len(user["name"]) <= 32
    assert user["id"].startswith(MOCK_ID_PREFIX)


def test_a_mock_id_can_never_collide_with_a_real_snowflake():
    """The cutover query. Heolstor grew adopt_orphan_builds because nobody
    could tell which rows a fake identity had written."""
    assert is_mock_id(f"{MOCK_ID_PREFIX}tester")
    assert not is_mock_id("164936124170752000")
    assert not is_mock_id(None)
    # Structural, not anecdotal: a snowflake is decimal digits only, so no real
    # id can begin with a prefix that contains a non-digit.
    assert not MOCK_ID_PREFIX[0].isdigit()


def test_snowflake_ids_are_kept_as_strings():
    """2**53-1 is JavaScript's safe integer ceiling and every snowflake is
    larger. Heolstor's web half stored them as text and its bot half as ints
    and needed a coercion shim; this coerces once, where ids enter."""
    session = session_from_discord_user({"id": 164936124170752000, "username": "spifuth"})
    assert session["id"] == "164936124170752000"
    assert isinstance(session["id"], str)
    assert int(session["id"]) > 2**53 - 1
    assert session["name"] == "spifuth"
    assert session["mode"] == "discord"
    # global_name wins when Discord supplies one, same as Heolstor.
    assert session_from_discord_user(
        {"id": "1", "username": "spifuth", "global_name": "Spifuth"}
    )["name"] == "Spifuth"


def test_a_discord_user_with_no_id_is_not_a_user():
    with pytest.raises(ValueError, match="id"):
        session_from_discord_user({"username": "spifuth"})


# ---------------------------------------------------------------- the session


def test_logout_clears_the_session(monkeypatch):
    _mock_env(monkeypatch)
    with TestClient(create_app(), base_url="https://testserver") as client:
        client.get("/api/auth/login", params={"as": "tester"}, follow_redirects=False)
        client.post("/api/auth/logout")
        assert client.get("/api/auth/me").json()["user"] is None


def test_a_planted_cookie_actually_reaches_the_server(monkeypatch):
    """The control for every "this cookie is refused" test in this file.

    They all assert `user is None`, and so would a test that sends no cookie at
    all. This one plants a good session the same way and asserts the opposite,
    so the plumbing they share is proven to work.
    """
    _mock_env(monkeypatch)
    good = serializer(Settings(session_secret="test-secret")).dumps(
        {"id": f"{MOCK_ID_PREFIX}tester", "name": "tester", "mode": "mock"}
    )
    with TestClient(create_app(), base_url="https://testserver") as client:
        body = client.get("/api/auth/me", headers=_cookie(good)).json()
    assert body["user"] == {"id": f"{MOCK_ID_PREFIX}tester", "name": "tester", "mode": "mock"}


def test_a_forged_cookie_is_refused_and_does_not_crash_the_endpoint(monkeypatch):
    """itsdangerous raises BadPayload, which is NOT a subclass of BadSignature
    (verified: BadPayload -> BadData -> Exception). Catching only BadSignature
    turns a junk cookie into a 500 instead of a logged-out visitor."""
    _mock_env(monkeypatch)
    with TestClient(create_app(), base_url="https://testserver") as client:
        for junk in ("garbage", "a.b.c", "eyJ4IjoxfQ.aaaa.bbbb"):
            response = client.get("/api/auth/me", headers=_cookie(junk))
            assert response.status_code == 200
            assert response.json()["user"] is None


def test_a_correctly_signed_cookie_with_a_junk_payload_is_refused(monkeypatch):
    """The case that separates `except BadData` from `except BadSignature`.

    A valid signature over an unparseable payload raises BadPayload, and
    BadPayload does NOT inherit from BadSignature (BadPayload -> BadData ->
    Exception, measured in the container). Every junk cookie an outsider can
    make fails at the signature instead, so without this test the two spellings
    are indistinguishable and the narrower one ships.
    """
    _mock_env(monkeypatch)
    settings = Settings(session_secret="test-secret")
    # b".AAAA" and not b"@@ junk @@": the payload becomes part of the cookie
    # value, and a space in a cookie value means the header is cut short before
    # the signature is ever checked. Leading "." is itsdangerous' compressed
    # marker, so this reaches "could not zlib decompress" -- a BadPayload with
    # a VALID signature, which is the only way to tell the two excepts apart.
    signed_garbage = serializer(settings).make_signer("td2-session").sign(b".AAAA").decode()
    with TestClient(create_app(), base_url="https://testserver") as client:
        response = client.get("/api/auth/me", headers=_cookie(signed_garbage))
    assert response.status_code == 200
    assert response.json()["user"] is None


def test_a_session_signed_with_another_key_is_refused(monkeypatch):
    _mock_env(monkeypatch)
    forged = serializer(Settings(session_secret="not-the-servers-key")).dumps(
        {"id": "164936124170752000", "name": "admin", "mode": "mock"}
    )
    with TestClient(create_app(), base_url="https://testserver") as client:
        assert client.get("/api/auth/me", headers=_cookie(forged)).json()["user"] is None


def test_the_session_cookie_is_httponly_and_secure(monkeypatch):
    _mock_env(monkeypatch)
    with TestClient(create_app(), base_url="https://testserver") as client:
        response = client.get(
            "/api/auth/login", params={"as": "tester"}, follow_redirects=False
        )
    header = response.headers["set-cookie"].lower()
    assert "httponly" in header
    assert "secure" in header
    assert "samesite=lax" in header


# ------------------------------------------------------------ the mode is visible


def test_the_mode_is_visible_on_every_response(monkeypatch):
    """A mock nobody can see is the hazard, not the mock."""
    _mock_env(monkeypatch)
    with TestClient(create_app(), base_url="https://testserver") as client:
        # Not just the endpoints auth owns, and not just 200s: the operator
        # who needs this warning is looking at whatever response they happen
        # to have in front of them.
        checked = [
            client.get("/api/health"),                                   # 200
            client.post("/api/auth/logout"),                             # 204
            client.get("/api/auth/login", params={"as": "t"},
                       follow_redirects=False),                          # 307
            client.get("/api/nothing-here"),                             # 404, no route
            client.get("/api/weapons", params={"limit": "not-a-number"}),  # 422
            client.options("/api/health", headers={                      # CORS preflight
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            }),
        ]
    assert [r.status_code for r in checked] == [200, 204, 307, 404, 422, 200]
    assert [r.headers.get("X-Auth-Mode") for r in checked] == ["mock"] * 6


def test_the_mode_header_says_discord_when_discord_is_real(monkeypatch):
    """Otherwise the header is a constant, and a constant is not a signal."""
    _discord_env(monkeypatch)
    with TestClient(create_app()) as client:
        assert client.get("/api/health").headers["X-Auth-Mode"] == "discord"


def test_the_mode_header_is_readable_from_the_browser(monkeypatch):
    """Cross-origin JS cannot read a response header that is not exposed, and
    the frontend is cross-origin by design (localhost:3000, *.vercel.app). An
    unexposed header is visible to curl and invisible to the one person the
    warning is for."""
    _mock_env(monkeypatch)
    with TestClient(create_app()) as client:
        response = client.get("/api/health", headers={"Origin": "http://localhost:3000"})
    assert "x-auth-mode" in response.headers.get("access-control-expose-headers", "").lower()


# ------------------------------------------------------------ the Discord shape


def test_discord_login_redirects_to_discord_without_leaking_the_secret(monkeypatch):
    _discord_env(monkeypatch)
    with TestClient(create_app()) as client:
        response = client.get("/api/auth/login", follow_redirects=False)
    location = response.headers["location"]
    assert response.status_code in (302, 307)
    assert location.startswith("https://discord.com/api/v10/oauth2/authorize?")
    assert "shhh-never-in-a-url" not in location
    assert "client_id=1234567890" in location
    assert "state=" in location
    # Percent-encoded, not pasted: the configured URI carries its own query
    # string and Discord matches redirect_uri as an exact string.
    assert "redirect_uri=https%3A%2F%2Fexample.test%2Fapi%2Fauth%2Fcallback%3Fv%3D1" in location


def test_discord_login_does_not_mint_a_session(monkeypatch):
    """Nothing is signed until the callback proves who this is."""
    _discord_env(monkeypatch)
    with TestClient(create_app(), base_url="https://testserver") as client:
        response = client.get("/api/auth/login", follow_redirects=False)
        assert "set-cookie" not in response.headers
        assert client.get("/api/auth/me").json()["user"] is None


def test_oauth_state_is_single_use(monkeypatch):
    _discord_env(monkeypatch)
    provider = build_provider(Settings())
    assert isinstance(provider, DiscordProvider)
    url, _ = provider.login_target()
    state = _state_of(url)
    assert provider.consume_state(state) is True
    assert provider.consume_state(state) is False
    assert provider.consume_state("never-issued") is False


def test_oauth_state_expires(monkeypatch):
    _discord_env(monkeypatch)
    provider = build_provider(Settings())
    url, _ = provider.login_target()
    state = _state_of(url)
    provider._states[state] = time.time() - OAUTH_STATE_MAX_AGE - 1
    assert provider.consume_state(state) is False


def test_pending_oauth_states_are_bounded(monkeypatch):
    """/api/auth/login is unauthenticated, and every call adds an entry that
    lives five minutes. Unbounded, that is a memory-growth vector reachable by
    anyone who can reach the service."""
    _discord_env(monkeypatch)
    provider = build_provider(Settings())
    for _ in range(2100):
        provider.login_target()
    assert len(provider._states) <= 1024


def test_the_callback_is_an_honest_501_not_a_silent_login(monkeypatch):
    """The token exchange is deliberately not implemented: it cannot be tested
    without real credentials, and untested OAuth is worse than a 501. What must
    not happen is a callback that returns 200 with a session."""
    _discord_env(monkeypatch)
    with TestClient(create_app(), base_url="https://testserver") as client:
        response = client.get("/api/auth/callback", params={"code": "x", "state": "y"})
        assert response.status_code == 501
        assert "set-cookie" not in response.headers
        assert client.get("/api/auth/me").json()["user"] is None


def test_there_is_no_callback_in_mock_mode(monkeypatch):
    _mock_env(monkeypatch)
    with TestClient(create_app(), base_url="https://testserver") as client:
        assert client.get("/api/auth/callback", params={"code": "x"}).status_code == 404
