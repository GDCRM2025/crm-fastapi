from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "backend" / "main.py"


def main() -> None:
    text = MAIN.read_text(encoding="utf-8")
    line = 'include_router_safe(app, "backend.routers.whatsapp_embedded_signup")'

    if line not in text:
        anchor = 'include_router_safe(app, "backend.routers.whatsapp_webhook")'
        if anchor not in text:
            raise SystemExit("No se encontró el router whatsapp_webhook en backend/main.py")
        text = text.replace(anchor, anchor + "\n" + line, 1)

    MAIN.write_text(text, encoding="utf-8")
    print("WHATSAPP_EMBEDDED_CALLBACK_INSTALLED")


if __name__ == "__main__":
    main()
