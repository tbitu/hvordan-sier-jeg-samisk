# Hvordan sier jeg på samisk

Lokal-først plattform for norsk tale til samisk oversetting og uttale.

## Mal

Prosjektet bygges som et monorepo med:

- `apps/api`: FastAPI-kontrollplan for tale, oversetting og speech/TTS-pipeline.
- `apps/web`: lokal webfrontend med mikrofonopptak mot API-et.
- `packages/contracts`: delt API-kontrakt.
- `infra`: containere og compose-profiler for AMD/ROCm + Podman og senere DGX Spark.

## Status

Dette repoet er nå brukbart som lokal MVP for norsk tale til samisk tekst, fonemer og betinget audio. Repositoriet inneholder nå:

- FastAPI-API med jobbko, polling og pipeline for lyd eller tekst inn.
- React/Vite-frontend for live mikrofon, runtime-status og jobbprogresjon.
- Containerprofiler for ROCm, CUDA og CPU.
- Dokumentert capability-matrise for samiske varianter.
- Optional lokal transformers-runtime for nb-whisper-large og Tahetorn_9B, med stub-modus som trygg standard.
- Stub-safe audio for hele demo-løpet og ekte TTS via offentlig Divvun API for `sme`, `smj` og `sma`.

Praktisk status per variant:

- `sme`: tekst, fonemer og audio-løp kan testes. I stub-modus får du demo-WAV. Ellers brukes offentlig Divvun TTS-API med stemmene `biret`, `mahtte` og `sunna`.
- `smj`: samme arbeidsflyt som `sme`, via offentlig Divvun TTS-API med stemmene `abmut`, `nihkol` og `sigga`.
- `sma`: tekst, fonemer og audio-løp kan testes via offentlig Divvun TTS-API med stemmen `aanna`.

## Kom i gang

### Backend

```bash
cd apps/api
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
uvicorn app.main:app --reload
```

Hvis alt er riktig satt opp, skal denne health-sjekken svare med runtime-profiler og gjeldende stub-modus:

```bash
curl http://localhost:8000/api/v1/health
```

### Backend med lokal inferens

```bash
cd apps/api
python3 -m venv .venv
. .venv/bin/activate
pip install -e .[inference]
pip install --force-reinstall --index-url https://download.pytorch.org/whl/rocm7.1 torch==2.10.0 torchaudio==2.10.0
export HSJS_PROVIDER_STUB_MODE=false
export HSJS_PROVIDER_RUNTIME=transformers
export HSJS_HF_DEVICE=auto
export HSJS_HF_DTYPE=float16
uvicorn app.main:app --reload
```

Merk:

- `HSJS_PROVIDER_STUB_MODE=true` er fortsatt trygg default for utvikling uten modeller.
- Per dagens oppstrømsmatrise er nyeste stabile PyTorch `2.11.0` med ROCm `7.2`, men lokal `nb-whisper`-inferens segfaulter på denne maskinen med den kombinasjonen.
- Bruk derfor `torch==2.10.0` og `torchaudio==2.10.0` fra `rocm7.1`-indeksen i dette repoet inntil videre. Det er den nyeste kombinasjonen som er verifisert lokalt med fungerende ASR.
- `accelerate` må være installert i inference-miljøet for at Tahetorn skal kunne bruke `device_map=auto`.
- `GET /api/v1/health` rapporterer nå både `inference_runtime_ready`, `tts_command_available`, `model_cache_state` og `runtime_issues` slik at du kan se om lokal inferens faktisk er klar før du sender en jobb.
- Når du oppdaterer installasjonskommandoer eller versjoner i dette repoet, bør versjonsvalget verifiseres mot oppstrøms webkilder først, ikke gjettes fra lokal eller historisk kunnskap.
- Tahetorn_9B bruker modellens egen chat-template i transformers-runtime og kan smoke-testes lokalt via prosjekt-CLI.

### Smoke-test av nb-whisper-large

Hvis du har installert inference-avhengighetene i prosjekt-venv og vil teste ASR direkte uten API-laget:

```bash
cd apps/api
HSJS_PROVIDER_STUB_MODE=false HSJS_PROVIDER_RUNTIME=transformers HSJS_HF_DEVICE=auto HSJS_HF_DTYPE=float16 hsjs-smoke-nb-whisper /path/to/norsk-lydfil.mp3
```

Prosjektet har fallback til `imageio-ffmpeg`, så smoke-testen kan bruke MP3 uten systeminstallert `ffmpeg`.

### Smoke-test av Tahetorn_9B

Hvis du har installert inference-avhengighetene i prosjekt-venv og vil teste oversetting direkte uten API-laget:

