import os
import re
import psycopg


def normalize_dsn(dsn: str) -> str:
    dsn = dsn.strip()
    dsn = dsn.replace("postgresql+psycopg://", "postgresql://")
    dsn = dsn.replace("postgresql+psycopg2://", "postgresql://")
    return dsn


def default_dsn() -> str:
    return "postgresql://BDGD:SpC18302020@localhost:5432/BDGD"


def exec_sql(conn, label: str, sql: str):
    print(f"\n--- {label} ---")
    with conn.cursor() as cur:
        cur.execute(sql)
    print("OK")


def table_exists(conn, table: str) -> bool:
    q = """
    SELECT 1
    FROM information_schema.tables
    WHERE table_schema='public' AND table_name=%s
    """
    with conn.cursor() as cur:
        cur.execute(q, (table,))
        return cur.fetchone() is not None


def dump_columns(conn, table: str):
    q = """
    SELECT column_name, data_type
    FROM information_schema.columns
    WHERE table_schema='public' AND table_name=%s
    ORDER BY ordinal_position
    """
    with conn.cursor() as cur:
        cur.execute(q, (table,))
        rows = cur.fetchall()
    print(f"\nColumnas actuales de '{table}':")
    for c, t in rows:
        print(f"  - {c}: {t}")


def main():
    dsn = normalize_dsn(os.getenv("DATABASE_URL") or default_dsn())
    print("Conectando a:", re.sub(r"://([^:]+):[^@]+@", r"://\\1:****@", dsn))

    conn = psycopg.connect(dsn)
    conn.autocommit = True

    with conn.cursor() as cur:
        cur.execute("SELECT current_database(), current_user, now();")
        db, usr, now = cur.fetchone()
    print(f"DB={db} USER={usr} NOW={now}")

    # Función trigger updated_at
    exec_sql(conn, "Función set_updated_at()", """
    CREATE OR REPLACE FUNCTION set_updated_at()
    RETURNS trigger AS $$
    BEGIN
      NEW.updated_at = now();
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)

    # MARCAS
    exec_sql(conn, "Tabla marcas (create if missing)", """
    CREATE TABLE IF NOT EXISTS marcas (
      id_marca   serial PRIMARY KEY,
      marca      text UNIQUE NOT NULL,
      logo_path  text,
      is_active  boolean NOT NULL DEFAULT true
    );
    """)

    exec_sql(conn, "Marcas: columnas contrato (idempotente)", """
    ALTER TABLE marcas ADD COLUMN IF NOT EXISTS logo_path text;
    ALTER TABLE marcas ADD COLUMN IF NOT EXISTS is_active boolean NOT NULL DEFAULT true;
    ALTER TABLE marcas ADD COLUMN IF NOT EXISTS updated_at timestamptz;
    DROP TRIGGER IF EXISTS trg_marcas_updated_at ON marcas;
    CREATE TRIGGER trg_marcas_updated_at
    BEFORE UPDATE ON marcas
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """)

    # PRODUCTOS (sin precio_lista)
    exec_sql(conn, "Tabla productos (create if missing)", """
    CREATE TABLE IF NOT EXISTS productos (
      id_producto serial PRIMARY KEY,
      producto    text,
      descripcion text,
      marca       text,
      costo       numeric(12,2),
      is_active   boolean NOT NULL DEFAULT true,
      orden       int NOT NULL DEFAULT 0,
      created_at  timestamptz NOT NULL DEFAULT now(),
      updated_at  timestamptz
    );
    """)

    exec_sql(conn, "Productos: columnas contrato + compatibilidad", """
    ALTER TABLE productos ADD COLUMN IF NOT EXISTS descripcion text;
    ALTER TABLE productos ADD COLUMN IF NOT EXISTS marca text;
    ALTER TABLE productos ADD COLUMN IF NOT EXISTS costo numeric(12,2);
    ALTER TABLE productos ADD COLUMN IF NOT EXISTS is_active boolean NOT NULL DEFAULT true;
    ALTER TABLE productos ADD COLUMN IF NOT EXISTS orden int NOT NULL DEFAULT 0;
    ALTER TABLE productos ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();
    ALTER TABLE productos ADD COLUMN IF NOT EXISTS updated_at timestamptz;

    DO $$
    BEGIN
      IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name='productos' AND column_name='ingredientes'
      ) THEN
        EXECUTE 'UPDATE productos SET descripcion = COALESCE(descripcion, ingredientes) WHERE descripcion IS NULL';
      END IF;
    END $$;

    ALTER TABLE productos DROP COLUMN IF EXISTS precio_lista;

    DROP TRIGGER IF EXISTS trg_productos_updated_at ON productos;
    CREATE TRIGGER trg_productos_updated_at
    BEFORE UPDATE ON productos
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """)

    # LEADS
    exec_sql(conn, "Tabla leads (create if missing)", """
    CREATE TABLE IF NOT EXISTS leads (
      id_lead          serial PRIMARY KEY,
      cliente          text,
      email            text,
      telefono         text,
      direccion        text,
      fecha_evento     date,
      estado           text,
      notas            text,
      declinado_motivo text,
      declinado_at     timestamptz,
      created_at       timestamptz NOT NULL DEFAULT now(),
      updated_at       timestamptz
    );
    """)

    exec_sql(conn, "Leads: columnas contrato + limpieza", """
    DO $$
    BEGIN
      IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name='leads' AND column_name='nombre_cliente'
      ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name='leads' AND column_name='cliente'
      ) THEN
        EXECUTE 'ALTER TABLE leads RENAME COLUMN nombre_cliente TO cliente';
      END IF;
    END $$;

    ALTER TABLE leads ADD COLUMN IF NOT EXISTS cliente text;
    ALTER TABLE leads ADD COLUMN IF NOT EXISTS email text;
    ALTER TABLE leads ADD COLUMN IF NOT EXISTS telefono text;
    ALTER TABLE leads ADD COLUMN IF NOT EXISTS direccion text;
    ALTER TABLE leads ADD COLUMN IF NOT EXISTS fecha_evento date;
    ALTER TABLE leads ADD COLUMN IF NOT EXISTS estado text;
    ALTER TABLE leads ADD COLUMN IF NOT EXISTS notas text;
    ALTER TABLE leads ADD COLUMN IF NOT EXISTS declinado_motivo text;
    ALTER TABLE leads ADD COLUMN IF NOT EXISTS declinado_at timestamptz;
    ALTER TABLE leads ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();
    ALTER TABLE leads ADD COLUMN IF NOT EXISTS updated_at timestamptz;

    ALTER TABLE leads DROP COLUMN IF EXISTS estado_color;

    ALTER TABLE leads DROP CONSTRAINT IF EXISTS leads_declinado_motivo_chk;
    ALTER TABLE leads
      ADD CONSTRAINT leads_declinado_motivo_chk
      CHECK (estado <> 'DECLINADO' OR (declinado_motivo IS NOT NULL AND btrim(declinado_motivo) <> ''));

    CREATE INDEX IF NOT EXISTS idx_leads_estado ON leads(estado);
    CREATE INDEX IF NOT EXISTS idx_leads_fecha_evento ON leads(fecha_evento);

    DROP TRIGGER IF EXISTS trg_leads_updated_at ON leads;
    CREATE TRIGGER trg_leads_updated_at
    BEFORE UPDATE ON leads
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """)

    # LEAD_NOTAS
    exec_sql(conn, "Tabla lead_notas (create if missing)", """
    CREATE TABLE IF NOT EXISTS lead_notas (
      id_nota     serial PRIMARY KEY,
      id_lead     int NOT NULL REFERENCES leads(id_lead) ON DELETE CASCADE,
      tipo        text,
      texto       text,
      created_at  timestamptz NOT NULL DEFAULT now(),
      created_by  text
    );
    CREATE INDEX IF NOT EXISTS idx_lead_notas_id_lead ON lead_notas(id_lead);
    """)

    # COTIZACIONES
    exec_sql(conn, "Tabla cotizaciones (create if missing)", """
    CREATE TABLE IF NOT EXISTS cotizaciones (
      id_cotizacion serial PRIMARY KEY,
      id_lead       int NOT NULL REFERENCES leads(id_lead) ON DELETE CASCADE,
      numero        text UNIQUE,
      fecha_emision date NOT NULL DEFAULT current_date,
      vigencia_dias int,
      subtotal      numeric(12,2),
      iva           numeric(12,2),
      total         numeric(12,2),
      condiciones   text,
      created_at    timestamptz NOT NULL DEFAULT now(),
      created_by    text
    );
    CREATE INDEX IF NOT EXISTS idx_cotizaciones_id_lead ON cotizaciones(id_lead);
    """)

    # COTIZACION_ITEMS
    exec_sql(conn, "Tabla cotizacion_items (create if missing)", """
    CREATE TABLE IF NOT EXISTS cotizacion_items (
      id_item        serial PRIMARY KEY,
      id_cotizacion  int NOT NULL REFERENCES cotizaciones(id_cotizacion) ON DELETE CASCADE,
      id_producto    int REFERENCES productos(id_producto),
      producto       text,
      descripcion    text,
      cantidad       int NOT NULL DEFAULT 1,
      precio_unitario numeric(12,2) NOT NULL DEFAULT 0,
      total_linea    numeric(12,2) NOT NULL DEFAULT 0,
      marca          text
    );
    CREATE INDEX IF NOT EXISTS idx_cotizacion_items_id_cotizacion ON cotizacion_items(id_cotizacion);
    """)

    # Vista productos_vw (tu error actual del cotizador)
    exec_sql(conn, "Vista productos_vw", """
    DROP VIEW IF EXISTS productos_vw;
    CREATE VIEW productos_vw AS
    SELECT
      id_producto,
      producto,
      descripcion,
      marca,
      costo,
      is_active,
      orden
    FROM productos;
    """)

    # Upsert marcas desde productos
    exec_sql(conn, "Upsert marcas desde productos", """
    INSERT INTO marcas(marca)
    SELECT DISTINCT btrim(marca)
    FROM productos
    WHERE marca IS NOT NULL AND btrim(marca) <> ''
    ON CONFLICT (marca) DO NOTHING;
    """)

    for t in ["marcas", "productos", "leads", "lead_notas", "cotizaciones", "cotizacion_items"]:
        if table_exists(conn, t):
            dump_columns(conn, t)

    print("\n✅ Migración v3 aplicada. productos_vw ya existe.")
    conn.close()


if __name__ == "__main__":
    main()
