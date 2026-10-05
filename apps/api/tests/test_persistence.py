from __future__ import annotations

import logging
import os
import re
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier, Event, Lock, Thread
from typing import IO

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.settings import Settings, get_settings
from app.api.v1.routes.pipeline import _run_pipeline
from app.dependencies import (
    _build_job_store,
    close_job_store,
    get_job_store,
    pipeline_service,
    recover_interrupted_jobs,
)
from app.domain import JobRecord, JobStatus, PipelineRequest, PipelineResult, PipelineStage, VariantCode
from app.main import app
from app.persistence import SQLiteJobStore, StoreClosedError, _process_start_time
from app.state import InMemoryJobStore, JobStore, _UNSET


def _make_record(status: JobStatus = JobStatus.queued) -> JobRecord:
    return JobRecord(
        status=status,
        request=PipelineRequest(
            target_variant=VariantCode.sme,
            source_text="Hvor er toget?",
            include_phonemes=True,
            include_audio=False,
        ),
    )


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "jobs.db"


@pytest.fixture(params=["sqlite", "memory"])
def any_store(request: pytest.FixtureRequest, store_path: Path) -> Iterator[JobStore]:
    if request.param == "sqlite":
        store: JobStore = SQLiteJobStore(store_path)
    else:
        store = InMemoryJobStore()
    yield store
    close = getattr(store, "close", None)
    if close is not None:
        close()


def test_create_and_get_roundtrip(store_path: Path):
    store = SQLiteJobStore(store_path)
    record = store.create(_make_record())
    fetched = store.get(record.id)
    assert fetched is not None
    assert fetched.id == record.id
    assert fetched.status == JobStatus.queued
    assert fetched.request.target_variant == VariantCode.sme
    assert fetched.request.source_text == "Hvor er toget?"
    assert fetched.result is None
    assert fetched.error is None
    store.close()


def test_get_missing_returns_none(store_path: Path):
    store = SQLiteJobStore(store_path)
    assert store.get("does-not-exist") is None
    store.close()


def test_update_status_only_keeps_result_unset(any_store: JobStore):
    record = any_store.create(_make_record())
    outcome = any_store.update(record.id, status=JobStatus.running)
    assert outcome.updated
    updated = outcome.record
    assert updated is not None
    assert updated.status == JobStatus.running
    assert updated.result is None
    assert updated.error is None


def test_update_without_result_argument_preserves_existing_result(any_store: JobStore):
    record = any_store.create(_make_record())
    result = PipelineResult(transcript_text="Hvor er toget?", translated_text="[sme] Hvor er toget?")
    any_store.update(record.id, status=JobStatus.running, result=result, error=None)
    outcome = any_store.update(record.id, status=JobStatus.completed)
    assert outcome.updated
    updated = outcome.record
    assert updated is not None
    assert updated.status == JobStatus.completed
    assert updated.result is not None
    assert updated.result.translated_text == "[sme] Hvor er toget?"
    assert updated.error is None


def test_failed_job_update_sets_error_and_preserves_result(any_store: JobStore):
    record = any_store.create(_make_record())
    result = PipelineResult(transcript_text="Hvor er toget?", translated_text="[sme] Hvor er toget?")
    any_store.update(record.id, status=JobStatus.running, result=result, error=None)
    outcome = any_store.update(record.id, status=JobStatus.failed, error="stage exploded")
    assert outcome.updated
    updated = outcome.record
    assert updated is not None
    assert updated.status == JobStatus.failed
    assert updated.error == "stage exploded"
    assert updated.result is not None
    assert updated.result.translated_text == "[sme] Hvor er toget?"


def test_update_with_result_and_error(store_path: Path):
    store = SQLiteJobStore(store_path)
    record = store.create(_make_record())
    result = PipelineResult(transcript_text="Hvor er toget?", translated_text="[sme] Hvor er toget?")
    outcome = store.update(record.id, status=JobStatus.completed, result=result, error=None)
    assert outcome.updated
    updated = outcome.record
    assert updated is not None
    assert updated.status == JobStatus.completed
    assert updated.result is not None
    assert updated.result.translated_text == "[sme] Hvor er toget?"
    assert updated.error is None
    store.close()


def test_update_missing_reports_not_updated(store_path: Path):
    store = SQLiteJobStore(store_path)
    outcome = store.update("does-not-exist", status=JobStatus.running)
    assert not outcome.updated
    assert outcome.record is None
    store.close()


def test_list_returns_all_in_insertion_order(store_path: Path):
    store = SQLiteJobStore(store_path)
    first = store.create(_make_record())
    second = store.create(_make_record())
    third = store.create(_make_record())
    listed = store.list()
    assert [record.id for record in listed] == [first.id, second.id, third.id]
    store.close()


def test_persistence_across_restart(store_path: Path):
    store = SQLiteJobStore(store_path)
    record = store.create(_make_record())
    result = PipelineResult(transcript_text="Hvor er toget?", translated_text="[sme] Hvor er toget?")
    store.update(record.id, status=JobStatus.completed, result=result, error=None)
    store.close()

    reopened = SQLiteJobStore(store_path)
    fetched = reopened.get(record.id)
    assert fetched is not None
    assert fetched.status == JobStatus.completed
    assert fetched.request.source_text == "Hvor er toget?"
    assert fetched.result is not None
    assert fetched.result.translated_text == "[sme] Hvor er toget?"
    assert [r.id for r in reopened.list()] == [record.id]
    reopened.close()


def test_unicode_roundtrip_norwegian_and_sami(store_path: Path):
    store = SQLiteJobStore(store_path)
    source = "Hvor er toget? År 1905 – æøå"
    request = PipelineRequest(
        target_variant=VariantCode.sme,
        source_text=source,
        include_phonemes=True,
        include_audio=False,
    )
    record = store.create(JobRecord(request=request))
    result = PipelineResult(
        transcript_text="Hvor er toget? År 1905 – æøå",
        translated_text="[sme] Gosa lea toahkku? Áigodat 1905 – šđŋčž",
    )
    store.update(record.id, status=JobStatus.completed, result=result, error=None)
    store.close()

    reopened = SQLiteJobStore(store_path)
    fetched = reopened.get(record.id)
    reopened.close()
    assert fetched is not None
    assert fetched.request.source_text == source
    assert fetched.result is not None
    assert fetched.result.transcript_text == "Hvor er toget? År 1905 – æøå"
    assert fetched.result.translated_text == "[sme] Gosa lea toahkku? Áigodat 1905 – šđŋčž"


def test_creates_missing_parent_directories(tmp_path: Path):
    nested = tmp_path / "deep" / "nested" / "jobs.db"
    store = SQLiteJobStore(nested)
    assert nested.exists()
    store.close()


def test_in_memory_store_still_conforms_to_protocol(store_path: Path):
    memory = InMemoryJobStore()
    record = memory.create(_make_record())
    assert memory.get(record.id) is not None
    assert memory.update(record.id, status=JobStatus.running).updated
    assert not memory.update("does-not-exist", status=JobStatus.running).updated
    assert [r.id for r in memory.list()] == [record.id]


def test_update_result_none_clears_result(any_store: JobStore):
    record = any_store.create(_make_record())
    result = PipelineResult(transcript_text="Hvor er toget?", translated_text="[sme] Hvor er toget?")
    any_store.update(record.id, status=JobStatus.running, result=result, error=None)
    outcome = any_store.update(record.id, status=JobStatus.completed, result=None, error=None)
    assert outcome.updated
    updated = outcome.record
    assert updated is not None
    assert updated.status == JobStatus.completed
    assert updated.result is None
    assert updated.error is None


def test_update_clears_previously_set_error(any_store: JobStore):
    record = any_store.create(_make_record())
    failed_outcome = any_store.update(record.id, status=JobStatus.failed, error="boom")
    assert failed_outcome.updated
    failed = failed_outcome.record
    assert failed is not None
    assert failed.error == "boom"
    outcome = any_store.update(record.id, status=JobStatus.completed, error=None)
    assert outcome.updated
    updated = outcome.record
    assert updated is not None
    assert updated.status == JobStatus.completed
    assert updated.error is None


def test_create_duplicate_id_overwrites(any_store: JobStore):
    request = PipelineRequest(
        target_variant=VariantCode.sme,
        source_text="Hvor er toget?",
        include_phonemes=True,
        include_audio=False,
    )
    any_store.create(JobRecord(id="dup-job", request=request))
    any_store.create(JobRecord(id="dup-job", status=JobStatus.running, request=request))
    fetched = any_store.get("dup-job")
    assert fetched is not None
    assert fetched.status == JobStatus.running
    assert [record.id for record in any_store.list()] == ["dup-job"]


def test_create_with_result_and_error_roundtrip(store_path: Path):
    store = SQLiteJobStore(store_path)
    result = PipelineResult(transcript_text="Hvor er toget?", translated_text="[sme] Gosa lea toahkku?")
    completed = store.create(
        JobRecord(
            status=JobStatus.completed,
            request=PipelineRequest(
                target_variant=VariantCode.sme,
                source_text="Hvor er toget?",
                include_phonemes=True,
                include_audio=False,
            ),
            result=result,
            error=None,
        )
    )
    failed = store.create(
        JobRecord(
            status=JobStatus.failed,
            request=PipelineRequest(
                target_variant=VariantCode.sme,
                source_text="Hvor er toget?",
                include_phonemes=True,
                include_audio=False,
            ),
            result=None,
            error="stage exploded",
        )
    )
    fetched_completed = store.get(completed.id)
    assert fetched_completed is not None
    assert fetched_completed.status == JobStatus.completed
    assert fetched_completed.result is not None
    assert fetched_completed.result.translated_text == "[sme] Gosa lea toahkku?"
    assert fetched_completed.error is None
    fetched_failed = store.get(failed.id)
    assert fetched_failed is not None
    assert fetched_failed.status == JobStatus.failed
    assert fetched_failed.result is None
    assert fetched_failed.error == "stage exploded"
    store.close()


def test_create_conflict_preserves_created_at_and_bumps_updated_at(store_path: Path):
    store = SQLiteJobStore(store_path)
    record = store.create(_make_record())
    store.close()

    def read_timestamps() -> tuple[str, str]:
        conn = sqlite3.connect(store_path)
        try:
            row = conn.execute("SELECT created_at, updated_at FROM jobs WHERE id = ?", (record.id,)).fetchone()
        finally:
            conn.close()
        assert row is not None
        return row[0], row[1]

    created_at, updated_at = read_timestamps()
    assert created_at == updated_at

    # Upserting the same id must update the row (status/result/updated_at) while
    # preserving the original created_at.
    store = SQLiteJobStore(store_path)
    result = PipelineResult(transcript_text="Hvor er toget?", translated_text="[sme] Gosa lea toahkku?")
    store.create(
        JobRecord(
            id=record.id,
            status=JobStatus.completed,
            request=PipelineRequest(
                target_variant=VariantCode.sme,
                source_text="Hvor er toget?",
                include_phonemes=True,
                include_audio=False,
            ),
            result=result,
            error=None,
        )
    )
    store.close()

    created_at_after, updated_at_after = read_timestamps()
    assert created_at_after == created_at
    assert updated_at_after >= created_at_after

    reopened = SQLiteJobStore(store_path)
    try:
        fetched = reopened.get(record.id)
        assert fetched is not None
        assert fetched.status == JobStatus.completed
        assert fetched.result is not None
        assert fetched.result.translated_text == "[sme] Gosa lea toahkku?"
    finally:
        reopened.close()


