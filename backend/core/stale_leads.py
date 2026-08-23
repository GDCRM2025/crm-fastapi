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
    - Para tareas/bloqueo solo aplica si el evento es del mes actual.
    - Sin fecha_evento no entra al circuito automatico.
    - NUEVO: +7 dias desde created_at, sin comentarios y sin movimiento (updated_at ~ created_at).
    - CONTACTADO:
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

    # Estados estandar (seed) con fallback por si un deploy conserva los IDs historicos.
    def _estado_id_like(pattern: str, fallback: int) -> int:
        try:
            val = conn.execute(
                text("SELECT id_estado FROM public.estados_lead WHERE UPPER(nombre) LIKE :p ORDER BY id_estado LIMIT 1"),
                {"p": pattern},
            ).scalar()
            return int(val) if val is not None else int(fallback)
        except Exception:
            return int(fallback)

    nuevo_id = _estado_id_like("%NUEV%", 1)
    contactado_id = _estado_id_like("%CONTACT%", 2)
    cotizado_id = _estado_id_like("%COTIZ%", 3)
    confirmado_id = _estado_id_like("CONFIRM%", 4)
    declinado_id = _estado_id_like("%DECLIN%", 5)

    # "del mes": fecha_evento en el mismo ano/mes que hoy
    today_sql = "(now() AT TIME ZONE 'America/Santiago')::date"
    is_this_month_sql = (
        f"EXTRACT(YEAR FROM l.fecha_evento) = EXTRACT(YEAR FROM {today_sql}) "
        f"AND EXTRACT(MONTH FROM l.fecha_evento) = EXTRACT(MONTH FROM {today_sql})"
    )
    eligible_month_sql = f"(l.fecha_evento IS NOT NULL AND ({is_this_month_sql}))"

    # Para "sin movimiento" usamos updated_at; si no existe, caemos a created_at
    last_move_sql = "COALESCE(l.updated_at, l.created_at)"

    # Motivos
    # IMPORTANT: mantenemos estos textos en ASCII para compatibilidad con BDs con encoding SQL_ASCII.
    motivo_evento_pasado = "Declinado por no movimiento de la marca"
    motivo_nuevo = motivo_evento_pasado
    motivo_contacto_sin_fecha = motivo_evento_pasado
    motivo_contacto_stale = motivo_evento_pasado
    motivo_cotizado_evento_cerca = motivo_evento_pasado

    # CTE base (candidatos + dedup)
    ctes_sql = f"""
        WITH candidates AS (
          -- 0) Evento ya paso -> declinar cualquier estado activo (prioridad maxima).
          -- Confirmado se conserva porque representa venta/evento realizado.
          SELECT l.id_lead, 1 AS prio, :motivo_evento_pasado AS motivo
          FROM public.leads l
          WHERE l.id_estado <> :declinado_id
            AND l.id_estado <> :confirmado_id
            AND COALESCE(l.is_deleted,false)=false
            AND l.fecha_evento IS NOT NULL
            AND l.fecha_evento < {today_sql}

          UNION ALL

          -- 1) NUEVO +7d, sin comentarios, sin movimiento, solo mes actual
          SELECT l.id_lead, 2 AS prio, :motivo_nuevo AS motivo
          FROM public.leads l
          WHERE l.id_estado = :nuevo_id
            AND {eligible_month_sql}
            AND l.created_at <= (now() - INTERVAL '7 days')
            AND ({has_comments_sql}) IS FALSE
            AND {last_move_sql} <= (l.created_at + INTERVAL '1 minute')

          UNION ALL

          -- 2) CONTACTADO con fecha del mes, con comentarios, sin movimiento 7 dias
          SELECT l.id_lead, 4 AS prio, :motivo_contacto_stale AS motivo
          FROM public.leads l
          WHERE l.id_estado = :contactado_id
            AND l.fecha_evento IS NOT NULL
            AND ({is_this_month_sql})
            AND ({has_comments_sql}) IS TRUE
            AND {last_move_sql} <= (now() - INTERVAL '7 days')

          UNION ALL

          -- 3) COTIZADO con fecha del mes, con comentarios, con cotizacion,
          --    y evento a <= 4 dias. IMPORTANTE: si hay movimiento reciente (comentario/seguimiento),
          --    no declinamos; el contador se reinicia con updated_at.
          SELECT l.id_lead, 5 AS prio, :motivo_cotizado_evento_cerca AS motivo
          FROM public.leads l
          WHERE l.id_estado = :cotizado_id
            AND l.fecha_evento IS NOT NULL
            AND ({is_this_month_sql})
            AND ({has_comments_sql}) IS TRUE
            AND ({has_quote_sql}) IS TRUE
            AND l.fecha_evento <= ({today_sql} + 4)
            AND {last_move_sql} <= (now() - INTERVAL '3 days')
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
            "confirmado_id": confirmado_id,
            "declinado_id": declinado_id,
            "motivo_evento_pasado": motivo_evento_pasado,
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
            "confirmado_id": confirmado_id,
            "motivo_evento_pasado": motivo_evento_pasado,
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
