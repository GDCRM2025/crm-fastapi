#!/usr/bin/env bash
set -euo pipefail

# Deploy mínimo a producción (cPanel/Passenger) para GREENDIAMOND CRM.
# Corre en tu Mac (zsh/bash). Requiere: ssh/scp, key en data/id_rsa

HOST="${HOST:-199.250.218.212}"
PORT="${PORT:-2222}"
# IMPORTANT: don't use $USER. Prefer CRM_REMOTE_USER or REMOTE_USER explicitly.
REMOTE_USER="${REMOTE_USER:-${CRM_REMOTE_USER:-bf68ec5}}"
KEY="${KEY:-data/id_rsa}"
REMOTE_ROOT="${REMOTE_ROOT:-/home/bf68ec5/crm}"

echo "== Deploy (min) -> ${REMOTE_USER}@${HOST}:${REMOTE_ROOT} =="

retry() {
  # retry N command...
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

# Common SSH/SCP options (port 2222 can reset sporadically).
SCP_OPTS=(-o IdentitiesOnly=yes -o ConnectTimeout=12 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -P "$PORT" -i "$KEY")
SSH_OPTS=(-p "$PORT" -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=12 -o ServerAliveInterval=30 -o ServerAliveCountMax=3)

echo "== Backend =="
retry 3 scp "${SCP_OPTS[@]}" \
  backend/main.py \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/backend/"

retry 3 scp "${SCP_OPTS[@]}" \
  backend/core/logging_setup.py \
  backend/core/email.py \
  backend/core/drive_assets.py \
  backend/core/quote_assets.py \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/backend/core/"

retry 3 scp "${SCP_OPTS[@]}" \
  backend/routers/assets.py \
  backend/routers/activity.py \
  backend/routers/auth.py \
  backend/routers/finanzas.py \
  backend/routers/leads.py \
  backend/routers/leads_agenda.py \
  backend/routers/notifications.py \
  backend/routers/productos.py \
  backend/routers/quotes_override.py \
  backend/routers/recetas.py \
  backend/routers/settings.py \
  backend/routers/settings_live.py \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/backend/routers/"

echo "== Web (shell + chat + operaciones + system notifs) =="
retry 3 scp "${SCP_OPTS[@]}" \
  web/index.html \
  web/styles.css \
  web/panel.css \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/web/"

retry 3 scp "${SCP_OPTS[@]}" \
  web/js/panel.js \
  web/js/cotizador.js \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/web/js/"

retry 3 ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${HOST}" \
  "mkdir -p '${REMOTE_ROOT}/web/js/settings'"

retry 3 scp "${SCP_OPTS[@]}" \
  web/js/settings/catalog.js \
  "${REMOTE_USER}@${HOST}:${REMOTE_ROOT}/web/js/settings/"

retry 3 scp "${SCP_OPTS[@]}" \
  web/views/chat.html \
  web/views/dashboard.html \
  web/views/finanzas.html \
  web/views/finanzas_gastos.html \
  web/views/finanzas_evento.html \
  web/views/finanzas_pl.html \
  web/views/leads.html \
  web/views/filtro_leads.html \
  web/views/operadores.html \
  web/views/op_maquinaria_categorias.html \
  web/views/op_maquinaria_inventario.html \
  web/views/op_maquinaria_ficha.html \
  web/views/op_camiones_ficha.html \
  web/views/op_camiones_entrega.html \
  web/views/op_camiones_devolucion.html \
  web/views/operaciones_carritos.html \
  web/views/operaciones_costeo.html \
  web/views/operaciones_mice.html \
  web/views/ruta.html \
  web/views/tools_mail.html \
  web/views/system_notifs.html \
  web/views/assets.html \
  web/views/operaciones_recetas.html \
  web/views/inventario_mercancia.html \
  web/views/inventario_carritos.html \
  web/views/settings.html \
  web/views/cotizador.html \
  web/views/calendar.html \
  web/views/calendar_ops.html \
  web/views/portal_ops.html \
  web/views/portal_terreno.html \
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

echo "== Restart Passenger =="
retry 3 ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${HOST}" \
  "touch '${REMOTE_ROOT}/tmp/restart.txt'"

echo "OK"
