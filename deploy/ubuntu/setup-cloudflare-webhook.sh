#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="/opt/greendiamond/crm"
NGINX_SOURCE="${APP_DIR}/deploy/ubuntu/crm-webhook-only.nginx"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Ejecuta con sudo: sudo bash $0" >&2
  exit 1
fi

if [[ ! -f "${NGINX_SOURCE}" ]]; then
  echo "Falta ${NGINX_SOURCE}" >&2
  exit 1
fi

if ! command -v cloudflared >/dev/null 2>&1; then
  install -d -m 0755 /usr/share/keyrings
  curl --proto '=https' --tlsv1.2 -fsSL \
    https://pkg.cloudflare.com/cloudflare-main.gpg \
    -o /usr/share/keyrings/cloudflare-main.gpg
  echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' \
    > /etc/apt/sources.list.d/cloudflared.list
  apt-get update
  apt-get install -y cloudflared
fi

install -o root -g root -m 0644 "${NGINX_SOURCE}" /etc/nginx/sites-available/crm-webhook-only
ln -sfn /etc/nginx/sites-available/crm-webhook-only /etc/nginx/sites-enabled/crm-webhook-only
nginx -t
systemctl reload nginx

if [[ -z "${CLOUDFLARE_TUNNEL_TOKEN:-}" ]]; then
  read -rsp "Pega el token del túnel Cloudflare (no se mostrará): " CLOUDFLARE_TUNNEL_TOKEN
  echo
fi
if [[ -z "${CLOUDFLARE_TUNNEL_TOKEN}" ]]; then
  echo "Token vacío." >&2
  exit 1
fi

if systemctl list-unit-files cloudflared.service >/dev/null 2>&1; then
  systemctl disable --now cloudflared.service >/dev/null 2>&1 || true
  cloudflared service uninstall >/dev/null 2>&1 || true
fi

cloudflared service install "${CLOUDFLARE_TUNNEL_TOKEN}"
unset CLOUDFLARE_TUNNEL_TOKEN
systemctl enable --now cloudflared

curl -fsS --max-time 8 http://127.0.0.1:8081/not-allowed >/dev/null 2>&1 && {
  echo "ERROR: el proxy exclusivo expuso una ruta no autorizada." >&2
  exit 1
}
STATUS="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 8 \
  'http://127.0.0.1:8081/meta/webhook/whatsapp?hub.mode=subscribe&hub.verify_token=INVALID&hub.challenge=1')"
if [[ "${STATUS}" != "403" ]]; then
  echo "ERROR: validación local inesperada (${STATUS})." >&2
  exit 1
fi

echo "CLOUDFLARE_WEBHOOK_OK"
echo "Origen del hostname público: http://127.0.0.1:8081"
echo "Callback Meta: https://webhook.greendiamond.cl/meta/webhook/whatsapp"
