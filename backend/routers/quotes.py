from fastapi import APIRouter, Query
from sqlalchemy import text
from backend.core.db import get_connection

router = APIRouter(prefix="/quotes")

@router.get("/history")
def historial(id_lead: int = Query(..., ge=1)):
    with get_connection() as conn:
        rows = conn.execute(text("""
          SELECT
            id_cotizacion,
            numero,
            fecha::text AS fecha_emision,
            COALESCE(subtotal_productos,0) AS subtotal,
            COALESCE(iva,0) AS iva,
            COALESCE(total,0) AS total,
            created_at::text AS created_at,
            COALESCE(creado_por,'') AS created_by
          FROM public.cotizaciones
          WHERE id_lead=:id
          ORDER BY id_cotizacion DESC
        """), {"id": id_lead}).mappings().all()

        return {"items": list(rows)}
