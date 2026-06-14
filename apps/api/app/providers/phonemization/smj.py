from __future__ import annotations

from app.providers.phonemization.base import PHONEME_PREFIX, _normalize_text, phonemize_text


# ── Lule Sami (smj) variant-specific rules ─────────────────────────
# SMJ has ~6 vowels: a, e, i, o, u, y — NO /ä/ or /â/, simpler system.
# Vowel /ä/ merges with /e/ or /æ/, and /â/ merges with plain /a/.

LULE_SAMI_EXTRA_CHARS = {
    # Variant-specific vowel handling: merge sme-only vowels into smj inventory
    "ä": f"{PHONEME_PREFIX}e",  # /ä/ → [e] in Lule Sami
    "â": f"{PHONEME_PREFIX}a",  # back rounded merges with plain a
    "á": f"{PHONEME_PREFIX}aː",  # long/a-toned a

    # ž (voiced postalveolar fricative) is more common in smj than sme
    "ž": f"{PHONEME_PREFIX}z",

    # More nasal vowels prominent in smj
    "ŋ": f"{PHONEME_PREFIX}n",
}


def phonemize(text: str) -> str:
    """Phonemize Lule Sami text to phoneme representation.

    SMJ has a simpler vowel system than SME (no /ä/ or /â/), but has
    more nasal features and the voiced postalveolar fricative /ž/.

    Args:
        text: Input text in Lule Sami orthography.

    Returns:
        Phonemic string in /smj/ format with $-prefixed tokens.
    """
    normalized = _normalize_text(text)
    return phonemize_text(normalized, extra_single_rules=LULE_SAMI_EXTRA_CHARS)


# ── Example mappings for documentation/testing ───────────────────────
EXAMPLES: list[tuple[str, str]] = [
    ("buorre", f"$b o r r e"),     # good (similar to sme)
    ("giitu", f"$g i i t u"),      # thanks (similar to sme)
]


def example_mappings() -> list[tuple[str, str]]:
    """Return example mappings for documentation."""
    return EXAMPLES
