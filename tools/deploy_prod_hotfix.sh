#!/usr/bin/env bash
set -euo pipefail

# Deploy "hotfix" (seguro) a producción: SOLO archivos puntuales que no deberían
# romper flujos existentes.
#
# Uso:
#   bash tools/deploy_prod_hotfix.sh
#
# Variables opcionales:
#   HOST, PORT, USER, KEY, REMOTE_ROOT

HOST="${HOST:-199.250.218.212}"
PORT="${PORT:-2222}"
# IMPORTANT: don't use $USER (it's set by macOS to your local username).
REMOTE_USER="${REMOTE_USER:-bf68ec5}"
KEY="${KEY:-data/id_rsa}"
REMOTE_ROOT="${REMOTE_ROOT:-/home/bf68ec5/crm}"

echo "== Deploy (hotfix) -> ${REMOTE_USER}@${HOST}:${REMOTE_ROOT} =="

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

echo "== Backend =="
retry 3 scp "${SCP_OPTS[@]}" \
  backend/main.py \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/backend/main.py"

retry 3 scp "${SCP_OPTS[@]}" \
  backend/core/logging_setup.py \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/backend/core/logging_setup.py"

retry 3 scp "${SCP_OPTS[@]}" \
  backend/routers/leads.py \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/backend/routers/leads.py"

retry 3 scp "${SCP_OPTS[@]}" \
  backend/routers/auth.py \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/backend/routers/auth.py"

retry 3 scp "${SCP_OPTS[@]}" \
  backend/routers/leads_agenda.py \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/backend/routers/leads_agenda.py"

retry 3 scp "${SCP_OPTS[@]}" \
  backend/routers/assets.py \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/backend/routers/assets.py"

retry 3 scp "${SCP_OPTS[@]}" \
  backend/routers/notifications.py \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/backend/routers/notifications.py"

retry 3 scp "${SCP_OPTS[@]}" \
  backend/routers/recetas.py \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/backend/routers/recetas.py"

retry 3 scp "${SCP_OPTS[@]}" \
  backend/core/email.py \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/backend/core/email.py"

retry 3 scp "${SCP_OPTS[@]}" \
  backend/core/drive_assets.py \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/backend/core/drive_assets.py"

retry 3 scp "${SCP_OPTS[@]}" \
  backend/core/quote_assets.py \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/backend/core/quote_assets.py"

retry 3 scp "${SCP_OPTS[@]}" \
  backend/routers/productos.py \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/backend/routers/productos.py"

retry 3 scp "${SCP_OPTS[@]}" \
  backend/routers/activity.py \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/backend/routers/activity.py"

retry 3 scp "${SCP_OPTS[@]}" \
  backend/routers/quotes_override.py \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/backend/routers/quotes_override.py"

retry 3 scp "${SCP_OPTS[@]}" \
  backend/routers/settings_live.py \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/backend/routers/settings_live.py"

echo "== Web =="
retry 3 scp "${SCP_OPTS[@]}" \
  web/js/panel.js \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/web/js/panel.js"

retry 3 scp "${SCP_OPTS[@]}" \
  web/js/cotizador.js \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/web/js/cotizador.js"

retry 3 scp "${SCP_OPTS[@]}" \
  web/views/operaciones_recetas.html \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/web/views/operaciones_recetas.html"

retry 3 scp "${SCP_OPTS[@]}" \
  web/views/calendar_ops.html \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/web/views/calendar_ops.html"

retry 3 scp "${SCP_OPTS[@]}" \
  web/views/op_maquinaria_categorias.html \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/web/views/op_maquinaria_categorias.html"

retry 3 scp "${SCP_OPTS[@]}" \
  web/views/op_maquinaria_inventario.html \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/web/views/op_maquinaria_inventario.html"

retry 3 scp "${SCP_OPTS[@]}" \
  web/views/op_maquinaria_ficha.html \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/web/views/op_maquinaria_ficha.html"

retry 3 scp "${SCP_OPTS[@]}" \
  web/views/op_camiones_ficha.html \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/web/views/op_camiones_ficha.html"

retry 3 scp "${SCP_OPTS[@]}" \
  web/views/op_camiones_entrega.html \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/web/views/op_camiones_entrega.html"

retry 3 scp "${SCP_OPTS[@]}" \
  web/views/op_camiones_devolucion.html \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/web/views/op_camiones_devolucion.html"

retry 3 scp "${SCP_OPTS[@]}" \
  web/views/system_notifs.html \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/web/views/system_notifs.html"

retry 3 scp "${SCP_OPTS[@]}" \
  web/views/settings.html \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/web/views/settings.html"

retry 3 scp "${SCP_OPTS[@]}" \
  web/views/leads.html \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/web/views/leads.html"

retry 3 scp "${SCP_OPTS[@]}" \
  web/views/historial_cotizaciones.html \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/web/views/historial_cotizaciones.html"

echo "== Restart Passenger =="
retry 3 ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${HOST}" \
  "touch '${REMOTE_ROOT}/tmp/restart.txt'"

echo "OK"
