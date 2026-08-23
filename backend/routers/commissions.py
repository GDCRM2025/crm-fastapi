from __future__ import annotations

from datetime import date
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from backend.core.db import get_connection
from backend.routers.auth import get_current_user

router = APIRouter(prefix="/commissions", tags=["commissions"])


def _role(user: dict) -> str:
    return str(user.get("role") or user.get("rol") or "").upper()


def _is_admin(user: dict) -> bool:
    r = _role(user)
    return "ADMIN" in r or "SUPER" in r


def _require_admin(user: dict) -> None:
    if not _is_admin(user):
        raise HTTPException(403, "Solo Admin/SuperAdmin")


def _current_user_id(conn, user: dict) -> int | None:
    for key in ("id", "id_usuario", "user_id"):
        raw = user.get(key)
        if str(raw or "").isdigit():
            return int(raw)
    cand = [
        str(user.get("username") or "").strip(),
        str(user.get("email") or "").strip(),
        str(user.get("name") or "").strip(),
    ]
    cand = [x for x in cand if x]
    if not cand or not _table_exists(conn, "usuarios"):
        return None
    for c in cand:
        try:
            v = conn.execute(
                text(
                    """
                    SELECT id_usuario
                    FROM public.usuarios
                    WHERE lower(COALESCE(username,''))=lower(:u)
                       OR lower(COALESCE(email,''))=lower(:u)
                       OR lower(COALESCE(nombre,''))=lower(:u)
                    ORDER BY id_usuario
                    LIMIT 1
                    """
                ),
                {"u": c},
            ).scalar()
            if v is not None:
                return int(v)
        except Exception:
            pass
    return None


def _user_brand_ids(conn, user: dict) -> list[int]:
    ids: list[int] = []
    for raw in (user.get("marcas") or []):
        if str(raw).isdigit():
            ids.append(int(raw))
    uid = _current_user_id(conn, user)
    if uid and _table_exists(conn, "usuarios_marcas"):
        try:
            rows = conn.execute(
                text("SELECT id_marca FROM public.usuarios_marcas WHERE id_usuario=:u ORDER BY id_marca"),
                {"u": uid},
            ).fetchall()
            for r in rows:
                if r and r[0] is not None:
                    ids.append(int(r[0]))
        except Exception:
            pass
    return sorted(set(ids))


def _table_exists(conn, table: str) -> bool:
    try:
        return bool(conn.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"public.{table}"}).scalar())
    except Exception:
        return False


def _col_exists(conn, table: str, col: str) -> bool:
    try:
        return bool(
            conn.execute(
                text(
                    """
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema='public' AND table_name=:t AND column_name=:c
                    LIMIT 1
                    """
                ),
                {"t": table, "c": col},
            ).scalar()
        )
    except Exception:
        return False


