from __future__ import annotations

import logging
from threading import Lock

from app.core.settings import Settings, get_settings
from app.domain import JobStatus
from app.persistence import SQLiteJobStore
from app.pipeline.service import PipelineService
from app.providers.asr.nb_whisper import NbWhisperProvider
from app.providers.speech.common import SpeechProvider, SpeechSynthesisConfig
from app.providers.speech.registry import get_default_tts_voice, get_variant_tts_voices
from app.providers.speech.sma import SouthSamiSpeechProvider
from app.providers.speech.sme import NorthSamiSpeechProvider
from app.providers.speech.smj import LuleSamiSpeechProvider
from app.providers.translation.tahetorn import TahetornProvider
from app.state import InMemoryJobStore, JobStore

logger = logging.getLogger(__name__)


def _build_speech_config(variant: str) -> SpeechSynthesisConfig:
    available_voices = tuple(voice.voice for voice in get_variant_tts_voices(variant))
    default_voice = get_default_tts_voice(variant)
    return SpeechSynthesisConfig(
        runtime=settings.tts_runtime,
        api_base_url=settings.tts_api_base_url,
        command=settings.tts_command or None,
        command_cwd=settings.tts_command_cwd,
        voice_model=getattr(settings, f"tts_{variant}_voice_model"),
        vocoder_model=getattr(settings, f"tts_{variant}_vocoder_model"),
        available_voices=available_voices,
        default_voice=default_voice.voice if default_voice is not None else None,
        speaker_id=getattr(settings, f"tts_{variant}_speaker_id"),
        language_id=getattr(settings, f"tts_{variant}_language_id"),
        pace=getattr(settings, f"tts_{variant}_pace"),
    )


def _build_job_store(settings: Settings) -> JobStore:
    if settings.job_store_backend == "memory":
        return InMemoryJobStore()
    if settings.job_store_backend == "sqlite":
        return SQLiteJobStore(settings.db_path)
    raise ValueError(f"Unsupported job store backend: {settings.job_store_backend!r}")


_job_store: JobStore | None = None
_job_store_lock = Lock()


def get_job_store() -> JobStore:
    global _job_store
    with _job_store_lock:
        if _job_store is None:
            _job_store = _build_job_store(get_settings())
        return _job_store


def close_job_store() -> None:
    global _job_store
    # Swap the global under the lock, but close OUTSIDE it: close() can block on
    # the store's own lock while a beat or write holds it across a conn.execute
    # for the full busy timeout, so holding the global lock during close would
    # stall shutdown and every concurrent get_job_store() for the full duration
    # (and a process killed mid-close would skip the liveness-marker removal).
    # A concurrent get_job_store() during the close window may build a new store
    # on the same path: that is safe, because the new store's count increment
    # and the closing store's marker delete are serialized by the same registry
    # lock (the increment happens before the new store's marker registration
    # commit): the increment either lands before the delete (the close then does
    # not see last_instance and keeps the marker) or after it (the new store's
    # registration commit re-inserts the marker the delete removed).
    with _job_store_lock:
        store, _job_store = _job_store, None
        if store is None:
            return
    close = getattr(store, "close", None)
    if callable(close):
        close()


