from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEADS = ROOT / "web" / "views" / "leads.html"
TAG = '<script src="../js/agenda_wizard_v5.js?v=20260730-1"></script>'


def main() -> None:
    text = LEADS.read_text(encoding="utf-8")

    lines = [
        line
        for line in text.splitlines()
        if "agenda_wizard_v5.js" not in line
    ]
    text = "\n".join(lines) + "\n"

    if "agenda_wizard_v4.js" not in text:
        raise SystemExit("ERROR: agenda_wizard_v4.js debe permanecer cargado antes de V5")
    if "</body>" not in text:
        raise SystemExit("ERROR: no se encontro </body> en leads.html")

    text = text.replace("</body>", f"  {TAG}\n</body>", 1)
    LEADS.write_text(text, encoding="utf-8")

    print("OK agenda_wizard_v5 include")
    print("OK multidia: horario y direccion independientes por dia")
    print("OK direccion principal del lead = direccion del Dia 1")
    print("AGENDA_WIZARD_V5_INSTALLED")
    print(TAG)


if __name__ == "__main__":
    main()
