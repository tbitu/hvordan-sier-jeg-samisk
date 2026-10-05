from __future__ import annotations

import logging
import os
import socket
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import cast

from app.domain import JobRecord, JobStatus, PipelineRequest, PipelineResult
from app.state import StoreClosedError, UpdateResult, _UNSET

logger = logging.getLogger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    request TEXT NOT NULL,
    result TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


# Per-process liveness markers: startup recovery must not fail jobs that another
# live API process sharing this DB is still working on (the compose deployment
# mounts one shared volume, so `--scale api=2` would otherwise let the later
# booter's recovery kill the first replica's in-flight jobs).
#
# start_time is the process's start time (clock ticks since boot, from /proc):
# a PID alone cannot identify a process because the kernel recycles PIDs, so
# without it an unrelated process that reuses a dead predecessor's PID would
# inherit the predecessor's marker - and with it the protection of the
# predecessor's interrupted jobs - until the marker is pruned.
_PROCESSES_SCHEMA = """
CREATE TABLE IF NOT EXISTS processes (
    token TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    start_time INTEGER
)
"""

# Markers older than this are pruned on open so the table cannot grow without
# bound from crashed processes.
_MARKER_STALE_AFTER = timedelta(days=7)


# Per-process registry of open DB paths, counted: the liveness marker is
# process-wide (one token per OS process, shared by every store instance in
# the process), so close() may only delete it when the last store instance in
# this process that uses the same DB goes away: deleting it while a sibling
# instance is still open would make the survivor invisible to a co-booting
# peer's recovery (its writes refresh the marker in place and never re-insert
# a deleted row). A count (not a set) is required because several instances
# may share one path.
_OPEN_DB_PATHS: dict[Path, int] = {}
_OPEN_DB_PATHS_LOCK = Lock()


_proc_available_cache: bool | None = None


def _proc_available() -> bool:
    """True when /proc is usable (Linux); cached for the process lifetime.

    Distinguishes "/proc does not exist" (non-Linux) from "the process is
    gone" (a missing /proc/<pid>/stat on a /proc-capable host): the two cases
    must lead to different liveness conclusions, so the host capability is
    determined once and separately from any per-PID read.
    """
    global _proc_available_cache
    if _proc_available_cache is None:
        try:
            Path("/proc/self/stat").read_text()
            _proc_available_cache = True
        except OSError:
            _proc_available_cache = False
    return _proc_available_cache


def _read_proc_stat(pid: int) -> tuple[str, int] | None:
    """Read /proc/<pid>/stat, returning (state, start_time) or None if the
    process is gone. Only call when _proc_available() is True (Linux).

    The state letter lets the caller tell a zombie (defunct, not reaped: it
    passes a signal probe and keeps its start time, but cannot do work) from a
    live process, and a None return distinguishes a just-reaped process from a
    parse failure on a /proc-capable host.
    """
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    # Field 2 (comm) is parenthesized and may contain spaces or parentheses,
    # so split after the last ')'. after_comm[0] is field 3 (state);
    # after_comm[19] is field 22 (starttime).
    after_comm = stat.rsplit(")", 1)[-1].split()
    try:
        return after_comm[0], int(after_comm[19])
    except (IndexError, ValueError):
        return None


def _process_start_time(pid: int) -> int | None:
    """The process's start time (clock ticks since boot) from /proc, or None.

    The start time is stable for the life of the process and differs between
    any two processes that ever held the same PID, which is what makes it the
    PID-reuse discriminator. None when /proc is unavailable (non-Linux) or the
    process is gone: callers fall back to the weaker signal in that case.
    """
    if not _proc_available():
        return None
    info = _read_proc_stat(pid)
    if info is None:
        return None
    return info[1]


