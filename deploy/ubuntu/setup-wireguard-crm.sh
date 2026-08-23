#!/usr/bin/env bash
set -Eeuo pipefail

WG_INTERFACE="wg0"
WG_NETWORK_ADDRESS="10.77.0.1/24"
WG_LISTEN_PORT="51820"
WG_ENDPOINT="${WG_ENDPOINT:-181.43.135.236:${WG_LISTEN_PORT}}"
CLIENT_NAME="${CLIENT_NAME:-gourmet-piloto-01}"
CLIENT_ADDRESS="${CLIENT_ADDRESS:-10.77.0.2/32}"
WG_DIR="/etc/wireguard"
CLIENT_EXPORT_DIR="/home/oscar/vpn-clients"
SERVER_PRIVATE_KEY="${WG_DIR}/server.key"
SERVER_PUBLIC_KEY="${WG_DIR}/server.pub"
CLIENT_CONFIG="${CLIENT_EXPORT_DIR}/${CLIENT_NAME}.conf"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Ejecuta este script con sudo." >&2
  exit 1
fi

if [[ ! "${CLIENT_NAME}" =~ ^[a-zA-Z0-9_-]+$ ]]; then
  echo "CLIENT_NAME contiene caracteres no permitidos." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y wireguard-tools

install -d -o root -g root -m 0700 "${WG_DIR}"
install -d -o oscar -g oscar -m 0700 "${CLIENT_EXPORT_DIR}"

if [[ ! -s "${SERVER_PRIVATE_KEY}" ]]; then
  umask 077
  wg genkey > "${SERVER_PRIVATE_KEY}"
  wg pubkey < "${SERVER_PRIVATE_KEY}" > "${SERVER_PUBLIC_KEY}"
fi

if [[ ! -s "${CLIENT_CONFIG}" ]]; then
  CLIENT_PRIVATE="$(wg genkey)"
  CLIENT_PUBLIC="$(printf '%s' "${CLIENT_PRIVATE}" | wg pubkey)"
  SERVER_PUBLIC="$(cat "${SERVER_PUBLIC_KEY}")"

  cat > "${CLIENT_CONFIG}" <<EOF
[Interface]
PrivateKey = ${CLIENT_PRIVATE}
Address = ${CLIENT_ADDRESS}

[Peer]
PublicKey = ${SERVER_PUBLIC}
Endpoint = ${WG_ENDPOINT}
AllowedIPs = 10.77.0.1/32
PersistentKeepalive = 25
EOF
  chown oscar:oscar "${CLIENT_CONFIG}"
  chmod 0600 "${CLIENT_CONFIG}"
else
  CLIENT_PRIVATE="$(sed -n 's/^PrivateKey = //p' "${CLIENT_CONFIG}")"
  CLIENT_PUBLIC="$(printf '%s' "${CLIENT_PRIVATE}" | wg pubkey)"
fi

cat > "${WG_DIR}/${WG_INTERFACE}.conf" <<EOF
[Interface]
Address = ${WG_NETWORK_ADDRESS}
ListenPort = ${WG_LISTEN_PORT}
PrivateKey = $(cat "${SERVER_PRIVATE_KEY}")
PostUp = iptables -I INPUT 1 -i %i -j REJECT
PostUp = iptables -I INPUT 1 -i %i -p tcp -m multiport --dports 80,443 -j ACCEPT
PostDown = iptables -D INPUT -i %i -p tcp -m multiport --dports 80,443 -j ACCEPT
PostDown = iptables -D INPUT -i %i -j REJECT

[Peer]
# Gourmet: dispositivo piloto 01
PublicKey = ${CLIENT_PUBLIC}
AllowedIPs = ${CLIENT_ADDRESS}
EOF
chmod 0600 "${WG_DIR}/${WG_INTERFACE}.conf" "${SERVER_PRIVATE_KEY}" "${SERVER_PUBLIC_KEY}"

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q '^Status: active'; then
  ufw allow "${WG_LISTEN_PORT}/udp" comment "WireGuard CRM"
fi

systemctl enable --now "wg-quick@${WG_INTERFACE}"

echo "WIREGUARD_OK"
echo "Perfil Gourmet: ${CLIENT_CONFIG}"
echo "URL VPN del CRM: http://10.77.0.1/crm/"
echo "Router pendiente: UDP ${WG_LISTEN_PORT} -> 192.168.10.51:${WG_LISTEN_PORT}"
