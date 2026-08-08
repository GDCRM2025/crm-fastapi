# backend/routers/lead_estados.py
from fastapi import APIRouter
from sqlalchemy import text

from backend.core.db import get_connection

router = APIRouter(tags=["lead_estados"])


def table_exists(conn, table: str) -> bool:
    return bool(conn.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}).scalar())


@router.get("/lead-estados")
def lead_estados():
    with get_connection() as conn:
        if table_exists(conn, "estados_lead"):
            rows = conn.execute(
                text(
                    """
                    SELECT id_estado, nombre, nombre AS estado, color, orden, is_active
                    FROM public.estados_lead
                    ORDER BY orden, id_estado
                    """
                )
            ).mappings().all()
        elif table_exists(conn, "lead_estados"):
            rows = conn.execute(
                text(
                    """
                    SELECT id_estado, estado, estado AS nombre, color, orden, is_active
                    FROM public.lead_estados
                    ORDER BY orden, id_estado
                    """
                )
            ).mappings().all()
        else:
            rows = []
    return {"items": list(rows)}
