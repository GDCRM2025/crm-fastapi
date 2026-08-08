from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from datetime import datetime
from typing import Optional, Literal

# AJUSTA ESTOS IMPORTS si tu proyecto los tiene en otra ruta
from backend.db import engine
from backend.core.auth import get_current_user

router = APIRouter(prefix="/agenda", tags=["agenda"])


def _ensure_table():
    sql = """
    CREATE TABLE IF NOT EXISTS agenda_solicitudes (
      id BIGSERIAL PRIMARY KEY,
      id_lead INTEGER NOT NULL,
      fecha_hora TIMESTAMPTZ NOT NULL,
      operadores INTEGER NOT NULL DEFAULT 0,
      premontaje_horas NUMERIC(6,2) NOT NULL DEFAULT 0,
      monto NUMERIC(14,2) NOT NULL DEFAULT 0,
      notas TEXT,
      creado_por TEXT,
      estado TEXT NOT NULL DEFAULT 'PENDIENTE',
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS idx_agenda_solicitudes_lead ON agenda_solicitudes(id_lead);
    """
    with engine.begin() as cn:
        cn.execute(text(sql))


_ensure_table()


class AgendaCreate(BaseModel):
    id_lead: int
    fecha_hora: datetime
    operadores: int
    premontaje_horas: float
    notas: Optional[str] = ""
    monto: float = 0.0


class AgendaUpdateEstado(BaseModel):
    estado: Literal["PENDIENTE", "APROBADO", "RECHAZADO"]


@router.post("/solicitudes")
def crear_solicitud(p: AgendaCreate, me=Depends(get_current_user)):
    if p.operadores < 0 or p.premontaje_horas < 0:
        raise HTTPException(400, detail="Valores inválidos")

    creado_por = getattr(me, "username", None)
    if not creado_por and isinstance(me, dict):
        creado_por = me.get("username")
    creado_por = creado_por or "system"

    q = text("""
      INSERT INTO agenda_solicitudes
        (id_lead, fecha_hora, operadores, premontaje_horas, monto, notas, creado_por, estado)
      VALUES
        (:id_lead, :fecha_hora, :operadores, :premontaje_horas, :monto, :notas, :creado_por, 'PENDIENTE')
      RETURNING id, id_lead, fecha_hora, operadores, premontaje_horas, monto, notas, creado_por, estado, created_at
    """)

    with engine.begin() as cn:
        row = cn.execute(q, {
            "id_lead": int(p.id_lead),
            "fecha_hora": p.fecha_hora,
            "operadores": int(p.operadores),
            "premontaje_horas": float(p.premontaje_horas),
            "monto": float(p.monto or 0),
            "notas": (p.notas or "").strip(),
            "creado_por": creado_por,
        }).fetchone()

    return {"ok": True, "item": dict(row._mapping)}


@router.get("/solicitudes")
def listar_solicitudes(
    estado: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    me=Depends(get_current_user),
):
    where = "WHERE 1=1"
    params = {"limit": limit}

    if estado:
        where += " AND estado = :estado"
        params["estado"] = estado

    q = text(f"""
      SELECT id, id_lead, fecha_hora, operadores, premontaje_horas, monto, notas, creado_por, estado, created_at
      FROM agenda_solicitudes
      {where}
      ORDER BY created_at DESC
      LIMIT :limit
    """)

    with engine.connect() as cn:
        rows = cn.execute(q, params).fetchall()

    return {"ok": True, "items": [dict(r._mapping) for r in rows]}


@router.post("/solicitudes/{id}/estado")
def cambiar_estado(id: int, p: AgendaUpdateEstado, me=Depends(get_current_user)):
    q = text("""
      UPDATE agenda_solicitudes
      SET estado = :estado, updated_at = now()
      WHERE id = :id
      RETURNING id, estado
    """)

    with engine.begin() as cn:
        row = cn.execute(q, {"id": int(id), "estado": p.estado}).fetchone()
        if not row:
            raise HTTPException(404, detail="Solicitud no encontrada")

    return {"ok": True, "item": dict(row._mapping)}
