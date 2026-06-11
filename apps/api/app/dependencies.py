from __future__ import annotations

from app.core.settings import get_settings
from app.pipeline.service import PipelineService
from app.providers.asr.nb_whisper import NbWhisperProvider
from app.providers.speech.common import SpeechProvider, SpeechSynthesisConfig
from app.providers.speech.registry import get_default_tts_voice, get_variant_tts_voices
from app.providers.speech.sma import SouthSamiSpeechProvider
from app.providers.speech.sme import NorthSamiSpeechProvider
from app.providers.speech.smj import LuleSamiSpeechProvider
from app.providers.translation.tahetorn import TahetornProvider
from app.state import InMemoryJobStore


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


settings = get_settings()
job_store = InMemoryJobStore()
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
