from __future__ import annotations

from typing import Any

from sqlalchemy import text


PERMISSIONS = (
    "web_intelligence_view",
    "web_intelligence_configure",
    "web_intelligence_seo",
    "web_intelligence_campaigns",
    "web_intelligence_performance",
    "web_intelligence_change_request",
    "web_intelligence_approve_change",
    "web_intelligence_deploy",
    "web_intelligence_rollback",
    "paid_media_view",
    "paid_media_manage",
    "ai_use",
    "ai_admin",
    "ai_view_metrics",
    "waba_view",
    "waba_reply",
    "waba_admin",
    "system_logs_view",
    "system_integrations_manage",
    "system_jobs_manage",
    "system_users_manage",
    "system_super_admin",
)


EXECUTIVE_DEFAULTS = {
    "ai_use",
    "waba_view",
    "waba_reply",
}
MARKETING_DEFAULTS = {
    "web_intelligence_view",
    "web_intelligence_seo",
    "web_intelligence_campaigns",
    "web_intelligence_performance",
    "web_intelligence_change_request",
    "paid_media_view",
    "ai_use",
}
ADMIN_DEFAULTS = set(PERMISSIONS) - {
    "web_intelligence_deploy",
    "web_intelligence_rollback",
    "system_super_admin",
}


def role_key(user: dict[str, Any] | str | None) -> str:
    if isinstance(user, dict):
        value = user.get("role") or user.get("rol") or ""
    else:
        value = user or ""
    return " ".join(str(value).strip().upper().replace("_", " ").split())


def user_id(user: dict[str, Any]) -> int | None:
    value = user.get("id_usuario") or user.get("id") or user.get("user_id")
    try:
        return int(value) if str(value or "").isdigit() else None
    except (TypeError, ValueError):
        return None


def default_permissions(user: dict[str, Any] | str | None) -> set[str]:
    role = role_key(user)
    compact = role.replace(" ", "")
    if compact == "SUPERADMIN":
        return set(PERMISSIONS)
    if "ADMIN" in compact:
        return set(ADMIN_DEFAULTS)
    if "MARKETING" in role or "MERCADO" in role:
        return set(MARKETING_DEFAULTS)
    if "EJECUT" in role or "VENTA" in role:
        return set(EXECUTIVE_DEFAULTS)
    return set()


def resolve_permissions(conn, user: dict[str, Any]) -> set[str]:
    """Resolve safe role defaults plus optional explicit DB grants/denials."""
    resolved = default_permissions(user)
    tables_ready = conn.execute(
        text(
            """
            SELECT to_regclass('public.gd_role_permissions') IS NOT NULL
               AND to_regclass('public.gd_user_permissions') IS NOT NULL
            """
        )
    ).scalar()
    if not tables_ready:
        return resolved

    role = role_key(user)
    uid = user_id(user)
    rows = conn.execute(
        text(
            """
            SELECT permission_key, allowed, 1 AS precedence
            FROM public.gd_role_permissions
            WHERE upper(role_key)=upper(:role)
            UNION ALL
            SELECT permission_key, allowed, 2 AS precedence
            FROM public.gd_user_permissions
            WHERE user_id=:uid
            ORDER BY precedence
            """
        ),
        {"role": role, "uid": uid or -1},
    ).mappings()
    for row in rows:
        key = str(row["permission_key"])
        if key not in PERMISSIONS:
            continue
        if bool(row["allowed"]):
            resolved.add(key)
        else:
            resolved.discard(key)
    return resolved


def has_permission(conn, user: dict[str, Any], permission: str) -> bool:
    return permission in resolve_permissions(conn, user)
