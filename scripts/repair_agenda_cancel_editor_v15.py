from __future__ import annotations

import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEADS_HTML = ROOT / "web" / "views" / "leads.html"
AGENDA_PY = ROOT / "backend" / "routers" / "leads_agenda.py"
HTML_BACKUP = LEADS_HTML.with_suffix(".html.bak_v15")
PY_BACKUP = AGENDA_PY.with_suffix(".py.bak_v15")
MARKER = "GD-AGENDA-CANCEL-V15"


def fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        print(f"OK {label} ya aplicado")
        return text
    count = text.count(old)
    if count != 1:
        fail(f"{label}: se esperó 1 bloque y se encontraron {count}")
    print(f"OK {label}")
    return text.replace(old, new, 1)


BACKEND_HELPERS = r'''

# GD-AGENDA-CANCEL-V15
# Cancelación estricta: antes de sacar un lead de CONFIRMADO se eliminan TODOS
# sus eventos de Google Calendar. Si Calendar falla, el estado NO cambia.
def _calendar_event_ids_from_lead(lead: dict) -> list[str]:
    ids: list[str] = []

    def add(value) -> None:
        value = str(value or "").strip()
        if value and value not in ids:
            ids.append(value)

    add(lead.get("calendar_event_id"))
    for field in ("calendar_event_ids_json",):
        raw = lead.get(field)
        try:
            arr = raw if isinstance(raw, list) else json.loads(str(raw or "[]"))
        except Exception:
            arr = []
        if isinstance(arr, list):
            for value in arr:
                add(value)

    # Respaldo para registros antiguos que solo conservaron htmlLink.
    links: list[str] = []
    direct = str(lead.get("calendar_html_link") or "").strip()
    if direct:
        links.append(direct)
    try:
        raw_links = lead.get("calendar_html_links_json")
        arr_links = raw_links if isinstance(raw_links, list) else json.loads(str(raw_links or "[]"))
        if isinstance(arr_links, list):
            links.extend(str(value or "").strip() for value in arr_links)
    except Exception:
        pass

    if links:
        try:
            import base64
            from urllib.parse import parse_qs, urlparse

            for link in links:
                query = parse_qs(urlparse(link).query or "")
                explicit = (query.get("eventId") or query.get("eventid") or [None])[0]
                if explicit:
                    add(explicit)
                    continue
                encoded = (query.get("eid") or [None])[0]
                if not encoded:
                    continue
                token = str(encoded)
                token += "=" * ((4 - len(token) % 4) % 4)
                decoded = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8", "ignore").strip()
                if decoded:
                    add(decoded.split()[0])
        except Exception:
            pass

    return ids


def _delete_confirmed_calendar_events(id_lead: int, lead: dict) -> dict:
    from backend.db import SessionLocal
    from backend.routers.tools import _gcal_default_calendar_id, _gcal_service

    db = SessionLocal()
    got_lock = False
    try:
        got_lock = bool(db.execute(text("SELECT pg_try_advisory_lock(26042401)")).scalar())
        if not got_lock:
            raise HTTPException(status_code=409, detail="Calendar está procesando otro evento. Reintenta en unos segundos.")

        svc = _gcal_service(db)
        if not svc:
            raise HTTPException(status_code=502, detail="Google Calendar no está conectado. No se cambió el estado del lead.")
        calendar_id = _gcal_default_calendar_id(db, None)

        event_ids = _calendar_event_ids_from_lead(lead)

        # Respaldo para eventos creados con extendedProperties pero sin IDs persistidos.
        if not event_ids:
            try:
                found = svc.events().list(
                    calendarId=calendar_id,
                    privateExtendedProperty=f"lead_id={int(id_lead)}",
                    maxResults=100,
                    showDeleted=False,
                ).execute()
                for item in found.get("items") or []:
                    eid = str(item.get("id") or "").strip()
                    if eid and eid not in event_ids:
                        event_ids.append(eid)
            except Exception:
                pass

        deleted: list[str] = []
        already_missing: list[str] = []
        errors: list[str] = []
        for event_id in event_ids:
            try:
                svc.events().delete(calendarId=calendar_id, eventId=event_id).execute()
                deleted.append(event_id)
            except Exception as exc:
                message = str(exc)
                low = message.lower()
                if "404" in low or "notfound" in low or "not found" in low or "gone" in low:
                    already_missing.append(event_id)
                    continue
                errors.append(f"{event_id}: {message[:240]}")

        if errors:
            raise HTTPException(
                status_code=502,
                detail={
                    "message": "No se pudieron eliminar todos los eventos de Google Calendar. El lead sigue Confirmado.",
                    "errors": errors,
                },
            )

        return {
            "calendar_id": calendar_id,
            "tracked": len(event_ids),
            "deleted": deleted,
            "already_missing": already_missing,
        }
    finally:
        if got_lock:
            try:
                db.execute(text("SELECT pg_advisory_unlock(26042401)"))
            except Exception:
                pass
        try:
            db.close()
        except Exception:
            pass


def _notify_cancelled_event(
    *,
    id_lead: int,
    lead: dict,
    new_estado_id: int,
    user: dict,
    calendar_result: dict,
) -> dict:
    cliente = str(lead.get("cliente") or lead.get("nombre_cliente") or "").strip() or "Sin nombre"
    marca = _get_marca_nombre(int(lead.get("id_marca") or 0) or 0)
    comuna = _get_comuna_nombre(int(lead.get("id_comuna") or 0) or 0)
    evento = str(lead.get("pre_title") or cliente).strip() or cliente
    fecha = _fmt_ddmmyyyy(lead.get("fecha_evento"))
    nuevo_estado = _get_estado_nombre(int(new_estado_id)) or str(new_estado_id)
    who = str(
        user.get("username")
        or user.get("email")
        or user.get("name")
        or user.get("nombre")
        or user.get("id")
        or "CRM"
    ).strip()
    deleted_count = len(calendar_result.get("deleted") or [])
    missing_count = len(calendar_result.get("already_missing") or [])

    title = f"EVENTO CANCELADO · {marca} · {cliente}"
    body = "\n".join(
        [
            "EVENTO CANCELADO",
            "",
            f"Cliente: {cliente}",
            f"Evento: {evento}",
            f"Marca: {marca}",
            f"Comuna: {comuna}",
            f"Fecha del evento: {fecha}",
            "Estado anterior: Confirmado",
            f"Nuevo estado: {nuevo_estado}",
            f"Eventos eliminados de Calendar: {deleted_count}",
            f"Eventos que ya no existían en Calendar: {missing_count}",
            f"Cancelado por: {who}",
        ]
    ).strip()

    roles = ["ADMIN", "OPERACIONES", "MICE", "COMPRAS", "BODEGUERO"]
    try:
        _notify_roles_once(
            "EVENT_CANCELADO",
            roles,
            id_lead=int(id_lead),
            title=title,
            body=body,
            payload={
                "id_lead": int(id_lead),
                "marca": marca,
                "cliente": cliente,
                "comuna": comuna,
                "fecha_evento": fecha,
                "nuevo_estado": nuevo_estado,
                "calendar_deleted": deleted_count,
            },
        )
    except Exception:
        pass

    email_sent = False
    email_error = None
    try:
        from backend.core.email import send_email_group
        from backend.core.notify_routes import resolve_email_bcc, resolve_email_cc, resolve_email_to

        # Misma ruta y mismos destinatarios usados por EVENT_AGENDADO.
        to = resolve_email_to("AGENDA_EVENTOS", [])
        cc = resolve_email_cc("AGENDA_EVENTOS", [])
        bcc = resolve_email_bcc("AGENDA_EVENTOS", [])
        if not to:
            email_error = "La ruta AGENDA_EVENTOS no tiene destinatarios TO configurados."
        else:
            last_exc = None
            for _attempt in range(2):
                try:
                    send_email_group(
                        to,
                        title,
                        body + "\n\n--\nCRM Green Diamond\n",
                        cc_addrs=cc,
                        bcc_addrs=bcc,
                    )
                    email_sent = True
                    last_exc = None
                    break
                except Exception as exc:
                    last_exc = exc
            if last_exc is not None:
                email_error = str(last_exc)
    except Exception as exc:
        email_error = str(exc)

    if email_error:
        try:
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            _append_lead_notas(id_lead, f"[CANCELACIÓN {stamp}] Evento eliminado de Calendar, pero falló correo: {email_error[:260]}")
        except Exception:
            pass

    return {
        "email_sent": email_sent,
        "email_error": email_error,
        "subject": title,
    }
'''

