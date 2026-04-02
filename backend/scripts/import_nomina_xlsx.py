"""
Importa nómina básica (colaboradores) desde `data/nomina.xlsx` hacia tablas RRHH.

Este script NO usa pandas/openpyxl. Lee el XLSX como ZIP (XML) con stdlib, igual que
`import_brochures_products.py`, para mantener compatibilidad en servidores sin deps extra.

Qué carga (mínimo viable):
- rrhh_staff:
  - colaborador, centro_costo, rol (desde columna "Rol"), fecha_ingreso (desde "Fecha de Inicio")
  - ficha JSONB con: cargo, situacion_contractual, sueldo
- rrhh_nomina:
  - colaborador, centro_costo, situacion_contractual, sueldo_fijo, fecha_inicio, hh_liquido, raw JSONB

Compatibilidad:
- Python 3.6+ (NO usa `from __future__ import annotations`)

Uso (recomendado en server, con el python del virtualenv del CRM):
  python3 backend/scripts/import_nomina_xlsx.py --xlsx data/nomina.xlsx --dry-run
  python3 backend/scripts/import_nomina_xlsx.py --xlsx data/nomina.xlsx
"""

import argparse
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import date


NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _load_shared_strings(z):
    try:
        raw = z.read("xl/sharedStrings.xml")
    except Exception:
        return []
    root = ET.fromstring(raw)
    out = []
    for si in root.findall("s:si", NS):
        parts = []
        for t in si.findall(".//s:t", NS):
            parts.append(t.text or "")
        out.append("".join(parts))
    return out


def _cell_to_colrow(cellref):
    col = "".join([c for c in cellref if c.isalpha()])
    row = int("".join([c for c in cellref if c.isdigit()]) or 0)
    n = 0
    for ch in col:
        n = n * 26 + (ord(ch.upper()) - 64)
    return n, row


def _sheet_name_map(z):
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    nsr = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}
    rid_to_target = {}
    for rel in rels.findall("r:Relationship", nsr):
        rid_to_target[rel.get("Id") or ""] = rel.get("Target") or ""

    out = {}
    for sh in wb.findall(".//s:sheets/s:sheet", NS):
        name = sh.get("name") or ""
        rid = sh.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id") or ""
        target = rid_to_target.get(rid, "")
        if target:
            out[name] = "xl/" + target.lstrip("/")
    return out


def _read_sheet_rows(z, sheet_path, shared, max_cols=32, max_rows=5000):
    root = ET.fromstring(z.read(sheet_path))
    cells = {}
    for c in root.findall(".//s:c", NS):
        r = c.get("r")
        if not r:
            continue
        cn, rn = _cell_to_colrow(r)
        if cn > max_cols or rn > max_rows:
            continue
        v = c.find("s:v", NS)
        if v is None:
            continue
        val = v.text or ""
        if c.get("t") == "s" and shared:
            try:
                val = shared[int(val)]
            except Exception:
                pass
        cells[(rn, cn)] = val

    rows = []
    for rn in range(1, max_rows + 1):
        row = [str(cells.get((rn, cn), "") or "").strip() for cn in range(1, max_cols + 1)]
        if any(x for x in row):
            rows.append(row)
    return rows


MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}


def parse_date_es(s):
    """
    Soporta:
    - 'domingo, 1 de diciembre de 2024'
    - '1 de diciembre de 2024'
    """
    s = (s or "").strip()
    if not s:
        return None
    s = s.lower()
    s = s.replace(",", " ")
    s = re.sub(r"\s+", " ", s).strip()
    m = re.search(r"(\d{1,2})\s+de\s+([a-záéíóúñ]+)\s+de\s+(\d{4})", s)
    if not m:
        # fallback yyyy-mm-dd
        m2 = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
        if m2:
            try:
                return date(int(m2.group(1)), int(m2.group(2)), int(m2.group(3)))
            except Exception:
                return None
        return None
    dd = int(m.group(1))
    mm_name = m.group(2)
    yy = int(m.group(3))
    mm_name = (
        mm_name.replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
        .replace("ñ", "n")
    )
    mm = MONTHS.get(mm_name)
    if not mm:
        return None
    try:
        return date(yy, mm, dd)
    except Exception:
        return None


