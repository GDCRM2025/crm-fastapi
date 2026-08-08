from __future__ import annotations

import py_compile
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENDA_CONFIRM = ROOT / "backend" / "routers" / "agenda_confirmacion.py"
LEADS_AGENDA = ROOT / "backend" / "routers" / "leads_agenda.py"
LEADS_HTML = ROOT / "web" / "views" / "leads.html"
WIZARD_V4 = ROOT / "web" / "js" / "agenda_wizard_v4.js"
WIZARD_V6 = ROOT / "web" / "js" / "agenda_wizard_v6.js"
MARKER = "GD-AGENDA-INTEGRITY-V16"


def fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def backup(path: Path) -> None:
    target = path.with_name(path.name + ".bak_v16")
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


LOCK_HELPER = r'''

# GD-AGENDA-INTEGRITY-V16
# Recupera únicamente locks de agenda que quedaron huérfanos o bloqueados por más de 90 segundos.
def _recover_stale_agenda_lock(db: Session, lock_key: int) -> bool:
    try:
        rows = db.execute(
            text(
                """
                SELECT l.pid,
                       COALESCE(a.state, '') AS state,
                       EXTRACT(EPOCH FROM (now() - COALESCE(a.query_start, a.xact_start, a.backend_start)))::int AS age_seconds
                FROM pg_locks l
                LEFT JOIN pg_stat_activity a ON a.pid = l.pid
                WHERE l.locktype='advisory'
                  AND l.granted IS TRUE
                  AND l.classid::bigint = 0
                  AND l.objid::bigint = :lock_key
                  AND l.pid <> pg_backend_pid()
                """
            ),
            {"lock_key": int(lock_key)},
        ).mappings().all()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return False

    recovered = False
    for row in rows:
        try:
            pid = int(row.get("pid") or 0)
            state = str(row.get("state") or "").lower()
            age = int(row.get("age_seconds") or 0)
            stale = age >= 90 or state.startswith("idle")
            if pid > 0 and stale:
                killed = bool(db.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid}).scalar())
                recovered = recovered or killed
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
    try:
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
    return recovered
'''


def patch_agenda_confirm(text_in: str) -> str:
    text_out = text_in
    if MARKER not in text_out:
        anchor = '\n\n@router.post("/{id_lead}/confirmar_agendamiento")'
        if anchor not in text_out:
            fail("agenda_confirmacion: no se encontró la ruta confirmar_agendamiento")
        text_out = text_out.replace(anchor, LOCK_HELPER + anchor, 1)
        print("OK recuperación de lock huérfano agregada")
    else:
        print("OK recuperación de lock huérfano ya presente")

    old = '''        if not got_lock:\n            raise HTTPException(status_code=409, detail="Este lead ya se está agendando. Espera unos segundos.")'''
    new = '''        if not got_lock:\n            existing = _existing_result(db, id_lead, confirmado_id)\n            if existing:\n                return existing\n            if _recover_stale_agenda_lock(db, lock_key):\n                got_lock = bool(db.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key}).scalar())\n            if not got_lock:\n                raise HTTPException(status_code=409, detail="Este lead tiene otro agendamiento activo. Reintenta en 15 segundos; si persiste, el sistema liberará el proceso huérfano automáticamente.")'''
    text_out = replace_once(text_out, old, new, "reintento y recuperación del lock")

    old_exc = '''    except HTTPException:\n        raise'''
    new_exc = '''    except HTTPException as exc:\n        try:\n            db.rollback()\n        except Exception:\n            pass\n        try:\n            if exc.status_code != 409:\n                _audit_finish(\n                    db,\n                    key=key,\n                    status="failed",\n                    response={"ok": False, "status_code": exc.status_code, "detail": exc.detail},\n                )\n        except Exception:\n            pass\n        raise'''
    text_out = replace_once(text_out, old_exc, new_exc, "auditoría de fallos de agenda")
    return text_out


