from __future__ import annotations

import os
import re
import zipfile
from datetime import datetime, timedelta, timezone
from html import escape as _html_escape
from io import BytesIO
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text

from backend.core.db import get_connection
from backend.core.email import send_email, send_email_group
from backend.core.activity_log import ensure_activity_log, fmt_window_title
from backend.routers.auth import get_current_user

router = APIRouter(prefix="/activity", tags=["activity"])
CL_TZ = ZoneInfo("America/Santiago")

TRACKED_ROLE_NAMES = (
    "ADMIN",
    "SUPER ADMIN",
    "SUPERADMIN",
    "BODEGUERO",
    "COMPRAS",
    "EJECUTIVO DE VENTAS",
    "FINANZAS",
    "JEFE DE OPERACIONES",
    "MARKETING",
    "MICE",
    "OPERADOR DE PATIO",
    "RRHH",
)
TRACKED_ROLE_KEYS = tuple("".join(ch for ch in r.upper() if ch.isalnum()) for r in TRACKED_ROLE_NAMES)


TRACKED_ROLE_SQL = """
(
  regexp_replace(upper(COALESCE(rol,'')), '[^A-Z0-9]+', '', 'g') = ANY(CAST(:tracked_roles AS text[]))
  OR regexp_replace(upper(COALESCE(rol,'')), '[^A-Z0-9]+', '', 'g') LIKE '%ADMIN%'
  OR regexp_replace(upper(COALESCE(rol,'')), '[^A-Z0-9]+', '', 'g') LIKE '%EJECUTIV%VENTA%'
)
"""


def _role(user: dict) -> str:
    return str(user.get("role") or user.get("rol") or "").upper()


def _role_compact(value: Any) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def _require_admin(user: dict) -> None:
    role = _role(user).replace("_", " ").strip()
    compact = role.replace(" ", "")
    if compact not in ("ADMIN", "SUPERADMIN") and "ADMIN" not in compact:
        raise HTTPException(403, "Solo Admin")


COMMERCIAL_ROLE_KEYS = ("ADMIN", "SUPERADMIN", "SUPERADMINISTRADOR", "EJECUTIVODEVENTAS")


def _actor_label(row: dict) -> str:
    return (row.get("actor_name") or row.get("username") or row.get("role") or "Sistema").strip()


def _is_commercial_role(value: Any) -> bool:
    key = _role_compact(value)
    return key in COMMERCIAL_ROLE_KEYS or "ADMIN" in key


def _money(v: Any) -> str:
    try:
        return f"${float(v):,.0f}".replace(",", ".")
    except Exception:
        return str(v or "")


def _day_window(report_date: str = "", hours: int = 24) -> tuple[datetime, datetime, str]:
    raw = str(report_date or "").strip()
    if raw:
        try:
            local_start = datetime.strptime(raw[:10], "%Y-%m-%d").replace(tzinfo=CL_TZ)
            local_end = local_start + timedelta(days=1)
            return (
                local_start.astimezone(timezone.utc),
                local_end.astimezone(timezone.utc),
                local_start.strftime("%Y-%m-%d"),
            )
        except Exception:
            raise HTTPException(400, "Fecha inválida. Usa formato YYYY-MM-DD.")
    now = datetime.now(timezone.utc)
    return now - timedelta(hours=int(hours)), now, ""


def _meta(row: Any) -> dict:
    try:
        m = row.get("meta") if hasattr(row, "get") else row[7]
        return m if isinstance(m, dict) else {}
    except Exception:
        return {}


def _fmt_lead_ref(entity_id: Any, meta: dict) -> str:
    lid = meta.get("id_lead") or entity_id
    cliente = meta.get("cliente") or meta.get("nombre_cliente") or ""
    if cliente and lid:
        return f"Lead #{lid} · {cliente}"
    if lid:
        return f"Lead #{lid}"
    return str(cliente or "-")


def _action_label(action: Any) -> str:
    a = str(action or "")
    return {
        "LOGIN": "entró al CRM",
        "LOGOUT": "salió del CRM",
        "attendance.mark": "marcó asistencia",
        "quote.create": "creó cotización",
        "quote.update": "actualizó cotización",
        "lead.update": "actualizó lead",
        "lead.status.change": "cambió estado del lead",
        "lead.followup": "registró seguimiento",
        "lead.note": "agregó nota",
        "LEAD_FOLLOWUP": "registró seguimiento",
        "TASK_DONE": "completó tarea",
        "lead.delete": "mandó lead a eliminados",
        "permissions.user.update": "cambió permisos de usuario",
        "permissions.role.update": "cambió permisos de rol",
    }.get(a, a or "hizo una acción")


