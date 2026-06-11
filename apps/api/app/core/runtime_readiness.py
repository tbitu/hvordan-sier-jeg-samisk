from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from shutil import which
import shlex
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from app.core.settings import Settings
from app.providers.speech.registry import get_variant_capability, get_variant_tts_voices


_RUNTIME_COMPONENTS = [
    "accelerate",
    "torch",
    "transformers",
    "tokenizers",
    "safetensors",
    "sentencepiece",
    "numpy",
    "torchaudio",
    "imageio_ffmpeg",
]

_INFERENCE_COMPONENTS = ["torch", "transformers", "tokenizers", "safetensors", "sentencepiece", "numpy"]


@dataclass(frozen=True)
class ModelCacheDiagnostics:
    expected_path: Path
    exists: bool
    has_snapshots: bool
    has_incomplete: bool
    looks_usable: bool
    summary: str


@dataclass(frozen=True)
class RuntimeDiagnostics:
    runtime_components: dict[str, bool]
    inference_dependencies_ready: bool
    inference_runtime_ready: bool
    tts_api_reachable: bool
    tts_command_available: bool
    tts_variants_ready: dict[str, bool]
    tts_variants_local_ready: dict[str, bool]
    model_cache_state: dict[str, ModelCacheDiagnostics]
    runtime_issues: list[str]


def collect_runtime_diagnostics(settings: Settings) -> RuntimeDiagnostics:
    runtime_components = {name: find_spec(name) is not None for name in _RUNTIME_COMPONENTS}
    inference_dependencies_ready = all(runtime_components[name] for name in _INFERENCE_COMPONENTS)
    model_cache_dir = settings.effective_model_cache_dir
    model_cache_state = {
        "nb_whisper": _inspect_model_cache(model_cache_dir, settings.whisper_model_id),
        "tahetorn": _inspect_model_cache(model_cache_dir, settings.tahetorn_model_id),
    }
    tts_api_reachable = _check_tts_api(settings.tts_api_base_url)
    tts_command_available = _resolve_command_path(settings.tts_command) is not None
    tts_variants_local_ready = {
        variant: _tts_variant_local_ready(settings, variant, tts_command_available)
        for variant in settings.supported_variants
    }
    tts_variants_ready = {
        variant: _tts_variant_ready(settings, variant, tts_api_reachable, tts_variants_local_ready)
        for variant in settings.supported_variants
    }

    runtime_issues: list[str] = []
    if settings.provider_runtime != "transformers":
        runtime_issues.append(f"Ukjent provider-runtime konfigurert: {settings.provider_runtime}")

    if not settings.provider_stub_mode:
        if not inference_dependencies_ready:
            missing = [name for name in _INFERENCE_COMPONENTS if not runtime_components[name]]
            runtime_issues.append(
                "Inference-modus mangler runtimekomponenter: " + ", ".join(sorted(missing))
            )

        for model_name, diagnostics in model_cache_state.items():
            if not diagnostics.looks_usable:
                runtime_issues.append(f"Lokal modellcache for {model_name} er ikke klar: {diagnostics.summary}")

    if settings.tts_runtime == "divvun-command":
        if not settings.tts_command:
            runtime_issues.append("HSJS_TTS_COMMAND er ikke satt for divvun-command-runtime")
        elif not tts_command_available:
            runtime_issues.append(f"Fant ikke TTS-kommandoen: {settings.tts_command}")
        elif not settings.provider_stub_mode:
            for variant in settings.supported_variants:
                capability = get_variant_capability(variant)
                if capability is None or capability.capability.value != "audio":
                    continue

                voice_model = getattr(settings, f"tts_{variant}_voice_model")
                vocoder_model = getattr(settings, f"tts_{variant}_vocoder_model")
                if not _path_exists(voice_model):
                    runtime_issues.append(
                        f"{variant} TTS mangler voice-modell: sett HSJS_TTS_{variant.upper()}_VOICE_MODEL til en eksisterende .pte-fil"
                    )
                if not _path_exists(vocoder_model):
                    runtime_issues.append(
                        f"{variant} TTS mangler vocoder-modell: sett HSJS_TTS_{variant.upper()}_VOCODER_MODEL til en eksisterende .pte-fil"
                    )
    elif settings.tts_runtime == "divvun-api":
        if not settings.tts_api_base_url.strip():
            runtime_issues.append("HSJS_TTS_API_BASE_URL er ikke satt for divvun-api-runtime")
        elif not tts_api_reachable:
            runtime_issues.append(f"Fant ikke Divvun API TTS-endepunktet: {settings.tts_api_base_url}")

    inference_runtime_ready = (
        not settings.provider_stub_mode
        and settings.provider_runtime == "transformers"
        and inference_dependencies_ready
        and all(diagnostics.looks_usable for diagnostics in model_cache_state.values())
    )

    return RuntimeDiagnostics(
        runtime_components=runtime_components,
        inference_dependencies_ready=inference_dependencies_ready,
        inference_runtime_ready=inference_runtime_ready,
        tts_api_reachable=tts_api_reachable,
        tts_command_available=tts_command_available,
        tts_variants_ready=tts_variants_ready,
        tts_variants_local_ready=tts_variants_local_ready,
        model_cache_state=model_cache_state,
        runtime_issues=runtime_issues,
    )


