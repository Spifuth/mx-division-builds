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
