import os
import re
from datetime import datetime, timezone

import psycopg

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError('DATABASE_URL no seteada. Ej: export DATABASE_URL="postgresql://BDGD:***@localhost:5432/BDGD"')


def mask_dsn(dsn: str) -> str:
    return re.sub(r"//([^:]+):([^@]+)@", r"//\1:****@", dsn)


def exec_sql(conn, title: str, sql: str):
    print(f"\n--- {title} ---")
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
    print("OK")


def main():
    print(f"Conectando a: {mask_dsn(DATABASE_URL)}")
    cn = psycopg.connect(DATABASE_URL)
    with cn.cursor() as cur:
        cur.execute("SELECT current_database(), current_user, now()")
        db, usr, now = cur.fetchone()
        print(f"DB={db} USER={usr} NOW={now}")

    # Lead estados (catálogo)
    exec_sql(
        cn,
        "Tabla lead_estados (create if missing)",
        """
        CREATE TABLE IF NOT EXISTS public.lead_estados(
          id_estado   serial PRIMARY KEY,
          estado      text NOT NULL,
          color       text NOT NULL DEFAULT '#64748b',
          orden       int  NOT NULL DEFAULT 10,
          is_active   boolean NOT NULL DEFAULT true,
          updated_at  timestamptz NOT NULL DEFAULT now()
        );
        """,
    )

    exec_sql(
        cn,
        "Asegurar columnas lead_estados (idempotente)",
        """
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='public' AND table_name='lead_estados' AND column_name='color'
          ) THEN
            ALTER TABLE public.lead_estados ADD COLUMN color text NOT NULL DEFAULT '#64748b';
          END IF;

          IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='public' AND table_name='lead_estados' AND column_name='orden'
          ) THEN
            ALTER TABLE public.lead_estados ADD COLUMN orden int NOT NULL DEFAULT 10;
          END IF;

          IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='public' AND table_name='lead_estados' AND column_name='is_active'
          ) THEN
            ALTER TABLE public.lead_estados ADD COLUMN is_active boolean NOT NULL DEFAULT true;
          END IF;

          IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='public' AND table_name='lead_estados' AND column_name='updated_at'
          ) THEN
            ALTER TABLE public.lead_estados ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();
          END IF;
        END $$;
        """,
    )

    # Seed de estados (TU LISTADO)
    exec_sql(
        cn,
        "Seed lead_estados (NUEVO/CONTACTADO/COTIZADO/CONFIRMADO/DECLINADO)",
        """
        INSERT INTO public.lead_estados(id_estado, estado, color, orden, is_active)
        VALUES
          (1,'NUEVO','#3b82f6',10,true),
          (2,'CONTACTADO','#06b6d4',20,true),
          (3,'COTIZADO','#a855f7',30,true),
          (4,'CONFIRMADO','#22c55e',40,true),
          (5,'DECLINADO','#ef4444',90,true)
        ON CONFLICT (id_estado) DO UPDATE
          SET estado=EXCLUDED.estado,
              color=EXCLUDED.color,
              orden=EXCLUDED.orden,
              is_active=EXCLUDED.is_active,
              updated_at=now();
        """,
    )

    exec_sql(
        cn,
        "Asegurar unique por estado",
        """
        DO $$
        BEGIN
          BEGIN
            ALTER TABLE public.lead_estados ADD CONSTRAINT lead_estados_estado_key UNIQUE (estado);
          EXCEPTION WHEN duplicate_object THEN NULL;
          END;
        END $$;
        """,
    )

    # Tipos cliente (mínimo viable para que el front no reviente)
    exec_sql(
        cn,
        "Tabla tipos_cliente (create if missing)",
        """
        CREATE TABLE IF NOT EXISTS public.tipos_cliente(
          id_tipo_cliente serial PRIMARY KEY,
          tipo text NOT NULL UNIQUE,
          is_active boolean NOT NULL DEFAULT true,
          updated_at timestamptz NOT NULL DEFAULT now()
        );
        """,
    )
    exec_sql(
        cn,
        "Seed tipos_cliente",
        """
        INSERT INTO public.tipos_cliente(tipo,is_active)
        VALUES ('PERSONA',true),('EMPRESA',true)
        ON CONFLICT (tipo) DO NOTHING;
        """,
    )

    # Asegurar columnas en leads para integridad
    exec_sql(
        cn,
        "Asegurar columnas clave en leads",
        """
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='public' AND table_name='leads' AND column_name='id_tipo_cliente'
          ) THEN
            ALTER TABLE public.leads ADD COLUMN id_tipo_cliente int;
          END IF;

          IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='public' AND table_name='leads' AND column_name='created_at'
          ) THEN
            ALTER TABLE public.leads ADD COLUMN created_at timestamptz NOT NULL DEFAULT now();
          END IF;

          IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='public' AND table_name='leads' AND column_name='updated_at'
          ) THEN
            ALTER TABLE public.leads ADD COLUMN updated_at timestamptz;
          END IF;

          IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='public' AND table_name='leads' AND column_name='declinado_motivo'
          ) THEN
            ALTER TABLE public.leads ADD COLUMN declinado_motivo text;
          END IF;

          IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='public' AND table_name='leads' AND column_name='declinado_at'
          ) THEN
            ALTER TABLE public.leads ADD COLUMN declinado_at timestamptz;
          END IF;
        END $$;
        """,
    )

    # FK suaves (si existen tablas)
    exec_sql(
        cn,
        "FK leads -> lead_estados / tipos_cliente (si no existen, ignora)",
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='leads')
             AND EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='lead_estados')
          THEN
            BEGIN
              ALTER TABLE public.leads
                ADD CONSTRAINT leads_id_estado_fkey
                FOREIGN KEY (id_estado) REFERENCES public.lead_estados(id_estado);
            EXCEPTION WHEN duplicate_object THEN NULL;
            END;
          END IF;

          IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='leads')
             AND EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='tipos_cliente')
          THEN
            BEGIN
              ALTER TABLE public.leads
                ADD CONSTRAINT leads_id_tipo_cliente_fkey
                FOREIGN KEY (id_tipo_cliente) REFERENCES public.tipos_cliente(id_tipo_cliente);
            EXCEPTION WHEN duplicate_object THEN NULL;
            END;
          END IF;
        END $$;
        """,
    )

    # Productos_vw sin precio_lista (si existía en tu contrato viejo)
    exec_sql(
        cn,
        "Vista productos_vw (sin precio_lista)",
        """
        CREATE OR REPLACE VIEW public.productos_vw AS
        SELECT
          p.id_producto,
          p.producto,
          p.descripcion,
          p.ingredientes,
          p.marca,
          p.costo,
          p.is_active,
          p.orden,
          p.created_at,
          p.updated_at
        FROM public.productos p;
        """,
    )

    cn.close()
    print("\n✅ Migración v4 aplicada.")


if __name__ == "__main__":
    main()
