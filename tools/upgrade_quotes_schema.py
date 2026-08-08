# tools/upgrade_quotes_schema.py
import sqlite3
from datetime import datetime

DB_PATH = "backend/crm.db"


def column_exists(c, table, column):
    c.execute(f"PRAGMA table_info('{table}')")
    return any(row[1] == column for row in c.fetchall())


def table_exists(c, table):
    c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return c.fetchone() is not None


def view_exists(c, name):
    c.execute(
        "SELECT name FROM sqlite_master WHERE type='view' AND name=?",
        (name,),
    )
    return c.fetchone() is not None


def main():
    cx = sqlite3.connect(DB_PATH)
    c = cx.cursor()

    print(f"[INFO] Actualizando esquema cotizador en {DB_PATH}")

    # 1) lead.nro_cotizacion
    if not column_exists(c, "lead", "nro_cotizacion"):
        c.execute("ALTER TABLE lead ADD COLUMN nro_cotizacion TEXT")
        print("[OK] Agregada columna lead.nro_cotizacion")
    else:
        print("[SKIP] lead.nro_cotizacion ya existe")

    # 2) Tabla quotes (cabecera)
    if not table_exists(c, "quotes"):
        c.execute(
            """
            CREATE TABLE quotes (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                nro_cotizacion    TEXT UNIQUE NOT NULL,
                id_lead           INTEGER NOT NULL,
                marca             TEXT NOT NULL,
                fecha_cotizacion  TEXT NOT NULL,
                valido_hasta      TEXT,
                condicion_pago    TEXT,
                tipo_cliente      TEXT,
                aplica_descuento  INTEGER DEFAULT 0,
                descuento_porcentaje REAL DEFAULT 0,
                descuento_monto      REAL DEFAULT 0,
                subtotal_productos   REAL DEFAULT 0,
                traslado            REAL DEFAULT 0,
                extra               REAL DEFAULT 0,
                iva                 REAL DEFAULT 0,
                total               REAL DEFAULT 0,
                monto_neto          REAL DEFAULT 0,
                status_evento       TEXT,
                fecha_evento        TEXT,
                nombre_cliente      TEXT,
                comuna              TEXT,
                telefono            TEXT,
                email               TEXT,
                plataforma          TEXT,
                direccion           TEXT,
                version_label       TEXT DEFAULT 'A',
                base_quote_id       INTEGER,
                pdf_path            TEXT,
                created_at          TEXT NOT NULL,
                updated_at          TEXT,
                FOREIGN KEY (id_lead) REFERENCES lead(id_lead)
            )
            """
        )
        print("[OK] Creada tabla quotes")
    else:
        print("[SKIP] quotes ya existe")

    # 3) Tabla quote_items (detalle productos)
    if not table_exists(c, "quote_items"):
        c.execute(
            """
            CREATE TABLE quote_items (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                quote_id      INTEGER NOT NULL,
                orden         INTEGER NOT NULL,
                producto      TEXT NOT NULL,
                descripcion   TEXT,
                cantidad      REAL NOT NULL,
                precio_unitario REAL NOT NULL,
                subtotal      REAL NOT NULL,
                FOREIGN KEY (quote_id) REFERENCES quotes(id) ON DELETE CASCADE
            )
            """
        )
        print("[OK] Creada tabla quote_items")
    else:
        print("[SKIP] quote_items ya existe")

    # 4) Vista de historial "aplanada"
    if view_exists(c, "quotes_historial"):
        c.execute("DROP VIEW quotes_historial")
        print("[OK] Vista quotes_historial eliminada para recrear")

    c.execute(
        """
        CREATE VIEW quotes_historial AS
        SELECT
            q.id                         AS id,
            q.nro_cotizacion            AS nro_coti,
            q.fecha_cotizacion          AS fecha_coti,
            q.id_lead                   AS id_evento,
            q.condicion_pago,
            q.subtotal_productos        AS subtotal_productos,
            q.traslado,
            q.extra,
            q.descuento_porcentaje      AS dscto_porcentaje,
            q.descuento_monto           AS dscto_monto,
            q.tipo_cliente,
            q.aplica_descuento          AS tiene_descuento,
            q.monto_neto                AS monto_neto,
            q.status_evento,
            q.fecha_evento,
            q.nombre_cliente,
            q.marca,
            q.total                     AS total_cotizacion
        FROM quotes q
        """
    )
    print("[OK] Vista quotes_historial creada")

    cx.commit()
    cx.close()
    print("[DONE]", DB_PATH)


if __name__ == "__main__":
    main()
