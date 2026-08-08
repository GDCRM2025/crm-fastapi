from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIZARD = ROOT / "web" / "js" / "agenda_wizard_v4.js"
LEADS = ROOT / "web" / "views" / "leads.html"

V4_TAG = '  <script src="../js/agenda_wizard_v4.js?v=20260731-8"></script>'
V6_TAG = '  <script src="../js/agenda_wizard_v6.js?v=20260731-8"></script>'


def sub_required(text: str, pattern: str, replacement: str, label: str, flags: int = 0) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=flags)
    if count:
        print(f"OK {label}")
        return updated
    if replacement in text:
        print(f"OK {label} ya aplicado")
        return text
    raise SystemExit(f"ERROR: no se encontro bloque para {label}")


def main() -> None:
    js = WIZARD.read_text(encoding="utf-8")

    validation = r'''  function validateStep(step, popup) {
    const mode = modeValue(popup);
    const problems = [];
    const add = (selector, message, stepNumber = step, element = null) => {
      problems.push({ step: stepNumber, selector, element, message, blocking: true });
    };
    const statusText = (selector) => String(qs(selector, popup)?.textContent || "").trim();

    if (step === 0) {
      if (mode === "multiday" && Number(value("#ag_days_n", popup) || 0) < 2) {
        add("#ag_days_n", "Indica cuántos días tiene el evento. Deben ser al menos 2.");
      }
      if (mode === "blocks" && Number(value("#ag_blocks_n", popup) || 0) < 1) {
        add("#ag_blocks_n", "Indica cuántos bloques tendrá el evento.");
      }
      if (checked("#ag_flag_multiloc", popup) && Number(value("#ag_locs_n", popup) || 0) < 2) {
        add("#ag_locs_n", "Indica cuántas locaciones tendrá el evento. Deben ser al menos 2.");
      }
    }

    if (step === 1) {
      const quote = value("#ag_quote", popup);
      const calc = statusText("#ag_calc_status");
      const allocation = statusText("#ag_alloc_status");
      const products = String(qs("#ag_products_wrap", popup)?.textContent || qs("#ag_products_day", popup)?.textContent || "").trim();
      const distributed = mode === "multiday" || mode === "blocks" || checked("#ag_flag_multiloc", popup);

      if (!quote) add("#ag_quote", "Selecciona una cotización antes de continuar.");
      if (/error|inválid|inval|no hay productos|sin productos/i.test(`${calc} ${allocation}`)) {
        add("#gdW6ProductStatus", allocation || calc || "No fue posible cargar los productos de la cotización.");
      } else if (/actualizando|cargando|calculando|procesando/i.test(`${calc} ${allocation}`)) {
        add("#gdW6ProductStatus", "Los productos todavía se están cargando. Espera a que el estado quede en verde y vuelve a presionar Siguiente.");
      }

      if (distributed) {
        if (!allocation || !/^OK:/i.test(allocation)) {
          add("#ag_products_allocator", allocation || "Distribuye el 100% de los productos antes de continuar.");
        }
      } else if (!products || products === "—" || /sin productos/i.test(products)) {
        add("#ag_products_wrap", "La cotización no tiene productos cargados.");
      }
    }

    if (step === 2) {
      const distributed = mode === "multiday" || mode === "blocks" || checked("#ag_flag_multiloc", popup);
      const phone = value("#ag_tel", popup);
      const montage = String(qs("#ag_montaje_day", popup)?.value || qs("#ag_mont_manual", popup)?.value || "").trim();

      if (!phone) add("#ag_tel", "Ingresa el teléfono del cliente antes de continuar.");

      if (distributed) {
        const cards = getGroupCards(popup);
        if (!cards.length) add("#ag_group_editor", "No se generaron los días o bloques del evento.");

        cards.forEach((card, index) => {
          const data = groupData(card);
          const label = mode === "blocks" ? `Bloque ${index + 1}` : `Día ${index + 1}`;
          if (!data.day) add(null, `${label}: falta la fecha.`, 2, qs("[data-f='day']", card));
          if (!data.start) add(null, `${label}: falta la hora de inicio.`, 2, qs("[data-f='start_time']", card));
          if (!data.end) add(null, `${label}: falta la hora de término.`, 2, qs("[data-f='end_time']", card));
          if (data.start && data.end && !durationBetween(data.start, data.end)) {
            add(null, `${label}: el horario ingresado no es válido.`, 2, qs("[data-f='start_time']", card));
          }
          if (!data.comuna) add(null, `${label}: falta la comuna.`, 2, qs("[data-f='comuna']", card));
          if (!data.direccion) add(null, `${label}: falta la dirección.`, 2, qs("[data-f='direccion']", card));
          if (!Number.isFinite(data.ops) || data.ops < 1) {
            add(null, `${label}: debe tener al menos 1 operador.`, 2, qs("[data-f='ops']", card));
          }
          if (!data.products || data.products === "—" || /sin productos/i.test(data.products)) {
            add(null, `${label}: no tiene productos asignados.`, 2, card);
          }
        });

        const allocation = statusText("#ag_alloc_status");
        if (!allocation || !/^OK:/i.test(allocation)) {
          add("#ag_products_allocator", allocation || "La distribución de productos no está completa.", 1);
        }
      } else {
        syncSimpleTime(popup);
        const start = value("#ag_hini", popup);
        const end = value("#ag_hfin", popup);
        const hrTbd = checked("#ag_hrtbd", popup);
        const comuna = value("#ag_loc", popup);
        const direccion = value("#ag_dir", popup);
        const products = String(qs("#ag_products_day", popup)?.textContent || qs("#ag_products_wrap", popup)?.textContent || "").trim();

        if (!hrTbd && !start) add("#ag_hini", "Ingresa la hora de inicio o marca HR TBD.");
        if (!hrTbd && !end) add("#ag_hfin", "Ingresa la hora de término o marca HR TBD.");
        if (start && end && !durationBetween(start, end)) add("#ag_hini", "El horario ingresado no es válido.");
        if (!comuna) add("#ag_loc", "Ingresa la comuna del evento.");
        if (!direccion) add("#ag_dir", "Ingresa la dirección del evento.");
        if (!products || products === "—" || /sin productos/i.test(products)) add("#ag_products_wrap", "No hay productos cargados.", 1);
        if (Number(value("#ag_ops_day", popup) || 0) < 1) add("#ag_ops_day", "Debe existir al menos 1 operador.");
      }

      if (!montage) add("#ag_day_montaje_wrap", "Revisa y acepta el montaje sugerido antes de continuar.");

      const calc = statusText("#ag_calc_status");
      if (/error|inválid|inval|no hay/i.test(calc)) add("#ag_calc_status", calc);
    }

    return problems;
  }
'''

    js = sub_required(
        js,
        r"  function validateStep\(step, popup\) \{.*?\n  \}\n\n  function review",
        validation + "\n  function review",
        "validacion estricta por paso",
        re.S,
    )

    next_handler = r'''    next.addEventListener("click", () => {
      const problems = validateStep(currentStep, popup);
      setTopAlert(shell, problems, (problem) => focusProblem(problem, popup, shell, showStep));
      const blocking = problems.find((problem) => problem.blocking);
      if (blocking) {
        focusProblem(blocking, popup, shell, showStep);
        return;
      }
      showStep(currentStep + 1);
    });'''

    js = sub_required(
        js,
        r'    next\.addEventListener\("click",(?: async)? \(\) => \{.*?\n    \}\);\n\n    qsa\("\\\.gdW4Step", shell\)',
        next_handler + '\n\n    qsa(".gdW4Step", shell)',
        "boton siguiente inmediato",
        re.S,
    )

    js = re.sub(
        r"      if \(currentStep >= 2\) \{\n\s*triggerPreview\(popup\);\n\s*(?:await waitPreview\(popup\);\n\s*)?mountUI\(popup\);",
        "      if (currentStep >= 2) {\n        mountUI(popup);",
        js,
        count=1,
    )
    js = js.replace("    const showStep = async (targetStep, options = {}) => {", "    const showStep = (targetStep, options = {}) => {")
    js = js.replace('    next.textContent = "Guardar y continuar";', '    next.textContent = "Siguiente";')
    js = js.replace('    next.textContent = "Guardar y Continuar";', '    next.textContent = "Siguiente";')
    js = js.replace('    confirm.textContent = "Confirmar agendamiento";', '    confirm.textContent = "Guardar y agendar";')
    js = js.replace('      confirm.textContent = "Agendando…";', '      confirm.textContent = "Guardando…";')
    js = js.replace('html.scrollTo({ top: 0, behavior: "smooth" })', 'html.scrollTo({ top: 0, behavior: "auto" })')

    js = re.sub(
        r'''        window\.setTimeout\(\(\) => \{\n          syncModeUI\(popup\);\n          triggerPreview\(popup\);\n        \}, 60\);''',
        '''        syncModeUI(popup);\n        triggerPreview(popup);''',
        js,
        count=1,
    )

    js = js.replace(
        '    qs("#ag_tel", popup)?.addEventListener("input", () => triggerPreview(popup));',
        '    qs("#ag_tel", popup)?.addEventListener("change", () => triggerPreview(popup));',
    )
    js = js.replace(
        '    qs("#ag_dir", popup)?.addEventListener("input", () => triggerPreview(popup));',
        '    qs("#ag_dir", popup)?.addEventListener("change", () => triggerPreview(popup));',
    )
    js = js.replace(
        '    qs("#ag_loc", popup)?.addEventListener("input", () => triggerPreview(popup));',
        '    qs("#ag_loc", popup)?.addEventListener("change", () => triggerPreview(popup));',
    )

    js = js.replace(
        'new MutationObserver(() => window.setTimeout(() => decorateGroups(popup), 0))',
        'new MutationObserver(() => decorateGroups(popup))',
    )
    js = js.replace(
        'groupEditor?.addEventListener("change", () => window.setTimeout(() => decorateGroups(popup), 0));',
        'groupEditor?.addEventListener("change", () => decorateGroups(popup));',
    )
    js = js.replace(
        'groupEditor?.addEventListener("input", () => window.setTimeout(() => decorateGroups(popup), 0));',
        'groupEditor?.addEventListener("input", () => decorateGroups(popup));',
    )

    WIZARD.write_text(js, encoding="utf-8")

    html = LEADS.read_text(encoding="utf-8")
    lines = [
        line
        for line in html.splitlines()
        if "agenda_wizard_v2.js" not in line
        and "agenda_wizard_v3.js" not in line
        and "agenda_wizard_v4.js" not in line
        and "agenda_wizard_v5.js" not in line
        and "agenda_wizard_v6.js" not in line
    ]
    html = "\n".join(lines) + "\n"
    if "</body>" not in html:
        raise SystemExit("ERROR: no se encontro </body> en leads.html")
    html = html.replace("</body>", f"{V4_TAG}\n{V6_TAG}\n</body>", 1)
    LEADS.write_text(html, encoding="utf-8")

    print("OK V5 eliminado para evitar observadores duplicados")
    print("OK solo V4 + V6")
    print("OK siguiente sin espera")
    print("OK no avanza con campos obligatorios incompletos")
    print("AGENDA_WIZARD_V8_REPAIRED")


if __name__ == "__main__":
    main()
