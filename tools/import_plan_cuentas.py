#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import os
import sys
from typing import Dict, Optional

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sqlalchemy import text
from backend.core.db import get_connection


def _ensure_tables(conn):
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS plan_cuentas (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                classification TEXT,
                description TEXT,
                example_transactions TEXT,
                who_inputs TEXT,
                how_to_impute TEXT,
                parent_code TEXT REFERENCES plan_cuentas(code),
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT now(),
                updated_at TIMESTAMP DEFAULT now()
            )
            """
        )
    )
    conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_plan_cuentas_parent ON plan_cuentas(parent_code)")
    )
    conn.commit()


def _parent_for(code: str, codes: set[str]) -> Optional[str]:
    # Try replacing trailing digits with zeros until we find a parent
    for i in range(1, len(code)):
        cand = code[:-i] + ("0" * i)
        if cand != code and cand in codes:
            return cand
    return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--file", default="data/plan_cuentas_seed.csv")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if not os.path.exists(args.file):
        raise SystemExit(f"No existe: {args.file}")

    rows: list[Dict[str, str]] = []
    with open(args.file, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get("code"):
                continue
            clean = {}
            for k, v in row.items():
                if k is None:
                    # columnas extra por comillas/commas -> ignorar o concatenar
                    continue
                if isinstance(v, list):
                    v = " ".join([x for x in v if x])
                clean[k] = (v or "").strip()
            rows.append(clean)

    codes = {r["code"] for r in rows}
    for r in rows:
        if not r.get("parent_code"):
            r["parent_code"] = _parent_for(r["code"], codes) or ""

    if args.dry_run:
        print({"rows": len(rows), "with_parent": sum(1 for r in rows if r.get("parent_code"))})
        return

    inserted = 0
    updated = 0
    with get_connection() as conn:
        _ensure_tables(conn)
        for r in rows:
            payload = {
                "code": r.get("code"),
                "name": r.get("name"),
                "type": r.get("type"),
                "classification": r.get("classification") or None,
                "description": r.get("description") or None,
                "example_transactions": r.get("example_transactions") or None,
                "who_inputs": r.get("who_inputs") or None,
                "how_to_impute": r.get("how_to_impute") or None,
                "parent_code": r.get("parent_code") or None,
            }
            res = conn.execute(
                text(
                    """
                    INSERT INTO plan_cuentas
                    (code, name, type, classification, description, example_transactions,
                     who_inputs, how_to_impute, parent_code, is_active)
                    VALUES
                    (:code, :name, :type, :classification, :description, :example_transactions,
                     :who_inputs, :how_to_impute, :parent_code, TRUE)
                    ON CONFLICT (code) DO UPDATE SET
                      name=EXCLUDED.name,
                      type=EXCLUDED.type,
                      classification=EXCLUDED.classification,
                      description=EXCLUDED.description,
                      example_transactions=EXCLUDED.example_transactions,
                      who_inputs=EXCLUDED.who_inputs,
                      how_to_impute=EXCLUDED.how_to_impute,
                      parent_code=EXCLUDED.parent_code,
                      updated_at=now()
                    """
                ),
                payload,
            )
            if res.rowcount == 1:
                inserted += 1
            else:
                updated += 1
        conn.commit()

    print({"inserted": inserted, "updated": updated, "total": len(rows)})


if __name__ == "__main__":
    main()
