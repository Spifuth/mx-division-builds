"""FastAPI entrypoint.

Startup order matters: the dataset is loaded and validated *before* the app
serves, so a malformed table fails the boot rather than a request. This is the
shape presentation-app uses.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings

log = logging.getLogger("td2-api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("td2-api starting")
    yield
    log.info("td2-api stopping")


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    app = FastAPI(title="TD2 Build API", lifespan=lifespan)
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_origin_regex=settings.cors_origin_regex,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict:
        """Liveness only. Deliberately independent of upstream, the dataset and
        the database: the process is healthy while it can answer, and a health
        check that goes red when a third party does is useless for deciding
        whether to restart."""
        return {"ok": True}

    return app


app = create_app()
