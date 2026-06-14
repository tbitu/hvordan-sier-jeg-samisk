from __future__ import annotations


# ── Common phoneme prefix (keeps TTS systems ASCII-compatible) ────
PHONEME_PREFIX = "$"

# ── Base single-character mapping rules ─────────────────────────────
# These apply to any Sami variant; order matters (longer sequences first)
SINGLE_CHAR_MAP: dict[str, str] = {
    # Special Sami graphemes
    "č": f"{PHONEME_PREFIX}c",   # voiceless affricate [tʃ]
    "đ": f"{PHONEME_PREFIX}d",   # voiced palatal approximant/fricative [ɟ ~ ʝ]
    "ǧ": f"{PHONEME_PREFIX}g",   # voiced affricate [dʒ] — rare, mainly smj/sma
    "ŋ": f"{PHONEME_PREFIX}n",   # velar nasal
    "š": f"{PHONEME_PREFIX}s",   # voiceless postalveolar fricative [ʃ]
    "ž": f"{PHONEME_PREFIX}z",   # voiced postalveolar fricative [ʒ] — mainly smj/sma

    # Standard consonants (pass-through, but could be marked if needed)
    "k": "k",
    "g": "g",
    "t": "t",
    "d": "d",
    "p": "p",
    "b": "b",
    "m": "m",
    "n": "n",
    "h": "h",
    "r": "r",
    "f": "f",
    "j": "j",
    "s": "s",
    "v": f"{PHONEME_PREFIX}f",   # labiovelar approximant [ʋ ~ v]
    "l": "$l",                    # palatal lateral when geminated contextually

    # Vowels (base forms; length is inferred from context/gemination)
    "a": f"{PHONEME_PREFIX}a",
    "e": f"{PHONEME_PREFIX}e",
    "i": f"{PHONEME_PREFIX}i",
    "o": f"{PHONEME_PREFIX}o",
    "u": f"{PHONEME_PREFIX}u",
    "y": f"{PHONEME_PREFIX}y",   # front high rounded [y] — sme, smj
}

# ── Two- and multi-character sequences ──────────────────────────────
# Applied BEFORE single-character rules to avoid partial matches
SEQUENCE_MAP: dict[str, str] = {
    "skj": f"{PHONEME_PREFIX}sj",   # palatal sibilant + glide [ʃ]
    "ggj": "jj",                    # Norwegian "ggj" → Sami geminate glide
    "nj": f"{PHONEME_PREFIX}ň",     # palatal nasal [ɲ] — sme, smj
    "sk ": f"{PHONEME_PREFIX}s ",   # sk before back vowels stays /sk/ (space-delimited)
}

# ── Front vowel set for context-aware rules ─────────────────────────
FRONT_VOWELS = frozenset(["e", "i", "y", "ä"])


def _normalize_text(text: str) -> str:
    """Strip extra whitespace and normalize Unicode."""
    return " ".join(text.lower().split())


def _apply_sequence_rules(text: str) -> str:
    """Apply multi-character substitution rules before single-char rules."""
    result = text
    # Apply longest sequences first to avoid partial matches
    for seq, repl in sorted(SEQUENCE_MAP.items(), key=lambda x: -len(x[0])):
        result = result.replace(seq, repl)
    return result


def _apply_single_rules(text: str, char_map: dict[str, str]) -> str:
    """Apply single-character mapping rules."""
    chars = []
    for ch in text:
        chars.append(char_map.get(ch, ch))
    return "".join(chars)


def phonemize_text(text: str, extra_single_rules: dict[str, str] | None = None) -> str:
    """Core phonemization pipeline with shared rules.

    Args:
        text: Sami orthography text.
        extra_single_rules: Variant-specific single-char overrides.

    Returns:
        Phonemic representation (without variant tag).
    """
    normalized = _normalize_text(text)
    # Step 1: Apply sequence rules first
    result = _apply_sequence_rules(normalized)
    # Step 2: Merge char maps (base + extra per-variant)
    merged_map = dict(SINGLE_CHAR_MAP)
    if extra_single_rules:
        merged_map.update(extra_single_rules)
    # Step 3: Apply single-char rules
    result = _apply_single_rules(result, merged_map)
    return result.strip()
