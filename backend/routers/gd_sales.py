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
    if compact != "SUPERADMIN":
        raise HTTPException(status_code=403, detail="GD Sales Command Center está disponible sólo para Super Admin.")


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
    endpoint deliberately returns no notes, email or phone and is protected
    server-side for SUPERADMIN.
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
                  COALESCE(NULLIF(btrim(COALESCE(m.nombre,m.marca,'')),''),'Sin marca') AS marca,
                  l.fecha_evento::date AS fecha_evento,
                  COALESCE(l.monto_cotizado,0)::float AS monto,
                  COALESCE(e.nombre,'Sin estado') AS estado,
                  l.seguimiento_at,
                  l.created_at AS fecha_creacion,
                  l.updated_at,
                  COALESCE(NULLIF(btrim(t.assigned_username),''),'SIN ASIGNAR') AS ejecutivo,
                  COALESCE(l.num_cotizacion,'') AS num_cotizacion,
                  t.id_task,
                  t.kind AS task_kind,
                  t.status AS task_status,
                  t.due_at AS task_due_at
                FROM public.leads l
                LEFT JOIN public.marcas m ON m.id_marca=l.id_marca
                LEFT JOIN public.estados_lead e ON e.id_estado=l.id_estado
                LEFT JOIN LATERAL (
                  SELECT t.id_task,t.kind,t.status,t.due_at,t.assigned_username
                  FROM public.tasks t
                  WHERE lower(COALESCE(t.entity_type,''))='lead'
                    AND t.entity_id::text=l.id_lead::text
                    AND lower(COALESCE(t.status,''))='open'
                  ORDER BY t.due_at ASC NULLS LAST,t.id_task DESC
                  LIMIT 1
                ) t ON TRUE
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
        unassigned = str(row.get("ejecutivo") or "").upper() == "SIN ASIGNAR"
        if unassigned:
            action = "ASIGNAR EJECUTIVO"
        elif overdue:
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
                "marca": str(row.get("marca") or "").upper(),
                "fecha_evento": str(event_day) if event_day else None,
                "monto": _money(row.get("monto")),
                "estado": str(row.get("estado") or ""),
                "seguimiento_at": str(row.get("seguimiento_at")) if row.get("seguimiento_at") else None,
                "ejecutivo": str(row.get("ejecutivo") or "SIN ASIGNAR"),
                "num_cotizacion": str(row.get("num_cotizacion") or ""),
                "task_due_at": str(due_at) if due_at else None,
                "days_to_event": days_to_event,
                "no_followup": no_followup,
                "overdue": overdue,
                "within_7_days": bool(days_to_event is not None and 0 <= days_to_event <= 7),
                "next_month": False,
                "unassigned": unassigned,
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
        "unassigned": summarize([item for item in items if item["unassigned"]]),
    }
    executives: dict[str, dict[str, Any]] = {}
    for item in items:
        name = str(item.get("ejecutivo") or "SIN ASIGNAR")
        bucket = executives.setdefault(name, {"ejecutivo": name, "opportunities": 0, "pipeline": 0, "no_followup": 0, "overdue": 0})
        bucket["opportunities"] += 1
        bucket["pipeline"] += _money(item.get("monto"))
        bucket["no_followup"] += int(bool(item.get("no_followup")))
        bucket["overdue"] += int(bool(item.get("overdue")))

    return {
        "ok": True,
        "generated_at": now_cl.isoformat(),
        "summary": summary,
        "items": items,
        "executives": sorted(executives.values(), key=lambda value: (-int(value["pipeline"]), value["ejecutivo"])),
        "data_source": "LIVE_CRM",
    }