def parse_money_clp(s):
    """
    Normaliza CLP desde strings tipo:
    - '$2,100,000'
    - '551.26099999999997' (excel float)
    - '539' (miles)
    Regla: si queda < 20000, se asume en miles (x1000).
    """
    raw = (s or "").strip()
    if not raw:
        return None
    # Permite float de excel (string numérica)
    raw = raw.replace("$", "").replace(" ", "")
    # 2,100,000 -> 2100000
    raw = raw.replace(",", "")
    # 2.100.000 -> 2100000 (si hay múltiples puntos, asumir separador de miles)
    if raw.count(".") > 1:
        raw = raw.replace(".", "")
    try:
        n = float(raw)
    except Exception:
        # deja solo dígitos, punto y minus
        raw2 = re.sub(r"[^0-9.\-]", "", raw)
        if raw2.count(".") > 1:
            raw2 = raw2.replace(".", "")
        try:
            n = float(raw2)
        except Exception:
            return None
    if n and abs(n) < 20000:
        n = n * 1000.0
    try:
        return int(round(n))
    except Exception:
        return None


def load_nomina_rows(xlsx_path, sheet_name="nomina"):
    with zipfile.ZipFile(xlsx_path) as z:
        shared = _load_shared_strings(z)
        sheets = _sheet_name_map(z)
        sheet_path = sheets.get(sheet_name) or next(iter(sheets.values()))
        rows = _read_sheet_rows(z, sheet_path, shared, max_cols=16, max_rows=5000)
        if not rows:
            return []
        header = [h.strip() for h in rows[0]]
        # header esperado: Colaborador, Centro de Costo, Situación Contractual, Sueldo, Fecha de Inicio, Cargo, Rol
        out = []
        for r in rows[1:]:
            # corta a len(header) por si hay columnas vacías
            row = r[: len(header)]
            d = dict(zip(header, row))
            out.append(d)
        return out


