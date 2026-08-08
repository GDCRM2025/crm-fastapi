#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV="${ROOT}/.venv"

for command_name in git python3 node npm; do
  command -v "${command_name}" >/dev/null 2>&1 || {
    echo "ERROR: falta ${command_name}." >&2
    exit 1
  }
done

if [[ ! -d "${VENV}" ]]; then
  python3 -m venv "${VENV}"
fi

"${VENV}/bin/python" -m pip install --upgrade pip
"${VENV}/bin/python" -m pip install -r "${ROOT}/requirements.txt"

if [[ -f "${ROOT}/frontend/package-lock.json" ]]; then
  npm --prefix "${ROOT}/frontend" ci
fi

if [[ ! -f "${ROOT}/.env" ]]; then
  cp "${ROOT}/.env.example" "${ROOT}/.env"
  chmod 600 "${ROOT}/.env"
  echo "Se creó .env vacío. Completa valores desde el secret store aprobado."
fi

echo "BOOTSTRAP_MAC_OK"
echo "No se descargaron secretos ni se conectó a producción."
