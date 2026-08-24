from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.omnichannel_access import (
    CHANNELS,
    can_access_brand,
    can_operate_omnichannel,
    visible_brands,
)
from backend.routers.auth import get_current_user

router = APIRouter(tags=["Greenie Meta Omnichannel"])
log = logging.getLogger("crm")

META_CHANNELS = {"INSTAGRAM", "MESSENGER"}


class MetaReplyBody(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or default).strip()


def _json_env(name: str) -> dict[str, Any]:
    raw = _env(name)
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except Exception:
        log.warning("[GREENIE][META] %s contiene JSON inválido", name)
        return {}
    return value if isinstance(value, dict) else {}


def _channel(value: str) -> str:
    channel = str(value or "").strip().upper()
    if channel not in META_CHANNELS:
        raise HTTPException(status_code=400, detail="Canal Meta no compatible")
    return channel


def _verify_token(channel: str) -> str:
    if channel == "INSTAGRAM":
        return _env("INSTAGRAM_WEBHOOK_VERIFY_TOKEN") or _env("IG_WEBHOOK_VERIFY_TOKEN")
    return _env("MESSENGER_WEBHOOK_VERIFY_TOKEN")


def _app_secret(channel: str) -> str:
    if channel == "INSTAGRAM":
        return _env("INSTAGRAM_APP_SECRET") or _env("META_APP_SECRET")
    return _env("MESSENGER_APP_SECRET") or _env("META_APP_SECRET")


def verify_signature(channel: str, raw_body: bytes, signature_header: str | None) -> bool:
    secret = _app_secret(channel)
    if not secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    provided = signature_header.split("=", 1)[1].strip()
    return hmac.compare_digest(expected, provided)


def _account_map(channel: str) -> dict[str, Any]:
    if channel == "INSTAGRAM":
        return _json_env("INSTAGRAM_ACCOUNT_BRANDS_JSON")
    return _json_env("MESSENGER_PAGE_BRANDS_JSON")


def _mapping_info(channel: str, external_account_id: str) -> dict[str, Any]:
    raw = _account_map(channel).get(str(external_account_id))
    if isinstance(raw, str):
        return {"brand_code": raw.strip().upper()}
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def _brand_for_account(channel: str, external_account_id: str) -> str | None:
    value = _mapping_info(channel, external_account_id).get("brand_code")
    if value is None:
        return None
    normalized = str(value).strip().upper().replace(" ", "_")
    return normalized or None


def _token_for(channel: str, brand_code: str | None) -> str:
    brand_key = str(brand_code or "").strip().upper().replace(" ", "_")
    if channel == "INSTAGRAM":
        if brand_key:
            token = _env(f"INSTAGRAM_ACCESS_TOKEN_{brand_key}")
            if token:
                return token
        return _env("INSTAGRAM_ACCESS_TOKEN")
    if brand_key:
        token = _env(f"MESSENGER_PAGE_ACCESS_TOKEN_{brand_key}")
        if token:
            return token
    return _env("MESSENGER_PAGE_ACCESS_TOKEN")


