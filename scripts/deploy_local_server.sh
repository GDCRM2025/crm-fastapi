#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER="${CRM_SERVER:-oscar@192.168.100.51}"
REMOTE_ROOT="${CRM_REMOTE_ROOT:-/opt/greendiamond/crm}"
REMOTE_PY="${CRM_REMOTE_PY:-/opt/greendiamond/venv/bin/python}"
SERVICE="${CRM_SERVICE:-crm-gd.service}"
BACKUP_DIR="${CRM_BACKUP_DIR:-/opt/greendiamond/backups/deploy}"
STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILE="$BACKUP_DIR/crm_$STAMP.tar.gz"

cd "$ROOT"

command -v ssh >/dev/null 2>&1 || { echo "ERROR: falta ssh"; exit 1; }
command -v rsync >/dev/null 2>&1 || { echo "ERROR: falta rsync"; exit 1; }
command -v node >/dev/null 2>&1 || { echo "ERROR: falta node"; exit 1; }

LOCAL_PY=""
for CANDIDATE in "$ROOT/.venv/bin/python" "$ROOT/venv/bin/python" "$(command -v python3 2>/dev/null || true)"; do
  if [ -n "$CANDIDATE" ] && [ -x "$CANDIDATE" ]; then
    LOCAL_PY="$CANDIDATE"
    break
  fi
done

if [ -z "$LOCAL_PY" ]; then
  echo "ERROR: no se encontro Python local"
  exit 1
fi

echo "=== VALIDACION LOCAL ==="
"$LOCAL_PY" -m compileall -q backend

while IFS= read -r -d '' FILE; do
  node --check "$FILE" >/dev/null
done < <(find web/js -type f -name '*.js' ! -name '*.min.js' ! -name '*.bak*' -print0)

git diff --check

echo "=== CONEXION SERVIDOR ==="
ssh -o ConnectTimeout=10 "$SERVER" "test -d '$REMOTE_ROOT' && test -x '$REMOTE_PY' && sudo -n systemctl is-active '$SERVICE' >/dev/null"

echo "=== RESPALDO REMOTO ==="
ssh "$SERVER" "mkdir -p '$BACKUP_DIR' && cd '$REMOTE_ROOT' && tar -czf '$BACKUP_FILE' backend web scripts"

echo "RESPALDO: $BACKUP_FILE"

echo "=== SINCRONIZACION BACKEND ==="
rsync -az --checksum --itemize-changes \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '*.bak*' \
  --exclude '.DS_Store' \
  backend/ "$SERVER:$REMOTE_ROOT/backend/"

echo "=== SINCRONIZACION FRONTEND ==="
rsync -az --checksum --itemize-changes \
  --exclude '*.bak*' \
  --exclude '.DS_Store' \
  web/ "$SERVER:$REMOTE_ROOT/web/"

echo "=== SINCRONIZACION MIGRACION ==="
rsync -az --checksum --itemize-changes \
  scripts/migrate_release_20260801.py \
  "$SERVER:$REMOTE_ROOT/scripts/migrate_release_20260801.py"

echo "=== SINCRONIZACION ACCESO REMOTO SEGURO ==="
ssh "$SERVER" "mkdir -p '$REMOTE_ROOT/deploy/ubuntu'"
rsync -az --checksum --itemize-changes \
  --exclude '.DS_Store' \
  deploy/ubuntu/ "$SERVER:$REMOTE_ROOT/deploy/ubuntu/"

echo "=== VALIDACION Y REINICIO ==="
set +e
ssh "$SERVER" "bash -s" <<REMOTE
set -euo pipefail
REMOTE_ROOT='$REMOTE_ROOT'
REMOTE_PY='$REMOTE_PY'
SERVICE='$SERVICE'
BACKUP_FILE='$BACKUP_FILE'

rollback() {
  cd "\$REMOTE_ROOT"
  tar -xzf "\$BACKUP_FILE"
  sudo -n systemctl restart "\$SERVICE"
}

trap rollback ERR

"\$REMOTE_PY" -m compileall -q "\$REMOTE_ROOT/backend"
cd "\$REMOTE_ROOT"
"\$REMOTE_PY" scripts/migrate_release_20260801.py
sudo -n systemctl restart "\$SERVICE"

for I in 1 2 3 4 5 6 7 8 9 10; do
  if sudo -n systemctl is-active "\$SERVICE" >/dev/null 2>&1 && curl -fsS --max-time 5 http://127.0.0.1:8000/openapi.json >/dev/null 2>&1; then
    trap - ERR
    echo "CRM_LOCAL_DEPLOYED"
    exit 0
  fi
  sleep 1
done

exit 1
REMOTE
STATUS=$?
set -e

if [ "$STATUS" -ne 0 ]; then
  echo "ERROR: despliegue fallido. Se intento restaurar $BACKUP_FILE"
  exit "$STATUS"
fi

echo "=== ESTADO FINAL ==="
ssh "$SERVER" "sudo -n systemctl --no-pager --full status '$SERVICE' | sed -n '1,12p'"

echo "DEPLOY LOCAL COMPLETADO"
