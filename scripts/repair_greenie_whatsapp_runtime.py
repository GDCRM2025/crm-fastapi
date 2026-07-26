from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
WEBHOOK = ROOT / "backend/routers/whatsapp_webhook.py"
GIA = ROOT / "backend/routers/whatsapp_gia.py"
MAIN = ROOT / "backend/main.py"
TOOLS = ROOT / "web/views/tools.html"
WHATSAPP_VIEW = ROOT / "web/views/tools_whatsapp.html"
PANEL = ROOT / "web/js/panel.js"


def save(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    print("ACTUALIZADO:", path.relative_to(ROOT))


def replace_required(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        print("YA_OK:", label)
        return text
    if old not in text:
        raise SystemExit(f"ERROR: no se encontro ancla para {label}")
    return text.replace(old, new, 1)


# -----------------------------------------------------------------------------
# 1) Webhook: leer SIEMPRE las credenciales actuales desde /crm/.env.
#    Esto evita que Passenger conserve META_APP_SECRET antiguo en memoria/entorno.
# -----------------------------------------------------------------------------
webhook = WEBHOOK.read_text(encoding="utf-8")
webhook = replace_required(
    webhook,
    "import os\nfrom typing import Any\n",
    "import os\nfrom pathlib import Path\nfrom typing import Any\n\nfrom dotenv import dotenv_values\n",
    "imports runtime env webhook",
)

runtime_helper = '''\nBASE_DIR = Path(__file__).resolve().parents[2]\nENV_FILE = BASE_DIR / ".env"\n\n\ndef _runtime_env(name: str, default: str = "") -> str:\n    """Prefiere el .env del CRM sobre variables antiguas heredadas por Passenger."""\n    try:\n        value = dotenv_values(ENV_FILE).get(name)\n    except Exception:\n        value = None\n    if value is None or str(value).strip() == "":\n        value = os.environ.get(name, default)\n    return str(value or default).strip()\n\n'''
if "def _runtime_env(" not in webhook:
    webhook = webhook.replace(
        'log = logging.getLogger("crm")\n',
        'log = logging.getLogger("crm")\n' + runtime_helper,
        1,
    )
else:
    print("YA_OK: helper runtime env webhook")

start = webhook.index("def verify_signature(")
end = webhook.index("\ndef ensure_tables", start)
new_verify = '''def verify_signature(\n    raw_body: bytes,\n    signature_header: str | None,\n) -> bool:\n    """Valida X-Hub-Signature-256 usando la clave vigente de Greenie."""\n    validate = _runtime_env("WHATSAPP_VALIDATE_SIGNATURE", "true").lower() in (\n        "1", "true", "yes", "on"\n    )\n    if not validate:\n        return True\n\n    secrets: list[str] = []\n    current = _runtime_env("META_APP_SECRET")\n    if current:\n        secrets.append(current)\n    for candidate in _runtime_env("META_APP_SECRETS").split(","):\n        candidate = candidate.strip()\n        if candidate and candidate not in secrets:\n            secrets.append(candidate)\n\n    if not secrets:\n        log.error("[WHATSAPP] META_APP_SECRET no configurado")\n        return False\n    if not signature_header or not signature_header.startswith("sha256="):\n        return False\n\n    provided = signature_header.split("=", 1)[1].strip()\n    for secret in secrets:\n        expected = hmac.new(\n            secret.encode("utf-8"), raw_body, hashlib.sha256\n        ).hexdigest()\n        if hmac.compare_digest(expected, provided):\n            return True\n    return False\n\n'''
webhook = webhook[:start] + new_verify + webhook[end + 1:]
webhook = webhook.replace(
    "verify_token == WHATSAPP_WEBHOOK_VERIFY_TOKEN",
    'verify_token == _runtime_env("WHATSAPP_WEBHOOK_VERIFY_TOKEN", "CHANGE_ME")',
)
save(WEBHOOK, webhook)


# -----------------------------------------------------------------------------
# 2) GIA: token, Phone Number ID y WABA siempre desde el .env actual.
# -----------------------------------------------------------------------------
gia = GIA.read_text(encoding="utf-8")
if "from pathlib import Path" not in gia:
    gia = gia.replace("import os\n", "import os\nfrom pathlib import Path\n", 1)
if "from dotenv import dotenv_values" not in gia:
    insert_at = gia.find("from fastapi import ")
    gia = gia[:insert_at] + "from dotenv import dotenv_values\n" + gia[insert_at:]

gia_helper = '''\nBASE_DIR = Path(__file__).resolve().parents[2]\nENV_FILE = BASE_DIR / ".env"\n\n\ndef _runtime_env(name: str, default: str = "") -> str:\n    try:\n        value = dotenv_values(ENV_FILE).get(name)\n    except Exception:\n        value = None\n    if value is None or str(value).strip() == "":\n        value = os.environ.get(name, default)\n    return str(value or default).strip()\n\n'''
if "def _runtime_env(" not in gia:
    gia = gia.replace(
        'router = APIRouter(prefix="/gia/whatsapp", tags=["WhatsApp GIA"])\n',
        'router = APIRouter(prefix="/gia/whatsapp", tags=["WhatsApp Greenie"])\n' + gia_helper,
        1,
    )
else:
    print("YA_OK: helper runtime env GIA")

# Solo sustituye lecturas de configuración; _runtime_env usa os.environ para no recursar.
gia = re.sub(r'os\.getenv\("(WHATSAPP_[A-Z0-9_]+)",\s*"([^"]*)"\)', r'_runtime_env("\1", "\2")', gia)
gia = re.sub(r'os\.getenv\("(WHATSAPP_[A-Z0-9_]+)"\)', r'_runtime_env("\1")', gia)
save(GIA, gia)


# -----------------------------------------------------------------------------
# 3) Permitir micrófono same-origin para grabar notas de voz dentro del iframe.
# -----------------------------------------------------------------------------
main = MAIN.read_text(encoding="utf-8")
main = main.replace("microphone=(), camera=()", "microphone=(self), camera=()")
save(MAIN, main)


# -----------------------------------------------------------------------------
# 4) Romper caché de la vista nueva. tools.html era el wrapper que el menú abría.
# -----------------------------------------------------------------------------
tools = TOOLS.read_text(encoding="utf-8")
tools = re.sub(
    r'url:"/web/views/tools_whatsapp\.html(?:\?v=[^"]*)?"',
    'url:"/web/views/tools_whatsapp.html?v=20260726-greenie-final4"',
    tools,
)
save(TOOLS, tools)

panel = PANEL.read_text(encoding="utf-8")
panel = panel.replace(
    '/web/views/tools.html?v=20260327-tools7#whatsapp',
    '/web/views/tools.html?v=20260726-greenie-final4#whatsapp',
)
save(PANEL, panel)

view = WHATSAPP_VIEW.read_text(encoding="utf-8")
view = view.replace("<title>WhatsApp GIA</title>", "<title>WhatsApp Greenie</title>")
view = view.replace("<h1>WhatsApp GIA</h1>", "<h1>WhatsApp Greenie</h1>")
# El iframe necesita permiso explícito además de Permissions-Policy del servidor.
view = view.replace(
    '<meta name="viewport" content="width=device-width,initial-scale=1" />',
    '<meta name="viewport" content="width=device-width,initial-scale=1" />\n  <meta http-equiv="Cache-Control" content="no-store, no-cache, must-revalidate" />',
)
save(WHATSAPP_VIEW, view)

print("GREENIE_RUNTIME_REPAIR_OK")
