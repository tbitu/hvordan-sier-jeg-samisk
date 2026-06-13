# Auto-Sync OpenAPI Contract from FastAPI

## Problem

`packages/contracts/openapi.yaml` is manually maintained and can drift from the actual FastAPI routes. This creates a risk where the documented API contract no longer matches the running API, confusing consumers of the spec.

## Scope

### Allowed
- Create a script `packages/scripts/sync-openapi.sh` (or Python equivalent) that:
  - Starts the FastAPI app in stub mode
  - Fetches `/openapi.json` from the running instance
  - Converts the JSON to YAML (using `pyyaml` or `jsyaml`)
  - Writes the result to `packages/contracts/openapi.yaml`
  - Gracefully shuts down the server
- Make the script idempotent: only overwrites the file if content changed
- Add a Makefile target or npm script alias (e.g., `npm run sync:openapi` or `make sync-openapi`)
- Update `.gitignore` to exclude auto-generated OpenAPI artifacts if needed
- Add a CI note or GitHub Actions workflow option for automated sync
- Document the approach in `README.md` or `packages/contracts/README.md`

### Not Allowed
- No changes to FastAPI route definitions or schemas
- No changes to existing API behavior
- No new runtime dependencies for the API itself (only dev/infra scripts)

## Approach

1. **Create `packages/scripts/sync-openapi.sh`**:
   - `cd apps/api && pip install -e .` (install in venv if needed)
   - Start FastAPI in background with `HSJS_PROVIDER_STUB_MODE=true`
   - Wait for `/api/v1/health` to return 200
   - `curl -s http://localhost:8000/openapi.json > /tmp/openapi.json`
   - Convert JSON to YAML: `python -c "import yaml, json; ..."` or `yq`
   - Write to `packages/contracts/openapi.yaml`
   - Diff against existing: if identical, skip; if different, commit with message
   - Kill the background server
2. **Make idempotent**: Use `diff` or checksum to avoid unnecessary writes
3. **Add convenience scripts**:
   - `package.json`: `"sync:openapi": "bash packages/scripts/sync-openapi.sh"`
   - Or a simple `Makefile` target
4. **Document**: Add a section in `README.md` about how to keep the contract in sync

## Deliverables

- Working `packages/scripts/sync-openapi.sh` script
- Convenience script/npm target for easy execution
- Documentation of the approach
- No changes to existing API behavior
