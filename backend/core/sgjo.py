from __future__ import annotations

import hashlib
import math
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def ensure_sgjo_tables(db: Session) -> None:
    """
    SGJO — Sistema de Gestión de Jornada Operativa
    V1: sedes, puntos, dispositivos, marcaciones (evento real) + auditoría mínima.
    """
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.sgjo_sedes (
              id_sede SERIAL PRIMARY KEY,
              nombre TEXT NOT NULL UNIQUE,
              lat DOUBLE PRECISION NOT NULL,
              lng DOUBLE PRECISION NOT NULL,
              radius_m INTEGER NOT NULL DEFAULT 20,
              fallback_radius_m INTEGER NOT NULL DEFAULT 35,
              fallback_accuracy_m INTEGER NOT NULL DEFAULT 25,
              is_active BOOLEAN NOT NULL DEFAULT TRUE,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.sgjo_puntos (
              id_punto SERIAL PRIMARY KEY,
              id_sede INTEGER NOT NULL REFERENCES public.sgjo_sedes(id_sede) ON DELETE CASCADE,
              nombre TEXT NOT NULL,
              code TEXT NOT NULL UNIQUE,
              is_active BOOLEAN NOT NULL DEFAULT TRUE,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.sgjo_dispositivos (
              id_device SERIAL PRIMARY KEY,
              id_usuario INTEGER NOT NULL,
              device_id TEXT NOT NULL,
              ua_hash TEXT NOT NULL,
              enrolled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              revoked_at TIMESTAMPTZ,
              UNIQUE(id_usuario, device_id)
            )
            """
        )
    )
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.sgjo_marcaciones (
              id_marcacion BIGSERIAL PRIMARY KEY,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              id_usuario INTEGER NOT NULL,
              rut TEXT,
              tipo TEXT NOT NULL,              -- IN / OUT
              method TEXT NOT NULL,            -- QR / GEO / BOTH
              id_sede INTEGER,
              id_punto INTEGER,
              lat DOUBLE PRECISION,
              lng DOUBLE PRECISION,
              accuracy_m DOUBLE PRECISION,
              distance_m DOUBLE PRECISION,
              within_radius BOOLEAN,
              used_fallback BOOLEAN,
              ok BOOLEAN NOT NULL DEFAULT TRUE,
              error TEXT,
              meta JSONB NOT NULL DEFAULT '{}'::jsonb
            )
            """
        )
    )
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_sgjo_marc_user_time ON public.sgjo_marcaciones(id_usuario, created_at DESC)"))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_sgjo_marc_rut_time ON public.sgjo_marcaciones(rut, created_at DESC)"))


def seed_sedes_and_points(db: Session) -> None:
    """
    Seed idempotente de sedes/punto único (por sede) según definición actual.
    """
    # Sedes: coords entregadas por el usuario.
    sedes = [
        ("Rolfis", -33.4307435, -70.5538551),
        ("Greendiamond", -33.4280497, -70.5538937),
    ]
    for nombre, lat, lng in sedes:
        db.execute(
            text(
                """
                INSERT INTO public.sgjo_sedes(nombre,lat,lng,radius_m,fallback_radius_m,fallback_accuracy_m,is_active)
                VALUES (:n,:lat,:lng,20,35,25,TRUE)
                ON CONFLICT (nombre) DO UPDATE
                SET lat=EXCLUDED.lat,
                    lng=EXCLUDED.lng,
                    radius_m=EXCLUDED.radius_m,
                    fallback_radius_m=EXCLUDED.fallback_radius_m,
                    fallback_accuracy_m=EXCLUDED.fallback_accuracy_m,
                    is_active=TRUE
                """
            ),
            {"n": nombre, "lat": float(lat), "lng": float(lng)},
        )

    # Punto único por sede: code fijo e imprimible.
    rows = db.execute(text("SELECT id_sede, nombre FROM public.sgjo_sedes WHERE is_active IS TRUE")).mappings().all()
    for r in rows:
        sede_name = str(r.get("nombre") or "").strip()
        code = "SGJO-" + sede_name.upper().replace(" ", "").replace("-", "")
        db.execute(
            text(
                """
                INSERT INTO public.sgjo_puntos(id_sede,nombre,code,is_active)
                VALUES (:id,'Punto único',:c,TRUE)
                ON CONFLICT (code) DO UPDATE SET is_active=TRUE
                """
            ),
            {"id": int(r["id_sede"]), "c": code},
        )


def ua_hash(user_agent: str) -> str:
    s = (user_agent or "").strip().encode("utf-8")
    return hashlib.sha256(s).hexdigest()


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """
    Distancia aproximada en metros entre dos puntos.
    """
    r = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def modality_for_user(role: str, when: datetime) -> str:
    """
    V1 (hardcoded por rol, configurable luego por RRHH):
    - EJECUTIVO: remoto (A), con 1 día presencial por semana (default miércoles).
    - DISEÑADOR: remoto solo miércoles.
    - Resto: presencial.
    """
    r = (role or "").upper()
    wd = int(when.weekday())  # 0=Mon .. 2=Wed
    if "EJECUTIV" in r:
        return "PRESENCIAL" if wd == 2 else "REMOTO"
    if "DISE" in r:
        return "REMOTO" if wd == 2 else "PRESENCIAL"
    return "PRESENCIAL"


def get_user_rut(db: Session, id_usuario: int) -> str:
    try:
        uid = int(id_usuario)
        v = db.execute(text("SELECT COALESCE(rut,'') FROM public.usuarios WHERE id_usuario=:id LIMIT 1"), {"id": uid}).scalar()
        rut = str(v or "").strip()
        if rut:
            return rut
        # Fallback: si el usuario está vinculado en RRHH, usa ese RUT (evita depender de usuarios.rut).
        v2 = db.execute(
            text(
                """
                SELECT COALESCE(rut,'')
                FROM public.rrhh_staff
                WHERE id_usuario=:id AND is_active IS TRUE
                ORDER BY id_staff DESC
                LIMIT 1
                """
            ),
            {"id": uid},
        ).scalar()
        return str(v2 or "").strip()
    except Exception:
        return ""
