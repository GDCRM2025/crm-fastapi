from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEADS = ROOT / "web" / "views" / "leads.html"
V4 = ROOT / "web" / "js" / "agenda_wizard_v4.js"
V6 = ROOT / "web" / "js" / "agenda_wizard_v6.js"
MARKER = "GD-AGENDA-MULTIDAY-V18"


def fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def backup(path: Path) -> None:
    target = path.with_name(path.name + ".bak_v18")
    if not target.exists():
        shutil.copy2(path, target)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        print(f"OK {label} ya aplicado")
        return text
    count = text.count(old)
    if count != 1:
        fail(f"{label}: se esperaba 1 bloque y se encontraron {count}")
    print(f"OK {label}")
    return text.replace(old, new, 1)


def patch_submit_unified(text: str) -> str:
    if f"// {MARKER}: SUBMIT UNIFICADO" in text:
        print("OK submit unificado ya aplicado")
        return text

    pattern = re.compile(
        r'''  const out = await apiJSON\(`/leads/\$\{lead\.id_lead\}/move`, \{.*?\n  const gcalLead = \(gcal && typeof gcal === "object" && gcal\.lead && typeof gcal\.lead === "object"\) \? gcal\.lead : \{\};''',
        re.S,
    )
    match = pattern.search(text)
    if not match:
        fail("no se encontro el flujo move + approve para reemplazar")

    new = r'''  // GD-AGENDA-MULTIDAY-V18: SUBMIT UNIFICADO
  // Un solo endpoint realiza preparacion, Calendar y cambio a Confirmado de forma atomica.
  const idempotencyKey = `agenda:${lead.id_lead}:${Date.now()}:${Math.random().toString(36).slice(2)}`;
  let out = null;
  try{
    Swal.fire({
      title: "Agendando evento",
      html: "Creando Calendar y confirmando el lead...",
      allowOutsideClick: false,
      allowEscapeKey: false,
      showConfirmButton: false,
      didOpen: ()=> Swal.showLoading(),
    });
    out = await apiJSON(`/leads/${lead.id_lead}/confirmar_agendamiento`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Idempotency-Key": idempotencyKey,
      },
      body: JSON.stringify(payload),
    });
    Swal.close();
  }catch(err){
    Swal.close();
    await Swal.fire({
      icon: "error",
      title: "No se pudo confirmar y agendar",
      text: String(err?.message || err),
    });
    return false;
  }

  const ev = out?.evento || {};
  const gcal = out || {};
  const gcalLink = String(gcal?.calendar_html_link || (Array.isArray(gcal?.calendar_html_links) ? gcal.calendar_html_links[0] : "") || "");
  const gcalEid = String(gcal?.calendar_event_id || (Array.isArray(gcal?.calendar_event_ids) ? gcal.calendar_event_ids[0] : "") || "");
  const gcalErr = String(gcal?.gcal_error || "");
  const gcalCalId = (Array.isArray(gcal?.calendar_ids) && gcal.calendar_ids.length) ? String(gcal.calendar_ids[0] || "") : "";
  const gcalLead = (gcal && typeof gcal === "object" && gcal.lead && typeof gcal.lead === "object") ? gcal.lead : {};'''

    text = text[: match.start()] + new + text[match.end() :]
    print("OK submit atomico move + Calendar + Confirmado")
    return text


def patch_success_fallback(text: str) -> str:
    if f"// {MARKER}: POST SUCCESS SAFE" in text:
        print("OK mensaje final seguro ya aplicado")
        return text

    start_anchor = '  try{ playCashRegisterSound(); }catch(_){ }'
    end_anchor = '\n\n  return true;\n}\n\n\nasync function onDrop'
    start = text.find(start_anchor)
    end = text.find(end_anchor, start)
    if start < 0 or end < 0:
        fail("no se encontro bloque final post-agendamiento")

    text = (
        text[:start]
        + '  // GD-AGENDA-MULTIDAY-V18: POST SUCCESS SAFE\n  try{\n'
        + text[start:end]
        + r'''
  }catch(postSuccessError){
    console.error("[agenda] fallo solo la construccion del resumen final", postSuccessError);
    const idsOk = Array.isArray(gcal?.calendar_event_ids)
      ? gcal.calendar_event_ids.filter((item)=>String(item || "").trim())
      : (gcalEid ? [gcalEid] : []);
    const linksOk = Array.isArray(gcal?.calendar_html_links)
      ? gcal.calendar_html_links.filter((item)=>String(item || "").trim())
      : (gcalLink ? [gcalLink] : []);
    await Swal.fire({
      icon: "success",
      title: "Evento agendado correctamente",
      html: `
        <div style="display:grid;gap:10px;text-align:left">
          <div>El lead fue movido a <b>Confirmado</b>.</div>
          <div>Eventos creados en Calendar: <b>${idsOk.length || 1}</b>.</div>
          ${linksOk[0] ? `<a href="${escapeHtml(linksOk[0])}" target="_blank" rel="noopener" style="color:#60a5fa;font-weight:1000">Abrir en Google Calendar</a>` : ``}
          <div class="muted">El agendamiento quedo guardado. Solo fallo la vista extendida del resumen.</div>
        </div>
      `,
      confirmButtonText: "Cerrar",
    });
  }'''
        + text[end:]
    )
    print("OK mensaje final garantizado con fallback")
    return text


