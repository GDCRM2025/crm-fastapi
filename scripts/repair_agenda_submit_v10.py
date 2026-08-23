from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEADS = ROOT / "web" / "views" / "leads.html"


def main() -> None:
    text = LEADS.read_text(encoding="utf-8")

    # V9 podía dejar hrTbd usando hIni/hFin antes de declararlos.
    # Eso genera ReferenceError dentro de preConfirm y detiene todo antes
    # de mover el lead o llamar a Google Calendar.
    pattern = re.compile(
        r'''(?P<indent>\s*)const hrTbd = !!qs\("#ag_hrtbd"\)\?\.checked \|\| !hIni \|\| !hFin;\s*\n'''
        r'''(?P=indent)const hIni = \(qs\("#ag_hini"\)\?\.value \|\| ""\)\.trim\(\);\s*\n'''
        r'''(?P=indent)const hFin = \(qs\("#ag_hfin"\)\?\.value \|\| ""\)\.trim\(\);'''
    )
    replacement = (
        r'''\g<indent>const hIni = (qs("#ag_hini")?.value || "").trim();\n'''
        r'''\g<indent>const hFin = (qs("#ag_hfin")?.value || "").trim();\n'''
        r'''\g<indent>const hrTbd = !!qs("#ag_hrtbd")?.checked || !hIni || !hFin;'''
    )
    text, count = pattern.subn(replacement, text, count=1)

    if count == 0:
        ordered = (
            'const hIni = (qs("#ag_hini")?.value || "").trim();\n'
            '      const hFin = (qs("#ag_hfin")?.value || "").trim();\n'
            '      const hrTbd = !!qs("#ag_hrtbd")?.checked || !hIni || !hFin;'
        )
        if ordered not in text:
            raise SystemExit("ERROR: no se encontro el bloque hIni/hFin/hrTbd")
        print("OK orden hIni/hFin/hrTbd ya corregido")
    else:
        print("OK corregido ReferenceError hIni/hFin/hrTbd")

    # El número de OC sigue siendo opcional también en la validación final.
    text = re.sub(
        r'''\s*if \(mode === "oc" && !oc\) throw new Error\("Falta OC / referencia \(abono=0\)\."\);''',
        "",
        text,
        count=1,
    )
    text = re.sub(
        r'''\s*if \(abMode === "oc" && !abOC\)\{\s*Swal\.showValidationMessage\("Abono=0: falta OC / referencia\."\);\s*return false;\s*\}''',
        "",
        text,
        count=1,
        flags=re.S,
    )

    # Cache-buster del HTML.
    text = re.sub(
        r'agenda_wizard_v4\.js\?v=[^"\']+',
        'agenda_wizard_v4.js?v=20260731-10',
        text,
    )
    text = re.sub(
        r'agenda_wizard_v6\.js\?v=[^"\']+',
        'agenda_wizard_v6.js?v=20260731-10',
        text,
    )

    if "Abono=0: falta OC / referencia" in text or "Falta OC / referencia (abono=0)" in text:
        raise SystemExit("ERROR: sigue presente una validacion obligatoria de OC")

    # Comprobación de orden real.
    pos_hini = text.find('const hIni = (qs("#ag_hini")?.value || "").trim();')
    pos_hfin = text.find('const hFin = (qs("#ag_hfin")?.value || "").trim();', pos_hini)
    pos_tbd = text.find('const hrTbd = !!qs("#ag_hrtbd")?.checked || !hIni || !hFin;', pos_hfin)
    if min(pos_hini, pos_hfin, pos_tbd) < 0 or not (pos_hini < pos_hfin < pos_tbd):
        raise SystemExit("ERROR: orden de variables no valido")

    LEADS.write_text(text, encoding="utf-8")

    print("OK OC sin referencia obligatoria")
    print("OK preConfirm puede devolver payload")
    print("OK flujo move + Calendar vuelve a ejecutarse")
    print("AGENDA_SUBMIT_V10_REPAIRED")


if __name__ == "__main__":
    main()
