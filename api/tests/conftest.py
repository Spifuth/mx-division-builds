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
