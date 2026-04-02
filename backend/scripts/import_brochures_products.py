"""
Importa productos/ingredientes desde `listado_ingredientes_brochures.xlsx`.

Este archivo NO usa pandas/openpyxl. Lee el XLSX como ZIP (XML) con stdlib.

Qué hace:
- Lee 2 sheets:
  - "Petras Box"          -> marca "PETRAS"
  - "Carritos Mas Flow"   -> marca "MAS FLOW"
- Extrae columnas: Categoria, Producto, Descripcion, Ingredientes
- Genera un JSON listo para importar vía API (/settings/productos) o para convertir a SQL.

Uso (recomendado, vía API):
  python3 backend/scripts/import_brochures_products.py \
    --xlsx data/listado_ingredientes_brochures.xlsx \
    --api https://greendiamond.cl/crm \
    --token 'PEGA_TOKEN_SUPERADMIN' \
    --dry-run

Luego sin --dry-run para ejecutar.
"""

from __future__ import annotations

import argparse
import json
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Tuple


NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


@dataclass(frozen=True)
class BrochureRow:
    marca: str
    categoria: str
    producto: str
    descripcion: str
    ingredientes: str


def _load_shared_strings(z: zipfile.ZipFile) -> List[str]:
    root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    out: List[str] = []
    for si in root.findall("s:si", NS):
        parts: List[str] = []
        for t in si.findall(".//s:t", NS):
            parts.append(t.text or "")
        out.append("".join(parts))
    return out


def _cell_to_colrow(cellref: str) -> Tuple[int, int]:
    col = "".join([c for c in cellref if c.isalpha()])
    row = int("".join([c for c in cellref if c.isdigit()]) or 0)
    n = 0
    for ch in col:
        n = n * 26 + (ord(ch.upper()) - 64)
    return n, row


def _sheet_name_map(z: zipfile.ZipFile) -> Dict[str, str]:
    # workbook.xml defines sheet name -> r:id ; workbook.xml.rels maps r:id -> sheetX.xml
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    nsr = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}
    rid_to_target: Dict[str, str] = {}
    for rel in rels.findall("r:Relationship", nsr):
        rid_to_target[rel.get("Id") or ""] = rel.get("Target") or ""

    out: Dict[str, str] = {}
    for sh in wb.findall(".//s:sheets/s:sheet", NS):
        name = sh.get("name") or ""
        rid = sh.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id") or ""
        target = rid_to_target.get(rid, "")
        if target:
            out[name] = "xl/" + target.lstrip("/")
    return out


def _read_sheet_rows(
    z: zipfile.ZipFile,
    sheet_path: str,
    shared: List[str],
    max_cols: int = 4,
    max_rows: int = 5000,
) -> List[List[str]]:
    root = ET.fromstring(z.read(sheet_path))
    cells: Dict[Tuple[int, int], str] = {}
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
        if c.get("t") == "s":
            try:
                val = shared[int(val)]
            except Exception:
                pass
        cells[(rn, cn)] = val

    rows: List[List[str]] = []
    for rn in range(1, max_rows + 1):
        row = [str(cells.get((rn, cn), "") or "").strip() for cn in range(1, max_cols + 1)]
        if any(x for x in row):
            rows.append(row)
    return rows


def load_brochure_rows(xlsx_path: str) -> List[BrochureRow]:
    with zipfile.ZipFile(xlsx_path) as z:
        shared = _load_shared_strings(z)
        sheets = _sheet_name_map(z)
        want = {
            "Petras Box": "PETRAS",
            "Carritos Mas Flow": "MAS FLOW",
        }
        out: List[BrochureRow] = []
        for sheet_name, marca in want.items():
            sheet_path = sheets.get(sheet_name)
            if not sheet_path:
                raise RuntimeError(f"No encontré sheet '{sheet_name}' en XLSX.")
            rows = _read_sheet_rows(z, sheet_path, shared, max_cols=4, max_rows=5000)
            if not rows:
                continue
            header = [h.strip().lower() for h in rows[0]]
            if header[:4] != ["categoria", "producto", "descripcion", "ingredientes"]:
                raise RuntimeError(f"Header inesperado en '{sheet_name}': {rows[0]}")
            for r in rows[1:]:
                categoria, producto, descripcion, ingredientes = (r + ["", "", "", ""])[:4]
                if not producto:
                    continue
                out.append(
                    BrochureRow(
                        marca=marca,
                        categoria=categoria.strip(),
                        producto=producto.strip(),
                        descripcion=descripcion.strip(),
                        ingredientes=ingredientes.strip(),
                    )
                )
        return out


def _as_payload_items(rows: Iterable[BrochureRow]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for r in rows:
        items.append(
            {
                "marca": r.marca,
                "producto": r.producto,
                "descripcion": r.descripcion,
                # Estos campos pueden existir o no en DB. Si no existen, el endpoint los ignora.
                "categoria": r.categoria,
                "ingredientes": r.ingredientes,
                "is_active": True,
            }
        )
    return items


def _post_json(url: str, token: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    import requests

    r = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        timeout=60,
    )
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text}
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {data}")
    return data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--api", default="")  # e.g. https://greendiamond.cl/crm
    ap.add_argument("--token", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows = load_brochure_rows(args.xlsx)
    items = _as_payload_items(rows)
    payload = {"items": items}

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    if args.api:
        if not args.token:
            raise SystemExit("--token requerido si usas --api")
        base = args.api.rstrip("/")
        # Import simple: crea/actualiza por producto+marca via endpoint batch si existe; si no, fallback 1x1.
        # Por compat, usamos /settings/import/brochures si existe; si no, /settings/productos uno a uno.
        if args.dry_run:
            print(f"[dry-run] {len(items)} productos listos para importar a {base}")
            return 0

        # Endpoint nuevo sugerido (si existe)
        try:
            data = _post_json(f"{base}/settings/import/brochures", args.token, payload)
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return 0
        except Exception:
            pass

        ok = 0
        fail = 0
        for it in items:
            try:
                _post_json(f"{base}/settings/productos", args.token, it)
                ok += 1
            except Exception as e:
                fail += 1
                print(f"[fail] {it.get('marca')} :: {it.get('producto')} :: {e}")
        print(json.dumps({"ok": True, "created": ok, "failed": fail}, ensure_ascii=False))
        return 0 if fail == 0 else 2

    # print to stdout if no api/out
    if not args.out:
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