def ensure_commission_tables(conn) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.commission_rules (
              id_rule SERIAL PRIMARY KEY,
              tipo_cliente TEXT NOT NULL,
              definicion TEXT,
              porcentaje_base NUMERIC(6,2) NOT NULL DEFAULT 0,
              meses_antiguedad INT NOT NULL DEFAULT 12,
              prioridad INT NOT NULL DEFAULT 10,
              is_active BOOLEAN NOT NULL DEFAULT TRUE,
              created_at TIMESTAMP DEFAULT now(),
              updated_at TIMESTAMP DEFAULT now()
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.commission_channel_bonus (
              id_bonus SERIAL PRIMARY KEY,
              canal TEXT NOT NULL,
              descripcion TEXT,
              porcentaje_extra NUMERIC(6,2) NOT NULL DEFAULT 0,
              requiere_gestion_ejecutivo BOOLEAN NOT NULL DEFAULT TRUE,
              is_active BOOLEAN NOT NULL DEFAULT TRUE,
              created_at TIMESTAMP DEFAULT now(),
              updated_at TIMESTAMP DEFAULT now()
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.commission_group_bonus (
              id_group_bonus SERIAL PRIMARY KEY,
              nivel TEXT NOT NULL,
              cumplimiento_min_pct NUMERIC(6,2) NOT NULL DEFAULT 100,
              bono_sobre_comision_pct NUMERIC(6,2) NOT NULL DEFAULT 0,
              orden INT NOT NULL DEFAULT 10,
              is_active BOOLEAN NOT NULL DEFAULT TRUE,
              created_at TIMESTAMP DEFAULT now(),
              updated_at TIMESTAMP DEFAULT now()
            )
            """
        )
    )
    conn.execute(
        text(
            """
            INSERT INTO public.commission_rules(tipo_cliente, definicion, porcentaje_base, meses_antiguedad, prioridad)
            SELECT 'CLIENTE NUEVO', 'Primera compra o sin compras en los últimos 12 meses', 3.00, 12, 10
            WHERE NOT EXISTS (SELECT 1 FROM public.commission_rules WHERE upper(tipo_cliente)='CLIENTE NUEVO')
            """
        )
    )
    conn.execute(
        text(
            """
            INSERT INTO public.commission_rules(tipo_cliente, definicion, porcentaje_base, meses_antiguedad, prioridad)
            SELECT 'CLIENTE ANTIGUO', 'Compra confirmada en los últimos 12 meses', 1.50, 12, 20
            WHERE NOT EXISTS (SELECT 1 FROM public.commission_rules WHERE upper(tipo_cliente)='CLIENTE ANTIGUO')
            """
        )
    )
    for canal in ("INSTAGRAM", "MAIL"):
        conn.execute(
            text(
                """
                INSERT INTO public.commission_channel_bonus(canal, descripcion, porcentaje_extra, requiere_gestion_ejecutivo)
                SELECT :canal, 'Captación digital gestionada por ejecutivo', 1.00, TRUE
                WHERE NOT EXISTS (SELECT 1 FROM public.commission_channel_bonus WHERE upper(canal)=:canal)
                """
            ),
            {"canal": canal},
        )
    for nivel, cumplimiento, bono, orden in (("BRONCE", 100, 2, 10), ("PLATA", 115, 3, 20), ("ORO", 125, 5, 30)):
        conn.execute(
            text(
                """
                INSERT INTO public.commission_group_bonus(nivel, cumplimiento_min_pct, bono_sobre_comision_pct, orden)
                SELECT :nivel, :cumplimiento, :bono, :orden
                WHERE NOT EXISTS (SELECT 1 FROM public.commission_group_bonus WHERE upper(nivel)=:nivel)
                """
            ),
            {"nivel": nivel, "cumplimiento": cumplimiento, "bono": bono, "orden": orden},
        )


def _confirmed_id(conn) -> int | None:
    try:
        v = conn.execute(
            text("SELECT id_estado FROM public.estados_lead WHERE upper(nombre) LIKE '%CONFIRM%' ORDER BY id_estado LIMIT 1")
        ).scalar()
        return int(v) if v is not None else None
    except Exception:
        return None


def _norm_phone_expr(expr: str) -> str:
    return f"regexp_replace(COALESCE({expr},''), '[^0-9]+', '', 'g')"


@router.post("/ensure")
def commissions_ensure(user: dict = Depends(get_current_user)):
    _require_admin(user)
    with get_connection() as conn:
        ensure_commission_tables(conn)
        conn.commit()
    return {"ok": True}


@router.get("/preview")
def commissions_preview(
    year: int = Query(..., ge=2020, le=2100),
    month: int = Query(..., ge=1, le=12),
    user: dict = Depends(get_current_user),
):
    """Cálculo preliminar configurable. Admin ve todo; ejecutivo ve sus marcas asignadas."""
    start = date(int(year), int(month), 1)
    end = date(int(year) + (1 if int(month) == 12 else 0), 1 if int(month) == 12 else int(month) + 1, 1)
    with get_connection() as conn:
        ensure_commission_tables(conn)
        conf = _confirmed_id(conn)
        if not conf or not _table_exists(conn, "leads"):
            return {"ok": True, "items": [], "summary": {}}
        cols = {r[0] for r in conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='leads'")).fetchall()}
        if "fecha_evento" not in cols or "id_estado" not in cols or "id_lead" not in cols:
            return {"ok": True, "items": [], "summary": {}, "warning": "Tabla leads sin columnas mínimas para calcular comisiones"}
        scope_sql = ""
        params: Dict[str, Any] = {"conf": conf, "start": start, "end": end}
        if not _is_admin(user):
            brand_ids = _user_brand_ids(conn, user)
            if "id_marca" not in cols or not brand_ids:
                return {"ok": True, "year": int(year), "month": int(month), "items": [], "summary": {"total_comision": 0, "por_ejecutivo": {}}}
            scope_sql = " AND l.id_marca = ANY(:brand_ids)"
            params["brand_ids"] = brand_ids
        cliente_expr = "COALESCE(NULLIF(btrim(l.cliente),''), NULLIF(btrim(l.nombre_cliente),''), '')" if {"cliente", "nombre_cliente"} <= cols else ("COALESCE(l.cliente,'')" if "cliente" in cols else ("COALESCE(l.nombre_cliente,'')" if "nombre_cliente" in cols else "''::text"))
        email_expr = "lower(NULLIF(btrim(l.email),''))" if "email" in cols else "NULL::text"
        tel_expr = _norm_phone_expr("l.telefono") if "telefono" in cols else "NULL::text"
        rut_expr = "regexp_replace(upper(COALESCE(l.rut,'')), '[^0-9K]+', '', 'g')" if "rut" in cols else "NULL::text"
        prior_email_expr = "lower(NULLIF(btrim(l2.email),''))" if "email" in cols else "NULL::text"
        prior_tel_expr = _norm_phone_expr("l2.telefono") if "telefono" in cols else "NULL::text"
        prior_rut_expr = "regexp_replace(upper(COALESCE(l2.rut,'')), '[^0-9K]+', '', 'g')" if "rut" in cols else "NULL::text"
        amount_candidates = [
            c
            for c in (
                "monto_neto",
                "valor_neto",
                "monto_final",
                "monto_cotizado",
                "valor",
                "monto",
                "total",
            )
            if c in cols
        ]
        monto_expr = "COALESCE(" + ",".join(f"l.{c}" for c in amount_candidates) + ",0)" if amount_candidates else "0"
        canal_expr = "COALESCE(l.canal,l.origen,l.fuente,'')" if {"canal", "origen", "fuente"} <= cols else (
            "COALESCE(l.canal,'')" if "canal" in cols else ("COALESCE(l.origen,'')" if "origen" in cols else ("COALESCE(l.fuente,'')" if "fuente" in cols else "''::text"))
        )
        calendar_expr = "COALESCE(l.calendar_html_link,'')" if "calendar_html_link" in cols else "''::text"
        can_brand_exec = "id_marca" in cols and _table_exists(conn, "usuarios_marcas") and _table_exists(conn, "usuarios")
        brand_exec_cte = """
        brand_exec AS (
          SELECT um.id_marca,
                 string_agg(COALESCE(u.nombre, u.username, u.email, u.id_usuario::text), ', ' ORDER BY COALESCE(u.nombre, u.username, u.email, u.id_usuario::text)) AS ejecutivo_marca
          FROM public.usuarios_marcas um
          JOIN public.usuarios u ON u.id_usuario=um.id_usuario
          WHERE COALESCE(u.is_active, TRUE) IS TRUE
            AND upper(COALESCE(u.rol, u.cargo, '')) LIKE '%EJECUTIVO%'
          GROUP BY um.id_marca
        ),
        """ if can_brand_exec else """
        brand_exec AS (
          SELECT NULL::integer AS id_marca, NULL::text AS ejecutivo_marca WHERE FALSE
        ),
        """
        ejecutivo_expr = "COALESCE(NULLIF(COALESCE(u.nombre, u.username, u.email, l.id_usuario::text, ''), ''), be.ejecutivo_marca, '')" if "id_usuario" in cols and _table_exists(conn, "usuarios") else ("COALESCE(be.ejecutivo_marca, l.id_usuario::text, '')" if "id_usuario" in cols else "COALESCE(be.ejecutivo_marca, '')")
        user_join = "LEFT JOIN public.usuarios u ON u.id_usuario=l.id_usuario" if "id_usuario" in cols and _table_exists(conn, "usuarios") else ""
        brand_exec_join = "LEFT JOIN brand_exec be ON be.id_marca=l.id_marca" if "id_marca" in cols else ""
        marca_join = ""
        marca_expr = "''::text"
        if "id_marca" in cols and _table_exists(conn, "marcas"):
            marca_join = "LEFT JOIN public.marcas m ON m.id_marca=l.id_marca"
            marca_cols = {r[0] for r in conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='marcas'")).fetchall()}
            marca_expr = "COALESCE(m.nombre,m.marca,'')" if {"nombre", "marca"} <= marca_cols else ("COALESCE(m.nombre,'')" if "nombre" in marca_cols else "COALESCE(m.marca,'')")

        identity_checks = []
        if "rut" in cols:
            identity_checks.append(f"(s.rut_key IS NOT NULL AND s.rut_key<>'' AND {prior_rut_expr}=s.rut_key)")
        if "email" in cols:
            identity_checks.append(f"(s.email_key IS NOT NULL AND s.email_key<>'' AND {prior_email_expr}=s.email_key)")
        if "telefono" in cols:
            identity_checks.append(f"(s.phone_key IS NOT NULL AND s.phone_key<>'' AND {prior_tel_expr}=s.phone_key)")
        identity_sql = " OR ".join(identity_checks) if identity_checks else "FALSE"

        q = f"""
        WITH {brand_exec_cte}
        sales AS (
          SELECT l.id_lead, l.fecha_evento::date AS fecha_evento, {cliente_expr} AS cliente,
                 {email_expr} AS email_key, {tel_expr} AS phone_key, {rut_expr} AS rut_key,
                 {marca_expr} AS marca, {canal_expr} AS canal, {ejecutivo_expr} AS ejecutivo,
                 {calendar_expr} AS calendar_html_link,
                 ({monto_expr})::numeric AS venta_neta
          FROM public.leads l
          {marca_join}
          {user_join}
          {brand_exec_join}
          WHERE l.id_estado=:conf AND l.fecha_evento >= :start AND l.fecha_evento < :end {scope_sql}
        ),
        hist AS (
          SELECT s.id_lead, COUNT(l2.id_lead)::int AS compras_12m
          FROM sales s
          JOIN public.leads l2 ON l2.id_estado=:conf
            AND l2.id_lead <> s.id_lead
            AND l2.fecha_evento < s.fecha_evento
            AND l2.fecha_evento >= (s.fecha_evento - interval '12 months')
            AND ({identity_sql})
          GROUP BY s.id_lead
        ),
        rules AS (
          SELECT * FROM public.commission_rules WHERE is_active IS TRUE
        ),
        chan AS (
          SELECT upper(canal) AS canal, porcentaje_extra FROM public.commission_channel_bonus WHERE is_active IS TRUE
        )
        SELECT s.*,
               COALESCE(h.compras_12m,0) AS compras_12m,
               CASE WHEN COALESCE(h.compras_12m,0) > 0 THEN 'CLIENTE ANTIGUO' ELSE 'CLIENTE NUEVO' END AS tipo_cliente_calc,
               COALESCE((SELECT porcentaje_base FROM rules r WHERE upper(r.tipo_cliente)=CASE WHEN COALESCE(h.compras_12m,0) > 0 THEN 'CLIENTE ANTIGUO' ELSE 'CLIENTE NUEVO' END ORDER BY prioridad LIMIT 1),0) AS pct_base,
               COALESCE((SELECT porcentaje_extra FROM chan c WHERE upper(s.canal) LIKE '%' || c.canal || '%' ORDER BY porcentaje_extra DESC LIMIT 1),0) AS pct_canal
        FROM sales s
        LEFT JOIN hist h ON h.id_lead=s.id_lead
        ORDER BY s.fecha_evento ASC, s.id_lead ASC
        """
        rows = [dict(r) for r in conn.execute(text(q), params).mappings().all()]
        for r in rows:
            venta = float(r.get("venta_neta") or 0)
            pct_total = float(r.get("pct_base") or 0) + float(r.get("pct_canal") or 0)
            r["pct_total"] = pct_total
            r["comision_preliminar"] = round(venta * pct_total / 100.0, 0)
        total = sum(float(r.get("comision_preliminar") or 0) for r in rows)
        by_exec: Dict[str, float] = {}
        for r in rows:
            k = str(r.get("ejecutivo") or "SIN EJECUTIVO")
            by_exec[k] = by_exec.get(k, 0.0) + float(r.get("comision_preliminar") or 0)
        try:
            conn.commit()
        except Exception:
            conn.rollback()
        return {"ok": True, "year": int(year), "month": int(month), "items": rows, "summary": {"total_comision": total, "por_ejecutivo": by_exec}}
