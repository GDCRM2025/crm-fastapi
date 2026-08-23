from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEADS = ROOT / "web" / "views" / "leads.html"
WIZARD = ROOT / "web" / "js" / "agenda_wizard_v4.js"

V4 = '  <script src="../js/agenda_wizard_v4.js?v=20260731-8b"></script>'
V6 = '  <script src="../js/agenda_wizard_v6.js?v=20260731-8b"></script>'


def main():
    js = WIZARD.read_text(encoding="utf-8")
    if 'next.textContent = "Siguiente";' not in js:
        js = js.replace('next.textContent = "Guardar y continuar";', 'next.textContent = "Siguiente";')
    js = js.replace('confirm.textContent = "Confirmar agendamiento";', 'confirm.textContent = "Guardar y agendar";')
    js = js.replace('confirm.textContent = "Agendando…";', 'confirm.textContent = "Guardando…";')
    js = js.replace('html.scrollTo({ top: 0, behavior: "smooth" })', 'html.scrollTo({ top: 0, behavior: "auto" })')
    WIZARD.write_text(js, encoding="utf-8")

    html = LEADS.read_text(encoding="utf-8")
    lines = [line for line in html.splitlines() if "agenda_wizard_v" not in line]
    html = "\n".join(lines) + "\n"
    if "</body>" not in html:
        raise SystemExit("ERROR: no se encontro </body>")
    html = html.replace("</body>", f"{V4}\n{V6}\n</body>", 1)
    LEADS.write_text(html, encoding="utf-8")

    print("OK validacion estricta ya aplicada")
    print("OK siguiente inmediato ya aplicado")
    print("OK V5 eliminado")
    print("OK solo V4 + V6")
    print("AGENDA_WIZARD_V8_FINALIZED")


if __name__ == "__main__":
    main()
