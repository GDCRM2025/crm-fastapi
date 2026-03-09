from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from datetime import date, datetime
from typing import Any

from sqlalchemy import MetaData, Table, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.core.database import engine


PROJECT_ROOT = Path(__file__).resolve().parents[2]  # .../CRM 2025


def _to_none_if_blank(v: Any) -> Any:
    if isinstance(v, str) and not v.strip():
        return None
    return v


def _to_date(v: Any) -> Any:
    v = _to_none_if_blank(v)
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    s = str(v).strip()
    if len(s) >= 10:
        s = s[:10]
    try:
        return date.fromisoformat(s)
    except Exception:
        return None


def _to_datetime(v: Any) -> Any:
    v = _to_none_if_blank(v)
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).strip().replace(" ", "T")
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _default_for_type(col) -> Any:
    # Defaults agresivos para NOT NULL sin default
    t = col.type.__class__.__name__.lower()
    if "bool" in t:
        return False
    if "int" in t:
        return 0
    if "numeric" in t or "float" in t or "decimal" in t:
        return 0
    if "date" in t and "time" not in t:
        return date.today()
    if "time" in t or "timestamp" in t or "datetime" in t:
        return datetime.now()
    # string/text/others
    return ""


def _coerce(col, v: Any) -> Any:
    # Normaliza blancos a NULL y parsea fechas si aplica
    v = _to_none_if_blank(v)
    t = col.type.__class__.__name__.lower()
    if v is None:
        return None
    if "date" in t and "time" not in t:
        return _to_date(v)
    if "time" in t or "timestamp" in t or "datetime" in t:
        return _to_datetime(v)
    return v


def _ensure_stub_lead(conn, lead_id: int) -> None:
    # Inserta un lead mínimo solo si hace falta (para FK)
    conn.execute(
        text(
            """
            INSERT INTO leads(
              id_lead,nombre_cliente,email,telefono,id_marca,id_estado,fecha_evento,monto_cotizado,codigo_cliente,
              fecha_ingreso,id_tipo_cliente,plataforma,id_comuna,direccion,notas,num_cotizacion,
              pendiente_agendar
            )
            VALUES(
              :id,'Cliente','','',0,0,NULL,0,NULL,
              :fi,0,NULL,0,NULL,NULL,NULL,
              FALSE
            )
            ON CONFLICT (id_lead) DO NOTHING
            """
        ),
        {"id": lead_id, "fi": date.today()},
    )


def _fetch_id_set(conn, table: str, col: str) -> set[int]:
    rows = conn.execute(text(f"SELECT {col} FROM {table}")).fetchall()
    return {int(r[0]) for r in rows if r and r[0] is not None}


def _truncate_extras(conn) -> None:
    conn.execute(text("TRUNCATE cotizaciones_detalle RESTART IDENTITY CASCADE"))
    conn.execute(text("TRUNCATE cotizaciones RESTART IDENTITY CASCADE"))
    conn.execute(text("TRUNCATE eventos_calendario RESTART IDENTITY CASCADE"))


