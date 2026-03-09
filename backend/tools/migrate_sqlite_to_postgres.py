from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Any
from datetime import date

from sqlalchemy import text

from backend.core.database import engine
from backend.db_schema import bootstrap
from backend.core.auth import hash_password


PROJECT_ROOT = Path(__file__).resolve().parents[2]  # .../CRM 2025


def _tables(cur: sqlite3.Cursor) -> set[str]:
    rows = cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r[0] for r in rows}


def _cols(cur: sqlite3.Cursor, table: str) -> list[str]:
    return [r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()]


def _has_table(tables: set[str], name: str) -> bool:
    return name in tables


def _first_table(tables: set[str], candidates: list[str]) -> str | None:
    for c in candidates:
        if c in tables:
            return c
    return None


def _row_get(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    try:
        if key in row.keys():
            return row[key]
    except Exception:
        pass
    return default


def _as_bool(v: Any, default: bool = True) -> bool:
    if v is None:
        return default
    try:
        if isinstance(v, (int, float)):
            return bool(int(v))
        if isinstance(v, str):
            s = v.strip().lower()
            if s in ("1", "true", "t", "yes", "y", "si", "sí", "on"):
                return True
            if s in ("0", "false", "f", "no", "n", "off"):
                return False
    except Exception:
        pass
    return bool(v)


def _as_int(v: Any, default: int = 0) -> int:
    if v is None or v == "":
        return default
    try:
        return int(float(v))
    except Exception:
        return default


def _as_float(v: Any, default: float = 0.0) -> float:
    if v is None or v == "":
        return default
    try:
        return float(v)
    except Exception:
        return default


def _to_iso_ts(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    return s.replace(" ", "T")


def _to_iso_date(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    if not s:
        return ""
    if len(s) >= 10:
        return s[:10]
    return ""


def _set_sequences(pg) -> None:
    seqs = [
        ("roles", "id_rol"),
        ("marcas", "id_marca"),
        ("comunas", "id_comuna"),
        ("tipocliente", "id_tipo_cliente"),
        ("estados_lead", "id_estado"),
        ("usuarios", "id_usuario"),
        ("productos", "id_producto"),
        ("leads", "id_lead"),
        ("cotizaciones", "id_cotizacion"),
        ("cotizaciones_detalle", "id_detalle"),
        ("eventos_calendario", "id_evento"),
    ]
    for table, col in seqs:
        pg.execute(
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


def _ensure_fk_placeholders(pg) -> None:
    pg.execute(text("INSERT INTO marcas(id_marca,nombre,is_active) VALUES (0,'Sin Marca',TRUE) ON CONFLICT (id_marca) DO NOTHING"))
    pg.execute(text("INSERT INTO comunas(id_comuna,nombre,neto,bruto,is_active) VALUES (0,'Sin Comuna',0,0,TRUE) ON CONFLICT (id_comuna) DO NOTHING"))
    pg.execute(text("INSERT INTO tipocliente(id_tipo_cliente,nombre,is_active) VALUES (0,'Sin Tipo',TRUE) ON CONFLICT (id_tipo_cliente) DO NOTHING"))
    pg.execute(text("INSERT INTO estados_lead(id_estado,nombre,color,orden,is_active) VALUES (0,'Sin Estado',NULL,0,TRUE) ON CONFLICT (id_estado) DO NOTHING"))


def _fetch_id_set(pg, table: str, col: str) -> set[int]:
    rows = pg.execute(text(f"SELECT {col} FROM {table}")).fetchall()
    return {int(r[0]) for r in rows if r and r[0] is not None}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", default=str(PROJECT_ROOT / "backend" / "crm.db"))
    ap.add_argument("--wipe", action="store_true")
    args = ap.parse_args()

    sqlite_path = Path(args.sqlite)
    if not sqlite_path.exists():
        raise SystemExit(f"No existe SQLite: {sqlite_path}")

    bootstrap(engine)

    con = sqlite3.connect(str(sqlite_path))
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    tables = _tables(cur)

    print(f"SQLite: {sqlite_path}")
    print(f"Tablas SQLite: {sorted(list(tables))}")

    with engine.begin() as pg:
        if args.wipe:
            print("WIPING Postgres (TRUNCATE CASCADE)...")
            pg.execute(text("TRUNCATE cotizaciones_detalle RESTART IDENTITY CASCADE"))
            pg.execute(text("TRUNCATE cotizaciones RESTART IDENTITY CASCADE"))
            pg.execute(text("TRUNCATE eventos_calendario RESTART IDENTITY CASCADE"))
            pg.execute(text("TRUNCATE leads RESTART IDENTITY CASCADE"))
            pg.execute(text("TRUNCATE productos RESTART IDENTITY CASCADE"))
            pg.execute(text("TRUNCATE usuarios RESTART IDENTITY CASCADE"))
            pg.execute(text("TRUNCATE estados_lead RESTART IDENTITY CASCADE"))
            pg.execute(text("TRUNCATE tipocliente RESTART IDENTITY CASCADE"))
            pg.execute(text("TRUNCATE comunas RESTART IDENTITY CASCADE"))
            pg.execute(text("TRUNCATE marcas RESTART IDENTITY CASCADE"))
            pg.execute(text("TRUNCATE roles RESTART IDENTITY CASCADE"))

        roles_map: dict[int, str] = {}
        if _has_table(tables, "roles"):
            print("Migrando roles...")
            for r in cur.execute("SELECT * FROM roles"):
                id_rol = _as_int(_row_get(r, "id_rol", 0))
                nombre = str(_row_get(r, "nombre", "") or "").strip()
                if not nombre:
                    continue
                roles_map[id_rol] = nombre
                pg.execute(text("INSERT INTO roles(id_rol,nombre) VALUES (:i,:n) ON CONFLICT DO NOTHING"), {"i": id_rol, "n": nombre})

        if _has_table(tables, "marca"):
            print("Migrando marcas (marca -> marcas)...")
            marca_cols = _cols(cur, "marca")
            q = "SELECT id_marca, nombre" + (", is_active" if "is_active" in marca_cols else "") + " FROM marca"
            for r in cur.execute(q):
                id_marca = _as_int(_row_get(r, "id_marca", 0))
                nombre = str(_row_get(r, "nombre", "") or "").strip()
                if not nombre:
                    continue
                is_active = _as_bool(_row_get(r, "is_active", None), default=True)
                pg.execute(text("INSERT INTO marcas(id_marca,nombre,is_active) VALUES (:i,:n,:a) ON CONFLICT DO NOTHING"),
                           {"i": id_marca, "n": nombre, "a": is_active})

        if _has_table(tables, "comunas"):
            print("Migrando comunas...")
            ccols = _cols(cur, "comunas")
            for r in cur.execute("SELECT * FROM comunas"):
                id_comuna = _as_int(_row_get(r, "id_comuna", 0))
                nombre = str(_row_get(r, "nombre", "") or "").strip()
                if not nombre:
                    continue
                neto = _as_float(_row_get(r, "neto", 0), 0)
                bruto = _as_float(_row_get(r, "bruto", 0), 0)
                is_active = _as_bool(_row_get(r, "is_active", None), default=True) if "is_active" in ccols else True
                pg.execute(text("INSERT INTO comunas(id_comuna,nombre,neto,bruto,is_active) VALUES (:i,:n,:neto,:bruto,:a) ON CONFLICT DO NOTHING"),
                           {"i": id_comuna, "n": nombre, "neto": neto, "bruto": bruto, "a": is_active})

        if _has_table(tables, "tipocliente"):
            print("Migrando tipocliente...")
            tcols = _cols(cur, "tipocliente")
            for r in cur.execute("SELECT * FROM tipocliente"):
                id_tipo = _as_int(_row_get(r, "id_tipo_cliente", 0))
                nombre = str(_row_get(r, "nombre", "") or "").strip()
                if not nombre:
                    continue
                is_active = _as_bool(_row_get(r, "is_active", None), default=True) if "is_active" in tcols else True
                pg.execute(text("INSERT INTO tipocliente(id_tipo_cliente,nombre,is_active) VALUES (:i,:n,:a) ON CONFLICT DO NOTHING"),
                           {"i": id_tipo, "n": nombre, "a": is_active})

        if _has_table(tables, "estados_lead"):
            print("Migrando estados_lead...")
            ecols = _cols(cur, "estados_lead")
            for r in cur.execute("SELECT * FROM estados_lead"):
                id_estado = _as_int(_row_get(r, "id_estado", 0))
                nombre = str(_row_get(r, "nombre", "") or "").strip()
                if not nombre:
                    continue
                color = _row_get(r, "color", None) if "color" in ecols else None
                orden = _as_int(_row_get(r, "orden", 0)) if "orden" in ecols else 0
                is_active = _as_bool(_row_get(r, "is_active", None), default=True) if "is_active" in ecols else True
                pg.execute(text("INSERT INTO estados_lead(id_estado,nombre,color,orden,is_active) VALUES (:i,:n,:c,:o,:a) ON CONFLICT DO NOTHING"),
                           {"i": id_estado, "n": nombre, "c": color, "o": orden, "a": is_active})

        print("Asegurando placeholders FK (ID=0)...")
        _ensure_fk_placeholders(pg)

        marca_ids = _fetch_id_set(pg, "marcas", "id_marca")
        comuna_ids = _fetch_id_set(pg, "comunas", "id_comuna")
        tipo_ids = _fetch_id_set(pg, "tipocliente", "id_tipo_cliente")
        estado_ids = _fetch_id_set(pg, "estados_lead", "id_estado")

        if _has_table(tables, "usuarios"):
            print("Migrando usuarios...")
            ucols = _cols(cur, "usuarios")
            pwd_fields = ["hashed_password", "password", "clave", "contrasena", "contrasena_hash", "pass"]
            pwd_field = next((f for f in pwd_fields if f in ucols), None)

            for r in cur.execute("SELECT * FROM usuarios"):
                id_usuario = _as_int(_row_get(r, "id_usuario", 0))
                nombre = str(_row_get(r, "nombre", "") or "").strip() or "Usuario"
                username = str(_row_get(r, "username", "") or "").strip()
                email = _row_get(r, "email", None)

                raw_pwd = ""
                if pwd_field:
                    raw_pwd = str(_row_get(r, pwd_field, "") or "")
                if raw_pwd and not raw_pwd.startswith("$"):
                    raw_pwd = hash_password(raw_pwd)
                if not raw_pwd:
                    raw_pwd = hash_password("1234")

                if not username:
                    username = (str(email).split("@")[0] if email else f"user{id_usuario}")

                pg.execute(
                    text("""
                        INSERT INTO usuarios(id_usuario,nombre,email,username,telefono,cargo,rol,is_active,hashed_password)
                        VALUES (:id,:n,:e,:u,'','','User',TRUE,:hp)
                        ON CONFLICT (id_usuario) DO NOTHING
                    """),
                    {"id": id_usuario, "n": nombre, "e": email, "u": username, "hp": raw_pwd},
                )

        if _has_table(tables, "productos"):
            print("Migrando productos...")
            for r in cur.execute("SELECT * FROM productos"):
                id_producto = _as_int(_row_get(r, "id_producto", 0))
                producto = str(_row_get(r, "producto", "") or "").strip()
                if not producto:
                    continue
                costo = _as_float(_row_get(r, "costo", 0), 0)
                pg.execute(
                    text("""
                        INSERT INTO productos(id_producto,producto,ingredientes,marca,costo,is_active)
                        VALUES (:id,:p,NULL,'',:c,TRUE)
                        ON CONFLICT (id_producto) DO NOTHING
                    """),
                    {"id": id_producto, "p": producto, "c": costo},
                )

        lead_table = _first_table(tables, ["lead", "leads"])
        if lead_table:
            print(f"Migrando leads desde '{lead_table}'...")
            lcols = _cols(cur, lead_table)
            for r in cur.execute(f"SELECT * FROM {lead_table}"):
                id_lead = _as_int(_row_get(r, "id_lead", 0))
                nombre_cliente = str(_row_get(r, "nombre_cliente", "") or "").strip()
                if not nombre_cliente:
                    continue

                id_marca = _as_int(_row_get(r, "id_marca", 0)) if "id_marca" in lcols else 0
                id_estado = _as_int(_row_get(r, "id_estado", 0)) if "id_estado" in lcols else 0
                id_tipo_cliente = _as_int(_row_get(r, "id_tipo_cliente", 0)) if "id_tipo_cliente" in lcols else 0
                id_comuna = _as_int(_row_get(r, "id_comuna", 0)) if "id_comuna" in lcols else 0

                if id_marca not in marca_ids:
                    id_marca = 0
                if id_estado not in estado_ids:
                    id_estado = 0
                if id_tipo_cliente not in tipo_ids:
                    id_tipo_cliente = 0
                if id_comuna not in comuna_ids:
                    id_comuna = 0

                fecha_ingreso = _to_iso_date(_row_get(r, "fecha_ingreso", None)) if "fecha_ingreso" in lcols else ""
                if not fecha_ingreso:
                    fecha_ingreso = date.today().isoformat()

                pg.execute(
                    text("""
                        INSERT INTO leads(
                          id_lead,nombre_cliente,email,telefono,id_marca,id_estado,fecha_evento,monto_cotizado,codigo_cliente,
                          fecha_ingreso,id_tipo_cliente,plataforma,id_comuna,direccion,notas,num_cotizacion,
                          pendiente_agendar,
                          pre_title,pre_start,pre_end,pre_location,pre_telefono,pre_direccion,pre_products_text,pre_montaje_text,pre_ops,pre_description,
                          calendar_start,calendar_end,calendar_html_link,
                          agenda_approved_by,agenda_approved_at
                        )
                        VALUES(
                          :id,:nc,:em,:tel,:idm,:ide,
                          NULLIF(:fe,'')::date,
                          :m,:cc,
                          NULLIF(:fi,'')::date,
                          :itc,:plat,:ic,:dir,:notas,:numc,
                          :pend,
                          :pt, NULLIF(:ps,'')::timestamp, NULLIF(:pe,'')::timestamp, :pl, :ptel, :pdir, :pp, :pm, :ops, :pdesc,
                          NULLIF(:cs,'')::timestamp, NULLIF(:ce,'')::timestamp, :chl,
                          :a_by, NULLIF(:a_at,'')::timestamptz
                        )
                        ON CONFLICT (id_lead) DO NOTHING
                    """),
                    {
                        "id": id_lead,
                        "nc": nombre_cliente,
                        "em": _row_get(r, "email", None),
                        "tel": _row_get(r, "telefono", None),
                        "idm": id_marca,
                        "ide": id_estado,
                        "fe": _to_iso_date(_row_get(r, "fecha_evento", None)) if "fecha_evento" in lcols else "",
                        "m": _as_float(_row_get(r, "monto_cotizado", 0), 0),
                        "cc": _row_get(r, "codigo_cliente", None) if "codigo_cliente" in lcols else None,
                        "fi": fecha_ingreso,
                        "itc": id_tipo_cliente,
                        "plat": _row_get(r, "plataforma", None) if "plataforma" in lcols else None,
                        "ic": id_comuna,
                        "dir": _row_get(r, "direccion", None) if "direccion" in lcols else None,
                        "notas": _row_get(r, "notas", None) if "notas" in lcols else None,
                        "numc": _row_get(r, "num_cotizacion", None) if "num_cotizacion" in lcols else None,
                        "pend": _as_bool(_row_get(r, "pendiente_agendar", None), default=False) if "pendiente_agendar" in lcols else False,
                        "pt": _row_get(r, "pre_title", None) if "pre_title" in lcols else None,
                        "ps": _to_iso_ts(_row_get(r, "pre_start", None)) or "",
                        "pe": _to_iso_ts(_row_get(r, "pre_end", None)) or "",
                        "pl": _row_get(r, "pre_location", None) if "pre_location" in lcols else None,
                        "ptel": _row_get(r, "pre_telefono", None) if "pre_telefono" in lcols else None,
                        "pdir": _row_get(r, "pre_direccion", None) if "pre_direccion" in lcols else None,
                        "pp": _row_get(r, "pre_products_text", None) if "pre_products_text" in lcols else None,
                        "pm": _row_get(r, "pre_montaje_text", None) if "pre_montaje_text" in lcols else None,
                        "ops": _as_int(_row_get(r, "pre_ops", 0), 0),
                        "pdesc": _row_get(r, "pre_description", None) if "pre_description" in lcols else None,
                        "cs": _to_iso_ts(_row_get(r, "calendar_start", None)) or "",
                        "ce": _to_iso_ts(_row_get(r, "calendar_end", None)) or "",
                        "chl": _row_get(r, "calendar_html_link", None) if "calendar_html_link" in lcols else None,
                        "a_by": _row_get(r, "agenda_approved_by", None) if "agenda_approved_by" in lcols else None,
                        "a_at": str(_row_get(r, "agenda_approved_at", "") or ""),
                    },
                )

        _set_sequences(pg)

    print("OK: migración completada -> PostgreSQL (BDGD)")


if __name__ == "__main__":
    main()
