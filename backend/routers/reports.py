# backend/routers/reports.py
import sqlite3
from pathlib import Path
from typing import List, Dict, Any, Optional

from fastapi import APIRouter, Query

DB = "backend/crm.db"
BASE_DIR = Path(__file__).resolve().parents[1]

reports_router = APIRouter(prefix="/reports", tags=["reports"])


def get_cx():
    cx = sqlite3.connect(DB)
    cx.row_factory = sqlite3.Row
    return cx


STATE_ORDER = [
    "Pendiente",
    "Contactado",
    "En negociación",
    "Cotizado",
    "Confirmado",
    "Declinado",
]


def _build_date_filter(field_expr: str, fecha_inicio: Optional[str], fecha_termino: Optional[str]):
    where = []
    params: List[Any] = []
    if fecha_inicio:
        where.append(f"{field_expr} >= ?")
        params.append(fecha_inicio)
    if fecha_termino:
        where.append(f"{field_expr} <= ?")
        params.append(fecha_termino)
    return where, params


@reports_router.get("/funnel")
def funnel(
    fecha_inicio: Optional[str] = Query(None),
    fecha_termino: Optional[str] = Query(None),
    id_marca: Optional[int] = Query(None),
):
    """
    Funnel de venta total con desglose por marca.
    Usa tabla lead + estados_lead + marca.
    """
    cx = get_cx()
    c = cx.cursor()

    base_where, params = _build_date_filter("date(l.fecha_ingreso)", fecha_inicio, fecha_termino)

    if id_marca is not None:
        base_where.append("l.id_marca = ?")
        params.append(id_marca)

    where_sql = ""
    if base_where:
        where_sql = "WHERE " + " AND ".join(base_where)

    # total
    rows_total = c.execute(
        f"""
        SELECT e.nombre AS estado, COUNT(*) AS cantidad
        FROM lead l
        LEFT JOIN estados_lead e ON l.id_estado = e.id_estado
        {where_sql}
        GROUP BY e.nombre
        """,
        tuple(params),
    ).fetchall()

    total = {st: 0 for st in STATE_ORDER}
    for r in rows_total:
        if r["estado"]:
            total[r["estado"]] = int(r["cantidad"])

    total_list = [{"estado": st, "cantidad": total[st]} for st in STATE_ORDER]

    # por marca
    rows_marca = c.execute(
        f"""
        SELECT m.nombre AS marca, e.nombre AS estado, COUNT(*) AS cantidad
        FROM lead l
        LEFT JOIN estados_lead e ON l.id_estado = e.id_estado
        LEFT JOIN marca m        ON l.id_marca = m.id_marca
        {where_sql}
        GROUP BY m.nombre, e.nombre
        """,
        tuple(params),
    ).fetchall()
    cx.close()

    por_marca: Dict[str, Dict[str, int]] = {}
    for r in rows_marca:
        marca = r["marca"] or "Sin marca"
        estado = r["estado"] or "Sin estado"
        bucket = por_marca.setdefault(marca, {st: 0 for st in STATE_ORDER})
        if estado in bucket:
            bucket[estado] = int(r["cantidad"])

    por_marca_list = []
    for marca, estados in por_marca.items():
        por_marca_list.append(
            {
                "marca": marca,
                "estados": [{"estado": st, "cantidad": estados[st]} for st in STATE_ORDER],
            }
        )

    return {"total": total_list, "por_marca": por_marca_list}


@reports_router.get("/ventas-por-canal")
def ventas_por_canal(
    fecha_inicio: Optional[str] = Query(None),
    fecha_termino: Optional[str] = Query(None),
):
    """
    Ventas por canal (plataforma) considerando leads en estado Confirmado.
    """
    cx = get_cx()
    c = cx.cursor()

    base_where, params = _build_date_filter("date(l.fecha_ingreso)", fecha_inicio, fecha_termino)
    base_where.append("e.nombre = 'Confirmado'")
    where_sql = "WHERE " + " AND ".join(base_where)

    rows = c.execute(
        f"""
        SELECT
            l.plataforma AS canal,
            COUNT(*)     AS cantidad,
            SUM(COALESCE(l.monto_cotizado, 0)) AS monto
        FROM lead l
        LEFT JOIN estados_lead e ON l.id_estado = e.id_estado
        {where_sql}
        GROUP BY l.plataforma
        ORDER BY monto DESC
        """,
        tuple(params),
    ).fetchall()
    cx.close()
    return {"items": [dict(r) for r in rows]}


