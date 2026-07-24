from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.core.database import get_db


router = APIRouter(tags=["WhatsApp"])
log = logging.getLogger("crm")


WHATSAPP_WEBHOOK_VERIFY_TOKEN = os.getenv(
    "WHATSAPP_WEBHOOK_VERIFY_TOKEN",
    "CHANGE_ME",
)

META_APP_SECRET = os.getenv(
    "META_APP_SECRET",
    "",
)

WHATSAPP_VALIDATE_SIGNATURE = (
    os.getenv("WHATSAPP_VALIDATE_SIGNATURE", "true")
    .strip()
    .lower()
    in ("1", "true", "yes", "on")
)


def verify_signature(
    raw_body: bytes,
    signature_header: str | None,
) -> bool:
    """
    Valida que el POST provenga realmente de Meta.

    Meta envía:
    X-Hub-Signature-256: sha256=<firma>
    """

    if not WHATSAPP_VALIDATE_SIGNATURE:
        return True

    if not META_APP_SECRET:
        log.error(
            "[WHATSAPP] META_APP_SECRET no configurado "
            "con validación de firma activa"
        )
        return False

    if not signature_header:
        return False

    if not signature_header.startswith("sha256="):
        return False

    provided_signature = signature_header.split("=", 1)[1].strip()

    expected_signature = hmac.new(
        META_APP_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(
        expected_signature,
        provided_signature,
    )


def ensure_tables(db: Session) -> None:
    """
    Crea las tablas iniciales del webhook.

    No toca leads, clientes ni cotizaciones.
    """

    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS whatsapp_webhook_events (
                id BIGSERIAL PRIMARY KEY,
                event_key TEXT NOT NULL UNIQUE,
                entry_id TEXT,
                field_name TEXT,
                phone_number_id TEXT,
                whatsapp_message_id TEXT,
                event_type TEXT,
                processing_status TEXT NOT NULL DEFAULT 'received',
                payload JSONB NOT NULL,
                received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                processed_at TIMESTAMPTZ,
                error_message TEXT
            )
            """
        )
    )

    db.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS
            idx_whatsapp_webhook_events_received
            ON whatsapp_webhook_events (received_at DESC)
            """
        )
    )

    db.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS
            idx_whatsapp_webhook_events_phone
            ON whatsapp_webhook_events (
                phone_number_id,
                received_at DESC
            )
            """
        )
    )

    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS whatsapp_channels (
                id BIGSERIAL PRIMARY KEY,
                brand_code TEXT NOT NULL UNIQUE,
                brand_name TEXT NOT NULL,
                executive_name TEXT,
                phone_number_id TEXT UNIQUE,
                waba_id TEXT,
                display_phone_number TEXT,
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )

    db.execute(
        text(
            """
            INSERT INTO whatsapp_channels (
                brand_code,
                brand_name,
                executive_name
            )
            VALUES
                ('CAMALEON', 'CAMALEON', 'ANDRES'),
                ('DEL_SABOR', 'DEL SABOR', 'ANDRES'),
                ('GOURMET', 'GOURMET', 'JORKINHA'),
                ('EXPRESS', 'EXPRESS', 'JORKINHA')
            ON CONFLICT (brand_code) DO NOTHING
            """
        )
    )

    db.commit()


def normalized_payload_hash(payload: dict[str, Any]) -> str:
    normalized = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    return hashlib.sha256(
        normalized.encode("utf-8")
    ).hexdigest()


def extract_events(
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Extrae mensajes y estados de forma defensiva.

    Un mismo POST de Meta puede contener más de un evento.
    """

    extracted: list[dict[str, Any]] = []

    entries = payload.get("entry") or []

    if not isinstance(entries, list):
        return extracted

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        entry_id = str(entry.get("id") or "") or None
        changes = entry.get("changes") or []

        if not isinstance(changes, list):
            continue

        for change in changes:
            if not isinstance(change, dict):
                continue

            field_name = str(change.get("field") or "") or None
            value = change.get("value") or {}

            if not isinstance(value, dict):
                value = {}

            metadata = value.get("metadata") or {}

            if not isinstance(metadata, dict):
                metadata = {}

            phone_number_id = (
                str(metadata.get("phone_number_id") or "")
                or None
            )

            messages = value.get("messages") or []

            if isinstance(messages, list):
                for message in messages:
                    if not isinstance(message, dict):
                        continue

                    message_id = (
                        str(message.get("id") or "")
                        or None
                    )

                    event_key = (
                        f"message:{message_id}"
                        if message_id
                        else f"payload:{normalized_payload_hash(payload)}"
                    )

                    extracted.append(
                        {
                            "event_key": event_key,
                            "entry_id": entry_id,
                            "field_name": field_name,
                            "phone_number_id": phone_number_id,
                            "whatsapp_message_id": message_id,
                            "event_type": (
                                f"message:{message.get('type') or 'unknown'}"
                            ),
                        }
                    )

            statuses = value.get("statuses") or []

            if isinstance(statuses, list):
                for status in statuses:
                    if not isinstance(status, dict):
                        continue

                    message_id = (
                        str(status.get("id") or "")
                        or None
                    )

                    status_name = (
                        str(status.get("status") or "unknown")
                    )

                    timestamp = (
                        str(status.get("timestamp") or "")
                    )

                    if message_id:
                        event_key = (
                            f"status:{message_id}:"
                            f"{status_name}:{timestamp}"
                        )
                    else:
                        event_key = (
                            f"payload:{normalized_payload_hash(payload)}"
                        )

                    extracted.append(
                        {
                            "event_key": event_key,
                            "entry_id": entry_id,
                            "field_name": field_name,
                            "phone_number_id": phone_number_id,
                            "whatsapp_message_id": message_id,
                            "event_type": f"status:{status_name}",
                        }
                    )

    if not extracted:
        extracted.append(
            {
                "event_key": (
                    f"payload:{normalized_payload_hash(payload)}"
                ),
                "entry_id": None,
                "field_name": None,
                "phone_number_id": None,
                "whatsapp_message_id": None,
                "event_type": "unknown",
            }
        )

    return extracted


