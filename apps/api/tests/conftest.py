import os
import shutil
import tempfile
from pathlib import Path

_TEST_DB_DIR = Path(tempfile.mkdtemp(prefix="hsjs-test-"))
os.environ["HSJS_DB_PATH"] = str(_TEST_DB_DIR / "jobs.db")
os.environ["HSJS_JOB_STORE_BACKEND"] = "sqlite"
os.environ.setdefault("HSJS_PROVIDER_STUB_MODE", "true")

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_db_dir():
    yield
    shutil.rmtree(_TEST_DB_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def _isolate_global_settings_and_store():
    """Pin the global-state invariant the suite used to rely on implicitly.

    The global-store tests (test_app_shutdown_releases_job_store,
    test_get_job_store_caches_instance, test_close_job_store_swaps_and_closes_atomically)
    bypass the client fixture and were safe only because every env-rewriting
    test happened to clear the get_settings cache in its own teardown. Enforce
    the invariant centrally: after every test the global store is closed and
    the settings cache cleared, so no test can leak a cached settings object or
    an open store into the next one.
    """
    yield
    from app.core.settings import get_settings
    from app.dependencies import close_job_store

    close_job_store()
    get_settings.cache_clear()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Return a TestClient tied to the FastAPI app (stub mode).

    Each test gets its own DB file: with a shared session DB, every test's
    lifespan startup recovery would relabel any non-terminal job left by a
    previous flaky/slow test to failed/"interrupted by server restart", so a
    later test asserting that job's state would see the relabel instead of the
    original fault (order-dependent masking).
    """
    from app.core.settings import get_settings
    from app.dependencies import close_job_store

    db_path = tmp_path / "jobs.db"
    monkeypatch.setenv("HSJS_DB_PATH", str(db_path))
    get_settings.cache_clear()
    close_job_store()
    try:
        with TestClient(app) as c:
            yield c
    finally:
        close_job_store()
        get_settings.cache_clear()
