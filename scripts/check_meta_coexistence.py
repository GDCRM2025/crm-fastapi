#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"


def value(name: str) -> str:
    file_value = ""
    try:
        file_value = str(dotenv_values(ENV_FILE).get(name) or "").strip()
    except Exception:
        pass
    return file_value or str(os.getenv(name) or "").strip()


def configured(name: str) -> bool:
    raw = value(name)
    lowered = raw.lower()
    return bool(
        raw
        and "replace-with" not in lowered
        and "example.com" not in lowered
        and lowered not in {"changeme", "change-me", "none", "null"}
    )


def main() -> int:
    checks: list[tuple[str, bool, str]] = []
    required = (
        "META_APP_ID",
        "META_APP_SECRET",
        "META_EMBEDDED_SIGNUP_CONFIG_ID",
        "WHATSAPP_WEBHOOK_VERIFY_TOKEN",
    )
    for name in required:
        checks.append((name, configured(name), "configurado" if configured(name) else "FALTA"))

    redirect = value("META_EMBEDDED_SIGNUP_REDIRECT_URI")
    parsed = urlparse(redirect)
    redirect_ok = (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and parsed.hostname not in {"localhost", "127.0.0.1"}
        and parsed.path.endswith("/meta/whatsapp/embedded-signup/callback")
    )
    checks.append(
        (
            "META_EMBEDDED_SIGNUP_REDIRECT_URI",
            redirect_ok,
            redirect if redirect_ok else "debe ser HTTPS público y terminar en /meta/whatsapp/embedded-signup/callback",
        )
    )
    state_secret_ok = any(
        configured(name)
        for name in (
            "META_EMBEDDED_SIGNUP_STATE_SECRET",
            "JWT_SECRET",
            "META_APP_SECRET",
        )
    )
    checks.append(
        (
            "Firma de estado OAuth",
            state_secret_ok,
            "configurada" if state_secret_ok else "falta META_EMBEDDED_SIGNUP_STATE_SECRET, JWT_SECRET o META_APP_SECRET",
        )
    )
    state_required = (value("META_EMBEDDED_SIGNUP_REQUIRE_STATE") or "1").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    checks.append(
        (
            "META_EMBEDDED_SIGNUP_REQUIRE_STATE",
            state_required,
            "activo" if state_required else "debe quedar en 1",
        )
    )
    signature_required = value("WHATSAPP_VALIDATE_SIGNATURE").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    checks.append(
        (
            "WHATSAPP_VALIDATE_SIGNATURE",
            signature_required,
            "activo" if signature_required else "debe quedar en true",
        )
    )

    print("META / WHATSAPP COEXISTENCIA")
    print("=" * 32)
    for name, ok, detail in checks:
        print(f"[{'OK' if ok else 'PENDIENTE'}] {name}: {detail}")
    pending = [name for name, ok, _ in checks if not ok]
    if pending:
        print(f"\nNO LISTO: {len(pending)} pendiente(s).")
        return 1
    print("\nLISTO PARA PRUEBA CONTROLADA EN META.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
