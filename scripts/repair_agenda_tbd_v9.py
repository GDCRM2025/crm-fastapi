from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEADS = ROOT / "web" / "views" / "leads.html"
WIZARD = ROOT / "web" / "js" / "agenda_wizard_v4.js"

V4_TAG = '  <script src="../js/agenda_wizard_v4.js?v=20260731-9"></script>'
V6_TAG = '  <script src="../js/agenda_wizard_v6.js?v=20260731-9"></script>'


def replace_function(text: str, name: str, next_name: str, body: str) -> str:
    pattern = rf"  function {re.escape(name)}\(.*?\n  \}}\n\n  function {re.escape(next_name)}"
    replacement = body.rstrip() + f"\n\n  function {next_name}"
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f"ERROR: no se pudo reemplazar {name}")
    return updated


def remove_oc_requirements(text: str) -> str:
    patterns = [
        r'''\s*if\s*\(\s*abMode\s*===\s*["']oc["']\s*&&\s*!abOC\s*\)\s*\{\s*Swal\.showValidationMessage\([^;]*OC[^;]*\);\s*return false;\s*\}\s*''',
        r'''\s*if\s*\(\s*mode\s*===\s*["']oc["']\s*&&\s*!oc\s*\)\s*throw new Error\([^;]*OC[^;]*\);\s*''',
        r'''\s*if\s*\([^\n{}]*abono[^\n{}]*oc[^\n{}]*\)\s*\{\s*Swal\.showValidationMessage\([^;]*(?:OC|referencia)[^;]*\);\s*return false;\s*\}\s*''',
    ]
    for pattern in patterns:
        text = re.sub(pattern, "\n", text, flags=re.I | re.S)

    lines = []
    for line in text.splitlines():
        lowered = line.lower()
        if ("showvalidationmessage" in lowered or "throw new error" in lowered) and "oc" in lowered and "referencia" in lowered:
            continue
        lines.append(line)
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def main() -> None:
    js = WIZARD.read_text(encoding="utf-8")

    validate = r'''  function validateStep(step, popup) {
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
      const distributed = mode === "multiday" || mode === "blocks" || checked("#ag_flag_multiloc", popup);
      const products = String(qs("#ag_products_wrap", popup)?.textContent || qs("#ag_products_day", popup)?.textContent || "").trim();

      if (!quote) add("#ag_quote", "Selecciona una cotización antes de continuar.");
      if (/error|inválid|inval|no hay productos|sin productos/i.test(`${calc} ${allocation}`)) {
        add("#gdW6ProductStatus", allocation || calc || "No fue posible cargar los productos.");
      } else if (/actualizando|cargando|calculando|procesando/i.test(`${calc} ${allocation}`)) {
        add("#gdW6ProductStatus", "Los productos todavía se están cargando. Espera a que el estado quede en verde.");
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
      const montage = String(qs("#ag_montaje_day", popup)?.value || qs("#ag_mont_manual", popup)?.value || "").trim();

      if (distributed) {
        const cards = getGroupCards(popup);
        if (!cards.length) add("#ag_group_editor", "No se generaron los días o bloques del evento.");

        cards.forEach((card, index) => {
          const data = groupData(card);
          const label = mode === "blocks" ? `Bloque ${index + 1}` : `Día ${index + 1}`;
          if (!data.day) add(null, `${label}: falta la fecha.`, 2, qs("[data-f='day']", card));
          if (!data.comuna) add(null, `${label}: la comuna es obligatoria.`, 2, qs("[data-f='comuna']", card));
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
        const comuna = value("#ag_loc", popup);
        const products = String(qs("#ag_products_day", popup)?.textContent || qs("#ag_products_wrap", popup)?.textContent || "").trim();
        if (!comuna) add("#ag_loc", "La comuna es obligatoria.");
        if (!products || products === "—" || /sin productos/i.test(products)) add("#ag_products_wrap", "No hay productos cargados.", 1);
        if (Number(value("#ag_ops_day", popup) || 0) < 1) add("#ag_ops_day", "Debe existir al menos 1 operador.");
      }

      if (!montage) add("#ag_day_montaje_wrap", "Revisa y acepta el montaje sugerido antes de continuar.");

      const calc = statusText("#ag_calc_status");
      if (/error|inválid|inval|no hay/i.test(calc)) add("#ag_calc_status", calc);
    }

    return problems;
  }'''

    js = replace_function(js, "validateStep", "review", validate)

    js = js.replace(
        '        else if (/abono|OC|crédito/i.test(message)) problem = { step: 1, selector: "#ag_abono_mode", message, blocking: true };',
        '        else if (/abono|crédito/i.test(message) && !/OC|referencia/i.test(message)) problem = { step: 1, selector: "#ag_abono_mode", message, blocking: true };',
    )
    js = js.replace(
        '        else if (/direcci/i.test(message)) problem = { step: 1, selector: "#ag_dir", message, blocking: true };',
        '        else if (/comuna/i.test(message)) problem = { step: 2, selector: "#ag_loc", message, blocking: true };',
    )
    js = js.replace(
        '        else if (/hora|horario/i.test(message)) problem = { step: modeValue(popup) === "simple" ? 1 : 2, selector: modeValue(popup) === "simple" ? "#ag_hini" : "#ag_group_editor", message, blocking: true };',
        '        else if (/hora|horario|direcci|teléfono|telefono/i.test(message)) return;',
    )
    js = js.replace('html.scrollTo({ top: 0, behavior: "smooth" })', 'html.scrollTo({ top: 0, behavior: "auto" })')
    WIZARD.write_text(js, encoding="utf-8")

    html = LEADS.read_text(encoding="utf-8")
    html = remove_oc_requirements(html)

    html = html.replace(
        'const hrTbd = !!qs("#ag_hrtbd")?.checked;',
        'const hrTbd = !!qs("#ag_hrtbd")?.checked || !hIni || !hFin;',
    )

    html = re.sub(
        r'''(?m)^\s*if \(!String\(b\.direccion\|\|""\)\.trim\(\)\) return Swal\.showValidationMessage\(`Falta dirección en Bloque \$\{i\+1\}\.`\);\s*$''',
        "",
        html,
    )
    html = re.sub(
        r'''(?m)^\s*if \(!String\(b\.start_time\|\|""\)\.trim\(\) \|\| !String\(b\.end_time\|\|""\)\.trim\(\)\) return Swal\.showValidationMessage\(`Faltan horas en Bloque \$\{i\+1\}\.`\);\s*$''',
        "",
        html,
    )
    html = re.sub(
        r'''(?m)^\s*if \(!String\(s\.direccion\|\|""\)\.trim\(\)\) return Swal\.showValidationMessage\(`Falta dirección en Grupo \$\{i\+1\}\.`\);\s*$''',
        "",
        html,
    )
    html = re.sub(
        r'''(?m)^\s*if \(!String\(s\.start_time\|\|""\)\.trim\(\) \|\| !String\(s\.end_time\|\|""\)\.trim\(\)\) return Swal\.showValidationMessage\(`Faltan horas en Grupo \$\{i\+1\}\.`\);\s*$''',
        "",
        html,
    )

    marker = '      let blksOut = (!isSimple && model.blocks) ? model.blocks : null;'
    normalization = r'''

      // Datos opcionales: nunca bloquean el agendamiento.
      // La comuna sí es obligatoria y se valida por separado.
      const telInputTbd = qs("#ag_tel");
      const dirInputTbd = qs("#ag_dir");
      if (telInputTbd && !String(telInputTbd.value || "").trim()) telInputTbd.value = "TBD";
      if (dirInputTbd && !String(dirInputTbd.value || "").trim()) dirInputTbd.value = "DIR TBD";

      const applyTbd = (row) => {
        const out = { ...(row || {}) };
        if (!String(out.direccion || "").trim()) out.direccion = "DIR TBD";
        if (!String(out.start_time || "").trim() || !String(out.end_time || "").trim()) {
          out.start_time = "";
          out.end_time = "";
          out.hr_tbd = true;
        }
        return out;
      };
      if (blksOut) blksOut = blksOut.map(applyTbd);
      if (segsOut) segsOut = segsOut.map(applyTbd);'''

    if normalization.strip() not in html:
        if marker not in html:
            raise SystemExit("ERROR: no se encontro punto para normalizar TBD")
        html = html.replace(marker, marker + normalization, 1)

    html = html.replace(
        'const isSimple = (!multiLoc && !blocks);',
        'const isSimple = (!multiLoc && !blocks && !multiDay);',
    )

    # Solo una capa visual: V4 base + V6 UX.
    lines = [line for line in html.splitlines() if "agenda_wizard_v" not in line]
    html = "\n".join(lines) + "\n"
    if "</body>" not in html:
        raise SystemExit("ERROR: no se encontro </body>")
    html = html.replace("</body>", f"{V4_TAG}\n{V6_TAG}\n</body>", 1)

    forbidden = [
        "Abono=0: falta OC / referencia",
        "Falta OC / referencia",
        "Ingresa OC / referencia",
        "Falta dirección en Bloque",
        "Faltan horas en Bloque",
        "Falta dirección en Grupo",
        "Faltan horas en Grupo",
    ]
    found = [item for item in forbidden if item in html]
    if found:
        raise SystemExit("ERROR: validaciones antiguas siguen presentes: " + ", ".join(found))

    LEADS.write_text(html, encoding="utf-8")

    print("OK OC sin numero obligatorio")
    print("OK telefono opcional -> TBD")
    print("OK direccion opcional -> DIR TBD")
    print("OK horario opcional -> HR TBD")
    print("OK comuna obligatoria")
    print("OK solo V4 + V6")
    print("AGENDA_TBD_V9_REPAIRED")


if __name__ == "__main__":
    main()