def resolve_processing_status(
    db: Session,
    phone_number_id: str | None,
) -> str:
    """
    Determina si el número ya está asociado a una marca.
    """

    if not phone_number_id:
        return "unknown_channel"

    exists = db.execute(
        text(
            """
            SELECT 1
            FROM whatsapp_channels
            WHERE phone_number_id = :phone_number_id
              AND enabled = TRUE
            LIMIT 1
            """
        ),
        {
            "phone_number_id": phone_number_id,
        },
    ).scalar()

    if exists:
        return "received"

    return "unknown_channel"


def insert_event(
    db: Session,
    event: dict[str, Any],
    payload: dict[str, Any],
) -> bool:
    """
    Devuelve True si se insertó.
    Devuelve False si era duplicado.
    """

    processing_status = resolve_processing_status(
        db,
        event.get("phone_number_id"),
    )

    inserted = db.execute(
        text(
            """
            INSERT INTO whatsapp_webhook_events (
                event_key,
                entry_id,
                field_name,
                phone_number_id,
                whatsapp_message_id,
                event_type,
                processing_status,
                payload
            )
            VALUES (
                :event_key,
                :entry_id,
                :field_name,
                :phone_number_id,
                :whatsapp_message_id,
                :event_type,
                :processing_status,
                CAST(:payload AS JSONB)
            )
            ON CONFLICT (event_key) DO NOTHING
            RETURNING id
            """
        ),
        {
            "event_key": event["event_key"],
            "entry_id": event.get("entry_id"),
            "field_name": event.get("field_name"),
            "phone_number_id": event.get(
                "phone_number_id"
            ),
            "whatsapp_message_id": event.get(
                "whatsapp_message_id"
            ),
            "event_type": event.get("event_type"),
            "processing_status": processing_status,
            "payload": json.dumps(
                payload,
                ensure_ascii=False,
            ),
        },
    ).scalar()

    return inserted is not None


@router.get(
    "/meta/webhook/whatsapp",
    response_class=PlainTextResponse,
    include_in_schema=False,
)
async def whatsapp_verify(request: Request):
    """
    Verificación inicial solicitada por Meta.
    """

    query = request.query_params

    mode = query.get("hub.mode")
    verify_token = query.get("hub.verify_token")
    challenge = query.get("hub.challenge")

    if (
        mode == "subscribe"
        and verify_token == WHATSAPP_WEBHOOK_VERIFY_TOKEN
        and challenge
    ):
        log.info(
            "[WHATSAPP] Webhook verificado correctamente"
        )

        return PlainTextResponse(
            content=str(challenge),
            status_code=200,
        )

    log.warning(
        "[WHATSAPP] Verificación rechazada mode=%s",
        mode,
    )

    return PlainTextResponse(
        content="forbidden",
        status_code=403,
    )


@router.post(
    "/meta/webhook/whatsapp",
    include_in_schema=False,
)
async def whatsapp_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(
        default=None,
        alias="X-Hub-Signature-256",
    ),
    db: Session = Depends(get_db),
):
    """
    Recibe mensajes y estados enviados por Meta.
    """

    raw_body = await request.body()

    if not verify_signature(
        raw_body,
        x_hub_signature_256,
    ):
        log.warning(
            "[WHATSAPP] Firma inválida o ausente"
        )

        return JSONResponse(
            status_code=401,
            content={
                "ok": False,
                "error": "invalid_signature",
            },
        )

    try:
        payload = json.loads(
            raw_body.decode("utf-8")
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error": "invalid_json",
            },
        )

    if not isinstance(payload, dict):
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error": "invalid_payload",
            },
        )

    if (
        payload.get("object")
        != "whatsapp_business_account"
    ):
        log.info(
            "[WHATSAPP] Payload ignorado object=%s",
            payload.get("object"),
        )

        return {
            "ok": True,
            "status": "ignored",
        }

    try:
        ensure_tables(db)

        extracted_events = extract_events(payload)

        inserted_count = 0
        duplicate_count = 0

        for event in extracted_events:
            inserted = insert_event(
                db,
                event,
                payload,
            )

            if inserted:
                inserted_count += 1
            else:
                duplicate_count += 1

            log.info(
                "[WHATSAPP] event_key=%s "
                "phone_number_id=%s "
                "message_id=%s "
                "event_type=%s",
                event.get("event_key"),
                event.get("phone_number_id"),
                event.get("whatsapp_message_id"),
                event.get("event_type"),
            )

        db.commit()

        if inserted_count == 0 and duplicate_count > 0:
            return {
                "ok": True,
                "status": "duplicate",
                "duplicates": duplicate_count,
            }

        return {
            "ok": True,
            "status": "received",
            "inserted": inserted_count,
            "duplicates": duplicate_count,
        }

    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass

        log.exception(
            "[WHATSAPP] Error guardando webhook: %s",
            exc,
        )

        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "where": "whatsapp_webhook",
                "error": "internal_error",
            },
        )
