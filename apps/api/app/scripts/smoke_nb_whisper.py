from __future__ import annotations

import argparse
from pathlib import Path

from app.core.settings import get_settings
from app.providers.asr.nb_whisper import NbWhisperProvider


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke-test nb-whisper-large i lokal runtime")
    parser.add_argument("audio_path", type=Path, help="Sti til WAV- eller annen stottet lydfil")
    parser.add_argument("--device", default=None, help="Overstyr HSJS_HF_DEVICE, for eksempel cpu eller auto")
    parser.add_argument("--timestamps", default=None, help="Overstyr timestamp-modus: false, true eller word")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    settings = get_settings()

    provider = NbWhisperProvider(
        model_id=settings.whisper_model_id,
        stub_mode=False,
        runtime=settings.provider_runtime,
        language=settings.whisper_language,
        chunk_length_s=settings.whisper_chunk_length_s,
        batch_size=settings.whisper_batch_size,
        num_beams=settings.whisper_num_beams,
        return_timestamps=args.timestamps or settings.whisper_return_timestamps,
        device=args.device or settings.hf_device,
        dtype=settings.hf_dtype,
        trust_remote_code=settings.hf_trust_remote_code,
        attn_implementation=settings.whisper_attn_implementation,
    )

    transcript = provider.transcribe(args.audio_path)
    print(transcript)


if __name__ == "__main__":
    main()