def _load_stale_leads(conn, hours: int = 48, limit: int = 80) -> list[dict]:
    """
    Leads activos sin movimiento reciente. Best-effort y tolerante a esquemas legacy.
    """
    try:
        cols = {
            r[0]
            for r in conn.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema='public' AND table_name='leads'
                    """
                )
            ).fetchall()
        }
        updated_expr = "COALESCE(l.updated_at, l.created_at, now())"
        date_filter = ""
        if "fecha_evento" in cols:
            date_filter = "AND (l.fecha_evento IS NULL OR l.fecha_evento >= (now() AT TIME ZONE 'America/Santiago')::date)"
        rows = conn.execute(
            text(
                f"""
                SELECT l.id_lead,
                       COALESCE(l.cliente,'') AS cliente,
                       COALESCE(m.nombre, m.marca, '') AS marca,
                       COALESCE(e.nombre,'') AS estado,
                       {updated_expr} AS last_movement,
                       GREATEST(0, FLOOR(EXTRACT(EPOCH FROM (now() - {updated_expr})) / 86400))::int AS days_without_movement
                FROM public.leads l
                LEFT JOIN public.estados_lead e ON e.id_estado=l.id_estado
                LEFT JOIN public.marcas m ON m.id_marca=l.id_marca
                WHERE COALESCE(l.is_deleted,false)=false
                  AND {updated_expr} < (now() - make_interval(hours => :hours))
                  {date_filter}
                  AND UPPER(COALESCE(e.nombre,'')) NOT LIKE '%CONFIRM%'
                  AND UPPER(COALESCE(e.nombre,'')) NOT LIKE '%DECLIN%'
                  AND UPPER(COALESCE(e.nombre,'')) NOT LIKE '%ELIM%'
                ORDER BY days_without_movement DESC, {updated_expr} ASC, l.id_lead DESC
                LIMIT :lim
                """
            ),
            {"hours": int(hours), "lim": int(limit)},
        ).mappings().all()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _load_user_profiles(conn) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    users = conn.execute(
        text(
            """
            SELECT id_usuario,
                   COALESCE(NULLIF(btrim(nombre),''), NULLIF(btrim(username),''), NULLIF(btrim(email),''), 'Usuario ' || id_usuario::text) AS nombre,
                   COALESCE(NULLIF(btrim(email),''), NULLIF(btrim(username),''), '') AS username,
                   COALESCE(NULLIF(btrim(email),''), '') AS email,
                   COALESCE(NULLIF(btrim(rol),''), '') AS rol
            FROM public.usuarios
            WHERE COALESCE(is_active, TRUE) IS TRUE
              AND {tracked_role_sql}
            ORDER BY upper(COALESCE(rol,'')), lower(COALESCE(nombre, username, email, '')), id_usuario
            LIMIT 700
            """
            .replace("{tracked_role_sql}", TRACKED_ROLE_SQL)
        ),
        {"tracked_roles": list(TRACKED_ROLE_KEYS)},
    ).mappings().all()
    items = [dict(u) for u in users]
    by_key: dict[str, dict[str, Any]] = {}
    for u in items:
        for key in (u.get("id_usuario"), u.get("username"), u.get("email"), u.get("nombre")):
            k = str(key or "").strip().lower()
            if k:
                by_key[k] = u
    return items, by_key


def _display_user(value: Any, user_map: dict[str, dict[str, Any]]) -> str:
    raw = str(value or "").strip()
    u = user_map.get(raw.lower())
    if not u:
        return raw or "Sistema"
    name = str(u.get("nombre") or raw).strip()
    email = str(u.get("email") or u.get("username") or "").strip()
    return f"{name} ({email})" if email and email.lower() != name.lower() else name


def _load_daily_commercial_metrics(conn) -> dict[str, dict[str, Any]]:
    """
    Métricas del día Chile por ejecutivo/admin. Best-effort y liviano.
    Asigna leads por id_usuario cuando existe; si no, por ejecutivo de la marca.
    """
    try:
        cols = {
            str(r[0])
            for r in conn.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema='public' AND table_name='leads'
                    """
                )
            ).fetchall()
        }
        sale_expr = "l.agenda_approved_at" if "agenda_approved_at" in cols else "COALESCE(l.updated_at, l.created_at)"
        rows = conn.execute(
            text(
                f"""
                WITH brand_owner AS (
                  SELECT um.id_marca, MIN(um.id_usuario) AS owner_id
                  FROM public.usuarios_marcas um
                  JOIN public.usuarios u ON u.id_usuario=um.id_usuario
                  WHERE COALESCE(u.is_active, TRUE) IS TRUE
                    AND regexp_replace(upper(COALESCE(u.rol,'')), '[^A-Z0-9]+', '', 'g') = 'EJECUTIVODEVENTAS'
                  GROUP BY um.id_marca
                ),
                base AS (
                  SELECT l.*,
                         CASE
                           WHEN l.id_usuario IS NOT NULL AND btrim(l.id_usuario::text) ~ '^[0-9]+$' THEN (l.id_usuario::text)::int
                           ELSE bo.owner_id
                         END AS owner_id,
                         COALESCE(e.nombre,'') AS estado_nombre,
                         {sale_expr} AS sale_at
                  FROM public.leads l
                  LEFT JOIN public.estados_lead e ON e.id_estado=l.id_estado
                  LEFT JOIN brand_owner bo ON bo.id_marca=l.id_marca
                  WHERE COALESCE(l.is_deleted,false)=false
                )
                SELECT owner_id,
                       COUNT(*) FILTER (WHERE (created_at AT TIME ZONE 'America/Santiago')::date = (now() AT TIME ZONE 'America/Santiago')::date) AS leads_nuevos,
                       COUNT(*) FILTER (
                         WHERE COALESCE(monto_cotizado,0) > 0
                           AND (COALESCE(updated_at, created_at) AT TIME ZONE 'America/Santiago')::date = (now() AT TIME ZONE 'America/Santiago')::date
                       ) AS leads_con_valor,
                       COUNT(*) FILTER (
                         WHERE UPPER(estado_nombre) LIKE '%CONFIRM%'
                           AND (sale_at AT TIME ZONE 'America/Santiago')::date = (now() AT TIME ZONE 'America/Santiago')::date
                       ) AS leads_cerrados,
                       COALESCE(SUM(CASE
                         WHEN UPPER(estado_nombre) LIKE '%CONFIRM%'
                          AND (sale_at AT TIME ZONE 'America/Santiago')::date = (now() AT TIME ZONE 'America/Santiago')::date
                         THEN COALESCE(monto_cotizado,0) ELSE 0 END),0) AS vendido_hoy
                FROM base
                WHERE owner_id IS NOT NULL
                GROUP BY owner_id
                """
            )
        ).mappings().all()
        out: dict[str, dict[str, Any]] = {}
        for r in rows:
            key = str(r.get("owner_id") or "").strip()
            if key:
                out[key] = dict(r)
        return out
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return {}


def _load_daily_sales_today(conn) -> list[dict[str, Any]]:
    try:
        cols = {
            str(r[0])
            for r in conn.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema='public' AND table_name='leads'
                    """
                )
            ).fetchall()
        }
        sale_expr = "l.agenda_approved_at" if "agenda_approved_at" in cols else "COALESCE(l.updated_at, l.created_at)"
        name_expr = "COALESCE(l.cliente, l.nombre_cliente, '')"
        if "cliente" in cols and "nombre_cliente" not in cols:
            name_expr = "COALESCE(l.cliente,'')"
        elif "nombre_cliente" in cols and "cliente" not in cols:
            name_expr = "COALESCE(l.nombre_cliente,'')"
        elif "cliente" not in cols and "nombre_cliente" not in cols:
            name_expr = "''"
        rows = conn.execute(
            text(
                f"""
                SELECT l.id_lead,
                       {name_expr} AS cliente,
                       COALESCE(m.nombre,m.marca,'') AS marca,
                       COALESCE(l.monto_cotizado,0) AS monto,
                       {sale_expr} AS fecha_venta
                FROM public.leads l
                LEFT JOIN public.marcas m ON m.id_marca=l.id_marca
                LEFT JOIN public.estados_lead e ON e.id_estado=l.id_estado
                WHERE COALESCE(l.is_deleted,false)=false
                  AND UPPER(COALESCE(e.nombre,'')) LIKE '%CONFIRM%'
                  AND ({sale_expr} AT TIME ZONE 'America/Santiago')::date = (now() AT TIME ZONE 'America/Santiago')::date
                ORDER BY {sale_expr} DESC, l.id_lead DESC
                LIMIT 80
                """
            )
        ).mappings().all()
        return [dict(r) for r in rows]
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return []


def _parse_recipients() -> List[str]:
    raw = (os.getenv("ADMIN_DIGEST_TO") or "").strip()
    if not raw:
        return []
    out: List[str] = []
    for x in raw.split(","):
        e = x.strip()
        if "@" in e and "." in e:
            out.append(e)
    return out


def _recipients_from_db() -> List[str]:
    """
    Si no hay ADMIN_DIGEST_TO, tomamos admins desde BD.
    Excluye operadores/choferes porque filtramos por rol.
    """
    try:
        with get_connection() as conn:
            # Intento 1: por tabla roles
            rows = conn.execute(
                text(
                    """
                    SELECT DISTINCT u.email
                    FROM public.usuarios u
                    LEFT JOIN public.roles r ON r.id_rol=u.id_rol
                    WHERE COALESCE(u.is_active, TRUE) = TRUE
                      AND u.email IS NOT NULL AND u.email <> ''
                      AND (
                        UPPER(COALESCE(r.nombre,'')) IN ('ADMIN','SUPERADMIN')
                        OR UPPER(COALESCE(u.rol,'')) IN ('ADMIN','SUPERADMIN')
                      )
                    """
                )
            ).fetchall()
        out: List[str] = []
        for r in rows:
            e = (r[0] or "").strip()
            if "@" in e and "." in e:
                out.append(e)
        return sorted(set(out))
    except Exception:
        return []


def _recipient_options_from_db() -> list[dict[str, str]]:
    try:
        with get_connection() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT COALESCE(NULLIF(btrim(nombre),''), NULLIF(btrim(email),''), NULLIF(btrim(username),''), 'Usuario') AS nombre,
                           COALESCE(NULLIF(btrim(email),''), NULLIF(btrim(username),''), '') AS email,
                           COALESCE(NULLIF(btrim(rol),''), '') AS rol
                FROM public.usuarios
                WHERE COALESCE(is_active, TRUE) IS TRUE
                  AND COALESCE(NULLIF(btrim(email),''), NULLIF(btrim(username),''), '') <> ''
                  AND {tracked_role_sql}
                ORDER BY upper(COALESCE(rol,'')), lower(COALESCE(nombre, email, username, ''))
                LIMIT 300
                """
                    .replace("{tracked_role_sql}", TRACKED_ROLE_SQL)
                ),
                {"tracked_roles": list(TRACKED_ROLE_KEYS)},
            ).mappings().all()
        out = []
        seen = set()
        for r in rows:
            email = str(r.get("email") or "").strip()
            if "@" not in email:
                continue
            key = email.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append({"nombre": str(r.get("nombre") or email), "email": email, "rol": str(r.get("rol") or "")})
        return out
    except Exception:
        return []

