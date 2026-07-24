from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.core.database import get_db


router = APIRouter(prefix="/gia/whatsapp", tags=["WhatsApp GIA"])


class SendMessageBody(BaseModel):
    text: str = Field(min_length=1, max_length=4096)


class AssignConversationBody(BaseModel):
    brand_code: str


BRANDS: dict[str, tuple[str, str]] = {
    "CAMALEON": ("CAMALEÓN", "Andrés Landerer"),
    "DEL_SABOR": ("DEL SABOR", "Walter Canales"),
    "GOURMET": ("GOURMET", "Constanza Franco"),
    "EXPRESS": ("EXPRESS", "Daniel Toledo"),
}


def _ensure_tables(db: Session) -> None:
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS whatsapp_contacts (
            id BIGSERIAL PRIMARY KEY,
            wa_id TEXT NOT NULL UNIQUE,
            profile_name TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS whatsapp_conversations (
            id BIGSERIAL PRIMARY KEY,
            contact_id BIGINT NOT NULL REFERENCES whatsapp_contacts(id),
            phone_number_id TEXT NOT NULL,
            brand_code TEXT,
            executive_name TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            unread_count INTEGER NOT NULL DEFAULT 0,
            last_message_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(contact_id, phone_number_id)
        )
    """))
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS whatsapp_messages (
            id BIGSERIAL PRIMARY KEY,
            conversation_id BIGINT NOT NULL REFERENCES whatsapp_conversations(id) ON DELETE CASCADE,
            whatsapp_message_id TEXT UNIQUE,
            direction TEXT NOT NULL,
            message_type TEXT NOT NULL DEFAULT 'text',
            body TEXT,
            status TEXT,
            sent_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            raw_payload JSONB
        )
    """))
    db.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_whatsapp_conversations_last_message
        ON whatsapp_conversations(last_message_at DESC)
    """))
    db.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_whatsapp_messages_conversation
        ON whatsapp_messages(conversation_id, sent_at)
    """))
    db.commit()


