#!/usr/bin/env bash
set -euo pipefail

CLOUDFLARED_BIN="${CLOUDFLARED_BIN:-/home/oscar/.local/bin/cloudflared}"
TOKEN_FILE="${GREENIE_TUNNEL_TOKEN_FILE:-/home/oscar/.config/cloudflared/greenie.token}"

if [[ ! -x "$CLOUDFLARED_BIN" ]]; then
  echo "cloudflared no está instalado en $CLOUDFLARED_BIN" >&2
  exit 1
fi

if [[ ! -s "$TOKEN_FILE" ]]; then
  echo "Falta la credencial del túnel en $TOKEN_FILE" >&2
  exit 1
fi

exec "$CLOUDFLARED_BIN" tunnel --no-autoupdate run --token-file "$TOKEN_FILE"
