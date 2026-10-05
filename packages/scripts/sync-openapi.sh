#!/usr/bin/env bash
# Sync packages/contracts/openapi.yaml from the running FastAPI app.
#
# Starts the API in stub mode on a free local port, fetches /openapi.json,
# converts it to YAML and writes packages/contracts/openapi.yaml.
# Idempotent: the file is only rewritten when the content actually changes.
# Only the server started by this script may provide the spec: ownership is
# verified against every listener on the port (and re-verified right before
# the fetch), so a foreign process on a candidate port cannot pollute the
# contract.
#
# Usage: see --help.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
API_ROOT="${REPO_ROOT}/apps/api"
CONTRACT_PATH="${REPO_ROOT}/packages/contracts/openapi.yaml"
HOST="127.0.0.1"
SHUTDOWN_GRACE_S=5
FETCH_ATTEMPTS=3

CHECK_MODE=0
for arg in "$@"; do
  case "${arg}" in
    --check) CHECK_MODE=1 ;;
    -h|--help)
      cat <<'USAGE'
Sync packages/contracts/openapi.yaml fra den kjørende FastAPI-appen.

Starter API-et i stub-modus på en lokal port, henter /openapi.json fra den
kjørende instansen, konverterer det til YAML og skriver
packages/contracts/openapi.yaml. Idempotent: filen skrives bare om innholdet
faktisk endrer seg. Bare serveren startet av dette skriptet kan levere
spesifikasjonen (eierskap verifisert mot alle lyttere på porten, og
gjenverifisert rett før henting), så en fremmed prosess på en kandidatport
kan ikke forurene kontrakten.

Bruk:
  bash packages/scripts/sync-openapi.sh          # synkroniser (skriv om endret)
  bash packages/scripts/sync-openapi.sh --check  # CI-modus: feil om utdatert
  HSJS_SYNC_PORT=8123 bash packages/scripts/sync-openapi.sh

Miljøvariabler:
  HSJS_SYNC_PORT         Eksplisitt port (1-65535). Standard: kandidatene
                         8000, 8123, 8234, 8345. Med en eksplisitt port er
                         det ingen automatisk fallback: en opptatt port gir
                         en hard feil.
  HSJS_SYNC_WAIT_TIMEOUT Sekunder å vente på at serveren svarer (standard 60).

Avslutningskoder: 0 = OK (evt. uendret), 1 = feil, 2 = ugyldig argument
eller miljøvariabel.
USAGE
      exit 0
      ;;
    *)
      echo "ukjent argument: ${arg}" >&2
      exit 2
      ;;
  esac
done

# HSJS_SYNC_WAIT_TIMEOUT: positive integer number of seconds.
# Leading zeros are rejected: bash arithmetic would read them as octal.
WAIT_TIMEOUT_S="${HSJS_SYNC_WAIT_TIMEOUT:-60}"
if ! [[ "${WAIT_TIMEOUT_S}" =~ ^[1-9][0-9]{0,4}$ ]]; then
  echo "Ugyldig HSJS_SYNC_WAIT_TIMEOUT: '${HSJS_SYNC_WAIT_TIMEOUT}' (må være et positivt heltall uten førerende nuller, antall sekunder)." >&2
  exit 2
fi

# HSJS_SYNC_PORT: explicit port (1-65535) or unset to auto-pick from candidates.
if [[ -n "${HSJS_SYNC_PORT:-}" ]]; then
  if ! [[ "${HSJS_SYNC_PORT}" =~ ^[1-9][0-9]{0,4}$ ]] || (( HSJS_SYNC_PORT > 65535 )); then
    echo "Ugyldig HSJS_SYNC_PORT: '${HSJS_SYNC_PORT}' (må være et tall mellom 1 og 65535 uten førerende nuller)." >&2
    exit 2
  fi
  CANDIDATE_PORTS=( "${HSJS_SYNC_PORT}" )
else
  CANDIDATE_PORTS=( 8000 8123 8234 8345 )
fi

# Tooling preflight: fail fast with a clear message instead of a misleading
# runtime failure (without ps, server_alive is always false and a healthy
# server is reported as dead; without curl, every probe is empty and the full
# wait budget is burned per candidate).
for tool in python3 curl ps; do
  if ! command -v "${tool}" >/dev/null 2>&1; then
    echo "Mangler verktøy for synkronisering: ${tool}." >&2
    exit 1
  fi
done
if ! command -v ss >/dev/null 2>&1 && ! command -v lsof >/dev/null 2>&1; then
  echo "Advarsel: verken ss eller lsof finnes; eierskap verifiseres kun via uvicorns bind-logg (svaker garanti)." >&2
