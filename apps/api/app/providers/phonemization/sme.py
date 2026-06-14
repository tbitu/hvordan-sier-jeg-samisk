from __future__ import annotations

from app.providers.phonemization.base import PHONEME_PREFIX, _normalize_text, phonemize_text


# ── North Sami (sme) variant-specific rules ────────────────────────
# SME has ~10 vowels: a, e, i, o, u, y, â, ä + length contrasts

NORTH_SAMI_EXTRA_CHARS = {
    # Additional graphemes specific to sme
    "â": f"{PHONEME_PREFIX}ô",  # back rounded mid vowel [ɔ] — sme only
    "ä": f"{PHONEME_PREFIX}æ",  # front open-mid [æ ~ ɛ] — sme only
    "á": f"{PHONEME_PREFIX}aː",  # long/a-toned a
    "â": f"{PHONEME_PREFIX}ô",   # back rounded mid vowel

    # Vowel length markers: geminated following consonant in orthography
    # These are handled by context; we don't need explicit rules for them
    # since the phoneme tokens keep geminate consonants as-is
}


def phonemize(text: str) -> str:
    """Phonemize North Sami text to phoneme representation.

    SME uses full phoneme modeling with rich vowel inventory including
    /ä/ and /â/ (back rounded mid). Length is marked through context
    (geminate consonants or explicitly long vowels like á).

    Args:
        text: Input text in North Sami orthography.

    Returns:
        Phonemic string in /sme/ format with $-prefixed tokens.
    """
    normalized = _normalize_text(text)
    return phonemize_text(normalized, extra_single_rules=NORTH_SAMI_EXTRA_CHARS)


# ── Example mappings for documentation/testing ───────────────────────
EXAMPLES: list[tuple[str, str]] = [
    # (sami_orthography -> expected_phonemes_without_tag)
    ("buorre", f"$b o r r e"),                    # good
    ("čáhci", f"$c $a $s h i"),                   # water
    ("giitu", f"$g i i t u"),                     # thanks
    ("moarri", f"$m o a r r i"),                  # young man
    ("muotka", f"$m u $ô t k a"),                 # fjord (湾)
]


def example_mappings() -> list[tuple[str, str]]:
    """Return example mappings for documentation."""
    return EXAMPLES
