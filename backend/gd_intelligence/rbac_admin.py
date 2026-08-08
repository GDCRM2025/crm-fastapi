from __future__ import annotations

from typing import Any

from sqlalchemy import text

from backend.gd_intelligence.permissions import PERMISSIONS, default_permissions


ROLE_KEYS = ("EXECUTIVO", "MARKETING", "ADMIN", "SUPER_ADMIN")


def role_matrix(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            """
            SELECT upper(role_key) AS role_key, permission_key, allowed
            FROM public.gd_role_permissions
            WHERE upper(role_key)=ANY(:roles)
            ORDER BY role_key, permission_key
            """
        ),
        {"roles": list(ROLE_KEYS)},
    ).mappings()
    overrides: dict[str, dict[str, bool]] = {role: {} for role in ROLE_KEYS}
    for row in rows:
        role = str(row["role_key"])
        key = str(row["permission_key"])
        if role in overrides and key in PERMISSIONS:
            overrides[role][key] = bool(row["allowed"])

    items: list[dict[str, Any]] = []
    for role in ROLE_KEYS:
        defaults = default_permissions(role)
        effective = set(defaults)
        for key, allowed in overrides[role].items():
            effective.add(key) if allowed else effective.discard(key)
        items.append(
            {
                "role": role,
                "defaults": {key: key in defaults for key in PERMISSIONS},
                "overrides": overrides[role],
                "effective": {key: key in effective for key in PERMISSIONS},
            }
        )
    return items


def save_role_permissions(
    conn,
    role: str,
    permissions: dict[str, bool | None],
    actor: str,
) -> dict[str, int]:
    normalized = str(role or "").strip().upper().replace(" ", "_")
    if normalized not in ROLE_KEYS:
        raise ValueError("Rol GD Intelligence no permitido")
    unknown = sorted(set(permissions) - set(PERMISSIONS))
    if unknown:
        raise ValueError(f"Permisos desconocidos: {', '.join(unknown[:5])}")

    changed = 0
    inherited = 0
    for key, allowed in permissions.items():
        if allowed is None:
            result = conn.execute(
                text(
                    "DELETE FROM public.gd_role_permissions "
                    "WHERE upper(role_key)=:role AND permission_key=:permission"
                ),
                {"role": normalized, "permission": key},
            )
            changed += int(result.rowcount or 0)
            inherited += 1
            continue
        conn.execute(
            text(
                """
                INSERT INTO public.gd_role_permissions(role_key,permission_key,allowed,updated_by,updated_at)
                VALUES (:role,:permission,:allowed,:actor,now())
                ON CONFLICT(role_key,permission_key) DO UPDATE SET
                  allowed=excluded.allowed,
                  updated_by=excluded.updated_by,
                  updated_at=now()
                """
            ),
            {
                "role": normalized,
                "permission": key,
                "allowed": bool(allowed),
                "actor": actor,
            },
        )
        changed += 1
    return {"changed": changed, "inherited": inherited}
