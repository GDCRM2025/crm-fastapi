import argparse
import os
import sys
from sqlalchemy import text

# Ensure project root on path
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.core.db import get_connection
import bcrypt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--password-mode", default="rut", choices=["rut", "static"])
    ap.add_argument("--default-password", default="Temporal123!")
    args = ap.parse_args()

    created = 0
    skipped = 0
    with get_connection() as conn:
        rows = conn.execute(
            text(
                """
                SELECT nombre, email, rut, cargo, status
                FROM operadores_allowlist
                """
            )
        ).mappings().all()

        for r in rows:
            email = (r.get("email") or "").strip().lower()
            nombre = (r.get("nombre") or "").strip()
            rut = (r.get("rut") or "").strip().lower()
            if not email or not rut:
                skipped += 1
                continue
            exists = conn.execute(
                text("SELECT id_usuario FROM usuarios WHERE lower(email)=:e LIMIT 1"),
                {"e": email},
            ).first()
            if exists:
                skipped += 1
                continue
            cargo = (r.get("cargo") or "OPERADOR").strip().upper()
            if cargo not in ("OPERADOR", "CHOP", "CHOFER", "CONDUCTOR"):
                cargo = "OPERADOR"
            # role id (fallbacks)
            role_row = conn.execute(
                text("SELECT id_rol, nombre FROM roles WHERE upper(nombre)=:n LIMIT 1"),
                {"n": cargo},
            ).mappings().first()
            if not role_row and cargo in ("CHOP", "CHOFER", "CONDUCTOR"):
                role_row = conn.execute(
                    text("SELECT id_rol, nombre FROM roles WHERE upper(nombre)=:n LIMIT 1"),
                    {"n": "CONDUCTOR (CHOP)"},
                ).mappings().first()
            if not role_row and cargo in ("CHOP", "CHOFER", "CONDUCTOR"):
                role_row = conn.execute(
                    text("SELECT id_rol, nombre FROM roles WHERE upper(nombre)=:n LIMIT 1"),
                    {"n": "CONDUCTOR"},
                ).mappings().first()
            if not role_row:
                skipped += 1
                continue
            role_id = role_row["id_rol"]
            # Password: RUT sin puntos, con guion (ej: 12345678-9)
            if args.password_mode == "rut":
                pwd = rut.replace(".", "").replace(" ", "")
            else:
                pwd = args.default_password
            hp = bcrypt.hashpw(pwd.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            username = email
            conn.execute(
                text(
                    """
                    INSERT INTO usuarios(nombre,email,username,hashed_password,telefono,cargo,id_rol,rol,is_active,created_at,updated_at)
                    VALUES (:n,:e,:u,:hp,NULL,:cargo,:rid,:rol,TRUE,now(),now())
                    """
                ),
                {"n": nombre or username, "e": email, "u": username, "hp": hp, "cargo": cargo, "rid": role_id, "rol": cargo},
            )
            created += 1
        conn.commit()

    print({"created": created, "skipped": skipped, "password_mode": args.password_mode, "default_password": args.default_password})


if __name__ == "__main__":
    main()
