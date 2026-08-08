from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


# Un único lock transaccional para todas las inicializaciones de esquema
# WhatsApp. Evita que procesos Passenger distintos ejecuten DDL concurrente
# sobre whatsapp_channels, whatsapp_conversations y whatsapp_messages.
GREENIE_SCHEMA_LOCK_ID = 2_026_072_701


def acquire_greenie_schema_lock(db: Session) -> None:
    db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": GREENIE_SCHEMA_LOCK_ID},
    )
