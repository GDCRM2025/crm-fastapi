#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import subprocess


DEFAULT_DB = {
    "host": os.getenv("PGHOST", "localhost"),
    "port": os.getenv("PGPORT", "5432"),
    "dbname": os.getenv("PGDATABASE", "BDGD"),
    "user": os.getenv("PGUSER", "BDGD"),
    "password": os.getenv("PGPASSWORD", "SpC18302020"),
}


SQL_HOTFIX = """
DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT DISTINCT c.table_schema, c.table_name
        FROM information_schema.columns c
        WHERE c.column_name = 'subtotal_productos'
          AND c.table_schema NOT IN ('pg_catalog', 'information_schema')
    LOOP
        IF NOT EXISTS (
            SELECT 1
            FROM information_schema.columns c2
            WHERE c2.table_schema = r.table_schema
              AND c2.table_name = r.table_name
              AND c2.column_name = 'subtotal'
        ) THEN
            EXECUTE format(
                'ALTER TABLE %I.%I ADD COLUMN subtotal NUMERIC',
                r.table_schema,
                r.table_name
            );

            RAISE NOTICE 'Columna subtotal creada en %.%', r.table_schema, r.table_name;
        ELSE
            RAISE NOTICE 'Columna subtotal ya existia en %.%', r.table_schema, r.table_name;
        END IF;

        EXECUTE format(
            $SQL$
            UPDATE %I.%I
            SET subtotal = CASE
                WHEN subtotal_productos IS NULL THEN subtotal
                WHEN NULLIF(regexp_replace(subtotal_productos::text, '[^0-9]', '', 'g'), '') IS NULL THEN subtotal
                ELSE NULLIF(regexp_replace(subtotal_productos::text, '[^0-9]', '', 'g'), '')::numeric
            END
            WHERE subtotal IS NULL
              AND subtotal_productos IS NOT NULL
            $SQL$,
            r.table_schema,
            r.table_name
        );

        RAISE NOTICE 'Subtotal sincronizado en %.%', r.table_schema, r.table_name;
    END LOOP;
END $$;
"""


SQL_VERIFY = """
SELECT
    c.table_schema,
    c.table_name,
    COUNT(*) FILTER (WHERE c.column_name = 'subtotal_productos') AS tiene_subtotal_productos,
    COUNT(*) FILTER (WHERE c.column_name = 'subtotal') AS tiene_subtotal
FROM information_schema.columns c
WHERE c.column_name IN ('subtotal_productos', 'subtotal')
  AND c.table_schema NOT IN ('pg_catalog', 'information_schema')
GROUP BY c.table_schema, c.table_name
ORDER BY c.table_schema, c.table_name;
"""


def import_psycopg():
    try:
        import psycopg
        return "psycopg", psycopg
    except ImportError:
        pass

    try:
        import psycopg2
        return "psycopg2", psycopg2
    except ImportError:
        pass

    print("No esta instalado psycopg. Intentando instalar psycopg[binary]...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "psycopg[binary]"])

    import psycopg
    return "psycopg", psycopg


def connect():
    database_url = os.getenv("DATABASE_URL")
    driver_name, driver = import_psycopg()

    if database_url:
        return driver.connect(database_url)

    if driver_name == "psycopg":
        return driver.connect(**DEFAULT_DB)

    return driver.connect(
        host=DEFAULT_DB["host"],
        port=DEFAULT_DB["port"],
        dbname=DEFAULT_DB["dbname"],
        user=DEFAULT_DB["user"],
        password=DEFAULT_DB["password"],
    )


def main():
    print("==============================================")
    print("GREEN DIAMOND CRM - HOTFIX SUBTOTAL")
    print("==============================================")
    print(f"DB: {DEFAULT_DB['dbname']}")
    print(f"USER: {DEFAULT_DB['user']}")
    print(f"HOST: {DEFAULT_DB['host']}:{DEFAULT_DB['port']}")
    print("----------------------------------------------")

    conn = None

    try:
        conn = connect()
        cur = conn.cursor()

        print("Aplicando hotfix...")
        cur.execute(SQL_HOTFIX)
        conn.commit()

        print("Verificando columnas...")
        cur.execute(SQL_VERIFY)
        rows = cur.fetchall()

        cur.close()
        conn.close()

        print("----------------------------------------------")
        print("RESULTADO:")
        if not rows:
            print("No encontre tablas con subtotal_productos. Revisa nombre real de tabla/columna.")
        else:
            for row in rows:
                schema, table, has_subtotal_productos, has_subtotal = row
                print(
                    f"- {schema}.{table} | "
                    f"subtotal_productos={has_subtotal_productos} | "
                    f"subtotal={has_subtotal}"
                )

        print("----------------------------------------------")
        print("HOTFIX COMPLETADO OK.")
        print("Ahora prueba mover el lead a CONFIRMADO.")
        print("==============================================")

    except Exception as e:
        if conn:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass

        print("----------------------------------------------")
        print("ERROR APLICANDO HOTFIX:")
        print(str(e))
        print("----------------------------------------------")
        sys.exit(1)


if __name__ == "__main__":
    main()
