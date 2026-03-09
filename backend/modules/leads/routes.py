# backend/modules/leads/routes.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from backend.core.database import get_db
from .models import Lead

router = APIRouter(prefix="/leads", tags=["Leads"])


@router.get("", response_model=list[dict])
def list_leads(db: Session = Depends(get_db)):
    rows = db.query(Lead).limit(50).all()
    return [{"id_lead": r.id_lead, "nombre": r.nombre} for r in rows]
