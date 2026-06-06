from __future__ import annotations

from typing import Any
from sqlalchemy import text

from backend.core.db import get_connection
from backend.core.utils import valid_email

_BLOCKED_ROLE_BY_PROCESS = {
    "AGENDA_EVENTOS": {"MICE"},
    "EVENTO_MODIFICADO": {"MICE"},
}


def _ensure_email_routes(conn) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.notify_email_routes (
              process_key TEXT PRIMARY KEY,
              enabled BOOLEAN NOT NULL DEFAULT TRUE,
              to_list TEXT,
              cc_list TEXT,
              bcc_list TEXT,
              to_user_ids INT[],
              cc_user_ids INT[],
              bcc_user_ids INT[],
              to_roles TEXT[],
              cc_roles TEXT[],
              bcc_roles TEXT[],
              note TEXT,
              updated_by INT,
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    # Backward-compatible upgrades
    try:
        conn.execute(text("ALTER TABLE public.notify_email_routes ADD COLUMN IF NOT EXISTS to_user_ids INT[]"))
        conn.execute(text("ALTER TABLE public.notify_email_routes ADD COLUMN IF NOT EXISTS cc_user_ids INT[]"))
        conn.execute(text("ALTER TABLE public.notify_email_routes ADD COLUMN IF NOT EXISTS bcc_user_ids INT[]"))
        conn.execute(text("ALTER TABLE public.notify_email_routes ADD COLUMN IF NOT EXISTS to_roles TEXT[]"))
        conn.execute(text("ALTER TABLE public.notify_email_routes ADD COLUMN IF NOT EXISTS cc_roles TEXT[]"))
        conn.execute(text("ALTER TABLE public.notify_email_routes ADD COLUMN IF NOT EXISTS bcc_roles TEXT[]"))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    try:
        conn.commit()
    except Exception:
        pass


def _split_emails(v: str | None) -> list[str]:
    raw = str(v or "")
    if not raw.strip():
        return []
    parts = []
    for chunk in raw.replace(";", ",").replace("\n", ",").split(","):
        s = chunk.strip()
        if not s:
            continue
        s = s.strip("<> ").lower()
        if valid_email(s):
            parts.append(s)
    # de-dup preserving order
    out: list[str] = []
    seen = set()
    for x in parts:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def get_email_route(process_key: str) -> dict[str, Any] | None:
    pk = str(process_key or "").strip()
    if not pk:
        return None
    with get_connection() as conn:
        _ensure_email_routes(conn)
        row = conn.execute(
            text(
                """
                SELECT process_key, enabled, to_list, cc_list, bcc_list,
                       to_user_ids, cc_user_ids, bcc_user_ids,
                       to_roles, cc_roles, bcc_roles,
                       note, updated_by, updated_at
                FROM public.notify_email_routes
                WHERE process_key=:k
                LIMIT 1
                """
            ),
            {"k": pk},
        ).mappings().first()
        return dict(row) if row else None


def _emails_from_user_ids(user_ids: list[int] | None) -> list[str]:
    ids = []
    for x in (user_ids or []):
        try:
            xi = int(x)
            if xi > 0:
                ids.append(xi)
        except Exception:
            continue
    if not ids:
        return []
    try:
        with get_connection() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT COALESCE(NULLIF(btrim(email),''), NULL) AS email
                    FROM public.usuarios
                    WHERE id_usuario = ANY(:ids)
                      AND COALESCE(is_active, TRUE) IS TRUE
                    """
                ),
                {"ids": ids},
            ).fetchall()
            out = []
            for (em,) in rows or []:
                if em and str(em).strip() and valid_email(str(em).strip()):
                    out.append(str(em).strip().lower())
            # de-dup
            seen=set()
            res=[]
            for e in out:
                if e not in seen:
                    seen.add(e); res.append(e)
            return res
    except Exception:
        return []


def _emails_from_roles(roles: list[str] | None) -> list[str]:
    rs: list[str] = []
    for r in (roles or []):
        s = str(r or "").strip()
        if s:
            rs.append(s)
    # de-dup
    seen = set()
    roles_u: list[str] = []
    for r in rs:
        ru = r.upper()
        if ru not in seen:
            seen.add(ru)
            roles_u.append(ru)
    if not roles_u:
        return []
    try:
        with get_connection() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT COALESCE(NULLIF(btrim(email),''), NULL) AS email
                    FROM public.usuarios
                    WHERE COALESCE(is_active, TRUE) IS TRUE
                      AND COALESCE(NULLIF(btrim(email),''), NULL) IS NOT NULL
                      AND upper(COALESCE(rol,'')) = ANY(:roles)
                    """
                ),
                {"roles": roles_u},
            ).fetchall()
            out = []
            for (em,) in rows or []:
                if em and str(em).strip() and valid_email(str(em).strip()):
                    out.append(str(em).strip().lower())
            # de-dup
            seen2 = set()
            res = []
            for e in out:
                if e not in seen2:
                    seen2.add(e)
                    res.append(e)
            return res
    except Exception:
        return []


