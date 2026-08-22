"""FastAPI entrypoint.

Startup order matters: the dataset is loaded and validated *before* the app
serves, so a malformed table fails the boot rather than a request. This is the
shape presentation-app uses.

With one deliberate exception. A malformed *seed* dataset is a packaging bug
and still fails the boot; a malformed *promoted snapshot* does not, because
LIVE would still point at it on the next restart and the service would stay
down forever over data it already has a known-good replacement for. That case
falls back to the seed and says so on /api/meta.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.loader import DatasetError, load_dataset
from app.refresh import describe_error
from app.routes import admin as admin_routes
from app.routes import data as data_routes
from app.snapshots import SnapshotStore
from app.sources.buildstation import BuildstationSource

log = logging.getLogger("td2-api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = app.state.settings
    app.state.degraded = None
    app.state.dataset = load_dataset(settings.data_dir)

    app.state.snapshots = SnapshotStore(settings.snapshot_dir)
    app.state.source = BuildstationSource()

    live = app.state.snapshots.live()
    if live is not None:
        try:
            app.state.dataset = load_dataset(live)
        except (DatasetError, OSError) as exc:
            # Keep serving. The seed loaded a line above is known-good, and
            # throwing it away to abort the boot would take the API down and
            # keep it down: LIVE still points at the same bad directory on
            # every restart after this one. write_candidate is not atomic, so
            # a half-written snapshot is reachable from a full disk or a kill.
            app.state.degraded = (
                f"promoted snapshot {live.name} failed to load "
                f"({describe_error(exc)}); serving the seed dataset instead"
            )
            log.error("DEGRADED: %s", app.state.degraded)
        else:
            log.info("serving promoted snapshot %s", live.name)

    # Logged here rather than straight after the seed load: the line above may
    # have replaced the dataset, and a boot line describing data that is not
    # being served is worse than no boot line.
    log.info(
        "dataset live: %d tables, %d rows, version %s",
        len(app.state.dataset.tables),
        sum(app.state.dataset.counts.values()),
        app.state.dataset.version,
    )

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

    app.include_router(data_routes.router)
    app.include_router(admin_routes.router)

    @app.get("/api/health")
    def health() -> dict:
        """Liveness only. Deliberately independent of upstream, the dataset and
        the database: the process is healthy while it can answer, and a health
        check that goes red when a third party does is useless for deciding
        whether to restart."""
        return {"ok": True}

    return app


app = create_app()