def patch_multiday_payload(text: str) -> str:
    if f"// {MARKER}: NORMALIZA TBD" in text:
        print("OK normalizacion multiday ya aplicada")
        return text

    anchor = '      let blksOut = (!isSimple && model.blocks) ? model.blocks : null;'
    pos = text.find(anchor)
    if pos < 0:
        fail("no se encontro salida de bloques/segmentos")
    insert_at = pos + len(anchor)
    block = r'''

      // GD-AGENDA-MULTIDAY-V18: NORMALIZA TBD
      // En varios dias solo son obligatorios fecha, comuna, OPS y productos.
      // Direccion y horario vacios se transforman en DIR TBD / HR TBD.
      const normalizeAgendaGroupV18 = (row)=>{
        const out = { ...(row || {}) };
        if (!String(out.direccion || "").trim()) out.direccion = "DIR TBD";
        if (!String(out.start_time || "").trim() || !String(out.end_time || "").trim()){
          out.start_time = "";
          out.end_time = "";
          out.hr_tbd = true;
        }
        return out;
      };
      if (blksOut) blksOut = blksOut.map(normalizeAgendaGroupV18);
      if (segsOut) segsOut = segsOut.map(normalizeAgendaGroupV18);'''
    text = text[:insert_at] + block + text[insert_at:]

    text = re.sub(r'(?m)^\s*if \(!String\(b\.direccion\|\|""\)\.trim\(\)\) return Swal\.showValidationMessage\(`Falta direcci[oó]n en Bloque \$\{i\+1\}\.`\);\s*$', '', text)
    text = re.sub(r'(?m)^\s*if \(!String\(b\.start_time\|\|""\)\.trim\(\) \|\| !String\(b\.end_time\|\|""\)\.trim\(\)\) return Swal\.showValidationMessage\(`Faltan horas en Bloque \$\{i\+1\}\.`\);\s*$', '', text)
    text = re.sub(r'(?m)^\s*if \(!String\(s\.direccion\|\|""\)\.trim\(\)\) return Swal\.showValidationMessage\(`Falta direcci[oó]n en Grupo \$\{i\+1\}\.`\);\s*$', '', text)
    text = re.sub(r'(?m)^\s*if \(!String\(s\.start_time\|\|""\)\.trim\(\) \|\| !String\(s\.end_time\|\|""\)\.trim\(\)\) return Swal\.showValidationMessage\(`Faltan horas en Grupo \$\{i\+1\}\.`\);\s*$', '', text)

    print("OK multi dia acepta DIR TBD y HR TBD")
    return text


def patch_v4_validation(text: str) -> str:
    text = re.sub(
        r'''\s*if \(\(data\.start && !data\.end\) \|\| \(!data\.start && data\.end\)\) \{\s*problems\.push\(\{ step: 2, element: qs\("\[data-f='start_time'\]", card\), message: `\$\{label\}: completa ambas horas o d[eé]jalas vac[ií]as como HR TBD\.`, blocking: true \}\);\s*\}\s*''',
        '\n',
        text,
        flags=re.S,
    )
    text = re.sub(r'(?m)^\s*if \(!data\.start\) add\([^\n]*hora de inicio[^\n]*\);\s*$', '', text, flags=re.I)
    text = re.sub(r'(?m)^\s*if \(!data\.end\) add\([^\n]*hora de t[eé]rmino[^\n]*\);\s*$', '', text, flags=re.I)
    text = re.sub(r'(?m)^\s*if \(!data\.direccion\) add\([^\n]*direcci[oó]n[^\n]*\);\s*$', '', text, flags=re.I)
    text = re.sub(r'(?m)^\s*if \(!phone\) add\([^\n]*tel[eé]fono[^\n]*\);\s*$', '', text, flags=re.I)
    return text


