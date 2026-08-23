import os
from pathlib import Path

import pytest


@pytest.fixture
def seed_dir() -> Path:
    """The dataset shipped in the repo.

    Resolved from the environment the container sets, with a repo-relative
    fallback so the suite also runs outside the container. It must NOT be
    skippable: a fixture that skips when its input is missing is how a test
    goes green while verifying nothing.
    """
    path = Path(os.getenv("TD2_DATA_DIR", Path(__file__).parents[2] / "public" / "data"))
    if not path.is_dir():
        raise RuntimeError(f"seed dataset not found at {path}; the suite cannot be meaningful without it")
    return path


@pytest.fixture(autouse=True, scope="session")
def isolated_snapshot_dir(tmp_path_factory) -> Path:
    """A snapshot store that belongs to this run and no other.

    Every create_app() builds a SnapshotStore at settings.snapshot_dir, so
    without this the suite shares whatever fixed path the environment names --
    /tmp/snapshots in docker-compose.dev.yml. One run's promoted LIVE pointer
    would then be the next run's startup state, and a test that passes because
    an earlier run left a snapshot behind is not a test. Autouse and
    session-scoped so it is set before create_app() is ever called; individual
    tests still monkeypatch it when they need to plant a specific store.
    """
    path = tmp_path_factory.mktemp("snapshots")
    os.environ["TD2_SNAPSHOT_DIR"] = str(path)
    return path


@pytest.fixture(autouse=True, scope="session")
def isolated_db_path(tmp_path_factory) -> Path:
    """A builds database that belongs to this run and no other.

    Same reasoning as isolated_snapshot_dir above, and the same hazard: as of
    Task 7 every create_app() calls init_db(settings.db_path) in its lifespan,
    and ~250 tests call create_app(). Without this they all share whatever
    fixed path the environment names -- /srv/db/builds.db, which inside the
    `api` service is the REAL volume. A test run would then write builds into
    production data and read one run's rows in the next.

    Autouse and session-scoped so it is set before the first create_app();
    tests/test_builds.py narrows it further to one file per test, because it
    counts rows.
    """
    path = tmp_path_factory.mktemp("db") / "builds.db"
    os.environ["TD2_DB_PATH"] = str(path)
    return path
