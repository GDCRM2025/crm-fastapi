from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIZARD = ROOT / "web" / "js" / "agenda_wizard_v4.js"
LEADS = ROOT / "web" / "views" / "leads.html"


def replace_required(text: str, old: str, new: str, label: str) -> str:
    if old in text:
        print(f"OK {label}")
        return text.replace(old, new, 1)
    if new in text:
        print(f"OK {label} ya aplicado")
        return text
    raise SystemExit(f"ERROR: no se encontro bloque para {label}")


def main() -> None:
    js = WIZARD.read_text(encoding="utf-8")

    js = replace_required(
        js,
        '    const showStep = async (targetStep, options = {}) => {',
        '    const showStep = (targetStep, options = {}) => {',
        "showStep sin espera",
    )

    js = replace_required(
        js,
        '      if (currentStep >= 2) {\n'
        '        triggerPreview(popup);\n'
        '        await waitPreview(popup);\n'
        '        mountUI(popup);\n'
        '        decorateGroups(popup);\n'
        '      }',
        '      if (currentStep >= 2) {\n'
        '        triggerPreview(popup);\n'
        '        mountUI(popup);\n'
        '        decorateGroups(popup);\n'
        '      }',
        "vista previa no bloquea paso",
    )

    js = replace_required(
        js,
        '    next.addEventListener("click", async () => {',
        '    next.addEventListener("click", () => {',
        "boton siguiente sin espera",
    )

    js = replace_required(
        js,
        '      triggerPreview(popup);\n'
        '      await waitPreview(popup);\n'
        '      await showStep(currentStep + 1);',
        '      triggerPreview(popup);\n'
        '      showStep(currentStep + 1);',
        "avance inmediato",
    )

    js = js.replace('    next.textContent = "Guardar y continuar";', '    next.textContent = "Siguiente";')
    js = js.replace('    confirm.textContent = "Confirmar agendamiento";', '    confirm.textContent = "Guardar y agendar";')
    js = js.replace('      confirm.textContent = "Agendando…";', '      confirm.textContent = "Guardando…";')

    WIZARD.write_text(js, encoding="utf-8")

    html = LEADS.read_text(encoding="utf-8")
    html = html.replace(
        'agenda_wizard_v4.js?v=20260731-2',
        'agenda_wizard_v4.js?v=20260731-4',
    )
    html = html.replace(
        'agenda_wizard_v4.js?v=20260730-1',
        'agenda_wizard_v4.js?v=20260731-4',
    )
    LEADS.write_text(html, encoding="utf-8")

    print("OK botones: Siguiente / Guardar y agendar")
    print("OK sin temporizador ni espera entre pasos")
    print("AGENDA_NO_WAIT_V7_REPAIRED")


if __name__ == "__main__":
    main()
