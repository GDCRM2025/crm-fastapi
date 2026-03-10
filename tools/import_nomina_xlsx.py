#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import sys
import zipfile
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sqlalchemy import text
from backend.core.db import get_connection

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_cell_re = re.compile(r"([A-Z]+)([0-9]+)")


def _col_to_idx(col: str) -> int:
    idx = 0
    for c in col:
        idx = idx * 26 + (ord(c) - 64)
    return idx - 1


def _read_shared_strings(z: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    out = []
    for si in root.findall("s:si", NS):
        texts = [t.text or "" for t in si.findall(".//s:t", NS)]
        out.append("".join(texts))
    return out


def _sheet_map(z: zipfile.ZipFile) -> dict[str, str]:
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    relmap = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in rels.findall("{http://schemas.openxmlformats.org/package/2006/relationships}Relationship")
    }
    sheets = {}
    for sh in wb.findall("s:sheets/s:sheet", NS):
        name = sh.attrib["name"]
        rid = sh.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
        sheets[name] = "xl/" + relmap[rid]
    return sheets


def _parse_sheet(z: zipfile.ZipFile, path: str, shared: list[str]) -> list[list[str]]:
    root = ET.fromstring(z.read(path))
    rows = []
    max_col = 0
    for row in root.findall("s:sheetData/s:row", NS):
        cells = {}
        for c in row.findall("s:c", NS):
            ref = c.attrib.get("r")
            if not ref:
                continue
            m = _cell_re.match(ref)
            if not m:
                continue
            col = _col_to_idx(m.group(1))
            v = c.find("s:v", NS)
            if v is None:
                continue
            val = v.text or ""
            if c.attrib.get("t") == "s":
                try:
                    val = shared[int(val)]
                except Exception:
                    pass
            cells[col] = val
            max_col = max(max_col, col)
        if cells:
            arr = [""] * (max_col + 1)
            for k, v in cells.items():
                if k < len(arr):
                    arr[k] = v
            rows.append(arr)
    return rows


def _normalize_header(h: str) -> str:
    h = (h or "").strip().lower()
    h = re.sub(r"[^a-z0-9]+", "_", h).strip("_")
    return h


def _excel_date(val: str):
    if val is None:
        return None
    if isinstance(val, str) and val.strip() == "":
        return None
    try:
        f = float(val)
    except Exception:
        return None
    # Excel serial date (1900 system)
    base = datetime(1899, 12, 30)
    return (base + timedelta(days=f)).date().isoformat()