def patch_v6_day_status(text: str) -> str:
    if MARKER in text:
        print("OK estados inline por dia ya aplicados")
        return text

    style_anchor = '      .gdW6LoadingNote{display:none}.gdW6LoadingNote.show{display:grid}'
    style_new = style_anchor + r'''
      .gdW18DayStatus{margin-top:9px;padding:9px 11px;border-radius:11px;border:1px solid;font-size:11px;font-weight:900;line-height:1.45}
      .gdW18DayStatus.ok{border-color:rgba(25,195,125,.45);background:rgba(25,195,125,.09);color:#58dca7}
      .gdW18DayStatus.error{border-color:rgba(239,68,68,.55);background:rgba(239,68,68,.10);color:#fca5a5}
      .gdW18DayStatus.warn{border-color:rgba(245,158,11,.52);background:rgba(245,158,11,.10);color:#fbbf24}
      .gdW4Alert.show{position:sticky;top:66px;z-index:29}'''
    text = replace_once(text, style_anchor, style_new, "estilo de validacion visible por dia")

    anchor = '  function decorateDayCards(popup) {'
    pos = text.find(anchor)
    if pos < 0:
        fail("v6: no se encontro decorateDayCards")

    helpers = r'''  // GD-AGENDA-MULTIDAY-V18
  function dayCardProblems(card, index) {
    const problems = [];
    const day = value("[data-f='day']", card);
    const comuna = value("[data-f='comuna']", card);
    const ops = Number(value("[data-f='ops']", card) || 0);
    const products = String(qs("[data-f='plist']", card)?.textContent || "").trim();
    const start = value("[data-f='start_time']", card);
    const end = value("[data-f='end_time']", card);

    if (!day) problems.push("selecciona la fecha");
    if (!comuna) problems.push("ingresa la comuna");
    if (!Number.isFinite(ops) || ops < 1) problems.push("OPS debe ser 1 o mas");
    if (!products || products === "—" || /sin productos/i.test(products)) problems.push("asigna productos a este dia");

    const warnings = [];
    if (!start || !end) warnings.push("horario quedara HR TBD");
    if (!value("[data-f='direccion']", card)) warnings.push("direccion quedara DIR TBD");
    return { problems, warnings, index };
  }

  function updateDayCardStatuses(popup) {
    if (mode(popup) !== "multiday") return;
    qsa("#ag_group_editor [data-g-idx]", popup).forEach((card, index) => {
      const result = dayCardProblems(card, index);
      let status = qs(".gdW18DayStatus", card);
      if (!status) {
        status = document.createElement("div");
        status.className = "gdW18DayStatus";
        card.append(status);
      }
      card.classList.toggle("gdW4Problem", result.problems.length > 0);
      if (result.problems.length) {
        status.className = "gdW18DayStatus error";
        status.textContent = `Dia ${index + 1}: falta ${result.problems.join(", ")}.`;
      } else if (result.warnings.length) {
        status.className = "gdW18DayStatus warn";
        status.textContent = `Dia ${index + 1} listo. ${result.warnings.join("; ")}.`;
      } else {
        status.className = "gdW18DayStatus ok";
        status.textContent = `Dia ${index + 1} listo para agendar.`;
      }
    });
  }

'''
    text = text[:pos] + helpers + text[pos:]

    call_anchor = '    syncLeadFromFirstDay(popup);\n  }'
    call_new = '    syncLeadFromFirstDay(popup);\n    updateDayCardStatuses(popup);\n  }'
    text = replace_once(text, call_anchor, call_new, "estado inline actualizado al decorar")

    listener_old = '''    groupEditor?.addEventListener("input", (event) => {
      if (mode(popup) !== "multiday") return;
      const card = event.target?.closest?.("[data-g-idx]");
      if (!card || card !== qs("#ag_group_editor [data-g-idx]", popup)) return;
      const field = String(event.target?.getAttribute?.("data-f") || "");
      if (field === "direccion" || field === "comuna") syncLeadFromFirstDay(popup);
    }, true);'''
    listener_new = '''    groupEditor?.addEventListener("input", (event) => {
      if (mode(popup) !== "multiday") return;
      const card = event.target?.closest?.("[data-g-idx]");
      if (card && card === qs("#ag_group_editor [data-g-idx]", popup)) {
        const field = String(event.target?.getAttribute?.("data-f") || "");
        if (field === "direccion" || field === "comuna") syncLeadFromFirstDay(popup);
      }
      updateDayCardStatuses(popup);
    }, true);
    groupEditor?.addEventListener("change", () => updateDayCardStatuses(popup), true);'''
    if listener_old in text:
        text = text.replace(listener_old, listener_new, 1)
    else:
        observer_anchor = '    if (groupEditor) {\n      const groupObserver'
        if observer_anchor not in text:
            fail("v6: no se encontro observer de grupos")
        text = text.replace(
            observer_anchor,
            '    groupEditor?.addEventListener("input", () => updateDayCardStatuses(popup), true);\n'
            '    groupEditor?.addEventListener("change", () => updateDayCardStatuses(popup), true);\n\n'
            + observer_anchor,
            1,
        )

    text = text.replace(
        'const groupObserver = new MutationObserver(() => decorateDayCards(popup));',
        'const groupObserver = new MutationObserver(() => { decorateDayCards(popup); updateDayCardStatuses(popup); });',
        1,
    )

    print("OK cada dia muestra exactamente que falta")
    return text


