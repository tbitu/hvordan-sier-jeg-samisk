from __future__ import annotations

from fastapi.testclient import TestClient


class TestHealthEndpoint:
    def test_health_returns_200(self, client: TestClient):
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200

    def test_health_stub_mode_true(self, client: TestClient):
        data = client.get("/api/v1/health").json()
        # conftest sets HSJS_PROVIDER_STUB_MODE=true; verify stub_mode is True
        assert "HSJS_PROVIDER_STUB_MODE" in __import__("os").environ
        assert __import__("os").environ["HSJS_PROVIDER_STUB_MODE"] == "true"
        assert data["stub_mode"] is True

    def test_health_has_required_fields(self, client: TestClient):
        data = client.get("/api/v1/health").json()
        required = [
            "name",
            "environment",
            "stub_mode",
            "provider_runtime",
            "tts_runtime",
            "runtime_profiles",
        ]
        for field in required:
            assert field in data, f"Missing field: {field}"


class TestVariantsEndpoint:
    def test_list_variants(self, client: TestClient):
        resp = client.get("/api/v1/variants")
        assert resp.status_code == 200
        variants = resp.json()
        assert isinstance(variants, list)
        assert len(variants) == 3
        codes = [v["code"] for v in variants]
        labels = [v["label"] for v in variants]
        assert "sme" in codes
        assert "smj" in codes
        assert "sma" in codes
        # Each variant must have a non-empty label
        for label in labels:
            assert isinstance(label, str) and len(label) > 0


class TestRootEndpoint:
    def test_root(self, client: TestClient):
        resp = client.get("/")
        assert resp.status_code == 200
