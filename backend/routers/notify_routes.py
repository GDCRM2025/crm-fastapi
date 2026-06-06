from __future__ import annotations

import re

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import text

from backend.core.db import get_connection
from backend.core import notify_routes as _nr  # robust import (package + module)
from backend.core.utils import valid_email
from backend.routers.auth import get_current_user

get_email_route = _nr.get_email_route


router = APIRouter(prefix="/admin/notify_routes", tags=["notify_routes"])


def _is_admin(me: dict) -> bool:
    r = str(me.get("role") or me.get("rol") or "").upper()
    return ("SUPER" in r) or ("ADMIN" in r) or ("FINAN" in r)


def _require_admin(me: dict) -> None:
    if not _is_admin(me):
        raise HTTPException(status_code=403, detail="Solo Admin/SuperAdmin/Finanzas.")


def _norm_email_text(value) -> str | None:
    raw = str(value or "")
    if not raw.strip():
        return None
    out: list[str] = []
    seen: set[str] = set()
    for chunk in re.findall(r"[^\s,;<>]+@[^\s,;<>]+\.[^\s,;<>]+", raw):
        s = chunk.strip().strip("<> ").lower()
        if not s or not valid_email(s) or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return "\n".join(out) or None


def _ensure_email_routes(conn) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.notify_email_routes (
              process_key TEXT PRIMARY KEY,
              enabled BOOLEAN NOT NULL DEFAULT TRUE,
              to_list TEXT,
              cc_list TEXT,
              bcc_list TEXT,
              to_user_ids INT[],
              cc_user_ids INT[],
              bcc_user_ids INT[],
              to_roles TEXT[],
              cc_roles TEXT[],
              bcc_roles TEXT[],
              note TEXT,
              updated_by INT,
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    try:
        conn.commit()
    except Exception:
        pass

    # upgrades
    try:
        conn.execute(text("ALTER TABLE public.notify_email_routes ADD COLUMN IF NOT EXISTS to_user_ids INT[]"))
        conn.execute(text("ALTER TABLE public.notify_email_routes ADD COLUMN IF NOT EXISTS cc_user_ids INT[]"))
        conn.execute(text("ALTER TABLE public.notify_email_routes ADD COLUMN IF NOT EXISTS bcc_user_ids INT[]"))
        conn.execute(text("ALTER TABLE public.notify_email_routes ADD COLUMN IF NOT EXISTS to_roles TEXT[]"))
        conn.execute(text("ALTER TABLE public.notify_email_routes ADD COLUMN IF NOT EXISTS cc_roles TEXT[]"))
        conn.execute(text("ALTER TABLE public.notify_email_routes ADD COLUMN IF NOT EXISTS bcc_roles TEXT[]"))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


# Registro “catálogo” de procesos (para UI).
# Nota: los defaults aquí son informativos; el envío real depende de cada módulo.
EMAIL_PROCESSES: list[dict] = [
    {"key": "RRHH_DESVIOS_REPORT", "label": "RRHH · Reporte diario de desvíos", "default_to": ""},
    {"key": "RRHH_SOLICITUDES", "label": "RRHH · Solicitudes (crear/actualizar)", "default_to": ""},
    {"key": "RRHH_TURNOS", "label": "RRHH · Turnos/Horarios (asignaciones)", "default_to": ""},
    {"key": "RRHH_DEVICE_ENROLL", "label": "RRHH · Enrolamiento SGJO (admin)", "default_to": ""},
    {"key": "RRHH_MARCACIONES", "label": "RRHH · Marcaciones (errores / seguimiento)", "default_to": ""},
    {"key": "RRHH_AUSENCIAS", "label": "RRHH · Ausencias", "default_to": ""},
    {"key": "AGENDA_FAIL", "label": "Agenda · Errores/alertas", "default_to": ""},
    {"key": "AGENDA_EVENTOS", "label": "Eventos · Confirmación/cambios/cancelaciones", "default_to": ""},
    {"key": "EVENTO_MODIFICADO", "label": "Eventos · Evento modificado (alerta)", "default_to": ""},
    {"key": "EVENT_CHECKLIST_FAIL", "label": "Eventos · Checklist con fallas", "default_to": ""},
    {"key": "USERS_CREDENTIALS", "label": "Usuarios · Envío de credenciales", "default_to": ""},
    {"key": "USERS_NEW", "label": "Usuarios · Usuario nuevo", "default_to": ""},
    {"key": "AUTH_RESET", "label": "Auth · Recuperación de acceso", "default_to": ""},
    {"key": "PRODUCTOS_NUEVOS", "label": "Productos · Nuevos productos / cambios", "default_to": ""},
    {"key": "PRODUCTOS_STOCK_BAJO", "label": "Inventario · Stock bajo", "default_to": ""},
    {"key": "VENTA_ALERTAS", "label": "Ventas · Alertas / inconsistencias", "default_to": ""},
    {"key": "FIN_GASTOS_ALERTA", "label": "Finanzas · Gastos (alertas)", "default_to": ""},
    {"key": "FIN_PAGOS_VENCEN", "label": "Finanzas · Vencimientos (por pagar/cobrar)", "default_to": ""},
    {"key": "MARKETING_CAMPAIGN", "label": "Marketing · Campañas", "default_to": ""},
    {"key": "GIA_INBOX_ALERT", "label": "Correo (GIA) · Alertas bandeja", "default_to": ""},
]


@router.get("/email/processes")
def list_email_processes(me=Depends(get_current_user)):
    _require_admin(me)
    with get_connection() as conn:
        _ensure_email_routes(conn)
        rows = conn.execute(
            text(
                """
                SELECT process_key, enabled, to_list, cc_list, bcc_list,
                       to_user_ids, cc_user_ids, bcc_user_ids,
                       to_roles, cc_roles, bcc_roles,
                       note, updated_by, updated_at
                FROM public.notify_email_routes
                ORDER BY process_key
                """
            )
        ).mappings().all()
        by_key = {str(r["process_key"]): dict(r) for r in rows}

    items = []
    for p in EMAIL_PROCESSES:
        k = str(p["key"])
        cfg = by_key.get(k) or {}
        items.append(
            {
                "process_key": k,
                "label": p.get("label") or k,
                "default_to": p.get("default_to") or "",
                "enabled": (cfg.get("enabled") if cfg else None),
                "to_list": cfg.get("to_list") if cfg else None,
                "cc_list": cfg.get("cc_list") if cfg else None,
                "bcc_list": cfg.get("bcc_list") if cfg else None,
                "to_user_ids": cfg.get("to_user_ids") if cfg else None,
                "cc_user_ids": cfg.get("cc_user_ids") if cfg else None,
                "bcc_user_ids": cfg.get("bcc_user_ids") if cfg else None,
                "to_roles": cfg.get("to_roles") if cfg else None,
                "cc_roles": cfg.get("cc_roles") if cfg else None,
                "bcc_roles": cfg.get("bcc_roles") if cfg else None,
                "note": cfg.get("note") if cfg else None,
                "updated_at": cfg.get("updated_at") if cfg else None,
            }
        )
    return {"ok": True, "items": items}


@router.get("/email")
def list_email_routes(me=Depends(get_current_user)):
    _require_admin(me)
    with get_connection() as conn:
        _ensure_email_routes(conn)
        rows = conn.execute(
            text(
                """
                SELECT process_key, enabled, to_list, cc_list, bcc_list,
                       to_user_ids, cc_user_ids, bcc_user_ids,
                       to_roles, cc_roles, bcc_roles,
                       note, updated_by, updated_at
                FROM public.notify_email_routes
                ORDER BY process_key
                """
            )
        ).mappings().all()
    return {"ok": True, "items": [dict(r) for r in rows]}


@router.get("/email/users")
def list_email_users(me=Depends(get_current_user)):
    """
    Devuelve usuarios candidatos para recibir correos:
    - activos
    - con email
    - rol distinto de OPERADOR/CONDUCTOR (y similares)
    """
    _require_admin(me)
    with get_connection() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id_usuario,
                       COALESCE(NULLIF(btrim(nombre),''), NULLIF(btrim(username),''), 'Usuario') AS nombre,
                       COALESCE(NULLIF(btrim(email),''), NULL) AS email,
                       COALESCE(NULLIF(btrim(rol),''), '') AS rol
                FROM public.usuarios
                WHERE COALESCE(is_active, TRUE) IS TRUE
                  AND COALESCE(NULLIF(btrim(email),''), NULL) IS NOT NULL
                  AND upper(COALESCE(rol,'')) NOT LIKE '%OPERADOR%'
                  AND upper(COALESCE(rol,'')) NOT LIKE '%CONDUCTOR%'
                  AND upper(COALESCE(rol,'')) NOT LIKE '%CHOFER%'
                  AND upper(COALESCE(rol,'')) NOT LIKE '%PATIO%'
                ORDER BY upper(COALESCE(rol,'')) ASC, lower(COALESCE(nombre, username, '')) ASC, id_usuario ASC
                LIMIT 2000
                """
            )
        ).mappings().all()
    return {"ok": True, "items": [dict(r) for r in rows]}


@router.get("/email/roles")
def list_email_roles(me=Depends(get_current_user)):
    _require_admin(me)
    with get_connection() as conn:
        rows = conn.execute(
            text(
                """
                SELECT DISTINCT upper(COALESCE(rol,'')) AS rol
                FROM public.usuarios
                WHERE COALESCE(is_active, TRUE) IS TRUE
                  AND COALESCE(NULLIF(btrim(email),''), NULL) IS NOT NULL
                  AND upper(COALESCE(rol,'')) <> ''
                  AND upper(COALESCE(rol,'')) NOT LIKE '%OPERADOR%'
                  AND upper(COALESCE(rol,'')) NOT LIKE '%CONDUCTOR%'
                  AND upper(COALESCE(rol,'')) NOT LIKE '%CHOFER%'
                  AND upper(COALESCE(rol,'')) NOT LIKE '%PATIO%'
                ORDER BY upper(COALESCE(rol,'')) ASC
                """
            )
        ).fetchall()
    items = [r[0] for r in (rows or []) if r and r[0]]
    return {"ok": True, "items": items}


@router.put("/email/{process_key}")
def upsert_email_route(process_key: str, body: dict = Body(default_factory=dict), me=Depends(get_current_user)):
    _require_admin(me)
    pk = str(process_key or "").strip()
    if not pk:
        raise HTTPException(status_code=400, detail="process_key requerido")
    enabled = body.get("enabled")
    if enabled is None:
        enabled = True
    enabled = bool(enabled)
    to_list = _norm_email_text(body.get("to_list"))
    cc_list = _norm_email_text(body.get("cc_list"))
    bcc_list = _norm_email_text(body.get("bcc_list"))
    to_user_ids = body.get("to_user_ids") or []
    cc_user_ids = body.get("cc_user_ids") or []
    bcc_user_ids = body.get("bcc_user_ids") or []
    def _norm_ids(v):
        out=[]
        for x in (v or []):
            try:
                xi=int(x)
                if xi>0: out.append(xi)
            except Exception:
                continue
        # dedup
        seen=set(); res=[]
        for x in out:
            if x not in seen:
                seen.add(x); res.append(x)
        return res or None
    to_user_ids = _norm_ids(to_user_ids)
    cc_user_ids = _norm_ids(cc_user_ids)
    bcc_user_ids = _norm_ids(bcc_user_ids)
    def _norm_roles(v):
        out=[]
        for x in (v or []):
            s=str(x or "").strip()
            if s:
                out.append(s.upper())
        seen=set(); res=[]
        for x in out:
            if x not in seen:
                seen.add(x); res.append(x)
        return res or None
    to_roles = _norm_roles(body.get("to_roles") or [])
    cc_roles = _norm_roles(body.get("cc_roles") or [])
    bcc_roles = _norm_roles(body.get("bcc_roles") or [])
    note = str(body.get("note") or "").strip() or None

    uid = me.get("id") or me.get("id_usuario") or me.get("user_id")
    try:
        uid_i = int(uid) if uid is not None and str(uid).strip().isdigit() else None
    except Exception:
        uid_i = None

    with get_connection() as conn:
        _ensure_email_routes(conn)
        conn.execute(
            text(
                """
                INSERT INTO public.notify_email_routes(
                  process_key, enabled,
                  to_list, cc_list, bcc_list,
                  to_user_ids, cc_user_ids, bcc_user_ids,
                  to_roles, cc_roles, bcc_roles,
                  note, updated_by, updated_at
                )
                VALUES (
                  :k, :en,
                  :to, :cc, :bcc,
                  :tou, :ccu, :bccu,
                  :to_roles, :cc_roles, :bcc_roles,
                  :note, :by, now()
                )
                ON CONFLICT (process_key) DO UPDATE
                SET enabled=EXCLUDED.enabled,
                    to_list=EXCLUDED.to_list,
                    cc_list=EXCLUDED.cc_list,
                    bcc_list=EXCLUDED.bcc_list,
                    to_user_ids=EXCLUDED.to_user_ids,
                    cc_user_ids=EXCLUDED.cc_user_ids,
                    bcc_user_ids=EXCLUDED.bcc_user_ids,
                    to_roles=EXCLUDED.to_roles,
                    cc_roles=EXCLUDED.cc_roles,
                    bcc_roles=EXCLUDED.bcc_roles,
                    note=EXCLUDED.note,
                    updated_by=EXCLUDED.updated_by,
                    updated_at=now()
                """
            ),
            {
                "k": pk,
                "en": enabled,
                "to": to_list,
                "cc": cc_list,
                "bcc": bcc_list,
                "tou": to_user_ids,
                "ccu": cc_user_ids,
                "bccu": bcc_user_ids,
                "to_roles": to_roles,
                "cc_roles": cc_roles,
                "bcc_roles": bcc_roles,
                "note": note,
                "by": uid_i,
            },
        )
        conn.commit()
    return {"ok": True, "item": get_email_route(pk)}
