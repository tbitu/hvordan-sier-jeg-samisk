import logging
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile

from app.core.runtime_readiness import collect_runtime_diagnostics
from app.core.settings import get_settings
from app.dependencies import get_job_store, pipeline_service
from app.domain import ErrorResponse, JobRecord, JobStatus, PipelineRequest, VariantCode
from app.persistence import StoreClosedError
from app.providers.speech.registry import get_tts_voice, get_variant_capability
from app.state import JobStore

logger = logging.getLogger(__name__)

router = APIRouter()


def _variant_value(variant: VariantCode) -> str:
    return getattr(variant, "value", str(variant))


def _run_pipeline(store: JobStore, job_id: str, request: PipelineRequest, audio_bytes: bytes | None, filename: str | None) -> None:
    try:
        try:
            store.update(job_id, status=JobStatus.running)
        except StoreClosedError:
            raise
        except Exception as exc:
            # A transient store failure (locked or full DB) must not fail the job
            # before the pipeline has even run: the job stays in its last
            # persisted state and the final write persists the terminal state.
            logger.warning("Job %s: could not persist running status (%s: %s)", job_id, type(exc).__name__, exc)

        def on_update(result) -> None:
            try:
                store.update(job_id, status=JobStatus.running, result=result, error=None)
            except StoreClosedError:
                raise
            except Exception as exc:
                # A transient store failure must not fail an otherwise healthy
                # pipeline: the intermediate progress update is not critical, and
                # the final write persists the complete result.
                logger.warning(
                    "Job %s: could not persist intermediate progress (%s: %s)", job_id, type(exc).__name__, exc
                )

        if audio_bytes is not None:
            suffix = Path(filename or "input.wav").suffix or ".wav"
            with NamedTemporaryFile(delete=True, suffix=suffix) as temp_file:
                temp_file.write(audio_bytes)
                temp_file.flush()
                result = pipeline_service.run(request, audio_path=Path(temp_file.name), on_update=on_update)
        else:
            result = pipeline_service.run(request, on_update=on_update)
    except StoreClosedError:
        # Shutdown is in progress: the job is left in its last persisted state and
        # the next startup's recovery marks it failed.
        logger.warning("Job %s: job store closed during run; status left for startup recovery", job_id)
        return
    except Exception as exc:
        try:
            store.update(job_id, status=JobStatus.failed, error=str(exc))
        except StoreClosedError:
            # Log the original exception too: without it the real failure reason
            # is permanently masked behind the later interrupted-by-server-restart
            # label that startup recovery writes.
            logger.warning(
                "Job %s: job store closed while recording failure (%s: %s); status left for startup recovery",
                job_id,
                type(exc).__name__,
                exc,
            )
        except Exception:
            logger.exception("Job %s: could not record failure after error: %s", job_id, exc)
        return

    # The pipeline produced a result; record it. A store failure here must never
    # mark the job failed with the store error: that would report a pipeline
    # failure that never happened (a completed job marked failed).
    failed_stage = next((stage for stage in result.stages if stage.status == JobStatus.failed), None)
    try:
        if failed_stage is not None:
            store.update(job_id, status=JobStatus.failed, result=result, error=failed_stage.summary)
        else:
            store.update(job_id, status=JobStatus.completed, result=result, error=None)
    except StoreClosedError:
        # Shutdown is in progress: the job is left in its last persisted state and
        # the next startup's recovery marks it failed.
        logger.warning("Job %s: job store closed while recording result; status left for startup recovery", job_id)
    except Exception as exc:
        # A transient store failure leaves the job in its last persisted state:
        # the result is already unrecoverable by the client, and the next
        # startup's recovery marks the job failed (honest: the client can never
        # retrieve the result).
        logger.exception(
            "Job %s: could not record pipeline result (%s: %s); job left in its last persisted state",
            job_id,
            type(exc).__name__,
            exc,
        )


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
    # Read the upload BEFORE persisting the job: an unreadable upload must not
    # leave an orphaned queued job (persisted but never scheduled, stuck until a
    # restart relabels it), and the read failure must map to a declared error
    # (503), not an unmodeled 500 (the route models only 200/400/503/422).
    if audio is not None:
        try:
            audio_bytes = audio.file.read()
        except OSError as exc:
            logger.exception("Could not read uploaded audio")
            raise HTTPException(status_code=503, detail="Klarte ikke å lese opplastet lydfil akkurat nå") from exc
    else:
        audio_bytes = None
    filename = audio.filename if audio is not None else None

    # The task keeps the store instance it was created with: after shutdown the
    # instance is closed and refuses writes, so a zombie task can never resurrect
    # a job that the next startup's recovery already failed.
    try:
        # get_job_store() sits inside the guard: a failing open on the lazy build
        # path (locked or corrupt DB) is the same store-failure class as
        # create() and must map to the declared 503, not an unmodeled 500.
        store = get_job_store()
        record = store.create(JobRecord(request=request))
    except Exception as exc:
        # A store failure here (closed store during shutdown, locked or full DB,
        # or a failing open) must surface as a declared error: the route models
        # only 400/503, and the in-memory predecessor could not fail at this
        # point.
        logger.exception("Could not persist new pipeline job")
        raise HTTPException(status_code=503, detail="Jobblageret er utilgjengelig: kan ikke lagre jobben akkurat nå") from exc
    background_tasks.add_task(_run_pipeline, store, record.id, request, audio_bytes, filename)
    return record