_CREATE_UPSERT_SQL = (
    "INSERT INTO jobs (id, status, request, result, error, created_at, updated_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?) "
    "ON CONFLICT(id) DO UPDATE SET "
    "status = excluded.status, request = excluded.request, result = excluded.result, "
    "error = excluded.error, updated_at = excluded.updated_at"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class RecoverySnapshot:
    """A consistent view of the jobs table and peer markers for startup recovery.

    Every field derives from the same read transaction, so the protection set,
    the parsed records and the raw non-terminal ids cannot disagree about which
    jobs exist: a concurrent cross-process commit is either visible to all of
    them (a live peer's job is then in the protection set) or to none (the
    recovery pass cannot see the job and cannot fail it).
    """

    records: tuple[JobRecord, ...]
    non_terminal_ids: frozenset[str]
    protected: frozenset[str]


class SQLiteJobStore:
    def __init__(
        self,
        db_path: str | Path = "data/jobs.db",
        *,
        busy_timeout_ms: int = 5000,
        marker_heartbeat_s: float | None = 30.0,
    ) -> None:
        self._db_path = Path(db_path)
        self._db_key = self._db_path.resolve()
        self._lock = Lock()
        self._conn: sqlite3.Connection | None = None
        # Cached connection for post-close reads: the supported post-close read
        # contract must not open a fresh connection per read (each would be
        # released only by GC, leaking file handles under repeated reads).
        # close() releases it explicitly. Accessed only under _lock (every read
        # path and close hold it).
        self._read_only_conn: sqlite3.Connection | None = None
        # True while the current connection is one that registered this
        # instance in _OPEN_DB_PATHS (the initial open). A post-close read-only
        # reopen does not register, so close() must not decrement the refcount
        # for it: a close->read->close sequence would otherwise unbalance the
        # count and delete the process-wide liveness marker while a sibling
        # instance on the same DB is still open.
        self._conn_registered = False
        self._closed = False
        self._busy_timeout_ms = busy_timeout_ms
        # One token per OS process (hostname disambiguates containers that share
        # the volume but have separate PID namespaces): every store instance in
        # this process shares it, so sibling instances never count as peers.
        self._token = f"{socket.gethostname()}:{os.getpid()}"
        self._start_time = _process_start_time(os.getpid())
        self._heartbeat_interval_s = marker_heartbeat_s
        self._heartbeat_stop: threading.Event | None = None
        self._heartbeat_thread: threading.Thread | None = None
        with self._lock:
            self._open_connection()

    def _open_connection(self, register_marker: bool = True) -> None:
        timestamp = _now()
        conn: sqlite3.Connection | None = None
        counted = False
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute(f"PRAGMA busy_timeout = {int(self._busy_timeout_ms)}")
            conn.execute(_SCHEMA)
            conn.execute(_PROCESSES_SCHEMA)
            # Migrate DBs created before the start_time column existed: the
            # table is only created with the column when it does not exist yet.
            # The check and the ALTER are not atomic across connections (DDL
            # autocommits in legacy mode), so two concurrent first opens of a
            # legacy DB can both read the column absent before either ALTER
            # commits: the second ALTER then fails with "duplicate column
            # name", which is the success case (the first opener already added
            # the column), not an error.
            existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(processes)")}
            if "start_time" not in existing_columns:
                try:
                    conn.execute("ALTER TABLE processes ADD COLUMN start_time INTEGER")
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc):
                        raise
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            # Run the exact statement create() uses, twice: the first execution
            # validates the statement against the existing schema (including the
            # id uniqueness constraint the ON CONFLICT clause requires) and the
            # second exercises the conflict path. A pre-existing jobs table with
            # the right columns but no unique id therefore fails at startup
            # instead of passing the probe and 500ing on every create().
            probe_params = ("__open_probe__", "queued", "{}", None, None, timestamp, timestamp)
            conn.execute(_CREATE_UPSERT_SQL, probe_params)
            conn.execute(_CREATE_UPSERT_SQL, probe_params)
            conn.execute("ROLLBACK")
            if register_marker:
                # Count this instance as open BEFORE committing the marker
                # registration: close() computes last_instance and deletes the
                # marker under this same registry lock, so the increment must be
                # ordered against the delete. A close that ran between the
                # registration commit and the increment would delete the marker
                # this open just committed, and the survivor's writes and
                # heartbeat refresh the marker in place (UPDATE-only, never
                # re-inserting a deleted row), leaving the process invisible to a
                # co-booting peer's recovery. A failed open rolls the count back
                # (below) so a failed instance never keeps the marker alive.
                with _OPEN_DB_PATHS_LOCK:
                    _OPEN_DB_PATHS[self._db_key] = _OPEN_DB_PATHS.get(self._db_key, 0) + 1
                counted = True
                # Register this process's liveness marker (refreshed on every
                # write and by the heartbeat) and prune markers left by
                # long-gone processes. Skipped on a post-close read-only
                # reopen: a closed store is not a live peer, and re-registering
                # here would undo the deletion close() performs, making the next
                # startup within the grace window skip recovery.
                # On a token conflict refresh start_time AND started_at only when
                # the recorded start time differs: then the token is hostname:pid
                # and a (dead, reaped) predecessor owned this PID and a new process
                # has reused it. Keeping the predecessor's start_time would make a
                # co-booting peer's start-time check mismatch the live process and
                # misclassify it dead, failing its in-flight jobs; keeping the
                # predecessor's started_at would let the successor protect jobs it
                # never touched (started "before" their last update). When the
                # recorded start time matches, the conflict is a sibling store open
                # by THIS process (the token is shared process-wide): advancing
                # started_at to the later open would push it past this process's
                # in-flight jobs' updated_at, and a peer _protected_job_ids then
                # treats those jobs as unprotected and fails them. Keeping the
                # first open's earlier started_at preserves that protection.
                conn.execute(
                    "INSERT INTO processes (token, started_at, last_seen_at, start_time) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(token) DO UPDATE SET "
                    "started_at = CASE WHEN processes.start_time IS NOT NULL AND processes.start_time = excluded.start_time "
                    "THEN processes.started_at ELSE excluded.started_at END, "
                    "last_seen_at = excluded.last_seen_at, start_time = excluded.start_time",
                    (self._token, timestamp, timestamp, self._start_time),
                )
                conn.execute(
                    "DELETE FROM processes WHERE last_seen_at < ?",
                    ((datetime.now(timezone.utc) - _MARKER_STALE_AFTER).isoformat(),),
                )
            conn.commit()
        except (OSError, sqlite3.Error) as exc:
            if counted:
                # Undo the count increment: a failed open must not keep the
                # per-path refcount (and with it the liveness marker) alive.
                with _OPEN_DB_PATHS_LOCK:
                    count = _OPEN_DB_PATHS.get(self._db_key, 0) - 1
                    if count <= 0:
                        _OPEN_DB_PATHS.pop(self._db_key, None)
                    else:
                        _OPEN_DB_PATHS[self._db_key] = count
            if conn is not None:
                # Close the abandoned connection: a failed probe after
                # BEGIN IMMEDIATE would otherwise leave it holding SQLite's
                # RESERVED lock until the exception's traceback chain is GC'd,
                # blocking a second opener with "database is locked".
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                conn.close()
            raise RuntimeError(f"Cannot open SQLite job store at {self._db_path}: {exc}") from exc
        self._conn = conn
        self._conn_registered = register_marker
        if register_marker:
            self._start_heartbeat()

    def _start_heartbeat(self) -> None:
        """Refresh our liveness marker periodically while the store is open.

        Marker freshness is the only liveness signal a peer on another host
        (separate PID namespace) can use, and writes alone do not keep it
        honest: a live peer that is write-idle beyond the recovery grace
        window (a long pipeline run) would otherwise be treated as dead and
        its in-flight jobs failed by a co-booting peer's startup recovery. The
        heartbeat keeps the marker fresh for as long as the process is alive,
        independent of write activity. A dead process stops heartbeating, so
        the freshness window stays bounded (last beat + grace).
        """
        if self._heartbeat_interval_s is None or self._heartbeat_interval_s <= 0:
            return
        stop = threading.Event()
        self._heartbeat_stop = stop

        def beat() -> None:
            while not stop.wait(self._heartbeat_interval_s):
                if self._closed:
                    return
                try:
                    with self._lock:
                        conn = self._conn
                        if conn is None or self._closed:
                            return
                        try:
                            conn.execute(
                                "UPDATE processes SET last_seen_at = ? WHERE token = ?",
                                (_now(), self._token),
                            )
                            conn.commit()
                        except (sqlite3.Error, OSError):
                            # A failed commit otherwise leaves the implicit
                            # transaction open, holding SQLite's write lock on the
                            # shared connection until the next successful beat
                            # (up to the heartbeat interval): roll it back so the
                            # lock is released at once (mirrors _execute_write).
                            # The rollback must run while holding the store lock:
                            # if it ran after releasing the lock, a concurrent
                            # recovery_snapshot could BEGIN on the same
                            # connection while this failed beat's transaction is
                            # still open (cannot start a transaction within a
                            # transaction), a latent unmodeled boot failure. A
                            # transient failure (locked DB) just skips this beat;
                            # the next beat retries. A persistent one (deleted
                            # file) is harmless: the store is unusable anyway.
                            try:
                                conn.rollback()
                            except sqlite3.Error:
                                pass
                except (sqlite3.Error, OSError):
                    continue

        thread = threading.Thread(target=beat, daemon=True)
        self._heartbeat_thread = thread
        thread.start()

    def _connection(self) -> sqlite3.Connection:
        conn = self._conn
        if conn is None:
            if self._closed:
                # Reads after close() remain a supported contract, but the
                # reopen is read-only in effect (writes stay fenced by
                # _write_connection). Do not re-register the liveness marker
                # (this store is closed, so it is not a live peer, and
                # re-registering would undo close()'s deletion and make the next
                # startup within the grace window skip recovery), and use the
                # side-effect-free read-only reopen (no file recreation, no
                # write probe). The connection is cached: a fresh connection per
                # read would be released only by GC, leaking file handles under
                # repeated post-close reads.
                conn = self._read_only_conn
                if conn is None:
                    conn = self._open_read_only_connection()
                    self._read_only_conn = conn
            else:
                self._open_connection(register_marker=True)
                conn = self._conn
        if conn is None:
            raise RuntimeError("SQLite job store connection is unavailable")
        return conn

    def _open_read_only_connection(self) -> sqlite3.Connection:
        """Open a connection for a post-close read, with no write side effects.

        Unlike _open_connection, this does not create the parent directory or
        the DB file and does not run the write probe: a read must not have
        write side effects. A deleted file has no data to read, so an empty
        in-memory database is used instead (the reads serve empty results)
        rather than resurrecting the file, which would diverge from a fresh
        open of a missing file.
        """
        if self._db_path.is_file():
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        else:
            conn = sqlite3.connect(":memory:", check_same_thread=False)
            conn.execute(_SCHEMA)
            conn.execute(_PROCESSES_SCHEMA)
            conn.commit()
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {int(self._busy_timeout_ms)}")
        return conn

    def _delete_marker(self, conn: sqlite3.Connection) -> bool:
        """Delete this process's liveness marker, retrying transient failures.

        Returns True when the marker is gone (deleted or already absent). A
        failed DELETE would leave a fresh stale marker behind (the heartbeat
        keeps last_seen_at fresh until close): a co-booting peer within the
        grace window would then treat this gone process as live and skip
        recovery of its interrupted jobs, delaying recovery by the grace window
        (and, for cross-host peers, until the next boot after the marker ages
        out). Transient failures (a locked DB) are therefore retried.
        """
        for _ in range(3):
            try:
                conn.execute("DELETE FROM processes WHERE token = ?", (self._token,))
                conn.commit()
                return True
            except (sqlite3.Error, OSError):
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
        return False

    def close(self) -> None:
        with self._lock:
            self._closed = True
            stop = self._heartbeat_stop
            if stop is not None:
                stop.set()
            thread = self._heartbeat_thread
            conn = self._conn
            registered = self._conn_registered
            if conn is not None:
                if registered:
                    with _OPEN_DB_PATHS_LOCK:
                        count = _OPEN_DB_PATHS.get(self._db_key, 0) - 1
                        if count <= 0:
                            _OPEN_DB_PATHS.pop(self._db_key, None)
                        else:
                            _OPEN_DB_PATHS[self._db_key] = count
                        # Drop our liveness marker so a peer's next startup does
                        # not mistake this (now gone) process for a live one.
                        # The marker is process-wide (one token per OS process,
                        # shared by every store instance in the process on every
                        # path), so it may only be deleted when the last store
                        # instance in the process on ANY path goes away: deleting
                        # it while a sibling instance (on the same or a different
                        # DB) is still open would make the survivor invisible to a
                        # co-booting peer's recovery (its writes refresh the
                        # marker in place and never re-insert a deleted row). A
                        # post-close read-only reopen is not registered, so a
                        # close->read->close sequence cannot unbalance the count
                        # and delete a live sibling's marker.
                        last_instance = not any(_OPEN_DB_PATHS.values())
                        # The delete must run under the registry lock, ordered
                        # against open's count increment (which happens before
                        # the marker registration commit): an open that
                        # increments before our decrement prevents the delete,
                        # and one that increments after it commits its
                        # registration after the delete, re-inserting the
                        # marker. Deleting after releasing the lock let a store
                        # opened in the close window commit its registration
                        # first and then get deleted, leaving the survivor
                        # invisible to a co-booting peer's recovery.
                        if last_instance and not self._delete_marker(conn):
                            logger.warning("Could not remove process marker on close; it will be pruned on a later open")
                try:
                    conn.close()
                except (sqlite3.Error, OSError):
                    # Callers (lifespan teardown, close_job_store) assume close
                    # cannot raise: a raising close would escape teardown and
                    # demote the original startup error to __context__. The
                    # connection is released when it is garbage collected.
                    logger.warning("Could not close the SQLite connection on close; it will be released when garbage collected")
            self._conn = None
            self._conn_registered = False
            read_only_conn = self._read_only_conn
            if read_only_conn is not None:
                # Release the cached post-close read connection too: leaving it
                # open would keep its file handle alive until garbage collection
                # (a close->read->close sequence would otherwise leak it).
                self._read_only_conn = None
                try:
                    read_only_conn.close()
                except (sqlite3.Error, OSError):
                    logger.warning("Could not close the cached post-close read connection; it will be released when garbage collected")
        if thread is not None:
            # Join outside the store lock: a beat in flight may still be
            # waiting for it.
            thread.join(timeout=5)

    def _write_connection(self) -> sqlite3.Connection:
        if self._closed:
            raise StoreClosedError(
                f"Cannot write to closed SQLite job store at {self._db_path}: "
                "a post-shutdown write could resurrect a job that startup recovery already failed"
            )
        return self._connection()

    def _execute_write(self, sql: str, params: tuple) -> int:
        """Run a write statement, committing on success and rolling back on failure.

        Rolling back on failure is essential: a failed commit otherwise leaves a
        stale open transaction on the shared connection that serves non-durable
        data and blocks other connections. Returns the number of affected rows.
        """
        conn = self._write_connection()
        try:
            cursor = conn.execute(sql, params)
            # Refresh our liveness marker in the same transaction: a process
            # that is actively writing jobs is a live peer whose in-flight
            # jobs a co-booting process's startup recovery must not fail.
            conn.execute("UPDATE processes SET last_seen_at = ? WHERE token = ?", (_now(), self._token))
            conn.commit()
            return cursor.rowcount
        except (sqlite3.Error, OSError) as exc:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            raise exc

    def create(self, record: JobRecord) -> JobRecord:
        timestamp = _now()
        with self._lock:
            self._execute_write(
                _CREATE_UPSERT_SQL,
                (
                    record.id,
                    record.status.value,
                    record.request.model_dump_json(),
                    record.result.model_dump_json() if record.result is not None else None,
                    record.error,
                    timestamp,
                    timestamp,
                ),
            )
        return record

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            conn = self._connection()
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        # An unreadable row (unknown status, malformed JSON) is treated as
        # missing: the route serves its declared 404 instead of an unmodeled 500,
        # and the error is logged for diagnosis.
        return self._row_to_record(row)

    def update(self, job_id: str, *, status: JobStatus, result=_UNSET, error=_UNSET) -> UpdateResult:
        with self._lock:
            conn = self._write_connection()
            if result is _UNSET:
                result_mode, result_value = 0, None
            elif result is None:
                result_mode, result_value = 2, None
            else:
                result_mode, result_value = 1, cast("PipelineResult", result).model_dump_json()
            error_mode = 0 if error is _UNSET else 1
            error_value = None if error is _UNSET else error
            # The write and the readback run in one transaction: a concurrent
            # cross-process writer cannot land between them, so the returned
            # record reflects this write (a post-commit readback could return a
            # concurrent writer's row, contradicting the caller's own write).
            try:
                cursor = conn.execute(
                    "UPDATE jobs SET "
                    "status = ?, "
                    "result = CASE ? WHEN 1 THEN ? WHEN 2 THEN NULL ELSE result END, "
                    "error = CASE ? WHEN 1 THEN ? ELSE error END, "
                    "updated_at = ? "
                    "WHERE id = ?",
                    (status.value, result_mode, result_value, error_mode, error_value, _now(), job_id),
                )
                if cursor.rowcount == 0:
                    conn.rollback()
                    return UpdateResult(updated=False)
                row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
                assert row is not None
                # Refresh our liveness marker in the same transaction: a process
                # that is actively writing jobs is a live peer whose in-flight
                # jobs a co-booting process's startup recovery must not fail.
                conn.execute("UPDATE processes SET last_seen_at = ? WHERE token = ?", (_now(), self._token))
                conn.commit()
            except (sqlite3.Error, OSError) as exc:
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                raise exc
        # The row existed and was updated; the record is None only when its
        # stored payload cannot be parsed by the current model version
        # (updated=True, record=None keeps that distinct from a missing job).
        return UpdateResult(updated=True, record=self._row_to_record(row))

    def list(self) -> list[JobRecord]:
        with self._lock:
            conn = self._connection()
            rows = conn.execute("SELECT * FROM jobs ORDER BY rowid").fetchall()
        records: list[JobRecord] = []
        for row in rows:
            record = self._row_to_record(row)
            if record is not None:
                records.append(record)
        return records

    def _consistent_snapshot(self) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
        """Read the jobs table and the peer liveness markers in one read transaction.

        A consistent snapshot prevents a concurrent cross-process commit from
        landing between the two reads (which could make a live peer's in-flight
        job look like it was last updated before the peer started, and get
        failed by recovery). The peers read is skipped when no row is
        non-terminal: there is then nothing to protect.
        """
        with self._lock:
            conn = self._connection()
            conn.execute("BEGIN")
            try:
                rows = conn.execute("SELECT * FROM jobs ORDER BY rowid").fetchall()
                peers: list[sqlite3.Row] = []
                if any(
                    row["status"] in (JobStatus.queued.value, JobStatus.running.value)
                    for row in rows
                ):
                    peers = conn.execute(
                        "SELECT token, started_at, last_seen_at, start_time FROM processes WHERE token != ?",
                        (self._token,),
                    ).fetchall()
            finally:
                conn.execute("ROLLBACK")
        return rows, peers

    def _protected_job_ids(
        self, jobs: list[sqlite3.Row], peers: list[sqlite3.Row], grace_seconds: float
    ) -> frozenset[str]:
        """IDs of the snapshot's non-terminal jobs a live peer may still be working on.

        Used by startup recovery to avoid failing jobs that a live process
        sharing this DB is still working on (the compose deployment mounts one
        shared volume, so a scaled deployment would otherwise let the later
        booter's recovery kill the first replica's in-flight jobs).

        A job is protected when a live peer started at or before the job's last
        update: the peer was running when the job was last touched, so it may be
        working on it. A peer that started after the job's last update cannot be
        working on it, so concurrent startups on a shared DB still recover a
        dead predecessor's interrupted jobs instead of all skipping recovery.

        Same-host peers are live when their PID is alive AND, when the marker
        records a process start time, that start time matches the live process:
        a write-idle peer (a long pipeline run) must still be protected, a dead
        predecessor's PID check fails so a quick restart (systemd/docker
        auto-restart) within the grace window still recovers, and a reused PID
        (an unrelated process that inherited the dead predecessor's PID) does
        not inherit the predecessor's protection. Other hosts (separate PID
        namespaces) and unparseable tokens fall back to the marker's freshness
        within the grace window, the best signal available there (kept honest
        by the liveness heartbeat while the peer process is alive).
        """
        if not peers:
            return frozenset()
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=grace_seconds)).isoformat()
        protected: set[str] = set()
        for job in jobs:
            for peer in peers:
                if peer["started_at"] <= job["updated_at"] and self._peer_is_live(
                    peer["token"], peer["last_seen_at"], cutoff, peer["start_time"]
                ):
                    protected.add(job["id"])
                    break
        return frozenset(protected)

    def recovery_snapshot(self, grace_seconds: float) -> RecoverySnapshot:
        """Consistent snapshot of the jobs and peer markers for startup recovery.

        The protection set, the parsed records and the raw non-terminal ids
        all come from one read transaction (see _consistent_snapshot): a live
        peer sharing the DB that commits a job while this process's recovery is
        running is either before the snapshot (the job is in the protection set
        when the peer may be working on it) or after it (the pass cannot see
        the job at all and cannot fail it). Reading the three as separate
        queries let such a commit land in the raw scan but outside the
        protection set, failing a live peer's job.

        The raw non-terminal ids include rows the record parser quarantines
        (a poisoned non-terminal row: valid queued/running status, unreadable
        JSON): those are invisible to records but must still be failed by
        recovery, since the write path does not parse the row and the terminal
        state is durable even though the record stays unreadable.
        """
        rows, peers = self._consistent_snapshot()
        non_terminal = [
            row for row in rows if row["status"] in (JobStatus.queued.value, JobStatus.running.value)
        ]
        records: list[JobRecord] = []
        for row in rows:
            record = self._row_to_record(row)
            if record is not None:
                records.append(record)
        return RecoverySnapshot(
            records=tuple(records),
            non_terminal_ids=frozenset(row["id"] for row in non_terminal),
            protected=self._protected_job_ids(non_terminal, peers, grace_seconds),
        )

    def _peer_is_live(self, token: str, last_seen_at: str, cutoff: str, start_time: int | None) -> bool:
        """True when a peer marker names a process that is verifiably still alive."""
        hostname, sep, pid_str = token.rpartition(":")
        if not sep:
            # Unparseable token: fall back to the time-based signal.
            return last_seen_at >= cutoff
        if hostname == socket.gethostname():
            # Same host: the PID is the liveness signal, independent of marker
            # freshness (a live write-idle peer must still be protected; a dead
            # predecessor's PID check fails).
            try:
                pid = int(pid_str)
            except ValueError:
                return last_seen_at >= cutoff
            if pid <= 0:
                # Non-positive pids are process-group selectors, not single
                # processes (os.kill(0, ...) targets the caller's own group and
                # os.kill(-1, ...) every signalable process): the probe would
                # succeed regardless of whether the marker's process exists.
                return last_seen_at >= cutoff
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                # No such process: the marker's process is gone.
                return False
            except PermissionError:
                # Exists but owned by another user: it is alive (we cannot
                # refine further without /proc access to its state).
                return True
            except OverflowError:
                # The pid exceeds this platform's pid_t range, so no process can
                # hold it here and the probe cannot run: fall back to the marker
                # freshness signal instead of crashing startup recovery.
                return last_seen_at >= cutoff
            if not _proc_available():
                # /proc unavailable (non-Linux): PID existence is the best
                # signal available.
                return True
            info = _read_proc_stat(pid)
            if info is None:
                # Reaped between the signal probe and the /proc read: the
                # process is gone, so it must not protect its interrupted jobs
                # (a just-dead same-host peer is dead, not a non-Linux fallback).
                return False
            state, actual = info
            if state == "Z":
                # A zombie (defunct, not reaped) passes the signal probe and
                # keeps its start time, but it cannot do work: treat it as dead
                # so its interrupted jobs are recovered.
                return False
            if start_time is None:
                # Legacy marker without a recorded start time: PID existence
                # (and a non-zombie state) is the best signal available.
                return True
            # The PID is alive, but a reused PID belongs to a different
            # process: a start-time mismatch means the marker's process is gone
            # and the live PID is an unrelated successor, so the marker must
            # not protect the predecessor's interrupted jobs.
            return actual == start_time
        # Different host (separate PID namespace): the PID cannot be checked
        # from here, so a fresh marker is the best liveness signal we have.
        return last_seen_at >= cutoff

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> JobRecord | None:
        """Parse a row, quarantining unreadable data instead of raising.

        A row the current model version cannot parse (a status value removed in a
        later version, malformed JSON) must not break list()/get() or startup
        recovery: the DB is persisted across deployments, so one poisoned row
        would otherwise wedge the API in a crash loop until manual DB surgery.
        """
        try:
            return JobRecord(
                id=row["id"],
                status=JobStatus(row["status"]),
                request=PipelineRequest.model_validate_json(row["request"]),
                result=PipelineResult.model_validate_json(row["result"]) if row["result"] is not None else None,
                error=row["error"],
            )
        except ValueError as exc:
            logger.error(
                "Quarantining unreadable job row %s (status=%r): %s",
                row["id"],
                row["status"],
                exc,
            )
            return None
