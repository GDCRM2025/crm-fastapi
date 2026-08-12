from datetime import date, datetime, timezone
from pathlib import Path
import json
from typing import Any
from fastapi import APIRouter, HTTPException, Query, Body, Depends, Request, UploadFile, File, Response
from fastapi.responses import FileResponse
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text
from backend.core.db import get_connection
from backend.core.activity_log import log_activity
from backend.core.rbac import role_key, user_id, username
from backend.core.stale_leads import auto_decline_stale_leads
from backend.core.pdf_parse import extract_text as _pdf_extract_text, parse_items_from_text as _pdf_parse_items, sample_lines as _pdf_sample_lines
from backend.core.public_tokens import sign as sign_public
from backend.core.storage import persistent_data_root
from backend.routers.auth import get_current_user
from backend.gd_intelligence.tracking import attach_lead_attribution

router = APIRouter()

DECLINADO_ID = 5

_LEAD_QUOTES_DIR = persistent_data_root() / "quotes"

def _lead_quote_path(id_lead: int) -> Path:
    d = _LEAD_QUOTES_DIR / f"lead_{id_lead}"
    d.mkdir(parents=True, exist_ok=True)
    return d

def now():
    return datetime.now(timezone.utc)

def _valid_fecha_evento(value: Any, field: str = "fecha_evento") -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        d = value.date()
    elif isinstance(value, date):
        d = value
    else:
        raw = str(value).strip()
        if not raw or raw.lower() in ("null", "none", "undefined"):
            return None
        raw = raw[:10]
        parsed = None
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
            try:
                parsed = datetime.strptime(raw, fmt).date()
                break
            except Exception:
                pass
        if parsed is None:
            raise HTTPException(400, f"{field} inválida. Usa formato YYYY-MM-DD.")
        d = parsed
    if d < date(2000, 1, 1) or d >= date(2100, 1, 1):
        raise HTTPException(400, f"{field} fuera de rango permitido (2000-01-01 a 2099-12-31).")
    return d

def _append_notas(conn, id_lead: int, text_block: str) -> None:
    """
    Append seguro a leads.notas (no rompe si la columna no existe).
    """
    try:
        cols = _cols_for("leads")
        if "notas" not in cols:
            return
        block = (text_block or "").strip()
        if not block:
            return
        conn.execute(
            text(
                """
                UPDATE public.leads
                SET notas = CASE
                  WHEN COALESCE(notas,'') = '' THEN :b
                  ELSE notas || E'\n\n' || :b
                END,
                updated_at = now()
                WHERE id_lead=:id
                """
            ),
            {"id": int(id_lead), "b": block},
        )
    except Exception:
        return

_LEAD_MOVEMENT_FIELDS = {
    "cliente",
    "nombre_cliente",
    "nombre",
    "email",
    "telefono",
    "direccion",
    "id_marca",
    "id_estado",
    "id_comuna",
    "id_tipo_cliente",
    "fecha_evento",
    "monto_cotizado",
    "plataforma",
    "num_cotizacion",
}


def _is_lead_operator(user: dict) -> bool:
    role = (role_key(user) or user.get("role") or user.get("rol") or "").strip().upper()
    compact = "".join(ch for ch in role if ch.isalnum())
    return (
        "ADMIN" in compact
        or "SUPERADMIN" in compact
        or ("EJECUTIV" in compact and "VENTA" in compact)
        or "VENTAS" in compact
    )


def _movement_comment(payload: dict) -> str:
    for key in ("movement_comment", "comentario", "comment", "nota_movimiento", "detalle", "motivo"):
        val = payload.get(key)
        if val is None:
            continue
        txt = str(val).strip()
        if txt:
            return txt[:4000]
    return ""


def _has_new_note_text(old_notas: Any, payload: dict) -> bool:
    if "notas" not in payload:
        return False
    old = str(old_notas or "").strip()
    new = str(payload.get("notas") or "").strip()
    return bool(new and new != old and len(new) > len(old))


def _lead_change_needs_comment(payload: dict, *, old_estado: Any = None, old_marca: Any = None, old_row: dict | None = None) -> bool:
    """
    Regla operativa:
    - Cambiar ESTADO del lead exige comentario.
    - Editar datos (nombre, comuna, telefono, fecha, marca, monto, etc.) queda auditado,
      pero no bloquea al ejecutivo.
    """
    if not payload:
        return False
    if _movement_comment(payload):
        return False
    if "id_estado" in payload and str(payload.get("id_estado") or "").strip():
        try:
            if int(payload.get("id_estado")) != int(old_estado or 0):
                return True
        except Exception:
            return True
    return False


def _append_movement_comment(conn, id_lead: int, payload: dict, user: dict) -> None:
    comment = _movement_comment(payload)
    if not comment:
        return
    who = (user.get("name") or user.get("nombre") or user.get("username") or user.get("id") or "Usuario")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    _append_notas(conn, id_lead, f"[MOVIMIENTO {ts}] {who}\n{comment}")

def _cols_for(table: str) -> set[str]:
    with get_connection() as conn:
        rows = conn.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name=:t
        """), {"t": table}).fetchall()
        return {r[0] for r in rows}

def _table_exists(table: str) -> bool:
    with get_connection() as conn:
        return bool(conn.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}).scalar())


def _run_auto_decline_before_leads_list(conn, user: dict) -> None:
    """
    Limpieza liviana antes de mostrar Kanban/listado.
    Corre con advisory lock y throttle para no castigar Passenger ni la BD.
    """
    try:
        got_lock = bool(conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": 25022027}).scalar())
        if not got_lock:
            return
        try:
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS public.system_kv (
                      key TEXT PRIMARY KEY,
                      value TEXT NOT NULL,
                      updated_at TIMESTAMP DEFAULT now()
                    )
                    """
                )
            )
            should_run = bool(
                conn.execute(
                    text(
                        """
                        SELECT NOT EXISTS (
                          SELECT 1
                          FROM public.system_kv
                          WHERE key='auto_decline_leads_list_last_run'
                            AND updated_at > (now() - INTERVAL '15 minutes')
                        )
                        """
                    )
                ).scalar()
            )
            if not should_run:
                return
            triggered_by = (
                user.get("nombre")
                or user.get("name")
                or user.get("email")
                or user.get("username")
                or "LEADS_LIST"
            )
            auto_decline_stale_leads(dry_run=False, triggered_by=str(triggered_by), conn=conn)
            conn.execute(
                text(
                    """
                    INSERT INTO public.system_kv(key, value, updated_at)
                    VALUES ('auto_decline_leads_list_last_run', 'ok', now())
                    ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()
                    """
                )
            )
            conn.commit()
        finally:
            try:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": 25022027})
            except Exception:
                pass
    except Exception:
        return

def _estado_nombre_conn(conn, id_estado: Any) -> str | None:
    try:
        if id_estado is None or str(id_estado).strip() == "":
            return None
        return conn.execute(
            text("SELECT nombre FROM public.estados_lead WHERE id_estado=:i LIMIT 1"),
            {"i": int(id_estado)},
        ).scalar()
    except Exception:
        return None

def _marca_nombre_conn(conn, id_marca: Any) -> str | None:
    try:
        if id_marca is None or str(id_marca).strip() == "":
            return None
        cols = {
            str(r[0])
            for r in conn.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema='public' AND table_name='marcas'
                    """
                )
            ).fetchall()
        }
        if "marca" in cols and "nombre" in cols:
            expr = "COALESCE(NULLIF(marca,''), NULLIF(nombre,''), 'Marca ' || id_marca::text)"
        elif "marca" in cols:
            expr = "COALESCE(NULLIF(marca,''), 'Marca ' || id_marca::text)"
        elif "nombre" in cols:
            expr = "COALESCE(NULLIF(nombre,''), 'Marca ' || id_marca::text)"
        else:
            return f"Marca {id_marca}"
        return conn.execute(
            text(f"SELECT {expr} FROM public.marcas WHERE id_marca=:i LIMIT 1"),
            {"i": int(id_marca)},
        ).scalar()
    except Exception:
        return None

def _comuna_nombre_conn(conn, id_comuna: Any) -> str | None:
    try:
        if id_comuna is None or str(id_comuna).strip() == "":
            return None
        cols = {
            str(r[0])
            for r in conn.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema='public' AND table_name='comunas'
                    """
                )
            ).fetchall()
        }
        if "nombre" in cols and "comuna" in cols:
            expr = "COALESCE(NULLIF(nombre,''), NULLIF(comuna,''), 'Comuna ' || id_comuna::text)"
        elif "nombre" in cols:
            expr = "COALESCE(NULLIF(nombre,''), 'Comuna ' || id_comuna::text)"
        elif "comuna" in cols:
            expr = "COALESCE(NULLIF(comuna,''), 'Comuna ' || id_comuna::text)"
        else:
            return f"Comuna {id_comuna}"
        return conn.execute(
            text(f"SELECT {expr} FROM public.comunas WHERE id_comuna=:i LIMIT 1"),
            {"i": int(id_comuna)},
        ).scalar()
    except Exception:
        return None


def _is_confirmed_estado_conn(conn, id_estado: Any) -> bool:
    try:
        name = _estado_nombre_conn(conn, id_estado) or ""
        return "CONFIRM" in str(name).upper()
    except Exception:
        return False


def _actor_name(user: dict) -> str:
    return str(user.get("name") or user.get("nombre") or user.get("username") or user.get("email") or user.get("id") or "Usuario").strip()


def _fmt_hist_value(v: Any) -> str:
    if v is None:
        return "vacío"
    s = str(v).strip()
    return s if s else "vacío"


def _lead_data_change_lines(conn, old_row: dict, incoming: dict) -> tuple[list[str], list[str]]:
    lines: list[str] = []
    fields: list[str] = []

    def add(field: str, label: str, old_v: Any, new_v: Any):
        old_s = _fmt_hist_value(old_v)
        new_s = _fmt_hist_value(new_v)
        if old_s == new_s:
            return
        fields.append(field)
        lines.append(f"{label}: {old_s} → {new_s}")

    if "cliente" in incoming or "nombre_cliente" in incoming or "nombre" in incoming:
        add("cliente", "Nombre cliente", old_row.get("cliente"), incoming.get("cliente"))
    if "email" in incoming:
        add("email", "Email", old_row.get("email"), incoming.get("email"))
    if "telefono" in incoming:
        add("telefono", "Teléfono", old_row.get("telefono"), incoming.get("telefono"))
    if "direccion" in incoming:
        add("direccion", "Dirección", old_row.get("direccion"), incoming.get("direccion"))
    if "id_marca" in incoming:
        old_name = _marca_nombre_conn(conn, old_row.get("id_marca")) or old_row.get("id_marca")
        new_name = _marca_nombre_conn(conn, incoming.get("id_marca")) or incoming.get("id_marca")
        add("id_marca", "Marca", old_name, new_name)
    if "id_comuna" in incoming:
        old_name = _comuna_nombre_conn(conn, old_row.get("id_comuna")) or old_row.get("id_comuna")
        new_name = _comuna_nombre_conn(conn, incoming.get("id_comuna")) or incoming.get("id_comuna")
        add("id_comuna", "Comuna", old_name, new_name)
    if "fecha_evento" in incoming:
        old_d = str(old_row.get("fecha_evento") or "")[:10]
        new_d = str(incoming.get("fecha_evento") or "")[:10]
        add("fecha_evento", "Fecha evento", old_d, new_d)
    if "monto_cotizado" in incoming:
        add("monto_cotizado", "Monto cotizado", old_row.get("monto_cotizado"), incoming.get("monto_cotizado"))
    if "plataforma" in incoming:
        add("plataforma", "Plataforma", old_row.get("plataforma"), incoming.get("plataforma"))
    if "num_cotizacion" in incoming:
        add("num_cotizacion", "N° cotización", old_row.get("num_cotizacion"), incoming.get("num_cotizacion"))
    if "id_tipo_cliente" in incoming:
        add("id_tipo_cliente", "Tipo cliente", old_row.get("id_tipo_cliente"), incoming.get("id_tipo_cliente"))
    return lines, fields

def _role(user: dict) -> str:
    return (user.get("role") or user.get("rol") or "").upper()


def _ensure_cotizaciones_pdf_col() -> None:
    """
    Guardamos referencia del PDF también en cotizaciones (además de leads),
    para que MICE / reportes puedan encontrarlo por cotización.
    """
    if not _table_exists("cotizaciones"):
        return
    cols = _cols_for("cotizaciones")
    if "pdf_path" in cols:
        return
    with get_connection() as conn:
        conn.execute(text("ALTER TABLE public.cotizaciones ADD COLUMN IF NOT EXISTS pdf_path TEXT"))
        conn.commit()

def _is_superadmin(role: str) -> bool:
    r = (role or "").upper()
    return r in ("SUPERADMIN", "SUPER_ADMIN", "SUPER ADMIN")

def _is_admin(role: str) -> bool:
    # "ADMIN" (no super) existe, pero queda acotado por marcas (no es global).
    r = (role or "").upper()
    return r in ("ADMIN", "1")


def _is_sales(role: str) -> bool:
    r = (role or "").upper()
    # Soporta variantes: EJECUTIVO / EJECUTIVA / EJECUTIV@ (mismo criterio)
    return ("EJECUTIV" in r) or ("VENTAS" in r) or ("VENDEDOR" in r)


def _restrict_leads_to_user_marcas(role: str) -> bool:
    """
    Regla:
    - SUPERADMIN: ve todo.
    - ADMIN y Ventas/Ejecutivos: restringidos a sus marcas asignadas (y/o leads asignados).
    - Resto: sin acceso.
    """
    if _is_superadmin(role):
        return False
    return _is_admin(role) or _is_sales(role)

def _can_access_leads(role: str) -> bool:
    """
    Permisos:
    - SUPERADMIN: acceso completo a leads.
    - ADMIN: acceso a leads (filtrado por marcas).
    - VENTAS/EJECUTIVOS: acceso a leads (filtrado por marcas).
    - Resto: NO tiene acceso a la sección leads.
    """
    if _is_superadmin(role):
        return True
    if _is_admin(role):
        return True
    if _is_sales(role):
        return True
    return False

def _user_match_keys_for_leads(user: dict) -> list[str]:
    """
    Keys para matchear leads.id_usuario (que en varios deploys es TEXT/INT y puede contener id/username/email).
    """
    keys: list[str] = []
    for k in (
        user.get("id"),
        user.get("username"),
        user.get("email"),
        user.get("sub"),
        user.get("name"),
        user.get("nombre"),
    ):
        s = str(k or "").strip().lower()
        if not s:
            continue
        if s not in keys:
            keys.append(s)
    return keys