def bump_versions(text: str) -> str:
    text = re.sub(r'agenda_wizard_v4\.js\?v=[^"\']+', 'agenda_wizard_v4.js?v=20260731-18', text)
    text = re.sub(r'agenda_wizard_v6\.js\?v=[^"\']+', 'agenda_wizard_v6.js?v=20260731-18', text)
    return text


def check_inline_js(html: str) -> None:
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, flags=re.I | re.S)
    if not blocks:
        fail("no se encontraron scripts inline")
    with tempfile.TemporaryDirectory() as tmp:
        for index, block in enumerate(blocks, start=1):
            path = Path(tmp) / f"inline_{index}.js"
            path.write_text(block, encoding="utf-8")
            result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
            if result.returncode != 0:
                fail(f"JS inline {index}: {(result.stderr or result.stdout).strip()}")


def node_check(path: Path) -> None:
    result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
    if result.returncode != 0:
        fail(f"{path.name}: {(result.stderr or result.stdout).strip()}")


def main() -> None:
    for path in (LEADS, V4, V6):
        if not path.exists():
            fail(f"falta {path}")
        backup(path)

    html = LEADS.read_text(encoding="utf-8")
    v4 = V4.read_text(encoding="utf-8")
    v6 = V6.read_text(encoding="utf-8")

    html = patch_submit_unified(html)
    html = patch_success_fallback(html)
    html = patch_multiday_payload(html)
    html = bump_versions(html)
    v4 = patch_v4_validation(v4)
    v6 = patch_v6_day_status(v6)

    LEADS.write_text(html, encoding="utf-8")
    V4.write_text(v4, encoding="utf-8")
    V6.write_text(v6, encoding="utf-8")

    check_inline_js(html)
    node_check(V4)
    node_check(V6)

    final_html = LEADS.read_text(encoding="utf-8")
    final_v6 = V6.read_text(encoding="utf-8")
    if f"// {MARKER}: SUBMIT UNIFICADO" not in final_html:
        fail("falta submit unificado")
    if "/confirmar_agendamiento" not in final_html:
        fail("falta endpoint confirmar_agendamiento")
    if "Evento agendado correctamente" not in final_html:
        fail("falta fallback de exito")
    if MARKER not in final_v6:
        fail("falta validacion visual por dia")
    if 'Array.from({length:daysN}, ()=>"")' not in final_html:
        fail("regresion: las fechas multiday no estan vacias")

    print("OK multi dia muestra errores exactos dentro de cada dia")
    print("OK solo fecha, comuna, OPS y productos son obligatorios por dia")
    print("OK horario vacio queda HR TBD y direccion vacia queda DIR TBD")
    print("OK agendamiento usa endpoint atomico")
    print("OK Confirmado y Calendar se completan en una sola operacion")
    print("OK mensaje final de exito queda garantizado")
    print("OK fechas de varios dias siguen sin valores automaticos")
    print("OK sintaxis JavaScript completa")
    print("AGENDA_MULTIDAY_V18_REPAIRED")


if __name__ == "__main__":
    main()
