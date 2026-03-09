import os
import re
import psycopg
from psycopg.rows import dict_row


def masked(url: str) -> str:
    # oculta password
    return re.sub(r":([^:@/]+)@", r":****@", url)


def exec_sql(conn, title: str, sql: str):
    print(f"\n--- {title} ---")
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
    print("OK")


def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError('DATABASE_URL no está seteada. Ej: export DATABASE_URL="postgresql://..."')

    print("Conectando a:", masked(db_url))
    conn = psycopg.connect(db_url, autocommit=False, row_factory=dict_row)

    # 1) comunas: asegurar columna comuna
    exec_sql(conn, "Comunas: crear si falta", """
    CREATE TABLE IF NOT EXISTS public.comunas (
      id_comuna  serial PRIMARY KEY,
      comuna     text,
      is_active  boolean DEFAULT true
    );
    """)

    exec_sql(conn, "Comunas: asegurar columna comuna (si falta)", """
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name='comunas' AND column_name='comuna'
      ) THEN
        ALTER TABLE public.comunas ADD COLUMN comuna text;
      END IF;
    END $$;
    """)

    # si existe nombre/descripcion, copiar a comuna cuando comuna sea NULL/vacío
    exec_sql(conn, "Comunas: backfill comuna desde columnas existentes", """
    DO $$
    BEGIN
      IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name='comunas' AND column_name='nombre'
      ) THEN
        UPDATE public.comunas
           SET comuna = COALESCE(comuna, nombre)
         WHERE (comuna IS NULL OR comuna = '');
      ELSIF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name='comunas' AND column_name='descripcion'
      ) THEN
        UPDATE public.comunas
           SET comuna = COALESCE(comuna, descripcion)
         WHERE (comuna IS NULL OR comuna = '');
      END IF;
    END $$;
    """)

    # 2) lead_estados
    exec_sql(conn, "Lead_estados: crear si falta", """
    CREATE TABLE IF NOT EXISTS public.lead_estados (
      id_estado  serial PRIMARY KEY,
      estado     text NOT NULL,
      color      text DEFAULT '#64748b',
      orden      integer DEFAULT 0,
      is_active  boolean DEFAULT true
    );
    """)

    exec_sql(conn, "Lead_estados: columnas mínimas", """
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name='lead_estados' AND column_name='estado'
      ) THEN
        ALTER TABLE public.lead_estados ADD COLUMN estado text;
      END IF;

      IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name='lead_estados' AND column_name='color'
      ) THEN
        ALTER TABLE public.lead_estados ADD COLUMN color text DEFAULT '#64748b';
      END IF;

      IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name='lead_estados' AND column_name='orden'
      ) THEN
        ALTER TABLE public.lead_estados ADD COLUMN orden integer DEFAULT 0;
      END IF;

      IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name='lead_estados' AND column_name='is_active'
      ) THEN
        ALTER TABLE public.lead_estados ADD COLUMN is_active boolean DEFAULT true;
      END IF;
    END $$;
    """)

    exec_sql(conn, "Lead_estados: unique por estado (si no existe)", """
    DO $$
    BEGIN
      BEGIN
        ALTER TABLE public.lead_estados ADD CONSTRAINT lead_estados_estado_key UNIQUE (estado);
      EXCEPTION WHEN duplicate_object THEN
        NULL;
      END;
    END $$;
    """)

    # Seed básico (no rompe si ya existen)
    exec_sql(conn, "Lead_estados: seed básico", """
    INSERT INTO public.lead_estados(estado, color, orden, is_active) VALUES
      ('NUEVO',        '#3b82f6', 10, true),
      ('CONTACTADO',   '#06b6d4', 20, true),
      ('COTIZADO',     '#a855f7', 30, true),
      ('CONFIRMADO',   '#22c55e', 40, true),
      ('DECLINADO',    '#ef4444', 90, true)
    ON CONFLICT (estado) DO NOTHING;
    """)

    conn.close()
    print("\n✅ Catalogos OK (comunas + lead_estados).")


if __name__ == "__main__":
    main()