def _user_marcas(user: dict) -> list[int]:
    marcas = user.get("marcas") or []
    out: list[int] = []
    for m in marcas:
        try:
            out.append(int(m))
        except Exception:
            pass
    if out:
        return out
    # fallback DB (evita depender del token)
    uid = user.get("id")
    if uid is None or not str(uid).isdigit():
        return []
    try:
        with get_connection() as conn:
            for table in ("usuarios_marcas", "usuario_marcas"):
                exists = bool(conn.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"public.{table}"}).scalar())
                if not exists:
                    continue
                rows = conn.execute(
                    text(f"SELECT id_marca FROM public.{table} WHERE id_usuario=:u ORDER BY id_marca"),
                    {"u": int(uid)},
                ).fetchall()
                found = [int(r[0]) for r in rows if r[0] is not None]
                if found:
                    return found
            has_legacy = bool(
                conn.execute(
                    text(
                        """
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema='public' AND table_name='usuarios' AND column_name='id_marca'
                        LIMIT 1
                        """
                    )
                ).scalar()
            )
            if has_legacy:
                mid = conn.execute(
                    text("SELECT id_marca FROM public.usuarios WHERE id_usuario=:u LIMIT 1"),
                    {"u": int(uid)},
                ).scalar()
                if mid is not None:
                    return [int(mid)]
            return []
    except Exception:
        return []

def _ensure_form_token_col() -> None:
    try:
        with get_connection() as conn:
            conn.execute(text("ALTER TABLE public.marcas ADD COLUMN IF NOT EXISTS form_token text"))
            conn.commit()
    except Exception:
        pass

def _ensure_leads_delete_cols() -> None:
    """
    Borrado lógico: no elimina registros (para auditoría), pero los oculta del CRM.
    """
    try:
        cols = _cols_for("leads")
    except Exception:
        cols = set()
    try:
        with get_connection() as conn:
            if "is_deleted" not in cols:
                conn.execute(text("ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT FALSE"))
            if "deleted_at" not in cols:
                conn.execute(text("ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP NULL"))
            if "deleted_by" not in cols:
                conn.execute(text("ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS deleted_by TEXT NULL"))
            conn.commit()
    except Exception:
        pass

def _ensure_leads_followup_cols() -> None:
    """
    Último seguimiento real del lead (NO confundir con updated_at).
    Se usa para:
    - Tareas: decidir si falta seguimiento
    - UI: banderas de seguimiento
    """
    try:
        cols = _cols_for("leads")
    except Exception:
        cols = set()
    try:
        with get_connection() as conn:
            if "seguimiento_at" not in cols:
                conn.execute(text("ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS seguimiento_at TIMESTAMPTZ NULL"))
            conn.commit()
    except Exception:
        # Nunca romper en prod por DDL
        pass

def _phone_cl_e164(value: Any) -> str | None:
    """
    Normaliza teléfono Chile para WhatsApp:
    - Input recomendado: 9 dígitos (incluye 9)
    - Guarda en DB: +569XXXXXXXX (sin espacios)
    """
    raw = str(value or "").strip()
    if not raw:
        return None
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return None
    # Si viene con código país
    if digits.startswith("56") and len(digits) >= 11:
        digits = digits[2:]
    # Mantener últimos 9 dígitos (móvil)
    if len(digits) > 9:
        digits = digits[-9:]
    if len(digits) != 9:
        # fallback: no tocar si no calza (evita romper teléfonos fijos legacy)
        return raw
    return "+56" + digits

