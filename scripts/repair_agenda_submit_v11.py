from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEADS = ROOT / "web" / "views" / "leads.html"


def main() -> None:
    text = LEADS.read_text(encoding="utf-8")

    pattern = re.compile(
        r'(?P<indent>[ \t]*)const hrTbd = !!qs\("#ag_hrtbd"\)\?\.checked \|\| !hIni \|\| !hFin;\n'
        r'(?P=indent)const hIni = \(qs\("#ag_hini"\)\?\.value \|\| ""\)\.trim\(\);\n'
        r'(?P=indent)const hFin = \(qs\("#ag_hfin"\)\?\.value \|\| ""\)\.trim\(\);'
    )

    def reorder(match: re.Match[str]) -> str:
        indent = match.group("indent")
        return (
            f'{indent}const hIni = (qs("#ag_hini")?.value || "").trim();\n'
            f'{indent}const hFin = (qs("#ag_hfin")?.value || "").trim();\n'
            f'{indent}const hrTbd = !!qs("#ag_hrtbd")?.checked || !hIni || !hFin;'
        )

    text, reordered = pattern.subn(reorder, text)

    # Elimina cualquier validación residual que exija número de OC.
    text = re.sub(
        r'\s*if \(abMode === "oc" && !abOC\)\s*\{\s*Swal\.showValidationMessage\("Abono=0: falta OC / referencia\."\);\s*return false;\s*\}',
        "",
        text,
        flags=re.S,
    )
    text = re.sub(
        r'\s*if \(mode === "oc" && !oc\) throw new Error\("Falta OC / referencia \(abono=0\)\."\);',
        "",
        text,
    )

    # Verificación dura: dentro de cada bloque cercano, hIni/hFin deben declararse antes de hrTbd.
    bad = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if 'const hrTbd = !!qs("#ag_hrtbd")?.checked || !hIni || !hFin;' not in line:
            continue
        window = lines[max(0, index - 4):index]
        has_hini = any('const hIni = (qs("#ag_hini")?.value || "").trim();' in item for item in window)
        has_hfin = any('const hFin = (qs("#ag_hfin")?.value || "").trim();' in item for item in window)
        if not (has_hini and has_hfin):
            bad.append(index + 1)

    if bad:
        raise SystemExit("ERROR: hrTbd sigue antes de hIni/hFin en líneas: " + ", ".join(map(str, bad)))

    forbidden = [
        "Abono=0: falta OC / referencia.",
        "Falta OC / referencia (abono=0).",
    ]
    remaining = [item for item in forbidden if item in text]
    if remaining:
        raise SystemExit("ERROR: validación OC residual: " + ", ".join(remaining))

    LEADS.write_text(text, encoding="utf-8")

    print(f"OK bloques reordenados: {reordered}")
    print("OK hIni/hFin declarados antes de hrTbd")
    print("OK OC sin referencia obligatoria")
    print("OK preConfirm listo para ejecutar move + Calendar")
    print("AGENDA_SUBMIT_V11_REPAIRED")


if __name__ == "__main__":
    main()
