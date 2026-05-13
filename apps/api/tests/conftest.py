"""Shared pytest fixtures."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Seed deterministic config BEFORE importing the app so ``Settings`` picks it up.
# Each test gets its own ``OPENER_APPS_DIR`` via the ``storage`` fixture below;
# this default is just enough to let ``Settings()`` construct at import time.
_DEFAULT_DATA_DIR = Path(tempfile.gettempdir()) / "opener-apps-tests-default"
_DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("APP_WEB_ORIGIN", "http://localhost:3000")
os.environ.setdefault("GOOGLE_API_KEY", "")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("OPENER_APPS_DIR", str(_DEFAULT_DATA_DIR))

from collections.abc import Iterator  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from micracode_api.config import get_settings  # noqa: E402
from micracode_api.deps import reset_deps_cache  # noqa: E402
from micracode_api.main import create_app  # noqa: E402
from micracode_core.storage import Storage, reset_storage_cache  # noqa: E402

get_settings.cache_clear()
reset_deps_cache()
reset_storage_cache()


@pytest.fixture()
def opener_apps_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "opener-apps"
    target.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OPENER_APPS_DIR", str(target))
    get_settings.cache_clear()
    reset_deps_cache()
    reset_storage_cache()
    yield target
    get_settings.cache_clear()
    reset_deps_cache()
    reset_storage_cache()


@pytest.fixture()
def storage(opener_apps_dir: Path) -> Storage:
    return Storage(opener_apps_dir)


@pytest.fixture()
def client(opener_apps_dir: Path) -> Iterator[TestClient]:
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client