def _suppress_recipients(addrs: List[str]) -> List[str]:
    """
    Temporal: suprime envíos a ciertos correos (ej: SIMON) hasta que el digest quede validado.
    - Por defecto suprime simonurrutia.m@gmail.com
    - Solo se re-habilita si *dos* flags están activas:
      CRM_ALLOW_SIMON_DIGEST=1 y CRM_DIGEST_VALIDATED=1
    - Se puede agregar lista extra con SUPPRESS_DIGEST_TO="a@b.com,c@d.com"
    """
    if not addrs:
        return []
    allow_simon = (
        str(os.getenv("CRM_ALLOW_SIMON_DIGEST") or "").strip().lower() in ("1", "true", "yes")
        and str(os.getenv("CRM_DIGEST_VALIDATED") or "").strip().lower() in ("1", "true", "yes")
    )
    suppressed = set()
    if not allow_simon:
        suppressed.add("simonurrutia.m@gmail.com")
    extra = (os.getenv("SUPPRESS_DIGEST_TO") or "").strip()
    if extra:
        for x in extra.split(","):
            e = x.strip().lower()
            if "@" in e:
                suppressed.add(e)
    if not suppressed:
        return addrs
    out: List[str] = []
    for a in addrs:
        if (a or "").strip().lower() in suppressed:
            continue
        out.append(a)
    return out


