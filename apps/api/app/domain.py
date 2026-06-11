from __future__ import annotations

from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class CapabilityLevel(str, Enum):
    unavailable = "unavailable"
    text = "text"
    phonemes = "phonemes"
    audio = "audio"


class VariantCode(str, Enum):
    sme = "sme"
    smj = "smj"
    sma = "sma"


class RuntimeProfile(BaseModel):
    key: str
    architecture: str
    accelerator: str
    container_runtime: str
    priority: int


class VariantCapability(BaseModel):
    variant: VariantCode
    label: str
    capability: CapabilityLevel
    notes: str


class VariantSummary(BaseModel):
    code: VariantCode
    label: str


class TtsVoice(BaseModel):
    variant: VariantCode
    variant_label: str
    voice: str
    label: str
    gender: str
    is_default: bool = False


class PipelineRequest(BaseModel):
    target_variant: VariantCode = VariantCode.sme
    target_voice: str | None = None
    source_text: str | None = None
    include_phonemes: bool = True
    include_audio: bool = True


class PipelineStage(BaseModel):
    name: str
    status: JobStatus
    summary: str


class ModelCacheState(BaseModel):
    expected_path: str
    exists: bool
    has_snapshots: bool
    has_incomplete: bool
    looks_usable: bool
    summary: str


class PipelineResult(BaseModel):
    transcript_text: str | None = None
    translated_text: str | None = None
    phoneme_text: str | None = None
    audio_requested: bool = False
    audio_available: bool = False
    audio_url: str | None = None
    audio_summary: str | None = None
    stages: list[PipelineStage] = Field(default_factory=list)


class JobRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    status: JobStatus = JobStatus.queued
    request: PipelineRequest
    result: PipelineResult | None = None
    error: str | None = None


class TranscriptionResponse(BaseModel):
    transcript_text: str


class TranslationRequest(BaseModel):
    text: str
    target_variant: VariantCode = VariantCode.sme


class TranslationResponse(BaseModel):
    translated_text: str


class SynthesisRequest(BaseModel):
    text: str
    target_variant: VariantCode = VariantCode.sme
    target_voice: str | None = None


class SynthesisResponse(BaseModel):
    audio_url: str | None = None
    voice: str | None = None
    capability: CapabilityLevel


class ErrorResponse(BaseModel):
    detail: str


class HealthResponse(BaseModel):
    name: str
    environment: str
    stub_mode: bool
    provider_runtime: str
    tts_runtime: str
    tts_api_base_url: str | None = None
    tts_api_configured: bool
    tts_api_reachable: bool
    tts_command_configured: bool
    tts_command_available: bool
    tts_variants_ready: dict[str, bool]
    tts_variants_local_ready: dict[str, bool]
    runtime_components: dict[str, bool]
    inference_dependencies_ready: bool
    inference_runtime_ready: bool
    configured_models: dict[str, str]
    resolved_paths: dict[str, str]
    local_model_cache_present: bool
    model_cache_state: dict[str, ModelCacheState]
    runtime_issues: list[str]
    runtime_profiles: list[RuntimeProfile]
