from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Protocol

from app.domain import JobRecord, JobStatus


_UNSET = object()


@dataclass(frozen=True)
class UpdateResult:
    """Outcome of a JobStore.update() call.

    Distinguishes the two cases that both lack a returnable record: no such job
    exists (updated=False, record=None) versus the row was updated but its stored
    payload cannot be parsed by the current model version (updated=True,
    record=None). Collapsing both to None made an unreadable-but-updated row
    indistinguishable from a missing one.
    """

    updated: bool
    record: JobRecord | None = None


class StoreClosedError(RuntimeError):
    """Raised when a write is attempted on a closed store.

    Refusing the write (instead of silently reopening or accepting it) keeps a
    post-shutdown background task from resurrecting a job that the next
    startup's recovery has already failed.
    """


class JobStore(Protocol):
    def create(self, record: JobRecord) -> JobRecord: ...

    def get(self, job_id: str) -> JobRecord | None: ...

    def update(self, job_id: str, *, status: JobStatus, result=_UNSET, error=_UNSET) -> UpdateResult: ...

    def list(self) -> list[JobRecord]: ...

    def close(self) -> None: ...


class InMemoryJobStore:
    def __init__(self) -> None:
        self._items: dict[str, JobRecord] = {}
        self._lock = Lock()
        self._closed = False

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def _check_open(self) -> None:
        if self._closed:
            raise StoreClosedError(
                "Cannot write to closed in-memory job store: "
                "a post-shutdown write could resurrect a job that startup recovery already failed"
            )

    def create(self, record: JobRecord) -> JobRecord:
        with self._lock:
            self._check_open()
            # Store a copy and hand back the caller's own object: storing the
            # reference would let a caller mutate store state outside the lock.
            self._items[record.id] = record.model_copy()
        return record

    def get(self, job_id: str) -> JobRecord | None:
        # The snapshot is taken under the lock: without it a concurrent
        # create() (threadpool POST /pipeline) can resize the dict mid-read,
        # and a reader can observe a record mid-mutation by update().
        with self._lock:
            record = self._items.get(job_id)
            return None if record is None else record.model_copy()

    def update(self, job_id: str, *, status: JobStatus, result=_UNSET, error=_UNSET) -> UpdateResult:
        with self._lock:
            self._check_open()
            record = self._items.get(job_id)
            if record is None:
                return UpdateResult(updated=False)
            record.status = status
            if result is not _UNSET:
                record.result = result
            if error is not _UNSET:
                record.error = error
            self._items[job_id] = record
            # Return a copy (mirroring get/list): handing back the stored
            # reference would let a caller mutate store state outside the lock.
            return UpdateResult(updated=True, record=record.model_copy())

    def list(self) -> list[JobRecord]:
        # The snapshot is taken under the lock: without it a concurrent
        # create() (threadpool POST /pipeline) can resize the dict
        # mid-iteration and make list() raise RuntimeError (surfacing as a
        # declared 503).
        with self._lock:
            return [record.model_copy() for record in self._items.values()]
