from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEADS = ROOT / "web" / "views" / "leads.html"
V4_TAG = '<script src="../js/agenda_wizard_v4.js?v=20260731-2"></script>'
V5_TAG = '<script src="../js/agenda_wizard_v5.js?v=20260731-2"></script>'


def main() -> None:
    text = LEADS.read_text(encoding="utf-8")

    text = re.sub(
        r'^\s*<script\s+src="\.\./js/agenda_wizard_v\d+\.js\?v=[^"]+"></script>\s*$',
        "",
        text,
        flags=re.M,
    )

    if "</body>" not in text:
        raise SystemExit("ERROR: no se encontro </body> en web/views/leads.html")

    text = text.replace(
        "</body>",
        f"  {V4_TAG}\n  {V5_TAG}\n</body>",
        1,
    )

    text, is_simple_count = re.subn(
        r"const\s+isSimple\s*=\s*\(!multiLoc\s*&&\s*!blocks(?:\s*&&\s*!multiDay)?\s*\);",
        "const isSimple = (!multiLoc && !blocks && !multiDay);",
        text,
    )

    text = text.replace(
        '        if (abMode === "oc" && !abOC){\n'
        '          Swal.showValidationMessage("Abono=0: falta OC / referencia.");\n'
        '          return false;\n'
        '        }\n',
        "",
    )
    text = text.replace(
        '      if (mode === "oc" && !oc) throw new Error("Falta OC / referencia (abono=0).");\n',
        "",
    )

    text = re.sub(r"\n{3,}", "\n\n", text)
    LEADS.write_text(text, encoding="utf-8")

    final = LEADS.read_text(encoding="utf-8")
    wizard_lines = [line.strip() for line in final.splitlines() if "agenda_wizard_v" in line]

    if wizard_lines != [V4_TAG, V5_TAG]:
        raise SystemExit(f"ERROR: includes incorrectos: {wizard_lines}")
    if "agenda_wizard_v3.js" in final:
        raise SystemExit("ERROR: V3 sigue cargado")
    if "const isSimple = (!multiLoc && !blocks && !multiDay);" not in final:
        raise SystemExit("ERROR: no se aplico la regla multidia")

    print(f"OK isSimple actualizado: {is_simple_count} ocurrencia(s)")
    print("OK V3 eliminado")
    print("OK V4 cargado antes de V5")
    print("OK OC sin numero obligatorio")
    print("AGENDA_WIZARD_V5_REPAIRED")


if __name__ == "__main__":
    main()
