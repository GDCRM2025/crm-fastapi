#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

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


def _norm(s: str) -> str:
    return (s or "").strip()


def _ensure_table(conn):
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS operadores_allowlist (
              id_allow SERIAL PRIMARY KEY,
              rut TEXT NOT NULL,
              nombre TEXT NOT NULL,
              email TEXT NOT NULL,
              cargo TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'ACTIVO'
            )
            """
        )
    )
    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_operadores_email ON operadores_allowlist(lower(email))"))
    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_operadores_rut ON operadores_allowlist(lower(rut))"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    args = ap.parse_args()

    path = args.file
    if not os.path.exists(path):
        raise SystemExit(f"No existe: {path}")

    with zipfile.ZipFile(path) as z:
        shared = _read_shared_strings(z)
        sheets = _sheet_map(z)
        if not sheets:
            raise SystemExit("No se encontraron hojas")
        sheet_path = list(sheets.values())[0]
        rows = _parse_sheet(z, sheet_path, shared)

    if not rows:
        raise SystemExit("Archivo vacío")

    headers = [(_norm(h) or "").upper() for h in rows[0]]
    data_rows = rows[1:]

    def idx(name):
        try:
            return headers.index(name)
        except ValueError:
            return None

    i_rut = idx("RUT")
    i_nombre = idx("NOMBRE COMPLETO")
    i_email = idx("CORREO ELETRONICO")
    i_cargo = idx("CARGO")
    i_status = idx("STATUS")

    if None in (i_rut, i_nombre, i_email, i_cargo, i_status):
        raise SystemExit(f"Headers inválidos: {headers}")

    inserted = 0
    updated = 0
    with get_connection() as conn:
        _ensure_table(conn)
        for r in data_rows:
            rut = _norm(r[i_rut] if i_rut is not None and i_rut < len(r) else "")
            nombre = _norm(r[i_nombre] if i_nombre is not None and i_nombre < len(r) else "")
            email = _norm(r[i_email] if i_email is not None and i_email < len(r) else "").lower()
            cargo = _norm(r[i_cargo] if i_cargo is not None and i_cargo < len(r) else "")
            status = _norm(r[i_status] if i_status is not None and i_status < len(r) else "") or "ACTIVO"
            if not rut or not email or not nombre:
                continue
            row = conn.execute(
                text("SELECT id_allow FROM operadores_allowlist WHERE lower(email)=:e OR lower(rut)=:r LIMIT 1"),
                {"e": email, "r": rut.lower()},
            ).first()
            if row:
                conn.execute(
                    text(
                        """
                        UPDATE operadores_allowlist
                        SET rut=:rut, nombre=:nombre, email=:email, cargo=:cargo, status=:status
                        WHERE id_allow=:id
                        """
                    ),
                    {
                        "id": row[0],
                        "rut": rut,
                        "nombre": nombre,
                        "email": email,
                        "cargo": cargo,
                        "status": status,
                    },
                )
                updated += 1
            else:
                conn.execute(
                    text(
                        """
                        INSERT INTO operadores_allowlist(rut,nombre,email,cargo,status)
                        VALUES (:rut,:nombre,:email,:cargo,:status)
                        """
                    ),
                    {
                        "rut": rut,
                        "nombre": nombre,
                        "email": email,
                        "cargo": cargo,
                        "status": status,
                    },
                )
                inserted += 1
        conn.commit()

    print({"inserted": inserted, "updated": updated})


if __name__ == "__main__":
    main()
