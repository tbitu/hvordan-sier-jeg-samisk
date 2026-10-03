from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "packages" / "scripts" / "sync-openapi.sh"
REAL_API = REPO_ROOT / "apps" / "api"
REAL_CONTRACT = REPO_ROOT / "packages" / "contracts" / "openapi.yaml"

CANDIDATE_PORTS = (8000, 8123, 8234, 8345)

HANGING_APP = """import os
import signal
import time

from fastapi import FastAPI

app = FastAPI()


@app.on_event("startup")
def _hang():
    signal.signal(signal.SIGTERM, lambda *_args: os._exit(0))
    time.sleep(300)
"""

# Serves a spec full of non-ASCII text so the JSON -> YAML -> JSON round trip
# of the sync script is exercised end to end.
UNICODE_APP = """from fastapi import FastAPI

app = FastAPI(
    title="Hvordan sier jeg samisk – ÅÅÅ",
    description="Unicode-rundtur: æøåÆØÅ og é",
    version="0.1.0",
)


@app.get("/hei")
def hei():
    return {"melding": "Hei, æøå"}
"""

# Binds the port (startup completes) but delays every response, so probes
# connect and then wait out their full --max-time instead of being refused.
SLOW_APP = """import asyncio
import os
import signal

from fastapi import FastAPI, Request

app = FastAPI()


@app.on_event("startup")
def _setup():
    signal.signal(signal.SIGTERM, lambda *_args: os._exit(0))


@app.middleware("http")
async def slow(request: Request, call_next):
    await asyncio.sleep(300)
    return await call_next(request)
"""


def _port_free(port: int) -> bool:
    with socket.socket() as probe:
        # SO_REUSEADDR so a lingering TIME_WAIT socket from a previous test's
        # server does not make the port look busy (an active listener still
        # refuses the bind).
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class _DecoyHandler(BaseHTTPRequestHandler):
    """A foreign server answering on a candidate port with a marker spec."""

    spec = json.dumps(
        {
            "openapi": "3.1.0",
            "info": {"title": "DECOY-FOREIGN-SPEC", "version": "0.0.0"},
            "paths": {},
        }
    ).encode()

    def do_GET(self):
        body = self.spec if self.path == "/openapi.json" else b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    """Minimal repo layout so the script writes a throwaway contract.

    The script derives the repo root from its own location, so copying it
    into tmp_path and symlinking apps/api exercises the real script against
    a disposable contract without touching the real one.
    """
    (tmp_path / "packages" / "scripts").mkdir(parents=True)
    (tmp_path / "packages" / "contracts").mkdir(parents=True)
    shutil.copy(SCRIPT, tmp_path / "packages" / "scripts" / "sync-openapi.sh")
    (tmp_path / "apps").mkdir()
    (tmp_path / "apps" / "api").symlink_to(REAL_API)
    return tmp_path


