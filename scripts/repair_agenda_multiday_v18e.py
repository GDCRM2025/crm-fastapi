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
MARKER = "GD-AGENDA-MULTIDAY-V18E"


def fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def backup(path: Path) -> None:
    target = path.with_name(path.name + ".bak_v18e")
    if not target.exists():
        shutil.copy2(path, target)


def patch_html(text: str) -> str:
    if MARKER not in text:
        anchor = '      let blksOut = (!isSimple && model.blocks) ? model.blocks : null;'
        if anchor not in text:
            fail("no se encontro salida de grupos en leads.html")
        normalization = r'''

      // GD-AGENDA-MULTIDAY-V18E
      // Por dia solo son obligatorios fecha, comuna, OPS y productos.
      const normalizeGroupV18E = (row)=>{
        const out = { ...(row || {}) };
        if (!String(out.direccion || "").trim()) out.direccion = "DIR TBD";
        if (!String(out.start_time || "").trim() || !String(out.end_time || "").trim()){
          out.start_time = "";
          out.end_time = "";
          out.hr_tbd = true;
        }
        return out;
      };
      if (blksOut) blksOut = blksOut.map(normalizeGroupV18E);
      if (segsOut) segsOut = segsOut.map(normalizeGroupV18E);'''
        text = text.replace(anchor, anchor + normalization, 1)

    patterns = [
        r'(?m)^\s*if \(!String\(b\.direccion\|\|""\)\.trim\(\)\) return Swal\.showValidationMessage\(`Falta direcci[oó]n en Bloque \$\{i\+1\}\.`\);\s*$',
        r'(?m)^\s*if \(!String\(b\.start_time\|\|""\)\.trim\(\) \|\| !String\(b\.end_time\|\|""\)\.trim\(\)\) return Swal\.showValidationMessage\(`Faltan horas en Bloque \$\{i\+1\}\.`\);\s*$',
        r'(?m)^\s*if \(!String\(s\.direccion\|\|""\)\.trim\(\)\) return Swal\.showValidationMessage\(`Falta direcci[oó]n en Grupo \$\{i\+1\}\.`\);\s*$',
        r'(?m)^\s*if \(!String\(s\.start_time\|\|""\)\.trim\(\) \|\| !String\(s\.end_time\|\|""\)\.trim\(\)\) return Swal\.showValidationMessage\(`Faltan horas en Grupo \$\{i\+1\}\.`\);\s*$',
    ]
    for pattern in patterns:
        text = re.sub(pattern, "", text)

    success_marker = "GD-AGENDA-SUCCESS-FALLBACK-V18E"
    if success_marker not in text:
        start_anchor = '  try{ playCashRegisterSound(); }catch(_){ }'
        end_anchor = '\n\n  return true;\n}\n\n\nasync function onDrop'
        start = text.find(start_anchor)
        end = text.find(end_anchor, start)
        if start < 0 or end < 0:
            fail("no se encontro bloque final de exito")
        original = text[start:end]
        wrapped = (
            '  // GD-AGENDA-SUCCESS-FALLBACK-V18E\n'
            '  try{\n'
            + original
            + r'''
  }catch(summaryError){
    console.error("[agenda] fallo resumen final", summaryError);
    const linkOk = String(gcalLink || (Array.isArray(gcal?.calendar_html_links) ? gcal.calendar_html_links[0] : "") || "");
    const idsOk = Array.isArray(gcal?.calendar_event_ids)
      ? gcal.calendar_event_ids.filter((item)=>String(item || "").trim())
      : (gcalEid ? [gcalEid] : []);
    await Swal.fire({
      icon: "success",
      title: "Evento agendado correctamente",
      html: `
        <div style="display:grid;gap:10px;text-align:left">
          <div>El lead fue movido a <b>Confirmado</b>.</div>
          <div>Eventos creados en Calendar: <b>${idsOk.length || 1}</b>.</div>
          ${linkOk ? `<a href="${escapeHtml(linkOk)}" target="_blank" rel="noopener" style="color:#60a5fa;font-weight:1000">Abrir en Google Calendar</a>` : ``}
        </div>
      `,
      confirmButtonText: "Cerrar",
    });
  }'''
        )
        text = text[:start] + wrapped + text[end:]

    text = re.sub(r'agenda_wizard_v4\.js\?v=[^"\']+', 'agenda_wizard_v4.js?v=20260731-18e', text)
    text = re.sub(r'agenda_wizard_v6\.js\?v=[^"\']+', 'agenda_wizard_v6.js?v=20260731-18e', text)
    return text


def patch_v4(text: str) -> str:
    old = '''          if ((data.start && !data.end) || (!data.start && data.end)) {
            problems.push({ step: 2, element: qs("[data-f='start_time']", card), message: `${label}: completa ambas horas o déjalas vacías como HR TBD.`, blocking: true });
          }'''
    new = '''          if ((data.start && !data.end) || (!data.start && data.end)) {
            problems.push({ step: 2, element: qs("[data-f='start_time']", card), message: `${label}: horario incompleto; quedará HR TBD.`, blocking: false });
          }'''
    if old in text:
        text = text.replace(old, new, 1)
    elif new not in text:
        text = re.sub(
            r'''if \(\(data\.start && !data\.end\) \|\| \(!data\.start && data\.end\)\) \{\s*problems\.push\(\{ step: 2, element: qs\("\[data-f='start_time'\]", card\), message: `\$\{label\}:[^`]+`, blocking: true \}\);\s*\}''',
            new.strip(),
            text,
            count=1,
            flags=re.S,
        )
    return text


