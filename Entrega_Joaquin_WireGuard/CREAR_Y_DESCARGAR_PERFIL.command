#!/bin/zsh
set -euo pipefail

SERVER="oscar@192.168.100.51"
PROFILE_NAME="joaquin-control-01"
PROFILE_REMOTE="/home/oscar/vpn-clients/${PROFILE_NAME}.conf"
SCRIPT_DIR="${0:A:h}"
PROFILE_LOCAL="${SCRIPT_DIR}/${PROFILE_NAME}.conf"
DBEAVER_KEY="${SCRIPT_DIR}/joaquin-dbeaver-ed25519"

echo "Green Diamond - acceso Joaquin"
echo "Se solicitara la clave sudo del servidor una sola vez."
echo

ssh -t "${SERVER}" "set -e
  sudo -v
  sudo -u postgres psql bf68ec5_crm2025b -v ON_ERROR_STOP=1 -f /opt/greendiamond/current/scripts/joaquin_analytics_views.sql
  if sudo test -f '${PROFILE_REMOTE}'; then
    echo 'El perfil WireGuard ya estaba generado y pendiente de entrega.'
  else
    sudo env WG_ENDPOINT=181.43.135.236:51820 bash /opt/greendiamond/current/deploy/ubuntu/crm-vpn-peer add '${PROFILE_NAME}'
  fi
  sudo chown oscar:oscar '${PROFILE_REMOTE}'
  sudo chmod 600 '${PROFILE_REMOTE}'
"

scp "${SERVER}:${PROFILE_REMOTE}" "${PROFILE_LOCAL}"
chmod 600 "${PROFILE_LOCAL}"

if [[ ! -f "${DBEAVER_KEY}" ]]; then
  echo "FALTA: ${DBEAVER_KEY}"
  echo "La VPN funcionara, pero DBeaver no podra abrir su tunel SSH."
  exit 1
fi

echo
echo "LISTO: ${PROFILE_LOCAL}"
echo "LISTO: ${DBEAVER_KEY} (llave restringida para DBeaver)"
echo "Copia la carpeta Entrega_Joaquin_WireGuard al pendrive."
echo "No envies el perfil por correo ni WhatsApp: contiene una clave privada."
echo
read "?Presiona Enter para cerrar..."