def _inspect_model_cache(model_dir: Path, model_id: str) -> ModelCacheDiagnostics:
    expected_path = model_dir / f"models--{model_id.replace('/', '--')}"
    exists = expected_path.exists()
    snapshots_dir = expected_path / "snapshots"
    has_snapshots = snapshots_dir.exists() and any(snapshots_dir.iterdir())
    has_incomplete = exists and any(expected_path.rglob("*.incomplete"))
    looks_usable = exists and has_snapshots and not has_incomplete

    if not exists:
        summary = "Ingen lokal cache funnet"
    elif has_incomplete:
        summary = "Cache inneholder uferdige nedlastinger"
    elif not has_snapshots:
        summary = "Cache finnes, men snapshots mangler"
    else:
        summary = "Cache ser klar ut"

    return ModelCacheDiagnostics(
        expected_path=expected_path,
        exists=exists,
        has_snapshots=has_snapshots,
        has_incomplete=has_incomplete,
        looks_usable=looks_usable,
        summary=summary,
    )


def _tts_variant_local_ready(settings: Settings, variant: str, tts_command_available: bool) -> bool:
    if settings.tts_runtime != "divvun-command":
        return False
    if not settings.tts_command or not tts_command_available:
        return False

    voice_model = getattr(settings, f"tts_{variant}_voice_model")
    vocoder_model = getattr(settings, f"tts_{variant}_vocoder_model")
    return _path_exists(voice_model) and _path_exists(vocoder_model)


def _tts_variant_ready(
    settings: Settings,
    variant: str,
    tts_api_reachable: bool,
    tts_variants_local_ready: dict[str, bool],
) -> bool:
    if not get_variant_tts_voices(variant):
        return False
    if settings.tts_runtime == "divvun-api":
        return bool(settings.tts_api_base_url.strip()) and tts_api_reachable
    if settings.tts_runtime == "divvun-command":
        return tts_variants_local_ready.get(variant, False)
    return False


def _check_tts_api(api_base_url: str) -> bool:
    if not api_base_url.strip():
        return False
    parsed = urlparse(api_base_url)
    if not parsed.scheme or not parsed.netloc:
        return False
    probe_url = f"{parsed.scheme}://{parsed.netloc}/"
    request = Request(probe_url, headers={"Accept": "text/html,application/json"}, method="GET")
    try:
        with urlopen(request, timeout=3) as response:
            return 200 <= response.status < 300
    except (HTTPError, URLError):
        return False


def _path_exists(path: Path | None) -> bool:
    return path is not None and path.exists()


def _resolve_command_path(command: str) -> Path | None:
    if not command.strip():
        return None
    parts = shlex.split(command)
    if not parts:
        return None
    executable = parts[0]
    if "/" in executable:
        executable_path = Path(executable)
        return executable_path if executable_path.exists() else None
    resolved = which(executable)
    return Path(resolved) if resolved is not None else None