@pytest.fixture
def decoy():
    servers: list[ThreadingHTTPServer] = []

    def _start(port: int) -> int:
        server = ThreadingHTTPServer(("127.0.0.1", port), _DecoyHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        return port

    yield _start
    for server in servers:
        server.shutdown()
        server.server_close()


def _base_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("HSJS_SYNC_PORT", None)
    env.pop("HSJS_SYNC_WAIT_TIMEOUT", None)
    return env


def _kill_process_group(proc: subprocess.Popen) -> None:
    """Kill the script and every process it spawned (notably uvicorn).

    A timeout must not orphan the uvicorn child: subprocess.run's timeout
    handling SIGKILLs only the direct child (the bash script), and SIGKILL
    cannot be trapped, so the script's EXIT trap never runs. The script is
    therefore started in its own session (start_new_session=True) and the
    whole process group is killed here.
    """
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.kill()
    except ProcessLookupError:
        pass


def run_sync(
    repo: Path,
    args: list[str] | None = None,
    extra_env: dict[str, str] | None = None,
    timeout: int = 180,
) -> subprocess.CompletedProcess:
    env = _base_env()
    env.update(extra_env or {})
    cmd = ["bash", str(repo / "packages" / "scripts" / "sync-openapi.sh"), *(args or [])]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        proc.communicate()
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def run_sync_marked(
    repo: Path,
    args: list[str] | None = None,
    extra_env: dict[str, str] | None = None,
    markers: tuple[str, ...] = (),
    timeout: int = 120,
) -> tuple[subprocess.CompletedProcess, dict[str, float]]:
    """Run the script and record when each marker first appeared.

    Mark values are absolute time.monotonic() timestamps, so they can be
    combined with externally recorded monotonic events (e.g. the moment a
    port starts accepting connections).
    """
    env = _base_env()
    env.update(extra_env or {})
    cmd = ["bash", str(repo / "packages" / "scripts" / "sync-openapi.sh"), *(args or [])]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,
    )
    marks: dict[str, float] = {}
    out_lines: list[str] = []
    err_lines: list[str] = []

    def _read(stream, lines: list[str]) -> None:
        for line in stream:
            lines.append(line)
            for marker in markers:
                if marker in line and marker not in marks:
                    marks[marker] = time.monotonic()

    reader_out = threading.Thread(target=_read, args=(proc.stdout, out_lines), daemon=True)
    reader_err = threading.Thread(target=_read, args=(proc.stderr, err_lines), daemon=True)
    reader_out.start()
    reader_err.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Kill the whole group (script + uvicorn child) and drain the pipes
        # before propagating, so a timeout never orphans the server.
        _kill_process_group(proc)
        proc.wait()
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.stderr is not None:
            proc.stderr.close()
        reader_out.join(timeout=5)
        reader_err.join(timeout=5)
        raise
    reader_out.join()
    reader_err.join()
    result = subprocess.CompletedProcess(
        cmd, proc.returncode, "".join(out_lines), "".join(err_lines)
    )
    return result, marks


def contract_path(repo: Path) -> Path:
    return repo / "packages" / "contracts" / "openapi.yaml"


def _install_stub_api(repo: Path, source: str) -> None:
    """Replace the symlinked API with a stub app of the given source."""
    api_dir = repo / "apps" / "api"
    api_dir.unlink()
    api_dir.mkdir()
    (api_dir / "app").mkdir()
    (api_dir / "app" / "__init__.py").write_text("", encoding="utf-8")
    (api_dir / "app" / "main.py").write_text(source, encoding="utf-8")


def _install_hanging_api(repo: Path) -> None:
    """Replace the symlinked API with an app that starts but never serves."""
    _install_stub_api(repo, HANGING_APP)


def _install_slow_api(repo: Path) -> None:
    """Replace the symlinked API with an app that binds but never responds."""
    _install_stub_api(repo, SLOW_APP)


class TestUsageAndEnvValidation:
    def test_help_prints_usage(self, fake_repo: Path):
        result = run_sync(fake_repo, args=["--help"])
        assert result.returncode == 0
        assert "HSJS_SYNC_WAIT_TIMEOUT" in result.stdout
        assert "HSJS_SYNC_PORT" in result.stdout
        assert "--check" in result.stdout

    def test_unknown_argument_rejected(self, fake_repo: Path):
        result = run_sync(fake_repo, args=["--bogus"])
        assert result.returncode == 2

    @pytest.mark.parametrize(
        "env",
        [
            {"HSJS_SYNC_WAIT_TIMEOUT": "0"},
            {"HSJS_SYNC_WAIT_TIMEOUT": "008"},
            {"HSJS_SYNC_WAIT_TIMEOUT": "-5"},
            {"HSJS_SYNC_WAIT_TIMEOUT": "60x"},
            {"HSJS_SYNC_PORT": "0"},
            {"HSJS_SYNC_PORT": "08000"},
            {"HSJS_SYNC_PORT": "70000"},
        ],
    )
    def test_invalid_env_values_rejected(self, fake_repo: Path, env: dict[str, str]):
        result = run_sync(fake_repo, extra_env=env)
        assert result.returncode == 2


