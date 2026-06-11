from __future__ import annotations

import argparse

from app.core.settings import get_settings
from app.providers.translation.tahetorn import TahetornProvider


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke-test Tahetorn_9B i lokal runtime")
    parser.add_argument("text", help="Norsk tekst som skal oversettes")
    parser.add_argument("--variant", default="sme", choices=["sme", "smj", "sma"], help="Malsprakvariant")
    parser.add_argument("--device", default=None, help="Overstyr HSJS_HF_DEVICE, for eksempel cpu eller auto")
    parser.add_argument("--dtype", default=None, help="Overstyr HSJS_HF_DTYPE, for eksempel float16 eller bfloat16")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    settings = get_settings()

    provider = TahetornProvider(
        model_id=settings.tahetorn_model_id,
        stub_mode=False,
        runtime=settings.provider_runtime,
        device=args.device or settings.hf_device,
        dtype=args.dtype or settings.hf_dtype,
        trust_remote_code=settings.hf_trust_remote_code,
        system_prompt=settings.translation_system_prompt,
        max_new_tokens=settings.translation_max_new_tokens,
        temperature=settings.translation_temperature,
        top_p=settings.translation_top_p,
        repetition_penalty=settings.translation_repetition_penalty,
        attn_implementation=settings.translation_attn_implementation,
        use_chat_template=settings.translation_use_chat_template,
    )

    translation = provider.translate(args.text, args.variant)
    print(translation)


if __name__ == "__main__":
    main()