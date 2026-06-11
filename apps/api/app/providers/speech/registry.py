from __future__ import annotations

from app.domain import CapabilityLevel, TtsVoice, VariantCapability


VARIANT_CAPABILITIES: list[VariantCapability] = [
    VariantCapability(
        variant="sme",
        label="Nordsamisk",
        capability=CapabilityLevel.audio,
        notes="Bruker offentlig Divvun TTS-API og har flere tilgjengelige stemmer for audio.",
    ),
    VariantCapability(
        variant="smj",
        label="Lulesamisk",
        capability=CapabilityLevel.audio,
        notes="Bruker offentlig Divvun TTS-API og har flere tilgjengelige stemmer for audio.",
    ),
    VariantCapability(
        variant="sma",
        label="Sorsamisk",
        capability=CapabilityLevel.audio,
        notes="Bruker offentlig Divvun TTS-API med tilgjengelig Aanna-stemme for audio.",
    ),
]


TTS_VOICES: list[TtsVoice] = [
    TtsVoice(variant="sme", variant_label="Nordsamisk", voice="biret", label="Biret", gender="female", is_default=True),
    TtsVoice(variant="sme", variant_label="Nordsamisk", voice="mahtte", label="Mahtte", gender="male"),
    TtsVoice(variant="sme", variant_label="Nordsamisk", voice="sunna", label="Sunna", gender="female"),
    TtsVoice(variant="smj", variant_label="Lulesamisk", voice="abmut", label="Abmut", gender="male", is_default=True),
    TtsVoice(variant="smj", variant_label="Lulesamisk", voice="nihkol", label="Nihkol", gender="male"),
    TtsVoice(variant="smj", variant_label="Lulesamisk", voice="sigga", label="Sigga", gender="female"),
    TtsVoice(variant="sma", variant_label="Sorsamisk", voice="aanna", label="Aanna", gender="female", is_default=True),
]


def get_variant_capabilities() -> list[VariantCapability]:
    return VARIANT_CAPABILITIES


def get_variant_capability(variant: str) -> VariantCapability | None:
    return next((item for item in VARIANT_CAPABILITIES if item.variant == variant), None)


def get_tts_voices() -> list[TtsVoice]:
    return TTS_VOICES


def get_variant_tts_voices(variant: str) -> list[TtsVoice]:
    return [voice for voice in TTS_VOICES if voice.variant == variant]


def get_tts_voice(variant: str, voice_id: str) -> TtsVoice | None:
    normalized_voice = voice_id.strip().lower()
    return next(
        (voice for voice in TTS_VOICES if voice.variant == variant and voice.voice.lower() == normalized_voice),
        None,
    )


def get_default_tts_voice(variant: str) -> TtsVoice | None:
    voices = get_variant_tts_voices(variant)
    return next((voice for voice in voices if voice.is_default), voices[0] if voices else None)