def recover_interrupted_jobs(store: JobStore, *, grace_seconds: float | None = None) -> int:
    """Mark jobs left queued/running by an unclean restart as failed.

    A crashed process abandons its in-flight background tasks, so any job still in
    a non-terminal state at startup can never complete. Recovering them to a
    terminal ``failed`` state keeps the job list honest instead of stuck forever.
    Returns the number of jobs recovered.

    Cross-process exclusion: a non-terminal job is left untouched when a live
    peer may still be working on it (it started at or before the job's last
    update; the compose deployment mounts one shared volume, so a scaled
    deployment boots several processes on one DB). A peer that started after the
    job's last update cannot be working on it, so concurrent startups on a
    shared DB still recover a dead predecessor's jobs. The supported
    configuration remains one API instance per job store; see the README.

    The whole pass works from one consistent snapshot (SQLiteJobStore.
    recovery_snapshot): the protection set, the listed records and the raw
    non-terminal ids come from the same read transaction, so a live peer that
    commits a job while this pass is running is either in the snapshot (and in
    the protection set when it may be working on it) or after it (and invisible
    to the pass). Three separate reads let such a commit land in the raw scan
    but outside the protection set, failing a live peer's job.

    Poisoned non-terminal rows (valid queued/running status, unreadable JSON)
    are quarantined from the snapshot's records and would otherwise be
    invisible to this loop, stuck non-terminal forever: they are failed via the
    snapshot's raw-id scan, since the write path does not parse the row and the
    terminal state is durable even though the record stays unreadable.
    """
    if isinstance(store, SQLiteJobStore):
        if grace_seconds is None:
            grace_seconds = get_settings().recovery_grace_s
        snapshot = store.recovery_snapshot(grace_seconds)
        records = snapshot.records
        raw_non_terminal = snapshot.non_terminal_ids
        protected = snapshot.protected
    else:
        records = store.list()
        raw_non_terminal = frozenset()
        protected = frozenset()
    recovered = 0
    listed_ids: set[str] = set()
    for record in records:
        listed_ids.add(record.id)
        if record.status not in (JobStatus.queued, JobStatus.running):
            continue
        if record.id in protected:
            logger.warning(
                "Leaving job %s (%s) untouched: a live API process may still be working on it. "
                "Run one API instance per job store, or give each replica its own HSJS_DB_PATH.",
                record.id,
                record.status.value,
            )
            continue
        store.update(record.id, status=JobStatus.failed, error="interrupted by server restart")
        recovered += 1
    for job_id in raw_non_terminal - listed_ids:
        if job_id in protected:
            logger.warning(
                "Leaving job %s untouched: a live API process may still be working on it. "
                "Run one API instance per job store, or give each replica its own HSJS_DB_PATH.",
                job_id,
            )
            continue
        store.update(job_id, status=JobStatus.failed, error="interrupted by server restart")
        recovered += 1
    return recovered


settings = get_settings()
speech_providers: dict[str, SpeechProvider] = {
    "sme": NorthSamiSpeechProvider(
        settings.artifacts_dir,
        stub_mode=settings.provider_stub_mode,
        synth_config=_build_speech_config("sme"),
    ),
    "smj": LuleSamiSpeechProvider(
        settings.artifacts_dir,
        stub_mode=settings.provider_stub_mode,
        synth_config=_build_speech_config("smj"),
    ),
    "sma": SouthSamiSpeechProvider(
        settings.artifacts_dir,
        stub_mode=settings.provider_stub_mode,
        synth_config=_build_speech_config("sma"),
    ),
}
pipeline_service = PipelineService(
    asr=NbWhisperProvider(
        model_id=settings.whisper_model_id,
        stub_mode=settings.provider_stub_mode,
        runtime=settings.provider_runtime,
        language=settings.whisper_language,
        chunk_length_s=settings.whisper_chunk_length_s,
        batch_size=settings.whisper_batch_size,
        num_beams=settings.whisper_num_beams,
        return_timestamps=settings.whisper_return_timestamps,
        device=settings.hf_device,
        dtype=settings.hf_dtype,
        trust_remote_code=settings.hf_trust_remote_code,
        attn_implementation=settings.whisper_attn_implementation,
    ),
    translator=TahetornProvider(
        model_id=settings.tahetorn_model_id,
        stub_mode=settings.provider_stub_mode,
        runtime=settings.provider_runtime,
        device=settings.hf_device,
        dtype=settings.hf_dtype,
        trust_remote_code=settings.hf_trust_remote_code,
        system_prompt=settings.translation_system_prompt,
        max_new_tokens=settings.translation_max_new_tokens,
        temperature=settings.translation_temperature,
        top_p=settings.translation_top_p,
        repetition_penalty=settings.translation_repetition_penalty,
        attn_implementation=settings.translation_attn_implementation,
        use_chat_template=settings.translation_use_chat_template,
    ),
    speech_providers=speech_providers,
)