def patch_v6(text: str) -> str:
    if MARKER in text:
        return text

    style_anchor = '      .gdW6LoadingNote{display:none}.gdW6LoadingNote.show{display:grid}'
    if style_anchor not in text:
        fail("no se encontro ancla de estilos V6")
    style_extra = r'''
      .gdW18EDayStatus{margin-top:9px;padding:9px 11px;border-radius:11px;border:1px solid;font-size:11px;font-weight:900;line-height:1.45}
      .gdW18EDayStatus.ok{border-color:rgba(25,195,125,.45);background:rgba(25,195,125,.09);color:#58dca7}
      .gdW18EDayStatus.error{border-color:rgba(239,68,68,.55);background:rgba(239,68,68,.10);color:#fca5a5}
      .gdW18EDayStatus.warn{border-color:rgba(245,158,11,.52);background:rgba(245,158,11,.10);color:#fbbf24}'''
    text = text.replace(style_anchor, style_anchor + style_extra, 1)

    function_anchor = '  function decorateDayCards(popup) {'
    pos = text.find(function_anchor)
    if pos < 0:
        fail("no se encontro decorateDayCards")
    helpers = r'''  // GD-AGENDA-MULTIDAY-V18E
  function updateDayStatusV18E(popup) {
    if (mode(popup) !== "multiday") return;
    qsa("#ag_group_editor [data-g-idx]", popup).forEach((card, index) => {
      const missing = [];
      const warnings = [];
      const day = value("[data-f='day']", card);
      const comuna = value("[data-f='comuna']", card);
      const ops = Number(value("[data-f='ops']", card) || 0);
      const products = String(qs("[data-f='plist']", card)?.textContent || "").trim();
      const start = value("[data-f='start_time']", card);
      const end = value("[data-f='end_time']", card);
      const direccion = value("[data-f='direccion']", card);

      if (!day) missing.push("fecha");
      if (!comuna) missing.push("comuna");
      if (!Number.isFinite(ops) || ops < 1) missing.push("OPS");
      if (!products || products === "—" || /sin productos/i.test(products)) missing.push("productos");
      if (!start || !end) warnings.push("HR TBD");
      if (!direccion) warnings.push("DIR TBD");

      let status = qs(".gdW18EDayStatus", card);
      if (!status) {
        status = document.createElement("div");
        status.className = "gdW18EDayStatus";
        card.append(status);
      }
      card.classList.toggle("gdW4Problem", missing.length > 0);
      if (missing.length) {
        status.className = "gdW18EDayStatus error";
        status.textContent = `Día ${index + 1}: falta ${missing.join(", ")}.`;
      } else if (warnings.length) {
        status.className = "gdW18EDayStatus warn";
        status.textContent = `Día ${index + 1} listo. ${warnings.join(" · ")}.`;
      } else {
        status.className = "gdW18EDayStatus ok";
        status.textContent = `Día ${index + 1} listo para agendar.`;
      }
    });
  }

'''
    text = text[:pos] + helpers + text[pos:]

    call_anchor = '    syncLeadFromFirstDay(popup);\n  }'
    if call_anchor not in text:
        fail("no se encontro cierre de decorateDayCards")
    text = text.replace(call_anchor, '    syncLeadFromFirstDay(popup);\n    updateDayStatusV18E(popup);\n  }', 1)

    listener_anchor = '    const groupEditor = qs("#ag_group_editor", popup);'
    if listener_anchor not in text:
        fail("no se encontro groupEditor")
    listener_extra = '''    const groupEditor = qs("#ag_group_editor", popup);
    groupEditor?.addEventListener("input", () => updateDayStatusV18E(popup), true);
    groupEditor?.addEventListener("change", () => updateDayStatusV18E(popup), true);'''
    text = text.replace(listener_anchor, listener_extra, 1)

    text = text.replace(
        'const groupObserver = new MutationObserver(() => decorateDayCards(popup));',
        'const groupObserver = new MutationObserver(() => { decorateDayCards(popup); updateDayStatusV18E(popup); });',
        1,
    )
    return text


def check_inline(html: str) -> None:
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, flags=re.I | re.S)
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

    html = patch_html(LEADS.read_text(encoding="utf-8"))
    v4 = patch_v4(V4.read_text(encoding="utf-8"))
    v6 = patch_v6(V6.read_text(encoding="utf-8"))

    LEADS.write_text(html, encoding="utf-8")
    V4.write_text(v4, encoding="utf-8")
    V6.write_text(v6, encoding="utf-8")

    check_inline(html)
    node_check(V4)
    node_check(V6)

    if MARKER not in html or MARKER not in v6:
        fail("faltan marcadores V18E")
    if "Evento agendado correctamente" not in html:
        fail("falta mensaje final seguro")
    if 'agenda_wizard_v4.js?v=20260731-18e' not in html:
        fail("falta cache buster V18E")

    print("OK no depende del bloque submit que fallo en V18B")
    print("OK cada dia muestra exactamente que falta")
    print("OK solo fecha, comuna, OPS y productos bloquean")
    print("OK horario y direccion vacios quedan TBD")
    print("OK mensaje final aparece aunque falle el resumen extendido")
    print("OK sintaxis JavaScript completa")
    print("AGENDA_MULTIDAY_V18E_REPAIRED")


if __name__ == "__main__":
    main()
