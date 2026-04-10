import json

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
            # Marcas asignadas (para roles acotados por marcas). No dependemos del token.
            try:
                has_um = bool(cn.execute(text("SELECT to_regclass('public.usuarios_marcas') IS NOT NULL")).scalar())
            except Exception:
                has_um = False
            if has_um:
                try:
                    mids = cn.execute(
                        text("SELECT id_marca FROM public.usuarios_marcas WHERE id_usuario=:id ORDER BY id_marca"),
                        {"id": uid_int},
                    ).fetchall()
                    out["marcas"] = [int(r[0]) for r in mids if r and str(r[0]).isdigit()]
                except Exception:
                    out["marcas"] = out.get("marcas") or []
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
                tel_db = (row.get("telefono") or "").strip()
                tel_norm = ""
                tel_ok = False
                if tel_db:
                    try:
                        tel_norm = _normalize_phone_cl(tel_db)
                        tel_ok = bool(tel_norm)
                    except Exception:
                        tel_ok = False
                        tel_norm = ""

                # Si el teléfono existe pero viene con espacios/formato raro, lo normalizamos en BD
                # (best-effort; nunca debe romper /me).
                try:
                    if tel_ok and tel_norm and tel_norm != tel_db:
                        cn.execute(
                            text("UPDATE public.usuarios SET telefono=:t WHERE id_usuario=:id"),
                            {"t": tel_norm, "id": uid_int},
                        )
                        tel_db = tel_norm
                except Exception:
                    pass

                out["telefono"] = tel_db or None
                out["push_ok"] = bool(row.get("push_ok"))
                out["needs_phone"] = not bool(tel_ok)
                out["needs_push_ok"] = not bool(row.get("push_ok"))

            # Aviso auto-decline (global del día). Best-effort, nunca debe romper /me.
            try:
                has_kv = bool(cn.execute(text("SELECT to_regclass('public.system_kv') IS NOT NULL")).scalar())
            except Exception:
                has_kv = False
            if has_kv:
                try:
                    v = cn.execute(
                        text("SELECT value FROM public.system_kv WHERE key='auto_decline_last_payload' LIMIT 1")
                    ).scalar()
                    if v:
                        try:
                            out["auto_decline_last"] = json.loads(str(v))
                        except Exception:
                            out["auto_decline_last"] = None
                except Exception:
                    pass
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
            # Unicidad: un teléfono debe pertenecer a un solo usuario.
            if telefono_norm:
                other = cn.execute(
                    text(
                        """
                        SELECT id_usuario
                        FROM public.usuarios
                        WHERE telefono=:t AND id_usuario<>:id
                        LIMIT 1
                        """
                    ),
                    {"t": telefono_norm, "id": uid_int},
                ).scalar()
                if other is not None:
                    raise HTTPException(
                        status_code=409,
                        detail="Teléfono ya registrado por otro usuario. Inicia sesión con ese usuario para activar notificaciones.",
                    )
            sets.append("telefono=:tel")
            params["tel"] = telefono_norm or None
        if push_ok is True:
            sets.append("push_ok=TRUE")
        if not sets:
            return _enrich_user(me)
        cn.execute(text("UPDATE public.usuarios SET " + ", ".join(sets) + " WHERE id_usuario=:id"), params)
    return _enrich_user(me)