def _build_digest_window(
    dt_from: datetime | None,
    dt_to: datetime | None,
    subject_prefix: str = "CRM · Digest actividad",
    filter_username: str = "",
    period_label: str = "",
) -> tuple[str, str, str]:
    """
    Construye contenido del digest (texto + HTML). No envía correos.
    """
    now = datetime.now(timezone.utc)
    dt_from = dt_from or (now - timedelta(hours=6))
    dt_to = dt_to or now

    with get_connection() as conn:
        ensure_activity_log(conn)
        if filter_username:
            rows = conn.execute(
                text(
                    """
                    SELECT created_at, username, role, action, entity_type, entity_id, meta
                    FROM public.activity_log
                    WHERE created_at >= :a AND created_at <= :b
                      AND (username = :u OR LOWER(username) = LOWER(:u))
                    ORDER BY created_at DESC
                    LIMIT 4000
                    """
                ),
                {"a": dt_from, "b": dt_to, "u": filter_username},
            ).mappings().all()
        else:
            rows = conn.execute(
                text(
                    """
                    SELECT created_at, username, role, action, entity_type, entity_id, meta
                    FROM public.activity_log
                    WHERE created_at >= :a AND created_at <= :b
                    ORDER BY created_at DESC
                    LIMIT 4000
                    """
                ),
                {"a": dt_from, "b": dt_to},
            ).mappings().all()
        stale_leads = _load_stale_leads(conn, hours=int(os.getenv("CRM_STALE_LEAD_HOURS") or "48"), limit=80)
        active_users, user_map = _load_user_profiles(conn)
        daily_metrics = _load_daily_commercial_metrics(conn)
        daily_sales_today = _load_daily_sales_today(conn)

    title = f"{subject_prefix} ({period_label or fmt_window_title(dt_from, dt_to)})"

    # Resumen por usuario (filtrado a roles gestionables).
    allowed_user_keys = set()
    for u in active_users:
        for k in (u.get("username"), u.get("nombre")):
            kk = str(k or "").strip().lower()
            if kk:
                allowed_user_keys.add(kk)
    rows = [
        r for r in rows
        if _role_compact(r.get("role")) in TRACKED_ROLE_KEYS
        or str(r.get("username") or "").strip().lower() in allowed_user_keys
    ]
    per_user = {}
    for r in rows:
        u = (r.get("username") or "?").strip() or "?"
        role = (r.get("role") or "").strip()
        key = u.lower()
        if key not in per_user:
            prof = user_map.get(key) or {}
            per_user[key] = {
                "username": u,
                "display": _display_user(u, user_map),
                "role": prof.get("rol") or role,
                "id_usuario": prof.get("id_usuario"),
                "n": 0,
                "actions": {},
                "quote_created": 0,
                "quote_updated": 0,
                "lead_updated": 0,
                "status_changes": 0,
                "deleted": 0,
                "followups": 0,
                "tasks_done": 0,
                "logins": 0,
                "logouts": 0,
                "attendance": 0,
            }
        it = per_user[key]
        it["n"] = int(it["n"]) + 1
        act = (r.get("action") or "").strip() or "?"
        it["actions"][act] = int(it["actions"].get(act, 0)) + 1
        if act == "quote.create":
            it["quote_created"] = int(it.get("quote_created") or 0) + 1
        if act == "quote.update":
            it["quote_updated"] = int(it.get("quote_updated") or 0) + 1
        if act == "lead.update":
            it["lead_updated"] = int(it.get("lead_updated") or 0) + 1
        if act in ("lead.followup", "lead.note", "LEAD_FOLLOWUP"):
            it["followups"] = int(it.get("followups") or 0) + 1
        if act in ("TASK_DONE", "task.done"):
            it["tasks_done"] = int(it.get("tasks_done") or 0) + 1
        if act == "LOGIN":
            it["logins"] = int(it.get("logins") or 0) + 1
        if act == "LOGOUT":
            it["logouts"] = int(it.get("logouts") or 0) + 1
        if act == "attendance.mark":
            it["attendance"] = int(it.get("attendance") or 0) + 1
        if act == "lead.delete":
            it["deleted"] = int(it.get("deleted") or 0) + 1
        if act == "lead.status.change":
            it["status_changes"] = int(it.get("status_changes") or 0) + 1
        it["quotes"] = int(it.get("quote_created") or 0) + int(it.get("quote_updated") or 0)
        it["leads_touched"] = (
            int(it.get("lead_updated") or 0)
            + int(it.get("status_changes") or 0)
            + int(it.get("followups") or 0)
        )
    users_sorted = sorted(per_user.values(), key=lambda x: (-int(x.get("n", 0)), str(x.get("username", ""))))
    active_keys = {str(k or "").strip().lower() for k in per_user.keys()}
    inactive_users: list[dict[str, Any]] = []
    for u in active_users:
        keys = [
            str(u.get("username") or "").strip().lower(),
            str(u.get("nombre") or "").strip().lower(),
        ]
        if not any(k and k in active_keys for k in keys):
            inactive_users.append(dict(u))

    lines: List[str] = [title, ""]
    # HTML (para que sea legible en celular).
    html_rows: List[str] = []
    if not rows:
        lines.append("Sin actividad en el periodo.")
        html_body = f"<p><b>{_html_escape(title)}</b></p><p>Sin actividad en el periodo.</p>"
    else:
        lines.append("RESUMEN POR USUARIO (COLUMNAS)")
        lines.append("------------------------------")
        lines.append(
            "Usuario | Rol | Total | Cotiz. creadas | Cotiz. editadas | Leads editados | Cambios estado | Eliminados | Seguimientos | Tareas hechas | Logins | Salidas | Asistencia"
        )
        lines.append(
            "--- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---:"
        )
        html_rows.append(
            "<tr>"
            "<th style='text-align:left;padding:6px 8px;border-bottom:1px solid #ddd'>Usuario</th>"
            "<th style='text-align:left;padding:6px 8px;border-bottom:1px solid #ddd'>Rol</th>"
            "<th style='text-align:right;padding:6px 8px;border-bottom:1px solid #ddd'>Total</th>"
            "<th style='text-align:right;padding:6px 8px;border-bottom:1px solid #ddd'>Cotiz. creadas</th>"
            "<th style='text-align:right;padding:6px 8px;border-bottom:1px solid #ddd'>Cotiz. editadas</th>"
            "<th style='text-align:right;padding:6px 8px;border-bottom:1px solid #ddd'>Leads editados</th>"
            "<th style='text-align:right;padding:6px 8px;border-bottom:1px solid #ddd'>Cambios estado</th>"
            "<th style='text-align:right;padding:6px 8px;border-bottom:1px solid #ddd'>Eliminados</th>"
            "<th style='text-align:right;padding:6px 8px;border-bottom:1px solid #ddd'>Seguimientos</th>"
            "<th style='text-align:right;padding:6px 8px;border-bottom:1px solid #ddd'>Tareas</th>"
            "<th style='text-align:right;padding:6px 8px;border-bottom:1px solid #ddd'>Logins</th>"
            "<th style='text-align:right;padding:6px 8px;border-bottom:1px solid #ddd'>Salidas</th>"
            "<th style='text-align:right;padding:6px 8px;border-bottom:1px solid #ddd'>Asistencia</th>"
            "</tr>"
        )
        for u in users_sorted[:60]:
            acts = sorted((u.get("actions") or {}).items(), key=lambda t: (-int(t[1]), str(t[0])))[:4]
            acts_txt = ", ".join([f"{_action_label(a)}: {n}" for a, n in acts]) if acts else "-"
            role_txt = f" ({u.get('role')})" if u.get("role") else ""
            cols = {
                "n": int(u.get("n") or 0),
                "quote_created": int(u.get("quote_created") or 0),
                "quote_updated": int(u.get("quote_updated") or 0),
                "lead_updated": int(u.get("lead_updated") or 0),
                "status_changes": int(u.get("status_changes") or 0),
                "deleted": int(u.get("deleted") or 0),
                "followups": int(u.get("followups") or 0),
                "tasks_done": int(u.get("tasks_done") or 0),
                "logins": int(u.get("logins") or 0),
                "logouts": int(u.get("logouts") or 0),
                "attendance": int(u.get("attendance") or 0),
            }
            lines.append(
                f"{u.get('display') or u.get('username')}{role_txt} | {u.get('role') or '-'} | "
                f"{cols['n']} | {cols['quote_created']} | {cols['quote_updated']} | {cols['lead_updated']} | "
                f"{cols['status_changes']} | {cols['deleted']} | {cols['followups']} | {cols['tasks_done']} | "
                f"{cols['logins']} | {cols['logouts']} | {cols['attendance']}"
            )
            lines.append(f"  Top: {acts_txt}")
            html_rows.append(
                "<tr>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #eee'>{_html_escape(str(u.get('display') or u.get('username') or ''))}</td>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #eee'>{_html_escape(str(u.get('role') or ''))}</td>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #eee;text-align:right'>{cols['n']}</td>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #eee;text-align:right'>{cols['quote_created']}</td>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #eee;text-align:right'>{cols['quote_updated']}</td>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #eee;text-align:right'>{cols['lead_updated']}</td>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #eee;text-align:right'>{cols['status_changes']}</td>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #eee;text-align:right'>{cols['deleted']}</td>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #eee;text-align:right'>{cols['followups']}</td>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #eee;text-align:right'>{cols['tasks_done']}</td>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #eee;text-align:right'>{cols['logins']}</td>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #eee;text-align:right'>{cols['logouts']}</td>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #eee;text-align:right'>{cols['attendance']}</td>"
                "</tr>"
            )

        lines.append("")
        lines.append("VENTAS DEL DIA POR EJECUTIVO")
        lines.append("----------------------------")
        sales_lines: List[str] = []
        sales_ids_rendered: set[int] = set()
        for au in active_users:
            if not _is_commercial_role(au.get("rol")):
                continue
            dm = daily_metrics.get(str(au.get("id_usuario") or ""))
            if not dm:
                continue
            ln = (
                f"- {_display_user(au.get('id_usuario'), user_map)} · "
                f"leads nuevos: {int(dm.get('leads_nuevos') or 0)}, "
                f"leads con valor: {int(dm.get('leads_con_valor') or 0)}, "
                f"cerrados: {int(dm.get('leads_cerrados') or 0)}, "
                f"vendido hoy: {_money(dm.get('vendido_hoy'))}"
            )
            sales_lines.append(ln)
            lines.append(ln)
        for s in daily_sales_today:
            try:
                sales_ids_rendered.add(int(s.get("id_lead") or 0))
            except Exception:
                pass
        if daily_sales_today:
            total_today = sum(float(x.get("monto") or 0) for x in daily_sales_today)
            ln_total = f"- TOTAL vendido hoy: {_money(total_today)} · {len(daily_sales_today)} lead(s) confirmado(s)"
            sales_lines.append(ln_total)
            lines.append(ln_total)
            for s in daily_sales_today[:20]:
                ln = (
                    f"  · Lead #{s.get('id_lead')} · {s.get('cliente') or '-'} · "
                    f"{s.get('marca') or '-'} · {_money(s.get('monto'))}"
                )
                sales_lines.append(ln)
                lines.append(ln)
        if not sales_lines:
            lines.append("- Sin ventas/cierres registrados hoy para roles comerciales.")

        lines.append("")
        lines.append("USUARIOS SIN ACTIVIDAD")
        lines.append("----------------------")
        if inactive_users:
            for u in inactive_users[:120]:
                lines.append(f"- {u.get('nombre') or u.get('username') or u.get('id_usuario')} · {u.get('rol') or '-'} · sin actividad en el periodo")
        else:
            lines.append("- Todos los usuarios activos registran actividad en el periodo.")

        lines.append("")
        lines.append("DETALLE COMERCIAL")
        lines.append("-----------------")
        commercial_lines: List[str] = []
        quote_rows = [r for r in rows if str(r.get("action") or "") in ("quote.create", "quote.update")]
        follow_rows = [r for r in rows if str(r.get("action") or "") in ("lead.followup", "lead.note")]
        status_rows = [r for r in rows if str(r.get("action") or "") == "lead.status.change"]
        delete_rows = [r for r in rows if str(r.get("action") or "") == "lead.delete"]
        for r in quote_rows[:120]:
            m = _meta(r)
            actor = _display_user(r.get("username"), user_map)
            verb = "creó" if str(r.get("action") or "") == "quote.create" else "actualizó"
            ln = (
                f"- {r.get('created_at').strftime('%Y-%m-%d %H:%M')} · {actor} {verb} cotización #{r.get('entity_id')} · "
                f"{_fmt_lead_ref(m.get('id_lead'), m)} · "
                f"{m.get('marca') or ''} · total {_money(m.get('total'))}"
            )
            commercial_lines.append(ln)
            lines.append(ln)
        for r in status_rows[:160]:
            m = _meta(r)
            actor = _display_user(r.get("username"), user_map)
            ln = (
                f"- {r.get('created_at').strftime('%Y-%m-%d %H:%M')} · {actor} cambió estado · "
                f"{_fmt_lead_ref(r.get('entity_id'), m)} · {m.get('old_estado') or m.get('old_estado_id')} -> "
                f"{m.get('new_estado') or m.get('new_estado_id')}"
            )
            commercial_lines.append(ln)
            lines.append(ln)
        for r in follow_rows[:120]:
            m = _meta(r)
            actor = _display_user(r.get("username"), user_map)
            ln = (
                f"- {r.get('created_at').strftime('%Y-%m-%d %H:%M')} · {actor} registró seguimiento · "
                f"{_fmt_lead_ref(r.get('entity_id'), m)} · {m.get('kind') or 'NOTA'} · "
                f"{str(m.get('text_preview') or '')[:120]}"
            )
            commercial_lines.append(ln)
            lines.append(ln)
        for r in delete_rows[:80]:
            m = _meta(r)
            actor = _display_user(r.get("username"), user_map)
            ln = (
                f"- {r.get('created_at').strftime('%Y-%m-%d %H:%M')} · {actor} mandó a eliminados "
                f"{_fmt_lead_ref(r.get('entity_id'), m)} · estado anterior {m.get('old_estado') or ''}"
            )
            commercial_lines.append(ln)
            lines.append(ln)

        lines.append("")
        lines.append("ACTIVIDAD GENERAL (ROLES NO COMERCIALES)")
        lines.append("----------------------------------------")
        general_lines: List[str] = []
        general_rows = [r for r in rows if not _is_commercial_role(r.get("role"))]
        if general_rows:
            for r in general_rows[:250]:
                ts = r.get("created_at").strftime("%Y-%m-%d %H:%M")
                actor = _display_user(r.get("username"), user_map)
                role = str(r.get("role") or "").strip() or "-"
                action = _action_label(r.get("action"))
                et = str(r.get("entity_type") or "").strip()
                eid = r.get("entity_id") or ""
                target = f"{et} #{eid}".strip() if et or eid else "-"
                ln = f"- {ts} · {actor} · {role} · {action} · {target}"
                general_lines.append(ln)
                lines.append(ln)
        else:
            lines.append("- Sin actividad general fuera de roles comerciales.")

        lines.append("")
        lines.append("LEADS SIN MOVIMIENTO")
        lines.append("--------------------")
        if stale_leads:
            for r in stale_leads[:80]:
                last = r.get("last_movement")
                try:
                    last_txt = last.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    last_txt = str(last or "")
                lines.append(
                    f"- Lead #{r.get('id_lead')} · {r.get('cliente') or '-'} · {r.get('estado') or '-'} · "
                    f"marca: {r.get('marca') or '-'} · "
                    f"{int(r.get('days_without_movement') or 0)} día(s) sin movimiento · último movimiento {last_txt}"
                )
        else:
            lines.append("- Sin leads quietos según el umbral actual.")

        lines.append("")
        lines.append("DETALLE (últimos movimientos)")
        lines.append("-----------------------------")
        detail_lines: List[str] = []
        detail_by_user: dict[str, list[str]] = {}
        for r in rows[:800]:
            ts = r.get("created_at").strftime("%Y-%m-%d %H:%M")
            u = _display_user(r.get("username"), user_map)
            role = r.get("role") or ""
            action = _action_label(r.get("action"))
            et = r.get("entity_type") or ""
            eid = r.get("entity_id") or ""
            target = f"{et}:{eid}".strip(":")
            ln = f"  - {ts} · {action} {target}".strip()
            key = f"{u} ({role})".strip()
            detail_by_user.setdefault(key, []).append(ln)
            detail_lines.append(ln)
        for user_label in sorted(detail_by_user.keys()):
            lines.append(f"- {user_label}")
            lines.extend(detail_by_user[user_label][:120])

        sales_html = _html_escape("\n".join(sales_lines[:120]) or "Sin ventas/cierres registrados hoy para roles comerciales.")
        commercial_html = _html_escape("\n".join(commercial_lines[:280]))
        general_html = _html_escape("\n".join(general_lines[:250]) or "Sin actividad general fuera de roles comerciales.")
        stale_html = _html_escape("\n".join([
            (
                f"Lead #{r.get('id_lead')} · {r.get('cliente') or '-'} · {r.get('estado') or '-'} · "
                f"marca: {r.get('marca') or '-'} · "
                f"{int(r.get('days_without_movement') or 0)} día(s) sin movimiento · último movimiento {r.get('last_movement')}"
            )
            for r in stale_leads[:80]
        ]) or "Sin leads quietos según el umbral actual.")
        inactive_html = _html_escape("\n".join([
            f"{u.get('nombre') or u.get('username') or u.get('id_usuario')} · {u.get('rol') or '-'} · sin actividad en el periodo"
            for u in inactive_users[:120]
        ]) or "Todos los usuarios activos registran actividad en el periodo.")
        detail_html = "".join([
            (
                f"<details style='border-bottom:1px solid #e5e7eb;padding:8px 10px'>"
                f"<summary style='cursor:pointer;font-weight:900'>{_html_escape(user_label)} · {len(lines_user)} movimiento(s)</summary>"
                f"<pre style='margin:8px 0 0 0;white-space:pre-wrap;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;line-height:1.35'>"
                f"{_html_escape(chr(10).join(lines_user[:120]))}</pre></details>"
            )
            for user_label, lines_user in sorted(detail_by_user.items())
        ]) or "<div style='padding:10px 12px'>Sin movimientos.</div>"
        html_body = f"""
<div class="auditReport" style="font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Arial;color:#111827;background:#f8fafc">
  <div style="max-width:none;margin:0 auto;padding:12px 10px">
    <div style="font-weight:900;font-size:16px;margin:0 0 8px 0;color:#111827">{_html_escape(title)}</div>
    <div style="color:#475569;font-weight:700;font-size:12px;margin-bottom:10px">
      Resumen por usuario + detalle (últimos movimientos).
    </div>

    <div style="border:1px solid #cbd5e1;border-radius:14px;overflow:auto;background:#ffffff">
      <div style="padding:10px 12px;background:#e2e8f0;border-bottom:1px solid #cbd5e1;font-weight:900;color:#0f172a">
        Resumen por usuario
      </div>
      <div style="padding:0">
        <table style="border-collapse:collapse;width:100%;min-width:1180px;font-size:13px;color:#111827;background:#ffffff">
          <thead style="background:#0f172a;color:#ffffff">
            {html_rows[0] if html_rows else ""}
          </thead>
          <tbody>
            {''.join(html_rows[1:]) if len(html_rows) > 1 else ''}
          </tbody>
        </table>
      </div>
    </div>

    <div style="height:12px"></div>

    <div style="border:1px solid #bbf7d0;border-radius:14px;overflow:hidden;background:#ffffff">
      <div style="padding:10px 12px;background:#f0fdf4;border-bottom:1px solid #bbf7d0;font-weight:900">
        Ventas del día por ejecutivo
      </div>
      <pre style="margin:0;white-space:pre-wrap;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;
background:#fbfffb;padding:10px 12px;line-height:1.35">{sales_html}</pre>
    </div>

    <div style="height:12px"></div>

    <div style="border:1px solid #fed7aa;border-radius:14px;overflow:hidden;background:#ffffff">
      <div style="padding:10px 12px;background:#fff7ed;border-bottom:1px solid #fed7aa;font-weight:900">
        Usuarios sin actividad
      </div>
      <pre style="margin:0;white-space:pre-wrap;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;
background:#fffaf5;padding:10px 12px;line-height:1.35">{inactive_html}</pre>
    </div>

    <div style="height:12px"></div>

    <div style="border:1px solid #e5e7eb;border-radius:14px;overflow:hidden;background:#ffffff">
      <div style="padding:10px 12px;background:#f8fafc;border-bottom:1px solid #e5e7eb;font-weight:900">
        Detalle comercial
      </div>
      <pre style="margin:0;white-space:pre-wrap;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;
background:#fbfbfb;padding:10px 12px;line-height:1.35">{commercial_html or 'Sin movimientos comerciales.'}</pre>
    </div>

    <div style="height:12px"></div>

    <div style="border:1px solid #bfdbfe;border-radius:14px;overflow:hidden;background:#ffffff">
      <div style="padding:10px 12px;background:#eff6ff;border-bottom:1px solid #bfdbfe;font-weight:900">
        Actividad general por rol
      </div>
      <pre style="margin:0;white-space:pre-wrap;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;
background:#f8fbff;padding:10px 12px;line-height:1.35">{general_html}</pre>
    </div>

    <div style="height:12px"></div>

    <div style="border:1px solid #fecaca;border-radius:14px;overflow:hidden;background:#ffffff">
      <div style="padding:10px 12px;background:#fff1f2;border-bottom:1px solid #fecaca;font-weight:900">
        Leads sin movimiento
      </div>
      <pre style="margin:0;white-space:pre-wrap;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;
background:#fffafa;padding:10px 12px;line-height:1.35">{stale_html}</pre>
    </div>

    <div style="height:12px"></div>

    <div style="border:1px solid #e5e7eb;border-radius:14px;overflow:hidden;background:#ffffff">
      <div style="padding:10px 12px;background:#f8fafc;border-bottom:1px solid #e5e7eb;font-weight:900">
        Detalle (hasta 250)
      </div>
      <div style="background:#fbfbfb;font-size:12px;line-height:1.35">{detail_html}</div>
    </div>
  </div>
</div>
""".strip()

    body = "\n".join(lines).strip() + "\n"
    return title, body, html_body


