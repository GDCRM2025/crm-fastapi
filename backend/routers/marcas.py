from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from backend.core.db import get_connection


router = APIRouter(tags=["marcas"])


class MarcaUpsert(BaseModel):
    marca: str = Field(min_length=1)
    is_active: bool = True
    logo_path: str | None = None


@router.get("/marcas")
@router.get("/web/marcas")
def list_marcas(only_active: bool = False):
    where_sql = "WHERE is_active IS TRUE" if only_active else ""
    with get_connection() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT id_marca, marca, is_active, logo_path, updated_at
                FROM public.marcas
                {where_sql}
                ORDER BY marca
                """
            )
        ).mappings().all()

    items = []
    for r in rows:
        items.append(
            {
                "id_marca": r["id_marca"],
                "marca": r.get("marca"),
                "is_active": bool(r["is_active"]) if r.get("is_active") is not None else True,
                "logo_path": r.get("logo_path"),
            }
        )
    return {"items": items}


@router.post("/marcas")
@router.post("/web/marcas")
def upsert_marca(payload: MarcaUpsert):
    with get_connection() as conn:
        conn.execute(
            text(
                """
                INSERT INTO public.marcas (marca, is_active, logo_path)
                VALUES (:marca, :is_active, :logo_path)
                ON CONFLICT (marca)
                DO UPDATE SET is_active=EXCLUDED.is_active,
                              logo_path=EXCLUDED.logo_path,
                              updated_at=NOW()
                """
            ),
            {
                "marca": payload.marca.strip(),
                "is_active": payload.is_active,
                "logo_path": payload.logo_path,
            },
        )
    return {"ok": True}