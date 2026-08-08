from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ensure project imports
BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from sqlalchemy import text  # type: ignore
from google.oauth2 import service_account  # type: ignore
from googleapiclient.discovery import build  # type: ignore
from googleapiclient.errors import HttpError  # type: ignore

from backend.core.db import get_connection
from backend.core.quote_assets import normalize_marca


SHEETS = [
    {
        "brand": "GOURMET",
        "sheet_id": "1W6VwZ7QHrs1EyPs4HxuALHw_jQwuEewVlPsuiE66_gs",
        "tab": "CRM GOURMET",
    },
    {
        "brand": "CAMALEON",
        "sheet_id": "1wDKnpbacYPAk9XdUGYo3Nzs4twicT7QaeSkqZmpRbhc",
        "tab": "CRM CAMALEON",
    },
    {
        "brand": "EXPRESS",
        "sheet_id": "12CClgbF1II-AMQe5z3HJM8eMLqeq_4aABWEg_o-mAiA",
        "tab": "CRM EXPRESS",
    },
    {
        "brand": "DEL SABOR",
        "sheet_id": "1K7NaGXCqO51cSL7cAJYKBxHRhCVno0Kjg4L2--E3vpY",
        "tab": "CRM DEL SABOR",
    },
]

# For some hosting environments, psycopg2 can end up using SQL_ASCII client decoding,
# which breaks when reading UTF-8 text (e.g. comunas with Ñ/á/é). Force UTF-8.
os.environ.setdefault("PGCLIENTENCODING", "UTF8")


def norm(s: str) -> str:
    if s is None:
        return ""
    s = str(s).strip()
    if not s:
        return ""
    s = normalize_marca(s)
    return re.sub(r"\s+", " ", s).strip().upper()


