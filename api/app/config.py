"""Configuration, from the environment only.

There is no settings file and no `.env`: a global deny rule blocks every agent
in this project from creating one, and the app is required to boot with none
present. Every value below therefore has a working default, except the ones
where a wrong default would be unsafe -- those are validated in auth.py.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TD2_", extra="ignore")

    # The seed dataset shipped in the repo. A fresh clone with no network boots
    # and serves from this.
    data_dir: Path = Path("public/data")

    # Where validated snapshots are written. Outside the repo tree: this is
    # mutable runtime state, not source.
    snapshot_dir: Path = Path("/srv/snapshots")

    db_path: Path = Path("/srv/db/builds.db")

    # v0 previews run on vercel.app subdomains; without these the browser
    # blocks every call and the failure looks like the API is down.
    cors_origins: list[str] = ["http://localhost:3000", "https://v0.dev"]
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
