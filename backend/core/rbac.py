from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import text


ACCESS_RANK = {"none": 0, "read": 1, "full": 2}
EXCLUDED_ROLE_PARTS = ("OPERADOR", "CONDUCTOR", "CHOFER", "CHOP", "PATIO")


def role_key(user_or_role: dict[str, Any] | str | None) -> str:
    if isinstance(user_or_role, dict):
        raw = user_or_role.get("role") or user_or_role.get("rol") or ""
    else:
        raw = user_or_role or ""
    return str(raw).strip().upper().replace("_", " ")


def is_admin(user_or_role: dict[str, Any] | str | None) -> bool:
    role = role_key(user_or_role)
    compact = role.replace(" ", "")
    return compact in ("ADMIN", "SUPERADMIN") or "ADMIN" in compact


def is_excluded_role(user_or_role: dict[str, Any] | str | None) -> bool:
    role = role_key(user_or_role)
    return any(part in role for part in EXCLUDED_ROLE_PARTS)


def user_id(user: dict[str, Any] | None) -> int | None:
    raw = (user or {}).get("id_usuario") or (user or {}).get("id") or (user or {}).get("user_id")
    try:
        return int(raw) if str(raw or "").isdigit() else None
    except Exception:
        return None


def username(user: dict[str, Any] | None) -> str:
    u = user or {}
    return str(u.get("email") or u.get("username") or u.get("name") or u.get("sub") or "").strip()


def norm_access(value: Any) -> str:
    v = str(value or "none").strip().lower()
    return v if v in ACCESS_RANK else "none"


def access_allows(actual: str, required: str = "read") -> bool:
    return ACCESS_RANK.get(norm_access(actual), 0) >= ACCESS_RANK.get(norm_access(required), 1)


def ensure_permission_tables(conn) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.user_menu_permissions (
              id_usuario INTEGER NOT NULL,
              menu_id TEXT NOT NULL,
              access TEXT NOT NULL DEFAULT 'none',
              updated_by INTEGER,
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY (id_usuario, menu_id)
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.role_menu_permissions (
              rol TEXT NOT NULL,
              menu_id TEXT NOT NULL,
              access TEXT NOT NULL DEFAULT 'none',
              updated_by INTEGER,
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY (rol, menu_id)
            )
            """
        )
    )


def get_menu_access(conn, user: dict[str, Any], menu_id: str) -> str:
    """
    Devuelve none/read/full para un menu_id. Admin/SuperAdmin siempre full.
    Primero mira permiso por usuario; si no existe, mira permiso por rol.
    """
    mid = str(menu_id or "").strip()
    if not mid:
        return "none"
    if is_admin(user):
        return "full"
    if is_excluded_role(user):
        return "none"
    uid = user_id(user)
    role = role_key(user)
    ensure_permission_tables(conn)
    if uid:
        row = conn.execute(
            text(
                """
                SELECT access
                FROM public.user_menu_permissions
                WHERE id_usuario=:uid AND menu_id=:mid
                LIMIT 1
                """
            ),
            {"uid": int(uid), "mid": mid},
        ).scalar()
        if row:
            return norm_access(row)
    if role:
        row = conn.execute(
            text(
                """
                SELECT access
                FROM public.role_menu_permissions
                WHERE upper(rol)=upper(:role) AND menu_id=:mid
                LIMIT 1
                """
            ),
            {"role": role, "mid": mid},
        ).scalar()
        if row:
            return norm_access(row)
    return "none"


def require_menu_access(conn, user: dict[str, Any], menu_id: str, required: str = "read") -> str:
    actual = get_menu_access(conn, user, menu_id)
    if not access_allows(actual, required):
        raise HTTPException(status_code=403, detail="Sin permiso para este modulo.")
    return actual