def parse_date(s: Any) -> Optional[date]:
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            continue
    # try datetime format
    for fmt in ("%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            continue
    return None


def parse_money(s: Any) -> float:
    if s is None:
        return 0.0
    s = str(s).strip()
    if not s:
        return 0.0
    # remove currency symbols
    s = s.replace("$", "").replace("CLP", "").replace("clp", "").strip()
    # keep digits and separators
    s = re.sub(r"[^0-9.,-]", "", s)
    if not s:
        return 0.0
    # if both comma and dot, decide decimal by last occurrence
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "." in s and "," not in s:
        # treat dot as thousands if pattern 1.234.567
        if re.match(r"^\d{1,3}(\.\d{3})+$", s):
            s = s.replace(".", "")
        # else dot is decimal separator
    elif "," in s and "." not in s:
        # treat comma as thousands if pattern 1,234,567
        if re.match(r"^\d{1,3}(,\d{3})+$", s):
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    try:
        return float(s)
    except Exception:
        return 0.0


def apply_monto_scale(value: float, scale: float, threshold: float) -> float:
    if not value or scale <= 1:
        return float(value or 0.0)
    try:
        v = float(value)
    except Exception:
        return 0.0
    if v < float(threshold):
        return v * float(scale)
    return v


def build_service(account_json: str):
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds = service_account.Credentials.from_service_account_file(account_json, scopes=scopes)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def get_sheet_values(service, sheet_id: str, tab: str) -> List[List[str]]:
    rng = f"'{tab}'!A:Z"
    result = service.spreadsheets().values().get(
        spreadsheetId=sheet_id,
        range=rng,
        valueRenderOption="UNFORMATTED_VALUE",
        dateTimeRenderOption="FORMATTED_STRING",
    ).execute()
    return result.get("values", [])


def map_rows(values: List[List[str]]) -> List[Dict[str, str]]:
    if not values:
        return []
    header = [h.strip() for h in values[0]]
    rows = []
    for r in values[1:]:
        row = {}
        for i, h in enumerate(header):
            if i < len(r):
                v = r[i]
                if isinstance(v, str):
                    row[h] = v.strip()
                else:
                    row[h] = str(v).strip()
            else:
                row[h] = ""
        rows.append(row)
    return rows


def _norm_key(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]", "", s)
    return s


def getv(row: Dict[str, str], *keys: str) -> str:
    if not row:
        return ""
    # direct match
    for k in keys:
        if k in row and row[k] is not None:
            return row[k]
    # normalized match
    norm_map = { _norm_key(k): k for k in row.keys() }
    for k in keys:
        nk = _norm_key(k)
        if nk in norm_map:
            return row.get(norm_map[nk], "")
    # fallback: first header containing all tokens
    for k in keys:
        nk = _norm_key(k)
        for hk, hv in row.items():
            if nk and nk in _norm_key(hk):
                return hv
    return ""


def load_maps(conn) -> Tuple[Dict[str, int], Dict[str, int], Dict[str, int], Dict[str, int]]:
    marcas = {}
    for r in conn.execute(text("SELECT id_marca, nombre, marca FROM public.marcas")).fetchall():
        mid, nombre, marca = r
        if nombre:
            marcas[norm(nombre)] = int(mid)
        if marca:
            marcas[norm(marca)] = int(mid)

    comunas = {}
    for r in conn.execute(text("SELECT id_comuna, nombre FROM public.comunas")).fetchall():
        cid, nombre = r
        if nombre:
            comunas[norm(nombre)] = int(cid)

    estados = {}
    for r in conn.execute(text("SELECT id_estado, nombre FROM public.estados_lead")).fetchall():
        eid, nombre = r
        if nombre:
            estados[norm(nombre)] = int(eid)

    tipos = {}
    for r in conn.execute(text("SELECT id_tipo_cliente, tipo FROM public.tipos_cliente")).fetchall():
        tid, tipo = r
        if tipo:
            tipos[norm(tipo)] = int(tid)

    return marcas, comunas, estados, tipos


def should_include(fecha_ingreso: Optional[date], fecha_evento: Optional[date]) -> bool:
    # Regla de negocio: traer TODO lo que tenga evento 2026, sin importar fecha de ingreso.
    if fecha_evento and fecha_evento.year == 2026:
        return True
    # Fallback: si no hay fecha_evento, incluimos si el lead se ingresó en 2026.
    if fecha_ingreso and fecha_ingreso.year == 2026:
        return True
    return False


def build_notes(row: Dict[str, str]) -> str:
    fields = [
        ("Categoria Clientes", "Categoria Clientes"),
        ("Conversar", "Conversar"),
        ("Seguimiento 1", "Seguimiento 1"),
        ("Fecha de Seguimiento", "Fecha de Seguimiento"),
        ("Motivo de Evento", "Motivo de Evento"),
        ("Creado Por", "Creado Por"),
        ("Fecha De Cierre", "Fecha De Cierre"),
        ("Nota sobre el cliente", "Nota"),
        ("ID", "ID Origen"),
    ]
    parts = []
    for key, label in fields:
        val = (getv(row, key) or "").strip()
        if val:
            if label == "ID Origen" and re.match(r"^\d+(?:[.,]0+)?$", val):
                val = val.split(".")[0].split(",")[0]
            parts.append(f"{label}: {val}")
    return "\n".join(parts).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--service-account", required=True, help="Path a JSON de service account")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="0 = sin límite")
    ap.add_argument("--monto-scale", type=float, default=1.0, help="Multiplicar montos pequeños (ej: 1000)")
    ap.add_argument("--monto-threshold", type=float, default=10000.0, help="Solo escala si monto < este umbral")
    ap.add_argument(
        "--dedupe",
        action="store_true",
        help="Evita duplicados (por 'ID' origen si existe; si no, por email+telefono+fecha_evento+marca).",
    )
    args = ap.parse_args()

    service = build_service(args.service_account)

    report = {
        "inserted": 0,
        "skipped": 0,
        "by_brand": {},
        "errors": {},
    }

    with get_connection() as conn:
        # Force UTF-8 at session + driver level (prevents UnicodeDecodeError: 'ascii' codec ...).
        try:
            raw = getattr(getattr(conn, "connection", None), "connection", None)
            if raw and hasattr(raw, "set_client_encoding"):
                raw.set_client_encoding("UTF8")
        except Exception:
            pass
        try:
            conn.execute(text("SET client_encoding TO 'UTF8'"))
        except Exception:
            pass

        marcas_map, comunas_map, estados_map, tipos_map = load_maps(conn)
        estado_nuevo = estados_map.get(norm("NUEVO"), 1)

        for cfg in SHEETS:
            brand = cfg["brand"]
            try:
                values = get_sheet_values(service, cfg["sheet_id"], cfg["tab"])
            except HttpError as e:
                # Most common: 403 "The caller does not have permission" (sheet not shared with service account).
                # We continue with other brands and include a clear hint in the report.
                report["errors"][brand] = {
                    "sheet_id": cfg["sheet_id"],
                    "tab": cfg["tab"],
                    "status": getattr(e, "resp", None).status if getattr(e, "resp", None) else None,
                    "error": str(e),
                    "hint": "Comparte esta Google Sheet con el client_email del service account (Viewer) y reintenta.",
                }
                continue
            except Exception as e:
                report["errors"][brand] = {
                    "sheet_id": cfg["sheet_id"],
                    "tab": cfg["tab"],
                    "status": None,
                    "error": f"{type(e).__name__}: {e}",
                    "hint": "Error leyendo la Sheet. Revisa ID/tab y permisos del service account.",
                }
                continue
            rows = map_rows(values)
            out_rows = []

            for row in rows:
                if args.limit and len(out_rows) >= args.limit:
                    break
                cliente = getv(row, "Nombre Cliente", "Cliente", "Nombre").strip()
                if not cliente:
                    report["skipped"] += 1
                    continue

                fecha_ingreso = parse_date(getv(row, "Fecha de Ingreso", "Fecha Ingreso"))
                fecha_evento = parse_date(getv(row, "Fecha Evento", "Fecha de Evento", "Fecha"))
                if not should_include(fecha_ingreso, fecha_evento):
                    report["skipped"] += 1
                    continue

                marca_name = (getv(row, "Marca") or brand).strip()
                id_marca = marcas_map.get(norm(marca_name))
                id_comuna = comunas_map.get(norm(getv(row, "Comuna") or ""))
                id_tipo_cliente = tipos_map.get(norm(getv(row, "Tipo Cliente", "TipoCliente") or ""))
                estado_raw = getv(row, "Estado") or ""
                estado_key = norm(estado_raw)
                estado_alias = {
                    norm("PENDIENTE"): "NUEVO",
                    norm("EN NEGOCIACION"): "CONTACTADO",
                }
                estado_target = estado_alias.get(estado_key, estado_raw)
                id_estado = estados_map.get(norm(estado_target)) or estado_nuevo

                created_at = None
                if fecha_ingreso:
                    created_at = datetime.combine(fecha_ingreso, datetime.min.time())
                updated_at = created_at or datetime.utcnow()

                # ID estable del origen (si existe en la planilla). Esto nos permite evitar duplicados al reimportar.
                origin_id_raw = (getv(row, "ID", "Id", "ID Origen") or "").strip()
                origin_id = ""
                if origin_id_raw:
                    # normaliza "123.0" / "123,0" / "123"
                    if re.match(r"^\d+(?:[.,]0+)?$", origin_id_raw):
                        origin_id = origin_id_raw.split(".")[0].split(",")[0]
                    else:
                        origin_id = origin_id_raw

                notas = build_notes(row)

                data = {
                    "cliente": cliente,
                    "email": (getv(row, "E-Mail", "Email", "Correo") or "").strip() or None,
                    "telefono": (getv(row, "Telefono", "Teléfono", "Fono", "Celular") or "").strip() or None,
                    "direccion": None,
                    "id_marca": id_marca,
                    "id_estado": id_estado,
                    "id_comuna": id_comuna,
                    "id_tipo_cliente": id_tipo_cliente,
                    "fecha_evento": fecha_evento,
                    "monto_cotizado": apply_monto_scale(
                        parse_money(getv(row, "Monto Cotizado", "Monto") or ""),
                        args.monto_scale,
                        args.monto_threshold,
                    ),
                    "plataforma": (getv(row, "Plataforma") or "").strip() or None,
                    "notas": notas or None,
                    "num_cotizacion": int(getv(row, "Nro Cotizacion", "Nro Cotización") or 0) if str(getv(row, "Nro Cotizacion", "Nro Cotización") or "").isdigit() else None,
                    "created_at": created_at or datetime.utcnow(),
                    "updated_at": updated_at,
                    "_origin_id": origin_id,
                }
                out_rows.append(data)

            if not out_rows:
                continue

            report["by_brand"][brand] = report["by_brand"].get(brand, 0) + len(out_rows)

            if args.dry_run:
                report["inserted"] += len(out_rows)
                continue

            sql_insert = text(
                """
                INSERT INTO public.leads(
                  cliente,email,telefono,direccion,id_marca,id_estado,id_comuna,id_tipo_cliente,
                  fecha_evento,monto_cotizado,plataforma,notas,num_cotizacion,created_at,updated_at
                ) VALUES (
                  :cliente,:email,:telefono,:direccion,:id_marca,:id_estado,:id_comuna,:id_tipo_cliente,
                  :fecha_evento,:monto_cotizado,:plataforma,:notas,:num_cotizacion,:created_at,:updated_at
                )
                """
            )

            if not args.dedupe:
                # insert in chunks (fast)
                chunk = 500
                for i in range(0, len(out_rows), chunk):
                    rows_chunk = [dict(r, **{"_origin_id": None}) for r in out_rows[i : i + chunk]]
                    for r in rows_chunk:
                        r.pop("_origin_id", None)
                    conn.execute(sql_insert, rows_chunk)
                conn.commit()
                report["inserted"] += len(out_rows)
            else:
                # Dedupe: insert sólo si no existe.
                inserted = 0
                skipped = 0
                for r in out_rows:
                    origin_id = (r.get("_origin_id") or "").strip()
                    # 1) dedupe por ID origen (si existe)
                    if origin_id and r.get("id_marca"):
                        pat = f"%ID Origen: {origin_id}%"
                        ex = conn.execute(
                            text(
                                """
                                SELECT id_lead
                                FROM public.leads
                                WHERE id_marca=:m AND notas ILIKE :p
                                LIMIT 1
                                """
                            ),
                            {"m": int(r["id_marca"]), "p": pat},
                        ).scalar()
                        if ex:
                            skipped += 1
                            continue

                    # 2) fallback: email + telefono + fecha_evento + marca
                    email = (r.get("email") or "").strip().lower()
                    tel = re.sub(r"\\D+", "", (r.get("telefono") or ""))
                    fev = r.get("fecha_evento")
                    mid = r.get("id_marca")
                    if (email or tel) and fev and mid:
                        ex2 = conn.execute(
                            text(
                                """
                                SELECT id_lead
                                FROM public.leads
                                WHERE id_marca=:m
                                  AND fecha_evento=:fe
                                  AND (
                                    (COALESCE(lower(email),'') <> '' AND lower(email)=:e)
                                    OR (COALESCE(regexp_replace(telefono,'\\D','','g'),'') <> '' AND regexp_replace(telefono,'\\D','','g')=:t)
                                  )
                                LIMIT 1
                                """
                            ),
                            {"m": int(mid), "fe": fev, "e": email, "t": tel},
                        ).scalar()
                        if ex2:
                            skipped += 1
                            continue

                    r2 = dict(r)
                    r2.pop("_origin_id", None)
                    conn.execute(sql_insert, r2)
                    inserted += 1

                conn.commit()
                report["inserted"] += inserted
                report["skipped"] += skipped

    # write report
    out_dir = BASE / "data" / "imports"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "leads_import_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
