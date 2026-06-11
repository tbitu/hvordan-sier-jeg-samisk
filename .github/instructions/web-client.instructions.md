---
description: "Use when changing the React/Vite client in apps/web. Covers local API integration, Norwegian UI copy, simple state patterns, variant capability messaging, and lightweight frontend structure."
name: "Web Client"
applyTo: "apps/web/src/**/*.{ts,tsx,css}"
---
# Web Client Guidelines

- Treat the web app as a thin local client for the local API, not as a separate product surface. Keep the main flow centered on recording or typing Norwegian input and showing Sami pipeline output.
- Keep UI logic close to `apps/web/src/App.tsx` and small hooks such as `apps/web/src/features/live-mic/useLiveMic.ts`. Avoid adding heavy state-management, form, or data-fetching libraries unless the task clearly justifies them.
- Keep user-facing copy in Norwegian, and preserve the repository's ASCII transliteration style such as `pa`, `stotte`, and `kjorer` unless the task explicitly requires different text.
- Prefer direct local React state and straightforward `fetch` calls. Match the existing queued-job pattern: submit to `/pipeline`, then poll `/jobs/{id}` until completion or failure.
- Keep `VITE_API_BASE_URL` support intact so the web app can target the local API in dev and compose setups.
- Reflect current backend capability differences in the UI: `sme` and `smj` can present audio-oriented behavior, while `sma` should not imply verified full audio support.
- If backend payloads or route behavior change, update the web client in the same task and keep `packages/contracts/openapi.yaml` aligned.
- If a frontend task requires installing or upgrading dependencies, verify the exact current upstream versions with web search before changing package versions or install instructions.
- Use the smallest relevant validation after changes: `npm run lint:web` for typing and `npm run build:web` when the task affects broader app behavior.
- Link to `README.md` for environment and container setup rather than duplicating those instructions in frontend files.