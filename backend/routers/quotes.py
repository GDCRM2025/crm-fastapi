from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from backend.core.db import get_connection
from backend.routers.auth import get_current_user

router = APIRouter(prefix="/quotes")

@router.get("/history")
def historial(id_lead: int = Query(..., ge=1), me: dict = Depends(get_current_user)):
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


@router.get("/by_numero")
def quote_by_numero(
    numero: int = Query(..., ge=1),
    limit: int = Query(20, ge=1, le=200),
    me: dict = Depends(get_current_user),
):
    """
    Busca cotizaciones por `numero` (folio).
    Útil para diagnosticar cuando el usuario "elige" una cotización y el lead termina con otra.
    """
    role = (me.get("role") or me.get("rol") or "").upper().strip()
    if role not in ("ADMIN", "SUPERADMIN"):
        raise HTTPException(status_code=403, detail="No autorizado")
    with get_connection() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                  id_cotizacion,
                  id_lead,
                  numero,
                  created_at::text AS created_at,
                  COALESCE(subtotal_productos,0) AS subtotal_productos,
                  COALESCE(traslado,0) AS traslado,
                  COALESCE(iva,0) AS iva,
                  COALESCE(total,0) AS total,
                  COALESCE(estado,'') AS estado,
                  COALESCE(marca,'') AS marca,
                  COALESCE(nombre_cliente,'') AS nombre_cliente,
                  COALESCE(fecha_evento::text,'') AS fecha_evento
                FROM public.cotizaciones
                WHERE numero = :n
                ORDER BY id_cotizacion DESC
                LIMIT :lim
                """
            ),
            {"n": int(numero), "lim": int(limit)},
        ).mappings().all()
    return {"ok": True, "numero": int(numero), "items": list(rows)}
