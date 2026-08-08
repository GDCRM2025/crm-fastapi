# backend/routers/comunas.py
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.db import get_db

router = APIRouter(prefix="/comunas", tags=["comunas"])

@router.get("")
def list_comunas(page: int = 1, per: int = 50, db: Session = Depends(get_db)):
    page = max(1, int(page))
    per = max(1, min(500, int(per)))
    off = (page - 1) * per

    total = db.execute(text("SELECT COUNT(*) FROM comunas")).scalar_one()
    rows = db.execute(
        text("""
            SELECT id_comuna, nombre, neto, bruto, is_active
            FROM comunas
            ORDER BY nombre
            LIMIT :per OFFSET :off
        """),
        {"per": per, "off": off},
    ).mappings().all()

    return {"ok": True, "items": list(rows), "total": int(total), "page": page, "per": per}
