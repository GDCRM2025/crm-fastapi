from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session


def require_open_customer_window(db: Session, conversation_id: int) -> None:
    """Bloquea mensajes libres fuera de la ventana de atención de 24 horas."""
    is_open = bool(
        db.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM public.whatsapp_messages
                    WHERE conversation_id=:conversation_id
                      AND direction='inbound'
                      AND sent_at >= now() - interval '24 hours'
                )
                """
            ),
            {"conversation_id": int(conversation_id)},
        ).scalar()
    )
    if not is_open:
        raise HTTPException(
            status_code=409,
            detail=(
                "La ventana de WhatsApp de 24 horas está cerrada. "
                "El cliente debe escribir nuevamente o debes enviar una plantilla aprobada por Meta."
            ),
        )