def _send_digest_window(
    dt_from: datetime | None,
    dt_to: datetime | None,
    subject_prefix: str = "CRM · Digest actividad",
    filter_username: str = "",
    recipients: list[str] | None = None,
    period_label: str = "",
) -> Dict[str, Any]:
    """
    Envío best-effort de digest de activity_log, sin depender de auth.
    Usado por:
    - cron-safe endpoint (/activity/digest_cron)
    - logout PM (requisito del cliente)
    """
    title, body, html_body = _build_digest_window(
        dt_from, dt_to, subject_prefix=subject_prefix, filter_username=filter_username, period_label=period_label
    )

    to = _suppress_recipients(recipients or _parse_recipients() or _recipients_from_db())
    if not to:
        return {"ok": False, "sent": 0, "reason": "no_recipients"}

    # Enviamos en UN solo correo (To + Cc) para que quede un hilo común y reducir conexiones SMTP.
    try:
        send_email_group(to, title, body, html=html_body)
        return {"ok": True, "sent": len(to), "failed": 0, "errors": []}
    except Exception as e:
        # Fallback: intentamos individual (best-effort)
        sent = 0
        errors: List[str] = [f"group: {type(e).__name__}: {e}"]
        for addr in to:
            try:
                send_email(addr, title, body, html=html_body)
                sent += 1
            except Exception as ee:
                errors.append(f"{addr}: {type(ee).__name__}: {ee}")
        return {"ok": sent > 0, "sent": sent, "failed": (len(to) - sent), "errors": errors[:6]}