fi

if ! python3 -c "import fastapi, uvicorn, yaml" >/dev/null 2>&1; then
  echo "Mangler Python-avhengigheter for synkronisering (fastapi, uvicorn, pyyaml)." >&2
  echo "Kjør: cd ${API_ROOT} && pip install -e .[test]" >&2
  exit 1
fi

WORK_DIR="$(mktemp -d)"
SERVER_LOG="${WORK_DIR}/server.log"
JSON_PATH="${WORK_DIR}/openapi.json"
NEW_YAML="${WORK_DIR}/openapi.new.yaml"
SERVER_PID=""
CONTRACT_TMP=""

# True while the PID we started is alive AND still a child of this script.
# The ppid check guards against a recycled PID belonging to an unrelated
# process being signalled or mistaken for our server.
server_alive() {
  [[ -n "${SERVER_PID}" ]] || return 1
  kill -0 "${SERVER_PID}" 2>/dev/null || return 1
  local ppid
  ppid="$(ps -o ppid= -p "${SERVER_PID}" 2>/dev/null | tr -d '[:space:]' || true)"
  [[ "${ppid}" == "$$" ]]
}

# All PIDs listening on the given port, one per line (deduplicated). Empty
# when the tooling cannot see any PID (nothing listening, or the listeners
# belong to other users). Always returns 0: an empty result is a valid state,
# and under set -euo pipefail a grep/lsof "no match" exit must not leak out
# of the command substitution and kill the script silently.
port_owner_pids() {
  local port="$1"
  local result=""
  if command -v ss >/dev/null 2>&1; then
    result="$(ss -ltnp 2>/dev/null | awk -v p=":${port}$" '$4 ~ p' | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -un || true)"
  elif command -v lsof >/dev/null 2>&1; then
    result="$(lsof -ti "tcp:${port}" -sTCP:LISTEN 2>/dev/null | sort -un || true)"
  fi
  if [[ -n "${result}" ]]; then
    printf '%s\n' "${result}"
  fi
  return 0
}

# Number of LISTEN sockets the tooling sees on the port (0 when it cannot say).
# Always returns 0 for the same reason as port_owner_pids: a tool "no match"
# exit must not leak through set -o pipefail.
port_listener_count() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -ltn 2>/dev/null | awk -v p=":${port}$" '$4 ~ p' | wc -l | tr -d '[:space:]' || true
  elif command -v lsof >/dev/null 2>&1; then
    lsof -ti "tcp:${port}" -sTCP:LISTEN 2>/dev/null | sort -u | wc -l | tr -d '[:space:]' || true
  else
    printf '0'
  fi
}

# True only when we can prove the port is exclusively ours: every visible
# listener PID is our server AND no extra LISTEN socket is hiding behind a
# non-attributable PID (e.g. another user's SO_REUSEPORT listener). When the
# tooling sees nothing at all we fall back to our own uvicorn bind log line.
port_owned_by_server() {
  local port="$1" pids pid count visible
  pids="$(port_owner_pids "${port}")"
  if [[ -n "${pids}" ]]; then
    while IFS= read -r pid; do
      if [[ "${pid}" != "${SERVER_PID}" ]]; then
        return 1
      fi
    done <<< "${pids}"
    count="$(port_listener_count "${port}")"
    visible="$(printf '%s\n' "${pids}" | wc -l | tr -d '[:space:]')"
    if (( count > visible )); then
      return 1
    fi
    return 0
  fi
  if (( $(port_listener_count "${port}") > 0 )); then
    # The port is listened on, but no PID is visible: the listener is not ours
    # (our own socket would carry our PID).
    return 1
  fi
  grep -q "Uvicorn running on http://${HOST}:${port}" "${SERVER_LOG}"
}

# Kill the server we started, waiting at most SHUTDOWN_GRACE_S before SIGKILL.
kill_server() {
  if server_alive; then
    kill "${SERVER_PID}" 2>/dev/null || true
    local i
    for (( i = 0; i < SHUTDOWN_GRACE_S * 2; i++ )); do
      server_alive || break
      sleep 0.5
    done
    if server_alive; then
      kill -9 "${SERVER_PID}" 2>/dev/null || true
    fi
    wait "${SERVER_PID}" 2>/dev/null || true
  fi
  SERVER_PID=""
}

