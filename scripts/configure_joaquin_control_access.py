#!/usr/bin/env python3
"""Configure broad operational CRM access for the Control de Gestión role."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from backend.core.database import SessionLocal
from backend.routers.permissions import PERMISSION_CATALOG


USER_ID = 234
ROLE = "CONTROL DE GESTION"

MENU_PERMISSIONS = {str(item["id"]) for item in PERMISSION_CATALOG}
GD_PERMISSIONS = {
    "web_intelligence_view",
    "web_intelligence_configure",
    "web_intelligence_seo",
    "web_intelligence_campaigns",
    "web_intelligence_performance",
    "web_intelligence_change_request",
    "web_intelligence_approve_change",
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
}


def rows(session, query: str, params: dict) -> list[dict]:
    return [dict(row) for row in session.execute(text(query), params).mappings()]


def main() -> None:
    backup_dir = Path(os.environ.get("GD_ACCESS_BACKUP_DIR", "/opt/greendiamond/backups/access"))
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"joaquin-access-before-{stamp}.json"

    with SessionLocal() as session:
        user = session.execute(
            text(
                """
                SELECT id_usuario, username, nombre, email, rol, id_rol, is_active
                FROM public.usuarios WHERE id_usuario=:id
                """
            ),
            {"id": USER_ID},
        ).mappings().first()
        if not user or user["username"] != "usr_joaquinsal":
            raise RuntimeError("No se encontró el usuario esperado usr_joaquinsal (id 234).")
        if not bool(user["is_active"]) or str(user["rol"] or "").upper() != ROLE:
            raise RuntimeError("El usuario no está activo con el rol CONTROL DE GESTION.")

        backup = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "user": dict(user),
            "user_menu_permissions": rows(
                session,
                "SELECT * FROM public.user_menu_permissions WHERE id_usuario=:id ORDER BY menu_id",
                {"id": USER_ID},
            ),
            "role_menu_permissions": rows(
                session,
                "SELECT * FROM public.role_menu_permissions WHERE upper(rol)=upper(:role) ORDER BY menu_id",
                {"role": ROLE},
            ),
            "gd_role_permissions": rows(
                session,
                "SELECT * FROM public.gd_role_permissions WHERE upper(role_key)=upper(:role) ORDER BY permission_key",
                {"role": ROLE},
            ),
            "gd_user_permissions": rows(
                session,
                "SELECT * FROM public.gd_user_permissions WHERE user_id=:id ORDER BY permission_key",
                {"id": USER_ID},
            ),
        }
        backup_path.write_text(json.dumps(backup, ensure_ascii=False, indent=2, default=str) + "\n")
        backup_path.chmod(0o600)

        # Cierra la transacción de sólo lectura abierta por las consultas anteriores.
        session.rollback()
        with session.begin():
            # Elimina los overrides históricos de acceso total; vuelve a heredar del rol.
            session.execute(text("DELETE FROM public.user_menu_permissions WHERE id_usuario=:id"), {"id": USER_ID})
            session.execute(text("DELETE FROM public.gd_user_permissions WHERE user_id=:id"), {"id": USER_ID})

            session.execute(
                text("DELETE FROM public.role_menu_permissions WHERE upper(rol)=upper(:role)"),
                {"role": ROLE},
            )
            for menu_id in sorted(MENU_PERMISSIONS):
                session.execute(
                    text(
                        """
                        INSERT INTO public.role_menu_permissions(rol,menu_id,access,updated_by,updated_at)
                        VALUES(:role,:menu,'full',1,now())
                        """
                    ),
                    {"role": ROLE, "menu": menu_id},
                )

            session.execute(
                text("DELETE FROM public.gd_role_permissions WHERE upper(role_key)=upper(:role)"),
                {"role": ROLE},
            )
            for permission in sorted(GD_PERMISSIONS):
                session.execute(
                    text(
                        """
                        INSERT INTO public.gd_role_permissions(role_key,permission_key,allowed,updated_by,updated_at)
                        VALUES(:role,:permission,TRUE,1,now())
                        """
                    ),
                    {"role": ROLE, "permission": permission},
                )

    print(f"BACKUP={backup_path}")
    print(f"MENU_FULL={len(MENU_PERMISSIONS)}")
    print(f"GD_ADMIN_EQUIVALENT={len(GD_PERMISSIONS)}")


if __name__ == "__main__":
    main()