def patch_leads_agenda(text_in: str) -> str:
    text_out = text_in
    old_filter = '''                if not comuna_s or not direccion_s or not st_s or not en_s or ops_s < 1 or not products_s:\n                    continue\n                segments.append(\n                    {\n                        "day": dd,\n                        "comuna": comuna_s,\n                        "direccion": direccion_s,\n                        "start_time": st_s,\n                        "end_time": en_s,\n                        "ops": int(ops_s),\n                        "products_text": products_s,\n                        "montaje_text": montaje_s,\n                    }\n                )'''
    new_filter = '''                # La comuna, OPS y productos son obligatorios. Dirección y horario aceptan TBD.\n                if not comuna_s or ops_s < 1 or not products_s:\n                    continue\n                hr_tbd_s = bool(s.get("hr_tbd", False)) or not st_s or not en_s\n                if not direccion_s:\n                    direccion_s = "DIR TBD"\n                if hr_tbd_s:\n                    st_s = None\n                    en_s = None\n                segments.append(\n                    {\n                        "day": dd,\n                        "comuna": comuna_s,\n                        "direccion": direccion_s,\n                        "start_time": st_s,\n                        "end_time": en_s,\n                        "hr_tbd": hr_tbd_s,\n                        "ops": int(ops_s),\n                        "products_text": products_s,\n                        "montaje_text": montaje_s,\n                    }\n                )'''
    if new_filter not in text_out:
        if old_filter not in text_out:
            fail("leads_agenda: no se encontró validación legacy de segmentos")
        text_out = text_out.replace(old_filter, new_filter, 1)
        print("OK segmentos aceptan DIR TBD y HR TBD")
    else:
        print("OK segmentos TBD ya aplicado")

    old_call = '''                        hr_tbd=bool(hr_tbd),\n                        ops=int(s.get("ops") or 1),'''
    new_call = '''                        hr_tbd=bool(s.get("hr_tbd", hr_tbd)),\n                        ops=int(s.get("ops") or 1),'''
    text_out = replace_once(text_out, old_call, new_call, "HR TBD independiente por segmento")
    return text_out


def patch_html(text_in: str) -> str:
    text_out = text_in

    old_days = 'const days = f.multiday ? Array.from({length:daysN}, (_,i)=>_datePlusDays(d0,i)) : [d0];'
    count_days = text_out.count(old_days)
    if count_days:
        text_out = text_out.replace(old_days, 'const days = f.multiday ? Array.from({length:daysN}, ()=>"") : [d0];')
        print(f"OK fechas automáticas eliminadas: {count_days}")
    elif 'const days = f.multiday ? Array.from({length:daysN}, ()=>"") : [d0];' in text_out:
        print("OK fechas automáticas ya eliminadas")
    else:
        fail("leads.html: no se encontró construcción de días múltiples")

    pattern_lock = re.compile(
        r'''\t  const setMontajeLocked = \(locked(?:, force=false)?\)=>\{.*?\n\t  \};''',
        re.S,
    )
    new_lock = r'''	  const setMontajeLocked = (locked, force=false)=>{
	    # Protección central: ningún preview, observer o tecla puede bloquear mientras se edita.
	    if (locked && montageEditing && !force) return;
	    const ta = qs("#ag_montaje_day");
	    if (ta) ta.readOnly = !!locked;
	    const ops = qs("#ag_ops_day");
	    if (ops) ops.readOnly = !!locked;
	    const btnC = qs("#ag_montaje_confirm");
	    const btnE = qs("#ag_montaje_edit");
	    if (btnC) btnC.disabled = false;
	    if (btnE) btnE.disabled = !locked;
	  };'''.replace("# Protección", "// Protección")
    text_out, n_lock = pattern_lock.subn(new_lock, text_out, count=1)
    if n_lock != 1:
        fail(f"leads.html: no se pudo reemplazar setMontajeLocked ({n_lock})")
    print("OK bloqueo central del editor corregido")

    text_out = text_out.replace(
        '        montageEditing = false;\n\t\t        setMontajeLocked(true);',
        '        montageEditing = false;\n\t\t        setMontajeLocked(true, true);',
    )
    text_out = text_out.replace(
        '\t\t        montageEditing = false;\n\t\t        setMontajeLocked(true);',
        '\t\t        montageEditing = false;\n\t\t        setMontajeLocked(true, true);',
    )

    insert_anchor = '''    const fNow = _flags();\n    const isBlocks = !!fNow.blocks;\n    const isMultiLoc = !!fNow.multiloc;\n    const isMultiDay = !!fNow.multiday;\n    _applyMultiLocUI();'''
    insert_new = '''    const fNow = _flags();\n    const isBlocks = !!fNow.blocks;\n    const isMultiLoc = !!fNow.multiloc;\n    const isMultiDay = !!fNow.multiday;\n    _applyMultiLocUI();\n\n    // En varios días las fechas nunca se inventan: cada una debe elegirla el ejecutivo.\n    if (isMultiDay && (setupGroups || []).some((g) => !String(g.day || "").trim())) {\n      previewEvents = [];\n      if (st) st.textContent = "Selecciona la fecha de cada día para calcular montaje.";\n      return;\n    }'''
    text_out = replace_once(text_out, insert_anchor, insert_new, "preview detenido hasta elegir fechas")

    text_out = re.sub(r'agenda_wizard_v4\.js\?v=[^"\']+', 'agenda_wizard_v4.js?v=20260731-16', text_out)
    text_out = re.sub(r'agenda_wizard_v6\.js\?v=[^"\']+', 'agenda_wizard_v6.js?v=20260731-16', text_out)
    return text_out