def _ts(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def _brand_for_phone(db: Session, phone_number_id: str) -> tuple[str | None, str | None]:
    row = db.execute(text("""
        SELECT brand_code, executive_name
        FROM whatsapp_channels
        WHERE phone_number_id = :phone_number_id AND enabled = TRUE
        LIMIT 1
    """), {"phone_number_id": phone_number_id}).mappings().first()
    if not row:
        return None, None
    return row["brand_code"], row["executive_name"]


def _upsert_contact(db: Session, wa_id: str, profile_name: str | None) -> int:
    return int(db.execute(text("""
        INSERT INTO whatsapp_contacts(wa_id, profile_name)
        VALUES (:wa_id, :profile_name)
        ON CONFLICT (wa_id) DO UPDATE SET
            profile_name = COALESCE(EXCLUDED.profile_name, whatsapp_contacts.profile_name),
            updated_at = now()
        RETURNING id
    """), {"wa_id": wa_id, "profile_name": profile_name}).scalar_one())


def _upsert_conversation(
    db: Session,
    contact_id: int,
    phone_number_id: str,
    sent_at: datetime,
    unread_delta: int,
) -> int:
    brand_code, executive_name = _brand_for_phone(db, phone_number_id)
    return int(db.execute(text("""
        INSERT INTO whatsapp_conversations(
            contact_id, phone_number_id, brand_code, executive_name,
            unread_count, last_message_at
        ) VALUES (
            :contact_id, :phone_number_id, :brand_code, :executive_name,
            :unread_delta, :sent_at
        )
        ON CONFLICT (contact_id, phone_number_id) DO UPDATE SET
            brand_code = COALESCE(EXCLUDED.brand_code, whatsapp_conversations.brand_code),
            executive_name = COALESCE(EXCLUDED.executive_name, whatsapp_conversations.executive_name),
            unread_count = whatsapp_conversations.unread_count + :unread_delta,
            last_message_at = GREATEST(whatsapp_conversations.last_message_at, EXCLUDED.last_message_at),
            updated_at = now()
        RETURNING id
    """), {
        "contact_id": contact_id,
        "phone_number_id": phone_number_id,
        "brand_code": brand_code,
        "executive_name": executive_name,
        "unread_delta": unread_delta,
        "sent_at": sent_at,
    }).scalar_one())


def _sync_webhook_events(db: Session) -> None:
    _ensure_tables(db)
    rows = db.execute(text("""
        SELECT id, event_type, whatsapp_message_id, payload
        FROM whatsapp_webhook_events
        WHERE processing_status IN ('received', 'unknown_channel')
        ORDER BY id
        LIMIT 500
    """)).mappings().all()

    for row in rows:
        payload = row["payload"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        try:
            for entry in payload.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value") or {}
                    metadata = value.get("metadata") or {}
                    phone_number_id = str(metadata.get("phone_number_id") or "")
                    contacts = value.get("contacts") or []
                    profile_by_wa = {
                        str(c.get("wa_id") or ""): ((c.get("profile") or {}).get("name"))
                        for c in contacts if isinstance(c, dict)
                    }

                    for message in value.get("messages") or []:
                        wa_id = str(message.get("from") or "")
                        if not wa_id or not phone_number_id:
                            continue
                        message_id = str(message.get("id") or "") or None
                        message_type = str(message.get("type") or "unknown")
                        body = None
                        if message_type == "text":
                            body = ((message.get("text") or {}).get("body"))
                        elif message_type == "button":
                            body = ((message.get("button") or {}).get("text"))
                        elif message_type == "interactive":
                            interactive = message.get("interactive") or {}
                            body = json.dumps(interactive, ensure_ascii=False)
                        else:
                            body = f"[{message_type}]"
                        sent_at = _ts(message.get("timestamp"))
                        contact_id = _upsert_contact(db, wa_id, profile_by_wa.get(wa_id))
                        conversation_id = _upsert_conversation(
                            db, contact_id, phone_number_id, sent_at, 1
                        )
                        db.execute(text("""
                            INSERT INTO whatsapp_messages(
                                conversation_id, whatsapp_message_id, direction,
                                message_type, body, status, sent_at, raw_payload
                            ) VALUES (
                                :conversation_id, :message_id, 'inbound',
                                :message_type, :body, 'received', :sent_at,
                                CAST(:raw_payload AS JSONB)
                            )
                            ON CONFLICT (whatsapp_message_id) DO NOTHING
                        """), {
                            "conversation_id": conversation_id,
                            "message_id": message_id,
                            "message_type": message_type,
                            "body": body,
                            "sent_at": sent_at,
                            "raw_payload": json.dumps(message, ensure_ascii=False),
                        })

                    for status in value.get("statuses") or []:
                        message_id = str(status.get("id") or "")
                        status_name = str(status.get("status") or "unknown")
                        if message_id:
                            db.execute(text("""
                                UPDATE whatsapp_messages
                                SET status = :status
                                WHERE whatsapp_message_id = :message_id
                            """), {"status": status_name, "message_id": message_id})

            db.execute(text("""
                UPDATE whatsapp_webhook_events
                SET processing_status = 'processed', processed_at = now(), error_message = NULL
                WHERE id = :id
            """), {"id": row["id"]})
        except Exception as exc:
            db.execute(text("""
                UPDATE whatsapp_webhook_events
                SET processing_status = 'error', error_message = :error
                WHERE id = :id
            """), {"id": row["id"], "error": str(exc)[:1000]})

    db.commit()


def _meta_send_text(phone_number_id: str, to: str, body: str) -> dict[str, Any]:
    token = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()
    version = os.getenv("WHATSAPP_GRAPH_API_VERSION", "v25.0").strip()
    if not token:
        raise HTTPException(status_code=503, detail="WHATSAPP_ACCESS_TOKEN no configurado")
    url = f"https://graph.facebook.com/{version}/{phone_number_id}/messages"
    payload = json.dumps({
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": body},
    }).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"Meta rechazó el mensaje: {detail}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"No se pudo conectar con Meta: {exc}") from exc


@router.get("/conversations")
def list_conversations(
    q: str | None = Query(default=None),
    status: str | None = Query(default="open"),
    db: Session = Depends(get_db),
):
    _sync_webhook_events(db)
    where = ["1=1"]
    params: dict[str, Any] = {}
    if status and status != "all":
        where.append("c.status = :status")
        params["status"] = status
    if q:
        where.append("(ct.profile_name ILIKE :q OR ct.wa_id ILIKE :q OR COALESCE(m.body, '') ILIKE :q)")
        params["q"] = f"%{q}%"
    rows = db.execute(text(f"""
        SELECT
            c.id,
            ct.wa_id,
            ct.profile_name,
            c.phone_number_id,
            c.brand_code,
            c.executive_name,
            c.status,
            c.unread_count,
            c.last_message_at,
            m.body AS last_message,
            m.direction AS last_direction,
            m.status AS last_message_status
        FROM whatsapp_conversations c
        JOIN whatsapp_contacts ct ON ct.id = c.contact_id
        LEFT JOIN LATERAL (
            SELECT body, direction, status
            FROM whatsapp_messages
            WHERE conversation_id = c.id
            ORDER BY sent_at DESC, id DESC
            LIMIT 1
        ) m ON TRUE
        WHERE {' AND '.join(where)}
        ORDER BY c.last_message_at DESC NULLS LAST, c.id DESC
        LIMIT 200
    """), params).mappings().all()
    return {"ok": True, "items": [dict(r) for r in rows]}


@router.get("/conversations/{conversation_id}/messages")
def conversation_messages(conversation_id: int, db: Session = Depends(get_db)):
    _sync_webhook_events(db)
    conversation = db.execute(text("""
        SELECT c.*, ct.wa_id, ct.profile_name
        FROM whatsapp_conversations c
        JOIN whatsapp_contacts ct ON ct.id = c.contact_id
        WHERE c.id = :id
    """), {"id": conversation_id}).mappings().first()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversación no encontrada")
    db.execute(text("""
        UPDATE whatsapp_conversations
        SET unread_count = 0, updated_at = now()
        WHERE id = :id
    """), {"id": conversation_id})
    rows = db.execute(text("""
        SELECT id, whatsapp_message_id, direction, message_type, body, status, sent_at
        FROM whatsapp_messages
        WHERE conversation_id = :id
        ORDER BY sent_at, id
        LIMIT 1000
    """), {"id": conversation_id}).mappings().all()
    db.commit()
    return {
        "ok": True,
        "conversation": dict(conversation),
        "items": [dict(r) for r in rows],
    }


@router.post("/conversations/{conversation_id}/send")
def send_message(
    conversation_id: int,
    body: SendMessageBody,
    db: Session = Depends(get_db),
):
    _ensure_tables(db)
    conversation = db.execute(text("""
        SELECT c.id, c.phone_number_id, ct.wa_id
        FROM whatsapp_conversations c
        JOIN whatsapp_contacts ct ON ct.id = c.contact_id
        WHERE c.id = :id
    """), {"id": conversation_id}).mappings().first()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversación no encontrada")
    result = _meta_send_text(
        str(conversation["phone_number_id"]),
        str(conversation["wa_id"]),
        body.text.strip(),
    )
    message_id = None
    messages = result.get("messages") or []
    if messages and isinstance(messages[0], dict):
        message_id = messages[0].get("id")
    row = db.execute(text("""
        INSERT INTO whatsapp_messages(
            conversation_id, whatsapp_message_id, direction,
            message_type, body, status, sent_at, raw_payload
        ) VALUES (
            :conversation_id, :message_id, 'outbound',
            'text', :body, 'accepted', now(), CAST(:raw_payload AS JSONB)
        )
        RETURNING id, sent_at
    """), {
        "conversation_id": conversation_id,
        "message_id": message_id,
        "body": body.text.strip(),
        "raw_payload": json.dumps(result, ensure_ascii=False),
    }).mappings().one()
    db.execute(text("""
        UPDATE whatsapp_conversations
        SET last_message_at = :sent_at, updated_at = now()
        WHERE id = :id
    """), {"id": conversation_id, "sent_at": row["sent_at"]})
    db.commit()
    return {"ok": True, "message_id": message_id, "id": row["id"], "status": "accepted"}


@router.post("/conversations/{conversation_id}/assign")
def assign_conversation(
    conversation_id: int,
    body: AssignConversationBody,
    db: Session = Depends(get_db),
):
    brand_code = body.brand_code.strip().upper()
    if brand_code not in BRANDS:
        raise HTTPException(status_code=400, detail="Marca inválida")
    brand_name, executive_name = BRANDS[brand_code]
    updated = db.execute(text("""
        UPDATE whatsapp_conversations
        SET brand_code = :brand_code,
            executive_name = :executive_name,
            updated_at = now()
        WHERE id = :id
        RETURNING id
    """), {
        "id": conversation_id,
        "brand_code": brand_code,
        "executive_name": executive_name,
    }).scalar()
    if not updated:
        raise HTTPException(status_code=404, detail="Conversación no encontrada")
    db.commit()
    return {
        "ok": True,
        "brand_code": brand_code,
        "brand_name": brand_name,
        "executive_name": executive_name,
    }


@router.post("/conversations/{conversation_id}/close")
def close_conversation(conversation_id: int, db: Session = Depends(get_db)):
    updated = db.execute(text("""
        UPDATE whatsapp_conversations
        SET status = 'closed', unread_count = 0, updated_at = now()
        WHERE id = :id
        RETURNING id
    """), {"id": conversation_id}).scalar()
    if not updated:
        raise HTTPException(status_code=404, detail="Conversación no encontrada")
    db.commit()
    return {"ok": True}


@router.post("/conversations/{conversation_id}/reopen")
def reopen_conversation(conversation_id: int, db: Session = Depends(get_db)):
    updated = db.execute(text("""
        UPDATE whatsapp_conversations
        SET status = 'open', updated_at = now()
        WHERE id = :id
        RETURNING id
    """), {"id": conversation_id}).scalar()
    if not updated:
        raise HTTPException(status_code=404, detail="Conversación no encontrada")
    db.commit()
    return {"ok": True}
