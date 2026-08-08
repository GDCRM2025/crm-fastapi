#!/usr/bin/env python3
"""
Importa recetas y productos desde data/recetas y productos.xlsx
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from typing import Dict, List, Tuple

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.core.db import get_connection
from sqlalchemy import text


NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _read_shared_strings(z: zipfile.ZipFile) -> List[str]:
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    out = []
    for si in root.findall("s:si", NS):
        texts = [t.text or "" for t in si.findall(".//s:t", NS)]
        out.append("".join(texts))
    return out


def _sheet_map(z: zipfile.ZipFile) -> Dict[str, str]:
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


_cell_re = re.compile(r"([A-Z]+)([0-9]+)")


def _col_to_idx(col: str) -> int:
    idx = 0
    for c in col:
        idx = idx * 26 + (ord(c) - 64)
    return idx - 1


def _parse_sheet(z: zipfile.ZipFile, path: str, shared: List[str]) -> List[List[str]]:
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


def _unit_from_header(h: str) -> str:
    h = (h or "").lower()
    if "grs" in h or "gr " in h or "grs." in h:
        return "GRS"
    if "ml" in h or "cc" in h:
        return "ML"
    if "cant" in h or "cant." in h:
        return "UN"
    return "UN"


def _ensure_tables(conn):
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS recetas (
                id_receta SERIAL PRIMARY KEY,
                producto TEXT NOT NULL,
                marca TEXT,
                rendimiento NUMERIC(10,2),
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT now(),
                updated_at TIMESTAMP DEFAULT now()
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS receta_items (
                id_item SERIAL PRIMARY KEY,
                id_receta INT NOT NULL REFERENCES recetas(id_receta) ON DELETE CASCADE,
                ingrediente TEXT NOT NULL,
                cantidad NUMERIC(12,4),
                unidad TEXT,
                costo_unitario NUMERIC(12,4),
                created_at TIMESTAMP DEFAULT now()
            )
            """
        )
    )
    try:
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_receta_item ON receta_items(id_receta, ingrediente)"
            )
        )
    except Exception:
        pass
    conn.commit()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--file", default="data/recetas y productos.xlsx")
    args = p.parse_args()

    path = args.file
    if not os.path.exists(path):
        raise SystemExit(f"No existe: {path}")

    z = zipfile.ZipFile(path)
    shared = _read_shared_strings(z)
    sheets = _sheet_map(z)
    if "Productos" not in sheets or "Recetas" not in sheets:
        raise SystemExit("No encontré hojas Productos/Recetas")

    productos_rows = _parse_sheet(z, sheets["Productos"], shared)
    recetas_rows = _parse_sheet(z, sheets["Recetas"], shared)

    # Build maps
    prod_header = productos_rows[0]
    rec_header = recetas_rows[0]
    prod_data = productos_rows[1:]
    rec_data = recetas_rows[1:]

    # Map producto -> ingredients with qty/unit from Productos
    productos_map: Dict[str, List[Tuple[str, float | None, str]]] = {}
    for row in prod_data:
        if not row or not row[0]:
            continue
        prod = str(row[0]).strip()
        if not prod:
            continue
        items = []
        # pair-wise scan (ingredient col, qty col)
        for i in range(1, len(prod_header) - 1, 2):
            ing_name = prod_header[i]
            qty_header = prod_header[i + 1] if i + 1 < len(prod_header) else ""
            ing_val = row[i] if i < len(row) else ""
            qty_val = row[i + 1] if i + 1 < len(row) else ""
            ing = (ing_val or "").strip()
            if not ing:
                continue
            try:
                qty = float(str(qty_val).replace(",", "."))
            except Exception:
                qty = None
            unit = _unit_from_header(qty_header)
            items.append((ing, qty, unit))
        productos_map[prod] = items

    # Recetas sheet (ingredients w/out qty)
    recetas_map: Dict[str, List[str]] = {}
    for row in rec_data:
        if not row or not row[0]:
            continue
        prod = str(row[0]).strip()
        if not prod:
            continue
        ings = [str(x).strip() for x in row[1:] if str(x).strip()]
        recetas_map[prod] = ings

    inserted = 0
    items_inserted = 0

    with get_connection() as conn:
        _ensure_tables(conn)
        for prod, items in productos_map.items():
            rec = conn.execute(
                text("SELECT id_receta FROM recetas WHERE producto=:p LIMIT 1"),
                {"p": prod},
            ).fetchone()
            if rec:
                rec_id = int(rec[0])
            else:
                rec_id = conn.execute(
                    text("INSERT INTO recetas(producto, is_active) VALUES (:p, TRUE) RETURNING id_receta"),
                    {"p": prod},
                ).fetchone()[0]
                inserted += 1

            # insert items from productos
            for ing, qty, unit in items:
                conn.execute(
                    text(
                        """
                        INSERT INTO receta_items(id_receta, ingrediente, cantidad, unidad)
                        VALUES (:r, :i, :c, :u)
                        ON CONFLICT (id_receta, ingrediente) DO UPDATE
                        SET cantidad=EXCLUDED.cantidad,
                            unidad=EXCLUDED.unidad
                        """
                    ),
                    {"r": rec_id, "i": ing, "c": qty, "u": unit},
                )
                items_inserted += 1

            # merge extras from Recetas (sin qty)
            extras = recetas_map.get(prod, [])
            for ing in extras:
                conn.execute(
                    text(
                        """
                        INSERT INTO receta_items(id_receta, ingrediente, cantidad, unidad)
                        VALUES (:r, :i, NULL, 'UN')
                        ON CONFLICT (id_receta, ingrediente) DO NOTHING
                        """
                    ),
                    {"r": rec_id, "i": ing},
                )
        conn.commit()

    print({"recetas_inserted": inserted, "items_upserted": items_inserted})


if __name__ == "__main__":
    main()
