from __future__ import annotations

from threading import Lock

from app.domain import JobRecord, JobStatus


_UNSET = object()


class InMemoryJobStore:
    def __init__(self) -> None:
        self._items: dict[str, JobRecord] = {}
        self._lock = Lock()

    def create(self, record: JobRecord) -> JobRecord:
        with self._lock:
            self._items[record.id] = record
        return record

    def get(self, job_id: str) -> JobRecord | None:
        return self._items.get(job_id)

    def update(self, job_id: str, *, status: JobStatus, result=_UNSET, error=_UNSET) -> JobRecord | None:
        with self._lock:
            record = self._items.get(job_id)
            if record is None:
                return None
            record.status = status
            if result is not _UNSET:
                record.result = result
            if error is not _UNSET:
                record.error = error
            self._items[job_id] = record
            return record

    def list(self) -> list[JobRecord]:
        return list(self._items.values())