```bash
cd apps/api
HSJS_PROVIDER_STUB_MODE=false HSJS_PROVIDER_RUNTIME=transformers HSJS_HF_DEVICE=auto HSJS_HF_DTYPE=float16 hsjs-smoke-tahetorn "Hvor er toget?" --variant sme
```

Ved avbrutt modellnedlasting kan det hende at Tahetorn sin lokale Hugging Face-cache må hentes på nytt. `GET /api/v1/health` viser dette som `model_cache_state.tahetorn.has_incomplete=true` og `inference_runtime_ready=false` når lokal cache ikke ser komplett ut. Da holder det å slette bare `models--tartuNLP--Tahetorn_9B` under brukerens Hugging Face-cache og kjøre samme kommando på nytt.

Inferensruntime spesifiserer ikke lenger noen egen modellcache som default. Standard Hugging Face-cache under `~/.cache/huggingface/hub` brukes, eller annen standardplassering dersom du allerede har satt `HF_HOME`, `HF_HUB_CACHE` eller `HUGGINGFACE_HUB_CACHE` i miljot ditt.

### TTS via Divvun API

Ny standard i dette repoet er a bruke offentlig Divvun TTS-API i stedet for lokal `divvun-speech-rs`-kommando. Minimumsoppsettet ligger i [apps/api/.env.example](apps/api/.env.example):

```bash
cd apps/api
cp .env.example .env
. .venv/bin/activate
uvicorn app.main:app --reload
```

Health-endepunktet rapporterer na om TTS-API-et er naabart og hvilke varianter som er klare i feltene `tts_api_reachable` og `tts_variants_ready`.

Frontend henter og viser alle publiserte stemmer per variant fra `/api/v1/voices` og sender valgt stemme videre i pipeline-kallet.

### Nedlasting av lokale Divvun stemmefiler (valgfritt)

Hvis du ikke vil bruke offentlig Divvun TTS-API og heller vil kjøre TTS lokalt, må du skaffe lokale `.pte`-stemmefiler manuelt:

1. Last ned stemmepakker fra [Borealium](https://borealium.eu/) eller Divvun Manager.
2. Pakk ut `voice.pte` og `vocoder.pte` for ønskede varianter (`sme`, `smj`, `sma`).
3. Bruk prosjektets hjelper for å oppdage filene og skrive `.env`:

   ```bash
   cd apps/api
   . .venv/bin/activate
   python -m app.scripts.setup_divvun_tts --variant sme --search-root ~/Downloads --write-env .env
   ```

4. Set `HSJS_TTS_RUNTIME=divvun-command` i `.env` og peik `HSJS_TTS_COMMAND` til ein ferdig bygd `divvun-speech-rs`-synthesize-runner.

**Merk:** `divvun-speech-rs` har ingen publiserte GitHub Releases for synthesize-binaren, og Linux-bygg krev Rust, CMake og `EXECUTORCH_SYSROOT`. Borealium er foreløpig den tryggeste distribusjonskanalen for `.pte`-stemmefiler. `sma`-audio er ikkje verifisert som fullt fungerande med lokale filer i denne koden.

GiellaLT/Divvun har en lokal TTS-stakk for blant annet `sme` og `smj`, og prosjektet kan nå kalle den via ekstern kommando dersom du har bygd eller pakket en lokal `divvun-speech-rs`-runner selv.

Hvis du fortsatt vil bruke full lokal `.pte`-basert TTS, kan du overstyre defaulten og sette `HSJS_TTS_RUNTIME=divvun-command` i [apps/api/.env.example](apps/api/.env.example).

For Linux-oppsett i dette repoet finnes det na ogsa en liten hjelper som finner `voice.pte` og `vocoder.pte` under lokale mapper og skriver riktige `HSJS_*`-linjer til en `.env`:

```bash
cd apps/api
. .venv/bin/activate
python -m app.scripts.setup_divvun_tts --variant sme --search-root ~/Downloads --write-env .env
```

Hvis du har reinstallert API-pakken etter denne endringen med `pip install -e .`, kan du bruke den kortere aliasen `hsjs-setup-divvun-tts` i stedet.

Hvis du allerede har bygd upstream-eksempelet fra `divvun-speech-rs`, kan du sende inn kommandoen samtidig:

```bash
cd apps/api
. .venv/bin/activate
python -m app.scripts.setup_divvun_tts \
	--variant sme \
	--search-root ~/Downloads \
	--tts-command ~/src/divvun-speech-rs/target/release/examples/synthesize \
	--write-env .env
```

Hjelperen automatiserer ikke nedlasting av stemmepakker. Borealium/Divvun Manager er fortsatt den dokumenterte distribusjonskanalen for stemmene, mens `divvun-speech-rs` ma bygges lokalt for Linux-bruk i dette repoet.

Sett opp miljøet slik at kommandoen du peker til oppfører seg som `divvun-speech-rs` sitt `examples/synthesize.rs`:

```bash
cd apps/api
cp .env.example .env
# oppdater lokale stier i .env
. .venv/bin/activate
uvicorn app.main:app --reload
```

Hvis du heller vil sette variablene direkte i skallet, ser minimumsoppsettet slik ut:

```bash
cd apps/api
. .venv/bin/activate
export HSJS_PROVIDER_STUB_MODE=false
export HSJS_PROVIDER_RUNTIME=transformers
export HSJS_HF_DEVICE=auto
export HSJS_HF_DTYPE=float16
export HSJS_TTS_RUNTIME=divvun-command
export HSJS_TTS_COMMAND="/path/to/divvun-speech-synthesize"
export HSJS_TTS_SME_VOICE_MODEL="/path/to/sme/voice.pte"
export HSJS_TTS_SME_VOCODER_MODEL="/path/to/sme/vocoder.pte"
hsjs-smoke-tts "Bures boahtin" --variant sme
```

Hvis du bruker `.env`, trenger du normalt ikke a eksportere de samme `HSJS_*`-variablene manuelt i skallet. `Settings` laster `apps/api/.env` automatisk ved API-start.

Hvis du kjører direkte fra en `divvun-speech-rs` checkout i stedet for en installert binær, kan du bruke en kommando som inkluderer argumentene for runneren selv, for eksempel `HSJS_TTS_COMMAND="cargo run --release --example synthesize --"`, og eventuelt sette `HSJS_TTS_COMMAND_CWD` til checkout-mappen. Upstream-koden viser at eksempelet forventer denne signaturen:

```text
synthesize <voice.pte> <vocoder.pte> <text> [output.wav] [--pace <f32>] [--speaker <i64>] [--language <i64>]
```

`divvun-speech-rs` har ingen publiserte GitHub Releases for selve synthesize-binaren akkurat na, og upstream-bygg krever minst Rust, CMake og et satt `EXECUTORCH_SYSROOT`. Derfor holder dette repoet Linux-sporet pragmatisk: finn modellfilene, pek `HSJS_TTS_COMMAND` til en ferdig bygd runner, og la health-endepunktet bekrefte resten.

Vi bundler ikke TTS-modellene i dette repoet. Borealium og Divvun/GiellaLT-dokumentasjonen er riktig sted for stemmepakker og installasjon. `sma` holdes fortsatt som ikke-verifisert full audio-støtte i denne kodebasen til lokal validering er gjort.

Nar du skal verifisere ekte lokal SME-TTS, er `GET /api/v1/health` kilden til sannhet. Se spesielt etter disse feltene før du sender en jobb:

- `stub_mode=false`
- `provider_runtime=transformers`
- `tts_runtime=divvun-api`
- `tts_api_reachable=true`
- `tts_variants_ready.sme=true`
- `tts_command_available=true`
- `tts_variants_local_ready.sme=true`
- `inference_runtime_ready=true`

En rask sjekk ser slik ut:

```bash
curl http://localhost:8000/api/v1/health | jq '{stub_mode, provider_runtime, tts_runtime, tts_api_reachable, tts_variants_ready, tts_command_available, tts_variants_local_ready, inference_runtime_ready, runtime_issues}'
```

Hvis `tts_variants_local_ready.sme=false`, vil health-responsen na ogsa gi mer konkrete `runtime_issues` for manglende `HSJS_TTS_SME_VOICE_MODEL` eller `HSJS_TTS_SME_VOCODER_MODEL` nar stub-modus er avslatt.

Rask anbefalt valideringsrekkefolge for `sme`:

```bash
cd apps/api
. .venv/bin/activate
uvicorn app.main:app --reload
```

```bash
curl http://localhost:8000/api/v1/health | jq '{stub_mode, tts_command_available, tts_variants_local_ready, inference_runtime_ready, runtime_issues}'
hsjs-smoke-tahetorn "Hvor er toget?" --variant sme
hsjs-smoke-tts "Bures boahtin" --variant sme
```

```bash
curl -X POST http://localhost:8000/api/v1/pipeline \
	-F target_variant=sme \
	-F source_text="Hvor er toget?" \
	-F include_phonemes=true \
	-F include_audio=true
```

Containerprofilene er fortsatt primart satt opp for inferensruntime. For standard TTS-trafikk holder det at containeren eller vertsmaskinen kan na `https://api-giellalt.uit.no/tts`. Lokal `divvun-command` er fortsatt et avansert alternativ via `apps/api/.env`.

### Frontend

Fra repo-roten:

```bash
npm install
npm run dev:web
```

Frontend henter både `/api/v1/capabilities` og `/api/v1/health` ved oppstart. Hvis API-et ikke er tilgjengelig, bruker den en innebygd fallback-matrise for `sme`, `smj` og `sma` og viser at runtime-status mangler.

Webklienten viser:

- hvilken runtime som faktisk er aktiv akkurat nå
- om valgt variant kan få audio i den aktive konfigurasjonen
- jobb-ID, jobbstatus og pipeline-steg mens jobben kjører
- transkripsjon, oversetting, fonemer og audioartefakt når resultatet er klart

Frontend henter stemmelista fra `/api/v1/voices`, velger default-stemme per variant, og ber om audio nar API-et enten kjører i stub-modus eller rapporterer at TTS er klar for valgt variant.

### Rask lokal test

For å teste uten lokal modellinferens, behold stub-modus som default. Denne sekvensen antar at du allerede har opprettet `apps/api/.venv` og kjørt `pip install -e .` som vist over. Kjør backend + frontend hver for seg:

```bash
cd apps/api
. .venv/bin/activate
uvicorn app.main:app --reload
```

```bash
npm run dev:web
```

I et tredje skall kan du bekrefte at API-et svarer og sende en enkel tekstjobb:

```bash
curl http://localhost:8000/api/v1/health
```

Health-svaret brukes direkte av webklienten. Se spesielt etter:

- `stub_mode=true` hvis du vil ha en trygg ende-til-ende demo uten lokale modeller
- `inference_dependencies_ready=true` hvis du planlegger ekte ASR og oversetting
- `inference_runtime_ready=true` hvis lokal inferens faktisk kan kjøres uten å stoppe på manglende eller ufullstendig modellcache
- `tts_variants_local_ready.sme=true` eller `tts_variants_local_ready.smj=true` hvis du vil ha ekte lokal audio i stedet for demo-WAV
- `tts_command_available=true` hvis Divvun-kommandoen faktisk kan finnes og kjøres
- `resolved_paths.model_dir` for å bekrefte hvilken Hugging Face-cache runtime faktisk bruker
- `local_model_cache_present=true` for å se om valgt Hugging Face-cache finnes lokalt
- `model_cache_state.nb_whisper.summary` og `model_cache_state.tahetorn.summary` for å se om cache ser komplett ut eller fortsatt har uferdige nedlastinger
- `runtime_components` for å se exakt hvilke Python-pakker som mangler når inferens ikke er klar ennå

```bash
curl -X POST http://localhost:8000/api/v1/pipeline \
	-F target_variant=sme \
	-F source_text="Hvor er toget?" \
	-F include_phonemes=true \
	-F include_audio=true
```

For `sma` bør du sette `include_audio=false`, som matcher dagens capability-matrise:

```bash
curl -X POST http://localhost:8000/api/v1/pipeline \
	-F target_variant=sma \
	-F source_text="Hvor er toget?" \
	-F include_phonemes=true \
	-F include_audio=false
```

Etter at `/pipeline` returnerer en jobb-ID, kan resultatet hentes slik:

```bash
curl http://localhost:8000/api/v1/jobs/<job-id>
```

Mens jobben kjører, inneholder `/jobs/<job-id>` nå delresultater og oppdaterte pipeline-steg. Frontenden bruker dette for å vise progresjon i stedet for å vente blindt på sluttstatus.

Hvis du sender `include_audio=true` for en variant uten audio-støtte, avviser API-et jobben med `400` i stedet for å kjøre en jobb som garantert vil feile senere.

### Validering av webklienten

Fra repo-roten:

```bash
npm run lint:web
npm run build:web
```

### Podman referanseprofil

```bash
podman compose -f infra/compose/compose.rocm.yml up --build
```

ROCm-compose-profilen kjører nå API-et direkte i den verifiserte inferenscontaineren i stedet for det tynne `api`-bildet. Den mounte `.artifacts/` og Hugging Face-cachen, og lar modellene ligge i standard HF-cache i stedet for i repoet.

ROCm-inference-containeren i [infra/containers/inference.rocm.Dockerfile](infra/containers/inference.rocm.Dockerfile) er pin'et til AMDs offisielle `rocm/pytorch`-image for `PyTorch 2.10.0` og `ROCm 7.1.1`, fordi denne kombinasjonen er verifisert lokalt mens `2.11.0 + rocm7.2` segfaulter for `nb-whisper` på denne maskinen.

## Videre arbeid

Noe arbeid att:

1. Finn ut korleis vi skal distribuera lokale Divvun-stemmefiler (`voice.pte`/`vocoder.pte`) i staden for offentleg API. Mest sannsynleg via Borealium eller Divvun-manager.
2. Koble fonemstega til ein verifisert lokal fonemiserer.
3. Verifiser full audio-støtte for `sma` dersom lokale stemmemodellar blir tilgjengelege.