def _ensure_table(conn):
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS rrhh_nomina (
              id_nomina SERIAL PRIMARY KEY,
              colaborador TEXT,
              centro_costo TEXT,
              situacion_contractual TEXT,
              sueldo_fijo NUMERIC,
              fecha_inicio DATE,
              hh_liquido NUMERIC,
              hh_diario NUMERIC,
              dias_trabajados NUMERIC,
              a_pagar_liquido NUMERIC,
              fijo_bruto NUMERIC,
              comisiones_brutas NUMERIC,
              pct_imposicion NUMERIC,
              comision_neta NUMERIC,
              comision_no_imponible NUMERIC,
              adelantos NUMERIC,
              total_pagar NUMERIC,
              fecha_vencimiento DATE,
              estado TEXT,
              fecha_regularizacion DATE,
              antiguedad TEXT,
              dias_generados NUMERIC,
              dias_tomados NUMERIC,
              sobrante NUMERIC,
              observaciones TEXT,
              raw JSONB
            )
            """
        )
    )
    conn.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--sheet", default="nomina")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with zipfile.ZipFile(args.file) as z:
        shared = _read_shared_strings(z)
        sheets = _sheet_map(z)
        if args.sheet not in sheets:
            raise SystemExit(f"Hoja no encontrada: {args.sheet}. Disponibles: {list(sheets.keys())}")
        rows = _parse_sheet(z, sheets[args.sheet], shared)

    if not rows:
        raise SystemExit("No hay datos en la hoja.")
    headers = rows[0]
    data_rows = rows[1:]

    # map headers
    hmap = {_normalize_header(h): idx for idx, h in enumerate(headers)}

    def cell(row, key):
        idx = hmap.get(key)
        if idx is None or idx >= len(row):
            return ""
        return row[idx]

    def as_num(v):
        try:
            if v == "" or v is None:
                return None
            return float(v)
        except Exception:
            return None

    items = []
    for r in data_rows:
        nombre = cell(r, "colaborador")
        if not nombre:
            continue
        def clean_date(v):
            if v is None:
                return None
            if isinstance(v, str) and v.strip() == "":
                return None
            return v
        row = {
            "colaborador": nombre,
            "centro_costo": cell(r, "centro_de_costo") or cell(r, "centro_costo"),
            "situacion_contractual": cell(r, "situacion_contractual"),
            "sueldo_fijo": as_num(cell(r, "sueldo_fijo")),
            "fecha_inicio": clean_date(_excel_date(cell(r, "fecha_de_inicio")) or cell(r, "fecha_de_inicio")),
            "hh_liquido": as_num(cell(r, "hh_liquido")),
            "hh_diario": as_num(cell(r, "hh_diario")),
            "dias_trabajados": as_num(cell(r, "dias_trabajados")),
            "a_pagar_liquido": as_num(cell(r, "a_pagar_liquido")),
            "fijo_bruto": as_num(cell(r, "fijo_bruto")),
            "comisiones_brutas": as_num(cell(r, "comisiones_brutas")),
            "pct_imposicion": as_num(cell(r, "imposicion")) or as_num(cell(r, "imposicion_")),
            "comision_neta": as_num(cell(r, "comision_neta")),
            "comision_no_imponible": as_num(cell(r, "comision_o_bono_no_imponible")),
            "adelantos": as_num(cell(r, "adelantos")),
            "total_pagar": as_num(cell(r, "total_a_pagar")) or as_num(cell(r, "total_a_pagar_")),
            "fecha_vencimiento": clean_date(_excel_date(cell(r, "fecha_de_vencimiento")) or cell(r, "fecha_de_vencimiento")),
            "estado": cell(r, "estado"),
            "fecha_regularizacion": clean_date(_excel_date(cell(r, "fecha_regularizacion")) or cell(r, "fecha_regularizacion")),
            "antiguedad": cell(r, "antiguedad"),
            "dias_generados": as_num(cell(r, "dias_generados")),
            "dias_tomados": as_num(cell(r, "dias_tomados")),
            "sobrante": as_num(cell(r, "sobrante")),
            "observaciones": cell(r, "observaciones"),
            "raw": json.dumps({headers[i]: (r[i] if i < len(r) else "") for i in range(len(headers))}, ensure_ascii=False),
        }
        items.append(row)

    if args.dry_run:
        print({"rows": len(items)})
        return

    with get_connection() as conn:
        _ensure_table(conn)
        conn.execute(text("TRUNCATE rrhh_nomina"))
        for it in items:
            conn.execute(
                text(
                    """
                    INSERT INTO rrhh_nomina(
                      colaborador, centro_costo, situacion_contractual, sueldo_fijo, fecha_inicio,
                      hh_liquido, hh_diario, dias_trabajados, a_pagar_liquido, fijo_bruto,
                      comisiones_brutas, pct_imposicion, comision_neta, comision_no_imponible, adelantos,
                      total_pagar, fecha_vencimiento, estado, fecha_regularizacion, antiguedad,
                      dias_generados, dias_tomados, sobrante, observaciones, raw
                    ) VALUES (
                      :colaborador, :centro_costo, :situacion_contractual, :sueldo_fijo, :fecha_inicio,
                      :hh_liquido, :hh_diario, :dias_trabajados, :a_pagar_liquido, :fijo_bruto,
                      :comisiones_brutas, :pct_imposicion, :comision_neta, :comision_no_imponible, :adelantos,
                      :total_pagar, :fecha_vencimiento, :estado, :fecha_regularizacion, :antiguedad,
                      :dias_generados, :dias_tomados, :sobrante, :observaciones, CAST(:raw AS JSONB)
                    )
                    """
                ),
                it,
            )
        conn.commit()
    print({"inserted": len(items)})


if __name__ == "__main__":
    main()
