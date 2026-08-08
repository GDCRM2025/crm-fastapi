#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Ejecuta con sudo: sudo bash $0" >&2
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  apt-get update
  apt-get install -y ca-certificates curl
fi

if ! command -v netbird >/dev/null 2>&1; then
  curl --proto '=https' --tlsv1.2 -fsSL https://pkgs.netbird.io/install.sh | sh
fi

if [[ -z "${NETBIRD_SETUP_KEY:-}" ]]; then
  read -rsp "Pega la Setup Key de un solo uso para CRM-SERVIDOR: " NETBIRD_SETUP_KEY
  echo
fi

if [[ -z "${NETBIRD_SETUP_KEY}" ]]; then
  echo "La Setup Key está vacía." >&2
  exit 1
fi

netbird up --setup-key "${NETBIRD_SETUP_KEY}"
unset NETBIRD_SETUP_KEY

systemctl enable --now netbird
netbird status

NETBIRD_IP="$(ip -4 -o addr show wt0 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1)"
if [[ -z "${NETBIRD_IP}" ]]; then
  echo "ERROR: NetBird no asignó una IP al servidor." >&2
  exit 1
fi

if ! curl -fsS --max-time 10 "http://${NETBIRD_IP}/crm/" >/dev/null; then
  echo "ERROR: el CRM no responde por la interfaz NetBird (${NETBIRD_IP})." >&2
  exit 1
fi

echo "NETBIRD_CRM_OK"
echo "URL_CRM=http://${NETBIRD_IP}/crm/"
echo "Guarda esta URL en el manual y comprueba el acceso usando datos móviles."