def test_concurrent_creates_and_updates_are_safe(store_path: Path):
    store = SQLiteJobStore(store_path)
    errors: list[BaseException] = []
    barrier = Barrier(8)

    def worker() -> None:
        try:
            barrier.wait(timeout=10)
            for _ in range(10):
                record = store.create(_make_record())
                store.update(record.id, status=JobStatus.running)
                result = PipelineResult(transcript_text="Hvor er toget?", translated_text="[sme] Hvor er toget?")
                store.update(record.id, status=JobStatus.completed, result=result, error=None)
        except BaseException as exc:
            errors.append(exc)

    threads = [Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    listed = store.list()
    assert len(listed) == 80
    assert all(record.status == JobStatus.completed for record in listed)
    store.close()


def test_concurrent_updates_to_same_job(store_path: Path):
    store = SQLiteJobStore(store_path)
    record = store.create(_make_record())
    errors: list[BaseException] = []
    start = Barrier(8)
    settle = Barrier(8)

    def flipper(index: int) -> None:
        try:
            start.wait(timeout=10)
            for _ in range(25):
                status = JobStatus.running if index % 2 == 0 else JobStatus.queued
                assert store.update(record.id, status=status).updated
            settle.wait(timeout=10)
            if index == 0:
                assert store.update(record.id, status=JobStatus.failed, error="settled").updated
        except BaseException as exc:
            errors.append(exc)

    threads = [Thread(target=flipper, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    final = store.get(record.id)
    assert final is not None
    assert final.status == JobStatus.failed
    assert final.error == "settled"
    store.close()


def test_cross_instance_updates_are_atomic(store_path: Path):
    # Two independent store instances (separate connections, separate locks) hammer
    # the same row. Each update writes transcript_text and translated_text together,
    # so a non-atomic read-modify-write could interleave two writes and leave the
    # fields divergent. The atomic single-statement update must not.
    store_a = SQLiteJobStore(store_path)
    store_b = SQLiteJobStore(store_path)
    record = store_a.create(_make_record())
    errors: list[BaseException] = []
    barrier = Barrier(4)

    def worker(store: JobStore, tag: str) -> None:
        try:
            barrier.wait(timeout=10)
            for i in range(25):
                value = f"{tag}-{i}"
                assert store.update(
                    record.id,
                    status=JobStatus.running,
                    result=PipelineResult(transcript_text=value, translated_text=value),
                    error=None,
                ).updated
        except BaseException as exc:
            errors.append(exc)

    threads = [
        Thread(target=worker, args=(store_a, "a")),
        Thread(target=worker, args=(store_a, "a2")),
        Thread(target=worker, args=(store_b, "b")),
        Thread(target=worker, args=(store_b, "b2")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    final = store_a.get(record.id)
    assert final is not None
    assert final.result is not None
    assert final.result.transcript_text == final.result.translated_text
    store_a.close()
    store_b.close()


def test_timestamps_are_written_and_updated(store_path: Path):
    store = SQLiteJobStore(store_path)
    record = store.create(_make_record())
    store.close()

    def read_timestamps() -> tuple[str, str]:
        conn = sqlite3.connect(store_path)
        try:
            row = conn.execute("SELECT created_at, updated_at FROM jobs WHERE id = ?", (record.id,)).fetchone()
        finally:
            conn.close()
        assert row is not None
        return row[0], row[1]

    created_at, updated_at = read_timestamps()
    for value in (created_at, updated_at):
        datetime.fromisoformat(value)
    assert created_at == updated_at

    store = SQLiteJobStore(store_path)
    store.update(record.id, status=JobStatus.running)
    store.close()

    created_at_after, updated_at_after = read_timestamps()
    assert created_at_after == created_at
    assert updated_at_after >= created_at_after


def test_close_is_idempotent_and_store_reopens(store_path: Path):
    store = SQLiteJobStore(store_path)
    record = store.create(_make_record())
    store.close()
    store.close()
    fetched = store.get(record.id)
    assert fetched is not None
    assert fetched.id == record.id
    store.close()


def test_closed_store_refuses_writes_but_still_serves_reads(store_path: Path):
    store = SQLiteJobStore(store_path)
    record = store.create(_make_record())
    store.close()

    with pytest.raises(StoreClosedError):
        store.create(_make_record())
    with pytest.raises(StoreClosedError):
        store.update(record.id, status=JobStatus.running)

    # Reads after close still work (the connection may be reopened), and the
    # durable row is untouched by the refused writes.
    fetched = store.get(record.id)
    assert fetched is not None
    assert fetched.status == JobStatus.queued
    store.close()


def test_post_close_read_does_not_reregister_liveness_marker(store_path: Path):
    # A read after close() reopens the connection; it must NOT re-register the
    # liveness marker, or it would undo close()'s deletion and make the next
    # startup within the grace window skip recovery (treating this gone process
    # as a live peer).
    store = SQLiteJobStore(store_path)
    record = store.create(_make_record())
    token = f"{socket.gethostname()}:{os.getpid()}"
    store.close()

    # The read reopens the connection (the supported post-close contract).
    fetched = store.get(record.id)
    assert fetched is not None

    conn = sqlite3.connect(store_path)
    try:
        row = conn.execute("SELECT COUNT(*) FROM processes WHERE token = ?", (token,)).fetchone()
    finally:
        conn.close()
    assert row[0] == 0


def test_close_keeps_marker_while_sibling_instance_still_open(tmp_path: Path):
    # The liveness marker is process-wide (one token per OS process), so
    # closing one store instance must not delete it while a sibling instance
    # in the same process is still open: the survivor's writes refresh the
    # marker in place (UPDATE-only) and never re-insert a deleted row, so the
    # deletion would make the survivor invisible to a co-booting peer's
    # recovery, which would then fail its in-flight jobs.
    db_path = tmp_path / "jobs.db"
    token = f"{socket.gethostname()}:{os.getpid()}"

    def marker_rows() -> int:
        conn = sqlite3.connect(db_path, timeout=5)
        try:
            row = conn.execute("SELECT COUNT(*) FROM processes WHERE token = ?", (token,)).fetchone()
        finally:
            conn.close()
        return row[0]

    store_a = SQLiteJobStore(db_path)
    store_b = SQLiteJobStore(db_path)
    assert marker_rows() == 1

    store_a.close()
    assert marker_rows() == 1  # the survivor's marker is intact

    # The survivor's write refreshes the existing marker (it is never
    # resurrected from nothing).
    store_b.create(_make_record())
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        row = conn.execute("SELECT last_seen_at FROM processes WHERE token = ?", (token,)).fetchone()
    finally:
        conn.close()
    assert row is not None

    store_b.close()
    assert marker_rows() == 0  # deleted only when the last instance closes


def test_post_close_read_then_close_does_not_delete_sibling_marker(tmp_path: Path):
    # A close->read->close sequence must not unbalance the per-path refcount:
    # the post-close read reopens a read-only (unregistered) connection, so the
    # second close must not decrement the count or delete the process-wide
    # liveness marker while a sibling instance on the same DB is still open
    # (which would make the survivor invisible to a co-booting peer's recovery).
    db_path = tmp_path / "jobs.db"
    token = f"{socket.gethostname()}:{os.getpid()}"

    def marker_rows() -> int:
        conn = sqlite3.connect(db_path, timeout=5)
        try:
            row = conn.execute("SELECT COUNT(*) FROM processes WHERE token = ?", (token,)).fetchone()
        finally:
            conn.close()
        return row[0]

    store_a = SQLiteJobStore(db_path)
    store_b = SQLiteJobStore(db_path)
    record = store_b.create(_make_record())
    assert marker_rows() == 1

    store_a.close()
    # The supported post-close read reopens a read-only connection.
    fetched = store_a.get(record.id)
    assert fetched is not None
    # The second close must not touch the refcount or the marker.
    store_a.close()
    assert marker_rows() == 1  # the survivor's marker is still intact

    store_b.close()
    assert marker_rows() == 0  # deleted only when the last instance closes


def test_close_on_one_path_keeps_marker_while_sibling_on_other_path_open(tmp_path: Path):
    # The last-instance gate is process-wide, not per-path: the liveness marker
    # is one token per OS process shared by every store instance in the process
    # on every path, so closing the last instance on one path must not delete
    # the marker while a sibling instance on a DIFFERENT path is still open.
    # Every same-path sibling test passes green with a per-path gate, so this
    # cross-path case is the regression guard for the process-wide gate.
    db_path_a = tmp_path / "a" / "jobs.db"
    db_path_b = tmp_path / "b" / "jobs.db"
    token = f"{socket.gethostname()}:{os.getpid()}"

    def marker_rows(db_path: Path) -> int:
        conn = sqlite3.connect(db_path, timeout=5)
        try:
            row = conn.execute("SELECT COUNT(*) FROM processes WHERE token = ?", (token,)).fetchone()
        finally:
            conn.close()
        return row[0]

    store_a = SQLiteJobStore(db_path_a)
    store_b = SQLiteJobStore(db_path_b)
    assert marker_rows(db_path_a) == 1
    assert marker_rows(db_path_b) == 1

    store_a.close()
    # Last instance on path A, but a sibling on path B is still open: the
    # process-wide gate keeps the marker (a per-path gate would delete it).
    assert marker_rows(db_path_a) == 1
    assert marker_rows(db_path_b) == 1

    # The survivor's write refreshes its own marker (UPDATE-only, never
    # re-inserted from nothing) - it must still exist.
    store_b.create(_make_record())
    assert marker_rows(db_path_b) == 1

    store_b.close()
    assert marker_rows(db_path_b) == 0  # deleted when the last instance on ANY path closes
    # The closed path's marker stays until it is pruned on a later open (the
    # delete runs on the last instance's own connection, path B's).
    assert marker_rows(db_path_a) == 1


def test_sibling_store_open_does_not_advance_marker_started_at(store_path: Path):
    # A second store instance in this process shares the hostname:pid token; its open
    # must not advance the marker's started_at. Advancing it past this process's
    # in-flight jobs' updated_at would make a peer _protected_job_ids treat those
    # jobs as unprotected (peer "started after" their last update) and fail them on
    # the next boot. A genuine PID reuse (a different start time) still refreshes.
    store_a = SQLiteJobStore(store_path)

    def marker_started_at() -> str:
        conn = sqlite3.connect(store_path, timeout=5)
        try:
            row = conn.execute("SELECT started_at FROM processes").fetchone()
            assert row is not None
            return row[0]
        finally:
            conn.close()

    first_open_started_at = marker_started_at()
    job = store_a.create(_make_record())  # in-flight (queued) since the first open
    store_b = SQLiteJobStore(store_path)  # sibling instance, same process token

    assert marker_started_at() == first_open_started_at  # not advanced by B's open
    conn = sqlite3.connect(store_path, timeout=5)
    try:
        updated_at = conn.execute("SELECT updated_at FROM jobs WHERE id = ?", (job.id,)).fetchone()[0]
    finally:
        conn.close()
    assert marker_started_at() <= updated_at  # the job stays protectable by peers

    store_b.close()
    store_a.close()


def test_post_close_read_with_deleted_file_serves_empty_and_does_not_recreate(store_path: Path):
    # A read after close() must not recreate a deleted DB file (a read must not
    # have write side effects: no parent-dir creation, no file creation, no write
    # probe). A deleted file has no data, so the reads serve empty results
    # instead of resurrecting the file (which would diverge from a fresh open of
    # a missing file).
    store = SQLiteJobStore(store_path)
    record = store.create(_make_record())
    store.close()
    store_path.unlink()  # the DB file is gone after close

    # Reads through the closed instance serve empty results (no data) and do not
    # recreate the file.
    assert store.get(record.id) is None
    assert store.list() == []
    assert not store_path.exists()  # the file was not resurrected


def test_close_releases_cached_read_only_connection(store_path: Path):
    # close() must release the cached post-close read connection, not only the main
    # one: a close->read->close sequence would otherwise leak the second connection
    # (and its file handle) until garbage collection.
    store = SQLiteJobStore(store_path)
    record = store.create(_make_record())
    store.close()

    assert store.get(record.id) is not None  # opens and caches the read-only conn
    cached = store._read_only_conn
    assert cached is not None

    store.close()  # the second close must release it

    assert store._read_only_conn is None
    with pytest.raises(sqlite3.ProgrammingError):
        cached.execute("SELECT 1")


def test_closed_memory_store_refuses_writes_but_still_serves_reads():
    # The memory backend must carry the same zombie-write fence as the sqlite
    # backend: a post-shutdown background task writing through the orphaned
    # instance must be refused, not silently succeed while readers see a fresh
    # empty store.
    store = InMemoryJobStore()
    record = store.create(_make_record())
    store.close()

    with pytest.raises(StoreClosedError):
        store.create(_make_record())
    with pytest.raises(StoreClosedError):
        store.update(record.id, status=JobStatus.running)

    # Reads after close still work (parity with the sqlite store).
    fetched = store.get(record.id)
    assert fetched is not None
    assert [r.id for r in store.list()] == [record.id]


def test_in_memory_reads_are_safe_under_concurrent_writes():
    # The read paths must be lock-protected: without the lock a concurrent
    # create() (threadpool POST /pipeline) can resize the dict mid-iteration
    # and make list() raise RuntimeError (surfacing as a declared 503), and a
    # reader can observe a record mid-mutation by update().
    store = InMemoryJobStore()
    errors: list[BaseException] = []
    stop = Event()

    def writer() -> None:
        try:
            while not stop.is_set():
                store.create(_make_record())
        except BaseException as exc:
            errors.append(exc)

    def reader() -> None:
        try:
            while not stop.is_set():
                records = store.list()
                ids = [record.id for record in records]
                assert len(ids) == len(set(ids))
                for record in records:
                    _ = record.status
                    _ = record.request
        except BaseException as exc:
            errors.append(exc)

    threads = [Thread(target=writer) for _ in range(2)] + [Thread(target=reader) for _ in range(2)]
    for thread in threads:
        thread.start()
    time.sleep(0.5)
    stop.set()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []

    # Snapshot isolation (the trivial uniqueness invariant above would pass even if
    # reads returned aliased internal records): mutating a returned record outside
    # the lock must not leak into store state.
    snapshot = store.list()
    assert snapshot  # the writers created many jobs during the run
    victim_id = snapshot[0].id
    leaked_read = snapshot[0]
    leaked_read.status = JobStatus.failed
    leaked_read.error = "post-read mutation"
    stored = store.get(victim_id)
    assert stored is not None
    assert stored.status == JobStatus.queued  # the writers only create queued jobs
    assert stored.error != "post-read mutation"

    created = store.create(_make_record())
    created.status = JobStatus.failed  # mutate the record create() handed back
    fetched = store.get(created.id)
    assert fetched is not None
    assert fetched.status == JobStatus.queued

    outcome = store.update(victim_id, status=JobStatus.running)
    assert outcome.updated and outcome.record is not None
    outcome.record.error = "post-update mutation"  # mutate the record update() handed back
    stored_after_update = store.get(victim_id)
    assert stored_after_update is not None
    assert stored_after_update.status == JobStatus.running
    assert stored_after_update.error != "post-update mutation"

    store.close()


def test_post_shutdown_write_cannot_resurrect_recovered_job(store_path: Path):
    # Zombie background task scenario: the store instance the task holds is
    # closed by shutdown, and a new process's startup recovery has already
    # failed the abandoned job. The zombie's write must be refused, not
    # resurrect the job.
    zombie_store = SQLiteJobStore(store_path)
    job = zombie_store.create(_make_record())
    zombie_store.update(job.id, status=JobStatus.running)
    zombie_store.close()  # app shutdown

    # New process: startup recovery fails the interrupted job.
    fresh = SQLiteJobStore(store_path)
    for record in fresh.list():
        if record.status in (JobStatus.queued, JobStatus.running):
            fresh.update(record.id, status=JobStatus.failed, error="interrupted by server restart")
    recovered = fresh.get(job.id)
    assert recovered is not None
    assert recovered.status == JobStatus.failed

    # The zombie task wakes up and tries to finish the job: refused.
    with pytest.raises(StoreClosedError):
        zombie_store.update(job.id, status=JobStatus.completed)
    with pytest.raises(StoreClosedError):
        zombie_store.create(_make_record())

    final = fresh.get(job.id)
    assert final is not None
    assert final.status == JobStatus.failed
    assert final.error == "interrupted by server restart"
    fresh.close()


def test_run_pipeline_logs_original_error_when_failure_recording_hits_closed_store(tmp_path: Path, monkeypatch, caplog):
    # If the store closes between the pipeline failure and the failure-recording
    # update, the warning must still carry the original exception: otherwise the
    # real failure reason is permanently masked behind the later
    # interrupted-by-server-restart label that startup recovery writes.
    store = SQLiteJobStore(tmp_path / "jobs.db")
    record = store.create(_make_record())

    def fail_run(request, **kwargs):
        raise RuntimeError("boom: root cause of the pipeline failure")

    real_update = store.update
    update_calls = {"count": 0}

    def flaky_update(job_id, **kwargs):
        update_calls["count"] += 1
        if update_calls["count"] == 1:
            return real_update(job_id, **kwargs)
        store.close()
        raise StoreClosedError("job store closed while recording failure")

    monkeypatch.setattr(pipeline_service, "run", fail_run)
    monkeypatch.setattr(store, "update", flaky_update)

    with caplog.at_level(logging.WARNING, logger="app.api.v1.routes.pipeline"):
        _run_pipeline(store, record.id, record.request, None, None)

    assert any("boom: root cause of the pipeline failure" in message for message in caplog.messages)


def _ok_pipeline_result() -> PipelineResult:
    return PipelineResult(
        transcript_text="Hvor er toget?",
        translated_text="Gosa lea tog?",
        stages=[PipelineStage(name="translate", status=JobStatus.completed, summary="ok")],
    )


def test_transient_store_failure_does_not_fail_completed_job(tmp_path: Path, monkeypatch, caplog):
    # A transient store failure while recording a successful run must not flip
    # the job to failed: the pipeline completed, and recording the store error
    # as the job's failure would report a pipeline failure that never happened.
    # The job is left in its last persisted state for startup recovery.
    store = SQLiteJobStore(tmp_path / "jobs.db")
    record = store.create(_make_record())

    real_update = store.update
    calls = {"n": 0}

    def flaky_update(job_id, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return real_update(job_id, **kwargs)
        raise sqlite3.OperationalError("database is locked")

    def fake_run(request, on_update=None, **kwargs):
        if on_update is not None:
            on_update(_ok_pipeline_result())
        return _ok_pipeline_result()

    monkeypatch.setattr(pipeline_service, "run", fake_run)
    monkeypatch.setattr(store, "update", flaky_update)

    with caplog.at_level(logging.ERROR, logger="app.api.v1.routes.pipeline"):
        _run_pipeline(store, record.id, record.request, None, None)

    fetched = store.get(record.id)
    assert fetched is not None
    assert fetched.status == JobStatus.running  # not failed
    assert fetched.error is None
    assert any("database is locked" in message for message in caplog.messages)
    store.close()


def test_transient_store_failure_in_intermediate_update_does_not_fail_pipeline(tmp_path: Path, monkeypatch):
    # A transient store failure in an intermediate progress write must not fail
    # an otherwise healthy pipeline: the intermediate update is not critical,
    # and the final write persists the complete result.
    store = SQLiteJobStore(tmp_path / "jobs.db")
    record = store.create(_make_record())

    real_update = store.update
    calls = {"n": 0}

    def flaky_update(job_id, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise sqlite3.OperationalError("database is locked")
        return real_update(job_id, **kwargs)

    def fake_run(request, on_update=None, **kwargs):
        if on_update is not None:
            on_update(_ok_pipeline_result())
        return _ok_pipeline_result()

    monkeypatch.setattr(pipeline_service, "run", fake_run)
    monkeypatch.setattr(store, "update", flaky_update)

    _run_pipeline(store, record.id, record.request, None, None)

    fetched = store.get(record.id)
    assert fetched is not None
    assert fetched.status == JobStatus.completed
    store.close()


def _failed_stage_result() -> PipelineResult:
    return PipelineResult(
        transcript_text="Hvor er toget?",
        translated_text="Gosa lea tog?",
        stages=[
            PipelineStage(name="transcribe", status=JobStatus.completed, summary="ok"),
            PipelineStage(name="translate", status=JobStatus.failed, summary="translation exploded"),
        ],
    )


def test_run_pipeline_failed_stage_marks_job_failed_with_stage_summary(tmp_path: Path, monkeypatch):
    # The primary production failure path: PipelineService.run returns a result
    # with a failed stage and does not raise. The job must be marked failed
    # with the stage's summary as the error, and the partial result preserved.
    store = SQLiteJobStore(tmp_path / "jobs.db")
    record = store.create(_make_record())
    monkeypatch.setattr(
        pipeline_service, "run", lambda request, on_update=None, **kwargs: _failed_stage_result()
    )

    _run_pipeline(store, record.id, record.request, None, None)

    fetched = store.get(record.id)
    assert fetched is not None
    assert fetched.status == JobStatus.failed
    assert fetched.error == "translation exploded"
    assert fetched.result is not None
    assert [stage.status for stage in fetched.result.stages] == [JobStatus.completed, JobStatus.failed]
    store.close()


def test_run_pipeline_failed_stage_with_closed_store_leaves_status_for_recovery(tmp_path: Path, monkeypatch, caplog):
    # If the store closes between the failed run and the result-recording
    # update, the job is left in its last persisted state (the next startup's
    # recovery marks it failed) and the warning must not mask that the run
    # itself failed.
    store = SQLiteJobStore(tmp_path / "jobs.db")
    record = store.create(_make_record())

    real_update = store.update
    calls = {"n": 0}

    def flaky_update(job_id, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return real_update(job_id, **kwargs)
        store.close()
        raise StoreClosedError("job store closed while recording result")

    monkeypatch.setattr(
        pipeline_service, "run", lambda request, on_update=None, **kwargs: _failed_stage_result()
    )
    monkeypatch.setattr(store, "update", flaky_update)

    with caplog.at_level(logging.WARNING, logger="app.api.v1.routes.pipeline"):
        _run_pipeline(store, record.id, record.request, None, None)

    fetched = store.get(record.id)
    assert fetched is not None
    assert fetched.status == JobStatus.running  # left for startup recovery
    assert any("closed while recording result" in message for message in caplog.messages)


def test_run_pipeline_failed_stage_transient_store_failure_leaves_last_state(tmp_path: Path, monkeypatch, caplog):
    # A transient store failure while recording a failed run must not mask the
    # pipeline failure with the store error: the job is left in its last
    # persisted state, and the next startup's recovery marks it failed.
    store = SQLiteJobStore(tmp_path / "jobs.db")
    record = store.create(_make_record())

    real_update = store.update
    calls = {"n": 0}

    def flaky_update(job_id, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return real_update(job_id, **kwargs)
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(
        pipeline_service, "run", lambda request, on_update=None, **kwargs: _failed_stage_result()
    )
    monkeypatch.setattr(store, "update", flaky_update)

    with caplog.at_level(logging.ERROR, logger="app.api.v1.routes.pipeline"):
        _run_pipeline(store, record.id, record.request, None, None)

    fetched = store.get(record.id)
    assert fetched is not None
    assert fetched.status == JobStatus.running  # not failed with the store error
    assert any("could not record pipeline result" in message for message in caplog.messages)
    store.close()


def test_run_pipeline_initial_running_update_closed_store_leaves_status_for_recovery(
    tmp_path: Path, monkeypatch, caplog
):
    # The initial running-status update hitting a closed store must be re-raised
    # as StoreClosedError (not swallowed as a transient failure): the job is
    # left in its last persisted state (queued) for the next startup's recovery,
    # the pipeline never runs, and no exception escapes the background task.
    store = SQLiteJobStore(tmp_path / "jobs.db")
    record = store.create(_make_record())
    ran = {"pipeline": False}

    def fake_run(request, on_update=None, **kwargs):
        ran["pipeline"] = True
        return _ok_pipeline_result()

    def closed_update(job_id, **kwargs):
        raise StoreClosedError("job store closed before the run started")

    monkeypatch.setattr(pipeline_service, "run", fake_run)
    monkeypatch.setattr(store, "update", closed_update)

    with caplog.at_level(logging.WARNING, logger="app.api.v1.routes.pipeline"):
        _run_pipeline(store, record.id, record.request, None, None)

    assert not ran["pipeline"]  # the pipeline never started
    fetched = store.get(record.id)
    assert fetched is not None
    assert fetched.status == JobStatus.queued  # left for startup recovery
    assert any("closed during run" in message for message in caplog.messages)
    store.close()


def test_run_pipeline_on_update_closed_store_re_raises_and_leaves_status(
    tmp_path: Path, monkeypatch, caplog
):
    # A StoreClosedError in an intermediate progress write (on_update) must be
    # re-raised (not swallowed as a transient failure): it aborts the run and
    # leaves the job in its last persisted state (running) for the next
    # startup's recovery, instead of recording a result that was never produced.
    store = SQLiteJobStore(tmp_path / "jobs.db")
    record = store.create(_make_record())

    real_update = store.update
    calls = {"n": 0}

    def flaky_update(job_id, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return real_update(job_id, **kwargs)  # the initial running update succeeds
        store.close()
        raise StoreClosedError("job store closed during an intermediate update")

    def fake_run(request, on_update=None, **kwargs):
        if on_update is not None:
            on_update(_ok_pipeline_result())  # triggers the closed-store update
        return _ok_pipeline_result()

    monkeypatch.setattr(pipeline_service, "run", fake_run)
    monkeypatch.setattr(store, "update", flaky_update)

    with caplog.at_level(logging.WARNING, logger="app.api.v1.routes.pipeline"):
        _run_pipeline(store, record.id, record.request, None, None)

    # The on_update StoreClosedError aborted the run: the result was never
    # recorded, and the job is left in its last persisted state (running).
    fetched = store.get(record.id)
    assert fetched is not None
    assert fetched.status == JobStatus.running
    assert fetched.result is None
    assert any("closed during run" in message for message in caplog.messages)


def test_unwritable_db_path_raises_clear_error(tmp_path: Path):
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"not a directory")
    with pytest.raises(RuntimeError, match="Cannot open SQLite job store"):
        SQLiteJobStore(blocker / "jobs.db")


def test_corrupt_db_file_raises_clear_error(tmp_path: Path):
    db_path = tmp_path / "jobs.db"
    db_path.write_bytes(b"this is not a sqlite database file")
    with pytest.raises(RuntimeError, match="Cannot open SQLite job store"):
        SQLiteJobStore(db_path)


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root bypasses file permissions")
def test_readonly_db_file_raises_clear_error(tmp_path: Path):
    db_path = tmp_path / "jobs.db"
    store = SQLiteJobStore(db_path)
    store.create(_make_record())
    store.close()
    os.chmod(db_path, 0o444)
    try:
        with pytest.raises(RuntimeError, match="Cannot open SQLite job store"):
            SQLiteJobStore(db_path)
    finally:
        os.chmod(db_path, 0o644)


def test_open_rejects_jobs_table_without_unique_id(tmp_path: Path):
    # A pre-existing jobs table with the right columns but no unique id must fail
    # the startup probe: the probe runs create()'s upsert, whose ON CONFLICT(id)
    # clause requires a unique constraint on id. Without this, the store opens
    # cleanly and every create() raises an unmodeled OperationalError (500).
    db_path = tmp_path / "jobs.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE jobs ("
            "id TEXT, "
            "status TEXT NOT NULL, "
            "request TEXT NOT NULL, "
            "result TEXT, "
            "error TEXT, "
            "created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL)"
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(RuntimeError, match="Cannot open SQLite job store"):
        SQLiteJobStore(db_path)


def test_open_failure_releases_connection_and_lock(tmp_path: Path):
    # The no-unique-id probe failure happens after BEGIN IMMEDIATE: without
    # closing the abandoned connection on the failure path, it would hold
    # SQLite's RESERVED lock until the exception's traceback chain is GC'd, and
    # a second opener would fail with "database is locked".
    db_path = tmp_path / "jobs.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE jobs ("
            "id TEXT, "
            "status TEXT NOT NULL, "
            "request TEXT NOT NULL, "
            "result TEXT, "
            "error TEXT, "
            "created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL)"
        )
        conn.commit()
    finally:
        conn.close()

    # Keep the exception (and its traceback chain) alive: without the fix the
    # failed open's connection would stay referenced through it.
    exc = None
    try:
        SQLiteJobStore(db_path)
    except RuntimeError as e:
        exc = e
    assert exc is not None
    assert "Cannot open SQLite job store" in str(exc)

    # A fresh connection must be able to take the write lock immediately.
    probe = sqlite3.connect(db_path, timeout=1)
    try:
        probe.execute("PRAGMA busy_timeout = 100")
        probe.execute("BEGIN IMMEDIATE")
        probe.execute("ROLLBACK")
    finally:
        probe.close()


def test_concurrent_first_open_of_legacy_db_tolerates_duplicate_column_migration(tmp_path: Path, monkeypatch):
    # Two concurrent first opens of a legacy DB (created before the start_time
    # column) can both read the column absent before either ALTER commits (the
    # check and the ALTER are not atomic across connections; DDL autocommits in
    # legacy mode): the second ALTER then fails with "duplicate column name".
    # That is the success case, not an error - both openers must come up (before
    # the fix the second failed startup and only self-healed on a manual retry).
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE processes ("
        "token TEXT PRIMARY KEY, started_at TEXT NOT NULL, last_seen_at TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()

    original_connect = sqlite3.connect
    arrival_lock = Lock()
    alter_arrivals = 0
    release_first = Event()

    def trace(statement: str) -> None:
        nonlocal alter_arrivals
        if "ALTER TABLE" in statement.upper() and "START_TIME" in statement.upper():
            # Deterministic interleaving: park the first opener at its ALTER
            # until the second opener has also passed the column check (both
            # now know the column is absent), then let both ALTERs race -
            # exactly one commits, the other must tolerate the duplicate.
            with arrival_lock:
                alter_arrivals += 1
                is_first = alter_arrivals == 1
            if is_first:
                release_first.wait(timeout=10)
            else:
                release_first.set()

    def connect_with_trace(*args, **kwargs):
        c = original_connect(*args, **kwargs)
        c.set_trace_callback(trace)
        return c

    monkeypatch.setattr(sqlite3, "connect", connect_with_trace)

    stores: list[SQLiteJobStore] = []
    errors: list[BaseException] = []

    def open_store() -> None:
        try:
            stores.append(SQLiteJobStore(db_path))
        except BaseException as exc:
            errors.append(exc)

    threads = [Thread(target=open_store) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert len(stores) == 2
    for store in stores:
        store.close()


def test_locked_db_fails_open_clearly(tmp_path: Path):
    # The README promises fail-fast for a locked DB, like a corrupt one: opening
    # a store whose write lock is held by another connection must raise a clear
    # RuntimeError after the busy timeout, not open and 500 later.
    db_path = tmp_path / "jobs.db"
    seed = SQLiteJobStore(db_path, busy_timeout_ms=200)
    seed.close()
    blocker = sqlite3.connect(db_path)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(RuntimeError, match="Cannot open SQLite job store"):
            SQLiteJobStore(db_path, busy_timeout_ms=200)
    finally:
        blocker.close()


def test_cross_process_lock_contention_fails_write_after_busy_timeout(tmp_path: Path):
    # A real separate process holding the DB write lock (not just an in-process
    # blocker connection) must make the store's write fail after the busy
    # timeout, and the store's connection must be rolled back (usable again)
    # once the lock is released: the in-process blocker tests cannot exercise
    # the genuine cross-process contention path.
    db_path = tmp_path / "jobs.db"
    store = SQLiteJobStore(db_path, busy_timeout_ms=300)
    record = store.create(_make_record())

    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sqlite3, time\n"
            f"conn = sqlite3.connect({str(db_path)!r})\n"
            "conn.execute('PRAGMA busy_timeout = 0')\n"
            "conn.execute('BEGIN IMMEDIATE')\n"
            "print('locked', flush=True)\n"
            "time.sleep(30)\n",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout = holder.stdout
    assert stdout is not None
    try:
        assert stdout.readline().strip() == "locked"
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            store.update(record.id, status=JobStatus.running)
    finally:
        holder.terminate()
        holder.wait(timeout=10)
        if stdout is not None:
            stdout.close()

    # After the lock is released, the store's connection must be usable again
    # (the failed write rolled back, no stale open transaction left behind).
    outcome = store.update(record.id, status=JobStatus.completed)
    assert outcome.updated
    updated = outcome.record
    assert updated is not None
    assert updated.status == JobStatus.completed
    store.close()


def test_locked_db_fails_at_startup_not_first_request(tmp_path: Path, monkeypatch):
    # A locked DB must fail the app at startup (lifespan) rather than surfacing
    # as an unmodeled 500 on the first job request while health stays green.
    import app.dependencies as dependencies

    db_path = tmp_path / "jobs.db"
    seed = SQLiteJobStore(db_path, busy_timeout_ms=200)
    seed.close()
    blocker = sqlite3.connect(db_path)
    blocker.execute("BEGIN IMMEDIATE")
    monkeypatch.setenv("HSJS_DB_PATH", str(db_path))
    get_settings.cache_clear()
    close_job_store()
    monkeypatch.setattr(
        dependencies,
        "_build_job_store",
        lambda settings: SQLiteJobStore(settings.db_path, busy_timeout_ms=200),
    )
    try:
        with pytest.raises(RuntimeError, match="Cannot open SQLite job store"):
            with TestClient(app):
                pass
    finally:
        blocker.close()
        close_job_store()
        get_settings.cache_clear()


def test_failed_commit_rolls_back_and_releases_connection(store_path: Path):
    store = SQLiteJobStore(store_path)
    record = store.create(_make_record())

    # Hold a write lock from a separate connection so the store's write hits the
    # busy timeout and the commit fails.
    blocker = sqlite3.connect(store_path)
    blocker.execute("PRAGMA busy_timeout = 0")
    blocker.execute("BEGIN IMMEDIATE")
    blocker.execute("UPDATE jobs SET status = 'blocked' WHERE id = ?", (record.id,))
    store._connection().execute("PRAGMA busy_timeout = 200")

    try:
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            store.update(record.id, status=JobStatus.running)

        # After the failed commit the store's connection must be rolled back: a
        # fresh connection must NOT be blocked by a stale open transaction, and it
        # must read the durable value (not the uncommitted 'running').
        fresh = sqlite3.connect(store_path, timeout=2)
        try:
            fresh.execute("PRAGMA busy_timeout = 1000")
            row = fresh.execute("SELECT status FROM jobs WHERE id = ?", (record.id,)).fetchone()
            assert row is not None
            assert row[0] == "queued"
        finally:
            fresh.close()
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()

    # The store's connection must be usable again (no stale transaction left open).
    outcome = store.update(record.id, status=JobStatus.completed)
    assert outcome.updated
    updated = outcome.record
    assert updated is not None
    assert updated.status == JobStatus.completed
    store.close()


def test_create_failed_commit_rolls_back_and_releases_connection(store_path: Path):
    # The create() path (unlike update()'s inline transaction) commits through
    # _execute_write: a failed commit must roll back there too, or the shared
    # connection would keep a stale open transaction that serves non-durable
    # data and blocks other connections.
    store = SQLiteJobStore(store_path)
    store.create(_make_record())

    # Hold a write lock from a separate connection so the store's write hits the
    # busy timeout and the commit fails.
    blocker = sqlite3.connect(store_path)
    blocker.execute("PRAGMA busy_timeout = 0")
    blocker.execute("BEGIN IMMEDIATE")
    blocker.execute("UPDATE jobs SET status = 'blocked' WHERE id = (SELECT id FROM jobs LIMIT 1)")
    store._connection().execute("PRAGMA busy_timeout = 200")

    try:
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            store.create(_make_record())

        # After the failed commit the store's connection must be rolled back: a
        # fresh connection must NOT be blocked by a stale open transaction, and
        # the failed create must not have leaked a partial row.
        fresh = sqlite3.connect(store_path, timeout=2)
        try:
            fresh.execute("PRAGMA busy_timeout = 1000")
            count = fresh.execute("SELECT COUNT(*) FROM jobs").fetchone()
            assert count is not None
            assert count[0] == 1
        finally:
            fresh.close()
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()

    # The store's connection must be usable again (no stale transaction left open).
    new_record = store.create(_make_record())
    fetched = store.get(new_record.id)
    assert fetched is not None
    assert fetched.status == JobStatus.queued
    store.close()


class _FailingCommitConnection:
    """Proxies a real connection whose commit() raises OSError (disk full / I/O error)."""

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real

    def execute(self, *args, **kwargs):
        return self._real.execute(*args, **kwargs)

    def rollback(self) -> None:
        self._real.rollback()

    def close(self) -> None:
        self._real.close()

    def commit(self) -> None:
        raise OSError("No space left on device")


def test_create_db_write_oserror_rolls_back_and_propagates(store_path: Path, monkeypatch):
    # An OS-level write failure (disk full / I/O error) surfacing as OSError from the
    # create() commit must roll back in _execute_write and propagate to the route's
    # declared 503 mapping — not leak a partial row or a stale open transaction.
    store = SQLiteJobStore(store_path)
    monkeypatch.setattr(store, "_conn", _FailingCommitConnection(store._connection()))

    with pytest.raises(OSError, match="No space left on device"):
        store.create(_make_record())
    monkeypatch.undo()  # restore the real connection for the checks below

    # The failed create rolled back (no partial row), and the connection is usable again.
    assert store.list() == []
    record = store.create(_make_record())
    fetched = store.get(record.id)
    assert fetched is not None
    assert fetched.status == JobStatus.queued
    store.close()


def test_update_db_write_oserror_rolls_back_and_propagates(store_path: Path, monkeypatch):
    # The same contract for update()'s inline transaction: an OSError from the commit
    # must roll back the write and propagate, leaving the job in its last durable state.
    store = SQLiteJobStore(store_path)
    record = store.create(_make_record())
    monkeypatch.setattr(store, "_conn", _FailingCommitConnection(store._connection()))

    with pytest.raises(OSError, match="No space left on device"):
        store.update(record.id, status=JobStatus.running)
    monkeypatch.undo()  # restore the real connection for the checks below

    fetched = store.get(record.id)
    assert fetched is not None
    assert fetched.status == JobStatus.queued  # the failed update rolled back
    outcome = store.update(record.id, status=JobStatus.completed)  # connection usable again
    assert outcome.updated and outcome.record is not None
    assert outcome.record.status == JobStatus.completed
    store.close()


def test_app_shutdown_releases_job_store():
    store = get_job_store()
    assert isinstance(store, SQLiteJobStore)
    assert store._conn is not None  # connection is open before shutdown
    with TestClient(app):
        pass
    assert store._conn is None  # close() actually ran on shutdown
    assert get_job_store() is not store


def _run_lifespan_with_shutdown_error(shutdown_error: RuntimeError) -> None:
    """Drive the lifespan context manager by hand, delivering `shutdown_error` at
    the yield point exactly as `async with` would (the TestClient path cannot
    inject an error there)."""
    import asyncio

    import app.main as main_module

    async def scenario() -> None:
        ctx = main_module.lifespan(main_module.app)
        await ctx.__aenter__()
        try:
            raise shutdown_error
        except RuntimeError as exc:
            await ctx.__aexit__(type(exc), exc, exc.__traceback__)
            raise

    with pytest.raises(RuntimeError, match="simulated failure at the yield point"):
        asyncio.run(scenario())


def test_lifespan_teardown_closes_store_and_preserves_shutdown_error(monkeypatch):
    # Teardown must run even when an error is delivered at the yield point (the
    # startup section is guarded; teardown was not), and it must not mask that
    # original error.
    closed: list[bool] = []
    import app.main as main_module

    monkeypatch.setattr(main_module, "close_job_store", lambda: closed.append(True))

    _run_lifespan_with_shutdown_error(RuntimeError("simulated failure at the yield point"))

    assert closed == [True]  # teardown ran despite the shutdown error


def test_lifespan_teardown_close_failure_does_not_mask_shutdown_error(monkeypatch):
    # A close_job_store() that raises during teardown must not replace the original
    # shutdown failure (a masking teardown would hide the real cause from logs).
    import app.main as main_module

    def failing_close() -> None:
        raise OSError("simulated close failure")

    monkeypatch.setattr(main_module, "close_job_store", failing_close)

    _run_lifespan_with_shutdown_error(RuntimeError("simulated failure at the yield point"))


def test_startup_recovery_marks_interrupted_jobs_failed(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "jobs.db"
    monkeypatch.setenv("HSJS_DB_PATH", str(db_path))
    get_settings.cache_clear()
    close_job_store()
    try:
        # Seed the store with jobs in non-terminal states, simulating an unclean
        # restart that abandoned in-flight background tasks, plus terminal
        # control jobs that recovery must leave untouched (a "recovery fails ALL
        # jobs" regression must be caught here, not only incidentally by the
        # subprocess restart test).
        store = SQLiteJobStore(db_path)
        queued = store.create(_make_record())
        running = store.create(_make_record())
        store.update(running.id, status=JobStatus.running)
        completed = store.create(_make_record())
        store.update(completed.id, status=JobStatus.completed)
        failed = store.create(_make_record())
        store.update(failed.id, status=JobStatus.failed, error="earlier failure")
        store.close()

        # Starting the app triggers startup recovery.
        with TestClient(app):
            pass

        conn = sqlite3.connect(db_path, timeout=5)
        try:
            rows = conn.execute(
                "SELECT id, status, error FROM jobs WHERE id IN (?, ?, ?, ?)",
                (queued.id, running.id, completed.id, failed.id),
            ).fetchall()
        finally:
            conn.close()
        by_id = {row[0]: (row[1], row[2]) for row in rows}
        assert by_id[queued.id][0] == "failed"
        assert by_id[running.id][0] == "failed"
        assert "interrupted" in by_id[queued.id][1]
        assert "interrupted" in by_id[running.id][1]
        # Control group: terminal jobs survive recovery untouched.
        assert by_id[completed.id][0] == "completed"
        assert by_id[completed.id][1] is None
        assert by_id[failed.id][0] == "failed"
        assert by_id[failed.id][1] == "earlier failure"
    finally:
        close_job_store()
        get_settings.cache_clear()


def _poison_row(
    db_path: Path,
    job_id: str = "poisoned-job",
    status: str = "cancelled",
    request: str = "{}",
    result: str | None = None,
) -> None:
    """Insert a row the current model version cannot parse (unknown status or
    malformed request/result JSON)."""
    timestamp = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        conn.execute(
            "INSERT INTO jobs (id, status, request, result, error, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, NULL, ?, ?)",
            (job_id, status, request, result, timestamp, timestamp),
        )
        conn.commit()
    finally:
        conn.close()


def test_list_and_get_isolate_unreadable_row(tmp_path: Path, caplog):
    # A row with a status the current JobStatus enum cannot parse must not break
    # list()/get() (unmodeled 500 on GET /jobs and GET /jobs/{id}): it is
    # quarantined with a logged error and served as missing.
    db_path = tmp_path / "jobs.db"
    store = SQLiteJobStore(db_path)
    good = store.create(_make_record())
    _poison_row(db_path)

    with caplog.at_level(logging.ERROR, logger="app.persistence"):
        listed = store.list()
        poisoned = store.get("poisoned-job")

    assert [record.id for record in listed] == [good.id]
    assert poisoned is None
    assert any("poisoned-job" in message for message in caplog.messages)
    store.close()


def test_list_and_get_isolate_malformed_request_json(tmp_path: Path, caplog):
    # A row whose request is not valid JSON must be quarantined like an unknown
    # status: list() skips it and get() serves the declared 404 (None), instead
    # of an unmodeled 500.
    db_path = tmp_path / "jobs.db"
    store = SQLiteJobStore(db_path)
    good = store.create(_make_record())
    _poison_row(db_path, job_id="bad-request", status="queued", request="not-json")

    with caplog.at_level(logging.ERROR, logger="app.persistence"):
        listed = store.list()
        poisoned = store.get("bad-request")

    assert [record.id for record in listed] == [good.id]
    assert poisoned is None
    assert any("bad-request" in message for message in caplog.messages)
    store.close()


def test_list_and_get_isolate_malformed_result_json(tmp_path: Path, caplog):
    # A row whose result is not valid JSON must be quarantined the same way.
    db_path = tmp_path / "jobs.db"
    store = SQLiteJobStore(db_path)
    good = store.create(_make_record())
    _poison_row(
        db_path,
        job_id="bad-result",
        status="completed",
        request=_make_record().request.model_dump_json(),
        result="not-json",
    )

    with caplog.at_level(logging.ERROR, logger="app.persistence"):
        listed = store.list()
        poisoned = store.get("bad-result")

    assert [record.id for record in listed] == [good.id]
    assert poisoned is None
    assert any("bad-result" in message for message in caplog.messages)
    store.close()


def test_update_poisoned_row_reports_updated_without_record(tmp_path: Path):
    # A row whose stored payload the current model version cannot parse is still
    # updated durably; update() must report that as updated=True with record=None —
    # distinguishable from a missing job (updated=False), which used to collapse
    # into the same bare None.
    db_path = tmp_path / "jobs.db"
    store = SQLiteJobStore(db_path)
    _poison_row(db_path, job_id="bad-request", status="queued", request="not-json")

    outcome = store.update("bad-request", status=JobStatus.failed, error="interrupted by server restart")
    missing = store.update("does-not-exist", status=JobStatus.running)

    assert outcome.updated is True
    assert outcome.record is None  # the row still cannot be parsed back into a record
    assert not missing.updated
    assert missing.record is None

    conn = sqlite3.connect(db_path, timeout=5)
    try:
        status, error = conn.execute("SELECT status, error FROM jobs WHERE id = ?", ("bad-request",)).fetchone()
    finally:
        conn.close()
    assert status == "failed"  # the update was durable even though the record is unreadable
    assert error == "interrupted by server restart"
    store.close()


def test_startup_recovery_survives_poisoned_row(tmp_path: Path, monkeypatch):
    # The DB is persisted across deployments: a poisoned row (e.g. left by a
    # newer version before a rollback) must not crash startup recovery, and the
    # recoverable jobs must still be recovered.
    db_path = tmp_path / "jobs.db"
    monkeypatch.setenv("HSJS_DB_PATH", str(db_path))
    get_settings.cache_clear()
    close_job_store()
    try:
        store = SQLiteJobStore(db_path)
        queued = store.create(_make_record())
        _poison_row(db_path)
        store.close()

        with TestClient(app):
            pass

        conn = sqlite3.connect(db_path, timeout=5)
        try:
            rows = dict(conn.execute("SELECT id, status FROM jobs").fetchall())
        finally:
            conn.close()
        assert rows[queued.id] == "failed"
        # The poisoned row is quarantined, not mutated or deleted.
        assert rows["poisoned-job"] == "cancelled"
    finally:
        close_job_store()
        get_settings.cache_clear()


def test_startup_recovery_fails_poisoned_non_terminal_row(tmp_path: Path, monkeypatch):
    # A poisoned row with a valid non-terminal status (malformed JSON) is
    # quarantined from list() and therefore invisible to the recovery loop:
    # without a direct raw-id scan it would stay stuck non-terminal forever.
    db_path = tmp_path / "jobs.db"
    monkeypatch.setenv("HSJS_DB_PATH", str(db_path))
    get_settings.cache_clear()
    close_job_store()
    try:
        store = SQLiteJobStore(db_path)
        healthy = store.create(_make_record())
        _poison_row(db_path, job_id="poisoned-running", status="running", request="not-json")
        store.close()

        with TestClient(app):
            pass

        conn = sqlite3.connect(db_path, timeout=5)
        try:
            rows = {
                row[0]: (row[1], row[2])
                for row in conn.execute("SELECT id, status, error FROM jobs").fetchall()
            }
        finally:
            conn.close()
        assert rows[healthy.id][0] == "failed"
        assert rows["poisoned-running"][0] == "failed"
        assert "interrupted" in rows["poisoned-running"][1]
    finally:
        close_job_store()
        get_settings.cache_clear()


def _insert_peer_marker(
    db_path: Path, token: str, last_seen_at: str, started_at: str | None = None, start_time: int | None = None
) -> None:
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        conn.execute(
            "INSERT INTO processes (token, started_at, last_seen_at, start_time) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(token) DO UPDATE SET last_seen_at = excluded.last_seen_at",
            (token, started_at or last_seen_at, last_seen_at, start_time),
        )
        conn.commit()
    finally:
        conn.close()


def test_recovery_skips_when_another_process_is_live(tmp_path: Path):
    # The compose deployment mounts one shared volume: a co-booting process must
    # not fail the live process's in-flight jobs. A live peer that started
    # before the job's last update may be working on it, so recovery skips it.
    db_path = tmp_path / "jobs.db"
    store = SQLiteJobStore(db_path)
    job = store.create(_make_record())
    started = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    _insert_peer_marker(db_path, "peer-host:99999", datetime.now(timezone.utc).isoformat(), started_at=started)

    recovered = recover_interrupted_jobs(store, grace_seconds=300)

    assert recovered == 0
    fetched = store.get(job.id)
    assert fetched is not None
    assert fetched.status == JobStatus.queued
    store.close()


def test_recovery_runs_when_peer_marker_is_stale(tmp_path: Path):
    # A peer that has been silent beyond the grace window is treated as gone:
    # its abandoned jobs are recovered.
    db_path = tmp_path / "jobs.db"
    store = SQLiteJobStore(db_path)
    job = store.create(_make_record())
    stale = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    _insert_peer_marker(db_path, "peer-host:99999", stale)

    recovered = recover_interrupted_jobs(store, grace_seconds=300)

    assert recovered == 1
    fetched = store.get(job.id)
    assert fetched is not None
    assert fetched.status == JobStatus.failed
    store.close()


def test_recovery_runs_when_same_host_peer_is_dead(tmp_path: Path):
    # A fresh marker from a crashed same-host process (unclean shutdown) must not
    # count as a live peer: after a quick restart (systemd/docker auto-restart)
    # within the grace window the only "peer" is the dead predecessor, and
    # counting it would skip recovery and leave interrupted jobs stuck
    # non-terminal.
    db_path = tmp_path / "jobs.db"
    store = SQLiteJobStore(db_path)
    job = store.create(_make_record())
    # A real PID that has just exited: reliably dead, unlike an arbitrary number.
    # Its start time is recorded while it is still alive: if the PID is reused
    # before the check, the start-time mismatch still classifies the peer as
    # dead (a PID-existence-only check would flake here).
    exited = subprocess.Popen([sys.executable, "-c", "pass"])
    start_time = _process_start_time(exited.pid)
    exited.wait()
    dead_pid = exited.pid
    _insert_peer_marker(
        db_path, f"{socket.gethostname()}:{dead_pid}", datetime.now(timezone.utc).isoformat(), start_time=start_time
    )

    recovered = recover_interrupted_jobs(store, grace_seconds=300)

    assert recovered == 1
    fetched = store.get(job.id)
    assert fetched is not None
    assert fetched.status == JobStatus.failed
    store.close()


def test_recovery_skips_when_same_host_peer_is_alive(tmp_path: Path):
    # A fresh marker from a live same-host process (a genuinely co-running
    # instance) that started before the job's last update must still count as a
    # live peer, so recovery skips the job.
    db_path = tmp_path / "jobs.db"
    store = SQLiteJobStore(db_path)
    job = store.create(_make_record())
    live_proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        started = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        # The marker carries the live process's real start time: protection
        # must hold through the start-time verification, not only the PID check.
        start_time = _process_start_time(live_proc.pid)
        _insert_peer_marker(
            db_path,
            f"{socket.gethostname()}:{live_proc.pid}",
            datetime.now(timezone.utc).isoformat(),
            started_at=started,
            start_time=start_time,
        )
        recovered = recover_interrupted_jobs(store, grace_seconds=300)
    finally:
        live_proc.terminate()
        live_proc.wait()

    assert recovered == 0
    fetched = store.get(job.id)
    assert fetched is not None
    assert fetched.status == JobStatus.queued
    store.close()


def test_recovery_skips_when_same_host_peer_is_alive_but_write_idle(tmp_path: Path):
    # A live same-host peer that has not written within the grace window (a long
    # pipeline run) must still protect its in-flight job: same-host liveness is
    # the PID, not marker freshness. Treating the write-idle peer as dead would
    # fail its in-flight job.
    db_path = tmp_path / "jobs.db"
    store = SQLiteJobStore(db_path)
    job = store.create(_make_record())
    store.update(job.id, status=JobStatus.running)
    live_proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        started = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        idle = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        _insert_peer_marker(db_path, f"{socket.gethostname()}:{live_proc.pid}", idle, started_at=started)
        recovered = recover_interrupted_jobs(store, grace_seconds=300)
    finally:
        live_proc.terminate()
        live_proc.wait()

    assert recovered == 0
    fetched = store.get(job.id)
    assert fetched is not None
    assert fetched.status == JobStatus.running
    store.close()


def test_marker_heartbeat_refreshes_write_idle_marker(tmp_path: Path):
    # A live store that is not writing must still keep its liveness marker
    # fresh: marker freshness is the only liveness signal a peer on another
    # host can use, and without the heartbeat a write-idle peer (a long
    # pipeline run) beyond the grace window would be treated as dead.
    db_path = tmp_path / "jobs.db"
    store = SQLiteJobStore(db_path, marker_heartbeat_s=0.1)
    token = f"{socket.gethostname()}:{os.getpid()}"

    def last_seen() -> str:
        conn = sqlite3.connect(db_path, timeout=5)
        try:
            row = conn.execute("SELECT last_seen_at FROM processes WHERE token = ?", (token,)).fetchone()
        finally:
            conn.close()
        assert row is not None
        return row[0]

    first = last_seen()
    time.sleep(0.5)
    second = last_seen()
    assert second > first  # refreshed without any write
    store.close()


def test_heartbeat_beat_failure_rolls_back_and_releases_connection(tmp_path: Path):
    # A beat whose write fails (a locked DB) must roll back its implicit
    # transaction, or the shared connection would keep a stale open transaction
    # that serves non-durable data, blocks other connections, and lets a
    # concurrent recovery_snapshot BEGIN fail with "cannot start a
    # transaction within a transaction".
    db_path = tmp_path / "jobs.db"
    store = SQLiteJobStore(db_path, busy_timeout_ms=100, marker_heartbeat_s=0.05)

    # Hold a write lock from a separate connection so the beat's write fails
    # (database is locked) and the rollback path runs.
    blocker = sqlite3.connect(db_path)
    blocker.execute("PRAGMA busy_timeout = 0")
    blocker.execute("BEGIN IMMEDIATE")
    try:
        time.sleep(0.5)  # let several beats fire and fail against the lock
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()

    # After the failed beat(s) and the lock release, the connection must not be
    # left in an open transaction. Poll briefly: a beat may still be settling
    # (mid-commit) right after the lock release.
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        with store._lock:
            if store._conn is not None and not store._conn.in_transaction:
                break
        time.sleep(0.05)
    with store._lock:
        assert store._conn is not None
        assert store._conn.in_transaction is False
    store.close()


def test_heartbeat_disabled_with_none_interval(tmp_path: Path):
    # marker_heartbeat_s=None disables the heartbeat entirely (no thread): the
    # marker is then refreshed only by writes, which is the documented
    # write-activity-only liveness mode.
    db_path = tmp_path / "jobs.db"
    store = SQLiteJobStore(db_path, marker_heartbeat_s=None)
    assert store._heartbeat_thread is None
    assert store._heartbeat_stop is None
    store.close()


def test_heartbeat_disabled_with_zero_interval(tmp_path: Path):
    # A non-positive marker_heartbeat_s also disables the heartbeat (no thread),
    # mirroring the None path: a misconfigured 0/-1 must not spawn a tight
    # busy-loop thread.
    db_path = tmp_path / "jobs.db"
    store = SQLiteJobStore(db_path, marker_heartbeat_s=0)
    assert store._heartbeat_thread is None
    assert store._heartbeat_stop is None
    store.close()


def test_cross_host_write_idle_peer_is_still_protected(tmp_path: Path, monkeypatch):
    # The shared-volume --scale topology: a live peer on another host that has
    # not written within the grace window (a long pipeline run) must still
    # protect its in-flight job. Only the heartbeat keeps its marker fresh -
    # without it the co-booter's recovery would fail the live peer's job.
    import app.persistence as persistence

    db_path = tmp_path / "jobs.db"
    with monkeypatch.context() as m:
        m.setattr(persistence.socket, "gethostname", lambda: "peer-host")
        peer = SQLiteJobStore(db_path, marker_heartbeat_s=0.1)
    job = peer.create(_make_record())
    peer.update(job.id, status=JobStatus.running)
    last_write = time.monotonic()
    peer_token = f"peer-host:{os.getpid()}"
    try:
        # Wait until the peer is verifiably write-idle beyond the 1s grace
        # window (at least 1s since its last write) AND its heartbeat has
        # refreshed the marker since. A fixed sleep flakes: under preemption the
        # heartbeat can lag beyond the grace window and make the marker stale,
        # wrongly recovering the live peer's job.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if time.monotonic() - last_write < 1.0:
                time.sleep(0.05)
                continue
            cutoff = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            conn = sqlite3.connect(db_path, timeout=5)
            try:
                row = conn.execute(
                    "SELECT last_seen_at FROM processes WHERE token = ?", (peer_token,)
                ).fetchone()
            finally:
                conn.close()
            if row is not None and row[0] >= cutoff:
                break
            time.sleep(0.05)
        co_booter = SQLiteJobStore(db_path)
        recovered = recover_interrupted_jobs(co_booter, grace_seconds=1)
        fetched = co_booter.get(job.id)
        co_booter.close()
    finally:
        peer.close()

    assert recovered == 0
    assert fetched is not None
    assert fetched.status == JobStatus.running


def test_recovery_runs_when_same_host_pid_is_reused(tmp_path: Path):
    # A live same-host process that reused a dead predecessor's PID must not
    # count as the predecessor: the marker's recorded start time differs from
    # the live process's, so the peer is treated as gone (without the start
    # time check the predecessor's interrupted jobs would be protected until
    # the 7-day marker prune).
    if _process_start_time(os.getpid()) is None:
        pytest.skip("/proc not available")
    db_path = tmp_path / "jobs.db"
    store = SQLiteJobStore(db_path)
    job = store.create(_make_record())
    live_proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        start_time = _process_start_time(live_proc.pid)
        assert start_time is not None
        started = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        # A fresh marker from the (dead) predecessor whose PID the live
        # process now reuses: same PID, different start time.
        _insert_peer_marker(
            db_path,
            f"{socket.gethostname()}:{live_proc.pid}",
            datetime.now(timezone.utc).isoformat(),
            started_at=started,
            start_time=start_time + 1,
        )
        assert (
            store._peer_is_live(
                f"{socket.gethostname()}:{live_proc.pid}",
                datetime.now(timezone.utc).isoformat(),
                (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat(),
                start_time + 1,
            )
            is False
        )
        recovered = recover_interrupted_jobs(store, grace_seconds=300)
    finally:
        live_proc.terminate()
        live_proc.wait()

    assert recovered == 1
    fetched = store.get(job.id)
    assert fetched is not None
    assert fetched.status == JobStatus.failed
    store.close()


def test_concurrent_startups_still_recover_dead_predecessors_jobs(tmp_path: Path):
    # Two processes start concurrently on a shared DB that holds interrupted
    # jobs from a dead predecessor: each sees the other as a live peer, but
    # neither started before the job's last update, so the job must still be
    # recovered (a skip-all-on-any-live-peer guard left it stuck non-terminal).
    db_path = tmp_path / "jobs.db"
    store = SQLiteJobStore(db_path)
    job = store.create(_make_record())
    store.update(job.id, status=JobStatus.running)
    # Age the job's last update so both peers verifiably started after it.
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        conn.execute(
            "UPDATE jobs SET updated_at = ? WHERE id = ?",
            ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), job.id),
        )
        conn.commit()
    finally:
        conn.close()
    live1 = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    live2 = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        now = datetime.now(timezone.utc)
        _insert_peer_marker(db_path, f"{socket.gethostname()}:{live1.pid}", now.isoformat())
        _insert_peer_marker(db_path, f"{socket.gethostname()}:{live2.pid}", now.isoformat())
        recovered = recover_interrupted_jobs(store, grace_seconds=300)
    finally:
        live1.terminate()
        live1.wait()
        live2.terminate()
        live2.wait()

    assert recovered == 1
    fetched = store.get(job.id)
    assert fetched is not None
    assert fetched.status == JobStatus.failed
    assert "interrupted" in (fetched.error or "")
    store.close()


def test_recovery_does_not_fail_job_committed_by_live_peer_during_recovery(tmp_path: Path, monkeypatch):
    # A live peer sharing the DB that commits a new job while this process's
    # startup recovery is running must not have that job failed: with the
    # protection set, the record list and the raw scan as separate reads, the
    # commit could land in the raw scan but outside the protection set and get
    # failed despite the live peer. The pass works from one consistent
    # snapshot, so a commit after the snapshot is invisible to the whole pass.
    db_path = tmp_path / "jobs.db"
    store = SQLiteJobStore(db_path)
    abandoned = store.create(_make_record())
    _poison_row(db_path, job_id="poisoned-running", status="running", request="not-json")
    # Age both abandoned rows' last update so the peer (started 5 minutes ago)
    # verifiably started after it: it cannot be working on them, so they are
    # not protected and recovery must fail them - which also gives the peer's
    # commit a deterministic moment to land (the first recovery write).
    aged = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        conn.execute(
            "UPDATE jobs SET updated_at = ? WHERE id IN (?, ?)",
            (aged, abandoned.id, "poisoned-running"),
        )
        conn.commit()
    finally:
        conn.close()
    started = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    _insert_peer_marker(db_path, "peer-host:99999", datetime.now(timezone.utc).isoformat(), started_at=started)

    peer_job_id = "peer-job"
    original_update = store.update
    fired = False

    def update_during_recovery(job_id, *, status, result=_UNSET, error=_UNSET):
        nonlocal fired
        if not fired:
            fired = True
            now = datetime.now(timezone.utc).isoformat()
            peer_conn = sqlite3.connect(db_path, timeout=5)
            try:
                peer_conn.execute(
                    "INSERT INTO jobs (id, status, request, result, error, created_at, updated_at) "
                    "VALUES (?, ?, ?, NULL, NULL, ?, ?)",
                    (peer_job_id, "queued", "{}", now, now),
                )
                peer_conn.commit()
            finally:
                peer_conn.close()
        return original_update(job_id, status=status, result=result, error=error)

    monkeypatch.setattr(store, "update", update_during_recovery)

    recovered = recover_interrupted_jobs(store, grace_seconds=300)

    assert recovered == 2
    by_id = {record.id: record.status for record in store.list()}
    assert by_id[abandoned.id] == JobStatus.failed
    # The peer's job committed during the pass is after the snapshot: the pass
    # cannot see it, and it must survive.
    assert by_id[peer_job_id] == JobStatus.queued
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        rows = {row[0]: (row[1], row[2]) for row in conn.execute("SELECT id, status, error FROM jobs").fetchall()}
    finally:
        conn.close()
    assert rows["poisoned-running"][0] == "failed"
    assert "interrupted" in rows["poisoned-running"][1]
    store.close()


def test_peer_liveness_unparseable_token_falls_back_to_time(tmp_path: Path):
    # A token without the "host:pid" shape cannot be PID-checked: the marker's
    # freshness decides (a fresh marker protects, a stale one does not).
    db_path = tmp_path / "jobs.db"
    store = SQLiteJobStore(db_path)
    job = store.create(_make_record())
    started = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    _insert_peer_marker(db_path, "no-colon-token", datetime.now(timezone.utc).isoformat(), started_at=started)

    recovered = recover_interrupted_jobs(store, grace_seconds=300)

    assert recovered == 0
    fetched = store.get(job.id)
    assert fetched is not None
    assert fetched.status == JobStatus.queued

    # The same unparseable token, stale beyond the grace window, no longer protects.
    stale = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    _insert_peer_marker(db_path, "no-colon-token", stale, started_at=started)
    recovered = recover_interrupted_jobs(store, grace_seconds=300)
    assert recovered == 1
    store.close()


def test_peer_liveness_non_numeric_pid_falls_back_to_time(tmp_path: Path):
    # A same-host token whose pid is not numeric cannot be PID-checked: the
    # marker's freshness decides.
    db_path = tmp_path / "jobs.db"
    store = SQLiteJobStore(db_path)
    job = store.create(_make_record())
    started = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    _insert_peer_marker(
        db_path, f"{socket.gethostname()}:not-a-pid", datetime.now(timezone.utc).isoformat(), started_at=started
    )

    recovered = recover_interrupted_jobs(store, grace_seconds=300)

    assert recovered == 0
    fetched = store.get(job.id)
    assert fetched is not None
    assert fetched.status == JobStatus.queued

    stale = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    _insert_peer_marker(db_path, f"{socket.gethostname()}:not-a-pid", stale, started_at=started)
    recovered = recover_interrupted_jobs(store, grace_seconds=300)
    assert recovered == 1
    store.close()


def test_peer_liveness_permission_error_counts_as_alive(tmp_path: Path, monkeypatch):
    # os.kill raising PermissionError means the PID exists but is owned by
    # another user: the peer is alive, even when its marker is stale beyond the
    # grace window.
    import app.persistence as persistence

    db_path = tmp_path / "jobs.db"
    store = SQLiteJobStore(db_path)
    job = store.create(_make_record())
    started = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    stale = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    _insert_peer_marker(db_path, f"{socket.gethostname()}:424242", stale, started_at=started)

    def deny_kill(pid: int, sig: int) -> None:
        raise PermissionError("no permission to signal")

    monkeypatch.setattr(persistence.os, "kill", deny_kill)
    recovered = recover_interrupted_jobs(store, grace_seconds=300)

    assert recovered == 0
    fetched = store.get(job.id)
    assert fetched is not None
    assert fetched.status == JobStatus.queued
    store.close()


def test_peer_liveness_zombie_same_host_peer_is_dead(tmp_path: Path, monkeypatch):
    # A zombie (defunct, not reaped) same-host peer passes the signal probe and
    # keeps its start time, but it cannot do work: it must be classified dead so
    # recovery does not skip its interrupted jobs (a PID+start-time-only check
    # would protect them until the marker is pruned).
    import app.persistence as persistence

    db_path = tmp_path / "jobs.db"
    store = SQLiteJobStore(db_path)
    token = f"{socket.gethostname()}:424242"
    now = datetime.now(timezone.utc).isoformat()
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()

    monkeypatch.setattr(persistence.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(persistence, "_proc_available", lambda: True)
    monkeypatch.setattr(persistence, "_read_proc_stat", lambda pid: ("Z", 12345))

    assert store._peer_is_live(token, now, cutoff, 12345) is False
    store.close()


def test_peer_liveness_just_reaped_same_host_peer_is_dead(tmp_path: Path, monkeypatch):
    # A same-host peer reaped between the signal probe and the /proc read must
    # be classified dead (the process is gone), not live: the old None branch
    # misattributed it to the non-Linux fallback and returned True, protecting
    # the just-dead peer's interrupted jobs and skipping this startup's recovery.
    import app.persistence as persistence

    db_path = tmp_path / "jobs.db"
    store = SQLiteJobStore(db_path)
    token = f"{socket.gethostname()}:424242"
    now = datetime.now(timezone.utc).isoformat()
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()

    monkeypatch.setattr(persistence.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(persistence, "_proc_available", lambda: True)
    monkeypatch.setattr(persistence, "_read_proc_stat", lambda pid: None)

    assert store._peer_is_live(token, now, cutoff, 12345) is False
    store.close()


def test_peer_liveness_live_same_host_peer_with_matching_start_time_is_alive(tmp_path: Path, monkeypatch):
    # Control: a live (non-zombie) same-host peer whose start time matches the
    # marker is alive (the zombie/just-reaped fixes must not over-classify a
    # genuinely live peer as dead).
    import app.persistence as persistence

    db_path = tmp_path / "jobs.db"
    store = SQLiteJobStore(db_path)
    token = f"{socket.gethostname()}:424242"
    now = datetime.now(timezone.utc).isoformat()
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()

    monkeypatch.setattr(persistence.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(persistence, "_proc_available", lambda: True)
    monkeypatch.setattr(persistence, "_read_proc_stat", lambda pid: ("S", 12345))

    assert store._peer_is_live(token, now, cutoff, 12345) is True
    store.close()


def test_peer_liveness_unprobeable_pids_fall_back_to_marker_freshness(tmp_path: Path):
    # Pids that cannot be probed with os.kill must not crash startup recovery and
    # must fall back to the marker freshness signal (not an unconditional "alive"):
    # a pid beyond the platform's pid_t range raises OverflowError (previously
    # uncaught, failing boot), and non-positive pids are process-group selectors
    # whose probe succeeds for reasons unrelated to the marker's process.
    db_path = tmp_path / "jobs.db"
    store = SQLiteJobStore(db_path)
    now = datetime.now(timezone.utc).isoformat()
    stale = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()

    for pid in ("99999999999", "0", "-1"):  # > INT_MAX, own-group selector, broadcast
        token = f"{socket.gethostname()}:{pid}"
        assert store._peer_is_live(token, now, cutoff, None) is True
        assert store._peer_is_live(token, stale, cutoff, None) is False
    store.close()


def test_open_preserves_fresh_peer_marker_and_prunes_stale(tmp_path: Path):
    # The open-time prune must remove only markers older than 7 days: pruning a
    # fresh peer marker would silently disable the live-peer guard (recovery
    # would then fail that live peer's in-flight jobs), and never pruning would
    # let the table grow without bound from crashed processes.
    db_path = tmp_path / "jobs.db"
    seed = SQLiteJobStore(db_path)
    seed.close()
    fresh = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    stale = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    _insert_peer_marker(db_path, "peer-host:11111", fresh)
    _insert_peer_marker(db_path, "peer-host:22222", stale)

    store = SQLiteJobStore(db_path)
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        try:
            tokens = {row[0] for row in conn.execute("SELECT token FROM processes").fetchall()}
        finally:
            conn.close()
        own_token = f"{socket.gethostname()}:{os.getpid()}"
        assert tokens == {"peer-host:11111", own_token}
    finally:
        store.close()


def test_open_registers_own_liveness_marker(tmp_path: Path):
    # Opening the store registers this process's liveness marker (the signal a
    # peer startup uses to avoid failing this process's in-flight jobs), and a
    # second instance in the same process upserts the same token instead of
    # adding a second row.
    db_path = tmp_path / "jobs.db"
    store = SQLiteJobStore(db_path)
    token = f"{socket.gethostname()}:{os.getpid()}"

    conn = sqlite3.connect(db_path, timeout=5)
    try:
        rows = conn.execute("SELECT token, started_at, last_seen_at FROM processes").fetchall()
    finally:
        conn.close()
    assert [row[0] for row in rows] == [token]
    assert rows[0][1] == rows[0][2]

    second = SQLiteJobStore(db_path)
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        rows = conn.execute("SELECT token FROM processes").fetchall()
    finally:
        conn.close()
    assert [row[0] for row in rows] == [token]
    second.close()
    store.close()


def test_open_refreshes_start_time_and_started_at_on_pid_reuse(tmp_path):
    # A process that reuses a dead predecessor's PID must refresh the marker's
    # start_time (and started_at) on the token-conflict upsert, not just
    # last_seen_at: the token is hostname:pid, so a conflict means the (dead,
    # reaped) predecessor owned this PID. Keeping the predecessor's start_time
    # would make a co-booting peer's start-time check mismatch the live process
    # and misclassify it dead, failing its in-flight jobs; keeping the
    # predecessor's started_at would let the successor protect jobs it never
    # touched (started "before" their last update).
    if _process_start_time(os.getpid()) is None:
        pytest.skip("/proc not available")
    db_path = tmp_path / "jobs.db"
    seed = SQLiteJobStore(db_path)
    seed.close()  # creates the schema and removes this process's marker
    token = f"{socket.gethostname()}:{os.getpid()}"
    own_start_time = _process_start_time(os.getpid())
    assert own_start_time is not None
    # Simulate the dead predecessor's leftover marker: same token (reused PID),
    # stale start time and start time.
    stale_started = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    _insert_peer_marker(
        db_path,
        token,
        datetime.now(timezone.utc).isoformat(),
        started_at=stale_started,
        start_time=own_start_time + 12345,
    )

    store = SQLiteJobStore(db_path)
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        try:
            row = conn.execute(
                "SELECT started_at, last_seen_at, start_time FROM processes WHERE token = ?", (token,)
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        # start_time must be the live process's, not the predecessor's.
        assert row[2] == own_start_time
        # started_at must be the reopen (successor) time, not the predecessor's.
        assert row[0] != stale_started
        assert row[0] == row[1]
    finally:
        store.close()


def test_committed_write_refreshes_own_marker(tmp_path: Path, monkeypatch):
    # Every committed write refreshes this process's marker last_seen_at (the
    # freshness signal for other-host peers and unparseable tokens): a process
    # that is actively writing is a live peer.
    import app.persistence as persistence

    db_path = tmp_path / "jobs.db"
    store = SQLiteJobStore(db_path)
    token = f"{socket.gethostname()}:{os.getpid()}"
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 1, 2, 12, 0, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(persistence, "_now", lambda: t0.isoformat())
    record = store.create(_make_record())
    monkeypatch.setattr(persistence, "_now", lambda: t1.isoformat())
    store.update(record.id, status=JobStatus.running)

    conn = sqlite3.connect(db_path, timeout=5)
    try:
        row = conn.execute("SELECT last_seen_at FROM processes WHERE token = ?", (token,)).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == t1.isoformat()
    store.close()


def test_corrupt_db_fails_at_startup_not_first_request(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "jobs.db"
    db_path.write_bytes(b"this is not a sqlite database file")
    monkeypatch.setenv("HSJS_DB_PATH", str(db_path))
    get_settings.cache_clear()
    close_job_store()
    try:
        # A corrupt store must fail the app at startup (lifespan) rather than
        # surfacing as an unmodeled 500 on the first job request.
        with pytest.raises(RuntimeError, match="Cannot open SQLite job store"):
            with TestClient(app):
                pass
    finally:
        close_job_store()
        get_settings.cache_clear()


def test_startup_failure_after_store_open_closes_leaked_store(tmp_path: Path, monkeypatch):
    # If startup fails after the store is open (e.g. recovery raises), the open
    # store must not leak into the global: the next in-process startup would
    # otherwise reuse the leaked instance with the failed startup's DB path
    # instead of rebuilding from current settings.
    import app.dependencies as dependencies
    import app.main as main_module

    db_path = tmp_path / "jobs.db"
    monkeypatch.setenv("HSJS_DB_PATH", str(db_path))
    get_settings.cache_clear()
    close_job_store()
    state = {"fail": True}

    def flaky_recovery(store):
        if state["fail"]:
            raise RuntimeError("recovery exploded")
        return 0

    monkeypatch.setattr(main_module, "recover_interrupted_jobs", flaky_recovery)
    try:
        with pytest.raises(RuntimeError, match="recovery exploded"):
            with TestClient(app):
                pass
        assert dependencies._job_store is None

        # A subsequent startup rebuilds from current settings, not the leaked
        # instance with the failed startup's DB path.
        state["fail"] = False
        second_db = tmp_path / "second.db"
        monkeypatch.setenv("HSJS_DB_PATH", str(second_db))
        get_settings.cache_clear()
        with TestClient(app):
            store = get_job_store()
            assert isinstance(store, SQLiteJobStore)
            assert store._db_path == second_db
    finally:
        close_job_store()
        get_settings.cache_clear()


def test_startup_recovery_store_failure_fails_boot(tmp_path: Path, monkeypatch):
    # A transient store failure during startup recovery - a write lock held
    # past the busy timeout while recovery updates a non-terminal job, with
    # the open itself having succeeded - fails the whole boot with no retry
    # (documented fail-fast, only reachable in misconfigured shared-DB
    # topologies), and the open store must not leak into the global.
    import app.dependencies as dependencies

    db_path = tmp_path / "jobs.db"
    seed = SQLiteJobStore(db_path)
    seed.create(_make_record())
    seed.close()
    monkeypatch.setenv("HSJS_DB_PATH", str(db_path))
    get_settings.cache_clear()
    close_job_store()

    state: dict[str, sqlite3.Connection] = {}

    def building_store(settings):
        store = SQLiteJobStore(settings.db_path, busy_timeout_ms=200)
        # The open (including its write probe) succeeded; only now does a
        # foreign connection take the write lock, so the failure lands in
        # recovery's update, not in the open. check_same_thread=False: the
        # store (and this blocker) are built in the TestClient's portal thread
        # but the blocker is released from the test's main thread.
        blocker = sqlite3.connect(str(settings.db_path), check_same_thread=False)
        blocker.execute("PRAGMA busy_timeout = 0")
        blocker.execute("BEGIN IMMEDIATE")
        state["blocker"] = blocker
        return store

    monkeypatch.setattr(dependencies, "_build_job_store", building_store)
    try:
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            with TestClient(app):
                pass
        assert dependencies._job_store is None
    finally:
        blocker = state.get("blocker")
        if blocker is not None:
            blocker.close()
        close_job_store()
        get_settings.cache_clear()


def test_pipeline_and_jobs_routes_persist_to_default_sqlite_store(client):
    # Assert against the resolved setting (the file the app actually uses), not
    # the env var: coupling to conftest's import-time env would KeyError if that
    # setup ever moves, and the env names a file, not the resolved path.
    db_path = get_settings().db_path
    response = client.post(
        "/api/v1/pipeline",
        data={"source_text": "Hvor er toget?", "include_audio": "false"},
    )
    assert response.status_code == 200
    job_id = response.json()["id"]

    deadline = time.monotonic() + 15
    status = None
    while time.monotonic() < deadline:
        job = client.get(f"/api/v1/jobs/{job_id}")
        assert job.status_code == 200
        status = job.json()["status"]
        if status in ("completed", "failed"):
            break
        time.sleep(0.1)
    assert status == "completed"

    listed = client.get("/api/v1/jobs").json()
    assert job_id in [record["id"] for record in listed]

    conn = sqlite3.connect(db_path, timeout=5)
    try:
        row = conn.execute("SELECT status, result FROM jobs WHERE id = ?", (job_id,)).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == "completed"
    assert row[1] is not None


def test_pipeline_create_failure_maps_to_declared_503(client, monkeypatch):
    # A store failure in the route's own create() (closed store during a
    # shutdown race, locked or full DB) must surface as a declared error, not an
    # unmodeled 500: the route models only 400/503.
    import app.api.v1.routes.pipeline as pipeline_module

    closed_store = InMemoryJobStore()
    closed_store.close()
    monkeypatch.setattr(pipeline_module, "get_job_store", lambda: closed_store)

    response = client.post(
        "/api/v1/pipeline",
        data={"source_text": "Hvor er toget?", "include_audio": "false"},
    )
    assert response.status_code == 503


def test_pipeline_store_open_failure_maps_to_declared_503(client, monkeypatch):
    # get_job_store() sits inside the 503 guard: a failing open on the lazy build
    # path must map to the declared 503, not an unmodeled 500 (the route models
    # only 200/400/503).
    import app.api.v1.routes.pipeline as pipeline_module

    def failing_get_job_store():
        raise RuntimeError("Cannot open SQLite job store")

    monkeypatch.setattr(pipeline_module, "get_job_store", failing_get_job_store)

    response = client.post(
        "/api/v1/pipeline",
        data={"source_text": "Hvor er toget?", "include_audio": "false"},
    )
    assert response.status_code == 503


def test_pipeline_create_db_write_failure_maps_to_declared_503(client, monkeypatch):
    # A disk-full / I/O failure in store.create() (an OSError from the write commit)
    # must map to the declared 503, not an unmodeled 500: the route models only
    # 200/400/503/422.
    import app.api.v1.routes.pipeline as pipeline_module

    store = pipeline_module.get_job_store()

    def failing_create(record):
        raise OSError("No space left on device")

    monkeypatch.setattr(store, "create", failing_create)

    response = client.post(
        "/api/v1/pipeline",
        data={"source_text": "Hvor er toget?", "include_audio": "false"},
    )
    assert response.status_code == 503


def test_pipeline_audio_read_failure_maps_to_503_and_no_orphaned_job(client, monkeypatch):
    # An unreadable upload must map to the declared 503 (not an unmodeled 500;
    # the route models only 200/400/503/422) and must not leave an orphaned
    # queued job (persisted but never scheduled, stuck until a restart relabels
    # it): the upload is read BEFORE the job is persisted.
    import io

    from fastapi import BackgroundTasks, HTTPException, UploadFile

    import app.api.v1.routes.pipeline as pipeline_module

    store = pipeline_module.get_job_store()
    created: list = []
    real_create = store.create

    def tracking_create(record):
        created.append(record)
        return real_create(record)

    monkeypatch.setattr(store, "create", tracking_create)

    audio = UploadFile(file=io.BytesIO(b"audio-bytes"))

    def failing_read(*args, **kwargs):
        raise OSError("simulated I/O error reading the upload")

    monkeypatch.setattr(audio.file, "read", failing_read)

    # The route's Form/File defaults are FastAPI markers, so call it with every
    # parameter explicit (a direct call bypasses dependency resolution).
    with pytest.raises(HTTPException) as excinfo:
        pipeline_module.create_pipeline_job(
            background_tasks=BackgroundTasks(),
            target_variant=VariantCode.sme,
            target_voice=None,
            source_text=None,
            include_phonemes=True,
            include_audio=True,
            audio=audio,
        )

    assert excinfo.value.status_code == 503
    assert created == []  # no orphaned job was persisted


def test_read_routes_map_store_failure_to_declared_503(client, monkeypatch):
    # The read routes must map a store failure (failing open, a locked DB past
    # the busy timeout, or a failed reopen) to a declared 503, not an unmodeled
    # 500 (they model only 200/404, now plus 503).
    import app.api.v1.routes.jobs as jobs_module

    def failing_get_job_store():
        raise RuntimeError("Cannot open SQLite job store")

    monkeypatch.setattr(jobs_module, "get_job_store", failing_get_job_store)

    list_response = client.get("/api/v1/jobs")
    assert list_response.status_code == 503

    get_response = client.get("/api/v1/jobs/does-not-exist")
    assert get_response.status_code == 503


def test_read_routes_map_open_store_read_failure_to_declared_503(client, monkeypatch):
    # The 503 mapping must hold for an already-open store whose read fails
    # (a locked DB past the busy timeout, which the route comment claims), not
    # only for a failing open: the store resolves first, then list()/get()
    # raises.
    import app.api.v1.routes.jobs as jobs_module

    store = jobs_module.get_job_store()

    def failing_list():
        raise sqlite3.OperationalError("database is locked")

    def failing_get(job_id):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "list", failing_list)
    list_response = client.get("/api/v1/jobs")
    assert list_response.status_code == 503

    monkeypatch.setattr(store, "get", failing_get)
    get_response = client.get("/api/v1/jobs/does-not-exist")
    assert get_response.status_code == 503


def test_get_job_store_caches_instance():
    close_job_store()
    store = get_job_store()
    assert isinstance(store, SQLiteJobStore)
    assert get_job_store() is store
    close_job_store()


def test_close_job_store_releases_global_lock_during_close():
    # close_job_store must not hold the global store lock while closing: a slow
    # close (a beat or write holding the store lock across a conn.execute for
    # the full busy timeout) would otherwise stall shutdown and every concurrent
    # get_job_store() for the full duration. The swap happens under the lock;
    # the close happens outside it, so a concurrent get_job_store() in the close
    # window builds a new store instead of blocking (safe: the per-path refcount
    # keeps the liveness marker correct for sibling instances). The threads are
    # parked with explicit events (no sleeps: wall-clock sleeps flake under
    # preemption).
    close_job_store()
    store = get_job_store()
    assert isinstance(store, SQLiteJobStore)
    real_close = store.close
    close_started = Event()
    release_close = Event()

    def slow_close():
        close_started.set()
        assert release_close.wait(timeout=10)
        real_close()

    store.close = slow_close

    def closer():
        close_job_store()

    closer_thread = Thread(target=closer)
    try:
        closer_thread.start()
        assert close_started.wait(timeout=10)  # the close is in progress
        # While the old store is still closing, a concurrent get must not block
        # on the global lock: it builds a new store.
        rebuilt = get_job_store()
        assert rebuilt is not store
        release_close.set()
        closer_thread.join(timeout=10)
        assert not closer_thread.is_alive()
        # The old store is closed; the new store is the global.
        assert get_job_store() is rebuilt
    finally:
        release_close.set()
        closer_thread.join(timeout=10)
        real_close()  # idempotent: ensures the old store is closed on failure
        close_job_store()


def test_env_wiring_selects_memory_backend(monkeypatch):
    close_job_store()
    get_settings.cache_clear()
    monkeypatch.setenv("HSJS_JOB_STORE_BACKEND", "memory")
    try:
        store = get_job_store()
        assert isinstance(store, InMemoryJobStore)
        assert get_job_store() is store  # cached while the store is open
        record = store.create(_make_record())
        assert store.get(record.id) is not None
        close_job_store()
        # The memory backend is non-durable: closing tears the store down, so a
        # rebuild yields a fresh, empty store rather than leaking shared state.
        rebuilt = get_job_store()
        assert isinstance(rebuilt, InMemoryJobStore)
        assert rebuilt is not store
        assert rebuilt.get(record.id) is None
    finally:
        close_job_store()
        get_settings.cache_clear()


def test_memory_backend_serves_requests_without_db_file(tmp_path: Path, monkeypatch):
    # Drive a real request through the app with the memory backend: backend
    # selection is otherwise only verified via unit calls, so a wiring bug at
    # the app/lifespan layer that ignored the setting would pass. The job must
    # survive from the write to the read through the app, and no DB file may
    # be created.
    db_path = tmp_path / "jobs.db"
    monkeypatch.setenv("HSJS_DB_PATH", str(db_path))
    monkeypatch.setenv("HSJS_JOB_STORE_BACKEND", "memory")
    get_settings.cache_clear()
    close_job_store()
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/pipeline",
                data={"source_text": "Hvor er toget?", "include_audio": "false"},
            )
            assert response.status_code == 200
            job_id = response.json()["id"]

            deadline = time.monotonic() + 15
            status = None
            while time.monotonic() < deadline:
                job = client.get(f"/api/v1/jobs/{job_id}")
                assert job.status_code == 200
                status = job.json()["status"]
                if status in ("completed", "failed"):
                    break
                time.sleep(0.1)
            assert status == "completed"
    finally:
        close_job_store()
        get_settings.cache_clear()
    assert not db_path.exists()


def test_importing_app_has_no_db_side_effect(tmp_path: Path):
    api_root = Path(__file__).resolve().parents[1]
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "PYTHONPATH": str(api_root),
        "HSJS_PROVIDER_STUB_MODE": "true",
        "HSJS_DB_PATH": str(tmp_path / "jobs.db"),
    }
    proc = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"import failed: {proc.stderr}"
    assert not (tmp_path / "jobs.db").exists()
    assert not (tmp_path / "data").exists()


def test_db_path_resolves_against_cwd_not_repo_root(tmp_path: Path):
    # Unlike artifacts_dir (repo-anchored), db_path is CWD-relative by design
    # (documented in the README): a server started from a different CWD
    # persists to a different file. Pin the semantics so a "helpful"
    # repo-anchoring change is caught.
    api_root = Path(__file__).resolve().parents[1]
    code = (
        "from app.dependencies import _build_job_store, get_settings; "
        "from app.domain import JobRecord, PipelineRequest, VariantCode; "
        "store = _build_job_store(get_settings()); "
        "store.create(JobRecord(request=PipelineRequest(target_variant=VariantCode.sme, source_text='x'))); "
        "store.close(); "
        "import os; print(os.path.exists('data/jobs.db'))"
    )
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "PYTHONPATH": str(api_root),
        "HSJS_PROVIDER_STUB_MODE": "true",
    }
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"subprocess failed: {proc.stderr}"
    assert proc.stdout.strip() == "True"
    assert (tmp_path / "data" / "jobs.db").exists()


def test_build_job_store_selects_memory_backend():
    settings = Settings(job_store_backend="memory")
    assert isinstance(_build_job_store(settings), InMemoryJobStore)


def test_build_job_store_selects_sqlite_backend(tmp_path: Path):
    settings = Settings(job_store_backend="sqlite", db_path=tmp_path / "jobs.db")
    store = _build_job_store(settings)
    assert isinstance(store, SQLiteJobStore)
    store.close()


def test_settings_reject_unknown_job_store_backend():
    with pytest.raises(ValidationError):
        Settings(job_store_backend="postgres")


def test_settings_normalizes_job_store_backend_case():
    assert Settings(job_store_backend="Memory").job_store_backend == "memory"
    assert Settings(job_store_backend=" SQLITE ").job_store_backend == "sqlite"


def test_empty_job_store_backend_env_falls_back_to_default(monkeypatch):
    # A set-but-empty env var (a common .env slip) must fall back to the
    # sqlite default instead of failing startup with a ValidationError.
    monkeypatch.setenv("HSJS_JOB_STORE_BACKEND", "")
    settings = Settings()
    assert settings.job_store_backend == "sqlite"


def test_empty_db_path_env_falls_back_to_default(monkeypatch):
    # A set-but-empty HSJS_DB_PATH (a common .env slip) must fall back to the
    # default instead of parsing to Path('.') and failing startup with a
    # RuntimeError, mirroring the job_store_backend handling.
    monkeypatch.setenv("HSJS_DB_PATH", "")
    settings = Settings()
    assert settings.db_path == Path("data/jobs.db")


def test_recovery_grace_s_env_wiring_and_default(tmp_path: Path, monkeypatch):
    # The HSJS_RECOVERY_GRACE_S env -> settings.recovery_grace_s wiring and the
    # 300s default are otherwise unpinned (both grace tests pass grace_seconds
    # explicitly), so a rename/default regression would pass a green suite. Run
    # in a clean CWD: Settings reads env_file=".env" (CWD-relative), so a
    # developer's apps/api/.env (the documented workflow) would otherwise leak
    # HSJS_RECOVERY_GRACE_S into the default and fail the test.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HSJS_RECOVERY_GRACE_S", "42")
    assert Settings().recovery_grace_s == 42
    monkeypatch.delenv("HSJS_RECOVERY_GRACE_S", raising=False)
    assert Settings().recovery_grace_s == 300


def test_recovery_grace_s_rejects_negative(monkeypatch):
    # A negative grace would put the recovery cutoff in the future, inverting
    # the cross-host peer guard (no peer's marker could ever be fresh enough),
    # so a live cross-host peer's in-flight jobs would be failed on every boot:
    # the value must be rejected at settings load, not silently inverted.
    monkeypatch.setenv("HSJS_RECOVERY_GRACE_S", "-1")
    with pytest.raises(ValidationError):
        Settings()
    # Zero is a valid (if aggressive) configuration: no grace window.
    monkeypatch.setenv("HSJS_RECOVERY_GRACE_S", "0")
    assert Settings().recovery_grace_s == 0


def test_recovery_grace_s_rejects_overflowing_values(monkeypatch):
    # An unbounded grace would make timedelta(seconds=grace) in startup recovery
    # raise OverflowError and fail boot: the value must be capped at settings load,
    # not trusted to the datetime math.
    monkeypatch.setenv("HSJS_RECOVERY_GRACE_S", str(10**20))
    with pytest.raises(ValidationError):
        Settings()
    # The cap itself (7 days, mirroring the marker pruning horizon) stays valid.
    monkeypatch.setenv("HSJS_RECOVERY_GRACE_S", "604800")
    assert Settings().recovery_grace_s == 604800


def test_recovery_grace_s_env_controls_recovery_window(tmp_path: Path, monkeypatch):
    # recover_interrupted_jobs reads get_settings().recovery_grace_s when
    # grace_seconds is None; pin that the env var actually reaches the recovery
    # window (a peer that wrote 10s ago is live under the 300s default but gone
    # under a 1s window).
    db_path = tmp_path / "jobs.db"
    store = SQLiteJobStore(db_path)
    job = store.create(_make_record())
    recent = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    _insert_peer_marker(db_path, "peer-host:99999", recent)

    monkeypatch.setenv("HSJS_RECOVERY_GRACE_S", "1")
    get_settings.cache_clear()
    try:
        recovered = recover_interrupted_jobs(store)
    finally:
        get_settings.cache_clear()
        store.close()

    assert recovered == 1
    fetched = store.get(job.id)
    assert fetched is not None
    assert fetched.status == JobStatus.failed


def test_settings_defaults_to_sqlite_backend_and_db_path(tmp_path: Path, monkeypatch):
    # conftest force-sets both env vars session-wide; remove them to pin the
    # production defaults that no other test exercises (a default regression
    # would otherwise leave the whole suite green). Run in a clean CWD: Settings
    # reads env_file=".env" (CWD-relative), so a developer's apps/api/.env (the
    # documented workflow) would otherwise leak HSJS_DB_PATH into the defaults
    # and fail the test.
    monkeypatch.delenv("HSJS_JOB_STORE_BACKEND", raising=False)
    monkeypatch.delenv("HSJS_DB_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    settings = Settings()
    assert settings.job_store_backend == "sqlite"
    assert settings.db_path == Path("data/jobs.db")


def test_invalid_job_store_backend_env_fails_at_startup(monkeypatch):
    close_job_store()
    get_settings.cache_clear()
    monkeypatch.setenv("HSJS_JOB_STORE_BACKEND", "postgres")
    try:
        # An invalid HSJS_JOB_STORE_BACKEND must fail the app at startup
        # (lifespan) rather than surfacing later as an unmodeled error.
        with pytest.raises(ValidationError):
            with TestClient(app):
                pass
    finally:
        close_job_store()
        get_settings.cache_clear()


_UVICORN_RUNNING_RE = re.compile(r"Uvicorn running on http://127\.0\.0\.1:(\d+)")


def _log_tail(log_path: Path, limit: int = 4000) -> str:
    return log_path.read_text(encoding="utf-8", errors="replace")[-limit:] if log_path.exists() else "<no server log>"


def _start_api(db_path: Path, log_path: Path) -> tuple[subprocess.Popen, IO[bytes]]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "HSJS_PROVIDER_STUB_MODE": "true",
        "HSJS_DB_PATH": str(db_path),
        "HSJS_JOB_STORE_BACKEND": "sqlite",
    }
    api_root = Path(__file__).resolve().parents[1]
    log_file = log_path.open("wb")
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "0", "--log-level", "info"],
        cwd=str(api_root),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    return process, log_file


def _wait_for_api(process: subprocess.Popen, log_path: Path, timeout_s: float = 30.0) -> str:
    deadline = time.monotonic() + timeout_s
    base_url = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"API process exited early (code={process.returncode})\n--- server log ---\n{_log_tail(log_path)}")
        if base_url is None:
            match = _UVICORN_RUNNING_RE.search(_log_tail(log_path, limit=65536))
            if match is not None:
                base_url = f"http://127.0.0.1:{match.group(1)}"
        else:
            try:
                if httpx.get(f"{base_url}/", timeout=2.0).status_code == 200:
                    return base_url
            except httpx.HTTPError:
                pass
        time.sleep(0.2)
    raise TimeoutError(f"API did not become ready\n--- server log ---\n{_log_tail(log_path)}")


def _wait_for_job_completion(process: subprocess.Popen, log_path: Path, base_url: str, job_id: str, timeout_s: float = 30.0) -> str:
    deadline = time.monotonic() + timeout_s
    final_status = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"API process exited early (code={process.returncode})\n--- server log ---\n{_log_tail(log_path)}")
        try:
            job = httpx.get(f"{base_url}/api/v1/jobs/{job_id}", timeout=10.0).json()
            final_status = job["status"]
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            # Transient read error (connection reset, non-JSON body, missing field):
            # retry until the deadline instead of surfacing it and masking the
            # original failure. The server log is still surfaced on timeout.
            final_status = f"<transient: {type(exc).__name__}>"
            time.sleep(0.2)
            continue
        if final_status in ("completed", "failed"):
            return final_status
        time.sleep(0.2)
    raise TimeoutError(f"job {job_id} did not reach a final status (last={final_status})\n--- server log ---\n{_log_tail(log_path)}")


def _stop_api(process: subprocess.Popen, log_file: IO[bytes]) -> None:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)
    log_file.close()


def test_job_survives_api_restart(tmp_path: Path):
    db_path = tmp_path / "jobs.db"

    first_log = tmp_path / "api-first.log"
    first, first_log_file = _start_api(db_path, first_log)
    try:
        base_url = _wait_for_api(first, first_log)
        response = httpx.post(
            f"{base_url}/api/v1/pipeline",
            data={"source_text": "Hvor er toget?", "include_audio": "false"},
            timeout=10.0,
        )
        assert response.status_code == 200
        job_id = response.json()["id"]
        assert _wait_for_job_completion(first, first_log, base_url, job_id) == "completed"
    finally:
        _stop_api(first, first_log_file)

    second_log = tmp_path / "api-second.log"
    second, second_log_file = _start_api(db_path, second_log)
    try:
        base_url = _wait_for_api(second, second_log)
        job = httpx.get(f"{base_url}/api/v1/jobs/{job_id}", timeout=10.0)
        assert job.status_code == 200
        body = job.json()
        assert body["id"] == job_id
        assert body["status"] == "completed"
        assert body["request"]["source_text"] == "Hvor er toget?"
        assert body["result"]["translated_text"] is not None

        listed = httpx.get(f"{base_url}/api/v1/jobs", timeout=10.0).json()
        assert [record["id"] for record in listed] == [job_id]
    finally:
        _stop_api(second, second_log_file)


_KILLED_JOB_SCRIPT = """
import sys
import time

from app.domain import JobRecord, JobStatus, PipelineRequest, VariantCode
from app.persistence import SQLiteJobStore

store = SQLiteJobStore(sys.argv[1])
record = store.create(JobRecord(request=PipelineRequest(target_variant=VariantCode.sme, source_text="Hvor er toget?", include_audio=False)))
store.update(record.id, status=JobStatus.running)
print(record.id, flush=True)
time.sleep(300)
"""


def test_unclean_crash_recovery_fails_interrupted_job(tmp_path: Path):
    # The headline scenario end to end: a real process is SIGKILLed mid-job
    # (no close(), its liveness marker left fresh behind). The next startup's
    # recovery must fail the interrupted job: the dead predecessor's own
    # marker must not protect it (same-host PID check, plus the recorded start
    # time against a PID reuse).
    api_root = Path(__file__).resolve().parents[1]
    db_path = tmp_path / "jobs.db"
    proc = subprocess.Popen(
        [sys.executable, "-c", _KILLED_JOB_SCRIPT, str(db_path)],
        cwd=str(api_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout = proc.stdout
    stderr = proc.stderr
    assert stdout is not None and stderr is not None
    job_id = ""
    try:
        job_id = stdout.readline().strip()
        assert job_id, f"crash script produced no job id: {stderr.read()[:2000]}"
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)
        assert proc.returncode == -signal.SIGKILL
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
        stdout.close()
        stderr.close()

    store = SQLiteJobStore(db_path)
    try:
        recovered = recover_interrupted_jobs(store, grace_seconds=300)
    finally:
        store.close()

    assert recovered == 1
    fetched = store.get(job_id)
    assert fetched is not None
    assert fetched.status == JobStatus.failed
    assert "interrupted" in (fetched.error or "")
