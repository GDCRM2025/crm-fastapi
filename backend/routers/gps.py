from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from backend.core.db import get_connection
from backend.routers.auth import get_current_user

router = APIRouter(prefix="/gps", tags=["gps"])


class PingIn(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    accuracy: Optional[float] = None
    speed: Optional[float] = None
    heading: Optional[float] = None
    battery: Optional[float] = None


def _ensure_table() -> None:
    with get_connection() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS gps_pings (
                    id_ping SERIAL PRIMARY KEY,
                    id_usuario INT,
                    username TEXT,
                    nombre TEXT,
                    role TEXT,
                    lat DOUBLE PRECISION,
                    lng DOUBLE PRECISION,
                    accuracy DOUBLE PRECISION,
                    speed DOUBLE PRECISION,
                    heading DOUBLE PRECISION,
                    battery DOUBLE PRECISION,
                    created_at TIMESTAMPTZ DEFAULT now()
                )
                """
            )
        )
        conn.commit()


def _role(user: dict) -> str:
    return (user.get("role") or user.get("rol") or "").upper()


def _can_ping(role: str) -> bool:
    return role in ("ADMIN", "JEFE DE OPERACIONES", "OPERACIONES", "CONDUCTOR", "OPERADOR")


def _can_view(role: str) -> bool:
    return role in ("ADMIN", "JEFE DE OPERACIONES", "OPERACIONES", "EJECUTIVO DE VENTAS", "BODEGUERO", "COMPRAS", "OPERADOR")


@router.post("/ping")
def gps_ping(body: PingIn, user: dict = Depends(get_current_user)):
    role = _role(user)
    if not _can_ping(role):
        raise HTTPException(status_code=403, detail="Sin permisos para GPS")

    _ensure_table()
    with get_connection() as conn:
        conn.execute(
            text(
                """
                INSERT INTO gps_pings(id_usuario, username, nombre, role, lat, lng, accuracy, speed, heading, battery)
                VALUES (:id, :username, :nombre, :role, :lat, :lng, :accuracy, :speed, :heading, :battery)
                """
            ),
            {
                "id": int(user.get("id") or 0) or None,
                "username": user.get("username") or "",
                "nombre": user.get("name") or user.get("nombre") or "",
                "role": role,
                "lat": body.lat,
                "lng": body.lng,
                "accuracy": body.accuracy,
                "speed": body.speed,
                "heading": body.heading,
                "battery": body.battery,
            },
        )
        conn.commit()
    return {"ok": True}


@router.get("/live")
def gps_live(user: dict = Depends(get_current_user)):
    role = _role(user)
    if not _can_view(role):
        raise HTTPException(status_code=403, detail="Sin permisos para ver rutas")

    _ensure_table()
    since = datetime.utcnow() - timedelta(hours=2)
    with get_connection() as conn:
        rows = conn.execute(
            text(
                """
                SELECT DISTINCT ON (id_usuario)
                       id_usuario, username, nombre, role, lat, lng, accuracy, speed, heading, battery, created_at
                FROM gps_pings
                WHERE created_at >= :since
                ORDER BY id_usuario, created_at DESC
                """
            ),
            {"since": since},
        ).mappings().all()

    return {"ok": True, "items": list(rows)}
