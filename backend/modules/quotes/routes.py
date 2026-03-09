# backend/modules/quotes/routes.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from backend.core.database import get_db
from .models import Quote, QuoteItem
from backend.modules.leads.models import Lead

router = APIRouter(prefix="/quotes", tags=["Quotes"])


@router.post("", status_code=201)
def create_quote(payload: dict, db: Session = Depends(get_db)):
    id_lead = payload.get("id_lead")
    if not id_lead:
        raise HTTPException(400, "id_lead es requerido")

    lead = db.get(Lead, id_lead)
    if not lead:
        raise HTTPException(400, "El lead no existe")

    quote = Quote(id_lead=id_lead, fecha=None, total=0, pdf_url=None)
    db.add(quote)
    db.flush()  # obtiene id_cotizacion

    for item in payload.get("items", []):
        qi = QuoteItem(
            id_cotizacion=quote.id_cotizacion,
            producto=item.get("producto"),
            cantidad=item.get("cantidad", 1),
            precio_unitario=item.get("precio_unitario", 0),
        )
        db.add(qi)

    db.commit()
    db.refresh(quote)
    return {"id_cotizacion": quote.id_cotizacion}
