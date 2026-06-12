# Bubble Task

Source: inline text

Document the local-vs-API Divvun TTS trade-offs and make a clear reminder note about needing local `.pte` stemmepakker (voice/vocoder models) from Divvun/Borealium as an alternative to the public Divvun TTS API.

## Context

Prosjektet bruker per default offentlig Divvun TTS-API (`api-giellalt.uit.no/tts`) for `sme`, `smj` og `sma`. Men for fullt lokale scenarier (offline, without internet) er det nødvendig å hente ned lokale stemmefiler:

- `.pte` voice- og vocoder-modeller per variant
- Bygd `divvun-speech-rs` synthesize-runner (krever Rust, CMake, EXECUTORCH_SYSROOT)
- Eller ferdig bygd binær fra Borealium/Divvun Manager

## Refleksjon

1. `sma` er **ikke** verifisert som full audio i kodebasen — lokalt vil den kreve at `aanna` stemmen finnes og fungerer med `.pte`-formatet.
2. `divvun-speech-rs` har ingen publiserte GitHub Releases for synthesize-binaren, så Linux-bruk krever selvbygging.
3. Borealium er foreløpig den tryggeste distribusjonskanalen for `.pte`-stemmefiler.
