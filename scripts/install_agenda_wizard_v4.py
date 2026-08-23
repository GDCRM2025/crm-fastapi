from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEADS = ROOT / "web" / "views" / "leads.html"
TAG = '<script src="../js/agenda_wizard_v4.js?v=20260730-1"></script>'


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        if new in text:
            print(f"OK {label} ya aplicado")
            return text
        raise SystemExit(f"ERROR: no se encontro bloque para {label}")
    print(f"OK {label}")
    return text.replace(old, new, 1)


def main() -> None:
    text = LEADS.read_text(encoding="utf-8")

    # Solo una capa visual del wizard.
    lines = [
        line
        for line in text.splitlines()
        if "agenda_wizard_v2.js" not in line
        and "agenda_wizard_v3.js" not in line
        and "agenda_wizard_v4.js" not in line
    ]
    text = "\n".join(lines) + "\n"

    if "</body>" not in text:
        raise SystemExit("ERROR: no se encontro </body> en leads.html")
    text = text.replace("</body>", f"  {TAG}\n</body>", 1)
    print("OK include agenda_wizard_v4")

    # Varios dias no es un evento simple: debe conservar un segmento por dia.
    text = replace_once(
        text,
        '          const isSimple = (!multiLoc && !blocks);',
        '          const isSimple = (!multiLoc && !blocks && !multiDay);',
        "multidia usa segmentos por dia",
    )

    # En el flujo final, cada dia conserva sus grupos y no usa horas globales.
    text = replace_once(
        text,
        '              const isSimple = (!multiLoc && !blocks);',
        '              const isSimple = (!multiLoc && !blocks && !multiDay);',
        "multidia final usa segmentos por dia",
    )

    # OC puede seleccionarse aunque el cliente aun no entregue su numero.
    text = text.replace(
        '        if (abMode === "oc" && !abOC){\n'
        '          Swal.showValidationMessage("Abono=0: falta OC / referencia.");\n'
        '          return false;\n'
        '        }\n',
        '',
    )
    text = text.replace(
        '      if (mode === "oc" && !oc) throw new Error("Falta OC / referencia (abono=0).");\n',
        '',
    )
    print("OK OC sin numero obligatorio")

    LEADS.write_text(text, encoding="utf-8")
    print("AGENDA_WIZARD_V4_INSTALLED")
    print(TAG)


if __name__ == "__main__":
    main()