@reports_router.get("/top-productos")
def top_productos(
    fecha_inicio: Optional[str] = Query(None),
    fecha_termino: Optional[str] = Query(None),
    id_marca: Optional[int] = Query(None),
    limit: int = Query(10, ge=1, le=100),
):
    """
    Ranking de productos más vendidos según cotizaciones_detalle.
    """
    cx = get_cx()
    c = cx.cursor()

    where: List[str] = []
    params: List[Any] = []

    if fecha_inicio:
        where.append("date(c.fecha) >= ?")
        params.append(fecha_inicio)
    if fecha_termino:
        where.append("date(c.fecha) <= ?")
        params.append(fecha_termino)
    if id_marca is not None:
        where.append("l.id_marca = ?")
        params.append(id_marca)

    where_sql = ""
    if where:
        where_sql = "WHERE " + " AND ".join(where)

    rows = c.execute(
        f"""
        SELECT
            d.nombre_producto AS producto,
            m.nombre          AS marca,
            SUM(COALESCE(d.cantidad, 0))     AS cantidad_total,
            SUM(COALESCE(d.subtotal, 0))     AS monto_total
        FROM cotizaciones_detalle d
        JOIN cotizaciones c ON d.id_cotizacion = c.id_cotizacion
        JOIN lead l         ON c.id_lead       = l.id_lead
        LEFT JOIN marca m   ON l.id_marca      = m.id_marca
        {where_sql}
        GROUP BY d.nombre_producto, m.nombre
        ORDER BY monto_total DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    cx.close()
    return {"items": [dict(r) for r in rows]}


@reports_router.get("/clientes-frecuentes")
def clientes_frecuentes(
    fecha_inicio: Optional[str] = Query(None),
    fecha_termino: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=200),
):
    """
    Clientes con más eventos / mayor monto confirmado.
    """
    cx = get_cx()
    c = cx.cursor()

    base_where, params = _build_date_filter("date(l.fecha_ingreso)", fecha_inicio, fecha_termino)
    base_where.append("e.nombre = 'Confirmado'")
    where_sql = "WHERE " + " AND ".join(base_where)

    rows = c.execute(
        f"""
        SELECT
            l.nombre_cliente,
            COUNT(*) AS cantidad_eventos,
            SUM(COALESCE(l.monto_cotizado, 0)) AS monto_total
        FROM lead l
        LEFT JOIN estados_lead e ON l.id_estado = e.id_estado
        {where_sql}
        GROUP BY l.nombre_cliente
        ORDER BY cantidad_eventos DESC, monto_total DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    cx.close()
    return {"items": [dict(r) for r in rows]}


@reports_router.get("/comunas")
def comunas_frecuentes(
    fecha_inicio: Optional[str] = Query(None),
    fecha_termino: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    """
    Comunas más frecuentes y sus montos.
    """
    cx = get_cx()
    c = cx.cursor()

    base_where, params = _build_date_filter("date(l.fecha_ingreso)", fecha_inicio, fecha_termino)
    base_where.append("e.nombre = 'Confirmado'")
    where_sql = "WHERE " + " AND ".join(base_where)

    rows = c.execute(
        f"""
        SELECT
            co.nombre AS comuna,
            COUNT(*) AS cantidad_eventos,
            SUM(COALESCE(l.monto_cotizado, 0)) AS monto_total
        FROM lead l
        LEFT JOIN estados_lead e ON l.id_estado = e.id_estado
        LEFT JOIN comunas co     ON l.id_comuna = co.id_comuna
        {where_sql}
        GROUP BY co.nombre
        ORDER BY monto_total DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    cx.close()
    return {"items": [dict(r) for r in rows]}
