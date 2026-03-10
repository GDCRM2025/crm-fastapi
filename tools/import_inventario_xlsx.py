#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
import unicodedata
from typing import Dict, List

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


def _ensure_tables(conn):
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS inv_categorias (
                id_categoria SERIAL PRIMARY KEY,
                nombre TEXT UNIQUE NOT NULL
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS inv_unidades (
                id_unidad SERIAL PRIMARY KEY,
                nombre TEXT UNIQUE NOT NULL,
                abreviatura TEXT
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS inv_proveedores (
                id_proveedor SERIAL PRIMARY KEY,
                nombre TEXT UNIQUE NOT NULL,
                direccion TEXT,
                telefono TEXT,
                email TEXT,
                contacto TEXT,
                created_at TIMESTAMP DEFAULT now(),
                updated_at TIMESTAMP DEFAULT now()
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS inv_productos (
                id_producto SERIAL PRIMARY KEY,
                sku TEXT UNIQUE NOT NULL,
                nombre TEXT NOT NULL,
                id_categoria INT REFERENCES inv_categorias(id_categoria),
                id_unidad INT REFERENCES inv_unidades(id_unidad),
                id_proveedor INT REFERENCES inv_proveedores(id_proveedor),
                precio NUMERIC(12,2),
                pack_cantidad NUMERIC(12,3),
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT now(),
                updated_at TIMESTAMP DEFAULT now()
            )
            """
        )
    )
    conn.execute(text("ALTER TABLE inv_productos ADD COLUMN IF NOT EXISTS pack_cantidad NUMERIC(12,3)"))
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS inv_stock (
                id_producto INT PRIMARY KEY REFERENCES inv_productos(id_producto) ON DELETE CASCADE,
                stock_inicial NUMERIC(12,3),
                stock_bodega NUMERIC(12,3),
                stock_delivery NUMERIC(12,3),
                stock_cocina NUMERIC(12,3),
                stock_total NUMERIC(12,3),
                stock_actual NUMERIC(12,3) DEFAULT 0,
                stock_min NUMERIC(12,3),
                stock_max NUMERIC(12,3),
                updated_at TIMESTAMP DEFAULT now()
            )
            """
        )
    )
    conn.execute(text("ALTER TABLE inv_stock ADD COLUMN IF NOT EXISTS stock_inicial NUMERIC(12,3)"))
    conn.execute(text("ALTER TABLE inv_stock ADD COLUMN IF NOT EXISTS stock_bodega NUMERIC(12,3)"))
    conn.execute(text("ALTER TABLE inv_stock ADD COLUMN IF NOT EXISTS stock_delivery NUMERIC(12,3)"))
    conn.execute(text("ALTER TABLE inv_stock ADD COLUMN IF NOT EXISTS stock_cocina NUMERIC(12,3)"))
    conn.execute(text("ALTER TABLE inv_stock ADD COLUMN IF NOT EXISTS stock_total NUMERIC(12,3)"))
    conn.commit()


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join([c for c in s if not unicodedata.combining(c)])
    s = re.sub(r"[^a-zA-Z0-9]+", " ", s).strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def _find_col(header: List[str], names: List[str]) -> int | None:
    wanted = {_norm(n) for n in names}
    for idx, h in enumerate(header):
        if _norm(h) in wanted:
            return idx
    return None


def _num(val: str | None) -> float | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    # Normaliza formato CL: 1.234,56 -> 1234.56
    s = s.replace(" ", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        # Si sólo hay puntos y el último bloque es de 3 dígitos, es miles -> remover puntos
        if "." in s:
            parts = s.split(".")
            if len(parts[-1]) == 3 and all(p.isdigit() for p in parts):
                s = "".join(parts)
    try:
        return float(s)
    except Exception:
        return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--file", default="data/inventario_greendiamond.xlsx")
    args = p.parse_args()

    if not os.path.exists(args.file):
        raise SystemExit(f"No existe: {args.file}")

    z = zipfile.ZipFile(args.file)
    shared = _read_shared_strings(z)
    sheets = _sheet_map(z)
    if "PROVEEDORES" not in sheets or "PRODUCTOS" not in sheets:
        raise SystemExit("No encontré hojas PROVEEDORES / PRODUCTOS")

    prov_rows = _parse_sheet(z, sheets["PROVEEDORES"], shared)
    prod_rows = _parse_sheet(z, sheets["PRODUCTOS"], shared)

    prov_header = prov_rows[0]
    prov_data = prov_rows[1:]
    prod_header = prod_rows[0]
    prod_data = prod_rows[1:]

    # Header indexes
    ph = {
        "nombre": _find_col(prov_header, ["nombre"]),
        "direccion": _find_col(prov_header, ["direccion", "dirección"]),
        "telefono": _find_col(prov_header, ["telefono", "teléfono"]),
        "email": _find_col(prov_header, ["e-mail", "email", "e mail", "mail"]),
        "contacto": _find_col(prov_header, ["contacto"]),
    }
    prh = {
        "codigo": _find_col(prod_header, ["codigo", "sku"]),
        "producto": _find_col(prod_header, ["producto", "nombre"]),
        "categoria": _find_col(prod_header, ["categoria", "categoría"]),
        "unidad": _find_col(prod_header, ["unidad de medida", "unidad", "unidad medida", "unidad_medida"]),
        "proveedor": _find_col(prod_header, ["proveedor", "proveedor principal"]),
        "precio": _find_col(prod_header, ["precio", "valor", "costo"]),
    }

    inserted = {"proveedores": 0, "categorias": 0, "unidades": 0, "productos": 0}

    with get_connection() as conn:
        _ensure_tables(conn)

        # proveedores
        for row in prov_data:
            if not row:
                continue
            nombre_idx = ph.get("nombre")
            if nombre_idx is None or nombre_idx >= len(row):
                continue
            nombre = (row[nombre_idx] if nombre_idx < len(row) else "") or ""
            nombre = str(nombre).strip()
            if not nombre:
                continue
            direccion = (row[ph.get("direccion")] if ph.get("direccion") is not None and ph.get("direccion") < len(row) else "") or ""
            telefono = (row[ph.get("telefono")] if ph.get("telefono") is not None and ph.get("telefono") < len(row) else "") or ""
            email = (row[ph.get("email")] if ph.get("email") is not None and ph.get("email") < len(row) else "") or ""
            contacto = (row[ph.get("contacto")] if ph.get("contacto") is not None and ph.get("contacto") < len(row) else "") or ""
            conn.execute(
                text(
                    """
                    INSERT INTO inv_proveedores(nombre, direccion, telefono, email, contacto)
                    VALUES (:n,:d,:t,:e,:c)
                    ON CONFLICT (nombre) DO UPDATE
                    SET direccion=EXCLUDED.direccion,
                        telefono=EXCLUDED.telefono,
                        email=EXCLUDED.email,
                        contacto=EXCLUDED.contacto,
                        updated_at=now()
                    """
                ),
                {
                    "n": nombre,
                    "d": str(direccion).strip() or None,
                    "t": str(telefono).strip() or None,
                    "e": str(email).strip() or None,
                    "c": str(contacto).strip() or None,
                },
            )
            inserted["proveedores"] += 1

        # categorias/unidades/productos
        for row in prod_data:
            if not row or len(row) < 2:
                continue
            sku_idx = prh.get("codigo")
            nombre_idx = prh.get("producto")
            if sku_idx is None or nombre_idx is None:
                continue
            sku = str(row[sku_idx] if sku_idx < len(row) else "").strip()
            nombre = str(row[nombre_idx] if nombre_idx < len(row) else "").strip()
            categoria = str(row[prh.get("categoria")] if prh.get("categoria") is not None and prh.get("categoria") < len(row) else "").strip()
            unidad = str(row[prh.get("unidad")] if prh.get("unidad") is not None and prh.get("unidad") < len(row) else "").strip()
            proveedor = str(row[prh.get("proveedor")] if prh.get("proveedor") is not None and prh.get("proveedor") < len(row) else "").strip()
            precio = _num(row[prh.get("precio")] if prh.get("precio") is not None and prh.get("precio") < len(row) else None)

            if not sku or not nombre:
                continue

            # categoria
            if categoria:
                conn.execute(
                    text("INSERT INTO inv_categorias(nombre) VALUES (:n) ON CONFLICT (nombre) DO NOTHING"),
                    {"n": categoria},
                )
                inserted["categorias"] += 1
            # unidad
            if unidad:
                conn.execute(
                    text(
                        """
                        INSERT INTO inv_unidades(nombre, abreviatura)
                        VALUES (:n,:a)
                        ON CONFLICT (nombre) DO UPDATE SET abreviatura=EXCLUDED.abreviatura
                        """
                    ),
                    {"n": unidad, "a": unidad},
                )
                inserted["unidades"] += 1
            # proveedor
            if proveedor:
                conn.execute(
                    text("INSERT INTO inv_proveedores(nombre) VALUES (:n) ON CONFLICT (nombre) DO NOTHING"),
                    {"n": proveedor},
                )

            # fetch ids
            cat_id = None
            if categoria:
                cat_id = conn.execute(
                    text("SELECT id_categoria FROM inv_categorias WHERE nombre=:n"),
                    {"n": categoria},
                ).scalar()
            uni_id = None
            if unidad:
                uni_id = conn.execute(
                    text("SELECT id_unidad FROM inv_unidades WHERE nombre=:n"),
                    {"n": unidad},
                ).scalar()
            prov_id = None
            if proveedor:
                prov_id = conn.execute(
                    text("SELECT id_proveedor FROM inv_proveedores WHERE nombre=:n"),
                    {"n": proveedor},
                ).scalar()

            conn.execute(
                text(
                    """
                    INSERT INTO inv_productos(sku, nombre, id_categoria, id_unidad, id_proveedor, precio, is_active)
                    VALUES (:sku,:n,:cat,:uni,:prov,:precio,TRUE)
                    ON CONFLICT (sku) DO UPDATE
                    SET nombre=EXCLUDED.nombre,
                        id_categoria=EXCLUDED.id_categoria,
                        id_unidad=EXCLUDED.id_unidad,
                        id_proveedor=EXCLUDED.id_proveedor,
                        precio=EXCLUDED.precio,
                        updated_at=now()
                    """
                ),
                {
                    "sku": sku,
                    "n": nombre,
                    "cat": cat_id,
                    "uni": uni_id,
                    "prov": prov_id,
                    "precio": precio,
                },
            )
            inserted["productos"] += 1

        # ensure stock rows
        conn.execute(
            text(
                """
                INSERT INTO inv_stock(id_producto, stock_actual)
                SELECT p.id_producto, 0
                FROM inv_productos p
                ON CONFLICT (id_producto) DO NOTHING
                """
            )
        )

        # stock actual desde hoja STOCKS (si existe)
        if "STOCKS" in sheets:
            stock_rows = _parse_sheet(z, sheets["STOCKS"], shared)
            if stock_rows:
                # busca fila header real (la que contenga "producto" + "stock total" o similar)
                header = stock_rows[0]
                header_idx = 0
                for i, row in enumerate(stock_rows[:8]):
                    if _find_col(row, ["producto"]) is not None and (
                        _find_col(row, ["stock total", "stock general", "stock bodega"]) is not None
                    ):
                        header = row
                        header_idx = i
                        break
                code_idx = _find_col(header, ["codigo", "sku"]) or 0
                prod_idx = _find_col(header, ["producto"]) or 1
                stock_ini_idx = _find_col(header, ["inventario inicial", "stock inicial", "inicio"])
                stock_bod_idx = _find_col(header, ["stock bodega", "bodega"])
                stock_del_idx = _find_col(header, ["stock delivery", "delivery"])
                stock_coc_idx = _find_col(header, ["stock cocina", "cocina"])
                stock_tot_idx = _find_col(header, ["stock total", "stock general", "stock"]) or 3
                max_idx = _find_col(header, ["maximo", "máximo", "max"]) or None
                min_idx = _find_col(header, ["minimo", "mínimo", "min"]) or None
                for row in stock_rows[header_idx + 1:]:
                    if not row:
                        continue
                    sku = str(row[code_idx] if code_idx < len(row) else "").strip()
                    nombre = str(row[prod_idx] if prod_idx < len(row) else "").strip()
                    stock_ini = _num(row[stock_ini_idx] if stock_ini_idx is not None and stock_ini_idx < len(row) else None)
                    stock_bod = _num(row[stock_bod_idx] if stock_bod_idx is not None and stock_bod_idx < len(row) else None)
                    stock_del = _num(row[stock_del_idx] if stock_del_idx is not None and stock_del_idx < len(row) else None)
                    stock_coc = _num(row[stock_coc_idx] if stock_coc_idx is not None and stock_coc_idx < len(row) else None)
                    stock_tot = _num(row[stock_tot_idx] if stock_tot_idx is not None and stock_tot_idx < len(row) else None)
                    if not sku and not nombre:
                        continue
                    prod_id = None
                    if sku:
                        prod_id = conn.execute(
                            text("SELECT id_producto FROM inv_productos WHERE sku=:s"), {"s": sku}
                        ).scalar()
                    if not prod_id and nombre:
                        prod_id = conn.execute(
                            text("SELECT id_producto FROM inv_productos WHERE nombre=:n"), {"n": nombre}
                        ).scalar()
                    if prod_id is None:
                        continue
                    stock_max = _num(row[max_idx] if max_idx is not None and max_idx < len(row) else None)
                    stock_min = _num(row[min_idx] if min_idx is not None and min_idx < len(row) else None)
                    if all(x is None for x in [stock_ini, stock_bod, stock_del, stock_coc, stock_tot, stock_max, stock_min]):
                        continue
                    total = stock_tot
                    if total is None:
                        parts = [stock_bod, stock_del, stock_coc]
                        if any(p is not None for p in parts):
                            total = sum([p or 0 for p in parts])
                        else:
                            total = stock_ini
                    conn.execute(
                        text(
                            """
                            UPDATE inv_stock
                            SET stock_inicial=COALESCE(:si, stock_inicial),
                                stock_bodega=COALESCE(:sb, stock_bodega),
                                stock_delivery=COALESCE(:sd, stock_delivery),
                                stock_cocina=COALESCE(:sc, stock_cocina),
                                stock_total=COALESCE(:st, stock_total),
                                stock_actual=COALESCE(:st, stock_actual),
                                stock_max=COALESCE(:mx, stock_max),
                                stock_min=COALESCE(:mn, stock_min),
                                updated_at=now()
                            WHERE id_producto=:id
                            """
                        ),
                        {
                            "si": stock_ini,
                            "sb": stock_bod,
                            "sd": stock_del,
                            "sc": stock_coc,
                            "st": total,
                            "mx": stock_max,
                            "mn": stock_min,
                            "id": prod_id,
                        },
                    )
        conn.commit()

    print(inserted)


if __name__ == "__main__":
    main()
