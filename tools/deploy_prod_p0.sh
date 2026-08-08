#!/usr/bin/env bash
set -euo pipefail

# Deploy P0 (mínimo para destrabar Settings + Operaciones + Sub-recetas).
# Sube SOLO los archivos críticos y reinicia Passenger.
#
# Uso (recomendado):
#   CRM_REMOTE_USER=bf68ec5 bash tools/deploy_prod_p0.sh
# También soporta:
#   REMOTE_USER=bf68ec5 bash tools/deploy_prod_p0.sh
#
# Vars:
#   HOST, PORT, REMOTE_USER, KEY, REMOTE_ROOT

HOST="${HOST:-199.250.218.212}"
PORT="${PORT:-2222}"
REMOTE_USER="${REMOTE_USER:-${CRM_REMOTE_USER:-bf68ec5}}"
KEY="${KEY:-data/id_rsa}"
REMOTE_ROOT="${REMOTE_ROOT:-/home/bf68ec5/crm}"

echo "== Deploy (P0) -> ${REMOTE_USER}@${HOST}:${REMOTE_ROOT} =="

retry() {
  local -r n="${1:-3}"
  shift || true
  local i=1
  until "$@"; do
    if [[ "$i" -ge "$n" ]]; then
      return 1
    fi
    echo "!! retry ${i}/${n} failed; waiting 2s..." >&2
    i=$((i + 1))
    sleep 2
  done
}

SCP_OPTS=(-o IdentitiesOnly=yes -o ConnectTimeout=12 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -P "$PORT" -i "$KEY")
SSH_OPTS=(-p "$PORT" -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=12 -o ServerAliveInterval=30 -o ServerAliveCountMax=3)

echo "== Backend (settings) =="
retry 3 scp "${SCP_OPTS[@]}" \
  backend/routers/settings_live.py \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/backend/routers/settings_live.py"

retry 3 scp "${SCP_OPTS[@]}" \
  backend/routers/settings.py \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/backend/routers/settings.py"

echo "== Backend (assets/categorias/inventario) =="
retry 3 scp "${SCP_OPTS[@]}" \
  backend/routers/assets.py \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/backend/routers/assets.py"

echo "== Backend (calendar ops) =="
retry 3 scp "${SCP_OPTS[@]}" \
  backend/routers/calendar.py \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/backend/routers/calendar.py"

echo "== Web (operaciones + sub-recetas + chat dock) =="
retry 3 scp "${SCP_OPTS[@]}" \
  web/views/op_maquinaria_categorias.html \
  web/views/op_maquinaria_inventario.html \
  web/views/op_maquinaria_ficha.html \
  web/views/op_camiones_ficha.html \
  web/views/operaciones_recetas.html \
  web/views/calendar.html \
  web/views/calendar_ops.html \
  web/views/portal_ops.html \
  web/views/settings.html \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/web/views/"

echo "== Web Settings (CRUD) =="
retry 3 scp "${SCP_OPTS[@]}" \
  web/settings/_base.js \
  web/settings/settings.css \
  web/settings/catalog.html \
  web/settings/productos.html \
  web/settings/marcas.html \
  web/settings/usuarios.html \
  web/settings/comunas.html \
  web/settings/categorias.html \
  web/settings/estados.html \
  web/settings/segmentacion.html \
  web/settings/users.html \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/web/settings/"

retry 3 scp "${SCP_OPTS[@]}" \
  web/js/panel.js \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/web/js/panel.js"

retry 3 ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${HOST}" \
  "mkdir -p '${REMOTE_ROOT}/web/js/settings'"

retry 3 scp "${SCP_OPTS[@]}" \
  web/js/settings/catalog.js \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/web/js/settings/catalog.js"

retry 3 scp "${SCP_OPTS[@]}" \
  web/index.html \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/web/index.html"

retry 3 scp "${SCP_OPTS[@]}" \
  web/styles.css \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/web/styles.css"

echo "== Restart Passenger =="
retry 3 ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${HOST}" \
  "mkdir -p '${REMOTE_ROOT}/tmp' && touch '${REMOTE_ROOT}/tmp/restart.txt'"

echo "OK"
