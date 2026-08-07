#!/usr/bin/env bash
set -euo pipefail

CRM_ROOT="${GREENIE_CRM_ROOT:-/opt/greendiamond/crm}"
ENV_FILE="$CRM_ROOT/.env"
PYTHON_BIN="${GREENIE_PYTHON_BIN:-/opt/greendiamond/venv/bin/python3}"
PHONE_NUMBER_ID="${WHATSAPP_PHONE_NUMBER_ID:-1143679835506305}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "No se encontró $ENV_FILE" >&2
  exit 1
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "No se encontró Python en $PYTHON_BIN" >&2
  exit 1
fi

read -r -s -p "Pega el token nuevo de Meta y presiona Enter: " META_TOKEN_INPUT
echo

if [[ ${#META_TOKEN_INPUT} -lt 80 ]]; then
  echo "El token parece incompleto. No se modificó el CRM." >&2
  exit 1
fi

export META_TOKEN_INPUT ENV_FILE PHONE_NUMBER_ID
"$PYTHON_BIN" - <<'PY'
from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

env_file = Path(os.environ["ENV_FILE"])
token = os.environ["META_TOKEN_INPUT"].strip()
phone_number_id = os.environ["PHONE_NUMBER_ID"].strip()

url = (
    f"https://graph.facebook.com/v25.0/{phone_number_id}?"
    + urllib.parse.urlencode({"access_token": token})
)
try:
    with urllib.request.urlopen(url, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
except urllib.error.HTTPError as exc:
    try:
        detail = json.loads(exc.read().decode("utf-8")).get("error", {})
        message = detail.get("message") or f"HTTP {exc.code}"
    except Exception:
        message = f"HTTP {exc.code}"
    raise SystemExit(f"Meta rechazó el token: {message}. No se modificó el CRM.")

if str(payload.get("id") or "") != phone_number_id:
    raise SystemExit("El token no tiene acceso al número de prueba. No se modificó el CRM.")

original = env_file.read_text(encoding="utf-8")
lines = original.splitlines()
replacement = f"WHATSAPP_ACCESS_TOKEN={token}"
updated: list[str] = []
found = False
for line in lines:
    if line.startswith("WHATSAPP_ACCESS_TOKEN="):
        updated.append(replacement)
        found = True
    else:
        updated.append(line)
if not found:
    updated.append(replacement)

backup = env_file.with_name(
    f".env.backup-token-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
)
shutil.copy2(env_file, backup)

with tempfile.NamedTemporaryFile(
    mode="w",
    encoding="utf-8",
    dir=env_file.parent,
    prefix=".env.token-",
    delete=False,
) as handle:
    handle.write("\n".join(updated) + "\n")
    temporary = Path(handle.name)
temporary.chmod(env_file.stat().st_mode & 0o777)
temporary.replace(env_file)
print("Token validado y guardado de forma segura.")
print(f"Respaldo creado: {backup.name}")
PY
unset META_TOKEN_INPUT

sudo systemctl restart crm-gd.service
sleep 2
if [[ "$(systemctl is-active crm-gd.service)" != "active" ]]; then
  echo "El servicio no quedó activo. Revisa crm-gd.service." >&2
  exit 1
fi

echo "CRM reiniciado. Token de WhatsApp operativo."
