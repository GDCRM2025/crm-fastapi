from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List

from backend.core.database import get_db
from backend.core.auth import get_current_user, require_admin
from backend.modules.login.repository import User
from .models import Brand
from .schemas import BrandOut, BrandCreate, BrandUpdate

router = APIRouter(prefix="/brands", tags=["brands"])

@router.get("/raw")
def list_brands_raw(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user)
):
    # Para inspección rápida de columnas reales
    rows = db.execute(text("SELECT * FROM public.marcas ORDER BY 1 LIMIT 100")).mappings().all()
    return [dict(r) for r in rows]

@router.get("", response_model=List[BrandOut])
def list_brands(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user)
):
    return db.query(Brand).order_by(Brand.id_marca).all()

@router.post("", response_model=BrandOut, status_code=status.HTTP_201_CREATED)
def create_brand(
    payload: BrandCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin)  # solo admin crea
):
    exists = db.query(Brand).filter(Brand.nombre == payload.nombre).first()
    if exists:
        raise HTTPException(status_code=400, detail="La marca ya existe")
    b = Brand(nombre=payload.nombre)
    db.add(b)
    db.commit()
    db.refresh(b)
    return b

@router.put("/{id_marca}", response_model=BrandOut)
def update_brand(
    id_marca: int,
    payload: BrandUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin)  # solo admin edita
):
    b = db.get(Brand, id_marca)
    if not b:
        raise HTTPException(status_code=404, detail="Marca no encontrada")
    # Validar duplicado de nombre si cambia
    if payload.nombre != b.nombre:
        dup = db.query(Brand).filter(Brand.nombre == payload.nombre).first()
        if dup:
            raise HTTPException(status_code=400, detail="Ya existe una marca con ese nombre")
    b.nombre = payload.nombre
    db.add(b)
    db.commit()
    db.refresh(b)
    return b

# reemplaza solo la función delete_brand por esta versión
from sqlalchemy.exc import IntegrityError

@router.delete("/{id_marca}", status_code=status.HTTP_204_NO_CONTENT)
def delete_brand(
    id_marca: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin)
):
    b = db.get(Brand, id_marca)
    if not b:
        raise HTTPException(status_code=404, detail="Marca no encontrada")
    try:
        db.delete(b)
        db.commit()
    except IntegrityError:
        db.rollback()
        # Esta marca probablemente tiene productos / cotizaciones referenciándola
        raise HTTPException(
            status_code=409,
            detail="No se puede eliminar: la marca tiene datos asociados"
        )
    return