def _download_filename(ext: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    return f"crm-reporte-actividad-{stamp}.{ext}"


def _pdf_escape(value: Any) -> str:
    s = str(value or "")
    s = s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return s


def _make_text_pdf(title: str, body: str) -> bytes:
    lines = [title, ""] + [ln for ln in str(body or "").splitlines()]
    pages: list[list[str]] = []
    chunk: list[str] = []
    for ln in lines:
        chunk.append(ln[:135])
        if len(chunk) >= 54:
            pages.append(chunk)
            chunk = []
    if chunk:
        pages.append(chunk)
    if not pages:
        pages = [["Reporte sin contenido."]]

    objects: list[bytes] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{3 + i * 2} 0 R" for i in range(len(pages)))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode("latin-1"))
    for i, page_lines in enumerate(pages):
        page_obj = 3 + i * 2
        content_obj = page_obj + 1
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> "
            f"/Contents {content_obj} 0 R >>".encode("latin-1")
        )
        text_lines = ["BT /F1 9 Tf 36 756 Td 12 TL"]
        for ln in page_lines:
            text_lines.append(f"({_pdf_escape(ln)}) Tj T*")
        text_lines.append("ET")
        stream = "\n".join(text_lines).encode("latin-1", "replace")
        objects.append(b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream")

    out = BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets: list[int] = [0]
    for idx, obj in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{idx} 0 obj\n".encode("ascii"))
        out.write(obj)
        out.write(b"\nendobj\n")
    xref_at = out.tell()
    out.write(f"xref\n0 {len(objects)+1}\n".encode("ascii"))
    out.write(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.write(f"{off:010d} 00000 n \n".encode("ascii"))
    out.write(
        f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode("ascii")
    )
    return out.getvalue()


def _xlsx_cell(value: Any) -> str:
    return f"<c t=\"inlineStr\"><is><t>{_html_escape(str(value or ''))}</t></is></c>"


def _digest_rows_for_xlsx(body: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for raw in str(body or "").splitlines():
        line = raw.rstrip()
        if not line:
            rows.append([""])
            continue
        if "|" in line and not re.match(r"^\s*-+\s*\|", line):
            rows.append([part.strip() for part in line.split("|")])
        else:
            rows.append([line])
    return rows or [["Sin contenido"]]


def _make_xlsx(body: str) -> bytes:
    rows = _digest_rows_for_xlsx(body)
    sheet_rows = []
    for r_idx, row in enumerate(rows[:5000], start=1):
        cells = "".join(_xlsx_cell(v) for v in row[:32])
        sheet_rows.append(f"<row r=\"{r_idx}\">{cells}</row>")
    sheet = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\">"
        "<sheetData>" + "".join(sheet_rows) + "</sheetData></worksheet>"
    )
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>""")
        z.writestr("_rels/.rels", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""")
        z.writestr("xl/workbook.xml", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Actividad CRM" sheetId="1" r:id="rId1"/></sheets></workbook>""")
        z.writestr("xl/_rels/workbook.xml.rels", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>""")
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return out.getvalue()


@router.get("/digest")
def activity_digest(
    send: int = Query(0, ge=0, le=1),
    hours: int = Query(6, ge=1, le=48),
    date: str = Query(""),
    to: str = Query(""),
    user: dict = Depends(get_current_user),
):
    """
    Digest de actividad (BD) para admins.
    Se usa desde cron: 14:30 y 18:00.
    """
    _require_admin(user)

    dt_from, dt_to, report_day = _day_window(date, int(hours))

    if int(send or 0) == 1:
        recipients = [x.strip() for x in str(to or "").split(",") if "@" in x and "." in x]
        # usa el mismo formateo que cron/logout
        period_label = f"{report_day} · 00:00 -> 23:59 Chile" if report_day else ""
        r = _send_digest_window(
            dt_from,
            dt_to,
            subject_prefix="CRM · Auditoría diaria" if report_day else "CRM · Digest actividad",
            filter_username="",
            recipients=recipients or None,
            period_label=period_label,
        )
        if not r.get("ok"):
            raise HTTPException(400, f"No se pudo enviar digest: {r.get('reason') or r.get('error') or 'unknown'}")
        return {"ok": True, "sent": int(r.get("sent") or 0), "hours": int(hours), "date": report_day}

    period_label = f"{report_day} · 00:00 -> 23:59 Chile" if report_day else ""
    title, body, html = _build_digest_window(
        dt_from,
        dt_to,
        subject_prefix="CRM · Auditoría diaria" if report_day else "CRM · Digest actividad",
        filter_username="",
        period_label=period_label,
    )
    return {"ok": True, "hours": int(hours), "date": report_day, "subject": title, "preview": body, "html": html}


@router.get("/digest_download")
def activity_digest_download(
    hours: int = Query(6, ge=1, le=48),
    date: str = Query(""),
    format: str = Query("pdf"),
    user: dict = Depends(get_current_user),
):
    _require_admin(user)
    dt_from, dt_to, report_day = _day_window(date, int(hours))
    period_label = f"{report_day} · 00:00 -> 23:59 Chile" if report_day else ""
    title, body, html = _build_digest_window(
        dt_from,
        dt_to,
        subject_prefix="CRM · Auditoría diaria" if report_day else "CRM · Digest actividad",
        filter_username="",
        period_label=period_label,
    )
    fmt = str(format or "pdf").strip().lower()
    if fmt == "html":
        full = (
            "<!doctype html><html><head><meta charset='utf-8'><title>"
            + _html_escape(title)
            + "</title></head><body>"
            + html
            + "</body></html>"
        )
        return Response(
            full,
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{_download_filename("html")}"'},
        )
    if fmt == "xlsx":
        return Response(
            _make_xlsx(body),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{_download_filename("xlsx")}"'},
        )
    return Response(
        _make_text_pdf(title, body),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{_download_filename("pdf")}"'},
    )


@router.get("/recipients")
def activity_recipients(user: dict = Depends(get_current_user)):
    _require_admin(user)
    items = _recipient_options_from_db()
    default = set(x.lower() for x in (_parse_recipients() or _recipients_from_db()))
    for it in items:
        it["default"] = "1" if str(it.get("email") or "").lower() in default else ""
    return {"ok": True, "items": items}


@router.get("/logs")
def activity_logs(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    hours: int = Query(72, ge=1, le=24 * 90),
    date: str = Query(""),
    username: str = Query(""),
    action: str = Query(""),
    entity_type: str = Query(""),
    entity_id: int | None = Query(None),
    user: dict = Depends(get_current_user),
):
    """
    Bitacora consultable desde admin.
    Permite filtrar por usuario, accion, entidad e ID sin depender del digest por correo.
    """
    _require_admin(user)
    dt_from, dt_to, report_day = _day_window(date, int(hours))
    where = ["created_at >= :dt_from", "created_at < :dt_to"]
    params: dict[str, Any] = {"dt_from": dt_from, "dt_to": dt_to, "limit": int(limit), "offset": int(offset)}
    if username.strip():
        where.append("(username ILIKE :username OR role ILIKE :username)")
        params["username"] = f"%{username.strip()}%"
    if action.strip():
        where.append("action ILIKE :action")
        params["action"] = f"%{action.strip()}%"
    if entity_type.strip():
        where.append("entity_type ILIKE :entity_type")
        params["entity_type"] = f"%{entity_type.strip()}%"
    if entity_id is not None:
        where.append("entity_id = :entity_id")
        params["entity_id"] = int(entity_id)
    where_sql = " AND ".join(where)
    with get_connection() as conn:
        ensure_activity_log(conn)
        total = conn.execute(text(f"SELECT COUNT(*) FROM public.activity_log WHERE {where_sql}"), params).scalar() or 0
        rows = conn.execute(
            text(
                f"""
                SELECT id, created_at, username, user_id, role, action, entity_type, entity_id,
                       ip, user_agent, method, path, status_code, meta
                FROM public.activity_log
                WHERE {where_sql}
                ORDER BY created_at DESC, id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        ).mappings().all()
        by_action = conn.execute(
            text(
                f"""
                SELECT action, COUNT(*) AS total
                FROM public.activity_log
                WHERE {where_sql}
                GROUP BY action
                ORDER BY total DESC, action ASC
                LIMIT 20
                """
            ),
            params,
        ).mappings().all()
    items = []
    for r in rows:
        d = dict(r)
        d["actor"] = _actor_label(d)
        items.append(d)
    return {
        "ok": True,
        "total": int(total),
        "limit": int(limit),
        "offset": int(offset),
        "hours": int(hours),
        "date": report_day,
        "items": jsonable_encoder(items),
        "summary": {"by_action": jsonable_encoder([dict(r) for r in by_action])},
    }


@router.get("/users_summary")
def activity_users_summary(
    hours: int = Query(72, ge=1, le=24 * 90),
    date: str = Query(""),
    stale_hours: int = Query(48, ge=1, le=24 * 30),
    user: dict = Depends(get_current_user),
):
    """
    Resumen liviano por usuario para gestión:
    - incluye usuarios activos aunque no tengan activity_log en el periodo
    - agrega eventos por usuario sin devolver la bitácora completa
    - calcula leads asignados sin movimiento cuando el esquema lo permite
    """
    _require_admin(user)
    dt_from, dt_to, report_day = _day_window(date, int(hours))
    with get_connection() as conn:
        ensure_activity_log(conn)
        users = conn.execute(
            text(
                """
                SELECT id_usuario,
                       COALESCE(NULLIF(btrim(nombre),''), NULLIF(btrim(username),''), NULLIF(btrim(email),''), 'Usuario ' || id_usuario::text) AS nombre,
                       COALESCE(NULLIF(btrim(email),''), NULLIF(btrim(username),''), '') AS username,
                       COALESCE(NULLIF(btrim(email),''), '') AS email,
                       COALESCE(NULLIF(btrim(rol),''), '') AS rol
                FROM public.usuarios
                WHERE COALESCE(is_active, TRUE) IS TRUE
                  AND {tracked_role_sql}
                ORDER BY upper(COALESCE(rol,'')), lower(COALESCE(nombre, username, email, '')), id_usuario
                LIMIT 700
                """
                .replace("{tracked_role_sql}", TRACKED_ROLE_SQL)
            ),
            {"tracked_roles": list(TRACKED_ROLE_KEYS)},
        ).mappings().all()
        rows = conn.execute(
            text(
                """
                SELECT user_id,
                       lower(COALESCE(username,'')) AS username_key,
                       MAX(created_at) AS last_activity,
                       COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE action='LOGIN') AS logins,
                       COUNT(*) FILTER (WHERE action='LOGOUT') AS logouts,
                       COUNT(*) FILTER (WHERE action='LOGOUT' AND COALESCE(meta->>'reason','')='idle') AS idle_logouts,
                       COUNT(*) FILTER (WHERE action IN ('quote.create','quote.update')) AS quotes,
                       COUNT(*) FILTER (WHERE action IN ('lead.followup','lead.status.change','lead.update','lead.note')) AS lead_moves,
                       COUNT(*) FILTER (WHERE action='lead.status.change') AS status_changes,
                       COUNT(*) FILTER (WHERE action='lead.delete') AS deleted
                FROM public.activity_log
                WHERE created_at >= :dt_from AND created_at < :dt_to
                GROUP BY user_id, lower(COALESCE(username,''))
                """
            ),
            {"dt_from": dt_from, "dt_to": dt_to},
        ).mappings().all()
        by_uid: dict[int, dict[str, Any]] = {}
        by_username: dict[str, dict[str, Any]] = {}
        for r in rows:
            d = dict(r)
            try:
                if d.get("user_id") is not None:
                    by_uid[int(d["user_id"])] = d
            except Exception:
                pass
            key = str(d.get("username_key") or "").strip().lower()
            if key:
                by_username[key] = d

        stale_by_uid: dict[int, int] = {}
        assigned_by_uid: dict[int, int] = {}
        try:
            lead_cols = {
                str(r[0])
                for r in conn.execute(
                    text(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_schema='public' AND table_name='leads'
                        """
                    )
                ).fetchall()
            }
            if "id_usuario" in lead_cols:
                updated_expr = "COALESCE(updated_at, created_at, now())"
                deleted_expr = "COALESCE(is_deleted,false)=false" if "is_deleted" in lead_cols else "true"
                lead_rows = conn.execute(
                    text(
                        f"""
                        SELECT id_usuario::text AS uid_text,
                               COUNT(*) AS assigned,
                               COUNT(*) FILTER (WHERE {updated_expr} < (now() - make_interval(hours => :stale_hours))) AS stale
                        FROM public.leads
                        WHERE {deleted_expr}
                          AND id_usuario IS NOT NULL
                          AND btrim(id_usuario::text) ~ '^[0-9]+$'
                        GROUP BY id_usuario::text
                        """
                    ),
                    {"stale_hours": int(stale_hours)},
                ).mappings().all()
                for r in lead_rows:
                    try:
                        uid = int(r.get("uid_text") or 0)
                        assigned_by_uid[uid] = int(r.get("assigned") or 0)
                        stale_by_uid[uid] = int(r.get("stale") or 0)
                    except Exception:
                        pass
        except Exception:
            stale_by_uid = {}
            assigned_by_uid = {}

    items: list[dict[str, Any]] = []
    for u in users:
        uid = int(u.get("id_usuario") or 0)
        keys = [
            str(u.get("email") or "").strip().lower(),
            str(u.get("username") or "").strip().lower(),
            str(u.get("nombre") or "").strip().lower(),
        ]
        agg = by_uid.get(uid)
        if not agg:
            agg = next((by_username.get(k) for k in keys if k and by_username.get(k)), None)
        agg = agg or {}
        total = int(agg.get("total") or 0)
        quotes = int(agg.get("quotes") or 0)
        lead_moves = int(agg.get("lead_moves") or 0)
        idle_logouts = int(agg.get("idle_logouts") or 0)
        stale_assigned = int(stale_by_uid.get(uid, 0) or 0)
        last_activity = agg.get("last_activity")
        if total <= 0:
            status = "rojo"
            reason = f"Sin actividad el día {report_day}" if report_day else f"Sin actividad en las últimas {int(hours)} horas"
        elif lead_moves <= 0 and quotes <= 0:
            status = "amarillo"
            reason = "Entró al CRM, pero no registra movimiento comercial"
        elif stale_assigned > 0:
            status = "amarillo"
            reason = f"Tiene {stale_assigned} lead(s) asignados sin movimiento"
        else:
            status = "verde"
            reason = "Actividad comercial registrada"
        if idle_logouts > 0 and status == "verde":
            status = "amarillo"
            reason = f"{idle_logouts} cierre(s) por inactividad"
        items.append(
            {
                "id_usuario": uid,
                "nombre": u.get("nombre"),
                "username": u.get("username") or u.get("email"),
                "email": u.get("email"),
                "rol": u.get("rol"),
                "status": status,
                "reason": reason,
                "last_activity": last_activity,
                "total": total,
                "logins": int(agg.get("logins") or 0),
                "logouts": int(agg.get("logouts") or 0),
                "idle_logouts": idle_logouts,
                "quotes": quotes,
                "lead_moves": lead_moves,
                "status_changes": int(agg.get("status_changes") or 0),
                "deleted": int(agg.get("deleted") or 0),
                "assigned_leads": int(assigned_by_uid.get(uid, 0) or 0),
                "stale_assigned_leads": stale_assigned,
            }
        )
    order = {"rojo": 0, "amarillo": 1, "verde": 2}
    items.sort(key=lambda x: (order.get(str(x.get("status")), 9), -int(x.get("stale_assigned_leads") or 0), str(x.get("rol") or ""), str(x.get("nombre") or "")))
    summary = {
        "users": len(items),
        "red": sum(1 for x in items if x.get("status") == "rojo"),
        "yellow": sum(1 for x in items if x.get("status") == "amarillo"),
        "green": sum(1 for x in items if x.get("status") == "verde"),
        "no_commercial": sum(1 for x in items if int(x.get("total") or 0) > 0 and int(x.get("quotes") or 0) <= 0 and int(x.get("lead_moves") or 0) <= 0),
        "idle_logouts": sum(int(x.get("idle_logouts") or 0) for x in items),
        "stale_assigned_leads": sum(int(x.get("stale_assigned_leads") or 0) for x in items),
    }
    return {
        "ok": True,
        "hours": int(hours),
        "date": report_day,
        "stale_hours": int(stale_hours),
        "summary": summary,
        "items": jsonable_encoder(items),
    }


@router.get("/digest_cron", include_in_schema=False)
def activity_digest_cron(
    secret: str = Query(""),
    hours: int = Query(6, ge=1, le=48),
):
    """
    Endpoint para cron (SIN token), protegido por secreto.
    Cron recomendado: 14:30 y 18:00.
    """
    expected = (os.getenv("ADMIN_DIGEST_SECRET") or "").strip()
    if not expected or secret != expected:
        raise HTTPException(403, "Forbidden")
    now = datetime.now(timezone.utc)
    dt_from = now - timedelta(hours=int(hours))
    try:
        return _send_digest_window(dt_from, now, subject_prefix="CRM · Digest actividad", filter_username="")
    except Exception as e:
        # Cron no debería “tumbar” el CRM por un error de correo.
        return {"ok": False, "sent": 0, "error": f"{type(e).__name__}: {e}"}