def _insert_rows(conn, pg_table: Table, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0

    pk_cols = [c.name for c in pg_table.primary_key.columns] or []
    stmt = pg_insert(pg_table).values(rows)

    if pk_cols:
        stmt = stmt.on_conflict_do_nothing(index_elements=pk_cols)
    else:
        stmt = stmt.on_conflict_do_nothing()

    res = conn.execute(stmt)
    # res.rowcount puede ser -1 en batch; devolvemos len como aproximación “best-effort”
    return res.rowcount if (res.rowcount is not None and res.rowcount >= 0) else len(rows)


def _migrate_table(
    conn,
    sqlite_cur: sqlite3.Cursor,
    sqlite_table_name: str,
    pg_table: Table,
    ensure_lead_fk: bool = False,
    lead_id_field: str = "id_lead",
    require_parent: tuple[str, str] | None = None,  # ("cotizaciones","id_cotizacion") etc.
) -> int:
    pg_cols = {c.name: c for c in pg_table.columns}

    parent_ids: set[int] | None = None
    if require_parent:
        parent_table, parent_col = require_parent
        parent_ids = _fetch_id_set(conn, parent_table, parent_col)

    inserted = 0
    batch: list[dict[str, Any]] = []

    sqlite_rows = sqlite_cur.execute(f"SELECT * FROM {sqlite_table_name}")
    for r in sqlite_rows:
        data: dict[str, Any] = {}

        # FK parent gate (ej: detalle sin header)
        if parent_ids is not None:
            pid = int(r["id_cotizacion"]) if "id_cotizacion" in r.keys() and r["id_cotizacion"] is not None else 0
            if pid <= 0 or pid not in parent_ids:
                continue

        # ensure lead exists if needed
        if ensure_lead_fk and lead_id_field in r.keys() and r[lead_id_field] is not None:
            lid = int(r[lead_id_field])
            if lid > 0:
                # rápido: intenta asegurar existencia sin consultar
                _ensure_stub_lead(conn, lid)

        # construye payload por intersección de columnas
        for name, col in pg_cols.items():
            if name in r.keys():
                data[name] = _coerce(col, r[name])

        # rellena NOT NULL sin default
        for name, col in pg_cols.items():
            if name not in data or data[name] is None:
                if (not col.nullable) and (col.default is None) and (col.server_default is None):
                    # si es PK autoincrement y no viene, no forzamos
                    if col.primary_key and getattr(col, "autoincrement", False):
                        continue
                    data[name] = _default_for_type(col)

        batch.append(data)
        if len(batch) >= 500:
            inserted += _insert_rows(conn, pg_table, batch)
            batch.clear()

    if batch:
        inserted += _insert_rows(conn, pg_table, batch)

    return inserted


def _set_sequences(conn) -> None:
    seqs = [
        ("cotizaciones", "id_cotizacion"),
        ("cotizaciones_detalle", "id_detalle"),
        ("eventos_calendario", "id_evento"),
    ]
    for table, col in seqs:
        conn.execute(
            text(
                f"""
                SELECT setval(
                  pg_get_serial_sequence('{table}','{col}'),
                  COALESCE((SELECT MAX({col}) FROM {table}), 1),
                  true
                );
                """
            )
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", default=str(PROJECT_ROOT / "backend" / "crm.db"))
    ap.add_argument("--wipe", action="store_true", help="Trunca SOLO cotizaciones/cotizaciones_detalle/eventos_calendario en Postgres")
    args = ap.parse_args()

    sqlite_path = Path(args.sqlite)
    if not sqlite_path.exists():
        raise SystemExit(f"No existe SQLite: {sqlite_path}")

    con = sqlite3.connect(str(sqlite_path))
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    meta = MetaData()
    cot = Table("cotizaciones", meta, autoload_with=engine)
    cot_det = Table("cotizaciones_detalle", meta, autoload_with=engine)
    ev = Table("eventos_calendario", meta, autoload_with=engine)

    with engine.begin() as pg:
        if args.wipe:
            print("WIPING EXTRAS en Postgres...")
            _truncate_extras(pg)

        # Migración
        n_cot = _migrate_table(pg, cur, "cotizaciones", cot, ensure_lead_fk=True, lead_id_field="id_lead")
        n_det = _migrate_table(pg, cur, "cotizaciones_detalle", cot_det, require_parent=("cotizaciones", "id_cotizacion"))
        n_ev = _migrate_table(pg, cur, "eventos_calendario", ev, ensure_lead_fk=True, lead_id_field="id_lead")

        # Alinea Admin
        pg.execute(text("UPDATE usuarios SET rol='Admin' WHERE username='greengd' OR email='admin@greendiamond.cl' OR id_usuario=1"))

        _set_sequences(pg)

        print(f"OK extras -> cotizaciones: {n_cot}, detalle: {n_det}, eventos_calendario: {n_ev}")


if __name__ == "__main__":
    main()
