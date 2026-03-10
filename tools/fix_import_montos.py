from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

# ensure project imports
BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from sqlalchemy import text  # type: ignore
from google.oauth2 import service_account  # type: ignore
from googleapiclient.discovery import build  # type: ignore

from backend.core.db import get_connection
from tools.import_leads_sheets import SHEETS, parse_money, getv, apply_monto_scale  # reuse


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


def norm_id(s: str) -> str:
    s = (s or "").strip().upper()
    s = re.sub(r"\s+", "", s)
    # si es numérico, normaliza evitando 123.0 / 123,0
    if re.match(r"^\d+(?:[.,]0+)?$", s):
        return s.split(".")[0].split(",")[0]
    digits = re.sub(r"\D", "", s)
    return digits if digits else s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--service-account", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--monto-scale", type=float, default=1.0, help="Multiplicar montos pequeños (ej: 1000)")
    ap.add_argument("--monto-threshold", type=float, default=10000.0, help="Solo escala si monto < este umbral")
    args = ap.parse_args()

    service = build_service(args.service_account)

    # build map: id_origen -> monto
    monto_by_id: Dict[str, float] = {}
    for cfg in SHEETS:
        values = get_sheet_values(service, cfg["sheet_id"], cfg["tab"])
        rows = map_rows(values)
        for row in rows:
            id_origen = norm_id(getv(row, "ID") or "")
            if not id_origen:
                continue
            monto = apply_monto_scale(
                parse_money(getv(row, "Monto Cotizado", "Monto") or ""),
                args.monto_scale,
                args.monto_threshold,
            )
            monto_by_id[id_origen] = monto

    updated = 0
    skipped = 0
    with get_connection() as conn:
        rows = conn.execute(
            text("SELECT id_lead, notas, monto_cotizado FROM public.leads WHERE notas ILIKE '%ID Origen:%'")
        ).mappings().all()

        for r in rows:
            notas = r.get("notas") or ""
            m = re.search(r"ID Origen:\\s*([^\\n\\r]+)", notas)
            if not m:
                skipped += 1
                continue
            id_origen = norm_id(m.group(1).strip())
            if id_origen not in monto_by_id:
                skipped += 1
                continue
            monto = monto_by_id[id_origen]
            if args.dry_run:
                updated += 1
                continue
            conn.execute(
                text("UPDATE public.leads SET monto_cotizado=:m, updated_at=now() WHERE id_lead=:id"),
                {"m": monto, "id": r["id_lead"]},
            )
            updated += 1
        if not args.dry_run:
            conn.commit()

    print(json.dumps({"updated": updated, "skipped": skipped}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
