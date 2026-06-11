from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import shlex
import struct
import subprocess
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
import wave
from pathlib import Path
from shutil import which
from typing import Protocol


class SpeechProvider(Protocol):
    def phonemize(self, text: str) -> str: ...

    def synthesize(self, text: str, voice: str | None = None) -> str | None: ...


@dataclass(frozen=True)
class SpeechSynthesisConfig:
    runtime: str = "unconfigured"
    api_base_url: str | None = None
    command: str | None = None
    command_cwd: Path | None = None
    voice_model: Path | None = None
    vocoder_model: Path | None = None
    available_voices: tuple[str, ...] = ()
    default_voice: str | None = None
    speaker_id: int = 1
    language_id: int = 1
    pace: float = 1.0


_VARIANT_FREQUENCIES = {
    "sme": 440.0,
    "smj": 554.37,
    "sma": 659.25,
}

_VARIANT_TTS_API_TAGS = {
    "sme": "se",
    "smj": "smj",
    "sma": "sma",
}


def write_demo_wav(*, variant: str, text: str, artifacts_dir: Path) -> str:
    variant_dir = artifacts_dir / variant
    variant_dir.mkdir(parents=True, exist_ok=True)

    digest = hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]
    file_path = variant_dir / f"demo-{digest}.wav"
    if not file_path.exists():
        _render_demo_wav(file_path, _VARIANT_FREQUENCIES.get(variant, 440.0))
    return f"/artifacts/{variant}/{file_path.name}"


def synthesize_with_config(
    *,
    variant: str,
    text: str,
    artifacts_dir: Path,
    stub_mode: bool,
    config: SpeechSynthesisConfig,
    voice: str | None = None,
) -> str:
    if stub_mode:
        return write_demo_wav(variant=variant, text=text, artifacts_dir=artifacts_dir)
    if config.runtime == "divvun-api":
        return _run_divvun_api(variant=variant, text=text, artifacts_dir=artifacts_dir, config=config, voice=voice)
    if config.runtime == "divvun-command":
        return _run_divvun_command(variant=variant, text=text, artifacts_dir=artifacts_dir, config=config, voice=voice)
    raise RuntimeError(
        "Ingen TTS-runtime er konfigurert. Sett HSJS_TTS_RUNTIME til divvun-api eller divvun-command."
    )


