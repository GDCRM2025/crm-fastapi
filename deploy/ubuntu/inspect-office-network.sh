#!/usr/bin/env bash
set -Eeuo pipefail

echo "=== RED LOCAL DEL SERVIDOR ==="
ip -br -4 address
echo
echo "=== RUTA Y GATEWAY ==="
ip -4 route
echo
echo "=== DNS ==="
resolvectl status 2>/dev/null | sed -n '1,100p' || true
echo
echo "=== IPV4 VISTA DESDE INTERNET ==="
PUBLIC_IPV4="$(curl --proto '=https' --tlsv1.2 -4 -fsS --max-time 10 https://api.ipify.org || true)"
if [[ -n "${PUBLIC_IPV4}" ]]; then
  echo "PUBLIC_IPV4=${PUBLIC_IPV4}"
else
  echo "PUBLIC_IPV4=NO_DISPONIBLE"
fi
echo
echo "IMPORTANTE: entra al router Entel y anota su dirección WAN/Internet."
echo "Si WAN coincide con PUBLIC_IPV4, probablemente existe IPv4 pública directa."
echo "Si WAN es privada o no coincide, puede existir CGNAT o doble NAT."
echo "No cambies la IP del servidor todavía."
