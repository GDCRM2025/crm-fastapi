from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from backend.core.database import engine
from backend.routers.auth import get_current_user

router = APIRouter(tags=["me"])

def _normalize_phone_cl(raw: str) -> str:
    """
    Normaliza teléfono a formato E.164 para Chile.
    Regla UX: usuario ingresa solo 9 dígitos (sin +56). Aceptamos también +56 o 56.
    """
    s = (raw or "").strip()
    if not s:
        return ""
    # Mantener posible '+' solo al inicio
    s = s.replace(" ", "").replace("-", "").replace("(", "").replace(")", "").replace(".", "")
    if s.startswith("+"):
        digits = "".join(ch for ch in s[1:] if ch.isdigit())
        return "+" + digits
    digits = "".join(ch for ch in s if ch.isdigit())
    if digits.startswith("56") and len(digits) == 11:
        digits = digits[2:]
    if len(digits) != 9:
        raise HTTPException(status_code=400, detail="Teléfono inválido. Usa 9 dígitos (sin +56).")
    return "+56" + digits


def _ensure_push_ok_col(conn) -> None:
    try:
        conn.execute(text("ALTER TABLE public.usuarios ADD COLUMN IF NOT EXISTS push_ok BOOLEAN NOT NULL DEFAULT FALSE"))
    except Exception:
        try:
            conn.execute(text("ALTER TABLE public.users ADD COLUMN IF NOT EXISTS push_ok BOOLEAN NOT NULL DEFAULT FALSE"))
        except Exception:
            pass


def _enrich_user(user: dict) -> dict:
    out = dict(user or {})
    uid = out.get("id")
    try:
        uid_int = int(uid) if str(uid).isdigit() else None
    except Exception:
        uid_int = None
    if not uid_int:
        return out
    try:
        with engine.begin() as cn:
            _ensure_push_ok_col(cn)
            row = cn.execute(
                text(
                    """
                    SELECT telefono, COALESCE(push_ok,FALSE) AS push_ok
                    FROM public.usuarios
                    WHERE id_usuario=:id
                    """
                ),
                {"id": uid_int},
            ).mappings().first()
            if row:
                out["telefono"] = row.get("telefono")
                out["push_ok"] = bool(row.get("push_ok"))
                out["needs_phone"] = not bool((row.get("telefono") or "").strip())
                out["needs_push_ok"] = not bool(row.get("push_ok"))
    except Exception:
        pass
    return out


@router.get("/me")
def me(user=Depends(get_current_user)):
    return _enrich_user(user)

# Aliases legacy/compat (algunos frontends llaman /auth/me)
@router.get("/auth/me")
@router.get("/web/auth/me")
def me_alias(user=Depends(get_current_user)):
    return _enrich_user(user)


class MePatchIn(BaseModel):
    telefono: str | None = None
    push_ok: bool | None = None


@router.patch("/me")
def patch_me(data: MePatchIn, me=Depends(get_current_user)):
    uid = me.get("id")
    try:
        uid_int = int(uid) if str(uid).isdigit() else None
    except Exception:
        uid_int = None
    if not uid_int:
        raise HTTPException(status_code=401, detail="Invalid user")

    telefono_raw = (data.telefono or "").strip()
    telefono_norm = ""
    if data.telefono is not None:
        telefono_norm = _normalize_phone_cl(telefono_raw) if telefono_raw else ""
    push_ok = data.push_ok
    with engine.begin() as cn:
        _ensure_push_ok_col(cn)
        sets = []
        params = {"id": uid_int}
        if data.telefono is not None:
            sets.append("telefono=:tel")
            params["tel"] = telefono_norm or None
        if push_ok is True:
            sets.append("push_ok=TRUE")
        if not sets:
            return _enrich_user(me)
        cn.execute(text("UPDATE public.usuarios SET " + ", ".join(sets) + " WHERE id_usuario=:id"), params)
    return _enrich_user(me)