def _event_key(channel: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256((channel + ":" + canonical).encode("utf-8")).hexdigest()


def _require_access(user: dict = Depends(get_current_user)) -> dict:
    if not can_operate_omnichannel(user):
        raise HTTPException(status_code=403, detail="Sin permisos para Greenie")
    return user


def _conversation(db: Session, channel: str, conversation_id: int) -> dict[str, Any]:
    row = db.execute(
        text("""
            SELECT id, channel, external_account_id, peer_id, brand_code,
                   contact_name, status, unread_count, last_message_at
            FROM public.wi_meta_conversations
            WHERE id=:id AND channel=:channel
            LIMIT 1
        """),
        {"id": int(conversation_id), "channel": channel},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Conversación no encontrada")
    return dict(row)


def _check_brand(user: dict, brand_code: str | None) -> None:
    if not brand_code:
        # No adivinamos una marca cuando Meta no trae una cuenta mapeada.
        raise HTTPException(status_code=409, detail="Cuenta Meta sin marca configurada")
    if not can_access_brand(user, brand_code):
        raise HTTPException(status_code=403, detail="Sin acceso a la marca de esta conversación")


def _extract_messaging(payload: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        account_id = str(entry.get("id") or "").strip()
        for event in entry.get("messaging") or []:
            if isinstance(event, dict):
                out.append({"account_id": account_id, "event": event})
    return out


def _normalize_message(channel: str, account_id: str, event: dict[str, Any]) -> dict[str, Any] | None:
    sender_id = str((event.get("sender") or {}).get("id") or "").strip()
    recipient_id = str((event.get("recipient") or {}).get("id") or "").strip()
    message = event.get("message") if isinstance(event.get("message"), dict) else None
    postback = event.get("postback") if isinstance(event.get("postback"), dict) else None
    if not sender_id and not recipient_id:
        return None

    outbound = sender_id == account_id and bool(recipient_id)
    peer_id = recipient_id if outbound else sender_id
    if not peer_id:
        return None

    body = ""
    external_message_id = None
    kind = "event"
    if message is not None:
        external_message_id = str(message.get("mid") or "").strip() or None
        body = str(message.get("text") or "")
        kind = "message"
    elif postback is not None:
        external_message_id = str(postback.get("mid") or "").strip() or None
        body = str(postback.get("title") or postback.get("payload") or "")
        kind = "postback"
    elif event.get("read") is not None:
        kind = "read"
    elif event.get("delivery") is not None:
        kind = "delivery"

    timestamp_ms = event.get("timestamp")
    try:
        sent_at = datetime.fromtimestamp(int(timestamp_ms) / 1000, tz=timezone.utc) if timestamp_ms else datetime.now(timezone.utc)
    except Exception:
        sent_at = datetime.now(timezone.utc)

    return {
        "channel": channel,
        "account_id": account_id,
        "peer_id": peer_id,
        "direction": "OUT" if outbound else "IN",
        "kind": kind,
        "body": body,
        "external_message_id": external_message_id,
        "sent_at": sent_at,
        "raw_event": event,
    }


def _store_payload(db: Session, channel: str, payload: dict[str, Any]) -> None:
    event_key = _event_key(channel, payload)
    inserted = db.execute(
        text("""
            INSERT INTO public.wi_meta_events(event_key, channel, object_name, payload)
            VALUES (:event_key, :channel, :object_name, CAST(:payload AS JSONB))
            ON CONFLICT (event_key) DO NOTHING
            RETURNING id
        """),
        {
            "event_key": event_key,
            "channel": channel,
            "object_name": str(payload.get("object") or ""),
            "payload": json.dumps(payload, ensure_ascii=False),
        },
    ).scalar()
    # Meta reintenta webhooks. Si el envelope ya fue persistido no volvemos a
    # incrementar unread_count ni duplicamos efectos secundarios.
    if inserted is None:
        db.commit()
        return

    for wrapped in _extract_messaging(payload):
        account_id = wrapped["account_id"]
        normalized = _normalize_message(channel, account_id, wrapped["event"])
        if not normalized:
            continue
        brand_code = _brand_for_account(channel, account_id)
        conversation_id = db.execute(
            text("""
                INSERT INTO public.wi_meta_conversations(
                    channel, external_account_id, peer_id, brand_code,
                    unread_count, last_message_at, last_message_preview, last_direction
                ) VALUES (
                    :channel, :account_id, :peer_id, :brand_code,
                    :unread_delta, :sent_at, :preview, :direction
                )
                ON CONFLICT(channel, external_account_id, peer_id) DO UPDATE SET
                    brand_code=COALESCE(EXCLUDED.brand_code, wi_meta_conversations.brand_code),
                    unread_count=CASE
                        WHEN EXCLUDED.last_direction='IN' THEN wi_meta_conversations.unread_count + 1
                        ELSE wi_meta_conversations.unread_count
                    END,
                    last_message_at=GREATEST(wi_meta_conversations.last_message_at, EXCLUDED.last_message_at),
                    last_message_preview=EXCLUDED.last_message_preview,
                    last_direction=EXCLUDED.last_direction,
                    updated_at=now()
                RETURNING id
            """),
            {
                "channel": channel,
                "account_id": account_id,
                "peer_id": normalized["peer_id"],
                "brand_code": brand_code,
                "unread_delta": 1 if normalized["direction"] == "IN" and normalized["kind"] in {"message", "postback"} else 0,
                "sent_at": normalized["sent_at"],
                "preview": normalized["body"][:500],
                "direction": normalized["direction"],
            },
        ).scalar_one()

        message_key = normalized["external_message_id"] or hashlib.sha256(
            json.dumps(wrapped["event"], sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        db.execute(
            text("""
                INSERT INTO public.wi_meta_messages(
                    conversation_id, external_message_id, direction, kind,
                    body, sent_at, raw_payload
                ) VALUES (
                    :conversation_id, :external_message_id, :direction, :kind,
                    :body, :sent_at, CAST(:raw_payload AS JSONB)
                )
                ON CONFLICT(conversation_id, external_message_id) DO NOTHING
            """),
            {
                "conversation_id": int(conversation_id),
                "external_message_id": message_key,
                "direction": normalized["direction"],
                "kind": normalized["kind"],
                "body": normalized["body"],
                "sent_at": normalized["sent_at"],
                "raw_payload": json.dumps(wrapped["event"], ensure_ascii=False),
            },
        )
    db.commit()


async def _verify_webhook(request: Request, channel: str):
    qp = request.query_params
    expected = _verify_token(channel)
    supplied = str(qp.get("hub.verify_token") or "")
    challenge = qp.get("hub.challenge")
    if qp.get("hub.mode") == "subscribe" and expected and challenge and hmac.compare_digest(supplied, expected):
        return PlainTextResponse(str(challenge))
    return PlainTextResponse("forbidden", status_code=403)


async def _receive_webhook(
    request: Request,
    channel: str,
    signature: str | None,
    db: Session,
):
    raw = await request.body()
    if not verify_signature(channel, raw, signature):
        return JSONResponse(status_code=403, content={"ok": False, "error": "invalid_signature"})
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return JSONResponse(status_code=400, content={"ok": False, "error": "invalid_json"})
    if not isinstance(payload, dict):
        return JSONResponse(status_code=400, content={"ok": False, "error": "invalid_payload"})
    expected_object = "instagram" if channel == "INSTAGRAM" else "page"
    if str(payload.get("object") or "").lower() != expected_object:
        return JSONResponse(status_code=400, content={"ok": False, "error": "invalid_object"})
    try:
        _store_payload(db, channel, payload)
    except Exception:
        db.rollback()
        log.exception("[GREENIE][META] fallo guardando webhook %s", channel)
        return JSONResponse(status_code=503, content={"ok": False, "error": "schema_not_ready"})
    return {"ok": True, "channel": channel}


@router.get("/meta/webhook/instagram", response_class=PlainTextResponse, include_in_schema=False)
async def instagram_verify(request: Request):
    return await _verify_webhook(request, "INSTAGRAM")


@router.post("/meta/webhook/instagram", include_in_schema=False)
async def instagram_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
    db: Session = Depends(get_db),
):
    return await _receive_webhook(request, "INSTAGRAM", x_hub_signature_256, db)


@router.get("/meta/webhook/messenger", response_class=PlainTextResponse, include_in_schema=False)
async def messenger_verify(request: Request):
    return await _verify_webhook(request, "MESSENGER")


@router.post("/meta/webhook/messenger", include_in_schema=False)
async def messenger_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
    db: Session = Depends(get_db),
):
    return await _receive_webhook(request, "MESSENGER", x_hub_signature_256, db)


@router.get("/api/omnichannel/capabilities")
def capabilities(user: dict = Depends(_require_access)):
    configured = {
        "WHATSAPP": bool(_env("WHATSAPP_ACCESS_TOKEN")),
        "INSTAGRAM": bool(_app_secret("INSTAGRAM")) and bool(_verify_token("INSTAGRAM")),
        "MESSENGER": bool(_app_secret("MESSENGER")) and bool(_verify_token("MESSENGER")),
        "EMAIL": bool(_env("GIA_EMAIL_ACCOUNTS_JSON") or _env("EMAIL_ACCOUNTS_JSON") or (_env("IMAP_HOST") and _env("SMTP_HOST"))),
    }
    return {
        "ok": True,
        "channels": [
            {
                "channel": channel,
                "configured": configured[channel],
                "mode": "coexistence" if channel == "WHATSAPP" else "imap_smtp" if channel == "EMAIL" else "native",
            }
            for channel in CHANNELS
        ],
        "brands": visible_brands(user),
        "permissions": {"operate": True, "delete_leads": False},
    }


@router.get("/api/omnichannel/brands")
def omnichannel_brands(user: dict = Depends(_require_access)):
    items = visible_brands(user)
    return {"ok": True, "total": len(items), "items": items}


@router.get("/api/omnichannel/meta/{channel}/conversations")
def meta_conversations(
    channel: str,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: dict = Depends(_require_access),
):
    normalized = _channel(channel)
    rows = db.execute(
        text("""
            SELECT id, channel, external_account_id, peer_id, brand_code,
                   contact_name, status, unread_count, last_message_at,
                   last_message_preview, last_direction
            FROM public.wi_meta_conversations
            WHERE channel=:channel
            ORDER BY last_message_at DESC NULLS LAST, id DESC
            LIMIT :limit
        """),
        {"channel": normalized, "limit": int(limit)},
    ).mappings().all()
    items = [dict(row) for row in rows if row.get("brand_code") and can_access_brand(user, row.get("brand_code"))]
    return {"ok": True, "total": len(items), "items": items}


@router.get("/api/omnichannel/meta/{channel}/{conversation_id}/messages")
def meta_messages(
    channel: str,
    conversation_id: int,
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
    user: dict = Depends(_require_access),
):
    normalized = _channel(channel)
    conversation = _conversation(db, normalized, conversation_id)
    _check_brand(user, conversation.get("brand_code"))
    rows = db.execute(
        text("""
            SELECT id, external_message_id, direction, kind, body, status, sent_at
            FROM public.wi_meta_messages
            WHERE conversation_id=:conversation_id
            ORDER BY sent_at ASC, id ASC
            LIMIT :limit
        """),
        {"conversation_id": int(conversation_id), "limit": int(limit)},
    ).mappings().all()
    return {"ok": True, "conversation": conversation, "items": [dict(row) for row in rows]}


@router.post("/api/omnichannel/meta/{channel}/{conversation_id}/read")
def meta_read_local(
    channel: str,
    conversation_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(_require_access),
):
    normalized = _channel(channel)
    conversation = _conversation(db, normalized, conversation_id)
    _check_brand(user, conversation.get("brand_code"))
    db.execute(
        text("UPDATE public.wi_meta_conversations SET unread_count=0, updated_at=now() WHERE id=:id"),
        {"id": int(conversation_id)},
    )
    db.commit()
    return {"ok": True}


def _graph_post(channel: str, account_id: str, peer_id: str, text_body: str, brand_code: str | None) -> dict[str, Any]:
    token = _token_for(channel, brand_code)
    if not token:
        raise HTTPException(status_code=503, detail=f"Token {channel} no configurado")
    version = _env("META_GRAPH_API_VERSION", "v25.0")
    host = "graph.instagram.com" if channel == "INSTAGRAM" else "graph.facebook.com"
    url = f"https://{host}/{version}/{account_id}/messages"
    payload = json.dumps({"recipient": {"id": peer_id}, "message": {"text": text_body}}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1200]
        raise HTTPException(status_code=502, detail=f"Meta rechazó el envío: {detail}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="No se pudo contactar Meta") from exc
    try:
        parsed = json.loads(raw or "{}")
    except Exception:
        parsed = {"raw": raw[:500]}
    return parsed if isinstance(parsed, dict) else {"result": parsed}


@router.post("/api/omnichannel/meta/{channel}/{conversation_id}/reply")
def meta_reply(
    channel: str,
    conversation_id: int,
    payload: MetaReplyBody = Body(...),
    db: Session = Depends(get_db),
    user: dict = Depends(_require_access),
):
    normalized = _channel(channel)
    conversation = _conversation(db, normalized, conversation_id)
    _check_brand(user, conversation.get("brand_code"))
    result = _graph_post(
        normalized,
        str(conversation["external_account_id"]),
        str(conversation["peer_id"]),
        payload.text.strip(),
        conversation.get("brand_code"),
    )
    external_id = str(result.get("message_id") or result.get("id") or "").strip()
    if external_id:
        db.execute(
            text("""
                INSERT INTO public.wi_meta_messages(
                    conversation_id, external_message_id, direction, kind, body, status, sent_at
                ) VALUES (:conversation_id, :external_message_id, 'OUT', 'message', :body, 'sent', now())
                ON CONFLICT(conversation_id, external_message_id) DO NOTHING
            """),
            {"conversation_id": int(conversation_id), "external_message_id": external_id, "body": payload.text.strip()},
        )
        db.execute(
            text("""
                UPDATE public.wi_meta_conversations
                SET last_message_at=now(), last_message_preview=:body,
                    last_direction='OUT', updated_at=now()
                WHERE id=:id
            """),
            {"id": int(conversation_id), "body": payload.text.strip()[:500]},
        )
        db.commit()
    return {"ok": True, "result": result}