class TestDependencyPreflight:
    @pytest.mark.parametrize("missing", ["curl", "ps"])
    def test_missing_tool_fails_fast(self, fake_repo: Path, tmp_path: Path, missing: str):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        needed = [
            "bash",
            "python3",
            "curl",
            "ps",
            "ss",
            "awk",
            "grep",
            "cut",
            "sort",
            "tr",
            "wc",
            "mktemp",
            "diff",
            "cat",
            "chmod",
            "mv",
            "cp",
            "mkdir",
            "rm",
            "sleep",
            # dirname runs before the preflight (SCRIPT_DIR at the top of the
            # script); without it the script silently derives paths from CWD
            # and pollutes stderr with "command not found".
            "dirname",
        ]
        for tool in needed:
            if tool == missing:
                continue
            path = shutil.which(tool)
            if path is None:
                pytest.skip(f"{tool} not found on this host")
            os.symlink(path, bin_dir / tool)
        started = time.monotonic()
        result = run_sync(fake_repo, extra_env={"PATH": str(bin_dir)})
        elapsed = time.monotonic() - started
        assert result.returncode == 1
        assert missing in result.stderr
        # The restricted PATH must cover every external the script uses before
        # (and during) the preflight: no spurious "command not found" noise.
        assert "command not found" not in result.stderr
        assert elapsed < 10


class TestSyncBehavior:
    def test_idempotent_rerun_keeps_contract_and_mode(self, fake_repo: Path):
        port = free_port()
        env = {"HSJS_SYNC_PORT": str(port)}
        first = run_sync(fake_repo, extra_env=env)
        assert first.returncode == 0, first.stderr
        assert "Oppdaterte kontrakten" in first.stdout
        contract = contract_path(fake_repo)
        after_first = contract.read_bytes()
        assert contract.stat().st_mode & 0o777 == 0o644
        second = run_sync(fake_repo, extra_env=env)
        assert second.returncode == 0, second.stderr
        assert "uendret" in second.stdout
        assert contract.read_bytes() == after_first

    def test_foreign_process_on_first_candidate_cannot_pollute_contract(
        self, fake_repo: Path, decoy
    ):
        if not all(_port_free(port) for port in CANDIDATE_PORTS):
            pytest.skip("a candidate port is busy on this host")
        decoy(8000)
        result = run_sync(fake_repo)
        assert result.returncode == 0, result.stderr
        assert "Port 8000 er opptatt" in result.stderr
        contract = contract_path(fake_repo).read_text(encoding="utf-8")
        assert "DECOY-FOREIGN-SPEC" not in contract
        assert "/api/v1/health" in contract

    def test_check_mode_fails_with_diff_on_tampered_contract(self, fake_repo: Path):
        contract = contract_path(fake_repo)
        contract.write_text(
            REAL_CONTRACT.read_text(encoding="utf-8") + "\n# tamper-merke\n",
            encoding="utf-8",
        )
        port = free_port()
        result = run_sync(
            fake_repo, args=["--check"], extra_env={"HSJS_SYNC_PORT": str(port)}
        )
        assert result.returncode == 1
        assert "utdatert" in result.stderr
        assert "# tamper-merke" in result.stderr

    def test_check_mode_missing_contract_prints_expected_content(self, fake_repo: Path):
        port = free_port()
        result = run_sync(
            fake_repo, args=["--check"], extra_env={"HSJS_SYNC_PORT": str(port)}
        )
        assert result.returncode == 1
        assert "finnes ikke ennå" in result.stderr
        assert "openapi: 3.1.0" in result.stderr

    def test_check_mode_up_to_date_is_exit_zero(self, fake_repo: Path):
        port = free_port()
        env = {"HSJS_SYNC_PORT": str(port)}
        first = run_sync(fake_repo, extra_env=env)
        assert first.returncode == 0, first.stderr
        before = contract_path(fake_repo).read_bytes()
        result = run_sync(fake_repo, args=["--check"], extra_env=env)
        assert result.returncode == 0, result.stderr
        assert "uendret" in result.stdout
        # --check must never rewrite the contract.
        assert contract_path(fake_repo).read_bytes() == before

    def test_unicode_round_trip_survives_sync(self, fake_repo: Path):
        _install_stub_api(fake_repo, UNICODE_APP)
        port = free_port()
        result = run_sync(fake_repo, extra_env={"HSJS_SYNC_PORT": str(port)})
        assert result.returncode == 0, result.stderr
        text = contract_path(fake_repo).read_text(encoding="utf-8")
        # allow_unicode: characters are written literally, not as \u escapes.
        assert "æøå" in text
        loaded = yaml.safe_load(text)
        assert loaded["info"]["title"] == "Hvordan sier jeg samisk – ÅÅÅ"
        assert loaded["info"]["description"] == "Unicode-rundtur: æøåÆØÅ og é"


