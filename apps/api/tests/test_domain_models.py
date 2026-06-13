from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.domain import (
    CapabilityLevel,
    ErrorResponse,
    HealthResponse,
    JobRecord,
    JobStatus,
    ModelCacheState,
    PipelineRequest,
    PipelineResult,
    PipelineStage,
    RuntimeProfile,
    SynthesisRequest,
    SynthesisResponse,
    TranscriptionResponse,
    TranslationRequest,
    TranslationResponse,
    TtsVoice,
    VariantCapability,
    VariantCode,
    VariantSummary,
)


# ── Enum tests ───────────────────────────────────────────────

class TestVariantCode:
    def test_members(self):
        variants = [e.value for e in VariantCode]
        assert set(variants) == {"sme", "smj", "sma"}

    def test_member_count(self):
        # Exactly three variants
        assert len(VariantCode) == 3


class TestJobStatus:
    def test_members(self):
        statuses = [e.value for e in JobStatus]
        assert set(statuses) == {"queued", "running", "completed", "failed"}

    def test_member_count(self):
        assert len(JobStatus) == 4


class TestCapabilityLevel:
    def test_members(self):
        levels = [e.value for e in CapabilityLevel]
        assert set(levels) == {"unavailable", "text", "phonemes", "audio"}

    def test_member_count(self):
        assert len(CapabilityLevel) == 4


# ── PipelineRequest tests ────────────────────────────────────

class TestPipelineRequest:
    def test_defaults(self):
        req = PipelineRequest()
        assert req.target_variant == VariantCode.sme
        assert req.include_phonemes is True
        assert req.include_audio is True
        assert req.source_text is None
        assert req.target_voice is None

    def test_explicit_variant_smj(self):
        req = PipelineRequest(target_variant=VariantCode.smj)
        assert req.target_variant == VariantCode.smj

    def test_explicit_variant_sma(self):
        req = PipelineRequest(target_variant=VariantCode.sma)
        assert req.target_variant == VariantCode.sma

    def test_with_source_text(self):
        req = PipelineRequest(source_text="Hei hvordan har du det")
        assert req.source_text == "Hei hvordan har du det"

    def test_invalid_variant_raises(self):
        with pytest.raises(ValidationError):
            # type: ignore — passing invalid value on purpose
            PipelineRequest(target_variant="invalid")  # type: ignore[arg-type]

    def test_negative_include_phonemes_false(self):
        req = PipelineRequest(include_phonemes=False)
        assert req.include_phonemes is False

    def test_negative_include_audio_false(self):
        req = PipelineRequest(include_audio=False)
        assert req.include_audio is False


# ── JobRecord tests ──────────────────────────────────────────

class TestJobRecord:
    def test_create_generates_uuid_v4(self):
        req = PipelineRequest(source_text="test")
        job = JobRecord(request=req)
        uid = uuid.UUID(job.id)
        assert uid.version == 4  # verifies UUIDv4 from uuid4()
        assert job.status == JobStatus.queued
        assert job.result is None
        assert job.error is None

    def test_with_result(self):
        req = PipelineRequest()
        result = PipelineResult(transcript_text="hello")
        job = JobRecord(request=req, status=JobStatus.completed, result=result)
        assert job.status == JobStatus.completed
        assert job.result.transcript_text == "hello"

    def test_with_error(self):
        req = PipelineRequest()
        job = JobRecord(request=req, status=JobStatus.failed, error="nope")
        assert job.error == "nope"

    def test_negative_missing_request_raises(self):
        with pytest.raises(ValidationError):
            JobRecord()  # type: ignore[call-arg]


# ── PipelineResult tests ─────────────────────────────────────

class TestPipelineResult:
    def test_defaults(self):
        result = PipelineResult()
        assert result.transcript_text is None
        assert result.stages == []

    def test_with_fields(self):
        result = PipelineResult(
            transcript_text="nb text",
            translated_text="sme text",
            audio_available=True,
            audio_url="/artifacts/123.mp3",
        )
        assert result.transcript_text == "nb text"
        assert result.audio_url == "/artifacts/123.mp3"


# ── PipelineStage tests ──────────────────────────────────────

class TestPipelineStage:
    def test_create(self):
        stage = PipelineStage(name="asr", status=JobStatus.completed, summary="done")
        assert stage.name == "asr"
        assert stage.status == JobStatus.completed


# ── VariantCapability tests ──────────────────────────────────

class TestVariantCapability:
    def test_create(self):
        cap = VariantCapability(
            variant=VariantCode.sme,
            label="North Sami",
            capability=CapabilityLevel.audio,
            notes="testing",
        )
        assert cap.variant == VariantCode.sme


# ── VariantSummary tests ─────────────────────────────────────

class TestVariantSummary:
    def test_create(self):
        vs = VariantSummary(code=VariantCode.sme, label="North Sami")
        assert vs.code == VariantCode.sme
        assert vs.label == "North Sami"


# ── TtsVoice tests ───────────────────────────────────────────

