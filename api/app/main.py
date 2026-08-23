"""FastAPI entrypoint.

Startup order matters: the dataset is loaded and validated *before* the app
serves, so a malformed table fails the boot rather than a request. This is the
shape presentation-app uses.

With one deliberate exception. A malformed *seed* dataset is a packaging bug
and still fails the boot; a malformed *promoted snapshot* does not, because
LIVE would still point at it on the next restart and the service would stay
down forever over data it already has a known-good replacement for. That case
falls back to the seed and says so on /api/meta.

Auth runs the other way. build_provider() is called from create_app() rather
than from the lifespan, so a misconfigured auth layer kills the process before
it can listen: serving with the wrong identity provider is not a degraded mode,
it is a different service wearing this one's name.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth import build_provider
from app.config import get_settings
from app.db import init_db
from app.loader import DatasetError, load_dataset
from app.refresh import describe_error
from app.routes import admin as admin_routes
from app.routes import auth as auth_routes
from app.routes import builds as builds_routes
from app.routes import compute as compute_routes
from app.routes import data as data_routes
from app.snapshots import SnapshotStore
from app.sources.buildstation import BuildstationSource

log = logging.getLogger("td2-api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = app.state.settings
    app.state.degraded = None

    # First, and before anything is serving. init_db creates the file, sets WAL
    # and runs any migration, so an unwritable volume fails the boot loudly
    # instead of surfacing as a 500 on the first person who saves a build --
    # which is the shape the snapshot directory's ownership bug took.
    init_db(settings.db_path)

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

    # Before anything else, and deliberately not in the lifespan. A
    # misconfigured auth layer must stop the process from existing, not start
    # it and then answer every request the wrong way. AuthConfigError raised
    # here propagates out of `app = create_app()` at import time, so uvicorn
    # exits non-zero and never binds a socket.
    app.state.auth = build_provider(settings)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_origin_regex=settings.cors_origin_regex,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        # Without this the browser refuses to let JS read X-Auth-Mode on a
        # cross-origin response, and the frontend is cross-origin by design
        # (localhost:3000, *.vercel.app). A warning only curl can see is not a
        # warning for the person it is meant for.
        expose_headers=["X-Auth-Mode"],
    )

    @app.middleware("http")
    async def stamp_auth_mode(request, call_next):
        """Say which provider answered, on every response.

        The mock is only dangerous while it is invisible, so this is
        unconditional: 200s, 204s, redirects, 404s no route matched, 422s and
        CORS preflights all carry it. Measured.

        One response does not, and it is worth naming rather than implying
        otherwise: an unhandled exception unwinds past this middleware and the
        500 is written by Starlette's ServerErrorMiddleware, which sits above
        the whole stack. Adding a global Exception handler to close that gap
        would change every endpoint's error body for a case that cannot be a
        silent authentication -- a crash is not somebody being let in.
        """
        response = await call_next(request)
        response.headers["X-Auth-Mode"] = request.app.state.auth.mode
        return response

    app.include_router(data_routes.router)
    app.include_router(admin_routes.router)
    app.include_router(auth_routes.router)
    app.include_router(builds_routes.router)
    app.include_router(compute_routes.router)

    @app.get("/api/health")
    def health() -> dict:
        """Liveness only. Deliberately independent of upstream, the dataset and
        the database: the process is healthy while it can answer, and a health
        check that goes red when a third party does is useless for deciding
        whether to restart. The database is the one dependency this service
        cannot serve without -- which is exactly why it is checked at boot,
        where a failure stops the container, rather than here."""
        return {"ok": True}

    return app


app = create_app()