class TestFailureModes:
    def test_wait_budget_respects_timeout(self, fake_repo: Path):
        _install_slow_api(fake_repo)
        port = free_port()
        bind_at: dict[str, float] = {}
        stop = threading.Event()

        def _watch_bind() -> None:
            # Record the moment the port starts accepting connections: the
            # first probe the script can possibly send.
            while not stop.is_set():
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        bind_at["t"] = time.monotonic()
                        return
                except OSError:
                    time.sleep(0.05)

        watcher = threading.Thread(target=_watch_bind, daemon=True)
        watcher.start()
        result, marks = run_sync_marked(
            fake_repo,
            extra_env={"HSJS_SYNC_PORT": str(port), "HSJS_SYNC_WAIT_TIMEOUT": "1"},
            markers=("Starter API", "prøver neste kandidat"),
        )
        stop.set()
        watcher.join(timeout=1)
        assert result.returncode == 1
        assert "Starter API" in marks
        assert "prøver neste kandidat" in marks
        # Budget is 1s. Against a bound-but-silent port the old unclamped
        # probe (curl --max-time 5 + sleep 0.5) waited ~5.5s; the clamped
        # probe must stay within the budget plus the bind-to-first-probe gap
        # and SECONDS granularity.
        if "t" in bind_at:
            # The port bound before the budget expired: measure the wait
            # phase from the first probe, not from script start, so the
            # host-dependent uvicorn+fastapi import time does not count
            # against the budget.
            wait_phase = marks["prøver neste kandidat"] - bind_at["t"]
            assert wait_phase < 4.5
        else:
            # The budget expired before the port bound (slow host): the wait
            # loop is deadline-bounded regardless, because probes fail fast
            # while the port is closed. Import time still must not extend it.
            total_phase = marks["prøver neste kandidat"] - marks["Starter API"]
            assert total_phase < 4.5

    def test_explicit_port_busy_fails_hard_without_fallback(self, fake_repo: Path, decoy):
        # Documented semantics (README, --help): an explicit HSJS_SYNC_PORT
        # disables the candidate fallback - a busy explicit port is a hard
        # failure (exit 1), never a fall-through to 8000/8123/8234/8345.
        port = free_port()
        decoy(port)
        result = run_sync(fake_repo, extra_env={"HSJS_SYNC_PORT": str(port)})
        assert result.returncode == 1
        assert f"Port {port} er opptatt" in result.stderr
        assert f"Ingen av kandidatportene var tilgjengelige: {port}" in result.stderr
        # The server was started exactly once, on the explicit port: no
        # candidate fallback happened.
        assert result.stdout.count("Starter API i stub-modus") == 1
        assert f"http://127.0.0.1:{port}" in result.stdout
        # The foreign spec was never fetched into the contract (no write at
        # all on a hard failure).
        assert "DECOY-FOREIGN-SPEC" not in result.stdout + result.stderr
        assert not contract_path(fake_repo).exists()

    def test_rc3_tries_remaining_candidates(self, fake_repo: Path, decoy):
        if not all(_port_free(port) for port in CANDIDATE_PORTS):
            pytest.skip("a candidate port is busy on this host")
        _install_hanging_api(fake_repo)
        decoy(8123)
        decoy(8234)
        decoy(8345)
        result = run_sync(
            fake_repo, extra_env={"HSJS_SYNC_WAIT_TIMEOUT": "1"}, timeout=90
        )
        assert result.returncode == 1
        for port in CANDIDATE_PORTS:
            assert f"Starter API i stub-modus på http://127.0.0.1:{port}" in result.stdout
        assert "leverte aldri /openapi.json" in result.stderr