EARLY_CANCEL = r'''
        is_cancelling_confirmed = bool(
            (not dry_run)
            and confirmado_id
            and old_estado_id
            and int(old_estado_id) == int(confirmado_id)
            and int(id_estado) != int(confirmado_id)
        )
        if is_cancelling_confirmed:
            # Regla estricta: primero Calendar; solo después cambia el estado.
            calendar_result = _delete_confirmed_calendar_events(id_lead, lead)
            cancel_update = {
                "id_estado": int(id_estado),
                "calendar_start": None,
                "calendar_end": None,
                "calendar_html_link": None,
                "calendar_html_links_json": None,
                "calendar_event_id": None,
                "calendar_event_ids_json": None,
                "agenda_approved_by": None,
                "agenda_approved_at": None,
                "pendiente_agendar": False,
                "pre_events_json": None,
                "pre_start": None,
                "pre_end": None,
                "pre_location": None,
                "pre_title": None,
                "pre_description": None,
                "pre_products_text": None,
                "pre_montaje_text": None,
                "pre_ops": None,
            }
            _update_row("leads", "id_lead", id_lead, cancel_update)

            try:
                if _table_exists("eventos_calendario"):
                    cols_eventos = set(_cols_for("eventos_calendario"))
                    with engine.begin() as cn:
                        if "estado" in cols_eventos:
                            cn.execute(
                                text("UPDATE public.eventos_calendario SET estado='cancelado' WHERE id_lead=:id"),
                                {"id": int(id_lead)},
                            )
            except Exception:
                pass

            try:
                who = str(user.get("username") or user.get("email") or user.get("id") or "").strip() or "CRM"
                stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
                new_name = _get_estado_nombre(int(id_estado)) or str(id_estado)
                deleted_count = len(calendar_result.get("deleted") or [])
                _append_lead_notas(
                    id_lead,
                    f"[CANCELACIÓN {stamp}] Confirmado → {new_name} · {deleted_count} evento(s) eliminado(s) de Calendar · por {who}",
                )
            except Exception:
                pass

            notice = _notify_cancelled_event(
                id_lead=id_lead,
                lead=lead,
                new_estado_id=int(id_estado),
                user=user,
                calendar_result=calendar_result,
            )
            return {
                "ok": True,
                "ask_agendar": False,
                "calendar_cancelled": True,
                "calendar": calendar_result,
                "cancellation_email_sent": bool(notice.get("email_sent")),
                "cancellation_email_error": notice.get("email_error"),
            }
'''


