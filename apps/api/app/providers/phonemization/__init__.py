from __future__ import annotations


def phonemize(text: str, variant: str) -> str:
    """Convert Sami orthography to phoneme representation.

    The phonemizer receives Sami text (after Tahetorn translation from Norwegian)
    and converts it to a phonemic representation using $-prefixed tokens for
    non-standard characters, keeping TTS systems ASCII-compatible.

    Args:
        text: Input text in Sami orthography (sme, smj, or sma variant).
        variant: One of "sme", "smj", or "sma".

    Returns:
        Phoneme string with format: "/{variant}/ {phoneme_tokens}"
    """
    if variant == "sme":
        from app.providers.phonemization.sme import phonemize as _phonemize_sme

        return f"/sme/ {_phonemize_sme(text)}"
    elif variant == "smj":
        from app.providers.phonemization.smj import phonemize as _phonemize_smj

        return f"/smj/ {_phonemize_smj(text)}"
    elif variant == "sma":
        from app.providers.phonemization.sma import phonemize as _phonemize_sma

        return f"/sma/ {_phonemize_sma(text)}"
    else:
        raise ValueError(f"Ukjent variant: {variant}. Bruk sme, smj eller sma.")
