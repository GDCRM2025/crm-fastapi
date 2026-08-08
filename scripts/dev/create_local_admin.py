#!/usr/bin/env python3
"""Create/update a SUPER ADMIN fixture only in an unmistakably local test database."""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.password import hash_password


def main() -> int:
    database_url = str(os.getenv("DATABASE_URL") or "").strip()
    parsed = urlsplit(database_url.replace("postgresql+psycopg://", "postgresql://"))
    database = parsed.path.lstrip("/").lower()
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("REFUSED: DATABASE_URL no apunta al loopback local")
    if not any(marker in database for marker in ("test", "restore", "dev", "local")):
        raise SystemExit("REFUSED: el nombre de la base no parece de desarrollo/restore")
    password = getpass.getpass("Contraseña local (mínimo 12 caracteres): ")
    if len(password) < 12:
        raise SystemExit("REFUSED: contraseña demasiado corta")
    engine = create_engine(database_url)
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                UPDATE public.usuarios
                SET hashed_password=:password,rol='SUPER ADMIN',cargo='SUPER ADMIN',id_rol=12,
                    is_active=true,updated_at=now()
                WHERE username='gd_local_admin'
                """
            ),
            {"password": hash_password(password)},
        )
        if not result.rowcount:
            conn.execute(
                text(
                    """
                    INSERT INTO public.usuarios(
                      nombre,email,username,hashed_password,cargo,id_rol,rol,is_active,created_at,updated_at
                    ) VALUES (
                      'GD Local Admin','gd-local-admin@example.invalid','gd_local_admin',:password,
                      'SUPER ADMIN',12,'SUPER ADMIN',true,now(),now()
                    )
                    """
                ),
                {"password": hash_password(password)},
            )
    print("LOCAL_ADMIN_READY username=gd_local_admin")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