cleanup() {
  kill_server
  if [[ -n "${CONTRACT_TMP}" && -f "${CONTRACT_TMP}" ]]; then
    rm -f "${CONTRACT_TMP}"
  fi
  rm -rf "${WORK_DIR}"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

start_server() {
  local port="$1"
  # A unique throwaway DB per start: on a port-busy retry the previous
  # uvicorn's drain can still hold the file, and a second start's open probe
  # would race it on the same path (a stall past the 5s busy_timeout kills the
  # new server with "database is locked" and burns the port candidate).
  local db_path
  db_path="$(mktemp "${WORK_DIR}/jobs.XXXXXX")"
  (
    cd "${API_ROOT}"
    # Isolate the sync server's job store in a throwaway DB: its startup
    # recovery must never fail in-flight jobs of a live dev server that shares
    # the default DB.
    HSJS_PROVIDER_STUB_MODE=true HSJS_ARTIFACTS_DIR="${WORK_DIR}/artifacts" \
      HSJS_JOB_STORE_BACKEND=sqlite HSJS_DB_PATH="${db_path}" \
      exec python3 -m uvicorn app.main:app --host "${HOST}" --port "${port}" --log-level info
  ) >"${SERVER_LOG}" 2>&1 &
  SERVER_PID=$!
}

# Wait until OUR server serves /openapi.json. A 200 is only accepted once
# ownership of the port is verified exhaustively (see port_owned_by_server),
# so a foreign process that happens to answer on the candidate port can never
# be mistaken for ours. Each probe is clamped to the remaining budget, so the
# total wait never overshoots WAIT_TIMEOUT_S by more than one second.
# Return codes: 0 = serving, 1 = server died, 2 = foreign process on port,
# 3 = timed out with the server still alive.
wait_for_spec() {
  local deadline=$((SECONDS + WAIT_TIMEOUT_S))
  local code remaining probe_max owners
  while :; do
    remaining=$((deadline - SECONDS))
    if (( remaining <= 0 )); then
      break
    fi
    if ! server_alive; then
      echo "API-prosessen døde under oppstart:" >&2
      cat "${SERVER_LOG}" >&2
      return 1
    fi
    probe_max="${remaining}"
    if (( probe_max > 5 )); then
      probe_max=5
    fi
    code="$(curl -s --max-time "${probe_max}" -o /dev/null -w '%{http_code}' "${URL}/openapi.json" || true)"
    if [[ "${code}" == "200" ]]; then
      if port_owned_by_server "${PORT}"; then
        return 0
      fi
      owners="$(port_owner_pids "${PORT}")"
      if [[ -n "${owners}" ]]; then
        echo "Port ${PORT} svarer, men eies av en annen prosess (PID: $(tr '\n' ' ' <<< "${owners}"))." >&2
        return 2
      fi
      # A 200 we cannot attribute to our server and no visible foreign PID:
      # our uvicorn has not logged a successful bind yet (or the listener is
      # unattributable). Keep waiting; if the port is actually busy our server
      # dies and the busy-port path runs.
      if (( deadline - SECONDS >= 2 )); then
        sleep 0.5
      fi
      continue
    fi
    if (( deadline - SECONDS >= 2 )); then
      sleep 0.5
    fi
  done
  if server_alive; then
    echo "API-prosessen kjører, men leverte ikke ${URL}/openapi.json innen ${WAIT_TIMEOUT_S} sekunder:" >&2
    cat "${SERVER_LOG}" >&2
    return 3
  fi
  echo "API-prosessen døde under oppstart:" >&2
  cat "${SERVER_LOG}" >&2
  return 1
}

# Fetch the spec with a bounded number of retries: a brief HTTP flap right
# after a successful probe must not escape the documented exit-code scheme.
# Ownership is re-verified before EVERY attempt: if our server dies between
# attempts and a foreign process binds the port in the retry gap, we must not
# fetch the foreign spec.
fetch_spec() {
  local attempt
  for (( attempt = 1; attempt <= FETCH_ATTEMPTS; attempt++ )); do
    if ! server_alive || ! port_owned_by_server "${PORT}"; then
      echo "Port ${PORT} eies ikke lenger av API-prosessen (PID ${SERVER_PID}) under henting (forsøk ${attempt}); avbryter for ikke å hente en fremmed spesifikasjon." >&2
      return 1
    fi
    if curl -sf --max-time 30 "${URL}/openapi.json" -o "${JSON_PATH}"; then
      return 0
    fi
    echo "Henting av /openapi.json feilet (forsøk ${attempt}/${FETCH_ATTEMPTS})." >&2
    sleep 1
  done
  echo "Klarte ikke å hente ${URL}/openapi.json etter ${FETCH_ATTEMPTS} forsøk." >&2
  return 1
}

# Let uvicorn's own bind be the port test (no separate bind-test to race), and
# fall through to the next candidate port if the chosen one turns out busy.
PORT=""
URL=""
STARTUP_TIMEOUTS=0
for candidate in "${CANDIDATE_PORTS[@]}"; do
  PORT="${candidate}"
  URL="http://${HOST}:${PORT}"
  echo "Starter API i stub-modus på ${URL} ..."
  start_server "${PORT}"
  rc=0
  wait_for_spec || rc=$?
  case "${rc}" in
    0)
      break
      ;;
    2)
      echo "Port ${PORT} er opptatt; prøver neste kandidat." >&2
      kill_server
      continue
      ;;
    3)
      # The server process is alive but never served the spec within the
      # budget: usually a slow startup (e.g. an unresponsive readiness probe)
      # rather than a bad port, so try the remaining candidates first.
      echo "API-prosessen kjører, men leverte ikke ${URL}/openapi.json innen ${WAIT_TIMEOUT_S} sekunder; prøver neste kandidat." >&2
      STARTUP_TIMEOUTS=$((STARTUP_TIMEOUTS + 1))
      kill_server
      continue
      ;;
    *)
      if grep -qi "address already in use\|EADDRINUSE\|errno 98\|errno 48" "${SERVER_LOG}"; then
        echo "Port ${PORT} er opptatt; prøver neste kandidat." >&2
        kill_server
        continue
      fi
      echo "API-prosessen klarte ikke å starte på port ${PORT}." >&2
      exit 1
      ;;
  esac
