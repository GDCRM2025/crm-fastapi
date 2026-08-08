from __future__ import annotations

import secrets
from pathlib import Path
import sys

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from sqlalchemy import text  # type: ignore
from backend.core.db import get_connection


def main():
    with get_connection() as conn:
        conn.execute(text("ALTER TABLE public.marcas ADD COLUMN IF NOT EXISTS form_token text"))
        rows = conn.execute(
            text("SELECT id_marca, COALESCE(nombre,marca) AS nombre, form_token FROM public.marcas ORDER BY id_marca")
        ).mappings().all()

        out = []
        for r in rows:
            token = (r.get("form_token") or "").strip()
            if not token:
                token = secrets.token_urlsafe(24)
                conn.execute(
                    text("UPDATE public.marcas SET form_token=:t WHERE id_marca=:id"),
                    {"t": token, "id": r["id_marca"]},
                )
            out.append((r["id_marca"], r["nombre"], token))

        conn.commit()

    print("FORM TOKENS:")
    for mid, name, tok in out:
        print(f"- {name} (id {mid}): {tok}")


if __name__ == "__main__":
    main()