def _blocked_roles(process_key: str) -> set[str]:
    return set(_BLOCKED_ROLE_BY_PROCESS.get(str(process_key or "").strip().upper(), set()))


def _filter_blocked_role_emails(process_key: str, emails: list[str]) -> list[str]:
    blocked = _blocked_roles(process_key)
    cleaned = []
    seen = set()
    for e in emails or []:
        s = str(e or "").strip().lower()
        if not s or s in seen:
            continue
        seen.add(s)
        cleaned.append(s)
    if not cleaned or not blocked:
        return cleaned
    try:
        with get_connection() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT lower(btrim(email)) AS email, upper(COALESCE(rol,'')) AS rol
                    FROM public.usuarios
                    WHERE lower(btrim(email)) = ANY(:emails)
                    """
                ),
                {"emails": cleaned},
            ).fetchall()
        blocked_emails = set()
        for em, rol in rows or []:
            rol_u = str(rol or "").upper()
            if any(b in rol_u for b in blocked):
                blocked_emails.add(str(em or "").strip().lower())
        return [e for e in cleaned if e not in blocked_emails]
    except Exception:
        return cleaned


def resolve_email_to(process_key: str, default_to: list[str]) -> list[str]:
    """
    Resuelve destinatarios (To) de un proceso usando tabla configurable.
    Si no hay route o está deshabilitada o no trae correos válidos, cae al default.
    """
    try:
        route = get_email_route(process_key)
        if not route:
            return default_to
        if bool(route.get("enabled")) is False:
            return default_to
        to_override = _split_emails(route.get("to_list"))
        to_users = _emails_from_user_ids(route.get("to_user_ids"))
        to_roles = _emails_from_roles(route.get("to_roles"))
        merged = _filter_blocked_role_emails(process_key, to_roles + to_users + to_override)
        return merged or default_to
    except Exception:
        return default_to


def resolve_email_cc(process_key: str, default_cc: list[str] | None = None) -> list[str]:
    try:
        route = get_email_route(process_key)
        if not route:
            return list(default_cc or [])
        if bool(route.get("enabled")) is False:
            return list(default_cc or [])
        cc_override = _split_emails(route.get("cc_list"))
        cc_users = _emails_from_user_ids(route.get("cc_user_ids"))
        cc_roles = _emails_from_roles(route.get("cc_roles"))
        merged = _filter_blocked_role_emails(process_key, cc_roles + cc_users + cc_override)
        return merged or list(default_cc or [])
    except Exception:
        return list(default_cc or [])


def resolve_email_bcc(process_key: str, default_bcc: list[str] | None = None) -> list[str]:
    try:
        route = get_email_route(process_key)
        if not route:
            return list(default_bcc or [])
        if bool(route.get("enabled")) is False:
            return list(default_bcc or [])
        bcc_override = _split_emails(route.get("bcc_list"))
        bcc_users = _emails_from_user_ids(route.get("bcc_user_ids"))
        bcc_roles = _emails_from_roles(route.get("bcc_roles"))
        merged = _filter_blocked_role_emails(process_key, bcc_roles + bcc_users + bcc_override)
        return merged or list(default_bcc or [])
    except Exception:
        return list(default_bcc or [])
