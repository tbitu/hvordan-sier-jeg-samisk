from __future__ import annotations

import argparse

from app.dependencies import speech_providers


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke-test lokal TTS-provider")
    parser.add_argument("text", help="Samisk tekst som skal syntetiseres")
    parser.add_argument("--variant", default="sme", choices=["sme", "smj", "sma"], help="Samisk variant")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    provider = speech_providers[args.variant]
    audio_url = provider.synthesize(args.text)
    if audio_url is None:
        raise SystemExit(f"Varianten {args.variant} har ikke aktiv audio-stotte i denne konfigurasjonen")
    print(audio_url)


if __name__ == "__main__":
    main()