def _clear_preagenda_fields(id_lead: int) -> None:
    cols = _cols_for("leads")

    # Si existen eventos ya creados en Google Calendar, al "desconfirmar" debemos
    # eliminarlos para evitar inconsistencias (eventos huérfanos en Calendar).
    # Para multi-día, se usa calendar_event_ids_json cuando existe.
    event_ids: list[str] = []
    try:
        with get_connection() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT
                      COALESCE(calendar_event_id,'') AS eid,
                      COALESCE(calendar_event_ids_json,'') AS eids_json
                    FROM public.leads
                    WHERE id_lead=:id
                    """
                ),
                {"id": id_lead},
            ).mappings().first()
        if row:
            eid = str(row.get("eid") or "").strip()
            if eid:
                event_ids.append(eid)
            eids_json = str(row.get("eids_json") or "").strip()
            if eids_json:
                try:
                    parsed = json.loads(eids_json)
                    if isinstance(parsed, list):
                        for x in parsed:
                            s = str(x or "").strip()
                            if s:
                                event_ids.append(s)
                except Exception:
                    pass
    except Exception:
        event_ids = []
    # De-dup manteniendo orden
    seen = set()
    event_ids = [x for x in event_ids if not (x in seen or seen.add(x))]

    def _try_delete_gcal_event(eid: str) -> bool:
        eid = (eid or "").strip()
        if not eid:
            return False
        try:
            from googleapiclient.discovery import build  # type: ignore
            from google.oauth2.credentials import Credentials  # type: ignore
            from google.auth.transport.requests import Request  # type: ignore
        except Exception:
            return False

        # Lee credenciales desde DB (gcal_tokens) igual que /tools/gcal/status
        try:
            if not _table_exists("gcal_tokens"):
                return False
            with get_connection() as conn:
                row = conn.execute(
                    text("SELECT creds_json FROM public.gcal_tokens ORDER BY id_token DESC LIMIT 1")
                ).fetchone()
            if not row or not row[0]:
                return False
            data = json.loads(row[0])
            scopes = ["https://www.googleapis.com/auth/calendar"]
            creds = Credentials.from_authorized_user_info(data, scopes)
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                # guardar refresh (insert-only)
                try:
                    payload = {
                        "token": creds.token,
                        "refresh_token": creds.refresh_token,
                        "token_uri": creds.token_uri,
                        "client_id": creds.client_id,
                        "client_secret": creds.client_secret,
                        "scopes": creds.scopes,
                    }
                    with get_connection() as conn:
                        conn.execute(
                            text("INSERT INTO public.gcal_tokens(creds_json, updated_at) VALUES (:c, now())"),
                            {"c": json.dumps(payload)},
                        )
                        conn.commit()
                except Exception:
                    pass

            svc = build("calendar", "v3", credentials=creds)
            cal_id = "simonurrutia.m@gmail.com"
            try:
                svc.events().delete(calendarId=cal_id, eventId=eid).execute()
            except Exception:
                # Si ya no existe, lo tratamos como éxito (idempotente)
                return True
            return True
        except Exception:
            return False

    deleted_ok = False
    deleted_n = 0
    if event_ids:
        for eid in event_ids:
            try:
                if _try_delete_gcal_event(str(eid)):
                    deleted_n += 1
            except Exception:
                pass
        deleted_ok = deleted_n == len(event_ids)

    data = {}
    for col in (
        "pre_title","pre_start","pre_end","pre_location","pre_products_text","pre_montaje_text",
        "pre_ops","pre_telefono","pre_direccion","pre_description",
        "calendar_start","calendar_end","calendar_event_id",
        "calendar_html_link","agenda_approved_at","agenda_approved_by",
        "calendar_event_ids_json","calendar_html_links_json",
        "pendiente_agendar",
    ):
        if col in cols:
            data[col] = None
    if "pendiente_agendar" in cols:
        data["pendiente_agendar"] = False
    if data:
        sets = ", ".join([f"{c} = :{c}" for c in data.keys()])
        data["id_lead"] = id_lead
        with get_connection() as conn:
            conn.execute(text(f"UPDATE public.leads SET {sets}, updated_at=now() WHERE id_lead=:id_lead"), data)
            # Log de trazabilidad (no bloqueante)
            try:
                if "notas" in cols:
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
                    if event_ids:
                        if deleted_n:
                            extra = f"[CALENDAR {ts}] Eliminados {deleted_n}/{len(event_ids)} evento(s) al sacar de CONFIRMADO"
                        else:
                            extra = f"[CALENDAR {ts}] Lead sacado de CONFIRMADO (eventos NO eliminados)"
                    else:
                        extra = f"[CALENDAR {ts}] Lead sacado de CONFIRMADO (sin eventos asociados)"
                    conn.execute(
                        text(
                            """
                            UPDATE public.leads
                            SET notas=CASE
                              WHEN notas IS NULL OR notas='' THEN :x
                              ELSE notas || E'\n' || :x
                            END
                            WHERE id_lead=:id
                            """
                        ),
                        {"id": id_lead, "x": extra},
                    )
            except Exception:
                pass
            conn.commit()
    if _table_exists("eventos_calendario"):
        with get_connection() as conn:
            conn.execute(text("DELETE FROM public.eventos_calendario WHERE id_lead=:id"), {"id": id_lead})
            conn.commit()

def _catalogos_payload() -> dict:
    # Reusa el endpoint principal de catálogos
    from backend.routers.catalogos import catalogos as _catalogos  # local import para evitar ciclos
    base = _catalogos() or {}

    # Aliases para compat con distintos frontends
    estados = base.get("estados_lead", [])
    tipos = base.get("tipos_cliente", [])
    payload = {
        "ok": True,
        "catalogos": base,
        **base,
        "estados": estados,
        "tipocliente": tipos,
        "tipos": tipos,
    }
    return payload

@router.get("/leads/catalogos")
def leads_catalogos(user: dict = Depends(get_current_user)):
    payload = _catalogos_payload()
    role = _role(user)
    if not _can_access_leads(role):
        raise HTTPException(403, "Sin permiso para Leads")
    user_marcas = _user_marcas(user)
    if _restrict_leads_to_user_marcas(role) and user_marcas:
        try:
            marcas = payload.get("marcas") or payload.get("catalogos", {}).get("marcas") or []
            filtered = [m for m in marcas if int(m.get("id_marca") or 0) in set(user_marcas)]
            if "marcas" in payload:
                payload["marcas"] = filtered
            if "catalogos" in payload and "marcas" in payload["catalogos"]:
                payload["catalogos"]["marcas"] = filtered
        except Exception:
            pass
    elif _restrict_leads_to_user_marcas(role) and not user_marcas:
        if "marcas" in payload:
            payload["marcas"] = []
        if "catalogos" in payload and "marcas" in payload["catalogos"]:
            payload["catalogos"]["marcas"] = []
    return payload

@router.get("/leads/catalogs")
def leads_catalogs():
    return _catalogos_payload()

@router.get("/leads")
def list_leads(
    limit: int = Query(50, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    month: int | None = Query(None, ge=1, le=12),
    year: int | None = Query(None, ge=2000, le=2100),
    include_no_date: bool = Query(True),
    all: bool = Query(False),
    user: dict = Depends(get_current_user),
):
    _ensure_leads_delete_cols()
    role = _role(user)
    if not _can_access_leads(role):
        raise HTTPException(403, "Sin permiso para Leads")

    marcas = _user_marcas(user)
    only_own = _restrict_leads_to_user_marcas(role)
    user_keys = _user_match_keys_for_leads(user)

    lead_cols = _cols_for("leads")
    marca_cols = _cols_for("marcas") if _table_exists("marcas") else set()
    comuna_cols = _cols_for("comunas") if _table_exists("comunas") else set()

    marca_name_expr = "m.marca" if "marca" in marca_cols else ("m.nombre" if "nombre" in marca_cols else "NULL")
    comuna_name_expr = "c.nombre" if "nombre" in comuna_cols else ("c.comuna" if "comuna" in comuna_cols else "NULL")
    quote_pdf_expr = "l.cotizacion_pdf_url" if "cotizacion_pdf_url" in lead_cols else "NULL::text AS cotizacion_pdf_url"

    extra_cols = []
    if "fecha_ingreso" in lead_cols:
        extra_cols.append("l.fecha_ingreso")
    if "seguimiento_at" in lead_cols:
        extra_cols.append("l.seguimiento_at")
    for col in ("pre_start", "pre_end", "calendar_start", "calendar_end", "hora_inicio", "hora_fin"):
        if col in lead_cols:
            extra_cols.append(f"l.{col}")
    for col in ("id_cotizacion_vigente", "calendar_html_link", "calendar_event_id", "agenda_approved_at", "agenda_approved_by"):
        if col in lead_cols:
            extra_cols.append(f"l.{col}")
    extra_sql = (", " + ", ".join(extra_cols)) if extra_cols else ""

    with get_connection() as conn:
        _run_auto_decline_before_leads_list(conn, user)
        where_parts = [
            "COALESCE(l.is_deleted,false)=false",
            "(l.fecha_evento IS NULL OR (l.fecha_evento >= DATE '2000-01-01' AND l.fecha_evento < DATE '2100-01-01'))",
        ]
        params = {"limit": limit, "offset": offset}
        if not all and month is None and year is None:
            today = datetime.now().date()
            month = today.month
            year = today.year
        if month is not None or year is not None:
            if month is None or year is None:
                raise HTTPException(400, "month y year deben enviarse juntos")
            d0 = date(year, month, 1)
            d1 = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
            month_filter = "l.fecha_evento >= :d0 AND l.fecha_evento < :d1"
            if include_no_date:
                where_parts.append(f"(l.fecha_evento IS NULL OR ({month_filter}))")
            else:
                where_parts.append(f"({month_filter})")
            params["d0"] = d0
            params["d1"] = d1
        if only_own:
            clauses: list[str] = []
            if "id_usuario" in lead_cols and user_keys:
                clauses.append("lower(NULLIF(btrim(COALESCE(l.id_usuario::text,'')) ,'')) = ANY(:user_keys)")
                params["user_keys"] = user_keys
            if marcas:
                clauses.append("l.id_marca = ANY(:marcas)")
                params["marcas"] = marcas
            if not clauses:
                return {"total": 0, "items": []}
            where_parts.append("(" + " OR ".join(clauses) + ")")

        where_sql = "WHERE " + " AND ".join(where_parts)
        total = conn.execute(text(f"SELECT COUNT(*) FROM public.leads l {where_sql}"), params).scalar_one()

        created_expr = "l.created_at"
        updated_expr = "l.updated_at"
        if "fecha_ingreso" in lead_cols:
            created_expr = "COALESCE(l.created_at, l.fecha_ingreso::timestamp)"
            updated_expr = "COALESCE(l.updated_at, l.fecha_ingreso::timestamp)"

        q = f"""
            SELECT
              l.id_lead, l.cliente, l.cliente AS nombre_cliente, l.email, l.telefono, l.direccion,
              l.id_marca, l.id_estado, l.id_comuna, l.id_tipo_cliente,
              l.fecha_evento, l.monto_cotizado, l.plataforma, l.notas, l.num_cotizacion, {quote_pdf_expr},
              {created_expr} AS created_at,
              {updated_expr} AS updated_at{extra_sql},
              COALESCE({marca_name_expr},'Sin Marca') AS marca,
              COALESCE(e.nombre,'') AS estado_nombre,
              COALESCE(e.color,'#64748b') AS estado_color,
              COALESCE({comuna_name_expr},'Sin Comuna') AS comuna,
              COALESCE(e.nombre,'') AS estado,
              COALESCE(e.color,'#64748b') AS color,
              COALESCE({comuna_name_expr},'Sin Comuna') AS comuna_nombre
            FROM public.leads l
            LEFT JOIN public.marcas m ON m.id_marca = l.id_marca
            LEFT JOIN public.estados_lead e ON e.id_estado = l.id_estado
            LEFT JOIN public.comunas c ON c.id_comuna = l.id_comuna
            {where_sql}
            ORDER BY
              COALESCE({updated_expr}, {created_expr}) DESC NULLS LAST,
              {created_expr} DESC NULLS LAST,
              l.id_lead DESC
            LIMIT :limit OFFSET :offset
        """
        rows = conn.execute(text(q), params).mappings().all()
        return {"total": int(total), "items": jsonable_encoder(list(rows))}


@router.get("/leads/by_ids")
def leads_by_ids(
    ids: str = Query(..., description="Lista separada por coma de id_lead"),
    user: dict = Depends(get_current_user),
):
    """
    Devuelve leads por IDs (para drilldowns rápidos desde Dashboard).
    Aplica la misma restricción por marcas del usuario cuando corresponde.
    """
    _ensure_leads_delete_cols()
    role = _role(user)
    if not _can_access_leads(role):
        raise HTTPException(403, "Sin permiso para Leads")

    raw = [x.strip() for x in str(ids or "").split(",") if x.strip()]
    lead_ids: list[int] = []
    for x in raw[:600]:
        if not x.isdigit():
            continue
        try:
            lead_ids.append(int(x))
        except Exception:
            continue
    lead_ids = [x for x in lead_ids if x > 0]
    if not lead_ids:
        return {"ok": True, "items": []}

    marcas = _user_marcas(user)
    only_own = _restrict_leads_to_user_marcas(role)
    user_keys = _user_match_keys_for_leads(user)

    lead_cols = _cols_for("leads")
    marca_cols = _cols_for("marcas") if _table_exists("marcas") else set()
    comuna_cols = _cols_for("comunas") if _table_exists("comunas") else set()

    marca_name_expr = "m.marca" if "marca" in marca_cols else ("m.nombre" if "nombre" in marca_cols else "NULL")
    comuna_name_expr = "c.nombre" if "nombre" in comuna_cols else ("c.comuna" if "comuna" in comuna_cols else "NULL")
    quote_pdf_expr = "l.cotizacion_pdf_url" if "cotizacion_pdf_url" in lead_cols else "NULL::text AS cotizacion_pdf_url"

    extra_cols = []
    if "fecha_ingreso" in lead_cols:
        extra_cols.append("l.fecha_ingreso")
    if "seguimiento_at" in lead_cols:
        extra_cols.append("l.seguimiento_at")
    for col in ("pre_start", "pre_end", "calendar_start", "calendar_end", "hora_inicio", "hora_fin"):
        if col in lead_cols:
            extra_cols.append(f"l.{col}")
    for col in ("id_cotizacion_vigente", "calendar_html_link", "calendar_event_id", "agenda_approved_at", "agenda_approved_by"):
        if col in lead_cols:
            extra_cols.append(f"l.{col}")
    extra_sql = (", " + ", ".join(extra_cols)) if extra_cols else ""

    with get_connection() as conn:
        where_parts = ["COALESCE(l.is_deleted,false)=false", "l.id_lead = ANY(:ids)"]
        params: dict[str, Any] = {"ids": lead_ids}
        if only_own:
            clauses: list[str] = []
            if "id_usuario" in lead_cols and user_keys:
                clauses.append("lower(NULLIF(btrim(COALESCE(l.id_usuario::text,'')) ,'')) = ANY(:user_keys)")
                params["user_keys"] = user_keys
            if marcas:
                clauses.append("l.id_marca = ANY(:marcas)")
                params["marcas"] = marcas
            if not clauses:
                return {"ok": True, "items": []}
            where_parts.append("(" + " OR ".join(clauses) + ")")

        where_sql = "WHERE " + " AND ".join(where_parts)
        created_expr = "l.created_at"
        updated_expr = "l.updated_at"
        if "fecha_ingreso" in lead_cols:
            created_expr = "COALESCE(l.created_at, l.fecha_ingreso::timestamp)"
            updated_expr = "COALESCE(l.updated_at, l.fecha_ingreso::timestamp)"

        q = f"""
            SELECT
              l.id_lead, l.cliente, l.cliente AS nombre_cliente, l.email, l.telefono, l.direccion,
              l.id_marca, l.id_estado, l.id_comuna, l.id_tipo_cliente,
              l.fecha_evento, l.monto_cotizado, l.plataforma, l.notas, l.num_cotizacion, {quote_pdf_expr},
              {created_expr} AS created_at,
              {updated_expr} AS updated_at{extra_sql},
              COALESCE({marca_name_expr},'Sin Marca') AS marca,
              COALESCE(e.nombre,'') AS estado_nombre,
              COALESCE(e.color,'#64748b') AS estado_color,
              COALESCE({comuna_name_expr},'Sin Comuna') AS comuna,
              COALESCE(e.nombre,'') AS estado,
              COALESCE(e.color,'#64748b') AS color,
              COALESCE({comuna_name_expr},'Sin Comuna') AS comuna_nombre
            FROM public.leads l
            LEFT JOIN public.marcas m ON m.id_marca = l.id_marca
            LEFT JOIN public.estados_lead e ON e.id_estado = l.id_estado
            LEFT JOIN public.comunas c ON c.id_comuna = l.id_comuna
            {where_sql}
            ORDER BY (l.fecha_evento IS NULL) ASC, l.fecha_evento ASC, {created_expr} DESC, l.id_lead DESC
        """
        rows = conn.execute(text(q), params).mappings().all()
        return {"ok": True, "items": list(rows)}


@router.get("/leads/{id_lead}")
def get_lead(id_lead: int, user: dict = Depends(get_current_user)):
    _ensure_leads_delete_cols()

    marca_cols = _cols_for("marcas") if _table_exists("marcas") else set()
    comuna_cols = _cols_for("comunas") if _table_exists("comunas") else set()

    marca_expr = "COALESCE(m.marca,'')"
    if "nombre" in marca_cols:
        marca_expr = "COALESCE(m.marca, m.nombre, '')"

    comuna_expr = "COALESCE(c.nombre,'')"
    if "nombre" not in comuna_cols and "comuna" in comuna_cols:
        comuna_expr = "COALESCE(c.comuna,'')"
    elif "nombre" not in comuna_cols and "comuna" not in comuna_cols:
        comuna_expr = "''"

    q = f"""
        SELECT
          l.*,
          l.cliente AS nombre_cliente,
          {marca_expr} AS marca_nombre,
          COALESCE(m.logo_path,'') AS logo_url,
          {comuna_expr} AS comuna_nombre,
          COALESCE(c.neto,0) AS comuna_neto,
          COALESCE(e.nombre,'') AS estado_nombre,
          COALESCE(e.color,'#64748b') AS estado_color,
          COALESCE(tc.tipo,'') AS tipo_cliente_nombre
        FROM public.leads l
        LEFT JOIN public.marcas m ON m.id_marca=l.id_marca
        LEFT JOIN public.comunas c ON c.id_comuna=l.id_comuna
        LEFT JOIN public.estados_lead e ON e.id_estado=l.id_estado
        LEFT JOIN public.tipos_cliente tc ON tc.id_tipo_cliente=l.id_tipo_cliente
        WHERE l.id_lead=:id AND COALESCE(l.is_deleted,false)=false
    """

    with get_connection() as conn:
        row = conn.execute(text(q), {"id": id_lead}).mappings().first()
        if not row:
            raise HTTPException(404, "Lead no existe")
        data = dict(row)
        role = _role(user)
        if not _is_superadmin(role):
            marcas = _user_marcas(user)
            user_keys = _user_match_keys_for_leads(user)
            lead_marca = int(data.get("id_marca") or 0)
            assigned_ok = False
            try:
                raw_owner = str(data.get("id_usuario") or "").strip().lower()
                if raw_owner and user_keys and raw_owner in set(user_keys):
                    assigned_ok = True
            except Exception:
                assigned_ok = False
            if (not assigned_ok) and ((not marcas) or (lead_marca not in set(marcas))):
                raise HTTPException(403, "Sin acceso a este lead")
        if not data.get("logo_url"):
            try:
                from backend.core.quote_assets import logo_for, normalize_marca
                key = normalize_marca(data.get("marca_nombre") or data.get("marca") or "")
                if key:
                    data["logo_url"] = logo_for(key, prefer_local=True)
            except Exception:
                pass
        return data


@router.get("/leads/{id_lead}/vcard")
def lead_vcard(id_lead: int, user: dict = Depends(get_current_user)):
    """
    Genera una vCard (.vcf) para guardar el contacto del cliente en el teléfono.
    """
    role = _role(user)
    if not _can_access_leads(role):
        raise HTTPException(403, "Sin permiso para Leads")

    lead = get_lead(id_lead, user)

    def esc(s: str) -> str:
        s = (s or "").replace("\r", "").replace("\n", "\\n")
        s = s.replace(";", "\\;").replace(",", "\\,")
        return s

    name = str(lead.get("cliente") or lead.get("nombre_cliente") or "").strip() or f"Lead {id_lead}"
    email = str(lead.get("email") or "").strip()
    tel = str(lead.get("telefono") or "").strip()
    org = str(lead.get("marca_nombre") or lead.get("marca") or "").strip()
    comuna = str(lead.get("comuna_nombre") or lead.get("comuna") or "").strip()
    note_bits = []
    if comuna:
        note_bits.append(f"Comuna: {comuna}")
    if org:
        note_bits.append(f"Marca: {org}")
    note = " · ".join(note_bits)

    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"FN:{esc(name)}",
    ]
    parts = [p for p in name.split(" ") if p.strip()]
    if parts:
        first = parts[0]
        last = " ".join(parts[1:]) if len(parts) > 1 else ""
        lines.append(f"N:{esc(last)};{esc(first)};;;")
    if org:
        lines.append(f"ORG:{esc(org)}")
    if tel:
        lines.append(f"TEL;TYPE=CELL:{esc(tel)}")
    if email:
        lines.append(f"EMAIL;TYPE=INTERNET:{esc(email)}")
    if note:
        lines.append(f"NOTE:{esc(note)}")
    lines.append("END:VCARD")
    body = "\r\n".join(lines) + "\r\n"

    filename = f"contacto_lead_{id_lead}.vcf"
    return Response(
        content=body,
        media_type="text/vcard; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename=\"{filename}\"'},
    )


@router.get("/leads/{id_lead}/vcard_link")
def lead_vcard_link(id_lead: int, user: dict = Depends(get_current_user)):
    """
    Devuelve un link público (firmado) para descargar la vCard desde el teléfono,
    sin depender del JWT (ideal para compartir con ejecutivos por WhatsApp).
    """
    role = _role(user)
    if not _can_access_leads(role):
        raise HTTPException(status_code=403, detail="Sin permiso para Leads")

    # Valida existencia y permisos por marca (misma regla que las demás acciones de lead).
    get_lead(id_lead, user)

    token = sign_public({"t": "vcard", "id_lead": int(id_lead)}, ttl_seconds=30 * 24 * 3600)
    return {"ok": True, "url": f"/public/vcard/{token}", "ttl_days": 30}

@router.delete("/leads/{id_lead}")
def delete_lead(id_lead: int, request: Request, payload: dict | None = Body(None), user: dict = Depends(get_current_user)):
    """
    Solo Admin/SuperAdmin puede "borrar" un lead.
    Implementado como borrado lógico para no romper integridad ni auditoría.
    """
    _ensure_leads_delete_cols()
    role = _role(user)
    if role not in ("ADMIN", "SUPERADMIN"):
        raise HTTPException(403, "Solo Admin puede borrar leads")
    who = (user.get("name") or user.get("username") or str(user.get("id") or "")).strip() or "ADMIN"
    motivo = _movement_comment(payload or {})
    if len(motivo) < 8:
        raise HTTPException(400, "Motivo obligatorio: para enviar un lead a eliminados debes explicar por qué.")
    with get_connection() as conn:
        lead_row = conn.execute(
            text(
                """
                SELECT l.id_lead, COALESCE(l.cliente,'') AS cliente, l.id_estado,
                       COALESCE(e.nombre,'') AS estado
                FROM public.leads l
                LEFT JOIN public.estados_lead e ON e.id_estado=l.id_estado
                WHERE l.id_lead=:id
                """
            ),
            {"id": id_lead},
        ).mappings().first()
        if not lead_row:
            raise HTTPException(404, "Lead no existe")
        conn.execute(
            text(
                """
                UPDATE public.leads
                SET is_deleted=TRUE, deleted_at=now(), deleted_by=:who, updated_at=now()
                WHERE id_lead=:id
                """
            ),
            {"id": id_lead, "who": who},
        )
        _append_notas(conn, id_lead, f"[ELIMINADO {datetime.now().strftime('%Y-%m-%d %H:%M')}] {who}\n{motivo}")
        log_activity(
            conn,
            username=username(user),
            user_id=user_id(user),
            role=role_key(user),
            action="lead.delete",
            entity_type="lead",
            entity_id=int(id_lead),
            meta={
                "cliente": lead_row.get("cliente"),
                "old_estado_id": lead_row.get("id_estado"),
                "old_estado": lead_row.get("estado"),
                "deleted_by": who,
                "motivo": motivo,
            },
            request=request,
        )
        conn.commit()
    return {"ok": True, "id_lead": id_lead}

MANUAL_LEAD_SOURCES = {"WEB", "WHATSAPP", "TELÉFONO", "TELEFONO", "INSTAGRAM", "FACEBOOK", "GOOGLE", "REFERIDO", "EVENTO", "OTRO"}


@router.post("/leads")
def create_lead(payload: dict = Body(...), request: Request = None, user: dict = Depends(get_current_user)):
    # mínimos razonables
    cliente = (payload.get("cliente") or payload.get("nombre_cliente") or payload.get("nombre") or "").strip()
    if not cliente:
        raise HTTPException(400, "Nombre cliente es obligatorio")

    id_estado = int(payload.get("id_estado") or 1)

    role = _role(user)
    user_marcas = _user_marcas(user)
    # Marca:
    # - SUPERADMIN: puede crear sin marca (0), pero idealmente el frontend la enviará.
    # - ADMIN/Ventas: debe quedar en una marca válida del usuario.
    try:
        id_marca = int(payload.get("id_marca") or 0)
    except Exception:
        id_marca = 0
    if (not _is_superadmin(role)):
        if id_marca <= 0:
            if user_marcas:
                id_marca = int(user_marcas[0])
            else:
                raise HTTPException(403, "Usuario sin marcas asignadas")
        if user_marcas and (id_marca not in set(user_marcas)):
            raise HTTPException(403, "No autorizado para crear leads en esta marca")
    # OJO: varios formularios externos mandan comuna por nombre (string) o mandan basura en id_comuna.
    # Si falla el cast, lo tratamos como 0 y resolvemos por nombre dentro de la transacción.
    try:
        id_comuna = int(payload.get("id_comuna") or 0)
    except Exception:
        id_comuna = 0
    comuna_name = (payload.get("comuna") or payload.get("comuna_nombre") or payload.get("comunaName") or "").strip()
    marca_name = (payload.get("marca") or payload.get("brand") or "").strip()
    id_tipo_cliente = payload.get("id_tipo_cliente")
    id_tipo_cliente = int(id_tipo_cliente) if id_tipo_cliente not in (None, "", "null") else None

    explicit_source = payload.get("lead_source")
    lead_source = str(explicit_source or payload.get("plataforma") or "OTRO").strip().upper()[:80]
    if explicit_source is not None and lead_source not in MANUAL_LEAD_SOURCES:
        raise HTTPException(422, "Origen del lead inválido")
    if lead_source == "TELEFONO":
        lead_source = "TELÉFONO"

    with get_connection() as conn:
        def _norm_key(s: str) -> str:
            """Normalize text keys to reduce mismatches from accents/punctuation/casing."""
            try:
                import unicodedata

                s2 = unicodedata.normalize("NFD", str(s or "").strip().lower())
                s2 = "".join(ch for ch in s2 if unicodedata.category(ch) != "Mn")
            except Exception:
                s2 = str(s or "").strip().lower()
            out = []
            for ch in s2:
                if ch.isalnum():
                    out.append(ch)
            return "".join(out)

        # Resolver marca por nombre si viene en texto y no tenemos id_marca.
        if id_marca <= 0 and marca_name:
            try:
                mid = conn.execute(
                    text(
                        """
                        SELECT id_marca
                        FROM public.marcas
                        WHERE UPPER(COALESCE(nombre,marca)) = UPPER(:m)
                           OR UPPER(marca) = UPPER(:m)
                        LIMIT 1
                        """
                    ),
                    {"m": marca_name},
                ).scalar()
                if mid:
                    id_marca = int(mid)
            except Exception:
                pass

        # Resolver marca con normalización (tildes/puntos/espacios). Esto arregla leads
        # creados por integraciones que mandan "Camaleón", "camaleon.", etc.
        if id_marca <= 0 and marca_name:
            try:
                want = _norm_key(marca_name)
                if want:
                    mrows = conn.execute(
                        text("SELECT id_marca, COALESCE(nombre,marca) AS nombre, marca FROM public.marcas")
                    ).mappings().all()
                    idx = {}
                    for r in mrows:
                        for k in (r.get("nombre"), r.get("marca")):
                            kk = _norm_key(str(k or ""))
                            if kk and kk not in idx:
                                idx[kk] = int(r.get("id_marca") or 0)
                    if want in idx and int(idx[want] or 0) > 0:
                        id_marca = int(idx[want])
            except Exception:
                pass

        # Resolver comuna por nombre si no viene id_comuna válido.
        if id_comuna <= 0 and comuna_name:
            try:
                cid = conn.execute(
                    text(
                        """
                        SELECT id_comuna
                        FROM public.comunas
                        WHERE UPPER(COALESCE(nombre,comuna)) = UPPER(:c)
                           OR UPPER(nombre) LIKE UPPER(:c_like)
                        LIMIT 1
                        """
                    ),
                    {"c": comuna_name, "c_like": comuna_name + "%"},
                ).scalar()
                if cid:
                    id_comuna = int(cid)
            except Exception:
                pass

        # Resolver comuna con normalización (tildes/puntos) para integraciones.
        if id_comuna <= 0 and comuna_name:
            try:
                want = _norm_key(comuna_name)
                if want:
                    crows = conn.execute(
                        text("SELECT id_comuna, COALESCE(nombre,comuna) AS nombre, comuna FROM public.comunas")
                    ).mappings().all()
                    idx = {}
                    for r in crows:
                        for k in (r.get("nombre"), r.get("comuna")):
                            kk = _norm_key(str(k or ""))
                            if kk and kk not in idx:
                                idx[kk] = int(r.get("id_comuna") or 0)
                    if want in idx and int(idx[want] or 0) > 0:
                        id_comuna = int(idx[want])
            except Exception:
                pass

        if id_marca <= 0:
            raise HTTPException(400, "Marca es obligatoria")

        # validar estado existe
        ok_estado = conn.execute(text("SELECT 1 FROM public.estados_lead WHERE id_estado=:i"), {"i": id_estado}).first()
        if not ok_estado:
            raise HTTPException(400, "id_estado inválido")
        fecha_evento = _valid_fecha_evento(payload.get("fecha_evento"))
        if not fecha_evento:
            raise HTTPException(400, "Fecha evento es obligatoria")

        # comuna puede quedar en 0 si el lead viene incompleto; nombre/fecha/marca no.
        # Mensaje proveniente de formularios/extensión:
        # - A veces llega como `mensaje` además de `notas`. En ese caso NO queremos perderlo.
        # - Mantenemos todo en `leads.notas` por compatibilidad (luego migraremos a notas en líneas).
        def _get_msg(p: dict) -> str:
            for k in ("mensaje", "Mensaje", "comentario", "Comentario", "message", "Message"):
                v = p.get(k)
                if v is None:
                    continue
                s = str(v).strip()
                if s:
                    return s
            return ""

        notas_raw = payload.get("notas")
        notas_txt = ""
        if notas_raw is not None:
            notas_txt = str(notas_raw).strip()
        msg_txt = _get_msg(payload)

        if not notas_txt and msg_txt:
            notas_in = msg_txt
        elif notas_txt and msg_txt:
            # Si el mensaje ya está dentro de las notas, no lo duplicamos.
            if msg_txt.lower() in notas_txt.lower():
                notas_in = notas_txt
            else:
                notas_in = f"{notas_txt}\n\n[MENSAJE]\n{msg_txt}"
        else:
            notas_in = notas_txt or None

        _ensure_leads_followup_cols()
        new_id = conn.execute(text("""
            INSERT INTO public.leads(
              cliente,email,telefono,direccion,id_marca,id_estado,id_comuna,id_tipo_cliente,
              fecha_evento,monto_cotizado,plataforma,notas,num_cotizacion,created_at,updated_at
            ) VALUES (
              :cliente,:email,:telefono,:direccion,:id_marca,:id_estado,:id_comuna,:id_tipo_cliente,
              :fecha_evento,:monto_cotizado,:plataforma,:notas,:num_cotizacion, now(), now()
            )
            RETURNING id_lead
        """), {
            "cliente": cliente,
            "email": payload.get("email"),
            "telefono": _phone_cl_e164(payload.get("telefono")),
            "direccion": payload.get("direccion"),
            "id_marca": id_marca,
            "id_estado": id_estado,
            "id_comuna": id_comuna,
            "id_tipo_cliente": id_tipo_cliente,
            "fecha_evento": fecha_evento,
            "monto_cotizado": payload.get("monto_cotizado") or 0,
            "plataforma": lead_source,
            "notas": notas_in,
            "num_cotizacion": payload.get("num_cotizacion"),
        }).scalar_one()
        tracking_ready = bool(conn.execute(text("SELECT to_regclass('public.wi_lead_attribution') IS NOT NULL")).scalar())
        session_id = payload.get("gd_session_id") or (request.headers.get("X-GD-Session-ID") if request else None)
        if tracking_ready and session_id:
            try:
                attach_lead_attribution(conn, lead_id=int(new_id), session_id=session_id)
            except ValueError:
                pass
        elif tracking_ready:
            campaign_id = payload.get("campaign_id")
            if campaign_id:
                valid_campaign = conn.execute(text("SELECT id FROM public.wi_marketing_campaigns WHERE id=:id"), {"id": campaign_id}).scalar()
                if not valid_campaign:
                    raise HTTPException(422, "Campaña inválida")
            conn.execute(text("""
              INSERT INTO public.wi_lead_attribution(
                lead_id,campaign_id,source,attribution_method,confidence,is_manual,updated_by
              ) VALUES (:lead_id,:campaign_id,:source,'MANUAL_SOURCE',1.0,true,:actor)
              ON CONFLICT(lead_id) DO NOTHING
            """), {"lead_id": int(new_id), "campaign_id": campaign_id, "source": lead_source, "actor": username(user)})
        conn.commit()
        return {"ok": True, "id_lead": int(new_id)}


@router.post("/leads/form")
def create_lead_from_form(payload: dict = Body(...), request: Request = None):
    """
    Endpoint público para formularios web.
    Requiere token por marca (X-Form-Token o payload.token).
    """
    _ensure_form_token_col()

    token = request.headers.get("X-Form-Token") if request else None
    if not token:
        token = payload.get("token") or payload.get("form_token")
    if not token:
        raise HTTPException(401, "token requerido")

    marca_in = payload.get("marca") or payload.get("brand") or payload.get("id_marca")

    with get_connection() as conn:
        # Si el formulario no manda marca (pasa en integraciones), podemos inferirla por token.
        # Seguridad: el token es secreto y único por marca, así que el match exacto es suficiente.
        if not marca_in:
            mrow = conn.execute(
                text(
                    """
                    SELECT id_marca, COALESCE(nombre,marca) AS nombre, form_token
                    FROM public.marcas
                    WHERE COALESCE(form_token,'') <> '' AND form_token=:t
                    LIMIT 1
                    """
                ),
                {"t": str(token)},
            ).mappings().first()
            if not mrow:
                raise HTTPException(400, "marca requerida")
        else:
            mrow = None

        # resolver marca
        def _norm_key_local(s: str) -> str:
            """
            Normalización fuerte para matchear marca desde formularios:
            - lowercase
            - sin tildes
            - solo alfanumérico
            """
            try:
                import unicodedata

                s2 = unicodedata.normalize("NFD", str(s or "").strip().lower())
                s2 = "".join(ch for ch in s2 if unicodedata.category(ch) != "Mn")
            except Exception:
                s2 = str(s or "").strip().lower()
            out = []
            for ch in s2:
                if ch.isalnum():
                    out.append(ch)
            return "".join(out)

        if not mrow:
            if str(marca_in).isdigit():
                mrow = conn.execute(
                    text("SELECT id_marca, COALESCE(nombre,marca) AS nombre, form_token FROM public.marcas WHERE id_marca=:id"),
                    {"id": int(marca_in)},
                ).mappings().first()
            else:
                marca_txt = str(marca_in or "").strip()
                # 1) Match directo (rápido)
                mrow = conn.execute(
                    text(
                        """
                        SELECT id_marca, COALESCE(nombre,marca) AS nombre, form_token
                        FROM public.marcas
                        WHERE UPPER(COALESCE(nombre,marca))=UPPER(:m) OR UPPER(marca)=UPPER(:m)
                        LIMIT 1
                        """
                    ),
                    {"m": marca_txt},
                ).mappings().first()

                # 2) Fallback robusto (acentos/puntos/espacios/encoding raro)
                if not mrow and marca_txt:
                    want = _norm_key_local(marca_txt)
                    if want:
                        rows = conn.execute(
                            text("SELECT id_marca, COALESCE(nombre,marca) AS nombre, marca, form_token FROM public.marcas")
                        ).mappings().all()
                        idx = {}
                        for r in rows:
                            for k in (r.get("nombre"), r.get("marca")):
                                kk = _norm_key_local(str(k or ""))
                                if kk and kk not in idx:
                                    idx[kk] = r
                        mrow = idx.get(want)
        if not mrow:
            raise HTTPException(404, "marca no existe")

        form_token = (mrow.get("form_token") or "").strip()
        if not form_token:
            raise HTTPException(401, "marca sin token configurado")
        # Si inferimos por token, acá ya coincide.
        if str(token) != str(form_token):
            raise HTTPException(401, "token inválido")

        # estado NUEVO (si existe)
        estado_nuevo = conn.execute(
            text("SELECT id_estado FROM public.estados_lead WHERE UPPER(nombre) LIKE '%NUEVO%' LIMIT 1")
        ).scalar() or 1

        # comuna por nombre
        comuna_name = (payload.get("comuna") or payload.get("comuna_nombre") or payload.get("Comuna") or payload.get("comunaName") or "").strip()
        id_comuna = None
        if comuna_name:
            # Match exact (case-insensitive) first, then fallback to prefix.
            id_comuna = conn.execute(
                text(
                    """
                    SELECT id_comuna
                    FROM public.comunas
                    WHERE UPPER(COALESCE(nombre,comuna)) = UPPER(:n)
                       OR UPPER(COALESCE(nombre,comuna)) LIKE UPPER(:n_like)
                    LIMIT 1
                    """
                ),
                {"n": comuna_name, "n_like": comuna_name + "%"},
            ).scalar()

        # tipo cliente (empresa/particular) — formularios tienden a mandar keys distintas o variaciones.
        # Guardamos siempre id_tipo_cliente cuando se pueda inferir.
        id_tipo_cliente = None
        try:
            id_tipo_in = payload.get("id_tipo_cliente") or payload.get("tipo_cliente_id") or payload.get("idTipoCliente")
            if str(id_tipo_in or "").isdigit():
                id_tipo_cliente = int(id_tipo_in)
        except Exception:
            id_tipo_cliente = None

        def _norm_txt(s: str) -> str:
            try:
                import unicodedata

                s2 = unicodedata.normalize("NFD", str(s or "").strip().upper())
                s2 = "".join(ch for ch in s2 if unicodedata.category(ch) != "Mn")
            except Exception:
                s2 = str(s or "").strip().upper()
            # solo letras/números/espacio
            out = []
            for ch in s2:
                if ch.isalnum() or ch.isspace():
                    out.append(ch)
            return "".join(out).strip()

        tipo_name = ""
        if id_tipo_cliente is None:
            for k in (
                "tipo_cliente",
                "tipoCliente",
                "tipo_cliente_form",
                "tipoClienteForm",
                "tipo_cliente_select",
                "tipoClienteSelect",
                "cliente_tipo",
                "clienteTipo",
                "tipo_de_cliente",
                "tipoClienteNombre",
                "tipo_cliente_nombre",
                "tipocliente",
                "tipo",
                "client_type",
                "clientType",
                "empresa_particular",
                "empresaParticular",
            ):
                v = payload.get(k)
                if v is None:
                    continue
                s = str(v).strip()
                if s:
                    tipo_name = s
                    break

        tipo_norm = _norm_txt(tipo_name)
        if id_tipo_cliente is None and tipo_norm:
            is_emp = ("EMP" in tipo_norm) or ("CORP" in tipo_norm)
            is_part = ("PART" in tipo_norm) or ("PERSONA" in tipo_norm) or ("NATURAL" in tipo_norm)

            # 1) match exacto
            id_tipo_cliente = conn.execute(
                text("SELECT id_tipo_cliente FROM public.tipos_cliente WHERE UPPER(tipo)=UPPER(:n) LIMIT 1"),
                {"n": tipo_name},
            ).scalar()

            # 2) match por LIKE (sin tildes/variaciones)
            if not id_tipo_cliente:
                id_tipo_cliente = conn.execute(
                    text(
                        """
                        SELECT id_tipo_cliente
                        FROM public.tipos_cliente
                        WHERE UPPER(tipo) LIKE UPPER(:n_like)
                        ORDER BY id_tipo_cliente ASC
                        LIMIT 1
                        """
                    ),
                    {"n_like": "%" + tipo_norm.replace(" ", "%") + "%"},
                ).scalar()

            # 3) fallback por categoría
            if not id_tipo_cliente and is_emp:
                id_tipo_cliente = conn.execute(
                    text(
                        """
                        SELECT id_tipo_cliente
                        FROM public.tipos_cliente
                        WHERE UPPER(tipo) LIKE '%EMP%'
                        ORDER BY id_tipo_cliente ASC
                        LIMIT 1
                        """
                    )
                ).scalar()
            if not id_tipo_cliente and is_part:
                id_tipo_cliente = conn.execute(
                    text(
                        """
                        SELECT id_tipo_cliente
                        FROM public.tipos_cliente
                        WHERE UPPER(tipo) LIKE '%PART%' OR UPPER(tipo) LIKE '%PERSON%'
                        ORDER BY id_tipo_cliente ASC
                        LIMIT 1
                        """
                    )
                ).scalar()

            # 4) Último fallback: si la tabla no tiene EMPRESA/PARTICULAR, los creamos (idempotente best-effort)
            if not id_tipo_cliente and (is_emp or is_part):
                try:
                    def _ensure_tipo(tipo_txt: str) -> int | None:
                        tid = conn.execute(
                            text("SELECT id_tipo_cliente FROM public.tipos_cliente WHERE UPPER(tipo)=UPPER(:t) LIMIT 1"),
                            {"t": tipo_txt},
                        ).scalar()
                        if tid:
                            return int(tid)
                        try:
                            tid2 = conn.execute(
                                text("INSERT INTO public.tipos_cliente(tipo) VALUES (:t) RETURNING id_tipo_cliente"),
                                {"t": tipo_txt},
                            ).scalar()
                            return int(tid2) if tid2 else None
                        except Exception:
                            return None

                    emp_id = _ensure_tipo("EMPRESA")
                    part_id = _ensure_tipo("PARTICULAR")
                    if is_emp and emp_id:
                        id_tipo_cliente = emp_id
                    elif is_part and part_id:
                        id_tipo_cliente = part_id
                except Exception:
                    pass

        cliente = (payload.get("cliente") or payload.get("nombre_cliente") or payload.get("nombre") or "").strip()
        if not cliente:
            raise HTTPException(400, "cliente requerido")

        # Nota importante: el negocio quiere ver el mensaje del formulario en el lead
        # (Notas/Historial) de forma clara. Evitamos meter JSON enorme en `notas`
        # porque ensucia el UI y crece demasiado rápido.
        def _get_msg(p: dict) -> str:
            for k in ("mensaje", "Mensaje", "comentario", "Comentario", "message", "Message", "notas", "Notas"):
                v = p.get(k)
                if v is None:
                    continue
                s = str(v).strip()
                if s:
                    return s
            return ""

        msg = _get_msg(payload)
        tipo = (payload.get("tipo_evento") or payload.get("tipoEvento") or payload.get("TipoEvento") or "").strip()
        telefono = (payload.get("telefono") or payload.get("Telefono") or payload.get("phone") or "").strip()
        email = (payload.get("email") or payload.get("correo") or payload.get("Correo") or payload.get("mail") or "").strip()
        fecha = (payload.get("fecha_evento") or payload.get("fechaEvento") or payload.get("Fecha") or payload.get("fecha") or payload.get("FechaEvento") or "").strip()
        fecha_evento = _valid_fecha_evento(fecha)
        comuna = (payload.get("comuna") or payload.get("comuna_nombre") or "").strip()
        # Canal: este endpoint es el FORMULARIO, por requerimiento de funnel.
        plataforma = "FORMULARIO"
        plataforma_det = (payload.get("plataforma") or payload.get("canal") or payload.get("channel") or "").strip()
        rid = (payload.get("rid") or "").strip()

        lines = []
        if msg:
            lines.append(msg)
            lines.append("")
        lines.append("[FORMULARIO]")
        lines.append(f"Plataforma: {plataforma}")
        if plataforma_det and _norm_txt(plataforma_det) not in ("FORMULARIO", "WEBFORM"):
            lines.append(f"Fuente: {plataforma_det}")
        if tipo_norm:
            lines.append(f"Tipo cliente: {tipo_name}")
        if tipo:
            lines.append(f"Tipo evento: {tipo}")
        if fecha:
            lines.append(f"Fecha evento: {fecha}")
        if comuna:
            lines.append(f"Comuna: {comuna}")
        if telefono:
            lines.append(f"Teléfono: {telefono}")
        if email:
            lines.append(f"Email: {email}")
        if rid:
            lines.append(f"RID: {rid}")
        notas = "\n".join([x for x in lines if x is not None]).strip() or None

        _ensure_leads_followup_cols()
        new_id = conn.execute(text("""
            INSERT INTO public.leads(
              cliente,email,telefono,direccion,id_marca,id_estado,id_comuna,id_tipo_cliente,
              fecha_evento,monto_cotizado,plataforma,notas,num_cotizacion,created_at,updated_at
            ) VALUES (
              :cliente,:email,:telefono,:direccion,:id_marca,:id_estado,:id_comuna,:id_tipo_cliente,
              :fecha_evento,:monto_cotizado,:plataforma,:notas,:num_cotizacion, now(), now()
            )
            RETURNING id_lead
        """), {
            "cliente": cliente,
            "email": email or payload.get("email"),
            "telefono": _phone_cl_e164(telefono or payload.get("telefono")),
            "direccion": payload.get("direccion"),
            "id_marca": mrow.get("id_marca"),
            "id_estado": int(estado_nuevo),
            "id_comuna": id_comuna,
            "id_tipo_cliente": id_tipo_cliente,
            "fecha_evento": fecha_evento,
            "monto_cotizado": payload.get("monto_cotizado") or 0,
            "plataforma": plataforma,
            "notas": notas,
            "num_cotizacion": payload.get("num_cotizacion"),
        }).scalar_one()
        tracking_ready = bool(conn.execute(text("SELECT to_regclass('public.wi_lead_attribution') IS NOT NULL")).scalar())
        session_id = payload.get("gd_session_id") or (request.headers.get("X-GD-Session-ID") if request else None)
        if tracking_ready and session_id:
            try:
                attach_lead_attribution(conn, lead_id=int(new_id), session_id=session_id)
            except ValueError:
                pass
        conn.commit()
        return {"ok": True, "id_lead": int(new_id)}


@router.get("/leads/{id_lead}/attribution")
def lead_attribution(id_lead: int, user: dict = Depends(get_current_user)):
    get_lead(id_lead, user)
    with get_connection() as conn:
        ready = bool(conn.execute(text("SELECT to_regclass('public.wi_lead_attribution') IS NOT NULL")).scalar())
        if not ready:
            return {"ok": True, "item": None, "editable": False}
        row = conn.execute(text("""
          SELECT lead_id,session_id,campaign_id,first_touch,last_touch,source,medium,campaign,
                 landing_url,device,attribution_method,confidence,is_manual,updated_by,updated_at
          FROM public.wi_lead_attribution WHERE lead_id=:id
        """), {"id": id_lead}).mappings().one_or_none()
    role = _role(user).replace("_", " ")
    editable = any(key in role for key in ("ADMIN", "MARKETING", "MERCADO"))
    return {"ok": True, "item": dict(row) if row else None, "editable": editable}


@router.patch("/leads/{id_lead}/attribution")
def lead_attribution_update(id_lead: int, request: Request, payload: dict = Body(...), user: dict = Depends(get_current_user)):
    get_lead(id_lead, user)
    role = _role(user).replace("_", " ")
    if not any(key in role for key in ("ADMIN", "MARKETING", "MERCADO")):
        raise HTTPException(403, "Sólo roles autorizados pueden editar atribución")
    allowed = {"source", "medium", "campaign", "landing_url", "device", "confidence"}
    values = {key: payload[key] for key in allowed if key in payload}
    if "confidence" in values:
        values["confidence"] = max(0, min(float(values["confidence"]), 1))
    with get_connection() as conn:
        if not values:
            raise HTTPException(422, "Sin cambios permitidos")
        params = {"id": id_lead, "actor": username(user), **values}
        assignments = [f"{key}=:{key}" for key in values]
        row = conn.execute(text("UPDATE public.wi_lead_attribution SET " + ",".join(assignments) + ",is_manual=true,updated_by=:actor,updated_at=now() WHERE lead_id=:id RETURNING *"), params).mappings().one_or_none()
        if not row:
            raise HTTPException(404, "Atribución no encontrada")
        log_activity(conn, username=username(user), user_id=user_id(user), role=role_key(user), action="lead.attribution.update", entity_type="lead", entity_id=id_lead, meta={"fields": sorted(values)}, request=request)
        conn.commit()
    return {"ok": True, "item": dict(row)}


@router.get("/lead-catalogs/marketing-campaigns")
def lead_marketing_campaigns(user: dict = Depends(get_current_user)):
    if not _can_access_leads(_role(user)):
        raise HTTPException(403, "Sin permiso para Leads")
    with get_connection() as conn:
        ready = bool(conn.execute(text("SELECT to_regclass('public.wi_marketing_campaigns') IS NOT NULL")).scalar())
        rows = conn.execute(text("SELECT id,site_id,name,status FROM public.wi_marketing_campaigns WHERE status IN ('ACTIVE','DRAFT') ORDER BY name"), {}).mappings().all() if ready else []
    return {"ok": True, "items": [dict(row) for row in rows]}

@router.put("/leads/{id_lead}")
def update_lead(id_lead: int, request: Request, payload: dict = Body(...), user: dict = Depends(get_current_user)):
    _ensure_leads_followup_cols()
    with get_connection() as conn:
        row = conn.execute(
            text(
                """
                SELECT id_marca, id_estado, notas, cliente, email, telefono, direccion,
                       id_comuna, id_tipo_cliente, fecha_evento, monto_cotizado,
                       plataforma, num_cotizacion
                FROM public.leads
                WHERE id_lead=:id
                """
            ),
            {"id": id_lead},
        ).mappings().first()
        exists = bool(row)
        if not exists:
            raise HTTPException(404, "Lead no existe")
        old_id_marca = row.get("id_marca") if row else None
        old_estado = row.get("id_estado") if row else None
        old_notas = row.get("notas") if row else None

        if (
            _is_lead_operator(user)
            and _lead_change_needs_comment(payload, old_estado=old_estado, old_marca=old_id_marca, old_row=dict(row or {}))
            and not _is_confirmed_estado_conn(conn, payload.get("id_estado"))
            and not _has_new_note_text(old_notas, payload)
        ):
            raise HTTPException(
                400,
                "Comentario obligatorio: para cambiar el estado del lead debes escribir qué hiciste, qué respondió el cliente o cuál es el próximo paso.",
            )

        cliente = payload.get("cliente")
        if cliente is None:
            cliente = payload.get("nombre_cliente") or payload.get("nombre")

        # permitir null/0 sin romper
        id_estado = payload.get("id_estado")
        if id_estado is not None:
            ok_estado = conn.execute(text("SELECT 1 FROM public.estados_lead WHERE id_estado=:i"), {"i": int(id_estado)}).first()
            if not ok_estado:
                raise HTTPException(400, "id_estado inválido")

        # Regla negocio: nunca dejar COTIZADO sin respaldo de cotización.
        # - Si es por sistema: debe existir en tabla `cotizaciones` (o id_cotizacion_vigente si existe).
        # - Si es manual: debe tener MONTO + NÚMERO (PDF es opcional).
        try:
            cotizado_id = conn.execute(
                text("SELECT id_estado FROM public.estados_lead WHERE UPPER(nombre) LIKE '%COTIZ%' ORDER BY id_estado LIMIT 1")
            ).scalar()
            cotizado_id = int(cotizado_id) if cotizado_id is not None else None
        except Exception:
            cotizado_id = None

        def _has_system_quote() -> bool:
            try:
                # tabla cotizaciones
                if _table_exists("cotizaciones"):
                    if conn.execute(text("SELECT 1 FROM public.cotizaciones WHERE id_lead=:id LIMIT 1"), {"id": id_lead}).scalar():
                        return True
            except Exception:
                pass
            try:
                cols = _cols_for("leads")
                if "id_cotizacion_vigente" in cols:
                    v = conn.execute(text("SELECT id_cotizacion_vigente FROM public.leads WHERE id_lead=:id"), {"id": id_lead}).scalar()
                    if v is not None and str(v).strip() and str(v).strip() != "0":
                        return True
            except Exception:
                pass
            return False

        def _has_manual_quote_complete() -> bool:
            try:
                cur = conn.execute(
                    text("SELECT COALESCE(monto_cotizado,0) AS monto, COALESCE(num_cotizacion,'') AS num FROM public.leads WHERE id_lead=:id"),
                    {"id": id_lead},
                ).mappings().first()
                monto = float(cur.get("monto") or 0) if cur else 0.0
                num = str(cur.get("num") or "").strip() if cur else ""
                return (monto > 0) and bool(num)
            except Exception:
                return False

        def _incoming_manual_quote_complete() -> bool:
            try:
                monto_in = payload.get("monto_cotizado")
                num_in = payload.get("num_cotizacion")
                monto = float(monto_in) if monto_in not in (None, "", "null") else None
                num = str(num_in or "").strip() if num_in is not None else None
                if monto is None and num is None:
                    return False
                # Si viene uno solo, igual exigimos ambos (manual completo).
                return (float(monto or 0) > 0) and bool(num)
            except Exception:
                return False

        if cotizado_id and id_estado is not None and int(id_estado) == int(cotizado_id):
            if not (_has_system_quote() or _has_manual_quote_complete() or _incoming_manual_quote_complete()):
                raise HTTPException(400, "No se puede dejar en COTIZADO sin monto y número de cotización (manual) o cotización del sistema.")

        notas_set = "notas" in payload
        notas_val = payload.get("notas")
        if notas_val == "":
            notas_val = None
        # Evita perder mensajes del formulario:
        # algunas integraciones mandan `notas` vacío/None en updates posteriores.
        # Si ya había notas, NO las borramos a menos que el usuario escriba algo.
        if notas_set and (notas_val is None) and (old_notas is not None) and str(old_notas).strip():
            notas_set = False

        new_id_marca = payload.get("id_marca")
        if new_id_marca in ("", "null"):
            new_id_marca = None
        try:
            new_id_marca = int(new_id_marca) if new_id_marca is not None else None
        except Exception:
            pass
        fecha_evento = _valid_fecha_evento(payload.get("fecha_evento")) if "fecha_evento" in payload else None
        telefono_norm = _phone_cl_e164(payload.get("telefono"))

        actual_incoming: dict[str, Any] = {}
        if "cliente" in payload or "nombre_cliente" in payload or "nombre" in payload:
            actual_incoming["cliente"] = cliente
        for key, val in (
            ("email", payload.get("email")),
            ("telefono", telefono_norm),
            ("direccion", payload.get("direccion")),
            ("id_marca", new_id_marca),
            ("id_comuna", payload.get("id_comuna")),
            ("id_tipo_cliente", payload.get("id_tipo_cliente")),
            ("fecha_evento", fecha_evento),
            ("monto_cotizado", payload.get("monto_cotizado")),
            ("plataforma", payload.get("plataforma")),
            ("num_cotizacion", payload.get("num_cotizacion")),
        ):
            if key in payload:
                actual_incoming[key] = val
        data_change_lines, actual_changed_fields = _lead_data_change_lines(conn, dict(row or {}), actual_incoming)

        conn.execute(text("""
          UPDATE public.leads SET
            cliente=COALESCE(:cliente,cliente),
            email=COALESCE(:email,email),
            telefono=COALESCE(:telefono,telefono),
            direccion=COALESCE(:direccion,direccion),
            id_marca=COALESCE(:id_marca,id_marca),
            id_estado=COALESCE(:id_estado,id_estado),
            id_comuna=COALESCE(:id_comuna,id_comuna),
            id_tipo_cliente=COALESCE(:id_tipo_cliente,id_tipo_cliente),
            fecha_evento=COALESCE(:fecha_evento,fecha_evento),
            monto_cotizado=COALESCE(:monto_cotizado,monto_cotizado),
            plataforma=COALESCE(:plataforma,plataforma),
            notas=CASE WHEN :notas_set THEN :notas ELSE notas END,
            num_cotizacion=COALESCE(:num_cotizacion,num_cotizacion),
            updated_at=now()
          WHERE id_lead=:id
        """), {
            "id": id_lead,
            "cliente": cliente,
            "email": payload.get("email"),
            "telefono": telefono_norm,
            "direccion": payload.get("direccion"),
            "id_marca": new_id_marca,
            "id_estado": payload.get("id_estado"),
            "id_comuna": payload.get("id_comuna"),
            "id_tipo_cliente": payload.get("id_tipo_cliente"),
            "fecha_evento": fecha_evento,
            "monto_cotizado": payload.get("monto_cotizado"),
            "plataforma": payload.get("plataforma"),
            "notas_set": bool(notas_set),
            "notas": notas_val,
            "num_cotizacion": payload.get("num_cotizacion"),
        })

        _append_movement_comment(conn, id_lead, payload, user)
        if data_change_lines:
            try:
                actor = _actor_name(user)
                ts = datetime.now().strftime("%Y-%m-%d %H:%M")
                _append_notas(conn, id_lead, "[DATOS %s] %s\n%s" % (ts, actor, "\n".join(data_change_lines[:20])))
            except Exception:
                pass

        # Trazabilidad: si cambió estado desde este endpoint, agregar nota (timestamp + actor).
        try:
            if id_estado is not None and (str(id_estado).strip() != ""):
                new_estado = int(id_estado)
                old_estado_i = int(old_estado) if str(old_estado or "").isdigit() else None
                if old_estado_i and new_estado and old_estado_i != new_estado:
                    old_name = conn.execute(text("SELECT nombre FROM public.estados_lead WHERE id_estado=:i"), {"i": old_estado_i}).scalar() or str(old_estado_i)
                    new_name = conn.execute(text("SELECT nombre FROM public.estados_lead WHERE id_estado=:i"), {"i": new_estado}).scalar() or str(new_estado)
                    actor = (user.get("name") or user.get("username") or user.get("id") or "Usuario")
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
                    _append_notas(conn, id_lead, f"[ESTADO {ts}] {actor}: {old_name} → {new_name}")

                    # Si movieron el lead a CONTACTADO/COTIZADO, registramos seguimiento_at mínimo.
                    try:
                        upn = str(new_name or "").upper()
                        if ("CONTACT" in upn) or ("COTIZ" in upn):
                            if "seguimiento_at" in _cols_for("leads"):
                                conn.execute(text("UPDATE public.leads SET seguimiento_at=now() WHERE id_lead=:id"), {"id": int(id_lead)})
                    except Exception:
                        pass
        except Exception:
            pass

        # Regla negocio (consistencia): si el lead ya tiene cotización,
        # NO debe quedarse en NUEVO/CONTACTADO. Lo subimos a COTIZADO automáticamente
        # (salvo estados terminales como CONFIRMADO/DECLINADO).
        try:
            confirmado_id = conn.execute(
                text("SELECT id_estado FROM public.estados_lead WHERE UPPER(nombre) LIKE '%CONFIRM%' ORDER BY id_estado LIMIT 1")
            ).scalar()
            declinado_id = conn.execute(
                text("SELECT id_estado FROM public.estados_lead WHERE UPPER(nombre) LIKE '%DECLIN%' ORDER BY id_estado LIMIT 1")
            ).scalar()
            confirmado_id = int(confirmado_id) if confirmado_id is not None else None
            declinado_id = int(declinado_id) if declinado_id is not None else None

            cur = conn.execute(
                text(
                    """
                    SELECT id_estado,
                           COALESCE(monto_cotizado,0) AS monto,
                           COALESCE(num_cotizacion,'') AS num,
                           COALESCE(cotizacion_pdf_url,'') AS pdf
                    FROM public.leads
                    WHERE id_lead=:id
                    """
                ),
                {"id": id_lead},
            ).mappings().first()
            if cur and cotizado_id:
                cur_estado = int(cur.get("id_estado") or 0) or None
                is_terminal = (confirmado_id and cur_estado == confirmado_id) or (declinado_id and cur_estado == declinado_id)

                has_system_quote = _has_system_quote()
                has_manual_complete = (float(cur.get("monto") or 0) > 0) and bool(str(cur.get("num") or "").strip())

                # NUEVO/CONTACTADO -> COTIZADO (si hay cotización)
                if (has_system_quote or has_manual_complete) and (not is_terminal) and (cur_estado in (1, 2)) and (cur_estado != cotizado_id):
                    conn.execute(
                        text("UPDATE public.leads SET id_estado=:e, updated_at=now() WHERE id_lead=:id"),
                        {"e": cotizado_id, "id": id_lead},
                    )
        except Exception:
            # no bloqueamos la edición por un tema de normalización de estado
            pass

        # Si cambia la marca del lead, actualiza marca en cotizaciones para mantener consistencia
        try:
            old_mid = int(old_id_marca or 0)
            new_mid = int(new_id_marca or 0)
        except Exception:
            old_mid = old_id_marca or 0
            new_mid = new_id_marca or 0
        if new_id_marca is not None and new_mid != old_mid and _table_exists("cotizaciones") and ("marca" in _cols_for("cotizaciones")):
            mname = conn.execute(
                text("SELECT COALESCE(nombre,marca) FROM public.marcas WHERE id_marca=:id"),
                {"id": new_id_marca},
            ).scalar()
            if mname:
                conn.execute(
                    text("UPDATE public.cotizaciones SET marca=:m WHERE id_lead=:id"),
                    {"m": mname, "id": id_lead},
                )
        try:
            changed_fields = list(actual_changed_fields)
            if id_estado is not None and str(id_estado).strip() and str(old_estado or "") != str(id_estado):
                changed_fields.append("id_estado")
            old_estado_name = _estado_nombre_conn(conn, old_estado)
            new_estado_name = _estado_nombre_conn(conn, payload.get("id_estado"))
            old_marca_name = _marca_nombre_conn(conn, old_id_marca)
            new_marca_name = _marca_nombre_conn(conn, new_id_marca)
            log_activity(
                conn,
                username=username(user),
                user_id=user_id(user),
                role=role_key(user),
                action="lead.update",
                entity_type="lead",
                entity_id=int(id_lead),
                meta={
                    "cliente": cliente,
                    "changed_fields": changed_fields[:40],
                    "changes": data_change_lines[:40],
                    "old_estado_id": old_estado,
                    "old_estado": old_estado_name,
                    "new_estado_id": payload.get("id_estado"),
                    "new_estado": new_estado_name,
                    "old_id_marca": old_id_marca,
                    "old_marca": old_marca_name,
                    "new_id_marca": new_id_marca,
                    "new_marca": new_marca_name,
                },
                request=request,
            )
            if id_estado is not None and str(id_estado).strip() and str(old_estado or "") != str(id_estado):
                log_activity(
                    conn,
                    username=username(user),
                    user_id=user_id(user),
                    role=role_key(user),
                    action="lead.status.change",
                    entity_type="lead",
                    entity_id=int(id_lead),
                    meta={
                        "old_estado_id": old_estado,
                        "old_estado": old_estado_name,
                        "new_estado_id": int(id_estado),
                        "new_estado": new_estado_name,
                        "source": "lead.update",
                    },
                    request=request,
                )
        except Exception:
            pass
        conn.commit()
        return {"ok": True}


@router.post("/leads/{id_lead}/append_note")
def append_note(id_lead: int, request: Request, payload: dict = Body(...), user: dict = Depends(get_current_user)):
    """
    Agrega una entrada al historial/notas del lead (sin pisar el texto existente).
    Útil para registrar seguimientos WhatsApp, llamadas, etc, con timestamp.
    """
    text_in = (payload.get("text") or payload.get("nota") or payload.get("message") or "").strip()
    if not text_in:
        raise HTTPException(400, "text requerido")
    if len(text_in) > 8000:
        raise HTTPException(400, "text demasiado largo")

    kind = str(payload.get("kind") or payload.get("tipo") or "NOTE").strip().upper()[:24]
    title = str(payload.get("title") or payload.get("titulo") or "").strip()[:120]
    followup = bool(payload.get("followup") or payload.get("is_followup") or payload.get("seguimiento") or False)
    contact_outcome = str(payload.get("contact_outcome") or "").strip().upper()[:24]

    try:
        from datetime import datetime

        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    except Exception:
        ts = ""

    who = (user.get("name") or user.get("username") or "").strip()[:80] or "Usuario"

    header = f"[{kind}] {ts} · {who}".strip()
    if title:
        header = f"{header} · {title}".strip()

    block = f"{header}\n{text_in}".strip()

    with get_connection() as conn:
        _ensure_leads_followup_cols()
        row = conn.execute(
            text(
                """
                SELECT l.id_estado, COALESCE(e.nombre,'') AS estado_nombre
                FROM public.leads l
                LEFT JOIN public.estados_lead e ON e.id_estado=l.id_estado
                WHERE l.id_lead=:id
                """
            ),
            {"id": int(id_lead)},
        ).mappings().first()
        if not row:
            raise HTTPException(404, "Lead no existe")

        is_contact = kind in ("WSP", "CALL", "EMAIL")
        is_no_answer = contact_outcome == "NO_ANSWER"
        should_mark_contacted = (is_contact or followup) and not is_no_answer
        moved_to_estado = None
        conn.execute(
            text(
                """
                UPDATE public.leads
                SET notas = CASE
                  WHEN COALESCE(notas,'') = '' THEN :b
                  ELSE notas || E'\\n\\n' || :b
                END,
                updated_at = now()
                WHERE id_lead=:id
                """
            ),
            {"id": int(id_lead), "b": block},
        )
        # Seguimiento real: solo por evidencia (WSP/CALL/EMAIL) o followup explícito.
        try:
            if (is_contact or followup) and ("seguimiento_at" in _cols_for("leads")):
                conn.execute(text("UPDATE public.leads SET seguimiento_at=now() WHERE id_lead=:id"), {"id": int(id_lead)})
        except Exception:
            pass
        # Regla comercial:
        # - WhatsApp, llamada, email o seguimiento explícito sacan al lead de NUEVO.
        # - Un intento marcado explícitamente como NO_ANSWER queda en historial,
        #   pero conserva el estado actual.
        try:
            current_name = str(row.get("estado_nombre") or "").strip().upper()
            if should_mark_contacted and "NUEVO" in current_name:
                target = conn.execute(
                    text(
                        """
                        SELECT id_estado, nombre
                        FROM public.estados_lead
                        WHERE UPPER(nombre) LIKE 'CONTACT%'
                        ORDER BY id_estado
                        LIMIT 1
                        """
                    )
                ).mappings().first()
                if target and int(target["id_estado"]) != int(row.get("id_estado") or 0):
                    conn.execute(
                        text(
                            """
                            UPDATE public.leads
                            SET id_estado=:estado, updated_at=now()
                            WHERE id_lead=:id
                            """
                        ),
                        {"estado": int(target["id_estado"]), "id": int(id_lead)},
                    )
                    moved_to_estado = {
                        "id_estado": int(target["id_estado"]),
                        "nombre": str(target["nombre"]),
                    }
        except Exception:
            moved_to_estado = None
        # Si el usuario registró contacto/seguimiento, cerrar tareas relacionadas (si existe).
        # Importante: NO cerrar por notas genéricas del sistema; solo por evidencia (WSP/CALL/EMAIL)
        # o cuando el frontend indique explícitamente que es seguimiento (followup=true).
        try:
            uid_raw = user.get("id")
            uid = int(uid_raw) if str(uid_raw or "").isdigit() else None
            if uid and (is_contact or followup):
                # Evita fallar si la tabla aún no existe en instalaciones antiguas.
                has_tasks = bool(conn.execute(text("SELECT to_regclass('public.tasks') IS NOT NULL")).scalar())
                if not has_tasks:
                    raise Exception("tasks table missing")
                # Cierra todas las tareas abiertas del lead para este usuario.
                # Esto cumple la regla: si hay seguimiento desde el lead, debe desaparecer de "Tareas".
                # Best-effort: agrega evidencia en meta (evita errores de tipo en Postgres casteando params).
                try:
                    conn.execute(
                        text(
                            """
                            UPDATE public.tasks
                            SET status='done',
                                completed_at=now(),
                                completed_by=:by,
                                meta = COALESCE(meta,'{}'::jsonb) || jsonb_build_object(
                                  'followup_action', to_jsonb(CAST(:k AS text)),
                                  'followup_block', to_jsonb(CAST(:b AS text))
                                ),
                                updated_at=now()
                            WHERE status='open'
                              AND entity_type='lead'
                              AND entity_id=:lid
                              AND assigned_user_id=:uid
                            """
                        ),
                        {"by": who, "k": kind, "b": block[:2000], "lid": int(id_lead), "uid": int(uid)},
                    )
                except Exception:
                    conn.execute(
                        text(
                            """
                            UPDATE public.tasks
                            SET status='done', completed_at=now(), completed_by=:by, updated_at=now()
                            WHERE status='open'
                              AND entity_type='lead'
                              AND entity_id=:lid
                              AND assigned_user_id=:uid
                            """
                        ),
                        {"by": who, "lid": int(id_lead), "uid": int(uid)},
                    )
        except Exception:
            pass
        try:
            log_activity(
                conn,
                username=username(user),
                user_id=user_id(user),
                role=role_key(user),
                action="lead.followup" if (is_contact or followup) else "lead.note",
                entity_type="lead",
                entity_id=int(id_lead),
                meta={
                    "kind": kind,
                    "title": title,
                    "is_contact": bool(is_contact),
                    "followup": bool(followup),
                    "text_preview": text_in[:240],
                },
                request=request,
            )
        except Exception:
            pass
        conn.commit()
        # Importante para UX: devolvemos el bloque generado para que el frontend lo agregue
        # inmediatamente al historial local (sin esperar recargar/buscar de nuevo).
        return {
            "ok": True,
            "id_lead": int(id_lead),
            "block": block,
            "kind": kind,
            "title": title,
            "ts": ts,
            "who": who,
            "estado": moved_to_estado,
        }

@router.patch("/leads/{id_lead}/estado")
def move_estado(id_lead: int, request: Request, payload: dict = Body(...), user: dict = Depends(get_current_user)):
    """
    Payload:
      { "id_estado": 3 }
    Si id_estado == 5 (DECLINADO) exige:
      { "id_estado": 5, "motivo": "..." }
    """
    id_estado = int(payload.get("id_estado") or 0)
    if not id_estado:
        raise HTTPException(400, "id_estado requerido")

    motivo = (payload.get("motivo") or "").strip()
    # Compat: el frontend antiguo a veces no manda undo_preagenda al sacar de CONFIRMADO.
    # Regla negocio: si sale de CONFIRMADO, limpiamos pre-agenda y borramos evento(s) de Calendar (best-effort).
    undo_preagenda = bool(payload.get("undo_preagenda", False))

    # Asegurar columna de seguimiento (si el usuario DB permite DDL).
    # Si falla, seguimos sin romper producción.
    try:
        _ensure_leads_followup_cols()
    except Exception:
        pass

    with get_connection() as conn:
        lead = conn.execute(
            text("SELECT id_lead, id_estado, fecha_evento, COALESCE(notas,'') AS notas FROM public.leads WHERE id_lead=:id"),
            {"id": id_lead},
        ).mappings().first()
        if not lead:
            raise HTTPException(404, "Lead no existe")
        old_estado = lead.get("id_estado")
        if (
            _is_lead_operator(user)
            and old_estado is not None
            and int(old_estado) != int(id_estado)
            and not _is_confirmed_estado_conn(conn, id_estado)
            and not _movement_comment(payload)
            and not motivo
        ):
            raise HTTPException(
                400,
                "Comentario obligatorio: antes de cambiar el estado del lead debes indicar qué gestión se hizo o por qué se mueve.",
            )

        estado_row = conn.execute(
            text("SELECT id_estado, nombre FROM public.estados_lead WHERE id_estado=:i"),
            {"i": id_estado},
        ).mappings().first()
        if not estado_row:
            raise HTTPException(400, "Estado inválido")

        # Auto-undo preagenda al salir de CONFIRMADO (si no viene explicitado).
        if not undo_preagenda:
            try:
                conf_id = conn.execute(
                    text(
                        "SELECT id_estado FROM public.estados_lead WHERE upper(nombre) LIKE 'CONFIRM%' ORDER BY id_estado LIMIT 1"
                    )
                ).scalar()
                conf_id = int(conf_id) if conf_id is not None else None
            except Exception:
                conf_id = None
            try:
                if conf_id and old_estado and int(old_estado) == int(conf_id) and int(id_estado) != int(conf_id):
                    undo_preagenda = True
            except Exception:
                pass

        # No permitir "Cotizado" si no hay monto + N° cotización.
        # Si existe una cotización en tabla, intentamos auto-llenar estos campos en el lead para evitar fricción.
        est_name = (estado_row.get("nombre") or "").strip().upper()
        if id_estado == 3 or "COTIZAD" in est_name:
            lead_row = conn.execute(
                text("SELECT COALESCE(monto_cotizado,0) AS monto, COALESCE(num_cotizacion,'') AS num FROM public.leads WHERE id_lead=:id"),
                {"id": id_lead},
            ).mappings().first()
            monto = float(lead_row.get("monto") or 0) if lead_row else 0
            num = (lead_row.get("num") or "").strip() if lead_row else ""

            # Auto-fill desde cotizaciones (si existe)
            if (monto <= 0 or not num) and _table_exists("cotizaciones"):
                try:
                    q = conn.execute(
                        text(
                            """
                            SELECT numero, COALESCE(subtotal_productos,0) AS subtotal
                            FROM public.cotizaciones
                            WHERE id_lead=:id
                            ORDER BY id_cotizacion DESC
                            LIMIT 1
                            """
                        ),
                        {"id": id_lead},
                    ).mappings().first()
                    upd = {}
                    if q:
                        if (monto <= 0) and float(q.get("subtotal") or 0) > 0:
                            monto = float(q.get("subtotal") or 0)
                            upd["monto_cotizado"] = monto
                        if (not num) and q.get("numero") is not None:
                            num = str(q.get("numero")).strip()
                            if num:
                                upd["num_cotizacion"] = num
                    if upd:
                        conn.execute(
                            text(
                                """
                                UPDATE public.leads
                                SET monto_cotizado=COALESCE(:monto_cotizado, monto_cotizado),
                                    num_cotizacion=COALESCE(NULLIF(:num_cotizacion,''), num_cotizacion),
                                    updated_at=now()
                                WHERE id_lead=:id
                                """
                            ),
                            {
                                "id": id_lead,
                                "monto_cotizado": upd.get("monto_cotizado"),
                                "num_cotizacion": upd.get("num_cotizacion", ""),
                            },
                        )
                except Exception:
                    pass

            if monto <= 0 or not num:
                raise HTTPException(400, "Para pasar a COTIZADO debes tener monto y N° de cotización.")

        # No permitir "Confirmado" si no hay monto y número de cotización
        try:
            confirmado_id = conn.execute(
                text("SELECT id_estado FROM public.estados_lead WHERE UPPER(nombre) LIKE 'CONFIRM%' ORDER BY id_estado LIMIT 1")
            ).scalar()
            confirmado_id = int(confirmado_id) if confirmado_id is not None else None
        except Exception:
            confirmado_id = None

        is_confirming = bool((confirmado_id and int(id_estado) == int(confirmado_id)) or ("CONFIRM" in est_name))
        if is_confirming:
            row = conn.execute(
                text(
                    """
                    SELECT COALESCE(monto_cotizado,0) AS monto,
                           COALESCE(num_cotizacion,'') AS num,
                           COALESCE(calendar_event_id,'') AS calendar_event_id,
                           agenda_approved_at
                    FROM public.leads
                    WHERE id_lead=:id
                    """
                ),
                {"id": id_lead},
            ).mappings().first()
            monto = float(row.get("monto") or 0) if row else 0
            num = (row.get("num") or "").strip() if row else ""
            if monto <= 0 or not num:
                raise HTTPException(400, "No se puede CONFIRMAR sin monto y número de cotización.")
            # Regla negocio: no permitir CONFIRMAR si no está agendado (calendar_event_id + agenda_approved_at),
            # PERO si viene por el flujo de agenda (agendar=true) debemos permitir pasar por acá
            # porque el mismo request se encarga de preparar/crear Calendar.
            try:
                cal_eid = (row.get("calendar_event_id") or "").strip() if row else ""
                appr = row.get("agenda_approved_at") if row else None
                agendar = bool(payload.get("agendar"))
                if (not agendar) and (not cal_eid or appr is None):
                    raise HTTPException(
                        409,
                        "No se puede CONFIRMAR sin agendar en Calendar. Usa el botón de Agendar/Confirmar (Agenda).",
                    )
            except HTTPException:
                raise
            except Exception:
                # Si algo falla leyendo columnas, preferimos bloquear antes que dejar confirmados sin agenda.
                raise HTTPException(
                    409,
                    "No se puede CONFIRMAR sin agendar en Calendar. Usa el botón de Agendar/Confirmar (Agenda).",
                )

        if id_estado == DECLINADO_ID and not motivo:
            raise HTTPException(400, "motivo requerido para DECLINADO")

        if id_estado == DECLINADO_ID:
            # Consecuencia: no permitir declinar leads con fecha_evento futura sin seguimiento.
            # Esto evita “limpiar” leads a Perdido/Declinado sin intentar contactar.
            try:
                role_now = str(user.get("role") or user.get("rol") or "").upper()
                is_admin = role_now in ("ADMIN", "SUPERADMIN")
                fe = lead.get("fecha_evento")
                notas_now = str(lead.get("notas") or "").strip()
                has_note = bool(notas_now)
                has_note_tbl = False
                try:
                    has_note_tbl = bool(
                        conn.execute(
                            text("SELECT 1 FROM public.lead_notas WHERE id_lead=:id LIMIT 1"),
                            {"id": int(id_lead)},
                        ).scalar()
                    )
                except Exception:
                    has_note_tbl = False
                # Si la fecha_evento aún no pasa, exige al menos una nota/seguimiento (o Admin).
                if fe and not is_admin and not (has_note or has_note_tbl):
                    raise HTTPException(
                        400,
                        "Antes de DECLINAR, registra un seguimiento (nota / WhatsApp / llamada).",
                    )
            except HTTPException:
                raise
            except Exception:
                pass

            # agrega motivo a notas si existe columna
            if "notas" in _cols_for("leads"):
                conn.execute(text("""
                  UPDATE public.leads
                  SET id_estado=:e,
                      declinado_motivo=:m,
                      declinado_at=now(),
                      notas=CASE
                        WHEN notas IS NULL OR notas='' THEN :m_full
                        ELSE notas || E'\\n' || :m_full
                      END,
                      updated_at=now()
                  WHERE id_lead=:id
                """), {"e": id_estado, "m": motivo, "m_full": f"[DECLINADO {datetime.now().strftime('%Y-%m-%d %H:%M')}] {motivo}", "id": id_lead})
            else:
                conn.execute(text("""
                  UPDATE public.leads
                  SET id_estado=:e, declinado_motivo=:m, declinado_at=now(), updated_at=now()
                  WHERE id_lead=:id
                """), {"e": id_estado, "m": motivo, "id": id_lead})
        else:
            # al salir de declinado, limpia motivo/timestamp
            conn.execute(text("""
              UPDATE public.leads
              SET id_estado=:e, declinado_motivo=NULL, declinado_at=NULL, updated_at=now()
              WHERE id_lead=:id
            """), {"e": id_estado, "id": id_lead})

        _append_movement_comment(conn, id_lead, payload, user)

        # Si el ejecutivo mueve a CONTACTADO sin registrar nada, dejamos evidencia automática (WSP).
        # Esto evita que "se mueva estado" sin trazabilidad, y además ayuda a la lógica de tareas.
        try:
            if old_estado is not None and int(old_estado) != int(id_estado) and "CONTACT" in est_name:
                notas_now = str(lead.get("notas") or "").strip()
                has_note_tbl = False
                try:
                    has_note_tbl = bool(
                        conn.execute(text("SELECT 1 FROM public.lead_notas WHERE id_lead=:id LIMIT 1"), {"id": int(id_lead)}).scalar()
                    )
                except Exception:
                    has_note_tbl = False
                if not notas_now and not has_note_tbl:
                    who = (user.get("name") or user.get("username") or user.get("id") or "Usuario")
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
                    _append_notas(
                        conn,
                        id_lead,
                        f"[WSP] {ts} · {who}\nContacto registrado automáticamente al mover a CONTACTADO.",
                    )
        except Exception:
            pass

        # Al mover a CONTACTADO o COTIZADO, consideramos que hubo una acción real de seguimiento.
        # Esto evita que tareas vuelvan a aparecer como NUEVO/pendiente solo por recargar.
        try:
            if old_estado is not None and int(old_estado) != int(id_estado) and (("CONTACT" in est_name) or ("COTIZ" in est_name)):
                cols = _cols_for("leads")
                if "seguimiento_at" in cols:
                    conn.execute(
                        text("UPDATE public.leads SET seguimiento_at=now(), updated_at=now() WHERE id_lead=:id"),
                        {"id": int(id_lead)},
                    )
        except Exception:
            pass

        # Nota automática de cambio de estado (siempre).
        try:
            old_estado_i = int(old_estado) if str(old_estado or "").isdigit() else None
            new_estado_i = int(id_estado)
            if old_estado_i and new_estado_i and old_estado_i != new_estado_i:
                old_name = conn.execute(text("SELECT nombre FROM public.estados_lead WHERE id_estado=:i"), {"i": old_estado_i}).scalar() or str(old_estado_i)
                new_name = conn.execute(text("SELECT nombre FROM public.estados_lead WHERE id_estado=:i"), {"i": new_estado_i}).scalar() or str(new_estado_i)
                who = (user.get("name") or user.get("username") or user.get("id") or "Usuario")
                ts = datetime.now().strftime("%Y-%m-%d %H:%M")
                _append_notas(conn, id_lead, f"[ESTADO {ts}] {who}: {old_name} → {new_name}")
        except Exception:
            pass

        try:
            old_estado_i = int(old_estado) if str(old_estado or "").isdigit() else None
            new_estado_i = int(id_estado)
            old_name = conn.execute(text("SELECT nombre FROM public.estados_lead WHERE id_estado=:i"), {"i": old_estado_i}).scalar() if old_estado_i else None
            new_name = conn.execute(text("SELECT nombre FROM public.estados_lead WHERE id_estado=:i"), {"i": new_estado_i}).scalar()
            if old_estado_i != new_estado_i:
                log_activity(
                    conn,
                    username=username(user),
                    user_id=user_id(user),
                    role=role_key(user),
                    action="lead.status.change",
                    entity_type="lead",
                    entity_id=int(id_lead),
                    meta={
                        "old_estado_id": old_estado_i,
                        "old_estado": old_name,
                        "new_estado_id": new_estado_i,
                        "new_estado": new_name,
                        "motivo": motivo or None,
                        "undo_preagenda": bool(undo_preagenda),
                    },
                    request=request,
                )
        except Exception:
            pass
        conn.commit()

        # Notificación inmediata a SUPERADMIN al confirmar venta (para SweetAlert).
        try:
            old_estado_i = int(old_estado) if str(old_estado or "").isdigit() else None
        except Exception:
            old_estado_i = None
        try:
            if is_confirming and confirmado_id and (old_estado_i is None or int(old_estado_i) != int(confirmado_id)):
                try:
                    conn.execute(
                        text(
                            """
                            CREATE TABLE IF NOT EXISTS public.system_notifs (
                              id BIGSERIAL PRIMARY KEY,
                              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                              kind TEXT NOT NULL,
                              role_target TEXT NOT NULL,
                              id_lead BIGINT,
                              title TEXT NOT NULL,
                              body TEXT NOT NULL,
                              payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                              read_at TIMESTAMPTZ,
                              read_by TEXT,
                              UNIQUE(kind, role_target, id_lead)
                            )
                            """
                        )
                    )
                except Exception:
                    pass

                row2 = conn.execute(
                    text("SELECT cliente, fecha_evento, monto_cotizado, id_marca FROM public.leads WHERE id_lead=:id"),
                    {"id": int(id_lead)},
                ).fetchone()
                cliente = str((row2[0] if row2 else "") or "").strip() or f"Lead #{int(id_lead)}"
                fecha_evento = (row2[1] if row2 else None)
                monto = (row2[2] if row2 else None)
                id_marca = (row2[3] if row2 else None)
                marca_nombre = ""
                try:
                    if id_marca is not None:
                        marca_nombre = (
                            conn.execute(
                                text("SELECT COALESCE(nombre, marca, '') FROM public.marcas WHERE id_marca=:id LIMIT 1"),
                                {"id": int(id_marca)},
                            ).scalar()
                            or ""
                        )
                except Exception:
                    marca_nombre = ""

                productos: list[dict] = []
                try:
                    cid = None
                    try:
                        # Preferir cotización vigente del lead (si existe) para no tomar "la última" por error.
                        if "id_cotizacion_vigente" in _cols_for("leads"):
                            cid = conn.execute(
                                text("SELECT id_cotizacion_vigente FROM public.leads WHERE id_lead=:id LIMIT 1"),
                                {"id": int(id_lead)},
                            ).scalar()
                    except Exception:
                        cid = None

                    if not cid:
                        try:
                            cid = conn.execute(
                                text(
                                    "SELECT id_cotizacion FROM public.cotizaciones WHERE id_lead=:id ORDER BY id_cotizacion DESC LIMIT 1"
                                ),
                                {"id": int(id_lead)},
                            ).scalar()
                        except Exception:
                            cid = None

                    if cid:
                        rows_it = conn.execute(
                            text(
                                """
                                SELECT COALESCE(producto,'') AS producto, cantidad
                                FROM public.cotizacion_items
                                WHERE id_cotizacion=:cid
                                ORDER BY COALESCE(total_linea,0) DESC, COALESCE(cantidad,0) DESC
                                LIMIT 6
                                """
                            ),
                            {"cid": int(cid)},
                        ).fetchall()
                        for rr in rows_it:
                            p = str(rr[0] or "").strip()
                            if not p:
                                continue
                            productos.append({"producto": p, "cantidad": rr[1]})
                except Exception:
                    productos = []
                parts = [cliente]
                if marca_nombre:
                    parts.append(f"Marca: {marca_nombre}")
                if fecha_evento:
                    parts.append(f"Fecha: {fecha_evento}")
                if monto is not None:
                    try:
                        parts.append(f"Monto: {float(monto):,.0f}".replace(",", "."))
                    except Exception:
                        parts.append(f"Monto: {monto}")
                if productos:
                    parts.append(f"Productos: {', '.join([p['producto'] for p in productos[:4]])}" + ("..." if len(productos) > 4 else ""))
                body2 = " | ".join(parts)

                import json as _json
                payload_json = {"id_lead": int(id_lead), "cliente": cliente}
                if fecha_evento:
                    payload_json["fecha_evento"] = str(fecha_evento)
                if id_marca is not None:
                    try:
                        payload_json["id_marca"] = int(id_marca)
                    except Exception:
                        pass
                if marca_nombre:
                    payload_json["marca"] = str(marca_nombre)
                if monto is not None:
                    payload_json["monto_cotizado"] = monto
                if productos:
                    payload_json["productos"] = productos

                conn.execute(
                    text(
                        """
                        INSERT INTO public.system_notifs(kind, role_target, id_lead, title, body, payload)
                        VALUES ('EVENT_SOLD', 'SUPERADMIN', :id, 'Evento vendido (confirmado)', :body, CAST(:payload AS jsonb))
                        ON CONFLICT (kind, role_target, id_lead) DO NOTHING
                        """
                    ),
                    {"id": int(id_lead), "body": body2, "payload": _json.dumps(payload_json)},
                )
                conn.commit()
        except Exception:
            pass

    if undo_preagenda:
        _clear_preagenda_fields(id_lead)

    return {"ok": True, "id_lead": id_lead, "id_estado": id_estado}

@router.post("/leads/{id_lead}/estado")
def move_estado_post(id_lead: int, request: Request, payload: dict = Body(...), user: dict = Depends(get_current_user)):
    # compat con frontends legacy (POST), pero SIEMPRE autenticado
    return move_estado(id_lead=id_lead, request=request, payload=payload, user=user)


@router.post("/leads/{id_lead}/estado_ex")
def move_estado_ex(id_lead: int, request: Request, payload: dict = Body(...), user: dict = Depends(get_current_user)):
    # compat con frontend legacy (POST) — mismo comportamiento que PATCH
    return move_estado(id_lead=id_lead, request=request, payload=payload, user=user)


@router.post("/leads/{id_lead}/cotizacion_pdf")
def upload_cotizacion_pdf(id_lead: int, file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    if not file or not file.filename:
        raise HTTPException(400, "Archivo requerido")
    if not (file.content_type or "").lower().endswith("pdf"):
        if not str(file.filename).lower().endswith(".pdf"):
            raise HTTPException(400, "Solo PDF")
    max_pdf_bytes = 20 * 1024 * 1024
    with get_connection() as conn:
        lead = conn.execute(
            text("SELECT id_lead, COALESCE(num_cotizacion,'') AS num FROM public.leads WHERE id_lead=:id"),
            {"id": id_lead},
        ).mappings().first()
        if not lead:
            raise HTTPException(404, "Lead no existe")
    out_dir = _lead_quote_path(id_lead)
    out_path = out_dir / "cotizacion.pdf"
    total_bytes = 0
    try:
        with out_path.open("wb") as fh:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > max_pdf_bytes:
                    try:
                        fh.close()
                    except Exception:
                        pass
                    try:
                        out_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    raise HTTPException(400, "PDF supera 20 MB")
                fh.write(chunk)
    except HTTPException:
        raise
    rel = str(out_path.relative_to(Path(__file__).resolve().parents[2]))
    with get_connection() as conn:
        _ensure_cotizaciones_pdf_col()
        # Si el lead no está confirmado/declinado, al subir un PDF lo marcamos como "Cotizado" (si existe el estado).
        cot_id = None
        try:
            cot_id = conn.execute(
                text("SELECT id_estado FROM public.estados_lead WHERE UPPER(nombre) LIKE '%COTIZ%' ORDER BY id_estado LIMIT 1")
            ).scalar()
            cot_id = int(cot_id) if cot_id is not None else None
        except Exception:
            cot_id = None

        cur_estado = None
        try:
            cur_estado = conn.execute(text("SELECT id_estado FROM public.leads WHERE id_lead=:id"), {"id": id_lead}).scalar()
            cur_estado = int(cur_estado) if cur_estado is not None else None
        except Exception:
            cur_estado = None

        def _estado_nombre(eid: int | None) -> str:
            if not eid:
                return ""
            try:
                return str(conn.execute(text("SELECT nombre FROM public.estados_lead WHERE id_estado=:e"), {"e": int(eid)}).scalar() or "")
            except Exception:
                return ""

        cur_name = _estado_nombre(cur_estado).upper()
        is_terminal = ("CONFIRM" in cur_name) or ("DECLIN" in cur_name) or ("CERR" in cur_name) or ("PERD" in cur_name)

        conn.execute(
            text("UPDATE public.leads SET cotizacion_pdf_url=:u, updated_at=now() WHERE id_lead=:id"),
            {"u": rel, "id": id_lead},
        )
        if cot_id and not is_terminal and (cur_estado != cot_id):
            conn.execute(
                text("UPDATE public.leads SET id_estado=:e, updated_at=now() WHERE id_lead=:id"),
                {"e": int(cot_id), "id": id_lead},
            )

        # Trazabilidad: anotamos en notas que se subió un PDF.
        try:
            cols = _cols_for("leads")
            if "notas" in cols:
                stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
                who = (user.get("username") or user.get("email") or user.get("nombre") or "").strip() or "usuario"
                note = f"[{stamp}] Cotización PDF subida por {who} ({file.filename})"
                conn.execute(
                    text(
                        """
                        UPDATE public.leads
                        SET notas = CASE
                          WHEN notas IS NULL OR notas='' THEN :n
                          ELSE notas || E'\\n' || :n
                        END,
                        updated_at=now()
                        WHERE id_lead=:id
                        """
                    ),
                    {"n": note, "id": id_lead},
                )
        except Exception:
            pass

        # También dejamos el PDF registrado en la última cotización (si existe),
        # o creamos un placeholder mínimo si aún no hay cotización.
        if _table_exists("cotizaciones"):
            last_id = conn.execute(
                text(
                    """
                    SELECT id_cotizacion
                    FROM public.cotizaciones
                    WHERE id_lead=:id
                    ORDER BY created_at DESC, id_cotizacion DESC
                    LIMIT 1
                    """
                ),
                {"id": id_lead},
            ).scalar()
            if last_id:
                conn.execute(
                    text("UPDATE public.cotizaciones SET pdf_path=:u WHERE id_cotizacion=:cid"),
                    {"u": rel, "cid": int(last_id)},
                )
            else:
                num = (lead.get("num") or "").strip()
                created_by = (user.get("username") or user.get("email") or user.get("nombre") or "").strip() or None
                # Intentamos insertar con numero si está disponible (puede ser UNIQUE).
                try:
                    if num:
                        conn.execute(
                            text(
                                """
                                INSERT INTO public.cotizaciones(id_lead, numero, pdf_path, created_by)
                                VALUES (:id, :n, :u, :by)
                                """
                            ),
                            {"id": id_lead, "n": num, "u": rel, "by": created_by},
                        )
                    else:
                        conn.execute(
                            text(
                                """
                                INSERT INTO public.cotizaciones(id_lead, pdf_path, created_by)
                                VALUES (:id, :u, :by)
                                """
                            ),
                            {"id": id_lead, "u": rel, "by": created_by},
                        )
                except Exception:
                    # Si choca por UNIQUE(numero) o similar, no rompemos la carga del PDF al lead.
                    pass

        conn.commit()
    return {"ok": True, "path": rel}


@router.get("/leads/{id_lead}/cotizacion_pdf")
def get_cotizacion_pdf(
    id_lead: int,
    download: int = Query(default=0, ge=0, le=1),
    user: dict = Depends(get_current_user),
):
    with get_connection() as conn:
        row = conn.execute(text("SELECT cotizacion_pdf_url FROM public.leads WHERE id_lead=:id"), {"id": id_lead}).first()
        if not row or not row[0]:
            raise HTTPException(404, "Sin PDF")
        rel = row[0]
    base = Path(__file__).resolve().parents[2]
    file_path = base / rel
    if not file_path.exists():
        raise HTTPException(404, "PDF no encontrado")
    kind = "attachment" if int(download or 0) == 1 else "inline"
    fname = f"cotizacion_{id_lead}.pdf"
    return FileResponse(
        str(file_path),
        media_type="application/pdf",
        headers={"Content-Disposition": f'{kind}; filename="{fname}"'},
    )


@router.post("/leads/{id_lead}/cotizacion_pdf/parse")
def parse_cotizacion_pdf(id_lead: int, user: dict = Depends(get_current_user)):
    """
    Intenta extraer productos/cantidades desde el PDF del lead.
    Nota: funciona solo si el PDF tiene capa de texto (pdftotext).
    Si es escaneado, se requerirá OCR (fase posterior).
    """
    with get_connection() as conn:
        row = conn.execute(
            text("SELECT cotizacion_pdf_url FROM public.leads WHERE id_lead=:id"),
            {"id": id_lead},
        ).first()
        if not row or not row[0]:
            raise HTTPException(404, "Sin PDF")
        rel = str(row[0])
    base = Path(__file__).resolve().parents[2]
    file_path = base / rel
    if not file_path.exists():
        raise HTTPException(404, "PDF no encontrado")

    # Importante: este endpoint lo consume el frontend (y la extensión) que espera JSON.
    # En hosting compartido hemos visto respuestas text/plain por errores internos.
    # Blindamos para devolver JSON con detalle (sin 500) y así el usuario entiende qué pasó.
    try:
        res = _pdf_extract_text(file_path)
    except Exception as e:
        return {"ok": False, "used": "exception", "error": str(e), "items": []}

    if not res.ok:
        return {"ok": False, "error": res.error, "used": res.used, "items": []}

    text = res.text or ""
    try:
        items = _pdf_parse_items(text)
    except Exception as e:
        return {
            "ok": False,
            "used": res.used,
            "error": f"Error parseando texto: {e}",
            "items": [],
            "sample": _pdf_sample_lines(text, n=40),
            "has_text": bool(text.strip()),
        }

    return {
        "ok": True,
        "used": res.used,
        "items": items,
        "sample": _pdf_sample_lines(text, n=40),
        "has_text": bool(text.strip()),
    }

@router.post("/leads/auto_decline")
def leads_auto_decline(payload: dict = Body(default=None), user: dict = Depends(get_current_user)):
    """
    Ejecuta reglas de "leads sin movimiento" y declina automáticamente según criterios del negocio.
    Solo admin/operaciones.

    Body opcional:
      { "dry_run": true }
    """
    role = _role(user)
    r = (role or "").upper()
    if not (_is_superadmin(role) or _is_admin(role) or ("OPERACION" in r) or ("OPERACIONES" in r)):
        raise HTTPException(403, "Solo admin/operaciones")
    dry_run = True
    if isinstance(payload, dict) and "dry_run" in payload:
        dry_run = bool(payload.get("dry_run"))
    triggered_by = (user.get("nombre") or user.get("email") or str(user.get("id") or "")).strip() or "AUTO"
    return auto_decline_stale_leads(dry_run=dry_run, triggered_by=triggered_by)

@router.delete("/admin/purge/leads")
def purge_all_leads(user: dict = Depends(get_current_user)):
    role = _role(user)
    if role not in ("ADMIN", "SUPERADMIN"):
        raise HTTPException(403, "Solo Admin puede purgar")
    with get_connection() as conn:
        try:
            wanted = ["cotizacion_items", "cotizaciones", "lead_notas", "leads"]
            rows = conn.execute(
                text("""
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema='public' AND table_name = ANY(:names)
                """),
                {"names": wanted},
            ).fetchall()
            existing = [r[0] for r in rows if r and r[0]]
            # conserva orden deseado
            ordered = [t for t in wanted if t in existing]
            if not ordered:
                return {"ok": True, "note": "no tables to truncate"}

            sql = "TRUNCATE TABLE " + ", ".join([f"public.{t}" for t in ordered]) + " RESTART IDENTITY CASCADE"
            conn.execute(text(sql))
            conn.commit()
            return {"ok": True, "tables": ordered}
        except Exception as exc:
            conn.rollback()
            return {"ok": False, "error": str(exc)}
