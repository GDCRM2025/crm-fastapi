"""
Importa productos/ingredientes desde `listado_ingredientes_brochures.xlsx`.

Este script NO usa pandas/openpyxl. Lee el XLSX como ZIP (XML) con stdlib.

Compatibilidad:
- Funciona con Python 3.6+ (en el server `python3` suele ser 3.6).
- No requiere librerías externas (no usa `requests`).

Qué hace:
- Lee 2 sheets:
  - "Petras Box"          -> marca "PETRAS"
  - "Carritos Mas Flow"   -> marca "MAS FLOW"
- Extrae columnas: Categoria, Producto, Descripcion, Ingredientes
- Importa por API en batch: POST /settings/import/brochures (recomendado)
  o fallback 1x1 a /settings/productos.

Uso:
  python3 backend/scripts/import_brochures_products.py \
    --xlsx data/listado_ingredientes_brochures.xlsx \
    --api https://greendiamond.cl/crm \
    --token 'PEGA_TOKEN_SUPERADMIN' \
    --dry-run

Luego sin --dry-run para ejecutar.
"""

import argparse
import json
import zipfile
import xml.etree.ElementTree as ET
import urllib.request
import urllib.error
import ssl


NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _load_shared_strings(z):
    # sharedStrings.xml puede no existir si el XLSX no usa strings compartidos
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
    # workbook.xml defines sheet name -> r:id ; workbook.xml.rels maps r:id -> sheetX.xml
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


def _read_sheet_rows(
    z,
    sheet_path,
    shared,
    max_cols=4,
    max_rows=5000,
):
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


def load_brochure_rows(xlsx_path):
    with zipfile.ZipFile(xlsx_path) as z:
        shared = _load_shared_strings(z)
        sheets = _sheet_name_map(z)
        want = {
            "Petras Box": "PETRAS",
            "Carritos Mas Flow": "MAS FLOW",
        }
        out = []
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
                    {
                        "marca": marca,
                        "categoria": (categoria or "").strip(),
                        "producto": (producto or "").strip(),
                        "descripcion": (descripcion or "").strip(),
                        "ingredientes": (ingredientes or "").strip(),
                    }
                )
        return out


def _as_payload_items(rows):
    items = []
    for r in rows:
        items.append(
            {
                "marca": r.get("marca", ""),
                "producto": r.get("producto", ""),
                "descripcion": r.get("descripcion", ""),
                # Estos campos pueden existir o no en DB. Si no existen, el endpoint los ignora.
                "categoria": r.get("categoria", ""),
                "ingredientes": r.get("ingredientes", ""),
                "is_active": True,
            }
        )
    return items


def _post_json(url, token, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Authorization": "Bearer {}".format(token),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    ctx = None
    try:
        ctx = ssl.create_default_context()
    except Exception:
        ctx = None

    req = urllib.request.Request(url, data=body, headers=headers)
    # método POST (Python 3.6 no expone "method" como param en Request en todos los builds)
    req.get_method = lambda: "POST"
    try:
        if ctx is not None:
            resp = urllib.request.urlopen(req, timeout=60, context=ctx)
        else:
            resp = urllib.request.urlopen(req, timeout=60)
        raw = resp.read().decode("utf-8", "ignore")
        try:
            return json.loads(raw)
        except Exception:
            return {"raw": raw}
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", "ignore")
        except Exception:
            raw = str(e)
        try:
            data = json.loads(raw) if raw else {"detail": str(e)}
        except Exception:
            data = {"raw": raw, "detail": str(e)}
        raise RuntimeError("HTTP {}: {}".format(getattr(e, "code", "?"), data))


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
