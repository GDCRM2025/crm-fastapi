import os
import json
import hmac
import hashlib
import logging

from fastapi import APIRouter, Request, Header, Depends
from fastapi.responses import PlainTextResponse, JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.core.database import get_db

router = APIRouter(tags=["GIA"])
log = logging.getLogger("crm")

IG_WEBHOOK_VERIFY_TOKEN = os.getenv("IG_WEBHOOK_VERIFY_TOKEN", "CHANGE_ME")
META_APP_SECRET = os.getenv("META_APP_SECRET", "")
GREENI_DEFAULT_BRAND_ID = os.getenv("GREENI_DEFAULT_BRAND_ID", "gourmet")
GREENI_INTERNAL_KEY = os.getenv("GREENI_INTERNAL_KEY", "")


def verify_signature(raw_body, signature_header):
    if not META_APP_SECRET:
        # Nunca aceptar webhooks sin poder verificar su origen.
        log.error("[GIA][IG] META_APP_SECRET no configurado")
        return False

    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected = hmac.new(
        META_APP_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256
    ).hexdigest()

    provided = signature_header.split("=", 1)[1].strip()
    return hmac.compare_digest(expected, provided)


def ensure_table(db):
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS gia_ig_events (
            id BIGSERIAL PRIMARY KEY,
            brand_id TEXT NOT NULL,
            object_name TEXT,
            event_source TEXT,
            payload JSONB NOT NULL,
            received_at TIMESTAMP NOT NULL DEFAULT now()
        )
    """))
    db.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_gia_ig_events_brand_received
        ON gia_ig_events (brand_id, received_at DESC)
    """))
    db.commit()


def insert_event(db, brand_id, payload):
    object_name = None
    event_source = None

    if isinstance(payload, dict):
        object_name = payload.get("object")
        entries = payload.get("entry") or []
        if entries and isinstance(entries, list):
            first = entries[0] or {}
            if first.get("messaging"):
                event_source = "messaging"
            elif first.get("changes"):
                event_source = "changes"

    db.execute(
        text("""
            INSERT INTO gia_ig_events (brand_id, object_name, event_source, payload)
            VALUES (:brand_id, :object_name, :event_source, CAST(:payload AS JSONB))
        """),
        {
            "brand_id": brand_id,
            "object_name": object_name,
            "event_source": event_source,
            "payload": json.dumps(payload, ensure_ascii=False)
        }
    )
    db.commit()


@router.get("/meta/webhook/instagram", response_class=PlainTextResponse, include_in_schema=False)
async def instagram_verify(request: Request):
    qp = request.query_params
    mode = qp.get("hub.mode")
    token = qp.get("hub.verify_token")
    challenge = qp.get("hub.challenge")

    if mode == "subscribe" and token == IG_WEBHOOK_VERIFY_TOKEN and challenge:
        return challenge

    return PlainTextResponse("forbidden", status_code=403)


@router.post("/meta/webhook/instagram", include_in_schema=False)
async def instagram_webhook(
    request: Request,
    x_hub_signature_256 = Header(default=None, alias="X-Hub-Signature-256"),
    db: Session = Depends(get_db),
):
    try:
        raw = await request.body()

        if not verify_signature(raw, x_hub_signature_256):
            return JSONResponse(
                status_code=403,
                content={"ok": False, "error": "invalid_signature"}
            )

        payload = await request.json()

        ensure_table(db)
        insert_event(db, GREENI_DEFAULT_BRAND_ID, payload)

        log.info("[GIA][IG] webhook recibido object=%s", payload.get("object"))

        return {"ok": True, "router": "greeni_instagram"}

    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass

        log.exception("[GIA][IG] error guardando webhook: %s", e)
        return JSONResponse(
            status_code=500,
            content={"ok": False, "where": "instagram_webhook", "error": str(e)}
        )


@router.get("/gia/instagram/events", include_in_schema=False)
def gia_instagram_events(
    limit: int = 20,
    x_greeni_key = Header(default=None, alias="X-Greeni-Key"),
    db: Session = Depends(get_db),
):
    if not GREENI_INTERNAL_KEY or not hmac.compare_digest(
        str(x_greeni_key or ""), str(GREENI_INTERNAL_KEY)
    ):
        return JSONResponse(status_code=403, content={"ok": False, "error": "forbidden"})

    try:
        ensure_table(db)

        rows = db.execute(
            text("""
                SELECT id, brand_id, object_name, event_source, payload, received_at
                FROM gia_ig_events
                ORDER BY received_at DESC
                LIMIT :limit
            """),
            {"limit": max(1, min(int(limit), 200))}
        ).mappings().all()

        return {
            "ok": True,
            "items": [
                {
                    "id": r["id"],
                    "brand_id": r["brand_id"],
                    "object_name": r["object_name"],
                    "event_source": r["event_source"],
                    "payload": r["payload"],
                    "received_at": str(r["received_at"]),
                }
                for r in rows
            ]
        }

    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass

        return JSONResponse(
            status_code=500,
            content={"ok": False, "where": "gia_instagram_events", "error": str(e)}
        )
