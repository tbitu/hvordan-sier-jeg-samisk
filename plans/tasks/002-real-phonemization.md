# Implement Real Phonemization for Sami Variants

## Problem

All three Sami speech providers (sme, smj, sma) currently use stub phonemization:
- `NorthSamiSpeechProvider.phonemize()` returns `"/sme/ {text}"`
- `LuleSamiSpeechProvider.phonemize()` returns `"/smj/ {text}"`
- `SouthSamiSpeechProvider.phonemize()` returns `"/sma/ {text}"`

This just prepends a variant tag with no actual phoneme-level transformation. Blocking for anyone wanting real linguistic output.

## Scope

### Allowed
- Create a new module `apps/api/app/providers/phonemization/` with:
  - `__init__.py` - exports a `phonemize(text: str, variant: VariantCode) -> str` function
  - `base.py` - shared phonemization utilities (Norwegian->Sami orthographic conversion rules)
  - `sme.py` - North Sami phonemization rules
  - `smj.py` - Lule Sami phonemization rules
  - `sma.py` - South Sami phonemization rules (phoneme-first path)
- Update each speech provider to delegate to the phonemization module
- Keep `stub_mode` respect: phonemization runs regardless of stub_mode (it produces text, not audio)
- Add unit tests in `apps/api/tests/test_phonemization.py`
- Update `packages/contracts/openapi.yaml` if any schema changes are needed
- Update `README.md` or docs if phonemization output format changes

### Not Allowed
- No changes to audio synthesis logic
- No changes to ASR/transcription pipeline
- No changes to translation (Tahetorn) provider
- No new runtime dependencies that require system-level installs (prefer pure Python)
- No cloud API calls

## Approach

1. **Research existing resources**: Check if any Giellalt resources, DUTCH rules, or minimal rule-based mappings exist for Norwegian->Sami orthographic conversion
2. **Implement shared rules** (`base.py`):
   - Norwegian->Sami consonant/vowel mapping tables (common patterns across variants)
   - Context-aware substitution (e.g., Norwegian "kj" -> Sami "g", "sk" -> Sami "sk", etc.)
   - Vowel harmony rules (variant-specific)
3. **Implement variant-specific rules**:
   - `sme`: Full phoneme modeling (current capability level)
   - `smj`: Full phoneme modeling (current capability level)
   - `sma`: Phoneme-first approach (current capability level, limited audio)
4. **Wire into providers**: Each provider's `phonemize()` delegates to the module
5. **Test**: Unit tests covering representative Norwegian input -> expected Sami phoneme output

## Deliverables

- `apps/api/app/providers/phonemization/` module with working rules
- Updated `sme.py`, `smj.py`, `sma.py` providers
- Unit tests in `apps/api/tests/test_phonemization.py`
- No regression in existing synthesis behavior
