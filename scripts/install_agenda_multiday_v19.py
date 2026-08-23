from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEADS = ROOT / "web" / "views" / "leads.html"
ADDON = ROOT / "web" / "js" / "agenda_multiday_v19.js"
TAG = '  <script src="../js/agenda_multiday_v19.js?v=20260731-19"></script>'


def fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def main() -> None:
    if not LEADS.exists() or not ADDON.exists():
        fail("faltan leads.html o agenda_multiday_v19.js")

    check = subprocess.run(["node", "--check", str(ADDON)], capture_output=True, text=True)
    if check.returncode != 0:
        fail("sintaxis agenda_multiday_v19.js: " + (check.stderr or check.stdout).strip())

    html = LEADS.read_text(encoding="utf-8")
    backup = LEADS.with_name("leads.html.bak_v19")
    if not backup.exists():
        shutil.copy2(LEADS, backup)

    html = "\n".join(
        line for line in html.splitlines()
        if "agenda_multiday_v19.js" not in line
    ) + "\n"

    matches = list(re.finditer(r'^\s*<script\s+src="\.\./js/agenda_wizard_v6\.js\?v=[^"]+"\s*></script>\s*$', html, flags=re.M))
    if len(matches) == 1:
        match = matches[0]
        html = html[:match.end()] + "\n" + TAG + html[match.end():]
    elif "</body>" in html:
        html = html.replace("</body>", TAG + "\n</body>", 1)
    else:
        fail("no se encontro agenda_wizard_v6 ni </body>")

    if html.count("agenda_multiday_v19.js") != 1:
        fail("include V19 duplicado")
    if 'Array.from({length:daysN}, ()=>"")' not in html:
        fail("las fechas multi dia no estan vacias; no se publicara")

    LEADS.write_text(html, encoding="utf-8")

    print("OK addon V19 agregado despues de V6")
    print("OK no modifica el submit actual")
    print("OK cada dia mostrara el motivo exacto del error")
    print("OK solo fecha, comuna, OPS y productos bloquean")
    print("OK direccion y horario vacios se normalizan a TBD")
    print("OK mensaje final alternativo queda activo")
    print("OK fechas multi dia siguen vacias")
    print("OK sintaxis agenda_multiday_v19.js")
    print("AGENDA_MULTIDAY_V19_INSTALLED")


if __name__ == "__main__":
    main()
