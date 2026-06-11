from fastapi import APIRouter

from app.core.runtime_readiness import collect_runtime_diagnostics
from app.core.settings import get_settings
from app.domain import HealthResponse, RuntimeProfile

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    settings = get_settings()
    diagnostics = collect_runtime_diagnostics(settings)
    model_cache_dir = settings.effective_model_cache_dir
    return HealthResponse(
        name=settings.app_name,
        environment=settings.environment,
        stub_mode=settings.provider_stub_mode,
        provider_runtime=settings.provider_runtime,
        tts_runtime=settings.tts_runtime,
        tts_api_base_url=settings.tts_api_base_url or None,
        tts_api_configured=bool(settings.tts_api_base_url.strip()),
        tts_api_reachable=diagnostics.tts_api_reachable,
        tts_command_configured=bool(settings.tts_command),
        tts_command_available=diagnostics.tts_command_available,
        tts_variants_ready=diagnostics.tts_variants_ready,
        tts_variants_local_ready=diagnostics.tts_variants_local_ready,
        runtime_components=diagnostics.runtime_components,
        inference_dependencies_ready=diagnostics.inference_dependencies_ready,
        inference_runtime_ready=diagnostics.inference_runtime_ready,
        configured_models={"asr": settings.whisper_model_id, "translation": settings.tahetorn_model_id},
        resolved_paths={
            "repo_root": str(settings.repo_root),
            "api_root": str(settings.api_root),
            "artifacts_dir": str(settings.artifacts_dir),
            "model_dir": str(model_cache_dir),
        },
        local_model_cache_present=model_cache_dir.exists(),
        model_cache_state={
            key: {
                "expected_path": str(value.expected_path),
                "exists": value.exists,
                "has_snapshots": value.has_snapshots,
                "has_incomplete": value.has_incomplete,
                "looks_usable": value.looks_usable,
                "summary": value.summary,
            }
            for key, value in diagnostics.model_cache_state.items()
        },
        runtime_issues=diagnostics.runtime_issues,
        runtime_profiles=[
            RuntimeProfile(key="rocm-amd64-podman", architecture="amd64", accelerator="amd-rocm", container_runtime="podman", priority=1),
            RuntimeProfile(key="cuda-arm64-dgx-spark", architecture="arm64", accelerator="nvidia-cuda", container_runtime="rootless-docker", priority=2),
            RuntimeProfile(key="cpu-any", architecture="multi-arch", accelerator="cpu", container_runtime="podman-or-docker", priority=99),
        ],
    )
