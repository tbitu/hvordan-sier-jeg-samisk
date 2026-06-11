---
description: "Run the smallest relevant verification for a change in this repo and report exactly what was checked, what passed, and what still needs manual validation."
name: "Smoke Check"
argument-hint: "Describe the change area, such as backend pipeline, Tahetorn translation, web UI, or contract sync"
agent: "agent"
model: "GPT-5 (copilot)"
---
Run the smallest relevant verification for the current change or for the user-supplied target.

Constraints:
- Prefer the narrowest useful check instead of broad test sweeps.
- For backend changes, favor API startup, `GET /api/v1/health`, or the smoke CLIs `hsjs-smoke-nb-whisper` and `hsjs-smoke-tahetorn`.
- For frontend changes, favor `npm run lint:web`, and use `npm run build:web` when broader integration needs coverage.
- If API shapes changed, confirm whether `packages/contracts/openapi.yaml` and the web client are still aligned.
- Respect stub-safe defaults unless the task is explicitly about local inference.

Output format:
- Scope checked
- Commands or checks run
- Result
- Gaps or manual follow-up still needed