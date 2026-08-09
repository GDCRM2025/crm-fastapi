from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.omnichannel.service import (
    CHANNELS,
    can_access_brand,
    capability_payload,
    funnel_metrics,
    list_inbox,
    source_brand_ref,
    visible_items,
)
from backend.routers.auth import get_current_user


router = APIRouter(prefix="/api/omnichannel", tags=["Omnichannel"], dependencies=[Depends(get_current_user)])


class WorkItemUpdate(BaseModel):
    lead_id: int | None = Field(default=None, ge=1)
    quote_id: int | None = Field(default=None, ge=1)
    assigned_user_id: str | None = Field(default=None, max_length=180)
    assigned_user_name: str | None = Field(default=None, max_length=180)
    follow_up_at: datetime | None = None
    status: Literal["OPEN", "PENDING", "RESOLVED"] = "OPEN"


def _actor(user: dict) -> str:
    return str(user.get("username") or user.get("email") or user.get("name") or "CRM")[:180]


def _ensure_schema(db: Session) -> None:
    if not db.execute(text("SELECT to_regclass('public.wi_omnichannel_work_items')")).scalar():
        raise HTTPException(503, "La migración de Inbox omnicanal está pendiente")


@router.get("/capabilities")
def capabilities():
    return {"ok": True, "items": capability_payload()}


@router.get("/inbox")
def inbox(
    channel: list[str] | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    _ensure_schema(db)
    invalid = {str(item).upper() for item in (channel or [])} - set(CHANNELS)
    if invalid:
        raise HTTPException(400, f"Canal no reconocido: {', '.join(sorted(invalid))}")
    items = visible_items(list_inbox(db, channels=channel, limit=limit), user)
    return {"ok": True, "total": len(items), "items": items}


@router.patch("/items/{channel}/{source_ref}")
def update_work_item(
    channel: str,
    source_ref: str,
    payload: WorkItemUpdate = Body(...),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    _ensure_schema(db)
    normalized_channel = str(channel).upper()
    if normalized_channel not in CHANNELS:
        raise HTTPException(400, "Canal no reconocido")
    if not source_ref.strip() or len(source_ref) > 240:
        raise HTTPException(400, "Referencia de origen inválida")
    brand_ref = source_brand_ref(db, normalized_channel, source_ref.strip())
    if brand_ref is None:
        raise HTTPException(404, "La conversación de origen no existe en este canal")
    if not can_access_brand(user, brand_ref):
        raise HTTPException(403, "No tienes acceso a la marca de esta conversación")
    if payload.quote_id is not None and payload.lead_id is None:
        raise HTTPException(400, "Una cotización debe quedar vinculada a un lead")
    if payload.lead_id is not None:
        lead_exists = db.execute(
            text("SELECT 1 FROM public.leads WHERE id_lead=:id"), {"id": payload.lead_id}
        ).scalar()
        if not lead_exists:
            raise HTTPException(404, "El lead seleccionado no existe")
    if payload.quote_id is not None:
        quote_exists = db.execute(text("""
          SELECT 1 FROM public.cotizaciones
          WHERE id_cotizacion=:quote_id AND id_lead=:lead_id
        """), {"quote_id": payload.quote_id, "lead_id": payload.lead_id}).scalar()
        if not quote_exists:
            raise HTTPException(400, "La cotización no pertenece al lead seleccionado")
    actor = _actor(user)
    row = db.execute(text("""
        INSERT INTO public.wi_omnichannel_work_items(
          channel, source_ref, lead_id, quote_id, assigned_user_id, assigned_user_name,
          follow_up_at, status, created_by, updated_by
        ) VALUES (
          :channel, :source_ref, :lead_id, :quote_id, :assigned_user_id, :assigned_user_name,
          :follow_up_at, :status, :actor, :actor
        )
        ON CONFLICT(channel, source_ref) DO UPDATE SET
          lead_id=EXCLUDED.lead_id, quote_id=EXCLUDED.quote_id,
          assigned_user_id=EXCLUDED.assigned_user_id, assigned_user_name=EXCLUDED.assigned_user_name,
          follow_up_at=EXCLUDED.follow_up_at, status=EXCLUDED.status,
          updated_by=EXCLUDED.updated_by, updated_at=now()
        RETURNING channel, source_ref, lead_id, quote_id, assigned_user_id,
                  assigned_user_name, follow_up_at, status, updated_at
    """), {"channel": normalized_channel, "source_ref": source_ref.strip(), "actor": actor, **payload.model_dump()}).mappings().one()
    db.commit()
    return {"ok": True, "item": dict(row)}


@router.get("/analytics")
def analytics(
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    _ensure_schema(db)
    items = visible_items(list_inbox(db, limit=min(limit, 200)), user)
    lead_ids = sorted({int(item["lead_id"]) for item in items if item.get("lead_id") is not None})
    quote_rows = []
    if lead_ids and db.execute(text("SELECT to_regclass('public.cotizaciones')")).scalar():
        columns = {str(row[0]) for row in db.execute(text("""
          SELECT column_name FROM information_schema.columns
          WHERE table_schema='public' AND table_name='cotizaciones'
        """)).fetchall()}
        revenue_expr = "COALESCE(total,0)" if "total" in columns else (
            "COALESCE(monto_total,0)" if "monto_total" in columns else "0"
        )
        sale_parts = []
        if "estado" in columns:
            sale_parts.append("upper(COALESCE(estado,'')) IN ('APROBADA','ACEPTADA','VENDIDA','PAGADA')")
        if "accepted_at" in columns:
            sale_parts.append("accepted_at IS NOT NULL")
        sale_expr = " OR ".join(sale_parts) or "FALSE"
        rows = db.execute(text(f"""
          SELECT id_lead AS lead_id,
                 {revenue_expr} AS revenue,
                 CASE WHEN {sale_expr} THEN TRUE ELSE FALSE END AS is_sale
          FROM public.cotizaciones WHERE id_lead = ANY(:lead_ids)
        """), {"lead_ids": lead_ids}).mappings().all()
        quote_rows = [dict(row) for row in rows]
    return {"ok": True, "items": funnel_metrics(items, quote_rows), "scope": "current_inbox"}
