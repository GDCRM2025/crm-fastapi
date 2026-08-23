#!/usr/bin/env python3
"""Elimina únicamente datos sintéticos CODEX_AUDIT de la base local."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url

from backend.db import engine


def main() -> None:
    url = make_url(str(engine.url))
    if (
        os.getenv("CRM_AUDIT_ALLOW_LOCAL_BDGD") != "YES_I_HAVE_A_BACKUP"
        or str(url.host or "") not in {"127.0.0.1", "localhost", "::1"}
    ):
        raise SystemExit("SEGURIDAD: limpieza permitida solo en PostgreSQL local autorizado")

    inspector = inspect(engine)
    with engine.begin() as connection:
        lead_ids = [
            int(row[0])
            for row in connection.execute(
                text("SELECT id_lead FROM public.leads WHERE cliente LIKE 'CODEX_AUDIT%'")
            ).all()
        ]
        if not lead_ids:
            print("AUDIT_CLEANUP_OK leads=0 quotes=0")
            return

        quote_ids = [
            int(row[0])
            for row in connection.execute(
                text("SELECT id_cotizacion FROM public.cotizaciones WHERE id_lead = ANY(:ids)"),
                {"ids": lead_ids},
            ).all()
        ] if inspector.has_table("cotizaciones", schema="public") else []

        # Primero hijos de cotizaciones y luego hijos de leads definidos por FK.
        for parent, parent_ids in (("cotizaciones", quote_ids), ("leads", lead_ids)):
            if not parent_ids:
                continue
            for table in inspector.get_table_names(schema="public"):
                if table == parent:
                    continue
                for fk in inspector.get_foreign_keys(table, schema="public"):
                    if fk.get("referred_table") != parent or len(fk.get("constrained_columns") or []) != 1:
                        continue
                    column = fk["constrained_columns"][0]
                    connection.execute(
                        text(f'DELETE FROM public."{table}" WHERE "{column}" = ANY(:ids)'),
                        {"ids": parent_ids},
                    )

        # Tablas de auditoría/historial que deliberadamente no usan FK.
        if inspector.has_table("event_surveys", schema="public"):
            connection.execute(text("DELETE FROM public.event_surveys WHERE id_lead = ANY(:ids)"), {"ids": lead_ids})
        if inspector.has_table("activity_log", schema="public"):
            connection.execute(
                text("DELETE FROM public.activity_log WHERE (entity_type='lead' AND entity_id = ANY(:leads)) OR (entity_type='cotizacion' AND entity_id = ANY(:quotes))"),
                {"leads": lead_ids, "quotes": quote_ids or [-1]},
            )
        if inspector.has_table("activity_logs", schema="public"):
            connection.execute(
                text("DELETE FROM public.activity_logs WHERE (entity_type='lead' AND entity_id = ANY(:leads)) OR (entity_type='cotizacion' AND entity_id = ANY(:quotes))"),
                {"leads": lead_ids, "quotes": quote_ids or [-1]},
            )
        if quote_ids:
            connection.execute(text("DELETE FROM public.cotizaciones WHERE id_cotizacion = ANY(:ids)"), {"ids": quote_ids})
        connection.execute(text("DELETE FROM public.leads WHERE id_lead = ANY(:ids)"), {"ids": lead_ids})
    print(f"AUDIT_CLEANUP_OK leads={len(lead_ids)} quotes={len(quote_ids)}")


if __name__ == "__main__":
    main()