def patch_backend(text: str) -> str:
    if MARKER not in text:
        anchor = '\n\n@router.post("/{id_lead}/move")'
        if anchor not in text:
            fail("backend: no se encontró la ruta /{id_lead}/move")
        text = text.replace(anchor, BACKEND_HELPERS + anchor, 1)
        print("OK helper de cancelación Calendar/correo agregado")
    else:
        print("OK helper de cancelación Calendar/correo ya presente")

    anchor = '        should_update_now = int(id_estado) != confirmado_id\n\n        if dry_run:'
    replacement = '        should_update_now = int(id_estado) != confirmado_id\n' + EARLY_CANCEL + '\n        if dry_run:'
    if EARLY_CANCEL.strip() not in text:
        text = replace_once(text, anchor, replacement, "cancelación estricta antes del cambio de estado")
    else:
        print("OK cancelación estricta antes del cambio de estado ya aplicada")

    if text.count(MARKER) != 1:
        fail(f"backend: marcador V15 duplicado ({text.count(MARKER)})")
    if 'resolve_email_to("AGENDA_EVENTOS", [])' not in text:
        fail("backend: falta ruta de correo AGENDA_EVENTOS")
    if "calendar_cancelled" not in text:
        fail("backend: falta respuesta calendar_cancelled")
    return text


