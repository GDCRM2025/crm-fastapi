#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT}"

echo "REPOSITORY=${ROOT}"
git rev-parse --is-inside-work-tree >/dev/null
echo "BRANCH=$(git branch --show-current)"
echo "HEAD=$(git rev-parse HEAD)"

test -f .env.example
test -f requirements.txt
test -f frontend/package-lock.json

PYTHON="${ROOT}/.venv/bin/python"
if [[ ! -x "${PYTHON}" ]]; then
  echo "ERROR: falta .venv; ejecuta scripts/dev/bootstrap_mac.sh" >&2
  exit 1
fi

"${PYTHON}" -m compileall -q backend
"${PYTHON}" -m unittest discover -s tests -p 'test*.py' -q
npm --prefix frontend run build
git diff --check

if command -v gitleaks >/dev/null 2>&1; then
  gitleaks dir . --redact=100 --no-banner --no-color
else
  echo "WARNING: gitleaks no está instalado; secret scan omitido." >&2
fi

echo "VERIFY_ENVIRONMENT_OK"
