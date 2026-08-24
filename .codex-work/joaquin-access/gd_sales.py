from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.routers.auth import get_current_user


router = APIRouter(prefix="/tools/gd-sales", tags=["gd_sales"])


def _require_superadmin(user: dict[str, Any]) -> None:
    raw = str(user.get("role") or user.get("rol") or "").upper()
    compact = raw.replace(" ", "").replace("_", "").replace("-", "")
    if compact not in {"SUPERADMIN", "CONTROLDEGESTION"}:
        raise HTTPException(status_code=403, detail="Sin permiso para GD Sales Command Center.")


def _money(value: Any) -> int:
    try:
        return int(round(float(value or 0)))
    except Exception:
        return 0


@router.get("/command-center")
def command_center(
    limit: int = Query(700, ge=1, le=1200),
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(get_current_user),
):
    """Live commercial pipeline, generated from CRM tables only.

    The former dashboard was a static, unauthenticated HTML snapshot. This
    endpoint deliberately returns no notes or email and is protected
    server-side for SUPERADMIN and CONTROL DE GESTION. Phone is included because
    this is an explicit commercial analysis surface for those trusted roles.
    """
    _require_superadmin(user)
    now_cl = datetime.now(ZoneInfo("America/Santiago"))
    rows = (
        db.execute(
            text(
                """
                SELECT
                  l.id_lead::bigint AS id_lead,
                  COALESCE(NULLIF(btrim(l.cliente),''),'Sin nombre') AS cliente,
                  COALESCE(NULLIF(btrim(l.telefono),''),'') AS telefono,
                  COALESCE(NULLIF(btrim(COALESCE(m.nombre,m.marca,'')),''),'Sin marca') AS marca,
                  l.fecha_evento::date AS fecha_evento,
                  COALESCE(l.monto_cotizado,0)::float AS monto,
                  COALESCE(e.nombre,'Sin estado') AS estado,
                  l.seguimiento_at,
                  l.created_at AS fecha_creacion,
                  l.updated_at,
                  COALESCE(l.id_cotizacion_vigente,q.id_cotizacion) AS id_cotizacion,
                  COALESCE(NULLIF(btrim(l.num_cotizacion::text),''),q.numero::text,'') AS num_cotizacion,
                  t.id_task,
                  t.kind AS task_kind,
                  t.status AS task_status,
                  t.due_at AS task_due_at
                FROM public.leads l
                LEFT JOIN public.marcas m ON m.id_marca=l.id_marca
                LEFT JOIN public.estados_lead e ON e.id_estado=l.id_estado
                LEFT JOIN LATERAL (
                  SELECT t.id_task,t.kind,t.status,t.due_at
                  FROM public.tasks t
                  WHERE lower(COALESCE(t.entity_type,''))='lead'
                    AND t.entity_id::text=l.id_lead::text
                    AND lower(COALESCE(t.status,''))='open'
                  ORDER BY t.due_at ASC NULLS LAST,t.id_task DESC
                  LIMIT 1
                ) t ON TRUE
                LEFT JOIN LATERAL (
                  SELECT c.id_cotizacion,c.numero
                  FROM public.cotizaciones c
                  WHERE c.id_lead=l.id_lead
                  ORDER BY c.id_cotizacion DESC
                  LIMIT 1
                ) q ON TRUE
                WHERE COALESCE(l.is_deleted,false)=false
                  AND l.fecha_evento::date >= (now() AT TIME ZONE 'America/Santiago')::date
                  AND UPPER(COALESCE(e.nombre,'')) NOT LIKE '%CONFIRM%'
                  AND UPPER(COALESCE(e.nombre,'')) NOT LIKE '%DECLIN%'
                  AND UPPER(COALESCE(e.nombre,'')) NOT LIKE '%RECHAZ%'
                  AND UPPER(COALESCE(e.nombre,'')) NOT LIKE '%VENDID%'
                ORDER BY l.fecha_evento ASC NULLS LAST,COALESCE(l.monto_cotizado,0) DESC,l.id_lead DESC
                LIMIT :limit
                """
            ),
            {"limit": int(limit)},
        )
        .mappings()
        .all()
    )

    today = now_cl.date()
    items: list[dict[str, Any]] = []
    for row in rows:
        event_day = row.get("fecha_evento")
        days_to_event = (event_day - today).days if event_day else None
        due_at = row.get("task_due_at")
        due_local = None
        if due_at:
            try:
                due_local = due_at.astimezone(ZoneInfo("America/Santiago")) if due_at.tzinfo else due_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("America/Santiago"))
            except Exception:
                due_local = due_at
        overdue = bool(due_local and due_local < now_cl)
        no_followup = row.get("seguimiento_at") is None
        if overdue:
            action = "CONTACTAR HOY"
        elif no_followup:
            action = "PROGRAMAR SEGUIMIENTO"
        elif days_to_event is not None and days_to_event <= 7:
            action = "PRIORIZAR EVENTO"
        else:
            action = "REVISAR OPORTUNIDAD"
        items.append(
            {
                "id_lead": int(row.get("id_lead") or 0),
                "cliente": str(row.get("cliente") or ""),
                "telefono": str(row.get("telefono") or ""),
                "marca": str(row.get("marca") or "").upper(),
                "fecha_evento": str(event_day) if event_day else None,
                "monto": _money(row.get("monto")),
                "estado": str(row.get("estado") or ""),
                "seguimiento_at": str(row.get("seguimiento_at")) if row.get("seguimiento_at") else None,
                "id_cotizacion": int(row.get("id_cotizacion")) if row.get("id_cotizacion") else None,
                "num_cotizacion": str(row.get("num_cotizacion") or ""),
                "fecha_creacion": str(row.get("fecha_creacion")) if row.get("fecha_creacion") else None,
                "updated_at": str(row.get("updated_at")) if row.get("updated_at") else None,
                "task_due_at": str(due_at) if due_at else None,
                "days_to_event": days_to_event,
                "no_followup": no_followup,
                "overdue": overdue,
                "within_7_days": bool(days_to_event is not None and 0 <= days_to_event <= 7),
                "next_month": False,
                "action": action,
            }
        )

    # Calculate next-month flag without depending on dateutil.
    if today.month == 12:
        next_year, next_month = today.year + 1, 1
    else:
        next_year, next_month = today.year, today.month + 1
    for item in items:
        raw_day = item.get("fecha_evento")
        item["next_month"] = bool(raw_day and int(raw_day[:4]) == next_year and int(raw_day[5:7]) == next_month)

    def summarize(selected: list[dict[str, Any]]) -> dict[str, int]:
        return {"count": len(selected), "amount": sum(_money(item.get("monto")) for item in selected)}

    summary = {
        "pipeline": summarize(items),
        "no_followup": summarize([item for item in items if item["no_followup"]]),
        "overdue": summarize([item for item in items if item["overdue"]]),
        "within_7_days": summarize([item for item in items if item["within_7_days"]]),
        "next_month": summarize([item for item in items if item["next_month"]]),
        "high_value": summarize([item for item in items if _money(item.get("monto")) >= 1_000_000]),
    }

    return {
        "ok": True,
        "generated_at": now_cl.isoformat(),
        "summary": summary,
        "items": items,
        "data_source": "LIVE_CRM",
    }