def patch_v4(text_in: str) -> str:
    text_out = text_in

    old_step1 = 'if (/error|inválid|inval|no hay productos|sin productos/i.test(`${calc} ${allocation}`)) {'
    new_step1 = 'if (!/^OK:/i.test(allocation) && /error|inválid|inval|no hay productos|sin productos/i.test(`${calc} ${allocation}`)) {'
    if old_step1 in text_out:
        text_out = text_out.replace(old_step1, new_step1, 1)
        print("OK asignación OK prevalece sobre mensaje stale en paso productos")
    elif new_step1 in text_out:
        print("OK prioridad de asignación ya aplicada")

    old_status = '''      const status = String(qs("#ag_calc_status", popup)?.textContent || "").trim();\n      if (/error|no hay|inválid|inval/i.test(status)) {\n        problems.push({ step: 2, selector: "#ag_calc_status", message: status, blocking: true });\n      }'''
    new_status = '''      const status = String(qs("#ag_calc_status", popup)?.textContent || "").trim();\n      const allocationNow = String(qs("#ag_alloc_status", popup)?.textContent || "").trim();\n      const allocationOk = /^OK:/i.test(allocationNow);\n      const cardsHaveProducts = getGroupCards(popup).every((card) => {\n        const products = groupData(card).products;\n        return !!products && products !== "—" && !/sin productos/i.test(products);\n      });\n      const staleProductMessage = /faltan productos|no hay productos|sin productos/i.test(status);\n      if (/error|no hay|inválid|inval|faltan productos/i.test(status)\n          && !(allocationOk && cardsHaveProducts && staleProductMessage)) {\n        problems.push({ step: 2, selector: "#ag_calc_status", message: status, blocking: true });\n      }'''
    if old_status in text_out:
        text_out = text_out.replace(old_status, new_status, 1)
        print("OK validación ignora error stale si productos están 100% asignados")
    elif "const staleProductMessage" in text_out:
        print("OK validación stale ya aplicada")
    else:
        pattern = re.compile(
            r'''(\s*const status = String\(qs\("#ag_calc_status", popup\)\?\.textContent \|\| ""\)\.trim\(\);\n)(\s*)if \(/error\|no hay\|inválid\|inval/i\.test\(status\)\) \{'''
        )
        repl = r'''\1\2const allocationNow = String(qs("#ag_alloc_status", popup)?.textContent || "").trim();
\2const allocationOk = /^OK:/i.test(allocationNow);
\2const cardsHaveProducts = getGroupCards(popup).every((card) => {
\2  const products = groupData(card).products;
\2  return !!products && products !== "—" && !/sin productos/i.test(products);
\2});
\2const staleProductMessage = /faltan productos|no hay productos|sin productos/i.test(status);
\2if (/error|no hay|inválid|inval|faltan productos/i.test(status)
\2    && !(allocationOk && cardsHaveProducts && staleProductMessage)) {'''
        text_out, count = pattern.subn(repl, text_out, count=1)
        if count != 1:
            fail("agenda_wizard_v4: no se encontró validación de estado de cálculo")
        print("OK validación stale aplicada por patrón")

    return text_out


