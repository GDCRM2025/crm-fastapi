#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER="${CRM_INMOTION_SERVER:-bf68ec5@199.250.218.212}"
SSH_PORT="${CRM_INMOTION_PORT:-2222}"
REMOTE_ROOT="${CRM_INMOTION_ROOT:-/home/bf68ec5/crm}"
REMOTE_PY="${CRM_INMOTION_PY:-/home/bf68ec5/virtualenv/crm/3.10/bin/python}"
PUBLIC_HEALTH="${CRM_INMOTION_HEALTH:-https://greendiamond.cl/crm/openapi.json}"
STAMP="$(date +%Y%m%d_%H%M%S)"
SSH=(ssh -p "$SSH_PORT" "$SERVER")
RSYNC_SSH="ssh -p $SSH_PORT"

FILES=(
  backend/core/database.py
  backend/core/feature_flags.py
  backend/core/rbac.py
  backend/core/settings.py
  backend/core/whatsapp_window.py
  backend/routers/auth.py
  backend/routers/cotizador.py
  backend/routers/event_surveys.py
  backend/routers/features.py
  backend/routers/greeni_instagram.py
  backend/routers/leads.py
  backend/routers/leads_agenda.py
  backend/routers/permissions.py
  backend/routers/quotes_override.py
  backend/routers/whatsapp_commercial.py
  backend/routers/whatsapp_embedded_signup.py
  backend/routers/whatsapp_gia.py
  backend/routers/whatsapp_webhook.py
  web/login.html
  web/registrar.html
  web/views/historial_cotizaciones.html
  web/views/pdf_wait.html
  web/views/satisfaccion.html
  web/views/settings_features.html
  web/views/settings_whatsapp_coexistence.html
  scripts/audit_core_workflows.py
  scripts/cleanup_core_audit.py
  scripts/migrate_release_20260801.py
)

cd "$ROOT"
for file in "${FILES[@]}"; do
  test -f "$file" || { echo "Falta archivo local: $file" >&2; exit 1; }
done

python3 -m compileall -q backend scripts
while IFS= read -r -d '' file; do
  node --check "$file" >/dev/null
done < <(find web/js -type f -name '*.js' ! -name '*.min.js' ! -name '*.bak*' -print0)

echo "Creando respaldo remoto verificable..."
"${SSH[@]}" "set -euo pipefail; cd '$REMOTE_ROOT'; mkdir -p \"\$HOME/crm_backups/deploy\"; chmod 700 \"\$HOME/crm_backups\" \"\$HOME/crm_backups/deploy\"; PRESENT=(); for FILE in ${FILES[*]}; do test -e \"\$FILE\" && PRESENT+=(\"\$FILE\"); done; tar -czf \"\$HOME/crm_backups/deploy/pre_release_$STAMP.tar.gz\" \"\${PRESENT[@]}\"; chmod 600 \"\$HOME/crm_backups/deploy/pre_release_$STAMP.tar.gz\""

STAGE="/home/bf68ec5/crm_release_$STAMP"
rollback() {
  echo "Restaurando código anterior..." >&2
  "${SSH[@]}" "cd '$REMOTE_ROOT' && tar -xzf \"\$HOME/crm_backups/deploy/pre_release_$STAMP.tar.gz\" && mkdir -p tmp && touch tmp/restart.txt" || true
}
trap rollback ERR
"${SSH[@]}" "mkdir -p '$STAGE'"
for file in "${FILES[@]}"; do
  "${SSH[@]}" "mkdir -p '$STAGE/$(dirname "$file")'"
  rsync -az --checksum -e "$RSYNC_SSH" "$file" "$SERVER:$STAGE/$file"
done

"${SSH[@]}" "'$REMOTE_PY' -m compileall -q '$STAGE/backend' '$STAGE/scripts'"

echo "Instalando conjunto probado (sin borrar archivos remotos)..."
"${SSH[@]}" "cp -a '$STAGE'/backend '$STAGE'/web '$STAGE'/scripts '$REMOTE_ROOT'/"
"${SSH[@]}" "cd '$REMOTE_ROOT' && '$REMOTE_PY' scripts/migrate_release_20260801.py"
"${SSH[@]}" "mkdir -p '$REMOTE_ROOT/tmp' && touch '$REMOTE_ROOT/tmp/restart.txt'"

OK=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS --max-time 15 "$PUBLIC_HEALTH" >/dev/null; then
    OK=1
    break
  fi
  sleep 2
done

if [ "$OK" -ne 1 ]; then
  echo "Falló healthcheck; restaurando código anterior..." >&2
  false
fi

"${SSH[@]}" "rm -rf '$STAGE'"
trap - ERR
echo "INMOTION_RELEASE_OK=$STAMP"
echo "BACKUP=/home/bf68ec5/crm_backups/deploy/pre_release_$STAMP.tar.gz"
