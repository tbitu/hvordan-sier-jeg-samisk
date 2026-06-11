---
description: "Use when changing FastAPI routes, pipeline orchestration, providers, runtime settings, or OpenAPI contracts in apps/api and packages/contracts. Covers local-first inference, thin routes, stub-safe defaults, and minimal verification."
name: "Backend Runtime"
applyTo: "{apps/api/**/*.py,packages/contracts/openapi.yaml}"
---
# Backend Runtime Guidelines

- Keep the system fully local-first: Norwegian speech -> Norwegian text -> text processing -> translation to the selected Sami variant -> Sami phonemes/TTS. Do not add cloud APIs, hosted inference, or remote processing unless the task explicitly asks for it.
- Keep HTTP routes thin. Put orchestration in `apps/api/app/pipeline/service.py` and provider-specific runtime logic in `apps/api/app/providers/**`.
- Follow the explicit dependency wiring in `apps/api/app/dependencies.py`. Do not introduce a DI framework.
- Preserve the current queued-job model: FastAPI `BackgroundTasks` plus the ephemeral `InMemoryJobStore` in `apps/api/app/state.py`.
- Preserve both pipeline entry paths: direct `source_text` and uploaded audio.
- `HSJS_PROVIDER_STUB_MODE=true` is the safe default. Keep backend changes stub-safe unless the task is specifically about local model inference.
- For Python commands in `apps/api`, prefer the project virtual environment at `apps/api/.venv` when it exists instead of the system interpreter.
- When changing request or response shapes, update `packages/contracts/openapi.yaml` and keep the frontend behavior aligned.
- Keep local runtime behavior intact: `TahetornProvider` should keep using the tokenizer chat template when available, and `NbWhisperProvider` should keep its `torchaudio` -> WAV -> ffmpeg fallback chain.
- Supported variants are `sme`, `smj`, and `sma`. `sme` and `smj` currently model fuller audio capability; `sma` is currently phoneme-first.
- When changing backend installation steps, Python package versions, ROCm/PyTorch versions, or container base images, verify the exact current upstream versions with web search before editing files or running installs.
- Use the smallest relevant verification after changes: API startup, `GET /api/v1/health`, or the smoke CLIs `hsjs-smoke-nb-whisper` and `hsjs-smoke-tahetorn`.
- Link to `README.md` for environment setup and ROCm/runtime details instead of duplicating that setup inside code comments or docs.