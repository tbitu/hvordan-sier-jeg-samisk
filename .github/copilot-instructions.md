# Project Guidelines

## Architecture

- Treat this repository as a fully local full-stack motor: Norwegian speech -> Norwegian text -> text processing -> translation to the selected Sami variant -> Sami phonemes/TTS. Do not introduce cloud APIs, hosted inference, or remote processing unless explicitly requested.
- `apps/api` is the FastAPI control plane. Keep HTTP routes thin, keep orchestration in `app/pipeline/service.py`, and keep provider-specific runtime logic in `app/providers/**`.
- Dependency wiring lives in `apps/api/app/dependencies.py`. Follow the existing explicit construction pattern instead of adding a DI framework.
- `apps/web` is a small React/Vite client. Keep UI logic close to `src/App.tsx` and small hooks such as `src/features/live-mic/useLiveMic.ts`; avoid adding heavy state-management or data-fetching libraries without a clear need.
- Background jobs are queued with FastAPI `BackgroundTasks` and stored in the in-memory job store in `apps/api/app/state.py`. Preserve that ephemeral behavior unless the task is explicitly about persistence.
- If you change API shapes or route behavior, update `packages/contracts/openapi.yaml` and keep the frontend in sync.

## Code Style

- Match the existing minimal style: typed Python functions, Pydantic models in `apps/api/app/domain.py`, and straightforward React/TypeScript with explicit local state.
- Keep user-facing copy in Norwegian unless the task explicitly calls for another language.
- Preserve ASCII-only text where the repository already uses ASCII transliterations such as `pa`, `stotte`, and `kjorer`.
- Prefer small, direct changes over new abstraction layers.

## Build and Test

- Web dev/build/lint from the repo root: `npm run dev:web`, `npm run build:web`, `npm run lint:web`.
- Backend dev: `cd apps/api && python -m venv .venv && . .venv/bin/activate && pip install -e . && uvicorn app.main:app --reload`.
- Backend local inference uses `pip install -e .[inference]`; for AMD/ROCm machines, follow the ROCm torch install documented in `README.md`.
- When a task involves installing or upgrading packages, frameworks, container base images, or runtime toolchains, verify the exact current version and install command with web search against upstream sources before making changes. Do not rely on stale memory for version selection.
- Validate backend changes with the smallest relevant check: API startup, `GET /api/v1/health`, or the smoke CLIs `hsjs-smoke-nb-whisper` and `hsjs-smoke-tahetorn`.
- Container profiles live in `infra/compose/*.yml`.
- There is no dedicated automated test suite yet. Do not claim tests passed unless you actually ran the relevant lint, smoke, or startup checks.

## Conventions

- `HSJS_PROVIDER_STUB_MODE=true` is the safe default for development. Keep stub-safe behavior unless you are specifically working on local model inference.
- Preserve both pipeline entry paths: direct `source_text` and uploaded audio.
- Supported target variants are `sme`, `smj`, and `sma`. Capability differences matter: `sme` and `smj` currently model full audio capability, while `sma` is currently treated as phoneme-first.
- `TahetornProvider` uses the tokenizer chat template when available; `NbWhisperProvider` may load audio through `torchaudio`, WAV parsing, or ffmpeg/imageio-ffmpeg fallback. Keep those local-runtime paths intact.
- Link to existing docs instead of duplicating them. Use `README.md` for environment setup and runtime notes.