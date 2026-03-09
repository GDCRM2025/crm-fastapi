from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import text

from backend.core.db import get_connection
from backend.routers.auth import get_current_user
from backend.routers.assets import _ensure_schema, _require_assets_access

router = APIRouter(prefix="/assets", tags=["assets"])


@router.get("/vehicle_checklists")
def list_vehicle_checklists(
    id_lead: int = Query(..., ge=1),
    tipo: Optional[str] = Query(None),
    user: dict = Depends(get_current_user),
):
    _require_assets_access(user)
    tipo_norm = (tipo or "").strip().upper()
    with get_connection() as conn:
        _ensure_schema(conn)
        where = ["id_lead=:id"]
        params: Dict[str, Any] = {"id": int(id_lead)}
        if tipo_norm:
            where.append("tipo=:t")
            params["t"] = tipo_norm
        rows = conn.execute(
            text(
                f"""
                SELECT id_check, tipo, payload, created_by, created_at
                FROM public.vehicle_checklists
                WHERE {' AND '.join(where)}
                ORDER BY created_at DESC
                LIMIT 50
                """
            ),
            params,
        ).fetchall()
        items = []
        for r in rows:
            items.append(
                {
                    "id_check": int(r[0]),
                    "tipo": r[1],
                    "payload": r[2] if isinstance(r[2], dict) else (json.loads(r[2]) if r[2] else {}),
                    "created_by": r[3],
                    "created_at": r[4].isoformat() if hasattr(r[4], "isoformat") else str(r[4] or ""),
                }
            )
        return {"ok": True, "items": items}


@router.post("/vehicle_checklists")
def create_vehicle_checklist(payload: dict = Body(...), user: dict = Depends(get_current_user)):
    _require_assets_access(user)
    id_lead = payload.get("id_lead")
    if not str(id_lead or "").isdigit():
        raise HTTPException(400, "id_lead requerido")
    tipo = (payload.get("tipo") or "").strip().upper()
    if tipo not in ("ENTREGA", "DEVOLUCION"):
        raise HTTPException(400, "tipo inválido (ENTREGA|DEVOLUCION)")
    pl = payload.get("payload") or {}
    if not isinstance(pl, dict):
        raise HTTPException(400, "payload debe ser objeto JSON")
    created_by = (str(user.get("name") or user.get("username") or "").strip() or None)

    with get_connection() as conn:
        _ensure_schema(conn)
        conn.execute(
            text(
                """
                INSERT INTO public.vehicle_checklists(id_lead,tipo,payload,created_by,created_at)
                VALUES (:l,:t,:p,:by, now())
                """
            ),
            {"l": int(id_lead), "t": tipo, "p": json.dumps(pl), "by": created_by},
        )
        conn.commit()
    return {"ok": True}

