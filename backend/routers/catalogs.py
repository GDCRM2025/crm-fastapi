from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text

from backend.core.database import engine

try:
    from backend.routers.auth import get_current_user  # type: ignore
except Exception:  # pragma: no cover
    def get_current_user():  # type: ignore
        return {"rol": "Admin"}


router = APIRouter(prefix="/catalogs", tags=["catalogs"])


@router.get("/all")
def all_catalogs(user: dict = Depends(get_current_user)):
    with engine.connect() as cn:
        marcas = cn.execute(text("SELECT id_marca, nombre FROM marcas WHERE is_active=TRUE ORDER BY nombre")).mappings().all()
        comunas = cn.execute(text("SELECT id_comuna, nombre, neto, bruto FROM comunas ORDER BY nombre")).mappings().all()
        tipos = cn.execute(text("SELECT id_tipo_cliente, nombre, is_active FROM tipocliente ORDER BY nombre")).mappings().all()
        estados = cn.execute(text("""
            SELECT id_estado, nombre, color, orden, is_active
            FROM estados_lead
            WHERE is_active=TRUE
            ORDER BY orden, id_estado
        """)).mappings().all()
        roles = cn.execute(text("SELECT id_rol, nombre, is_active FROM roles ORDER BY nombre")).mappings().all()

    return {
        "ok": True,
        "catalogos": {
            "marcas": list(marcas),
            "comunas": list(comunas),
            "tipocliente": list(tipos),
            "estados_lead": list(estados),
            "roles": list(roles),
        }
    }