def _run_divvun_api(
    *,
    variant: str,
    text: str,
    artifacts_dir: Path,
    config: SpeechSynthesisConfig,
    voice: str | None,
) -> str:
    if not config.api_base_url:
        raise RuntimeError("HSJS_TTS_API_BASE_URL ma settes for divvun-api-runtime")

    selected_voice = _resolve_voice(variant=variant, requested_voice=voice, config=config)
    variant_dir = artifacts_dir / variant
    variant_dir.mkdir(parents=True, exist_ok=True)
    digest_source = "|".join([text.strip(), selected_voice, config.api_base_url.rstrip("/")])
    digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:16]
    output_path = variant_dir / f"divvun-api-{selected_voice}-{digest}.mp3"
    if output_path.exists():
        return f"/artifacts/{variant}/{output_path.name}"

    api_variant = _VARIANT_TTS_API_TAGS.get(variant, variant)

    request = Request(
        f"{config.api_base_url.rstrip('/')}/{quote(api_variant)}/{quote(selected_voice)}",
        data=json.dumps({"text": text.strip()}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "audio/mpeg"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            audio_bytes = response.read()
    except HTTPError as exc:
        if exc.code == 404:
            raise RuntimeError(f"Stemmen {selected_voice} finnes ikke for {variant} i Divvun API") from exc
        raise RuntimeError(f"Divvun API TTS feilet med HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"Fant ikke Divvun API TTS-endepunktet: {config.api_base_url}") from exc

    if not audio_bytes:
        raise RuntimeError("Divvun API returnerte tom lydrespons")

    output_path.write_bytes(audio_bytes)
    return f"/artifacts/{variant}/{output_path.name}"


def _run_divvun_command(
    *,
    variant: str,
    text: str,
    artifacts_dir: Path,
    config: SpeechSynthesisConfig,
    voice: str | None,
) -> str:
    if voice is not None:
        raise RuntimeError("Eksplisitt stemmevalg stottes ikke for divvun-command-runtime")
    if not config.command:
        raise RuntimeError("HSJS_TTS_COMMAND ma settes for divvun-command-runtime")
    if _resolve_command_path(config.command) is None:
        raise RuntimeError(
            f"Fant ikke divvun-speech-kommandoen for {variant}: {config.command}. Sjekk HSJS_TTS_COMMAND og PATH."
        )
    if config.voice_model is None or not config.voice_model.exists():
        raise RuntimeError(f"Fant ikke voice-modell for {variant}: {config.voice_model}")
    if config.vocoder_model is None or not config.vocoder_model.exists():
        raise RuntimeError(f"Fant ikke vocoder-modell for {variant}: {config.vocoder_model}")

    variant_dir = artifacts_dir / variant
    variant_dir.mkdir(parents=True, exist_ok=True)
    digest_source = "|".join(
        [
            text.strip(),
            str(config.voice_model),
            str(config.vocoder_model),
            str(config.speaker_id),
            str(config.language_id),
            str(config.pace),
        ]
    )
    digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:16]
    output_path = variant_dir / f"divvun-{digest}.wav"
    if output_path.exists():
        return f"/artifacts/{variant}/{output_path.name}"

    command = [
        *shlex.split(config.command),
        str(config.voice_model),
        str(config.vocoder_model),
        text.strip(),
        str(output_path),
        "--pace",
        str(config.pace),
        "--speaker",
        str(config.speaker_id),
        "--language",
        str(config.language_id),
    ]
    try:
        subprocess.run(
            command,
            check=True,
            cwd=str(config.command_cwd) if config.command_cwd is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(exc.stderr.strip() or "divvun-speech-kommandoen feilet") from exc

    if not output_path.exists():
        raise RuntimeError("divvun-speech-kommandoen fullforte uten a skrive forventet WAV-fil")
    return f"/artifacts/{variant}/{output_path.name}"


def _resolve_voice(*, variant: str, requested_voice: str | None, config: SpeechSynthesisConfig) -> str:
    available_voices = tuple(voice.lower() for voice in config.available_voices)
    if requested_voice is not None and requested_voice.strip():
        normalized_voice = requested_voice.strip().lower()
        if available_voices and normalized_voice not in available_voices:
            raise RuntimeError(
                f"Stemmen {normalized_voice} er ikke tilgjengelig for {variant}. Tilgjengelige stemmer: {', '.join(available_voices)}"
            )
        return normalized_voice

    if config.default_voice:
        return config.default_voice.lower()
    if available_voices:
        return available_voices[0]
    raise RuntimeError(f"Ingen stemmer er konfigurert for {variant}")


def _render_demo_wav(file_path: Path, frequency: float) -> None:
    sample_rate = 16000
    duration_s = 0.85
    frame_count = int(sample_rate * duration_s)
    amplitude = 0.2

    frames = bytearray()
    for index in range(frame_count):
        fade = min(index / 800, (frame_count - index) / 800, 1.0)
        sample = amplitude * fade * math.sin((2 * math.pi * frequency * index) / sample_rate)
        frames.extend(struct.pack("<h", int(sample * 32767)))

    with wave.open(str(file_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(bytes(frames))


def _resolve_command_path(command: str) -> Path | None:
    parts = shlex.split(command)
    if not parts:
        return None
    executable = parts[0]
    if "/" in executable:
        executable_path = Path(executable)
        return executable_path if executable_path.exists() else None
    resolved = which(executable)
    return Path(resolved) if resolved is not None else None