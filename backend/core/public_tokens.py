from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional, Tuple

from backend.core.settings import settings


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * ((4 - (len(s) % 4)) % 4)
    return base64.urlsafe_b64decode((s + pad).encode("utf-8"))


def _secret() -> bytes:
    # Reutilizamos el mismo secreto del JWT (si existe) para no exigir configuración extra.
    sec = (settings.JWT_SECRET or settings.SECRET_KEY or "").strip()
    if not sec:
        sec = "dev-secret-change-me"
    return sec.encode("utf-8")


def sign(payload: Dict[str, Any], *, ttl_seconds: int = 7 * 24 * 3600) -> str:
    """
    Token público simple (HMAC) para links compartibles.
    No requiere librerías externas (útil en hosting sin extras).
    """
    now = int(time.time())
    exp = now + max(60, int(ttl_seconds or 0))
    body = dict(payload or {})
    body["iat"] = now
    body["exp"] = exp
    msg = _b64url_encode(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    sig = hmac.new(_secret(), msg.encode("utf-8"), hashlib.sha256).digest()
    return f"{msg}.{_b64url_encode(sig)}"


def verify(token: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """
    Returns: (ok, payload, error)
    """
    try:
        raw = str(token or "").strip()
        if not raw or "." not in raw:
            return False, None, "token inválido"
        msg, sig = raw.split(".", 1)
        if not msg or not sig:
            return False, None, "token inválido"
        expected = hmac.new(_secret(), msg.encode("utf-8"), hashlib.sha256).digest()
        got = _b64url_decode(sig)
        if not hmac.compare_digest(expected, got):
            return False, None, "firma inválida"
        payload = json.loads(_b64url_decode(msg).decode("utf-8"))
        exp = int(payload.get("exp") or 0)
        if exp and int(time.time()) > exp:
            return False, None, "token expirado"
        return True, payload, ""
    except Exception:
        return False, None, "token inválido"

