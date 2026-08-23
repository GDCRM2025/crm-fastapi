#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Ejecuta con sudo: sudo bash $0" >&2
  exit 1
fi

if ! command -v tailscale >/dev/null 2>&1; then
  echo "Instalando Tailscale desde su instalador oficial..."
  curl --proto '=https' --tlsv1.2 -fsSL https://tailscale.com/install.sh | sh
fi

systemctl enable --now tailscaled

echo "Autenticando el servidor. Abre la URL que aparecerá y usa la cuenta administradora."
tailscale up \
  --hostname=crm-gd \
  --advertise-tags=tag:crm-server \
  --accept-dns=true \
  --accept-routes=false \
  --advertise-exit-node=false \
  --ssh=false

TS_IP="$(tailscale ip -4 | head -n1)"
if [[ -z "${TS_IP}" ]]; then
  echo "ERROR: Tailscale no asignó una IPv4." >&2
  exit 1
fi

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q '^Status: active'; then
  ufw allow in on tailscale0 to any port 80 proto tcp comment 'CRM por Tailscale'
fi

curl -fsS --max-time 8 http://127.0.0.1:8000/openapi.json >/dev/null
curl -fsS --max-time 8 "http://${TS_IP}/crm/openapi.json" >/dev/null

echo "TAILSCALE_CRM_OK"
echo "IP_TAILSCALE=${TS_IP}"
echo "URL_CRM=http://${TS_IP}/crm/"
echo "No compartas esta URL sin autorizar primero al usuario/dispositivo en Tailscale."
