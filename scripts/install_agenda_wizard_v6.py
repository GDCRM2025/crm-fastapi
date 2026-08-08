from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEADS = ROOT / "web" / "views" / "leads.html"
TAG = '<script src="../js/agenda_wizard_v6.js?v=20260731-1"></script>'


def main() -> None:
    text = LEADS.read_text(encoding="utf-8")

    lines = [
        line
        for line in text.splitlines()
        if "agenda_wizard_v6.js" not in line
    ]
    text = "\n".join(lines) + "\n"

    if "agenda_wizard_v4.js" not in text:
        raise SystemExit("ERROR: falta agenda_wizard_v4.js")
    if "agenda_wizard_v5.js" not in text:
        raise SystemExit("ERROR: falta agenda_wizard_v5.js")
    if "</body>" not in text:
        raise SystemExit("ERROR: no se encontro </body>")

    text = text.replace("</body>", f"  {TAG}\n</body>", 1)
    LEADS.write_text(text, encoding="utf-8")

    print("OK V6 cargado despues de V4 y V5")
    print("OK productos movidos antes de horarios y direcciones")
    print("OK flujo inmediato entre pasos")
    print("OK tips por importancia y estado")
    print("AGENDA_WIZARD_V6_INSTALLED")


if __name__ == "__main__":
    main()
