# backend/scripts/migrate_crm_catalogos_v1.py
import os
from datetime import datetime, timezone

import psycopg


def mask_db_url(url: str) -> str:
    # postgresql://USER:PASS@host/db  -> hide pass
    try:
        prefix, rest = url.split("://", 1)
        creds, tail = rest.split("@", 1)
        user = creds.split(":", 1)[0]
        return f"{prefix}://{user}:****@{tail}"
    except Exception:
        return "postgresql://****"


def exec_sql(conn: psycopg.Connection, title: str, sql: str) -> None:
    print(f"\n--- {title} ---")
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
    print("OK")


def main() -> None:
    db_url = os.environ.get("DATABASE_URL", "").strip()
    if not db_url:
        raise RuntimeError(
            'DATABASE_URL no está seteada. Ej:\nexport DATABASE_URL="postgresql://BDGD:***@localhost:5432/BDGD"'
        )

    print("Conectando a:", mask_db_url(db_url))
    conn = psycopg.connect(db_url)

    now = datetime.now(timezone.utc)
    print("NOW(UTC) =", now.isoformat())

    exec_sql(
        conn,
        "Tabla lead_estados (create if missing)",
        """
        CREATE TABLE IF NOT EXISTS public.lead_estados (
          id_estado   SERIAL PRIMARY KEY,
          estado      TEXT NOT NULL,
          color       TEXT NOT NULL DEFAULT '#64748b',
          orden       INT  NOT NULL DEFAULT 0,
          is_active   BOOLEAN NOT NULL DEFAULT TRUE,
          created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at  TIMESTAMPTZ
        );

        -- Unique por nombre de estado
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'lead_estados_estado_key'
          ) THEN
            ALTER TABLE public.lead_estados
            ADD CONSTRAINT lead_estados_estado_key UNIQUE (estado);
          END IF;
        END $$;
        """,
    )

    exec_sql(
        conn,
        "Seed estados (idempotente) + normalización",
        """
        INSERT INTO public.lead_estados (estado, color, orden, is_active)
        VALUES
          ('NUEVO',      '#3b82f6', 10, TRUE),
          ('CONTACTADO', '#06b6d4', 20, TRUE),
          ('COTIZADO',   '#a855f7', 30, TRUE),
          ('CONFIRMADO', '#22c55e', 40, TRUE),
          ('DECLINADO',  '#ef4444', 90, TRUE)
        ON CONFLICT (estado) DO UPDATE
          SET color     = EXCLUDED.color,
              orden     = EXCLUDED.orden,
              is_active = EXCLUDED.is_active;

        -- Si existen estados “basura” viejos, NO los borramos aquí (política: no destructivo).
        """,
    )

    exec_sql(
        conn,
        "Index auxiliar por orden",
        """
        CREATE INDEX IF NOT EXISTS idx_lead_estados_orden
        ON public.lead_estados(orden);
        """,
    )

    # diagnóstico rápido
    with conn.cursor() as cur:
        cur.execute("SELECT id_estado, estado, color, orden, is_active FROM public.lead_estados ORDER BY orden;")
        rows = cur.fetchall()
        print("\nEstados actuales:")
        for r in rows:
            print(" -", r)

    conn.close()
    print("\n✅ Migración catálogos v1 aplicada.")


if __name__ == "__main__":
    main()