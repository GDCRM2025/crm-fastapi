from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from backend.core.db import get_connection


def _table_exists(conn, table: str) -> bool:
    return bool(conn.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}).scalar())


def _cols(conn, table: str) -> set[str]:
    rows = conn.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name=:t
            """
        ),
        {"t": table},
    ).fetchall()
    return {r[0] for r in rows}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _build_has_comments_sql(leads_cols: set[str], has_lead_notas: bool) -> str:
    """
    Devuelve una expresion SQL booleana para "tiene comentarios".
    Considera:
    - leads.notas (si existe)
    - lead_notas (si existe)
    """
    parts: List[str] = []
    if "notas" in leads_cols:
        parts.append("(COALESCE(NULLIF(btrim(l.notas),''), NULL) IS NOT NULL)")
    if has_lead_notas:
        parts.append(
            "EXISTS (SELECT 1 FROM public.lead_notas ln WHERE ln.id_lead = l.id_lead)"
        )
    if not parts:
        return "FALSE"
    return "(" + " OR ".join(parts) + ")"


def _build_has_quote_sql(leads_cols: set[str], has_cotizaciones: bool) -> str:
    """
    Devuelve una expresion SQL booleana para "tiene cotizacion".
    Considera:
    - tabla cotizaciones (si existe)
    - leads.monto_cotizado
    - leads.num_cotizacion
    - leads.cotizacion_pdf_url (si existe)
    """
    parts: List[str] = []
    if "monto_cotizado" in leads_cols:
        parts.append("(COALESCE(l.monto_cotizado, 0) > 0)")
    if "num_cotizacion" in leads_cols:
        parts.append("(l.num_cotizacion IS NOT NULL)")
    if "cotizacion_pdf_url" in leads_cols:
        parts.append("(COALESCE(NULLIF(btrim(l.cotizacion_pdf_url),''), NULL) IS NOT NULL)")
    if has_cotizaciones:
        parts.append("EXISTS (SELECT 1 FROM public.cotizaciones c WHERE c.id_lead = l.id_lead)")
    if not parts:
        return "FALSE"
    return "(" + " OR ".join(parts) + ")"


def auto_decline_stale_leads(
    *,
    dry_run: bool = True,
    triggered_by: Optional[str] = None,
    conn=None,
) -> Dict[str, Any]:
    """
    Aplica reglas de "leads sin movimiento" (declinacion automatica).

    Reglas (segun definicion del negocio):
    - Solo aplica si el evento es del mes actual; si NO hay fecha_evento, se considera elegible.
    - NUEVO: +7 dias desde created_at, sin comentarios y sin movimiento (updated_at ~ created_at).
    - CONTACTADO:
      - sin fecha_evento y sin movimiento 5 dias => DECLINADO.
      - con fecha_evento del mes, con comentarios y sin movimiento 7 dias => DECLINADO.
    - COTIZADO: con fecha_evento del mes, con comentarios y con cotizacion => DECLINADO si fecha_evento <= hoy + 4 dias.
    """
    # Permite reutilizar una misma conexion (por ejemplo desde login) para locks y ejecucion atomica.
    if conn is None:
        with get_connection() as c:
            return auto_decline_stale_leads(dry_run=dry_run, triggered_by=triggered_by, conn=c)

    if not _table_exists(conn, "leads"):
        return {"ok": False, "error": "Tabla leads no existe"}

    leads_cols = _cols(conn, "leads")
    has_lead_notas = _table_exists(conn, "lead_notas")
    has_cotizaciones = _table_exists(conn, "cotizaciones")

    has_comments_sql = _build_has_comments_sql(leads_cols, has_lead_notas)
    has_quote_sql = _build_has_quote_sql(leads_cols, has_cotizaciones)

    # Estados estandar (seed): 1 NUEVO, 2 CONTACTADO, 3 COTIZADO, 5 DECLINADO
    # (Si en algun deploy cambia, esto se ajusta en la tabla estados_lead)
    nuevo_id = 1
    contactado_id = 2
    cotizado_id = 3
    declinado_id = 5

    # "del mes": fecha_evento en el mismo ano/mes que hoy
    is_this_month_sql = (
        "EXTRACT(YEAR FROM l.fecha_evento) = EXTRACT(YEAR FROM CURRENT_DATE) "
        "AND EXTRACT(MONTH FROM l.fecha_evento) = EXTRACT(MONTH FROM CURRENT_DATE)"
    )
    eligible_month_sql = f"(l.fecha_evento IS NULL OR ({is_this_month_sql}))"

    # Para "sin movimiento" usamos updated_at; si no existe, caemos a created_at
    last_move_sql = "COALESCE(l.updated_at, l.created_at)"

    # Motivos
    # IMPORTANT: mantenemos estos textos en ASCII para compatibilidad con BDs con encoding SQL_ASCII.
    motivo_evento_pasado_nuevo = "AUTO: Evento pasado (sin seguimiento ejecutivo)"
    motivo_evento_pasado_cliente = "AUTO: Evento pasado (cliente no contesto)"
    motivo_nuevo = "AUTO: NUEVO sin movimiento +7 dias (sin comentarios)"
    motivo_contacto_sin_fecha = "AUTO: CONTACTADO sin fecha de evento +5 dias (sin movimiento)"
    motivo_contacto_stale = "AUTO: CONTACTADO con fecha y comentarios +7 dias (sin movimiento)"
    motivo_cotizado_evento_cerca = "AUTO: COTIZADO sin confirmar (evento <= 4 dias)"

    # CTE base (candidatos + dedup)
    ctes_sql = f"""
        WITH candidates AS (
          -- 0) Evento ya paso -> declinar segun estado (prioridad maxima)
          SELECT l.id_lead, 1 AS prio, :motivo_evento_pasado_nuevo AS motivo
          FROM public.leads l
          WHERE l.id_estado = :nuevo_id
            AND l.fecha_evento IS NOT NULL
            AND l.fecha_evento < CURRENT_DATE

          UNION ALL

          SELECT l.id_lead, 1 AS prio, :motivo_evento_pasado_cliente AS motivo
          FROM public.leads l
          WHERE l.id_estado IN (:contactado_id, :cotizado_id)
            AND l.fecha_evento IS NOT NULL
            AND l.fecha_evento < CURRENT_DATE

          -- 1) NUEVO +7d, sin comentarios, sin movimiento, (mes actual o sin fecha)
          SELECT l.id_lead, 2 AS prio, :motivo_nuevo AS motivo
          FROM public.leads l
          WHERE l.id_estado = :nuevo_id
            AND {eligible_month_sql}
            AND l.created_at <= (now() - INTERVAL '7 days')
            AND ({has_comments_sql}) IS FALSE
            AND {last_move_sql} <= (l.created_at + INTERVAL '1 minute')

          UNION ALL

          -- 2a) CONTACTADO sin fecha_evento, sin movimiento 5 dias (no importa comentarios)
          SELECT l.id_lead, 3 AS prio, :motivo_contacto_sin_fecha AS motivo
          FROM public.leads l
          WHERE l.id_estado = :contactado_id
            AND l.fecha_evento IS NULL
            AND {last_move_sql} <= (now() - INTERVAL '5 days')

          UNION ALL

          -- 2b) CONTACTADO con fecha del mes, con comentarios, sin movimiento 7 dias
          SELECT l.id_lead, 4 AS prio, :motivo_contacto_stale AS motivo
          FROM public.leads l
          WHERE l.id_estado = :contactado_id
            AND l.fecha_evento IS NOT NULL
            AND ({is_this_month_sql})
            AND ({has_comments_sql}) IS TRUE
            AND {last_move_sql} <= (now() - INTERVAL '7 days')

          UNION ALL

          -- 3) COTIZADO con fecha del mes, con comentarios, con cotizacion,
          --    y evento a <= 4 dias (incluye pasado)
          SELECT l.id_lead, 5 AS prio, :motivo_cotizado_evento_cerca AS motivo
          FROM public.leads l
          WHERE l.id_estado = :cotizado_id
            AND l.fecha_evento IS NOT NULL
            AND ({is_this_month_sql})
            AND ({has_comments_sql}) IS TRUE
            AND ({has_quote_sql}) IS TRUE
            AND l.fecha_evento <= (CURRENT_DATE + 4)
        ),
        dedup AS (
          -- si un lead cae en mas de una regla, nos quedamos con la de menor prioridad
          SELECT DISTINCT ON (id_lead) id_lead, motivo, prio
          FROM candidates
          ORDER BY id_lead, prio ASC
        )
        """

    # Candidatos: devolvemos (id_lead, motivo)
    candidates_sql = f"""
        {ctes_sql}
        SELECT d.id_lead, d.motivo
        FROM dedup d
        ORDER BY d.id_lead;
        """

    # Defensa: algunas instalaciones tienen client_encoding tipo SQL_ASCII.
    # Aseguramos que el SQL sea ASCII para evitar UnicodeEncodeError al enviar la query.
    try:
        candidates_sql.encode("ascii")
    except UnicodeEncodeError as e:
        return {
            "ok": False,
            "error": "SQL no ASCII (revisar tildes/utf8 en stale_leads.py)",
            "where": "candidates_sql",
            "details": str(e),
        }

    candidates = conn.execute(
        text(candidates_sql),
        {
            "nuevo_id": nuevo_id,
            "contactado_id": contactado_id,
            "cotizado_id": cotizado_id,
            "motivo_evento_pasado_nuevo": motivo_evento_pasado_nuevo,
            "motivo_evento_pasado_cliente": motivo_evento_pasado_cliente,
            "motivo_nuevo": motivo_nuevo,
            "motivo_contacto_sin_fecha": motivo_contacto_sin_fecha,
            "motivo_contacto_stale": motivo_contacto_stale,
            "motivo_cotizado_evento_cerca": motivo_cotizado_evento_cerca,
        },
    ).fetchall()

    items = [{"id_lead": int(r[0]), "motivo": str(r[1])} for r in candidates]

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "total": len(items),
            "items": items,
            "meta": {
                "has_lead_notas": has_lead_notas,
                "has_cotizaciones": has_cotizaciones,
            },
        }

    # Aplicar update en bloque.
    now_str = _now_utc().strftime("%Y-%m-%d %H:%M")
    set_parts: List[str] = ["id_estado = :declinado_id", "updated_at = now()"]
    if "declinado_motivo" in leads_cols:
        set_parts.append("declinado_motivo = d.motivo")
    if "declinado_at" in leads_cols:
        set_parts.append("declinado_at = now()")
    if "notas" in leads_cols:
        # Agrega una linea, sin pisar contenido previo.
        set_parts.append(
            "notas = CASE "
            "WHEN l.notas IS NULL OR btrim(l.notas) = '' THEN (:nota_prefix || d.motivo) "
            "ELSE l.notas || E'\\n' || (:nota_prefix || d.motivo) "
            "END"
        )

    update_sql = f"""
        {ctes_sql}
        UPDATE public.leads l
        SET {", ".join(set_parts)}
        FROM dedup d
        WHERE l.id_lead = d.id_lead
          AND l.id_estado <> :declinado_id
        RETURNING l.id_lead, d.motivo;
        """

    try:
        update_sql.encode("ascii")
    except UnicodeEncodeError as e:
        return {
            "ok": False,
            "error": "SQL no ASCII (revisar tildes/utf8 en stale_leads.py)",
            "where": "update_sql",
            "details": str(e),
        }

    # Ejecuta update
    updated = conn.execute(
        text(update_sql),
        {
            "declinado_id": declinado_id,
            "nota_prefix": f"[AUTO-DECLINADO {now_str}] ",
            "nuevo_id": nuevo_id,
            "contactado_id": contactado_id,
            "cotizado_id": cotizado_id,
            "motivo_nuevo": motivo_nuevo,
            "motivo_contacto_sin_fecha": motivo_contacto_sin_fecha,
            "motivo_contacto_stale": motivo_contacto_stale,
            "motivo_cotizado_evento_cerca": motivo_cotizado_evento_cerca,
        },
    ).fetchall()

    changed = [{"id_lead": int(r[0]), "motivo": str(r[1])} for r in updated]

    # Bitacora en lead_notas (si existe)
    if has_lead_notas and changed:
        created_by = (triggered_by or "AUTO").strip() or "AUTO"
        for row in changed:
            conn.execute(
                text(
                    """
                    INSERT INTO public.lead_notas (id_lead, tipo, texto, created_at, created_by)
                    VALUES (:id_lead, 'DECLINADO', :texto, now(), :created_by)
                    """
                ),
                {
                    "id_lead": int(row["id_lead"]),
                    "texto": str(row["motivo"]),
                    "created_by": created_by,
                },
            )

    conn.commit()

    return {
        "ok": True,
        "dry_run": False,
        "total": len(changed),
        "items": changed,
        "meta": {
            "has_lead_notas": has_lead_notas,
            "has_cotizaciones": has_cotizaciones,
        },
    }
