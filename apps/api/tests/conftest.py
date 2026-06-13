import os

os.environ.setdefault("HSJS_PROVIDER_STUB_MODE", "true")

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    """Return a TestClient tied to the FastAPI app (stub mode)."""
    with TestClient(app) as c:
        yield c
