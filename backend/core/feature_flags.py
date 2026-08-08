from __future__ import annotations

import threading
import time
from typing import Any

from sqlalchemy import text

from backend.core.database import engine


FEATURES: dict[str, dict[str, Any]] = {
    "whatsapp": {
        "label": "WhatsApp Greenie",
        "description": "Bandeja, envío y gestión comercial de WhatsApp. El webhook sigue recibiendo mensajes para no perderlos.",
        "default_enabled": True,
        "menu_ids": ["tool_wapp"],
    },
    "email": {
        "label": "Recepción y envío de correos",
        "description": "Sincronización, bandeja, respuestas y creación de leads desde correo.",
        "default_enabled": True,
        "menu_ids": ["tool_gmail"],
    },
    "surveys": {
        "label": "Encuestas de satisfacción",
        "description": "Generación y gestión interna de encuestas. Los enlaces ya enviados continúan respondiendo.",
        "default_enabled": True,
        "menu_ids": ["encuestas_eventos", "rep_surveys"],
    },
    "attendance": {
        "label": "Marcación y control de asistencia",
        "description": "Marcación SGJO, dispositivos, QR y consultas de marcaciones.",
        "default_enabled": True,
        "menu_ids": ["rrhh_mark_plan", "rrhh_turnos_visor"],
    },
    "internal_chat": {
        "label": "Chat interno",
        "description": "Mensajería interna entre usuarios del CRM.",
        "default_enabled": False,
        "menu_ids": ["tool_chat"],
    },
    "instagram": {
        "label": "Instagram GIA",
        "description": "Bandeja e integración de mensajes de Instagram.",
        "default_enabled": True,
        "menu_ids": ["tool_ig"],
    },
}

_cache_lock = threading.Lock()
_cache: dict[str, bool] = {}
_cache_until = 0.0
_CACHE_SECONDS = 5.0


def ensure_feature_table(connection=None) -> None:
    sql = text(
        """
        CREATE TABLE IF NOT EXISTS public.system_features (
          feature_key TEXT PRIMARY KEY,
          enabled BOOLEAN NOT NULL DEFAULT TRUE,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_by TEXT,
          reason TEXT
        )
        """
    )
    if connection is not None:
        connection.execute(sql)
        return
    with engine.begin() as cn:
        cn.execute(sql)


def invalidate_feature_cache() -> None:
    global _cache_until
    with _cache_lock:
        _cache_until = 0.0


def get_feature_flags(*, force: bool = False) -> dict[str, bool]:
    global _cache, _cache_until
    now = time.monotonic()
    with _cache_lock:
        if not force and _cache and now < _cache_until:
            return dict(_cache)

    flags = {
        key: bool(meta.get("default_enabled", True))
        for key, meta in FEATURES.items()
    }
    try:
        with engine.begin() as cn:
            ensure_feature_table(cn)
            rows = cn.execute(
                text("SELECT feature_key, enabled FROM public.system_features")
            ).mappings().all()
        for row in rows:
            key = str(row.get("feature_key") or "")
            if key in flags:
                flags[key] = bool(row.get("enabled"))
    except Exception:
        # Fail-open: una caída transitoria de BD no debe apagar el CRM completo.
        pass

    with _cache_lock:
        _cache = dict(flags)
        _cache_until = time.monotonic() + _CACHE_SECONDS
    return flags


def feature_enabled(feature_key: str) -> bool:
    key = str(feature_key or "").strip()
    meta = FEATURES.get(key) or {}
    return bool(get_feature_flags().get(key, meta.get("default_enabled", True)))


def feature_for_path(path: str) -> str | None:
    p = str(path or "/")
    # Las rutas públicas de encuestas ya enviadas deben seguir funcionando.
    if p.startswith("/surveys"):
        return "surveys"
    if p.startswith("/gia/whatsapp"):
        return "whatsapp"
    if p.startswith("/gia/email"):
        return "email"
    if p.startswith("/chat"):
        return "internal_chat"
    if p.startswith("/sgjo") or p.startswith("/rrhh/sgjo") or p.startswith("/rrhh/marcaciones"):
        return "attendance"
    return None


def public_feature_catalog() -> list[dict[str, Any]]:
    flags = get_feature_flags()
    return [
        {
            "key": key,
            "label": str(meta.get("label") or key),
            "description": str(meta.get("description") or ""),
            "enabled": bool(flags.get(key, meta.get("default_enabled", True))),
            "menu_ids": list(meta.get("menu_ids") or []),
        }
        for key, meta in FEATURES.items()
    ]
