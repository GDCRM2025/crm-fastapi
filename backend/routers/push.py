from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from backend.routers.auth import get_current_user
from backend.core.database import engine
from backend.core.webpush import (
    save_subscription,
    deactivate_subscription,
    send_webpush_to_users,
    get_vapid_public_key,
)


router = APIRouter(prefix="/push", tags=["push"])


class PushKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscriptionIn(BaseModel):
    endpoint: str
    keys: PushKeys
    userAgent: str | None = None


class PushTestIn(BaseModel):
    title: str | None = None
    body: str | None = None
    url: str | None = None


@router.get("/vapid_public_key")
def vapid_public_key():
    # VAPID public key is safe to expose (it's public) and it avoids false errors on Safari
    # when the session token is missing/expired.
    k = get_vapid_public_key()
    if not k:
        raise HTTPException(status_code=503, detail="VAPID not configured")
    return {"publicKey": k}


@router.post("/subscribe")
def subscribe(data: PushSubscriptionIn, me=Depends(get_current_user)):
    uid = me.get("id") or me.get("id_usuario") or me.get("user_id")
    try:
        uid_int = int(uid)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid user")

    save_subscription(
        user_id=uid_int,
        endpoint=data.endpoint,
        p256dh=data.keys.p256dh,
        auth=data.keys.auth,
        user_agent=data.userAgent,
    )
    try:
        with engine.begin() as cn:
            cn.execute(text("ALTER TABLE public.usuarios ADD COLUMN IF NOT EXISTS push_ok BOOLEAN NOT NULL DEFAULT FALSE"))
            cn.execute(text("UPDATE public.usuarios SET push_ok=TRUE WHERE id_usuario=:id"), {"id": uid_int})
    except Exception:
        pass
    return {"ok": True}


@router.post("/unsubscribe")
def unsubscribe(data: PushSubscriptionIn, me=Depends(get_current_user)):
    uid = me.get("id") or me.get("id_usuario") or me.get("user_id")
    try:
        uid_int = int(uid)
    except Exception:
        uid_int = None
    deactivate_subscription(endpoint=data.endpoint, user_id=uid_int)
    return {"ok": True}


@router.post("/test")
def test_push(data: PushTestIn, me=Depends(get_current_user)):
    uid = me.get("id") or me.get("id_usuario") or me.get("user_id")
    try:
        uid_int = int(uid)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid user")
    res = send_webpush_to_users(
        user_ids=[uid_int],
        title=(data.title or "Prueba GD").strip(),
        body=(data.body or "Notificación de prueba").strip(),
        url=(data.url or "/crm/web/views/staff.html").strip(),
        tag="gd-test",
    )
    return {"ok": True, "result": res}