class TestTtsVoice:
    def test_defaults(self):
        voice = TtsVoice(
            variant=VariantCode.sme,
            variant_label="North Sami",
            voice="sivle",
            label="Sivle",
            gender="female",
        )
        assert voice.is_default is False

    def test_set_default(self):
        voice = TtsVoice(
            variant=VariantCode.sme,
            variant_label="North Sami",
            voice="sivle",
            label="Sivle",
            gender="female",
            is_default=True,
        )
        assert voice.is_default is True


# ── RuntimeProfile tests ─────────────────────────────────────

class TestRuntimeProfile:
    def test_create(self):
        rp = RuntimeProfile(
            key="cpu-any",
            architecture="multi-arch",
            accelerator="cpu",
            container_runtime="podman-or-docker",
            priority=99,
        )
        assert rp.key == "cpu-any"
        assert rp.priority == 99


# ── ModelCacheState tests ────────────────────────────────────

class TestModelCacheState:
    def test_create(self):
        mcs = ModelCacheState(
            expected_path="/models/foo",
            exists=True,
            has_snapshots=True,
            has_incomplete=False,
            looks_usable=True,
            summary="ready",
        )
        assert mcs.looks_usable is True


# ── TranscriptionResponse tests ─────────────────────────────

class TestTranscriptionResponse:
    def test_create(self):
        resp = TranscriptionResponse(transcript_text="hello world")
        assert resp.transcript_text == "hello world"


# ── TranslationRequest / Response tests ──────────────────────

class TestTranslationRequest:
    def test_default_variant(self):
        req = TranslationRequest(text="hei")
        assert req.target_variant == VariantCode.sme

    def test_explicit_variant(self):
        req = TranslationRequest(text="hei", target_variant=VariantCode.smj)
        assert req.target_variant == VariantCode.smj


class TestTranslationResponse:
    def test_create(self):
        resp = TranslationResponse(translated_text="bures")
        assert resp.translated_text == "bures"


# ── SynthesisRequest / Response tests ────────────────────────

class TestSynthesisRequest:
    def test_defaults(self):
        req = SynthesisRequest(text="test")
        assert req.target_variant == VariantCode.sme
        assert req.target_voice is None


class TestSynthesisResponse:
    def test_create(self):
        resp = SynthesisResponse(
            audio_url="/artifacts/t.mp3",
            voice="sivle",
            capability=CapabilityLevel.audio,
        )
        assert resp.capability == CapabilityLevel.audio


# ── ErrorResponse tests ─────────────────────────────────────

class TestErrorResponse:
    def test_create(self):
        err = ErrorResponse(detail="nope")
        assert err.detail == "nope"


# ── HealthResponse tests ─────────────────────────────────────

class TestHealthResponse:
    def test_minimal(self):
        resp = HealthResponse(
            name="test",
            environment="production",
            stub_mode=True,
            provider_runtime="transformers",
            tts_runtime="divvun-api",
            tts_api_base_url=None,
            tts_api_configured=False,
            tts_api_reachable=False,
            tts_command_configured=False,
            tts_command_available=False,
            tts_variants_ready={},
            tts_variants_local_ready={},
            runtime_components={},
            inference_dependencies_ready=False,
            inference_runtime_ready=False,
            configured_models={"asr": "m1", "translation": "m2"},
            resolved_paths={},
            local_model_cache_present=False,
            model_cache_state={},
            runtime_issues=[],
            runtime_profiles=[],
        )
        assert resp.name == "test"
        assert resp.environment == "production"
        assert resp.stub_mode is True
        assert resp.provider_runtime == "transformers"
        assert resp.tts_runtime == "divvun-api"
        assert resp.tts_api_base_url is None
        assert resp.tts_api_configured is False
        assert resp.tts_api_reachable is False
        assert resp.tts_command_configured is False
        assert resp.tts_command_available is False
        assert resp.tts_variants_ready == {}
        assert resp.tts_variants_local_ready == {}
        assert resp.runtime_components == {}
        assert resp.inference_dependencies_ready is False
        assert resp.inference_runtime_ready is False
        assert len(resp.configured_models) == 2
        assert resp.resolved_paths == {}
        assert resp.local_model_cache_present is False
        assert resp.model_cache_state == {}
        assert resp.runtime_issues == []
        assert resp.runtime_profiles == []

    def test_negative_missing_name_raises(self):
        with pytest.raises(ValidationError):
            HealthResponse(  # type: ignore[call-arg]
                environment="production",
                stub_mode=True,
                provider_runtime="transformers",
                tts_runtime="divvun-api",
                tts_api_base_url=None,
                tts_api_configured=False,
                tts_api_reachable=False,
                tts_command_configured=False,
                tts_command_available=False,
                tts_variants_ready={},
                tts_variants_local_ready={},
                runtime_components={},
                inference_dependencies_ready=False,
                inference_runtime_ready=False,
                configured_models={},
                resolved_paths={},
                local_model_cache_present=False,
                model_cache_state={},
                runtime_issues=[],
                runtime_profiles=[],
            )