def upsert_rrhh_from_nomina(db, items, dry_run=False):
    # Import lazy: permite `--dry-run` sin deps (sqlalchemy/fastapi).
    from sqlalchemy import text  # type: ignore

    from backend.routers.rrhh import _ensure_tables  # type: ignore

    _ensure_tables(db)
    created_staff = 0
    updated_staff = 0
    created_nom = 0
    updated_nom = 0
    skipped = 0

    for it in items:
        colaborador = (it.get("Colaborador") or "").strip()
        centro = (it.get("Centro de Costo") or "").strip()
        if not colaborador:
            skipped += 1
            continue

        situ = (it.get("Situación Contractual") or "").strip()
        sueldo = parse_money_clp(it.get("Sueldo") or "")
        f0 = parse_date_es(it.get("Fecha de Inicio") or "")
        cargo = (it.get("Cargo") or "").strip()
        rol = (it.get("Rol") or "").strip()

        ficha = {"cargo": cargo, "situacion_contractual": situ, "sueldo": sueldo}

        # rrhh_staff: match por (colaborador, centro_costo) case-insensitive
        row_staff = db.execute(
            text(
                """
                SELECT id_staff
                FROM rrhh_staff
                WHERE lower(colaborador)=lower(:c) AND lower(coalesce(centro_costo,''))=lower(:cc)
                LIMIT 1
                """
            ),
            {"c": colaborador, "cc": centro},
        ).fetchone()

        if not dry_run:
            if row_staff:
                db.execute(
                    text(
                        """
                        UPDATE rrhh_staff
                        SET centro_costo = :cc,
                            rol = COALESCE(NULLIF(:rol,''), rol),
                            fecha_ingreso = COALESCE(:fi, fecha_ingreso),
                            ficha = COALESCE(ficha,'{}'::jsonb) || :ficha::jsonb
                        WHERE id_staff=:id
                        """
                    ),
                    {
                        "id": int(row_staff[0]),
                        "cc": centro,
                        "rol": rol,
                        "fi": f0,
                        "ficha": json.dumps(ficha, ensure_ascii=False),
                    },
                )
                updated_staff += 1
            else:
                db.execute(
                    text(
                        """
                        INSERT INTO rrhh_staff(colaborador, centro_costo, rol, fecha_ingreso, ficha, is_active)
                        VALUES (:c,:cc,:rol,:fi,:ficha::jsonb, TRUE)
                        """
                    ),
                    {
                        "c": colaborador,
                        "cc": centro,
                        "rol": rol,
                        "fi": f0,
                        "ficha": json.dumps(ficha, ensure_ascii=False),
                    },
                )
                created_staff += 1

        # rrhh_nomina: match por (colaborador, centro_costo)
        row_nom = db.execute(
            text(
                """
                SELECT id_nomina
                FROM rrhh_nomina
                WHERE lower(colaborador)=lower(:c) AND lower(coalesce(centro_costo,''))=lower(:cc)
                LIMIT 1
                """
            ),
            {"c": colaborador, "cc": centro},
        ).fetchone()

        raw_json = json.dumps(it, ensure_ascii=False)
        if not dry_run:
            if row_nom:
                db.execute(
                    text(
                        """
                        UPDATE rrhh_nomina
                        SET situacion_contractual = COALESCE(NULLIF(:situ,''), situacion_contractual),
                            sueldo_fijo = COALESCE(:sf, sueldo_fijo),
                            hh_liquido = COALESCE(:hh, hh_liquido),
                            fecha_inicio = COALESCE(:fi, fecha_inicio),
                            raw = COALESCE(raw,'{}'::jsonb) || :raw::jsonb
                        WHERE id_nomina=:id
                        """
                    ),
                    {
                        "id": int(row_nom[0]),
                        "situ": situ,
                        "sf": sueldo,
                        "hh": sueldo,
                        "fi": f0,
                        "raw": raw_json,
                    },
                )
                updated_nom += 1
            else:
                db.execute(
                    text(
                        """
                        INSERT INTO rrhh_nomina(colaborador, centro_costo, situacion_contractual, sueldo_fijo, hh_liquido, fecha_inicio, raw)
                        VALUES (:c,:cc,:situ,:sf,:hh,:fi,:raw::jsonb)
                        """
                    ),
                    {
                        "c": colaborador,
                        "cc": centro,
                        "situ": situ,
                        "sf": sueldo,
                        "hh": sueldo,
                        "fi": f0,
                        "raw": raw_json,
                    },
                )
                created_nom += 1

    return {
        "created_staff": created_staff,
        "updated_staff": updated_staff,
        "created_nomina": created_nom,
        "updated_nomina": updated_nom,
        "skipped": skipped,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", required=True)
    ap.add_argument("--sheet", default="nomina")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    items = load_nomina_rows(args.xlsx, sheet_name=args.sheet)
    if args.dry_run:
        print("[dry-run] rows:", len(items))
        centros = {}
        for it in items:
            cc = (it.get("Centro de Costo") or "").strip() or "(sin centro)"
            centros[cc] = centros.get(cc, 0) + 1
        print("[dry-run] centros:", json.dumps(centros, ensure_ascii=False))
        for it in items[:5]:
            sueldo = parse_money_clp(it.get("Sueldo") or "")
            fi = parse_date_es(it.get("Fecha de Inicio") or "")
            print(
                json.dumps(
                    {
                        "Colaborador": it.get("Colaborador"),
                        "Centro de Costo": it.get("Centro de Costo"),
                        "Situación Contractual": it.get("Situación Contractual"),
                        "Sueldo_raw": it.get("Sueldo"),
                        "Sueldo_norm": sueldo,
                        "Fecha_raw": it.get("Fecha de Inicio"),
                        "Fecha_norm": fi.isoformat() if fi else None,
                        "Cargo": it.get("Cargo"),
                        "Rol": it.get("Rol"),
                    },
                    ensure_ascii=False,
                )
            )
        return 0

    from backend.db import SessionLocal  # type: ignore

    db = SessionLocal()
    try:
        res = upsert_rrhh_from_nomina(db, items, dry_run=False)
        db.commit()
        print(json.dumps({"ok": True, "result": res}, ensure_ascii=False, indent=2))
        return 0
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2))
        return 1
    finally:
        try:
            db.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
