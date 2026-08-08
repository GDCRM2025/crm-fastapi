#!/usr/bin/env python3

import getpass
import json
import re
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
DEFAULT_OUT = ROOT / "gia_email_accounts.json"


def _read_env_text() -> str:
    try:
        return ENV_PATH.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _write_env_text(txt: str) -> None:
    ENV_PATH.write_text(txt, encoding="utf-8")


def _strip_env_var(txt: str, var: str) -> str:
    # remove any line starting with VAR=
    return re.sub(rf"(?m)^\\s*{re.escape(var)}\\s*=.*(?:\\n|$)", "", txt)


def _upsert_env_var(txt: str, var: str, value: str) -> str:
    txt = _strip_env_var(txt, var)
    txt = txt.rstrip() + ("\n" if txt.strip() else "")
    return txt + f"{var}={value}\n"


def _load_existing_accounts() -> List[Dict[str, Any]]:
    # Prefer existing accounts file if referenced
    txt = _read_env_text()
    m = re.search(r"(?m)^\\s*GIA_EMAIL_ACCOUNTS_PATH\\s*=\\s*(.+)$", txt)
    if m:
        raw = m.group(1).strip().strip('"').strip("'")
        p = (ROOT / raw).resolve() if not Path(raw).is_absolute() else Path(raw)
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
                if isinstance(data, dict):
                    data = [data]
                if isinstance(data, list):
                    return [x for x in data if isinstance(x, dict)]
            except Exception:
                pass

    # Try env var (single-line only)
    m = re.search(r"(?m)^\\s*GIA_EMAIL_ACCOUNTS_JSON\\s*=\\s*(.+)$", txt)
    if m:
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                data = [data]
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict)]
        except Exception:
            pass
    return []


def _prompt_password(label: str, current: str) -> str:
    prompt = f"Password IMAP/SMTP para {label} (enter para mantener): "
    pw = getpass.getpass(prompt)
    pw = pw.strip()
    return current if not pw else pw


def main() -> None:
    accounts = _load_existing_accounts()
    if not accounts:
        print("No encontré cuentas existentes. Se creará un archivo nuevo.")
        accounts = []

    # Normalize minimal schema + ask passwords
    seen = set()
    out: List[Dict[str, Any]] = []
    for a in accounts:
        marca = str(a.get("marca") or "").strip()
        inbox_type = str(a.get("inbox_type") or "sales").strip().lower()
        if inbox_type not in ("sales", "payments"):
            inbox_type = "sales"
        from_email = str(a.get("from_email") or a.get("email") or a.get("username") or "").strip()
        username = str(a.get("username") or from_email or "").strip()
        imap_host = str(a.get("imap_host") or "").strip()
        smtp_host = str(a.get("smtp_host") or "").strip()
        if not (marca and from_email and username and imap_host and smtp_host):
            continue
        key = (marca, inbox_type, from_email.lower())
        if key in seen:
            continue
        seen.add(key)
        current_pw = str(a.get("password") or "").strip()
        pw = _prompt_password(f"{marca} ({inbox_type}) {from_email}", current_pw)
        out.append(
            {
                "marca": marca,
                "inbox_type": inbox_type,
                "from_email": from_email,
                "username": username,
                "password": pw,
                "imap_host": imap_host,
                "imap_port": int(a.get("imap_port") or 993),
                "imap_ssl": bool(a.get("imap_ssl", True)),
                "imap_folder": str(a.get("imap_folder") or "INBOX"),
                "smtp_host": smtp_host,
                "smtp_port": int(a.get("smtp_port") or 465),
                "smtp_ssl": bool(a.get("smtp_ssl", True)),
            }
        )

    if not out:
        print("No hay cuentas válidas para escribir. Tip: copia `tools/gia_email_accounts.example.json` a `gia_email_accounts.json`.")
        raise SystemExit(2)

    # Write pretty JSON
    DEFAULT_OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK: escrito {DEFAULT_OUT}")

    # Update .env to reference the file, and remove giant JSON lines (si existen)
    txt = _read_env_text()
    txt = _strip_env_var(txt, "GIA_EMAIL_ACCOUNTS_JSON")
    txt = _strip_env_var(txt, "GIA_EMAIL_ACCOUNTS")
    txt = _upsert_env_var(txt, "GIA_EMAIL_ACCOUNTS_PATH", str(DEFAULT_OUT.name))
    _write_env_text(txt)
    print("OK: .env actualizado (GIA_EMAIL_ACCOUNTS_PATH)")

    # Reminder
    print("Siguiente paso: reinicia Passenger con `touch tmp/restart.txt`.")


if __name__ == "__main__":
    main()
