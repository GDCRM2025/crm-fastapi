#!/bin/zsh
set -euo pipefail

SERVER="oscar@192.168.100.51"
DB_NAME="bf68ec5_crm2025b"
DB_ROLE="joaquin_analytics"
OUTPUT_FILE="/Users/oscarmendoza/Desktop/CRM 2025/Entrega_Joaquin_WireGuard/CLAVE_DBEAVER_JOAQUIN.txt"

NEW_PASSWORD="$(/usr/bin/openssl rand -hex 20)A1"

echo "Green Diamond - restablecer clave DBeaver de Joaquin"
echo "Se solicitara la clave sudo del servidor una sola vez."
echo

/usr/bin/ssh -t "$SERVER" \
  "sudo -u postgres psql -d '$DB_NAME' -v ON_ERROR_STOP=1 -c \"ALTER ROLE $DB_ROLE PASSWORD '$NEW_PASSWORD';\""

/bin/mkdir -p "${OUTPUT_FILE:h}"
umask 077
/usr/bin/printf '%s\n' \
  "CONEXION DBEAVER JOAQUIN" \
  "Host PostgreSQL: 127.0.0.1" \
  "Puerto PostgreSQL: 5432" \
  "Base de datos: $DB_NAME" \
  "Usuario: $DB_ROLE" \
  "Contrasena: $NEW_PASSWORD" \
  "SSH host: 10.77.0.1" \
  "SSH puerto: 22" \
  "SSH usuario: oscar" \
  "SSH llave: joaquin-dbeaver-ed25519" \
  > "$OUTPUT_FILE"
/bin/chmod 600 "$OUTPUT_FILE"

echo
echo "Clave actualizada correctamente."
echo "Quedo guardada solo en:"
echo "$OUTPUT_FILE"
echo "No envies esta clave por correo ni la subas al repositorio."
