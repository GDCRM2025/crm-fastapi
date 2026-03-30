from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.routers.auth import get_current_user
from backend.core.sgjo import (
    ensure_sgjo_tables,
    seed_sedes_and_points,
    ua_hash,
    haversine_m,
    modality_for_user,
    get_user_rut,
)


router = APIRouter(prefix="/sgjo", tags=["sgjo"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure(db: Session) -> None:
    ensure_sgjo_tables(db)
    seed_sedes_and_points(db)
    db.commit()


@router.get("/config")
def config(db: Session = Depends(get_db), user: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure(db)
    sedes = db.execute(
        text("SELECT id_sede, nombre, lat, lng, radius_m, fallback_radius_m, fallback_accuracy_m FROM public.sgjo_sedes WHERE is_active IS TRUE ORDER BY nombre")
    ).mappings().all()
    puntos = db.execute(
        text(
            """
            SELECT p.id_punto, p.id_sede, p.nombre, p.code
            FROM public.sgjo_puntos p
            JOIN public.sgjo_sedes s ON s.id_sede=p.id_sede
            WHERE p.is_active IS TRUE AND s.is_active IS TRUE
            ORDER BY s.nombre, p.nombre
            """
        )
    ).mappings().all()
    return {"ok": True, "sedes": [dict(r) for r in sedes], "puntos": [dict(r) for r in puntos]}


@router.get("/me")
def sgjo_me(db: Session = Depends(get_db), user: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure(db)
    uid = user.get("id")
    if not str(uid or "").isdigit():
        raise HTTPException(status_code=401, detail="Usuario inválido")
    uid_int = int(uid)
    rut = get_user_rut(db, uid_int)
    role = str(user.get("role") or user.get("rol") or "")
    mod = modality_for_user(role, _now())
    return {"ok": True, "id_usuario": uid_int, "rut": rut or None, "role": role, "modality_today": mod}


@router.post("/device/enroll")
def enroll_device(
    payload: dict = Body(default_factory=dict),
    user_agent: str | None = Header(default=None),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    _ensure(db)
    uid = user.get("id")
    if not str(uid or "").isdigit():
        raise HTTPException(status_code=401, detail="Usuario inválido")
    uid_int = int(uid)
    device_id = str(payload.get("device_id") or "").strip()
    if not device_id or len(device_id) < 12:
        raise HTTPException(status_code=400, detail="device_id requerido")
    h = ua_hash(user_agent or "")
    db.execute(
        text(
            """
            INSERT INTO public.sgjo_dispositivos(id_usuario, device_id, ua_hash)
            VALUES (:u,:d,:h)
            ON CONFLICT (id_usuario, device_id) DO UPDATE
            SET ua_hash=EXCLUDED.ua_hash, revoked_at=NULL
            """
        ),
        {"u": uid_int, "d": device_id, "h": h},
    )
    db.commit()
    return {"ok": True}


def _device_enrolled(db: Session, uid: int, device_id: str, user_agent: str) -> bool:
    try:
        h = ua_hash(user_agent or "")
        v = db.execute(
            text(
                """
                SELECT 1
                FROM public.sgjo_dispositivos
                WHERE id_usuario=:u AND device_id=:d AND revoked_at IS NULL AND ua_hash=:h
                LIMIT 1
                """
            ),
            {"u": int(uid), "d": device_id, "h": h},
        ).scalar()
        return bool(v)
    except Exception:
        return False


@router.post("/marcar")
def marcar(
    payload: dict = Body(default_factory=dict),
    user_agent: str | None = Header(default=None),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    _ensure(db)
    uid = user.get("id")
    if not str(uid or "").isdigit():
        raise HTTPException(status_code=401, detail="Usuario inválido")
    uid_int = int(uid)
    rut = get_user_rut(db, uid_int) or None
    role = str(user.get("role") or user.get("rol") or "")
    modality = modality_for_user(role, _now())

    device_id = str(payload.get("device_id") or "").strip()
    if not device_id:
        raise HTTPException(status_code=400, detail="device_id requerido")
    if not _device_enrolled(db, uid_int, device_id, user_agent or ""):
        raise HTTPException(status_code=403, detail="Dispositivo no enrolado")

    punto_code = str(payload.get("punto_code") or "").strip().upper()
    if not punto_code:
        raise HTTPException(status_code=400, detail="punto_code requerido")

    point = db.execute(
        text(
            """
            SELECT p.id_punto, p.id_sede, s.nombre AS sede, s.lat, s.lng, s.radius_m, s.fallback_radius_m, s.fallback_accuracy_m
            FROM public.sgjo_puntos p
            JOIN public.sgjo_sedes s ON s.id_sede=p.id_sede
            WHERE p.code=:c AND p.is_active IS TRUE AND s.is_active IS TRUE
            LIMIT 1
            """
        ),
        {"c": punto_code},
    ).mappings().first()
    if not point:
        raise HTTPException(status_code=404, detail="Punto no existe")

    lat = payload.get("lat")
    lng = payload.get("lng")
    acc = payload.get("accuracy_m")
    method = str(payload.get("method") or "QR").strip().upper()
    if method not in ("QR", "GEO", "BOTH"):
        method = "QR"

    distance_m = None
    within = None
    used_fb = False
    ok = True
    err = None

    if lat is None or lng is None:
        ok = False
        err = "Falta ubicación (lat/lng)"
    else:
        try:
            lat_f = float(lat)
            lng_f = float(lng)
            acc_f = float(acc) if acc is not None else None
            distance_m = float(haversine_m(lat_f, lng_f, float(point["lat"]), float(point["lng"])))
            hard = int(point["radius_m"] or 20)
            soft = int(point["fallback_radius_m"] or 35)
            acc_thr = int(point["fallback_accuracy_m"] or 25)
            within_hard = distance_m <= float(hard)
            within_soft = distance_m <= float(soft)
            acc_bad = (acc_f is not None) and (acc_f >= float(acc_thr))

            if modality == "REMOTO":
                within = True
            else:
                within = bool(within_hard or (within_soft and acc_bad))
                used_fb = bool((not within_hard) and within_soft and acc_bad)
            if not within:
                ok = False
                err = "Fuera de rango"
        except Exception:
            ok = False
            err = "No pude calcular distancia"

    # tipo IN/OUT: alterna según última marcación OK del usuario (últimas 24h).
    last = db.execute(
        text(
            """
            SELECT tipo
            FROM public.sgjo_marcaciones
            WHERE id_usuario=:u AND ok IS TRUE AND created_at >= (now() - interval '24 hours')
            ORDER BY created_at DESC
            LIMIT 1
            """
        ),
        {"u": uid_int},
    ).scalar()
    tipo = "IN" if str(last or "").upper() != "IN" else "OUT"

    db.execute(
        text(
            """
            INSERT INTO public.sgjo_marcaciones(
              id_usuario, rut, tipo, method,
              id_sede, id_punto,
              lat, lng, accuracy_m, distance_m,
              within_radius, used_fallback,
              ok, error,
              meta
            ) VALUES (
              :u,:rut,:tipo,:method,
              :ids,:idp,
              :lat,:lng,:acc,:dist,
              :within,:fb,
              :ok,:err,
              CAST(:meta AS JSONB)
            )
            """
        ),
        {
            "u": uid_int,
            "rut": rut,
            "tipo": tipo,
            "method": method,
            "ids": int(point["id_sede"]),
            "idp": int(point["id_punto"]),
            "lat": float(lat) if lat is not None else None,
            "lng": float(lng) if lng is not None else None,
            "acc": float(acc) if acc is not None else None,
            "dist": float(distance_m) if distance_m is not None else None,
            "within": bool(within) if within is not None else None,
            "fb": bool(used_fb),
            "ok": bool(ok),
            "err": err,
            "meta": "{}",
        },
    )
    db.commit()

    if not ok:
        raise HTTPException(status_code=400, detail=err or "Marca inválida")

    return {
        "ok": True,
        "tipo": tipo,
        "sede": point["sede"],
        "within_radius": bool(within),
        "used_fallback": bool(used_fb),
        "distance_m": distance_m,
        "modality": modality,
    }

