"""Configuration, from the environment only.

There is no settings file and no `.env`: a global deny rule blocks every agent
in this project from creating one, and the app is required to boot with none
present. Every value below therefore has a working default, except the ones
where a wrong default would be unsafe -- those are validated in auth.py.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# api/app/config.py -> api/app -> api -> repo root
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TD2_", extra="ignore")

    # The seed dataset shipped in the repo. A fresh clone with no network boots
    # and serves from this.
    #
    # Anchored to the repo root rather than the process's cwd: the container's
    # working_dir is /srv/api, so a relative "public/data" resolved to
    # /srv/api/public/data, which does not exist. Both compose services override
    # this with TD2_DATA_DIR so nothing was broken -- but a default that cannot
    # work is worse than no default, because the comment above it reads as a
    # promise the code does not keep.
    data_dir: Path = REPO_ROOT / "public" / "data"

    # Where validated snapshots are written. Outside the repo tree: this is
    # mutable runtime state, not source.
    snapshot_dir: Path = Path("/srv/snapshots")

    db_path: Path = Path("/srv/db/builds.db")

    # v0 previews run on vercel.app subdomains; without these the browser
    # blocks every call and the failure looks like the API is down.
    cors_origins: list[str] = ["http://localhost:3000", "https://v0.dev"]
    # DANGER, read before widening. This pairs with allow_credentials=True, and
    # since Task 6 the credential is a session cookie rather than nothing. The
    # regex trusts EVERY *.vercel.app deployment, and anyone can create one.
    #
    # What holds it today is SameSite=lax on the cookie: a cross-site request
    # from an attacker's Vercel app does not carry it, so the permissive origin
    # cannot be cashed in. Setting SameSite=None to make a cross-site frontend
    # work -- the obvious next move when the real frontend deploys -- removes
    # that and turns any attacker-controlled *.vercel.app page into a
    # credentialed reader of this API.
    #
    # If you need cross-site: pin this to the ONE deployment origin first, then
    # change SameSite. Never both at once. test_cors_credentials_guard pins it.
    cors_origin_regex: str = r"https://.*\.vercel\.app"

    auth_mode: str = "discord"
    allow_mock_auth: bool = False
    session_secret: str = ""
    discord_client_id: str = ""
    discord_client_secret: str = ""
    discord_redirect_uri: str = ""

    log_level: str = "INFO"


def get_settings() -> Settings:
    return Settings()
