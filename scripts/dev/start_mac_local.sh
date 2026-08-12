#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT}"

if [[ ! -f .env ]]; then
  echo "ERROR: falta .env; ejecuta scripts/dev/bootstrap_mac.sh y configúralo." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

PYTHON="${ROOT}/.venv/bin/python"
[[ -x "${PYTHON}" ]] || { echo "ERROR: falta .venv." >&2; exit 1; }
: "${DATABASE_URL:?ERROR: DATABASE_URL no está configurada}"
: "${JWT_SECRET:?ERROR: JWT_SECRET no está configurado}"
: "${GD_LOCAL_PGDATA:?ERROR: GD_LOCAL_PGDATA no está configurado}"

DB_HOST="$("${PYTHON}" -c 'import os,urllib.parse; print(urllib.parse.urlsplit(os.environ["DATABASE_URL"].replace("postgresql+psycopg://","postgresql://")).hostname or "")')"
DB_PORT="$("${PYTHON}" -c 'import os,urllib.parse; print(urllib.parse.urlsplit(os.environ["DATABASE_URL"].replace("postgresql+psycopg://","postgresql://")).port or 5432)')"
DB_NAME="$("${PYTHON}" -c 'import os,urllib.parse; print(urllib.parse.urlsplit(os.environ["DATABASE_URL"].replace("postgresql+psycopg://","postgresql://")).path.lstrip("/"))')"
[[ "${DB_HOST}" == "127.0.0.1" || "${DB_HOST}" == "localhost" || "${DB_HOST}" == "::1" ]] || {
  echo "REFUSED: la base local debe usar loopback." >&2
  exit 1
}
DB_NAME_LOWER="$(printf '%s' "${DB_NAME}" | tr '[:upper:]' '[:lower:]')"
case "${DB_NAME_LOWER}" in
  *test*|*restore*|*dev*|*local*) ;;
  *) echo "REFUSED: la base no parece aislada/de desarrollo." >&2; exit 1 ;;
esac
[[ -d "${GD_LOCAL_PGDATA}" ]] || { echo "ERROR: GD_LOCAL_PGDATA no existe." >&2; exit 1; }

PG_CTL=""
PG_ISREADY=""
PSQL=""
for candidate in /opt/homebrew/opt/postgresql@16/bin /usr/local/opt/postgresql@16/bin; do
  [[ -n "${PG_CTL}" ]] || [[ ! -x "${candidate}/pg_ctl" ]] || PG_CTL="${candidate}/pg_ctl"
  [[ -n "${PG_ISREADY}" ]] || [[ ! -x "${candidate}/pg_isready" ]] || PG_ISREADY="${candidate}/pg_isready"
  [[ -n "${PSQL}" ]] || [[ ! -x "${candidate}/psql" ]] || PSQL="${candidate}/psql"
done
[[ -n "${PG_CTL}" ]] || PG_CTL="$(command -v pg_ctl || true)"
[[ -n "${PG_ISREADY}" ]] || PG_ISREADY="$(command -v pg_isready || true)"
[[ -n "${PSQL}" ]] || PSQL="$(command -v psql || true)"
[[ -x "${PG_CTL}" && -x "${PG_ISREADY}" && -x "${PSQL}" ]] || {
  echo "ERROR: faltan binarios PostgreSQL 16." >&2
  exit 1
}

PG_SOCKET="${GD_LOCAL_PG_SOCKET:-/tmp/gdpg-${DB_PORT}}"
mkdir -p "${PG_SOCKET}" runtime/mac
if ! "${PG_ISREADY}" -h "${DB_HOST}" -p "${DB_PORT}" >/dev/null 2>&1; then
  "${PG_CTL}" -D "${GD_LOCAL_PGDATA}" -l "${ROOT}/runtime/mac/postgres.log" \
    -o "-p ${DB_PORT} -k ${PG_SOCKET} -c listen_addresses=${DB_HOST}" start
fi
"${PG_ISREADY}" -h "${DB_HOST}" -p "${DB_PORT}" -t 15 >/dev/null

for migration in \
  migrations/2026_08_08_gd_intelligence_core.sql \
  migrations/2026_08_08_web_intelligence_sources.sql \
  migrations/2026_08_08_site_health.sql \
  migrations/2026_08_08_integration_inventory.sql \
  migrations/2026_08_09_tracking_attribution.sql \
  migrations/2026_08_09_paid_media_help.sql \
  migrations/2026_08_10_paid_media_intelligence.sql \
  migrations/2026_08_10_credential_vault.sql \
  migrations/2026_08_10_intelligence_platform.sql \
  migrations/2026_08_11_omnichannel_inbox.sql \
  migrations/2026_08_12_integration_state_parity.sql; do
  "${PSQL}" "${DATABASE_URL}" -v ON_ERROR_STOP=1 -q -f "${migration}"
done

PID_FILE="${ROOT}/runtime/mac/uvicorn.pid"
if [[ "${1:-}" == "--foreground" ]]; then
  echo "MAC_LOCAL_STARTING"
  echo "LOGIN_URL=http://127.0.0.1:8000/web/login.html"
  exec "${PYTHON}" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
fi

if [[ -f "${PID_FILE}" ]] && kill -0 "$(<"${PID_FILE}")" 2>/dev/null; then
  echo "FastAPI ya está activo (PID $(<"${PID_FILE}"))."
else
  nohup "${PYTHON}" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 \
    >"${ROOT}/runtime/mac/uvicorn.log" 2>&1 &
  echo "$!" >"${PID_FILE}"
fi

for _ in {1..30}; do
  if curl -fsS --max-time 2 http://127.0.0.1:8000/healthz >/dev/null; then
    echo "MAC_LOCAL_READY"
    echo "LOGIN_URL=http://127.0.0.1:8000/web/login.html"
    exit 0
  fi
  sleep 1
done

echo "ERROR: FastAPI no respondió; revisa runtime/mac/uvicorn.log." >&2
exit 1
