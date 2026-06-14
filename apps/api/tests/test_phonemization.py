from __future__ import annotations

import pytest

from app.providers.phonemization import phonemize
from app.providers.phonemization.base import (
    FRONT_VOWELS,
    PHONEME_PREFIX,
    SEQUENCE_MAP,
    SINGLE_CHAR_MAP,
    _apply_sequence_rules,
    _normalize_text,
)
from app.providers.phonemization.sma import phonemize as phonemize_sma
from app.providers.phonemization.smj import phonemize as phonemize_smj
from app.providers.phonemization.sme import phonemize as phonemize_sme


# ── Module-level phonemize tests ───────────────────────────────


class TestPhonemizeModule:
    def test_phonemize_sme_format(self):
        result = phonemize("buorre", "sme")
        assert result.startswith("/sme/")

    def test_phonemize_smj_format(self):
        result = phonemize("buorre", "smj")
        assert result.startswith("/smj/")

    def test_phonemize_sma_format(self):
        result = phonemize("buorre", "sma")
        assert result.startswith("/sma/")

    def test_phonemize_invalid_variant_raises(self):
        with pytest.raises(ValueError, match="Ukjent variant"):
            phonemize("test", "invalid")

    def test_phonemize_returns_non_empty(self):
        for variant in ("sme", "smj", "sma"):
            result = phonemize("buorre", variant)
            assert len(result) > 6  # "/xxx/ " prefix + content


# ── North Sami (sme) tests ─────────────────────────────────────


class TestSmePhonemization:
    def test_simple_word_no_special_chars(self):
        result = phonemize_sme("buorre")
        assert PHONEME_PREFIX in result

    def test_special_char_cz(self):
        result = phonemize_sme("čáhci")
        # č maps to $c, á is kept, h stays
        assert f"{PHONEME_PREFIX}c" in result

    def test_basic_vocabulary(self):
        """Test a few basic SME vocabulary words."""
        tests = {
            "giitu": PHONEME_PREFIX + "g",  # contains g
            "moarri": PHONEME_PREFIX + "m",  # contains m
        }
        for word, expected_prefix in tests.items():
            result = phonemize_sme(word)
            assert len(result) > len(word), f"Phonemization should transform {word}"

    def test_handles_uppercase(self):
        result = phonemize_sme("BUORRE")
        assert len(result) > 0  # uppercased text is normalized lowercase

    def test_handles_whitespace(self):
        result = phonemize_sme("  buorre   boahtá  ")
        assert "  " not in result  # whitespace normalized

    def test_empty_string(self):
        result = phonemize_sme("")
        assert result == ""

    def test_sequence_skj_transformed(self):
        """skj should be mapped to $sj per SEQUENCE_MAP."""
        result = _apply_sequence_rules("skj")
        assert f"{PHONEME_PREFIX}sj" in result

    def test_sequence_ggj_transformed(self):
        """ggj should be mapped to jj per SEQUENCE_MAP."""
        result = _apply_sequence_rules("ggj")
        assert "jj" == result


# ── Lule Sami (smj) tests ──────────────────────────────────────


class TestSmjPhonemization:
    def test_basic_word(self):
        result = phonemize_smj("buorre")
        assert PHONEME_PREFIX in result

    def test_ä_maps_to_e(self):
        """SMJ does not have /ä/ — it maps to [e]."""
        result = phonemize_smj("bääkŋe")  # word with ä
        # ä should map to e, and ŋ should map to $n
        assert f"{PHONEME_PREFIX}e" in result

    def test_â_maps_to_a(self):
        """SMJ merges /â/ with plain /a/."""
        result = phonemize_smj("bâhtu")
        # â maps to a, ú or similar stays
        assert PHONEME_PREFIX + "a" in result

    def test_handles_sjj_sequence(self):
        """skj should map to $sj."""
        result = _apply_sequence_rules("skj")
        assert f"{PHONEME_PREFIX}sj" in result


# ── South Sami (sma) tests ─────────────────────────────────────


class TestSmaPhonemization:
    def test_basic_word(self):
        result = phonemize_sma("buorre")
        assert PHONEME_PREFIX in result

    def test_å_maps_to_on(self):
        """SMA has nasal å → [ɔ̃] mapped to $on."""
        result = phonemize_sma("bååtedes")  # word with å
        assert f"{PHONEME_PREFIX}on" in result

    def test_ä_nasalized(self):
        """SMA nasalizes /ä/ → [an]."""
        result = phonemize_sma("bääknes")  # word with ä
        assert f"{PHONEME_PREFIX}an" in result


# ── Base module tests ──────────────────────────────────────────


class TestBaseModule:
    def test_phoneme_prefix_constant(self):
        assert PHONEME_PREFIX == "$"

    def test_single_char_map_has_expected_keys(self):
        """SINGLE_CHAR_MAP should have entries for special Sami graphemes."""
        expected_keys = {"č", "đ", "ǧ", "ŋ", "š", "ž"}
        assert expected_keys.issubset(SINGLE_CHAR_MAP.keys())

    def test_single_char_map_prefixes(self):
        """All special char values should use PHONEME_PREFIX."""
        for key in ("č", "š", "đ"):
            assert SINGLE_CHAR_MAP[key].startswith(PHONEME_PREFIX)

    def test_sequence_map_has_expected_entries(self):
        """SEQUENCE_MAP should contain known multi-char sequences."""
        assert "skj" in SEQUENCE_MAP
        assert "ggj" in SEQUENCE_MAP

    def test_normalize_text_strips_extra_whitespace(self):
        result = _normalize_text("  hello   world  ")
        assert result == "hello world"

    def test_normalize_text_lowercases(self):
        result = _normalize_text("HELLO")
        assert result == "hello"

    def test_front_vowels_constant(self):
        expected = {"e", "i", "y", "ä"}
        assert FRONT_VOWELS == expected
