from __future__ import annotations

from app.providers.phonemization.base import PHONEME_PREFIX, _normalize_text, phonemize_text


# ── South Sami (sma) variant-specific rules ────────────────────────
# SMA has ~8-10 vowels with strong nasal quality distinctions.
# Nasal vowels are central to South Sami phonology.

SOUTH_SAMI_EXTRA_CHARS = {
    # Vowel handling: smä also has /ä/ but it's often nasally colored
    "ä": f"{PHONEME_PREFIX}an",   # /ä/ often nasally colored in SMA
    "â": f"{PHONEME_PREFIX}on",   # back rounded nasal-like in sma
    "á": f"{PHONEME_PREFIX}aː",

    # Nasal vowels: å is central in South Sami phonology → nasal [ɔ̃]
    "å": f"{PHONEME_PREFIX}on",

    # ž more common in smj/sma than sme
    "ž": f"{PHONEME_PREFIX}z",

    # ǧ used mainly in smj/sma
    "ǧ": f"{PHONEME_PREFIX}g",
}


def phonemize(text: str) -> str:
    """Phonemize South Sami text to phoneme representation.

    SMA uses a phoneme-first approach with strong nasal vowel distinctions.
    This provider keeps output simple and ASCII-compatible for TTS integration.

    Args:
        text: Input text in South Sami orthography.

    Returns:
        Phonemic string in /sma/ format with $-prefixed tokens.
    """
    normalized = _normalize_text(text)
    return phonemize_text(normalized, extra_single_rules=SOUTH_SAMI_EXTRA_CHARS)


# ── Example mappings for documentation/testing ───────────────────────
EXAMPLES: list[tuple[str, str]] = [
    ("buorre", f"$b o r r e"),     # good
]


def example_mappings() -> list[tuple[str, str]]:
    """Return example mappings for documentation."""
    return EXAMPLES
