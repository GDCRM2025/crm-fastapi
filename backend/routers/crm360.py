from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import text

from backend.core.activity_log import log_activity
from backend.core.db import get_connection
from backend.core.rbac import require_menu_access, username
from backend.routers.auth import get_current_user

router = APIRouter(prefix="/crm360", tags=["crm360"])


def _role(me: dict) -> str:
    return str(me.get("role") or me.get("rol") or "").upper().strip()


def _is_admin(me: dict) -> bool:
    return "ADMIN" in _role(me) or "SUPER" in _role(me)


def _uid(me: dict) -> int | None:
    raw = me.get("id_usuario") or me.get("id") or me.get("user_id")
    try:
        return int(raw) if str(raw or "").isdigit() else None
    except Exception:
        return None


def _cols(conn, table: str) -> set[str]:
    try:
        rows = conn.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name=:t
                """
            ),
            {"t": table},
        ).fetchall()
        return {str(r[0]) for r in rows if r and r[0]}
    except Exception:
        return set()


def _table_exists(conn, table: str) -> bool:
    try:
        return bool(conn.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"public.{table}"}).scalar())
    except Exception:
        return False


def _ensure_tables(conn) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.crm_followup_rules (
              id_rule SERIAL PRIMARY KEY,
              estado_like TEXT NOT NULL,
              max_hours INTEGER NOT NULL DEFAULT 72,
              label TEXT,
              is_active BOOLEAN NOT NULL DEFAULT TRUE,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.crm_event_closures (
              id_closure SERIAL PRIMARY KEY,
              id_lead INTEGER NOT NULL UNIQUE,
              closed_by INTEGER,
              status TEXT NOT NULL DEFAULT 'PENDIENTE',
              costo_real NUMERIC(14,2) NOT NULL DEFAULT 0,
              margen_real NUMERIC(14,2) NOT NULL DEFAULT 0,
              incidencias TEXT,
              cierre_operativo TEXT,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    try:
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    try:
        conn.execute(
            text(
                """
                DELETE FROM public.crm_followup_rules a
                USING public.crm_followup_rules b
                WHERE a.id_rule > b.id_rule
                  AND upper(btrim(a.estado_like)) = upper(btrim(b.estado_like))
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_crm_followup_rules_estado_like
                ON public.crm_followup_rules ((upper(btrim(estado_like))))
                """
            )
        )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    try:
        conn.execute(
            text(
                """
                INSERT INTO public.crm_followup_rules(estado_like,max_hours,label)
                VALUES
                  ('NUEVO',24,'Lead nuevo sin atención'),
                  ('CONTACT',48,'Contactado sin siguiente movimiento'),
                  ('COTIZ',72,'Cotizado sin respuesta'),
                  ('NEGOCI',72,'Negociación sin avance')
                ON CONFLICT DO NOTHING
                """
            )
        )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    if _table_exists(conn, "cotizaciones"):
        for ddl in (
            "ALTER TABLE public.cotizaciones ADD COLUMN IF NOT EXISTS approval_status TEXT NOT NULL DEFAULT 'NO_REQUIERE'",
            "ALTER TABLE public.cotizaciones ADD COLUMN IF NOT EXISTS approval_requested_at TIMESTAMPTZ",
            "ALTER TABLE public.cotizaciones ADD COLUMN IF NOT EXISTS approval_requested_by TEXT",
            "ALTER TABLE public.cotizaciones ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ",
            "ALTER TABLE public.cotizaciones ADD COLUMN IF NOT EXISTS approved_by TEXT",
            "ALTER TABLE public.cotizaciones ADD COLUMN IF NOT EXISTS sent_at TIMESTAMPTZ",
            "ALTER TABLE public.cotizaciones ADD COLUMN IF NOT EXISTS sent_to TEXT",
            "ALTER TABLE public.cotizaciones ADD COLUMN IF NOT EXISTS viewed_at TIMESTAMPTZ",
            "ALTER TABLE public.cotizaciones ADD COLUMN IF NOT EXISTS accepted_at TIMESTAMPTZ",
        ):
            try:
                conn.execute(text(ddl))
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
    try:
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


def _require_crm(conn, me: dict, required: str = "read") -> None:
    try:
        require_menu_access(conn, me, "crm360", required)
    except HTTPException:
        if _is_admin(me) or "EJECUTIVO" in _role(me):
            return
        raise


def _lead_scope_sql(me: dict, params: dict[str, Any]) -> str:
    if _is_admin(me):
        return ""
    marcas = [int(x) for x in (me.get("marcas") or []) if str(x).isdigit()]
    if marcas:
        params["scope_marcas"] = marcas
        return " AND l.id_marca = ANY(:scope_marcas)"
    uid = _uid(me)
    if uid:
        params["scope_uid"] = str(uid)
        return " AND COALESCE(l.id_usuario::text,'') = :scope_uid"
    return " AND 1=0"


def _lead_name_expr(cols: set[str]) -> str:
    if "cliente" in cols and "nombre_cliente" in cols:
        return "COALESCE(NULLIF(btrim(l.cliente),''), NULLIF(btrim(l.nombre_cliente),''), '')"
    if "cliente" in cols:
        return "COALESCE(NULLIF(btrim(l.cliente),''), '')"
    if "nombre_cliente" in cols:
        return "COALESCE(NULLIF(btrim(l.nombre_cliente),''), '')"
    return "''"


def _sale_date_expr(cols: set[str]) -> str:
    if "agenda_approved_at" in cols:
        return "l.agenda_approved_at"
    return "COALESCE(l.updated_at, l.created_at)"


@router.get("/clientes")
def search_clientes(
    q: str = Query("", max_length=120),
    limit: int = Query(30, ge=1, le=80),
    me: dict = Depends(get_current_user),
):
    with get_connection() as conn:
        _ensure_tables(conn)
        _require_crm(conn, me, "read")
        lcols = _cols(conn, "leads")
        name_expr = _lead_name_expr(lcols)
        params: dict[str, Any] = {"lim": int(limit), "q": f"%{q.strip()}%"}
        scope = _lead_scope_sql(me, params)
        rows = conn.execute(
            text(
                f"""
                SELECT l.id_lead,
                       {name_expr} AS cliente,
                       COALESCE(l.email,'') AS email,
                       COALESCE(l.telefono,'') AS telefono,
                       COALESCE(m.nombre,m.marca,'') AS marca,
                       COALESCE(e.nombre,'') AS estado,
                       l.fecha_evento,
                       COALESCE(l.monto_cotizado,0) AS monto_cotizado,
                       COALESCE(l.updated_at,l.created_at) AS last_activity
                FROM public.leads l
                LEFT JOIN public.marcas m ON m.id_marca=l.id_marca
                LEFT JOIN public.estados_lead e ON e.id_estado=l.id_estado
                WHERE COALESCE(l.is_deleted,false)=false
                  AND UPPER(COALESCE(e.nombre,'')) NOT LIKE '%DECLIN%'
                  {scope}
                  AND (
                    :q = '%%'
                    OR {name_expr} ILIKE :q
                    OR COALESCE(l.email,'') ILIKE :q
                    OR COALESCE(l.telefono,'') ILIKE :q
                    OR l.id_lead::text = btrim(:q_raw)
                  )
                ORDER BY COALESCE(l.updated_at,l.created_at) DESC
                LIMIT :lim
                """
            ),
            {**params, "q_raw": q.strip()},
        ).mappings().all()
        return {"ok": True, "items": [dict(r) for r in rows]}


@router.get("/clientes/{id_lead}/360")
def cliente_360(id_lead: int, me: dict = Depends(get_current_user)):
    with get_connection() as conn:
        _ensure_tables(conn)
        _require_crm(conn, me, "read")
        lcols = _cols(conn, "leads")
        name_expr = _lead_name_expr(lcols)
        params: dict[str, Any] = {"id": int(id_lead)}
        scope = _lead_scope_sql(me, params)
        lead = conn.execute(
            text(
                f"""
                SELECT l.*,
                       {name_expr} AS cliente_360,
                       COALESCE(m.nombre,m.marca,'') AS marca_nombre,
                       COALESCE(c.nombre,'') AS comuna_nombre,
                       COALESCE(e.nombre,'') AS estado_nombre
                FROM public.leads l
                LEFT JOIN public.marcas m ON m.id_marca=l.id_marca
                LEFT JOIN public.comunas c ON c.id_comuna=l.id_comuna
                LEFT JOIN public.estados_lead e ON e.id_estado=l.id_estado
                WHERE l.id_lead=:id {scope}
                LIMIT 1
                """
            ),
            params,
        ).mappings().first()
        if not lead:
            raise HTTPException(404, "Lead no existe o no tienes acceso")
        lead_d = dict(lead)
        email = str(lead_d.get("email") or "").strip()
        phone = str(lead_d.get("telefono") or "").strip()

        dup_where = []
        dup_params = {"id": int(id_lead)}
        if email:
            dup_where.append("lower(COALESCE(email,'')) = lower(:email)")
            dup_params["email"] = email
        if phone:
            dup_where.append("regexp_replace(COALESCE(telefono,''),'[^0-9]+','','g') = regexp_replace(:phone,'[^0-9]+','','g')")
            dup_params["phone"] = phone
        duplicates = []
        if dup_where:
            duplicates = [
                dict(r)
                for r in conn.execute(
                    text(
                        f"""
                        SELECT id_lead, {name_expr} AS cliente, COALESCE(email,'') AS email, COALESCE(telefono,'') AS telefono,
                               fecha_evento, COALESCE(monto_cotizado,0) AS monto_cotizado
                        FROM public.leads l
                        WHERE l.id_lead <> :id AND COALESCE(l.is_deleted,false)=false
                          AND ({' OR '.join(dup_where)})
                        ORDER BY COALESCE(updated_at,created_at) DESC
                        LIMIT 30
                        """
                    ),
                    dup_params,
                ).mappings().all()
            ]

        quotes = []
        if _table_exists(conn, "cotizaciones"):
            quotes = [
                dict(r)
                for r in conn.execute(
                    text(
                        """
                        SELECT id_cotizacion, numero, created_at, fecha, COALESCE(total,0) AS total,
                               COALESCE(estado,'') AS estado,
                               COALESCE(approval_status,'') AS approval_status,
                               sent_at, viewed_at, accepted_at
                        FROM public.cotizaciones
                        WHERE id_lead=:id
                        ORDER BY id_cotizacion DESC
                        LIMIT 80
                        """
                    ),
                    {"id": int(id_lead)},
                ).mappings().all()
            ]

        finance = []
        if _table_exists(conn, "fin_eventos"):
            finance = [
                dict(r)
                for r in conn.execute(
                    text(
                        """
                        SELECT id_evento, fecha_evento, COALESCE(monto_bruto,0) AS monto_bruto,
                               COALESCE(abono,0) AS abono, COALESCE(saldo,0) AS saldo,
                               COALESCE(factura_tipo_doc,'') AS factura_tipo_doc,
                               COALESCE(factura_num,'') AS factura_num,
                               COALESCE(factura_oc,'') AS factura_oc,
                               COALESCE(factura_hes,'') AS factura_hes
                        FROM public.fin_eventos
                        WHERE id_lead=:id
                        ORDER BY id_evento DESC
                        LIMIT 20
                        """
                    ),
                    {"id": int(id_lead)},
                ).mappings().all()
            ]

        activity = [
            dict(r)
            for r in conn.execute(
                text(
                    """
                    SELECT created_at, username, role, action, entity_type, entity_id, meta
                    FROM public.activity_log
                    WHERE (entity_type='lead' AND entity_id=:id)
                       OR (meta->>'id_lead') = :id_text
                    ORDER BY created_at DESC
                    LIMIT 120
                    """
                ),
                {"id": int(id_lead), "id_text": str(id_lead)},
            ).mappings().all()
        ]

        closure = None
        if _table_exists(conn, "crm_event_closures"):
            closure = conn.execute(
                text("SELECT * FROM public.crm_event_closures WHERE id_lead=:id LIMIT 1"),
                {"id": int(id_lead)},
            ).mappings().first()

        return {
            "ok": True,
            "lead": lead_d,
            "duplicates": duplicates,
            "quotes": quotes,
            "finance": finance,
            "activity": activity,
            "operation_closure": dict(closure) if closure else None,
        }


@router.get("/ventas/hoy")
def ventas_hoy(me: dict = Depends(get_current_user)):
    with get_connection() as conn:
        _ensure_tables(conn)
        _require_crm(conn, me, "read")
        lcols = _cols(conn, "leads")
        sale_expr = _sale_date_expr(lcols)
        params: dict[str, Any] = {}
        scope = _lead_scope_sql(me, params)
        rows = conn.execute(
            text(
                f"""
                SELECT l.id_lead,
                       {_lead_name_expr(lcols)} AS cliente,
                       COALESCE(m.nombre,m.marca,'') AS marca,
                       COALESCE(l.monto_cotizado,0) AS monto,
                       {sale_expr} AS fecha_venta,
                       l.fecha_evento
                FROM public.leads l
                LEFT JOIN public.marcas m ON m.id_marca=l.id_marca
                LEFT JOIN public.estados_lead e ON e.id_estado=l.id_estado
                WHERE COALESCE(l.is_deleted,false)=false
                  AND UPPER(COALESCE(e.nombre,'')) LIKE '%CONFIRM%'
                  AND ({sale_expr} AT TIME ZONE 'America/Santiago')::date = (now() AT TIME ZONE 'America/Santiago')::date
                  {scope}
                ORDER BY {sale_expr} DESC, l.id_lead DESC
                """
            ),
            params,
        ).mappings().all()
        items = [dict(r) for r in rows]
        return {"ok": True, "total": sum(float(x.get("monto") or 0) for x in items), "items": items}


@router.get("/seguimiento/pendientes")
def seguimiento_pendientes(
    limit: int = Query(120, ge=1, le=300),
    me: dict = Depends(get_current_user),
):
    with get_connection() as conn:
        _ensure_tables(conn)
        _require_crm(conn, me, "read")
        lcols = _cols(conn, "leads")
        params: dict[str, Any] = {"lim": int(limit)}
        scope = _lead_scope_sql(me, params)
        rows = conn.execute(
            text(
                f"""
                WITH rules AS (
                  SELECT DISTINCT ON (upper(btrim(estado_like)))
                         *
                  FROM public.crm_followup_rules
                  WHERE is_active IS TRUE
                  ORDER BY upper(btrim(estado_like)), id_rule
                )
                SELECT l.id_lead,
                       {_lead_name_expr(lcols)} AS cliente,
                       COALESCE(m.nombre,m.marca,'') AS marca,
                       COALESCE(e.nombre,'') AS estado,
                       l.fecha_evento,
                       COALESCE(l.monto_cotizado,0) AS monto,
                       COALESCE(l.updated_at,l.created_at) AS last_activity,
                       r.label,
                       r.max_hours,
                       FLOOR(EXTRACT(EPOCH FROM (now() - COALESCE(l.updated_at,l.created_at))) / 3600)::int AS hours_idle,
                       CASE
                         WHEN l.fecha_evento IS NULL THEN NULL
                         ELSE (l.fecha_evento - (now() AT TIME ZONE 'America/Santiago')::date)::int
                       END AS days_to_event
                FROM public.leads l
                LEFT JOIN public.marcas m ON m.id_marca=l.id_marca
                LEFT JOIN public.estados_lead e ON e.id_estado=l.id_estado
                JOIN rules r ON UPPER(COALESCE(e.nombre,'')) LIKE '%' || UPPER(r.estado_like) || '%'
                WHERE COALESCE(l.is_deleted,false)=false
                  AND UPPER(COALESCE(e.nombre,'')) NOT LIKE '%CONFIRM%'
                  AND UPPER(COALESCE(e.nombre,'')) NOT LIKE '%DECLIN%'
                  AND l.fecha_evento IS NOT NULL
                  AND l.fecha_evento >= (now() AT TIME ZONE 'America/Santiago')::date
                  AND l.fecha_evento >= date_trunc('month', (now() AT TIME ZONE 'America/Santiago')::date)::date
                  AND l.fecha_evento < (date_trunc('month', (now() AT TIME ZONE 'America/Santiago')::date) + INTERVAL '1 month')::date
                  AND COALESCE(l.updated_at,l.created_at) < now() - make_interval(hours => r.max_hours)
                  {scope}
                ORDER BY hours_idle DESC
                LIMIT :lim
                """
            ),
            params,
        ).mappings().all()
        return {"ok": True, "items": [dict(r) for r in rows]}


@router.get("/seguimiento/rules")
def seguimiento_rules(me: dict = Depends(get_current_user)):
    with get_connection() as conn:
        _ensure_tables(conn)
        _require_crm(conn, me, "read")
        rows = conn.execute(
            text("SELECT * FROM public.crm_followup_rules ORDER BY id_rule")
        ).mappings().all()
        return {"ok": True, "items": [dict(r) for r in rows]}


@router.post("/quotes/{id_cotizacion}/approval/request")
def request_quote_approval(id_cotizacion: int, me: dict = Depends(get_current_user)):
    with get_connection() as conn:
        _ensure_tables(conn)
        _require_crm(conn, me, "full")
        who = username(me) or str(_uid(me) or "")
        row = conn.execute(
            text(
                """
                UPDATE public.cotizaciones
                SET approval_status='PENDIENTE',
                    approval_requested_at=now(),
                    approval_requested_by=:who,
                    updated_at=COALESCE(updated_at, now())
                WHERE id_cotizacion=:id
                RETURNING id_cotizacion, id_lead, numero, approval_status
                """
            ),
            {"id": int(id_cotizacion), "who": who},
        ).mappings().first()
        if not row:
            raise HTTPException(404, "Cotización no existe")
        log_activity(
            conn,
            username=who,
            user_id=_uid(me),
            role=_role(me),
            action="quote.approval.request",
            entity_type="quote",
            entity_id=int(id_cotizacion),
            meta={"id_lead": row.get("id_lead"), "numero": row.get("numero")},
        )
        conn.commit()
        return {"ok": True, "item": dict(row)}


@router.post("/quotes/{id_cotizacion}/approval/approve")
def approve_quote(id_cotizacion: int, me: dict = Depends(get_current_user)):
    if not _is_admin(me):
        raise HTTPException(403, "Solo Admin/SuperAdmin aprueba cotizaciones")
    with get_connection() as conn:
        _ensure_tables(conn)
        who = username(me) or str(_uid(me) or "")
        row = conn.execute(
            text(
                """
                UPDATE public.cotizaciones
                SET approval_status='APROBADA',
                    approved_at=now(),
                    approved_by=:who,
                    estado=COALESCE(NULLIF(estado,''),'APROBADA')
                WHERE id_cotizacion=:id
                RETURNING id_cotizacion, id_lead, numero, approval_status
                """
            ),
            {"id": int(id_cotizacion), "who": who},
        ).mappings().first()
        if not row:
            raise HTTPException(404, "Cotización no existe")
        log_activity(conn, username=who, user_id=_uid(me), role=_role(me), action="quote.approval.approve", entity_type="quote", entity_id=int(id_cotizacion), meta={"id_lead": row.get("id_lead"), "numero": row.get("numero")})
        conn.commit()
        return {"ok": True, "item": dict(row)}


@router.post("/quotes/{id_cotizacion}/mark_sent")
def mark_quote_sent(id_cotizacion: int, payload: dict[str, Any] = Body(default_factory=dict), me: dict = Depends(get_current_user)):
    with get_connection() as conn:
        _ensure_tables(conn)
        _require_crm(conn, me, "full")
        who = username(me) or str(_uid(me) or "")
        sent_to = str(payload.get("sent_to") or payload.get("email") or "").strip()[:240]
        row = conn.execute(
            text(
                """
                UPDATE public.cotizaciones
                SET sent_at=now(), sent_to=:to, estado='ENVIADA'
                WHERE id_cotizacion=:id
                RETURNING id_cotizacion, id_lead, numero, sent_at, sent_to
                """
            ),
            {"id": int(id_cotizacion), "to": sent_to or None},
        ).mappings().first()
        if not row:
            raise HTTPException(404, "Cotización no existe")
        log_activity(conn, username=who, user_id=_uid(me), role=_role(me), action="quote.sent", entity_type="quote", entity_id=int(id_cotizacion), meta={"id_lead": row.get("id_lead"), "numero": row.get("numero"), "sent_to": sent_to})
        conn.commit()
        return {"ok": True, "item": dict(row)}


@router.get("/finanzas/cierre")
def finanzas_cierre(me: dict = Depends(get_current_user)):
    with get_connection() as conn:
        _ensure_tables(conn)
        _require_crm(conn, me, "read")
        lcols = _cols(conn, "leads")
        params: dict[str, Any] = {}
        scope = _lead_scope_sql(me, params)
        rows = conn.execute(
            text(
                f"""
                SELECT l.id_lead,
                       {_lead_name_expr(lcols)} AS cliente,
                       COALESCE(m.nombre,m.marca,'') AS marca,
                       l.fecha_evento,
                       COALESCE(l.monto_cotizado,0) AS monto,
                       fe.id_evento,
                       COALESCE(fe.abono,0) AS abono,
                       COALESCE(fe.saldo,0) AS saldo,
                       COALESCE(fe.factura_num,'') AS factura_num
                FROM public.leads l
                LEFT JOIN public.estados_lead e ON e.id_estado=l.id_estado
                LEFT JOIN public.marcas m ON m.id_marca=l.id_marca
                LEFT JOIN public.fin_eventos fe ON fe.id_lead=l.id_lead
                WHERE COALESCE(l.is_deleted,false)=false
                  AND UPPER(COALESCE(e.nombre,'')) LIKE '%CONFIRM%'
                  {scope}
                  AND (fe.id_evento IS NULL OR COALESCE(fe.saldo,0) > 0 OR COALESCE(fe.factura_num,'')='')
                ORDER BY l.fecha_evento ASC NULLS LAST, l.id_lead DESC
                LIMIT 250
                """
            ),
            params,
        ).mappings().all()
        return {"ok": True, "items": [dict(r) for r in rows]}


@router.post("/operaciones/{id_lead}/closure")
def save_operation_closure(id_lead: int, payload: dict[str, Any] = Body(default_factory=dict), me: dict = Depends(get_current_user)):
    with get_connection() as conn:
        _ensure_tables(conn)
        _require_crm(conn, me, "full")
        status = str(payload.get("status") or "PENDIENTE").strip().upper()[:40]
        costo_real = float(payload.get("costo_real") or 0)
        incidencias = str(payload.get("incidencias") or "").strip()[:5000]
        cierre = str(payload.get("cierre_operativo") or payload.get("cierre") or "").strip()[:5000]
        monto = conn.execute(text("SELECT COALESCE(monto_cotizado,0) FROM public.leads WHERE id_lead=:id"), {"id": int(id_lead)}).scalar()
        margen = float(monto or 0) - costo_real
        row = conn.execute(
            text(
                """
                INSERT INTO public.crm_event_closures(id_lead, closed_by, status, costo_real, margen_real, incidencias, cierre_operativo, updated_at)
                VALUES (:id, :uid, :st, :cr, :mg, :inc, :cie, now())
                ON CONFLICT (id_lead) DO UPDATE
                SET closed_by=:uid, status=:st, costo_real=:cr, margen_real=:mg,
                    incidencias=:inc, cierre_operativo=:cie, updated_at=now()
                RETURNING *
                """
            ),
            {"id": int(id_lead), "uid": _uid(me), "st": status, "cr": costo_real, "mg": margen, "inc": incidencias or None, "cie": cierre or None},
        ).mappings().first()
        log_activity(conn, username=username(me), user_id=_uid(me), role=_role(me), action="operation.post_event.close", entity_type="lead", entity_id=int(id_lead), meta={"status": status, "costo_real": costo_real, "margen_real": margen})
        conn.commit()
        return {"ok": True, "item": dict(row)}
