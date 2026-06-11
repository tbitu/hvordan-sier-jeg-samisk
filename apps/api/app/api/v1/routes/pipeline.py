from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile

from app.core.runtime_readiness import collect_runtime_diagnostics
from app.core.settings import get_settings
from app.dependencies import job_store, pipeline_service
from app.domain import ErrorResponse, JobRecord, JobStatus, PipelineRequest, VariantCode
from app.providers.speech.registry import get_tts_voice, get_variant_capability

router = APIRouter()


def _variant_value(variant: VariantCode) -> str:
    return getattr(variant, "value", str(variant))


def _run_pipeline(job_id: str, request: PipelineRequest, audio_bytes: bytes | None, filename: str | None) -> None:
    job_store.update(job_id, status=JobStatus.running)
    try:
        def on_update(result) -> None:
            job_store.update(job_id, status=JobStatus.running, result=result, error=None)

        if audio_bytes is not None:
            suffix = Path(filename or "input.wav").suffix or ".wav"
            with NamedTemporaryFile(delete=True, suffix=suffix) as temp_file:
                temp_file.write(audio_bytes)
                temp_file.flush()
                result = pipeline_service.run(request, audio_path=Path(temp_file.name), on_update=on_update)
        else:
            result = pipeline_service.run(request, on_update=on_update)
        failed_stage = next((stage for stage in result.stages if stage.status == JobStatus.failed), None)
        if failed_stage is not None:
            job_store.update(job_id, status=JobStatus.failed, result=result, error=failed_stage.summary)
        else:
            job_store.update(job_id, status=JobStatus.completed, result=result, error=None)
    except Exception as exc:
        job_store.update(job_id, status=JobStatus.failed, error=str(exc))


@router.post("/pipeline", response_model=JobRecord, responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
def create_pipeline_job(
    background_tasks: BackgroundTasks,
    target_variant: VariantCode = Form(VariantCode.sme),
    target_voice: str | None = Form(None),
    source_text: str | None = Form(None),
    include_phonemes: bool = Form(True),
    include_audio: bool = Form(True),
    audio: UploadFile | None = File(None),
) -> JobRecord:
    settings = get_settings()
    diagnostics = collect_runtime_diagnostics(settings)
    normalized_source_text = source_text.strip() if source_text is not None else None
    if normalized_source_text == "":
        normalized_source_text = None

    if audio is None and normalized_source_text is None:
        raise HTTPException(status_code=400, detail="Send med enten lydfil eller source_text")

    capability = get_variant_capability(target_variant)
    variant_key = _variant_value(target_variant)
    if include_audio and capability is not None and capability.capability.value != "audio":
        raise HTTPException(
            status_code=400,
            detail=f"Valgt variant ({variant_key}) stotter ikke audio i denne fasen. Send include_audio=false for denne jobben.",
        )

    if target_voice is not None and get_tts_voice(target_variant, target_voice) is None:
        raise HTTPException(status_code=400, detail=f"Valgt stemme ({target_voice}) finnes ikke for {variant_key}")

    if not settings.provider_stub_mode and not diagnostics.inference_runtime_ready:
        detail = "; ".join(diagnostics.runtime_issues) if diagnostics.runtime_issues else "Lokal inferens er ikke klar"
        raise HTTPException(status_code=503, detail=f"Lokal inferens er ikke klar: {detail}")

    if include_audio and not settings.provider_stub_mode and capability is not None and capability.capability.value == "audio":
        if not diagnostics.tts_variants_ready.get(variant_key, False):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"TTS er ikke klar for {variant_key}. Sjekk /api/v1/health og konfigurer "
                    "HSJS_TTS_RUNTIME samt eventuelt API-base eller lokal Divvun speech-runtime."
                ),
            )

    request = PipelineRequest(
        target_variant=target_variant,
        target_voice=target_voice.strip().lower() if target_voice is not None and target_voice.strip() else None,
        source_text=normalized_source_text,
        include_phonemes=include_phonemes,
        include_audio=include_audio,
    )
    record = job_store.create(JobRecord(request=request))
    audio_bytes = audio.file.read() if audio is not None else None
    filename = audio.filename if audio is not None else None
    background_tasks.add_task(_run_pipeline, record.id, request, audio_bytes, filename)
    return record
