#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT}"
PID_FILE="${ROOT}/runtime/mac/uvicorn.pid"

if [[ -f "${PID_FILE}" ]]; then
  PID="$(<"${PID_FILE}")"
  if kill -0 "${PID}" 2>/dev/null; then
    kill "${PID}"
    for _ in {1..20}; do
      kill -0 "${PID}" 2>/dev/null || break
      sleep 0.25
    done
  fi
  rm -f "${PID_FILE}"
fi

if [[ "${1:-}" == "--postgres" ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
  PG_CTL=/opt/homebrew/opt/postgresql@16/bin/pg_ctl
  [[ -x "${PG_CTL}" ]] || PG_CTL="$(command -v pg_ctl || true)"
  [[ -d "${GD_LOCAL_PGDATA:-}" ]] && "${PG_CTL}" -D "${GD_LOCAL_PGDATA}" stop -m fast || true
fi

echo "MAC_LOCAL_STOPPED"