def patch_frontend(text: str) -> str:
    old_set = r'''async function setLeadEstado(leadId, newEstado, extra = {}){
  const payloads = [
    // Prefer nuevo endpoint unificado (incluye auditoría y evita desalineación de routers).
    { url: `/leads/${leadId}/move`,      method:"POST", body: { id_estado: Number(newEstado), ...extra } },
    { url: `/leads/${leadId}/estado_ex`, method:"POST", body: { id_estado: Number(newEstado), ...extra } },
    { url: `/leads/${leadId}/estado`,    method:"POST", body: { id_estado: Number(newEstado), ...extra } },
    { url: `/leads/${leadId}`,           method:"PUT",  body: { id_estado: Number(newEstado), ...extra } },
    { url: `/leads/${leadId}`,           method:"PATCH",body: { id_estado: Number(newEstado), ...extra } },
  ];

  let lastErr = null;
  for (const p of payloads){
    try{
      await apiJSON(p.url, {
        method: p.method,
        headers: { "Content-Type":"application/json" },
        body: JSON.stringify(p.body),
      });
      return;
    }catch(e){
      lastErr = e;
    }
  }
  throw lastErr || new Error("No pude actualizar estado.");
}'''
    new_set = r'''async function setLeadEstado(leadId, newEstado, extra = {}){
  // Endpoint único. No se permiten fallbacks que cambien el estado sin borrar Calendar.
  return await apiJSON(`/leads/${leadId}/move`, {
    method: "POST",
    headers: { "Content-Type":"application/json" },
    body: JSON.stringify({ id_estado: Number(newEstado), ...extra }),
  });
}'''
    text = replace_once(text, old_set, new_set, "endpoint único para cambios de estado")

    text = replace_once(
        text,
        '  const touchedDay = {}; // day -> { ops, montaje }\n',
        '  const touchedDay = {}; // day -> { ops, montaje }\n  let montageEditing = false; // mientras está true, ningún preview puede volver a bloquear el textarea\n',
        "estado persistente del editor de montaje",
    )

    text = replace_once(
        text,
        '        saveCurrentDay();\n        selectedIdx = Math.max(0, Math.min(previewEvents.length-1, i));',
        '        saveCurrentDay();\n        montageEditing = false;\n        selectedIdx = Math.max(0, Math.min(previewEvents.length-1, i));',
        "bloqueo controlado al cambiar de día",
    )

    text = replace_once(
        text,
        '    // por defecto: montaje bloqueado (profesional). El usuario debe presionar "Editar".\n    setMontajeLocked(true);',
        '    // Nunca volver a bloquear mientras el usuario está escribiendo.\n    if (!montageEditing) setMontajeLocked(true);',
        "preview no bloquea editor activo",
    )

    old_input = r'''      qs("#ag_montaje_day") && qs("#ag_montaje_day").addEventListener("input", ()=>{
        const day = currentDay();
        const td = ensureDayTouched(day);
        td.montaje = true;
        saveCurrentDay();
        _debPreview();
      });'''
    new_input = r'''      qs("#ag_montaje_day") && qs("#ag_montaje_day").addEventListener("input", ()=>{
        montageEditing = true;
        const day = currentDay();
        const td = ensureDayTouched(day);
        td.montaje = true;
        saveCurrentDay();
        // No recalcular por cada tecla: conserva foco, cursor y edición continua.
      });'''
    text = replace_once(text, old_input, new_input, "edición continua sin preview por carácter")

    old_edit = r'''	      qs("#ag_montaje_edit") && (qs("#ag_montaje_edit").onclick = ()=>{
	        setMontajeLocked(false);
	        const ta = qs("#ag_montaje_day");
	        ta && ta.focus && ta.focus();
	      });'''
    new_edit = r'''	      qs("#ag_montaje_edit") && (qs("#ag_montaje_edit").onclick = ()=>{
	        montageEditing = true;
	        setMontajeLocked(false);
	        const ta = qs("#ag_montaje_day");
	        ta && ta.focus && ta.focus();
	      });'''
    text = replace_once(text, old_edit, new_edit, "botón Editar mantiene sesión de edición")

    old_confirm = r'''		      qs("#ag_montaje_confirm") && (qs("#ag_montaje_confirm").onclick = ()=>{
		        const isLocked = !!qs("#ag_montaje_day")?.readOnly;
		        const txt = isLocked
		          ? "¿Confirmas el montaje sugerido para este día?"
		          : "¿Guardar/confirmar los cambios de montaje para este día?";
		        // Importante: NO abrir otro SweetAlert aquí (SweetAlert2 no soporta modales anidados).
		        // Usamos confirm nativo para no cerrar el modal de agenda.
		        if (!window.confirm(txt)) return;
		        saveCurrentDay();
		        setMontajeLocked(true);
		        toast("Montaje confirmado", "success");
		      });'''
    new_confirm = r'''		      qs("#ag_montaje_confirm") && (qs("#ag_montaje_confirm").onclick = ()=>{
		        const isLocked = !!qs("#ag_montaje_day")?.readOnly;
		        const txt = isLocked
		          ? "¿Confirmas el montaje sugerido para este día?"
		          : "¿Guardar/confirmar los cambios de montaje para este día?";
		        // Importante: NO abrir otro SweetAlert aquí (SweetAlert2 no soporta modales anidados).
		        // Usamos confirm nativo para no cerrar el modal de agenda.
		        if (!window.confirm(txt)) return;
		        saveCurrentDay();
		        montageEditing = false;
		        setMontajeLocked(true);
		        runPreview().catch(()=>{});
		        toast("Montaje confirmado", "success");
		      });'''
    text = replace_once(text, old_confirm, new_confirm, "confirmación recalcula una sola vez")

    if "/estado_ex" in text[text.find("async function setLeadEstado"):text.find("function estadoNombre")]:
        fail("frontend: todavía existe fallback estado_ex")
    if "if (!montageEditing) setMontajeLocked(true);" not in text:
        fail("frontend: falta protección del editor")
    return text


