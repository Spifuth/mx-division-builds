"""The only source today.

Two upstreams, not one. The 20 tables come from buildstation.app; the version
token does not live there -- that endpoint answers
`{"message":"Unknown collection"}` -- it lives on mxswat's gh-pages branch.
Polling the small one and only pulling the big one when it moves is the whole
reason a refresh is cheap.

Fetching server-side also sidesteps the CORS allowlist that broke this app
twice: buildstation.app only sends Access-Control-Allow-Origin for
https://mxswat.github.io, and a server has no origin to be judged on.
"""

from __future__ import annotations

import asyncio

import httpx

from app.loader import TABLE_NAMES

BASE = "https://buildstation.app/api/td2/v2/data/mx"
VERSION_URL = "https://raw.githubusercontent.com/mxswat/mx-division-builds/gh-pages/DB.Version"


class BuildstationSource:
    name = "buildstation.app"

    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout

    async def fetch_version(self) -> str:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(VERSION_URL)
            response.raise_for_status()
            return response.text.strip()

    async def fetch_tables(self) -> dict[str, str]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async def one(name: str) -> tuple[str, str]:
                response = await client.get(f"{BASE}/{name}")
                response.raise_for_status()
                return name, response.text

            results = await asyncio.gather(*(one(n) for n in TABLE_NAMES))
        return dict(results)
