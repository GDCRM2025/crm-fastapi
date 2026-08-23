import json
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

CRM_DIR = Path("/opt/greendiamond/crm")
ENV_FILE = CRM_DIR / ".env"
STATE_FILE = Path("/opt/greendiamond/backups/whatsapp_sync_state.json")

def read_env(key: str) -> str:
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == key:
            return v.strip().strip('"').strip("'")
    return ""

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}

def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))

token = read_env("WHATSAPP_WEBHOOK_VERIFY_TOKEN")
if not token:
    raise SystemExit("ERROR: WHATSAPP_WEBHOOK_VERIFY_TOKEN no existe en .env local")

state = load_state()
seen = set(state.get("seen_files") or [])

pull_url = "https://crm.greendiamond.cl/meta/webhook/whatsapp/pull.php?limit=100"

req = urllib.request.Request(
    pull_url,
    headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "CRM-GD-WhatsApp-Sync/1.0",
    },
    method="GET",
)

with urllib.request.urlopen(req, timeout=30) as resp:
    data = json.loads(resp.read().decode("utf-8"))

if not data.get("ok"):
    raise SystemExit(f"ERROR pull remoto: {data}")

events = data.get("events") or []
print(f"[{datetime.now().isoformat()}] Eventos remotos encontrados: {len(events)}")

imported = 0
failed = 0
skipped = 0

for ev in events:
    file_name = ev.get("file") or "unknown"

    if file_name in seen:
        skipped += 1
        continue

    raw = ev.get("raw") or ""
    headers = ev.get("headers") or {}
    signature = headers.get("signature") or ""

    if not raw:
        print(f"SKIP sin raw: {file_name}")
        seen.add(file_name)
        continue

    post_headers = {
        "Content-Type": "application/json",
        "User-Agent": "CRM-GD-WhatsApp-Sync/1.0",
    }

    if signature:
        post_headers["X-Hub-Signature-256"] = signature

    post_req = urllib.request.Request(
        "http://127.0.0.1:8000/meta/webhook/whatsapp",
        data=raw.encode("utf-8"),
        headers=post_headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(post_req, timeout=30) as post_resp:
            body = post_resp.read().decode("utf-8", errors="replace")
            print(f"OK {file_name} -> {post_resp.status} {body[:180]}")
            imported += 1
            seen.add(file_name)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"FAIL {file_name} -> HTTP {e.code} {body[:300]}")
        failed += 1
    except Exception as e:
        print(f"FAIL {file_name} -> {type(e).__name__}: {e}")
        failed += 1

state["seen_files"] = sorted(list(seen))[-1000:]
state["last_run"] = datetime.now().isoformat()
save_state(state)

print(f"Resultado: imported={imported} skipped={skipped} failed={failed}")
