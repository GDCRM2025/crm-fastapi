#!/usr/bin/env python3
"""
Backfill para leads confirmados:
- Asegura columna `leads.tipo_cliente` (EMPRESA|PARTICULAR|SIN).
- Para confirmados con `id_cotizacion_vigente`, fija:
    monto_cotizado = (total productos / subtotal neto) + traslado   (SIN IVA)
    tipo_cliente   = EMPRESA si IVA>0, si no PARTICULAR
- Intenta completar `id_cotizacion_vigente` usando:
    1) cotizaciones.id_lead (última cotización)
    2) match por num_cotizacion <-> cotizaciones.numero

Uso (dry-run por defecto):
  python3 tools/backfill_confirmados.py
  python3 tools/backfill_confirmados.py --apply

Nota: si un lead confirmado no tiene forma de enlazar una cotización,
se deja su `monto_cotizado` tal como está (pero se puede normalizar `tipo_cliente`).
"""

from __future__ import annotations

import argparse
from typing import Any

from sqlalchemy import text

from backend.core.database import engine


def _cols(table: str) -> set[str]:
    with engine.begin() as cn:
        rows = cn.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name=:t
                """
            ),
            {"t": table},
        ).fetchall()
    return {str(r[0]) for r in rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Aplica cambios (si no, solo imprime el plan).")
    ap.add_argument("--confirm_id", type=int, default=4, help="ID estado CONFIRMADO (default: 4).")
    args = ap.parse_args()

    confirm_id = int(args.confirm_id or 4)

    leads_cols = _cols("leads")
    cot_cols = _cols("cotizaciones")

    if not leads_cols:
        raise SystemExit("No encontré public.leads")
    if not cot_cols:
        raise SystemExit("No encontré public.cotizaciones")

    has_tipo = "tipo_cliente" in leads_cols
    has_updated_at = "updated_at" in leads_cols
    has_id_cot_vig = "id_cotizacion_vigente" in leads_cols
    has_num_cot = "num_cotizacion" in leads_cols

    has_cot_id = "id_cotizacion" in cot_cols
    has_cot_num = "numero" in cot_cols
    has_cot_id_lead = "id_lead" in cot_cols
    has_cot_updated = ("updated_at" in cot_cols) or ("created_at" in cot_cols)

    if not has_id_cot_vig:
        raise SystemExit("public.leads no tiene id_cotizacion_vigente; no puedo enlazar cotizaciones.")
    if not has_cot_id:
        raise SystemExit("public.cotizaciones no tiene id_cotizacion; no puedo enlazar cotizaciones.")

    # Expresión neta (productos): priorizamos subtotal_productos -> subtotal -> (total - iva - traslado) -> 0
    parts = []
    if "subtotal_productos" in cot_cols:
        parts.append("c.subtotal_productos")
    if "subtotal" in cot_cols:
        parts.append("c.subtotal")
    if "total" in cot_cols and ("iva" in cot_cols or "traslado" in cot_cols):
        iva_expr = "COALESCE(c.iva,0)" if "iva" in cot_cols else "0"
        tr_expr = "COALESCE(c.traslado,0)" if "traslado" in cot_cols else "0"
        parts.append(f"(c.total - {iva_expr} - {tr_expr})")
    base_neto_expr = "COALESCE(" + ", ".join(parts + ["0"]) + ")"

    traslado_expr = "COALESCE(c.traslado,0)" if "traslado" in cot_cols else "0"
    iva_expr = "COALESCE(c.iva,0)" if "iva" in cot_cols else "0"

    plan: list[str] = []
    if not has_tipo:
        plan.append("ALTER TABLE public.leads ADD COLUMN tipo_cliente text;")
    if has_cot_id_lead:
        plan.append("Completar leads.id_cotizacion_vigente desde cotizaciones.id_lead (última cotización).")
    if has_num_cot and has_cot_num:
        plan.append("Completar leads.id_cotizacion_vigente por match num_cotizacion <-> cotizaciones.numero.")
    plan.append("Actualizar monto_cotizado y tipo_cliente para confirmados con id_cotizacion_vigente.")
    plan.append("Normalizar tipo_cliente para confirmados sin cotización (default PARTICULAR si vacío).")

    if not args.apply:
        print("DRY_RUN plan:")
        for p in plan:
            print("-", p)
        print()
        print("INFO:")
        print("confirm_id=", confirm_id)
        print("base_neto_expr=", base_neto_expr)
        print("traslado_expr=", traslado_expr)
        print("iva_expr=", iva_expr)
        return 0

    with engine.begin() as cn:
        if not has_tipo:
            cn.execute(text("ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS tipo_cliente text"))
            has_tipo = True

        # 1) Completar id_cotizacion_vigente desde cotizaciones.id_lead
        if has_cot_id_lead and has_cot_updated:
            upd_set = "id_cotizacion_vigente = lc.id_cotizacion"
            if has_updated_at:
                upd_set += ", updated_at=now()"
            cn.execute(
                text(
                    f"""
                    WITH last_cot AS (
                      SELECT DISTINCT ON (c.id_lead)
                        c.id_lead,
                        c.id_cotizacion
                      FROM public.cotizaciones c
                      WHERE c.id_lead IS NOT NULL
                      ORDER BY c.id_lead, COALESCE(c.updated_at, c.created_at) DESC NULLS LAST, c.id_cotizacion DESC
                    )
                    UPDATE public.leads l
                    SET {upd_set}
                    FROM last_cot lc
                    WHERE l.id_estado = :conf
                      AND (l.id_cotizacion_vigente IS NULL OR l.id_cotizacion_vigente=0)
                      AND lc.id_lead = l.id_lead
                    """
                ),
                {"conf": confirm_id},
            )

        # 2) Completar id_cotizacion_vigente por match num_cotizacion <-> cotizaciones.numero
        if has_num_cot and has_cot_num:
            upd_set = "id_cotizacion_vigente = c.id_cotizacion"
            if has_updated_at:
                upd_set += ", updated_at=now()"
            cn.execute(
                text(
                    f"""
                    UPDATE public.leads l
                    SET {upd_set}
                    FROM public.cotizaciones c
                    WHERE l.id_estado = :conf
                      AND (l.id_cotizacion_vigente IS NULL OR l.id_cotizacion_vigente=0)
                      AND NULLIF(btrim(COALESCE(l.num_cotizacion::text,'')),'') IS NOT NULL
                      AND btrim(COALESCE(c.numero::text,'')) = btrim(COALESCE(l.num_cotizacion::text,''))
                    """
                ),
                {"conf": confirm_id},
            )

        # 3) Actualizar monto_cotizado y tipo_cliente (solo confirmados con cot vigente)
        res = cn.execute(
            text(
                f"""
                UPDATE public.leads l
                SET
                  monto_cotizado = ({base_neto_expr} + {traslado_expr}),
                  tipo_cliente   = CASE WHEN {iva_expr} > 0 THEN 'EMPRESA' ELSE 'PARTICULAR' END
                  {", updated_at=now()" if has_updated_at else ""}
                FROM public.cotizaciones c
                WHERE l.id_estado = :conf
                  AND c.id_cotizacion = l.id_cotizacion_vigente
                """
            ),
            {"conf": confirm_id},
        )
        updated = int(getattr(res, "rowcount", 0) or 0)

        # 4) Normalizar tipo_cliente en confirmados sin cotización (no tocamos monto_cotizado)
        if has_tipo:
            cn.execute(
                text(
                    f"""
                    UPDATE public.leads
                    SET tipo_cliente = COALESCE(NULLIF(UPPER(tipo_cliente),''),'PARTICULAR')
                    {", updated_at=now()" if has_updated_at else ""}
                    WHERE id_estado=:conf
                      AND (id_cotizacion_vigente IS NULL OR id_cotizacion_vigente=0)
                    """
                ),
                {"conf": confirm_id},
            )

        # Resumen
        tot = cn.execute(text("SELECT COUNT(*) FROM public.leads WHERE id_estado=:conf"), {"conf": confirm_id}).scalar()
        con_cot = cn.execute(
            text(
                """
                SELECT COUNT(*)
                FROM public.leads l
                WHERE l.id_estado=:conf
                  AND l.id_cotizacion_vigente IS NOT NULL
                  AND l.id_cotizacion_vigente <> 0
                """
            ),
            {"conf": confirm_id},
        ).scalar()
        por_tipo = cn.execute(
            text(
                """
                SELECT COALESCE(UPPER(tipo_cliente),'SIN') AS tipo, COUNT(*)::int AS cnt
                FROM public.leads
                WHERE id_estado=:conf
                GROUP BY 1
                ORDER BY 2 DESC
                """
            ),
            {"conf": confirm_id},
        ).fetchall()

    print("UPDATED_ROWS=", updated)
    print("CONFIRMADOS_TOTAL=", int(tot or 0))
    print("CONFIRMADOS_CON_COT_VIGENTE=", int(con_cot or 0))
    print("POR_TIPO_CONFIRMADOS=", [(r[0], int(r[1])) for r in por_tipo])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