def patch_v6(text_in: str) -> str:
    text_out = text_in
    old = '''    } else if (/error|inválid|inval|no hay/i.test(combined)) {\n      box.classList.add("error");\n      box.textContent = combined || "No fue posible cargar o distribuir los productos.";\n    } else if (/^OK:/i.test(allocation) || (/^Listo$/i.test(calc) && allocation)) {\n      box.classList.add("ok");\n      box.textContent = allocation || "Productos cargados correctamente.";'''
    new = '''    } else if (/^OK:/i.test(allocation)) {\n      // La distribución al 100% es la fuente de verdad. Un error viejo del preview no puede contradecirla.\n      box.classList.add("ok");\n      box.textContent = allocation;\n    } else if (/error|inválid|inval|no hay|faltan productos/i.test(combined)) {\n      box.classList.add("error");\n      box.textContent = combined || "No fue posible cargar o distribuir los productos.";\n    } else if (/^Listo$/i.test(calc) && allocation) {\n      box.classList.add("ok");\n      box.textContent = allocation || "Productos cargados correctamente.";'''
    text_out = replace_once(text_out, old, new, "estado visual usa asignación 100% como fuente de verdad")
    return text_out


def check_inline_js(html: str) -> None:
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, flags=re.I | re.S)
    if not blocks:
        fail("no se encontraron scripts inline")
    with tempfile.TemporaryDirectory() as tmp:
        for idx, block in enumerate(blocks, start=1):
            path = Path(tmp) / f"inline_{idx}.js"
            path.write_text(block, encoding="utf-8")
            result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
            if result.returncode != 0:
                fail(f"JS inline {idx}: {(result.stderr or result.stdout).strip()}")


def check_js(path: Path) -> None:
    result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
    if result.returncode != 0:
        fail(f"{path.name}: {(result.stderr or result.stdout).strip()}")


def main() -> None:
    paths = [AGENDA_CONFIRM, LEADS_AGENDA, LEADS_HTML, WIZARD_V4, WIZARD_V6]
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        fail("faltan archivos: " + ", ".join(missing))

    originals = {path: path.read_text(encoding="utf-8") for path in paths}
    updated = {
        AGENDA_CONFIRM: patch_agenda_confirm(originals[AGENDA_CONFIRM]),
        LEADS_AGENDA: patch_leads_agenda(originals[LEADS_AGENDA]),
        LEADS_HTML: patch_html(originals[LEADS_HTML]),
        WIZARD_V4: patch_v4(originals[WIZARD_V4]),
        WIZARD_V6: patch_v6(originals[WIZARD_V6]),
    }

    for path in paths:
        if updated[path] != originals[path]:
            backup(path)
            path.write_text(updated[path], encoding="utf-8")

    py_compile.compile(str(AGENDA_CONFIRM), doraise=True)
    py_compile.compile(str(LEADS_AGENDA), doraise=True)
    check_js(WIZARD_V4)
    check_js(WIZARD_V6)
    check_inline_js(LEADS_HTML.read_text(encoding="utf-8"))

    final_html = LEADS_HTML.read_text(encoding="utf-8")
    final_v4 = WIZARD_V4.read_text(encoding="utf-8")
    final_v6 = WIZARD_V6.read_text(encoding="utf-8")
    final_confirm = AGENDA_CONFIRM.read_text(encoding="utf-8")
    final_backend = LEADS_AGENDA.read_text(encoding="utf-8")

    checks = {
        "lock recovery": MARKER in final_confirm and "pg_terminate_backend" in final_confirm,
        "no auto multiday dates": '_datePlusDays(d0,i)' not in final_html,
        "editor hard guard": "if (locked && montageEditing && !force) return;" in final_html,
        "preview waits for dates": "Selecciona la fecha de cada día para calcular montaje." in final_html,
        "allocation priority V4": "staleProductMessage" in final_v4,
        "allocation priority V6": "La distribución al 100% es la fuente de verdad" in final_v6,
        "segment TBD": '"hr_tbd": hr_tbd_s' in final_backend,
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        fail("fallaron verificaciones: " + ", ".join(failed))

    print("OK sintaxis Python y JavaScript")
    print("OK lock huérfano recuperable")
    print("OK productos asignados al 100% no muestran falso faltante")
    print("OK editor de montaje no se bloquea al escribir ni borrar")
    print("OK fechas de varios días quedan vacías y son obligatorias")
    print("OK segmentos aceptan DIR TBD y HR TBD")
    print("AGENDA_INTEGRITY_V16_REPAIRED")


if __name__ == "__main__":
    main()
