from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def _table_exists(conn, table: str) -> bool:
    return bool(
        conn.execute(
            text(
                """
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema='public' AND table_name=:t
                LIMIT 1
                """
            ),
            {"t": table},
        ).fetchone()
    )


def _col_exists(conn, table: str, col: str) -> bool:
    return bool(
        conn.execute(
            text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name=:t AND column_name=:c
                LIMIT 1
                """
            ),
            {"t": table, "c": col},
        ).fetchone()
    )


def _count(conn, table: str) -> int:
    return int(conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar_one())


def bootstrap(engine: Engine) -> None:
    """
    Bootstrap idempotente (PostgreSQL):
    - Crea tablas mínimas si no existen.
    - Agrega columnas faltantes con ALTER TABLE ... IF NOT EXISTS.
    - Seed SOLO si la tabla está vacía (para no "revivir" registros borrados).
    """
    with engine.begin() as cn:
        # -----------------------------
        # Tablas mínimas (solo si faltan)
        # -----------------------------
        if not _table_exists(cn, "roles"):
            cn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS roles (
                        id_rol SERIAL PRIMARY KEY,
                        nombre TEXT UNIQUE NOT NULL,
                        is_active BOOLEAN NOT NULL DEFAULT TRUE
                    )
                    """
                )
            )

        if not _table_exists(cn, "usuarios"):
            cn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS usuarios (
                        id_usuario SERIAL PRIMARY KEY,
                        nombre TEXT NOT NULL,
                        email TEXT,
                        username TEXT UNIQUE NOT NULL,
                        telefono TEXT,
                        cargo TEXT,
                        rol TEXT NOT NULL DEFAULT 'User',
                        is_active BOOLEAN NOT NULL DEFAULT TRUE,
                        hashed_password TEXT NOT NULL,
                        created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
                        updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now()
                    )
                    """
                )
            )

        if not _table_exists(cn, "marcas"):
            cn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS marcas (
                        id_marca SERIAL PRIMARY KEY,
                        nombre TEXT UNIQUE NOT NULL,
                        is_active BOOLEAN NOT NULL DEFAULT TRUE
                    )
                    """
                )
            )

        if not _table_exists(cn, "tipocliente"):
            cn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS tipocliente (
                        id_tipo_cliente SERIAL PRIMARY KEY,
                        nombre TEXT UNIQUE NOT NULL,
                        is_active BOOLEAN NOT NULL DEFAULT TRUE
                    )
                    """
                )
            )

        if not _table_exists(cn, "comunas"):
            cn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS comunas (
                        id_comuna SERIAL PRIMARY KEY,
                        nombre TEXT UNIQUE NOT NULL,
                        neto NUMERIC DEFAULT 0,
                        bruto NUMERIC DEFAULT 0,
                        is_active BOOLEAN NOT NULL DEFAULT TRUE
                    )
                    """
                )
            )

        if not _table_exists(cn, "estados_lead"):
            cn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS estados_lead (
                        id_estado SERIAL PRIMARY KEY,
                        nombre TEXT UNIQUE NOT NULL,
                        color TEXT,
                        orden INTEGER DEFAULT 0,
                        is_active BOOLEAN NOT NULL DEFAULT TRUE
                    )
                    """
                )
            )

        if not _table_exists(cn, "leads"):
            cn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS leads (
                        id_lead SERIAL PRIMARY KEY,
                        nombre_cliente TEXT NOT NULL,
                        email TEXT,
                        telefono TEXT,
                        id_marca INTEGER REFERENCES marcas(id_marca),
                        id_estado INTEGER REFERENCES estados_lead(id_estado),
                        id_tipo_cliente INTEGER REFERENCES tipocliente(id_tipo_cliente),
                        id_comuna INTEGER REFERENCES comunas(id_comuna),
                        fecha_evento DATE,
                        direccion TEXT,
                        plataforma TEXT,
                        notas TEXT,
                        monto_cotizado NUMERIC DEFAULT 0,
                        codigo_cliente TEXT,
                        created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
                        updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now()
                    )
                    """
                )
            )

        # -----------------------------
        # Columnas faltantes (safe)
        # -----------------------------
        if _table_exists(cn, "estados_lead"):
            if not _col_exists(cn, "estados_lead", "color"):
                cn.execute(text('ALTER TABLE "estados_lead" ADD COLUMN IF NOT EXISTS "color" TEXT'))
            if not _col_exists(cn, "estados_lead", "orden"):
                cn.execute(text('ALTER TABLE "estados_lead" ADD COLUMN IF NOT EXISTS "orden" INTEGER DEFAULT 0'))
            if not _col_exists(cn, "estados_lead", "is_active"):
                cn.execute(text('ALTER TABLE "estados_lead" ADD COLUMN IF NOT EXISTS "is_active" BOOLEAN NOT NULL DEFAULT TRUE'))

        if _table_exists(cn, "comunas"):
            if not _col_exists(cn, "comunas", "is_active"):
                cn.execute(text('ALTER TABLE "comunas" ADD COLUMN IF NOT EXISTS "is_active" BOOLEAN NOT NULL DEFAULT TRUE'))

        if _table_exists(cn, "productos"):
            if not _col_exists(cn, "productos", "is_active"):
                cn.execute(text('ALTER TABLE "productos" ADD COLUMN IF NOT EXISTS "is_active" BOOLEAN NOT NULL DEFAULT TRUE'))
            if not _col_exists(cn, "productos", "costo"):
                cn.execute(text('ALTER TABLE "productos" ADD COLUMN IF NOT EXISTS "costo" NUMERIC DEFAULT 0'))

        if _table_exists(cn, "roles"):
            if not _col_exists(cn, "roles", "is_active"):
                cn.execute(text('ALTER TABLE "roles" ADD COLUMN IF NOT EXISTS "is_active" BOOLEAN NOT NULL DEFAULT TRUE'))

        # -----------------------------
        # Seed (solo si vacío)
        # -----------------------------
        if _table_exists(cn, "roles") and _count(cn, "roles") == 0:
            cn.execute(text("INSERT INTO roles(nombre,is_active) VALUES ('Admin',TRUE),('User',TRUE)"))

        if _table_exists(cn, "tipocliente") and _count(cn, "tipocliente") == 0:
            cn.execute(text("INSERT INTO tipocliente(nombre,is_active) VALUES ('Empresa',TRUE),('Persona',TRUE)"))

        # Estados: SOLO si está totalmente vacío
        if _table_exists(cn, "estados_lead") and _count(cn, "estados_lead") == 0:
            cn.execute(
                text(
                    """
                    INSERT INTO estados_lead(nombre,color,orden,is_active)
                    VALUES
                      ('Pendiente', '#19C37D', 10, TRUE),
                      ('Contactado', '#19C37D', 20, TRUE),
                      ('Cotizado', '#19C37D', 30, TRUE),
                      ('Confirmado', '#19C37D', 40, TRUE),
                      ('Declinado', '#19C37D', 50, TRUE)
                    """
                )
            )
