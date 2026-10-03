from __future__ import annotations

from pathlib import Path

import yaml

from app.main import app

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = REPO_ROOT / "packages" / "contracts" / "openapi.yaml"


def _live_spec() -> dict:
    """The OpenAPI spec exactly as packages/scripts/sync-openapi.sh commits it."""
    spec = app.openapi()
    # Mirror of the servers default added by the sync script.
    spec.setdefault(
        "servers",
        [{"url": "http://localhost:8000", "description": "Lokal utviklingsinstans"}],
    )
    return spec


class TestCommittedContract:
    def test_contract_matches_live_spec(self):
        with CONTRACT_PATH.open(encoding="utf-8") as handle:
            committed = yaml.safe_load(handle)
        assert committed == _live_spec()

    def test_jobs_endpoint_documents_404(self):
        spec = _live_spec()
        responses = spec["paths"]["/api/v1/jobs/{job_id}"]["get"]["responses"]
        assert responses["404"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }

    def test_root_endpoint_documented(self):
        spec = _live_spec()
        assert "/" in spec["paths"]
