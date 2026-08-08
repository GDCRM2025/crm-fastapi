from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import text

from backend.core.activity_log import log_activity
from backend.core.database import engine
from backend.core.feature_flags import (
    FEATURES,
    ensure_feature_table,
    invalidate_feature_cache,
    public_feature_catalog,
)
from backend.routers.auth import get_current_user


router = APIRouter(tags=["features"])


def _role(user: dict) -> str:
    return str(user.get("role") or user.get("rol") or "").strip().upper()


def _is_admin(user: dict) -> bool:
    return "ADMIN" in _role(user)


def _username(user: dict) -> str:
    return str(
        user.get("username")
        or user.get("email")
        or user.get("name")
        or user.get("nombre")
        or user.get("id")
        or "admin"
    )[:200]


@router.get("/features")
def list_features(user: dict = Depends(get_current_user)):
    return {"ok": True, "items": public_feature_catalog()}


@router.get("/settings/features")
def settings_list_features(user: dict = Depends(get_current_user)):
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Solo administradores")
    return {"ok": True, "items": public_feature_catalog()}


@router.put("/settings/features/{feature_key}")
def settings_update_feature(
    feature_key: str,
    payload: dict = Body(...),
    user: dict = Depends(get_current_user),
):
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Solo administradores")
    key = str(feature_key or "").strip()
    if key not in FEATURES:
        raise HTTPException(status_code=404, detail="Función no reconocida")
    if "enabled" not in payload or not isinstance(payload.get("enabled"), bool):
        raise HTTPException(status_code=400, detail="enabled debe ser true o false")

    enabled = bool(payload["enabled"])
    reason = str(payload.get("reason") or "").strip()[:500]
    who = _username(user)
    with engine.begin() as cn:
        ensure_feature_table(cn)
        cn.execute(
            text(
                """
                INSERT INTO public.system_features(feature_key,enabled,updated_at,updated_by,reason)
                VALUES(:key,:enabled,now(),:who,NULLIF(:reason,''))
                ON CONFLICT(feature_key) DO UPDATE SET
                  enabled=EXCLUDED.enabled,
                  updated_at=now(),
                  updated_by=EXCLUDED.updated_by,
                  reason=EXCLUDED.reason
                """
            ),
            {"key": key, "enabled": enabled, "who": who, "reason": reason},
        )
        try:
            log_activity(
                cn,
                username=who,
                user_id=user.get("id") if str(user.get("id") or "").isdigit() else None,
                role=_role(user),
                action="FEATURE_TOGGLED",
                entity_type="system_feature",
                entity_id=None,
                meta={"feature_key": key, "enabled": enabled, "reason": reason},
            )
        except Exception:
            pass
    invalidate_feature_cache()
    return {
        "ok": True,
        "feature": {
            "key": key,
            "enabled": enabled,
            "label": FEATURES[key]["label"],
        },
    }
