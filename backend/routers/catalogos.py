# backend/routers/catalogos.py
from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from backend.core.db import get_connection
from backend.core.quote_assets import logo_for, normalize_marca

router = APIRouter()

def safe_query(conn, sql: str):
    try:
        return conn.execute(text(sql)).mappings().all()
    except ProgrammingError:
        return []

def table_exists(conn, table: str) -> bool:
    return bool(conn.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}).scalar())

def col_exists(conn, table: str, col: str) -> bool:
    return bool(
        conn.execute(
            text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name=:t AND column_name=:c
                LIMIT 1
                """
            ),
            {"t": table, "c": col},
        ).first()
    )

@router.get("/catalogos")
def catalogos():
    with get_connection() as conn:
        # Marcas (compat: marca|nombre)
        marcas = []
        if table_exists(conn, "marcas"):
            if col_exists(conn, "marcas", "marca"):
                marcas = safe_query(
                    conn,
                    """
                    SELECT id_marca, marca, marca AS nombre, is_active, COALESCE(logo_path,'') AS logo_path
                    FROM public.marcas
                    WHERE is_active=true
                    ORDER BY marca ASC
                    """,
                )
            elif col_exists(conn, "marcas", "nombre"):
                marcas = safe_query(
                    conn,
                    """
                    SELECT id_marca, nombre, nombre AS marca, is_active, '' AS logo_path
                    FROM public.marcas
                    WHERE is_active=true
                    ORDER BY nombre ASC
                    """,
                )
        # completar logo_path con catálogo si viene vacío
        marcas = [dict(m) for m in (marcas or [])]
        for m in marcas:
            key = normalize_marca(m.get("marca") or m.get("nombre") or "")
            if key and not (m.get("logo_path") or "").strip():
                m["logo_path"] = logo_for(key, prefer_local=True)

        # Comunas (compat: nombre|comuna, costo_traslado opcional)
        comunas = []
        if table_exists(conn, "comunas"):
            has_cost = col_exists(conn, "comunas", "costo_traslado")
            cost_expr = "COALESCE(costo_traslado,0) AS costo_traslado" if has_cost else "0 AS costo_traslado"
            if col_exists(conn, "comunas", "nombre"):
                comunas = safe_query(
                    conn,
                    f"""
                    SELECT id_comuna, nombre, nombre AS comuna, neto, bruto, is_active, {cost_expr}
                    FROM public.comunas
                    WHERE is_active=true
                    ORDER BY nombre ASC
                    """,
                )
            elif col_exists(conn, "comunas", "comuna"):
                comunas = safe_query(
                    conn,
                    f"""
                    SELECT id_comuna, comuna AS nombre, comuna, neto, bruto, is_active, {cost_expr}
                    FROM public.comunas
                    WHERE is_active=true
                    ORDER BY comuna ASC
                    """,
                )

        # Tipos cliente (compat: tipos_cliente.tipo | tipocliente.nombre)
        tipos_cliente = []
        if table_exists(conn, "tipos_cliente"):
            tipos_cliente = safe_query(
                conn,
                """
                SELECT id_tipo_cliente, tipo, tipo AS nombre, is_active
                FROM public.tipos_cliente
                WHERE is_active=true
                ORDER BY tipo ASC
                """,
            )
        elif table_exists(conn, "tipocliente"):
            tipos_cliente = safe_query(
                conn,
                """
                SELECT id_tipo_cliente, nombre, nombre AS tipo, is_active
                FROM public.tipocliente
                WHERE is_active=true
                ORDER BY nombre ASC
                """,
            )

        # Estados (compat: estados_lead.nombre | lead_estados.estado)
        estados_lead = []
        if table_exists(conn, "estados_lead"):
            estados_lead = safe_query(
                conn,
                """
                SELECT id_estado, nombre, nombre AS estado, color, orden, is_active
                FROM public.estados_lead
                WHERE is_active=true
                ORDER BY orden ASC, id_estado ASC
                """,
            )
        elif table_exists(conn, "lead_estados"):
            estados_lead = safe_query(
                conn,
                """
                SELECT id_estado, estado, estado AS nombre, color, orden, is_active
                FROM public.lead_estados
                WHERE is_active=true
                ORDER BY orden ASC, id_estado ASC
                """,
            )

    return {
        "marcas": list(marcas or []),
        "comunas": list(comunas or []),
        "tipos_cliente": list(tipos_cliente or []),
        "estados_lead": list(estados_lead or []),
        "estados": list(estados_lead or []),
        "tipocliente": list(tipos_cliente or []),
        "tipos": list(tipos_cliente or []),
    }