done

if ! server_alive; then
  if (( STARTUP_TIMEOUTS > 0 )); then
    echo "API-prosessen kjørte, men leverte aldri /openapi.json innen ${WAIT_TIMEOUT_S} sekunder på ${STARTUP_TIMEOUTS} av ${#CANDIDATE_PORTS[@]} kandidatportene (${CANDIDATE_PORTS[*]})." >&2
  else
    echo "Ingen av kandidatportene var tilgjengelige: ${CANDIDATE_PORTS[*]}" >&2
  fi
  exit 1
fi

# Re-verify ownership immediately before the fetch: a foreign listener that
# appeared after wait_for_spec must not be able to serve us its spec.
if ! port_owned_by_server "${PORT}"; then
  echo "Port ${PORT} eies ikke lenger av API-prosessen (PID ${SERVER_PID}); avbryter for ikke å hente en fremmed spesifikasjon." >&2
  exit 1
fi

if ! fetch_spec; then
  exit 1
fi
echo "Hentet /openapi.json fra kjørende instans."

python3 - "${JSON_PATH}" >"${NEW_YAML}" <<'PY'
import json
import sys

import yaml

with open(sys.argv[1], encoding="utf-8") as handle:
    spec = json.load(handle)

# FastAPI emits absolute paths (e.g. /api/v1/health), so the server base is the
# app root, not the API prefix.
spec.setdefault(
    "servers",
    [{"url": "http://localhost:8000", "description": "Lokal utviklingsinstans"}],
)

sys.stdout.write(
    yaml.safe_dump(
        spec,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=120,
    )
)
PY

if [[ -f "${CONTRACT_PATH}" ]] && diff -q "${CONTRACT_PATH}" "${NEW_YAML}" >/dev/null 2>&1; then
  echo "Kontrakten er oppdatert: ${CONTRACT_PATH} er uendret."
  exit 0
fi

if (( CHECK_MODE )); then
  echo "Kontrakten er utdatert. Kjør 'npm run sync:openapi' (eller 'bash packages/scripts/sync-openapi.sh') og commit endringen." >&2
  if [[ -f "${CONTRACT_PATH}" ]]; then
    diff -u "${CONTRACT_PATH}" "${NEW_YAML}" >&2 || true
  else
    echo "Kontrakten finnes ikke ennå (${CONTRACT_PATH}); forventet innhold:" >&2
    cat "${NEW_YAML}" >&2
  fi
  exit 1
fi

mkdir -p "$(dirname "${CONTRACT_PATH}")"
# Write to a temp file in the target directory, then rename: mv within the same
# directory is atomic, so an interrupt never leaves a truncated contract behind.
CONTRACT_TMP="$(mktemp "$(dirname "${CONTRACT_PATH}")/.openapi.XXXXXX")"
cp "${NEW_YAML}" "${CONTRACT_TMP}"
# mktemp creates 0600; restore the normal readable mode before the rename so a
# sync never silently tightens the contract's permissions.
chmod 644 "${CONTRACT_TMP}"
mv "${CONTRACT_TMP}" "${CONTRACT_PATH}"
CONTRACT_TMP=""
echo "Oppdaterte kontrakten: ${CONTRACT_PATH}"