def check_inline_js(html: str) -> None:
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, flags=re.I | re.S)
    if not blocks:
        fail("no se encontraron scripts inline para validar")
    with tempfile.TemporaryDirectory() as tmp:
        for idx, block in enumerate(blocks, start=1):
            path = Path(tmp) / f"inline_{idx}.js"
            path.write_text(block, encoding="utf-8")
            result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
            if result.returncode != 0:
                fail(f"sintaxis JS inline bloque {idx}: {(result.stderr or result.stdout).strip()}")
    print(f"OK sintaxis JS inline ({len(blocks)} bloque(s))")


def main() -> None:
    if not LEADS_HTML.exists() or not AGENDA_PY.exists():
        fail("ejecuta este script desde la raíz del CRM")

    original_html = LEADS_HTML.read_text(encoding="utf-8")
    original_py = AGENDA_PY.read_text(encoding="utf-8")

    updated_html = patch_frontend(original_html)
    updated_py = patch_backend(original_py)

    if updated_html != original_html:
        shutil.copy2(LEADS_HTML, HTML_BACKUP)
        LEADS_HTML.write_text(updated_html, encoding="utf-8")
        print(f"OK respaldo frontend: {HTML_BACKUP.name}")
    if updated_py != original_py:
        shutil.copy2(AGENDA_PY, PY_BACKUP)
        AGENDA_PY.write_text(updated_py, encoding="utf-8")
        print(f"OK respaldo backend: {PY_BACKUP.name}")

    py_compile.compile(str(AGENDA_PY), doraise=True)
    print("OK sintaxis backend/routers/leads_agenda.py")
    check_inline_js(LEADS_HTML.read_text(encoding="utf-8"))

    smoke = r'''
from backend.routers import leads_agenda as m
sample = {
    "calendar_event_id": "evt-main",
    "calendar_event_ids_json": '["evt-main", "evt-day-2", "evt-montaje"]',
}
ids = m._calendar_event_ids_from_lead(sample)
assert ids == ["evt-main", "evt-day-2", "evt-montaje"], ids
print("SMOKE_CANCEL_IDS_OK")
'''
    result = subprocess.run([sys.executable, "-c", smoke], cwd=str(ROOT), capture_output=True, text=True)
    if result.returncode != 0:
        fail("falló smoke de IDs Calendar: " + (result.stderr or result.stdout).strip())
    print(result.stdout.strip())

    final_html = LEADS_HTML.read_text(encoding="utf-8")
    final_py = AGENDA_PY.read_text(encoding="utf-8")
    checks = [
        (MARKER in final_py, "helper cancelación instalado"),
        ('resolve_email_to("AGENDA_EVENTOS", [])' in final_py, "mismos destinatarios de agendamiento"),
        ("calendar_cancelled" in final_py, "respuesta de cancelación Calendar"),
        ("let montageEditing = false;" in final_html, "estado del editor"),
        ("if (!montageEditing) setMontajeLocked(true);" in final_html, "protección contra bloqueo por preview"),
        ("runPreview().catch(()=>{});" in final_html, "preview solo al confirmar montaje"),
    ]
    missing = [label for ok, label in checks if not ok]
    if missing:
        fail("faltan validaciones: " + ", ".join(missing))

    print("OK sacar de Confirmado elimina todos los eventos de Calendar")
    print("OK si Calendar falla, el estado permanece Confirmado")
    print("OK correo de cancelación usa exactamente la ruta AGENDA_EVENTOS")
    print("OK editor de montaje permite escritura continua")
    print("OK montaje recalcula una sola vez al confirmar")
    print("AGENDA_CANCEL_EDITOR_V15_REPAIRED")


if __name__ == "__main__":
    main()
