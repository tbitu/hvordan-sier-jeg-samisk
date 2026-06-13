# Job Store Persistence with SQLite

## Problem

`apps/api/app/state.py` uses `InMemoryJobStore` — all job records vanish on application restart. This means:
- Clients polling for job status lose their job after a restart
- No audit trail of past jobs
- No way to check historical job results

## Scope

### Allowed
- Create `apps/api/app/persistence.py` with a `SQLiteJobStore` implementation that:
  - Uses SQLite (via Python `sqlite3` standard library — no new dependencies)
  - Stores job records with the same `JobRecord` schema
  - Persists: id, status, request (as JSON), result (as JSON), error, timestamps
  - Implements the same interface as `InMemoryJobStore` (create, get, update, list)
- Add a `JobStore` Protocol/ABC that both implementations conform to
- Update `apps/api/app/dependencies.py` or `main.py` to wire `SQLiteJobStore` as default
- Keep `InMemoryJobStore` for tests or a `--ephemeral` flag
- Add migration logic: on startup, copy any in-memory state to SQLite (if needed) — or just start fresh
- Add unit tests in `apps/api/tests/test_persistence.py`
- Update `README.md` if runtime config changes

### Not Allowed
- No changes to the `JobRecord` domain model
- No changes to API routes or HTTP behavior
- No external databases (PostgreSQL, etc.) — SQLite only
- No migration of historical data from other systems

## Approach

1. **Define `JobStore` Protocol** in `apps/api/app/state.py` (move current `InMemoryJobStore` to a dedicated module or keep inline):
   ```python
   class JobStore(Protocol):
       def create(self, record: JobRecord) -> JobRecord: ...
       def get(self, job_id: str) -> JobRecord | None: ...
       def update(self, job_id: str, *, status: JobStatus, result=_UNSET, error=_UNSET) -> JobRecord | None: ...
       def list(self) -> list[JobRecord]: ...
   ```
2. **Create `apps/api/app/persistence.py`**:
   - `SQLiteJobStore` class with `__init__` taking a DB path (default: `data/jobs.db`)
   - Table schema: `jobs(id TEXT PRIMARY KEY, status TEXT, request TEXT, result TEXT, error TEXT, created_at TEXT, updated_at TEXT)`
   - Thread-safe with `threading.Lock` (same as current implementation)
   - `request` and `result` stored as JSON strings, deserialized on read
3. **Wire in `dependencies.py` or `main.py`**:
   - Default to `SQLiteJobStore`
   - Keep `InMemoryJobStore` behind a setting or test fixture
4. **Test**:
   - Unit tests for `SQLiteJobStore` (create, get, update, list, persistence across "restarts")
   - Integration: start API, create job, restart API, verify job still exists
5. **Data location**: Default to `data/jobs.db` relative to working directory, documented as configurable via env var `HSJS_DB_PATH`

## Deliverables

- `apps/api/app/persistence.py` with `SQLiteJobStore`
- Updated `JobStore` interface in `state.py`
- Wiring in `dependencies.py`/`main.py`
- Unit tests in `apps/api/tests/test_persistence.py`
- No regression in existing API behavior
