from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.models.usuario import User

router = APIRouter(prefix="/summary", tags=["summary"])

@router.get("", summary="Dashboard summary")
def get_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Datos mockeados para el panel
    return {
        "user": {"id": current_user.id, "email": current_user.email},
        "kpis": {
            "leads": 28,
            "deals_open": 7,
            "revenue_month": 15430.50,
            "conversion_rate": 0.23,
        },
        "notifications": [
            "3 leads nuevos asignados hoy",
            "2 oportunidades vencen esta semana",
        ],
    }
