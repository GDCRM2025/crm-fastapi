from __future__ import annotations

from datetime import date
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.core.auth import get_current_user

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/reportes")
def reportes(
    id_marca: int | None = None,
    fecha_inicio: str | None = None,
    fecha_termino: str | None = None,
    db: Session = Depends(get_db),
    me=Depends(get_current_user),
):
    fi = None
    ft = None
    if fecha_inicio:
        try: fi = date.fromisoformat(fecha_inicio)
        except Exception: fi = None
    if fecha_termino:
        try: ft = date.fromisoformat(fecha_termino)
        except Exception: ft = None

    # Funnel por estado (conteo + suma)
    q_funnel = """
      SELECT e.nombre AS estado,
             COUNT(*)::int AS cantidad,
             COALESCE(SUM(l.monto_cotizado),0)::float AS monto
      FROM leads l
      LEFT JOIN estados_lead e ON e.id_estado=l.id_estado
      WHERE 1=1
    """
    params = {}
    if id_marca:
        q_funnel += " AND l.id_marca=:id_marca"
        params["id_marca"] = id_marca
    if fi:
        q_funnel += " AND l.fecha_ingreso >= :fi"
        params["fi"] = fi
    if ft:
        q_funnel += " AND l.fecha_ingreso <= :ft"
        params["ft"] = ft
    q_funnel += " GROUP BY e.nombre ORDER BY cantidad DESC"
    funnel = db.execute(text(q_funnel), params).mappings().all()

    # Eventos diarios (por fecha_evento en lead)
    q_ev = """
      SELECT l.fecha_evento::text AS dia,
             COUNT(*)::int AS cantidad,
             COALESCE(SUM(l.monto_cotizado),0)::float AS monto
      FROM leads l
      WHERE l.fecha_evento IS NOT NULL
    """
    if id_marca:
        q_ev += " AND l.id_marca=:id_marca"
    if fi:
        q_ev += " AND l.fecha_evento >= :fi"
    if ft:
        q_ev += " AND l.fecha_evento <= :ft"
    q_ev += " GROUP BY l.fecha_evento ORDER BY l.fecha_evento"
    eventos_diarios = db.execute(text(q_ev), params).mappings().all()

    # Top productos (detalle cotizaciones)
    q_top = """
      SELECT d.nombre_producto AS producto,
             COALESCE(SUM(d.cantidad),0)::float AS cantidad,
             COALESCE(SUM(d.subtotal),0)::float AS monto
      FROM cotizaciones_detalle d
      JOIN cotizaciones c ON c.id_cotizacion=d.id_cotizacion
      JOIN leads l ON l.id_lead=c.id_lead
      WHERE 1=1
    """
    if id_marca:
        q_top += " AND l.id_marca=:id_marca"
    if fi:
        q_top += " AND c.fecha::date >= :fi"
    if ft:
        q_top += " AND c.fecha::date <= :ft"
    q_top += " GROUP BY d.nombre_producto ORDER BY monto DESC LIMIT 20"
    top_productos = db.execute(text(q_top), params).mappings().all()

    # Clientes top
    q_cli = """
      SELECT l.nombre_cliente AS cliente,
             COUNT(c.id_cotizacion)::int AS cantidad,
             COALESCE(SUM(c.total),0)::float AS monto
      FROM cotizaciones c
      JOIN leads l ON l.id_lead=c.id_lead
      WHERE 1=1
    """
    if id_marca:
        q_cli += " AND l.id_marca=:id_marca"
    if fi:
        q_cli += " AND c.fecha::date >= :fi"
    if ft:
        q_cli += " AND c.fecha::date <= :ft"
    q_cli += " GROUP BY l.nombre_cliente ORDER BY monto DESC LIMIT 20"
    clientes = db.execute(text(q_cli), params).mappings().all()

    # Comunas top
    q_com = """
      SELECT co.nombre AS comuna,
             COUNT(*)::int AS cantidad,
             COALESCE(SUM(l.monto_cotizado),0)::float AS monto
      FROM leads l
      LEFT JOIN comunas co ON co.id_comuna=l.id_comuna
      WHERE 1=1
    """
    if id_marca:
        q_com += " AND l.id_marca=:id_marca"
    if fi:
        q_com += " AND l.fecha_ingreso >= :fi"
    if ft:
        q_com += " AND l.fecha_ingreso <= :ft"
    q_com += " GROUP BY co.nombre ORDER BY monto DESC NULLS LAST LIMIT 20"
    comunas = db.execute(text(q_com), params).mappings().all()

    return {
        "ok": True,
        "funnel": list(funnel),
        "eventos_diarios": list(eventos_diarios),
        "top_productos": list(top_productos),
        "clientes": list(clientes),
        "comunas": list(comunas),
    }
