from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
import threading
from typing import Any
import json

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, Query
from fastapi.responses import Response, RedirectResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.routers.auth import get_current_user
from backend.core.qr_local import make_qr_png_target, make_qr_svg
from backend.core.sgjo import (
    ensure_sgjo_tables,
    seed_sedes_and_points,
    ua_hash,
    haversine_m,
    get_user_rut,
)


router = APIRouter(prefix="/rrhh/sgjo", tags=["rrhh-sgjo"])

_SGJO_ENSURED = False
_SGJO_ENSURE_LOCK = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure(db: Session) -> None:
    # IMPORTANTE: DDL en hot paths puede causar locks/colas (Passenger "queue full").
    # Esto se ejecuta 1 vez por proceso para estabilizar performance.
    global _SGJO_ENSURED
    if _SGJO_ENSURED:
        return
    with _SGJO_ENSURE_LOCK:
        if _SGJO_ENSURED:
            return
        ensure_sgjo_tables(db)
        seed_sedes_and_points(db)
        # Extend RRHH staff schema (idempotente) para modalidad/turno mixto.
        try:
            db.execute(text("ALTER TABLE rrhh_staff ADD COLUMN IF NOT EXISTS presencial_dow SMALLINT"))
            db.execute(text("ALTER TABLE rrhh_staff ADD COLUMN IF NOT EXISTS modalidad_default TEXT"))
            db.execute(text("ALTER TABLE rrhh_staff ADD COLUMN IF NOT EXISTS puede_marcar BOOLEAN DEFAULT TRUE"))
            db.execute(text("ALTER TABLE rrhh_staff ADD COLUMN IF NOT EXISTS marcacion_method TEXT DEFAULT 'BOTH'"))
        except Exception:
            pass
        # Solicitudes de enrolamiento de dispositivo (aprobación por RRHH/SuperAdmin).
        try:
            db.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS public.sgjo_device_requests (
                      id_request BIGSERIAL PRIMARY KEY,
                      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                      decided_at TIMESTAMPTZ,
                      status TEXT NOT NULL DEFAULT 'pending', -- pending/approved/rejected
                      id_usuario BIGINT NOT NULL,
                      device_id TEXT NOT NULL,
                      device_name TEXT,
                      ua_hash TEXT NOT NULL,
                      decided_by BIGINT,
                      note TEXT,
                      UNIQUE(id_usuario, device_id, status)
                    )
                    """
                )
            )
        except Exception:
            pass
        try:
            db.execute(text("ALTER TABLE public.sgjo_device_requests ADD COLUMN IF NOT EXISTS device_name TEXT"))
        except Exception:
            pass
        try:
            db.execute(text("ALTER TABLE public.sgjo_device_requests ADD COLUMN IF NOT EXISTS device_kind TEXT"))
        except Exception:
            pass
        try:
            db.execute(text("ALTER TABLE public.sgjo_dispositivos ADD COLUMN IF NOT EXISTS device_name TEXT"))
        except Exception:
            pass
        try:
            db.execute(text("ALTER TABLE public.sgjo_dispositivos ADD COLUMN IF NOT EXISTS device_kind TEXT"))
        except Exception:
            pass
        # Backfill best-effort de device_kind para históricos (evita pruning incorrecto).
        try:
            db.execute(
                text(
                    """
                    UPDATE public.sgjo_dispositivos
                    SET device_kind = CASE
                      WHEN lower(COALESCE(device_kind,'')) IN ('phone','pc') THEN upper(device_kind)
                      WHEN lower(COALESCE(device_name,'')) ~ '(iphone|android|cel|phone|movil|móvil)' THEN 'PHONE'
                      ELSE 'PC'
                    END
                    WHERE COALESCE(NULLIF(btrim(device_kind),''), '') = ''
                    """
                )
            )
        except Exception:
            pass
        # Debug de marcaciones (telemetría operacional) para investigar intermitencias.
        try:
            db.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS public.sgjo_marks_debug (
                      id_debug BIGSERIAL PRIMARY KEY,
                      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                      id_usuario BIGINT,
                      device_id TEXT,
                      punto_code TEXT,
                      method TEXT,
                      tipo TEXT,
                      ok BOOLEAN,
                      within_radius BOOLEAN,
                      used_fallback BOOLEAN,
                      distance_m DOUBLE PRECISION,
                      accuracy_m DOUBLE PRECISION,
                      lat DOUBLE PRECISION,
                      lng DOUBLE PRECISION,
                      err TEXT,
                      ua_hash TEXT,
                      ip TEXT,
                      payload_json TEXT
                    )
                    """
                )
            )
        except Exception:
            pass
        try:
            db.commit()
        except Exception:
            db.rollback()
        _SGJO_ENSURED = True


def _log_sgjo_mark_debug(
    *,
    db: Session,
    id_usuario: int | None,
    device_id: str | None,
    punto_code: str | None,
    method: str | None,
    tipo: str | None,
    ok: bool | None,
    within: bool | None,
    used_fallback: bool | None,
    distance_m: float | None,
    accuracy_m: float | None,
    lat: Any,
    lng: Any,
    err: str | None,
    ua: str | None,
    ip: str | None,
    payload_json: str | None,
) -> None:
    try:
        uah = ua_hash(ua or "")
    except Exception:
        uah = ""
    try:
        db.execute(
            text(
                """
                INSERT INTO public.sgjo_marks_debug(
                  id_usuario, device_id, punto_code, method, tipo,
                  ok, within_radius, used_fallback,
                  distance_m, accuracy_m, lat, lng,
                  err, ua_hash, ip, payload_json
                ) VALUES (
                  :u, :d, :p, :m, :t,
                  :ok, :w, :fb,
                  :dist, :acc, :lat, :lng,
                  :err, :uah, :ip, :pj
                )
                """
            ),
            {
                "u": (int(id_usuario) if str(id_usuario or "").isdigit() else None),
                "d": (str(device_id or "")[:120] if device_id is not None else None),
                "p": (str(punto_code or "")[:80] if punto_code is not None else None),
                "m": (str(method or "")[:16] if method is not None else None),
                "t": (str(tipo or "")[:8] if tipo is not None else None),
                "ok": ok,
                "w": within,
                "fb": used_fallback,
                "dist": (float(distance_m) if distance_m is not None else None),
                "acc": (float(accuracy_m) if accuracy_m is not None else None),
                "lat": (float(lat) if lat is not None and str(lat).strip() != "" else None),
                "lng": (float(lng) if lng is not None and str(lng).strip() != "" else None),
                "err": (str(err or "")[:200] if err else None),
                "uah": (str(uah or "")[:80] if uah else None),
                "ip": (str(ip or "")[:80] if ip else None),
                "pj": (str(payload_json or "")[:4000] if payload_json else None),
            },
        )
        try:
            db.commit()
        except Exception:
            db.rollback()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass


def _exec_presencial_dow(uid: int, rut: str) -> int:
    """
    Ejecutivos: 1 día presencial por semana.
    Se asigna determinísticamente a MAR/MIE/JUE (1/2/3) usando hash del RUT (o uid).
    """
    choices = [1, 2, 3]  # Tue/Wed/Thu (0=Mon)
    key = (rut or str(uid)).strip().lower().encode("utf-8")
    h = int(hashlib.sha256(key).hexdigest(), 16)
    return choices[h % len(choices)]


def _rrhh_staff_row(db: Session, rut: str) -> dict[str, Any] | None:
    if not rut:
        return None
    try:
        row = db.execute(
            text(
                """
                SELECT rol, presencial_dow, modalidad_default
                FROM rrhh_staff
                WHERE lower(rut)=lower(:r) AND is_active IS TRUE
                LIMIT 1
                """
            ),
            {"r": rut},
        ).mappings().first()
        return dict(row) if row else None
    except Exception:
        return None


def _rrhh_marking_policy(db: Session, rut: str) -> dict[str, Any] | None:
    if not rut:
        return None
    try:
        row = db.execute(
            text(
                """
                SELECT
                  COALESCE(puede_marcar, TRUE) AS puede_marcar,
                  COALESCE(NULLIF(btrim(marcacion_method),''), 'BOTH') AS marcacion_method,
                  COALESCE(NULLIF(btrim(telefono),''), NULL) AS telefono
                FROM public.rrhh_staff
                WHERE lower(rut)=lower(:r)
                  AND is_active IS TRUE
                ORDER BY id_staff DESC
                LIMIT 1
                """
            ),
            {"r": rut},
        ).mappings().first()
        return dict(row) if row else None
    except Exception:
        return None


def modality_for_user(db: Session, *, uid: int, rut: str, role: str, when: datetime) -> dict[str, Any]:
    """
    Modalidad V1 (guardada en RRHH, con fallback determinístico):
    - EJECUTIVO: REMOTO, salvo 1 día presencial (mar/mie/jue) por usuario.
    - DISEÑADOR: REMOTO solo miércoles.
    - Resto: PRESENCIAL.
    """
    staff = _rrhh_staff_row(db, rut)
    staff_role = str((staff or {}).get("rol") or "")
    staff_modality = str((staff or {}).get("modalidad_default") or "").strip().upper()
    r = (staff_role or role or "").upper()
    wd = int(when.weekday())  # 0=Mon

    # RRHH override: rrhh_staff.presencial_dow si existe para el RUT
    presencial_dow = None
    try:
        v = (staff or {}).get("presencial_dow")
        if v is not None and str(v).strip() != "":
            presencial_dow = int(v)
    except Exception:
        presencial_dow = None

    if staff_modality in ("PRESENCIAL", "REMOTO"):
        return {"modality": staff_modality, "presencial_dow": presencial_dow}

    if "EJECUTIV" in r:
        if presencial_dow is None:
            presencial_dow = _exec_presencial_dow(uid, rut)
        return {
            "modality": "PRESENCIAL" if wd == int(presencial_dow) else "REMOTO",
            "presencial_dow": int(presencial_dow),
        }

    if "DISE" in r:
        return {"modality": "REMOTO" if wd == 2 else "PRESENCIAL", "presencial_dow": None}

    return {"modality": "PRESENCIAL", "presencial_dow": None}


@router.get("/config")
def config(db: Session = Depends(get_db), user: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure(db)
    sedes = db.execute(
        text(
            "SELECT id_sede, nombre, lat, lng, radius_m, fallback_radius_m, fallback_accuracy_m "
            "FROM public.sgjo_sedes WHERE is_active IS TRUE ORDER BY nombre"
        )
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


def _role_key(user: dict) -> str:
    return str(user.get("role") or user.get("rol") or "").strip().upper()


def _is_admin(user: dict) -> bool:
    r = _role_key(user)
    return ("SUPERADMIN" in r) or (r == "ADMIN") or ("JEFE DE OPERACIONES" in r) or ("COMPRAS" in r) or ("OPERACIONES" in r)

@router.get("/qr", response_class=Response, response_model=None)
@router.head("/qr", include_in_schema=False)
def qr_png(
    p: str,
    size: int = 900,
    src: str = "auto",
    request: Request = None,  # type: ignore[assignment]
) -> Response:
    """
    QR imprimible (PNG) para abrir la pantalla de marcación con el punto preseleccionado.

    - NO requiere token (se imprime/pega en sede).
    - El QR NO registra la marca: solo abre la URL. La marcación exige login + dispositivo enrolado + geolocalización.
    """
    code = str(p or "").strip().upper()
    if not code or len(code) > 64:
        raise HTTPException(status_code=400, detail="p inválido")

    try:
        sz = int(size)
        if sz < 180:
            sz = 180
        if sz > 1200:
            sz = 1200
    except Exception:
        sz = 420

    # `request` siempre existe en FastAPI, pero dejamos fallback por compatibilidad/harness.
    app_url = (os.getenv("APP_URL") or "").strip().rstrip("/")
    if not app_url:
        try:
            if request:
                app_url = str(request.base_url).rstrip("/")
        except Exception:
            app_url = ""
    if not app_url:
        # Worst-case fallback; should never happen in prod.
        app_url = "https://greendiamond.cl"

    from urllib.parse import quote_plus

    # Usamos un endpoint corto para evitar QRs densos (mejor lectura en cámara).
    mark_url = f"{app_url}/crm/rrhh/sgjo/m?p={quote_plus(code)}"
    src0 = str(src or "auto").strip().lower()
    if src0 not in ("auto", "google", "local", "svg"):
        src0 = "auto"

    def _resp_png(payload: bytes, source: str) -> Response:
        return Response(
            content=payload,
            media_type="image/png",
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": f'inline; filename="QR-{code}.png"',
                "X-QR-Source": source,
            },
        )

    def _looks_like_png(b: bytes) -> bool:
        return bool(b) and len(b) > 16 and b[:8] == b"\x89PNG\r\n\x1a\n"

    def _fetch_png(url: str, *, source: str) -> bytes | None:
        try:
            import requests

            r = requests.get(
                url,
                timeout=7,
                headers={
                    "Accept": "image/png,image/*;q=0.9,*/*;q=0.1",
                    "User-Agent": "GDCRM/qr (passenger)",
                },
                allow_redirects=True,
            )
            ct = (r.headers.get("content-type") or "").lower()
            if r.status_code == 200 and r.content and ct.startswith("image/") and _looks_like_png(r.content):
                return r.content
            return None
        except Exception:
            return None

    # Hotfix: generadores externos (PNG real) para asegurar lecturas en iPhone/Android/BarcodeDetector.
    # Si el hosting bloquea un dominio, probamos el siguiente.
    if src0 in ("auto", "google"):
        from urllib.parse import quote_plus, quote

        providers = [
            ("quickchart", f"https://quickchart.io/qr?text={quote(mark_url)}&size={sz}"),
            ("qrserver", f"https://api.qrserver.com/v1/create-qr-code/?size={sz}x{sz}&data={quote_plus(mark_url)}"),
            ("google", f"https://chart.googleapis.com/chart?cht=qr&chs={sz}x{sz}&chld=L|4&chl={quote_plus(mark_url)}"),
        ]
        if src0 == "google":
            # Forzar solo el provider Google (útil para diagnosticar).
            providers = [p for p in providers if p[0] == "google"]
        for src_name, url in providers:
            payload = _fetch_png(url, source=src_name)
            if payload:
                return _resp_png(payload, src_name)
        if src0 == "google":
            raise HTTPException(status_code=502, detail="No pude generar QR externo (google): bloqueo/red/DNS.")

    # Generación local (último recurso). Si el generador local vuelve a ser ilegible, preferimos fallar explícitamente.
    if src0 in ("auto", "local"):
        try:
            png = make_qr_png_target(mark_url, target_px=sz, border=10)
            if _looks_like_png(png):
                return _resp_png(png, "local")
            raise Exception("local invalid png")
        except Exception:
            if src0 == "local":
                raise HTTPException(status_code=500, detail="No pude generar QR local.")

    # SVG (útil para impresión)
    try:
        scale = 8 if sz >= 520 else 7 if sz >= 420 else 6
        svg = make_qr_svg(mark_url, scale=scale, border=10)
        return Response(
            content=svg,
            media_type="image/svg+xml",
            headers={"Cache-Control": "no-store"},
        )
    except Exception:
        # Worst-case fallback 1x1
        return Response(
            content=(
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
                b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc`\x00\x00\x00\x02\x00"
                b"\x01\xe2!\xbc3\x00\x00\x00\x00IEND\xaeB`\x82"
            ),
            media_type="image/png",
            headers={"Cache-Control": "no-store"},
        )


@router.get("/m", include_in_schema=False)
@router.head("/m", include_in_schema=False)
def sgjo_mark_redirect(p: str, request: Request) -> RedirectResponse:
    """
    Endpoint corto (para QR). Redirige a la vista de marcación.
    """
    code = str(p or "").strip().upper()
    if not code or len(code) > 64:
        raise HTTPException(status_code=400, detail="p inválido")
    next_url = f"/crm/web/views/rrhh_sgjo_marcacion.html?p={code}"
    # Siempre redirigimos a la vista: ella misma toma token desde cookie/localStorage y, si falta,
    # guía a login con retorno. Esto evita falsos "no logueado" en PWA/iframes.
    return RedirectResponse(url=next_url, status_code=302)


def _normalize_key(s: str) -> str:
    return "".join(ch for ch in (s or "").strip().upper() if ch.isalnum())


@router.get("/today")
def today_status(db: Session = Depends(get_db), user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """
    Estado de marcación del día (Chile) para el usuario actual.
    Usado para UX: pedir IN al iniciar sesión solo 1 vez por día.
    """
    _ensure(db)
    try:
        db.rollback()
    except Exception:
        pass
    uid = user.get("id")
    if not str(uid or "").isdigit():
        raise HTTPException(status_code=401, detail="Usuario inválido")
    uid_int = int(uid)

    # RUT y policy RRHH
    rut = get_user_rut(db, uid_int)
    pol = _rrhh_marking_policy(db, rut or "") if rut else None
    puede = None if pol is None else bool(pol.get("puede_marcar"))
    method_allowed = None if pol is None else str(pol.get("marcacion_method") or "BOTH").upper()

    # Default punto por centro de costo (match nombre sede)
    default_punto = None
    try:
        cc = db.execute(
            text(
                """
                SELECT COALESCE(NULLIF(btrim(centro_costo),''), NULLIF(btrim(centro),''), NULLIF(btrim(cc),''), NULLIF(btrim(centro_cost),''), '') AS cc
                FROM public.rrhh_staff
                WHERE lower(rut)=lower(:r) AND is_active IS TRUE
                ORDER BY id_staff DESC
                LIMIT 1
                """
            ),
            {"r": rut or ""},
        ).scalar()
        cc_key = _normalize_key(str(cc or ""))
        if cc_key:
            sedes = db.execute(text("SELECT id_sede, nombre FROM public.sgjo_sedes WHERE is_active IS TRUE")).mappings().all()
            sid = None
            for s in sedes:
                if _normalize_key(str(s.get("nombre") or "")) == cc_key:
                    sid = int(s["id_sede"])
                    break
            if sid is None:
                # Heurística: ROLFI -> ROLFIS
                for s in sedes:
                    if cc_key in _normalize_key(str(s.get("nombre") or "")) or _normalize_key(str(s.get("nombre") or "")) in cc_key:
                        sid = int(s["id_sede"])
                        break
            if sid is not None:
                default_punto = db.execute(
                    text(
                        """
                        SELECT code
                        FROM public.sgjo_puntos
                        WHERE id_sede=:sid AND is_active IS TRUE
                        ORDER BY id_punto
                        LIMIT 1
                        """
                    ),
                    {"sid": sid},
                ).scalar()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        default_punto = None

    try:
        rows = db.execute(
            text(
                """
                SELECT tipo, method, created_at
                FROM public.sgjo_marcaciones
                WHERE id_usuario=:u
                  AND ok IS TRUE
                  AND ((created_at AT TIME ZONE 'America/Santiago')::date = (now() AT TIME ZONE 'America/Santiago')::date)
                ORDER BY created_at ASC
                """
            ),
            {"u": uid_int},
        ).mappings().all()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        rows = []
    tipos = [str(r.get("tipo") or "").upper() for r in rows]
    has_in = "IN" in tipos
    has_out = "OUT" in tipos
    last = rows[-1] if rows else None
    try:
        today = db.execute(text("SELECT (now() AT TIME ZONE 'America/Santiago')::date")).scalar()
        today_s = str(today)
    except Exception:
        today_s = ""

    return {
        "ok": True,
        "today": today_s,
        "has_in": bool(has_in),
        "has_out": bool(has_out),
        "last_tipo": (str(last.get("tipo") or "").upper() if last else None),
        "last_at": (str(last.get("created_at")) if last else None),
        "puede_marcar": puede,
        "marcacion_method": method_allowed,
        "default_punto_code": (str(default_punto or "").strip().upper() or None),
    }


@router.patch("/admin/sede/{id_sede}")
def admin_update_sede(
    id_sede: int,
    payload: dict = Body(default_factory=dict),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    _ensure(db)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Solo Admin/SuperAdmin.")
    radius_m = payload.get("radius_m")
    fb_radius_m = payload.get("fallback_radius_m")
    fb_acc_m = payload.get("fallback_accuracy_m")
    data = {"id": int(id_sede)}
    sets = []
    if radius_m is not None:
        sets.append("radius_m=:r")
        data["r"] = int(radius_m)
    if fb_radius_m is not None:
        sets.append("fallback_radius_m=:fr")
        data["fr"] = int(fb_radius_m)
    if fb_acc_m is not None:
        sets.append("fallback_accuracy_m=:fa")
        data["fa"] = int(fb_acc_m)
    if not sets:
        return {"ok": True, "updated": False}
    db.execute(text(f"UPDATE public.sgjo_sedes SET {', '.join(sets)} WHERE id_sede=:id"), data)
    db.commit()
    return {"ok": True, "updated": True}


@router.get("/me")
def sgjo_me(db: Session = Depends(get_db), user: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure(db)
    uid = user.get("id")
    if not str(uid or "").isdigit():
        raise HTTPException(status_code=401, detail="Usuario inválido")
    uid_int = int(uid)
    rut = get_user_rut(db, uid_int)
    role = str(user.get("role") or user.get("rol") or "")
    mod = modality_for_user(db, uid=uid_int, rut=rut, role=role, when=_now())
    pol = _rrhh_marking_policy(db, rut or "") if rut else None

    # Dispositivo por defecto: cualquier dispositivo aprobado del usuario (el más reciente).
    device_id_default = ""
    devices_count = 0
    try:
        row = db.execute(
            text(
                """
                SELECT device_id
                FROM public.sgjo_dispositivos
                WHERE id_usuario=:u AND revoked_at IS NULL
                ORDER BY enrolled_at DESC, id_device DESC
                LIMIT 1
                """
            ),
            {"u": uid_int},
        ).scalar()
        device_id_default = str(row or "").strip()
    except Exception:
        device_id_default = ""
    try:
        devices_count = int(
            db.execute(
                text(
                    """
                    SELECT COUNT(1)
                    FROM public.sgjo_dispositivos
                    WHERE id_usuario=:u AND revoked_at IS NULL
                    """
                ),
                {"u": uid_int},
            ).scalar()
            or 0
        )
    except Exception:
        devices_count = 0
    return {
        "ok": True,
        "id_usuario": uid_int,
        "rut": rut or None,
        "role": role,
        "modality_today": mod["modality"],
        "presencial_dow": mod.get("presencial_dow"),
        "puede_marcar": (bool(pol.get("puede_marcar")) if pol else None),
        "marcacion_method": (str(pol.get("marcacion_method")) if pol else None),
        "telefono_rrhh": (str(pol.get("telefono")) if (pol and pol.get("telefono")) else None),
        "has_enrolled_device": bool(device_id_default),
        "enrolled_devices_count": devices_count,
        "device_id_default": (device_id_default or None),
    }


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
    rut_hint = str(payload.get("rut") or payload.get("rut_hint") or "").strip()[:32]
    tel_hint = str(payload.get("telefono") or payload.get("phone") or payload.get("tel") or "").strip()[:40]
    device_kind = str(payload.get("device_kind") or payload.get("kind") or payload.get("device_type") or "").strip().upper()
    if device_kind in ("CEL", "CELULAR", "MOBILE"):
        device_kind = "PHONE"
    if device_kind not in ("PHONE", "PC"):
        device_kind = "PHONE" if tel_hint else "PC"
    device_name = str(payload.get("device_name") or payload.get("deviceName") or payload.get("name") or "").strip()[:80] or None
    note_hint = ""
    if rut_hint:
        note_hint += f"RUT_HINT={rut_hint}\n"
    if tel_hint:
        note_hint += f"TEL_HINT={tel_hint}\n"
    if device_name:
        note_hint += f"DEVICE_NAME={device_name}\n"
    if device_kind:
        note_hint += f"DEVICE_KIND={device_kind}\n"
    note_hint = note_hint.strip() or None

    # Ya enrolado (con UA actual) => OK directo.
    if _device_enrolled(db, uid_int, device_id, user_agent or ""):
        return {"ok": True, "already": True}

    # Crea solicitud pendiente (requiere aprobación admin). Idempotente.
    try:
        pending = db.execute(
            text(
                """
                SELECT id_request
                FROM public.sgjo_device_requests
                WHERE id_usuario=:u AND device_id=:d AND status='pending'
                ORDER BY id_request DESC
                LIMIT 1
                """
            ),
            {"u": uid_int, "d": device_id},
        ).scalar()
        if not pending:
            db.execute(
                text(
                    """
                    INSERT INTO public.sgjo_device_requests(id_usuario, device_id, device_name, device_kind, ua_hash, status, note)
                    VALUES (:u,:d,:dn,:dk,:h,'pending', :n)
                    """
                ),
                {"u": uid_int, "d": device_id, "dn": device_name, "dk": device_kind, "h": h, "n": note_hint},
            )
            # Relee id_request recién creado para notificación interna.
            pending = db.execute(
                text(
                    """
                    SELECT id_request
                    FROM public.sgjo_device_requests
                    WHERE id_usuario=:u AND device_id=:d AND status='pending'
                    ORDER BY id_request DESC
                    LIMIT 1
                    """
                ),
                {"u": uid_int, "d": device_id},
            ).scalar()
        else:
            # Si ya existe, intentamos guardar hints en note (best-effort).
            if note_hint:
                try:
                    db.execute(
                        text(
                            """
                            UPDATE public.sgjo_device_requests
                            SET note = COALESCE(NULLIF(note,''), :n),
                                device_name = COALESCE(NULLIF(device_name,''), :dn),
                                device_kind = COALESCE(NULLIF(device_kind,''), :dk)
                            WHERE id_request=:id
                            """
                        ),
                        {"id": int(pending), "n": note_hint, "dn": device_name or "", "dk": device_kind or ""},
                    )
                except Exception:
                    pass
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"No pude crear solicitud: {e}")

    # Notificación interna (roles) para aprobación. No depende de push del usuario.
    try:
        from backend.core.system_notifs import push_system_notif

        rid = int(pending) if str(pending or "").isdigit() else int(uid_int)
        title = "RRHH · Enrolamiento de dispositivo"
        body_txt = f"Pendiente: {user.get('name') or user.get('username')} · UID {uid_int}"
        payload = {"id_request": int(pending) if str(pending or "").isdigit() else None, "id_usuario": uid_int, "device_id": device_id}
        push_system_notif(db, kind="RRHH_DEVICE_ENROLL", role_target="RRHH", id_lead=rid, title=title, body=body_txt, payload=payload)
        push_system_notif(db, kind="RRHH_DEVICE_ENROLL", role_target="ADMIN", id_lead=rid, title=title, body=body_txt, payload=payload)
        push_system_notif(db, kind="RRHH_DEVICE_ENROLL", role_target="SUPERADMIN", id_lead=rid, title=title, body=body_txt, payload=payload)
    except Exception:
        pass

    # Aviso por correo (best-effort). Push no sirve aquí porque aún no está enrolado.
    try:
        from backend.core.email import send_email_group

        rrhh_to = (os.getenv("RRHH_NOTIFY_TO") or "c.grez@clavetributariacontadores.cl").strip()
        rrhh_cc = [x.strip() for x in str(os.getenv("RRHH_NOTIFY_CC") or "").split(",") if x.strip()]
        to_list = [rrhh_to] + rrhh_cc if rrhh_to else rrhh_cc
        if to_list:
            rut = ""
            tel = ""
            try:
                rut = get_user_rut(db, uid_int) or ""
            except Exception:
                rut = ""
            try:
                tel = str(user.get("telefono") or "").strip()
                if not tel:
                    tel = str(
                        db.execute(
                            text("SELECT COALESCE(NULLIF(btrim(telefono),''), '') FROM public.usuarios WHERE id_usuario=:u LIMIT 1"),
                            {"u": uid_int},
                        ).scalar()
                        or ""
                    ).strip()
            except Exception:
                tel = ""
            subj = f"RRHH · Solicitud enrolamiento dispositivo · {user.get('name') or user.get('username')}"
            body = (
                f"Usuario ID: {uid_int}\n"
                f"Usuario: {user.get('username')}\n"
                f"Nombre: {user.get('name') or user.get('nombre')}\n"
                f"RUT: {rut}\n"
                f"Teléfono: {tel}\n"
                f"Device ID: {device_id}\n"
                f"UA hash: {h}\n\n"
                f"Aprueba desde RRHH → Puntos + QR → 'Solicitudes de dispositivos'.\n"
            )
            send_email_group(to_list, subj, body)
    except Exception:
        pass

    return {
        "ok": False,
        "pending_approval": True,
        "id_request": int(pending) if str(pending or "").isdigit() else None,
        "detail": "Solicitud enviada. Un admin debe aprobar este dispositivo.",
    }


@router.post("/device/issue")
def issue_device_id(
    prefer_existing: int = Query(default=1, ge=0, le=1),
    user_agent: str | None = Header(default=None),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Devuelve un device_id estable por usuario + UA hash.
    Motivo: en iOS/Safari/iframes puede fallar localStorage/cookies y se pierde el device_id,
    provocando re-enrolamientos. Con este endpoint el frontend puede recuperar siempre el mismo ID
    sin depender de storage local.
    """
    _ensure(db)
    uid = user.get("id")
    if not str(uid or "").isdigit():
        raise HTTPException(status_code=401, detail="Usuario inválido")
    uid_int = int(uid)

    # Si el usuario ya tiene dispositivos aprobados, podemos devolver el más reciente.
    # Útil para "recuperar" un device_id válido cuando iOS/Safari pierde storage.
    # Para enrolar un NUEVO dispositivo, el frontend debe llamar con prefer_existing=0.
    if int(prefer_existing or 0) == 1:
        try:
            did_db = (
                db.execute(
                    text(
                        """
                        SELECT device_id
                        FROM public.sgjo_dispositivos
                        WHERE id_usuario=:u AND revoked_at IS NULL
                        ORDER BY enrolled_at DESC, id_device DESC
                        LIMIT 1
                        """
                    ),
                    {"u": uid_int},
                ).scalar()
                or ""
            )
            did_db = str(did_db).strip()
            if did_db:
                return {"ok": True, "device_id": did_db, "source": "db_latest"}
        except Exception:
            pass

    h = ua_hash(user_agent or "")
    if not h:
        h = "no-ua"
    raw = f"{uid_int}:{h}".encode("utf-8", errors="ignore")
    did = "ua-" + hashlib.sha256(raw).hexdigest()[:32]
    return {"ok": True, "device_id": did, "source": "ua_hash"}


@router.get("/admin/device_requests")
def admin_device_requests(
    status: str = "pending",
    id_usuario: int | None = None,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    _ensure(db)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Solo Admin/SuperAdmin.")
    st = str(status or "pending").strip().lower()
    if st not in ("pending", "approved", "rejected"):
        st = "pending"
    where = "WHERE r.status = :st"
    params: dict[str, Any] = {"st": st}
    if id_usuario is not None:
        try:
            uid = int(id_usuario)
            where += " AND r.id_usuario = :uid"
            params["uid"] = uid
        except Exception:
            pass
    rows = db.execute(
        text(
            f"""
            SELECT r.id_request, r.created_at, r.decided_at, r.status, r.id_usuario, r.device_id,
                   COALESCE(r.device_name,'') AS device_name,
                   COALESCE(r.note,'') AS note,
                   u.username, COALESCE(NULLIF(btrim(u.nombre),''), u.username) AS display,
                   COALESCE(u.email,'') AS email,
                   COALESCE(u.telefono,'') AS telefono,
                   COALESCE(s.rut,'') AS rut_rrhh,
                   COALESCE(s.telefono,'') AS telefono_rrhh,
                   COALESCE(s.marcacion_method,'') AS marcacion_method
            FROM public.sgjo_device_requests r
            LEFT JOIN public.usuarios u ON u.id_usuario = r.id_usuario
            LEFT JOIN LATERAL (
              SELECT rut, telefono, marcacion_method
              FROM public.rrhh_staff
              WHERE id_usuario = r.id_usuario AND is_active IS TRUE
              ORDER BY id_staff DESC
              LIMIT 1
            ) s ON TRUE
            {where}
            ORDER BY r.created_at DESC, r.id_request DESC
            LIMIT 200
            """
        ),
        params,
    ).mappings().all()
    return {"ok": True, "items": [dict(r) for r in rows]}


@router.get("/admin/marks/stats")
def admin_marks_stats(
    days: int = Query(default=1, ge=1, le=31),
    id_usuario: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Telemetría: resumen de intentos de marcación (sgjo_marks_debug) para detectar causas.
    """
    _ensure(db)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Solo Admin/SuperAdmin.")
    n = int(days or 1)
    if n < 1:
        n = 1
    if n > 31:
        n = 31
    params: dict[str, Any] = {"n": n}
    where = ["created_at >= (now() - (:n || ' days')::interval)"]
    if id_usuario is not None:
        try:
            params["u"] = int(id_usuario)
            where.append("id_usuario = :u")
        except Exception:
            pass
    wsql = " AND ".join(where)
    rows = db.execute(
        text(
            f"""
            SELECT
              COALESCE(method,'') AS method,
              COALESCE(tipo,'') AS tipo,
              COALESCE(err,'') AS err,
              COUNT(1) AS cnt,
              SUM(CASE WHEN ok IS TRUE THEN 1 ELSE 0 END) AS ok_cnt,
              SUM(CASE WHEN ok IS NOT TRUE THEN 1 ELSE 0 END) AS fail_cnt,
              SUM(CASE WHEN within_radius IS FALSE THEN 1 ELSE 0 END) AS out_of_range_cnt
            FROM public.sgjo_marks_debug
            WHERE {wsql}
            GROUP BY 1,2,3
            ORDER BY cnt DESC
            LIMIT 80
            """
        ),
        params,
    ).mappings().all()
    total = db.execute(
        text(f"SELECT COUNT(1) FROM public.sgjo_marks_debug WHERE {wsql}"),
        params,
    ).scalar()
    return {"ok": True, "days": n, "id_usuario": id_usuario, "total": int(total or 0), "items": [dict(r) for r in rows]}


@router.get("/admin/marks/debug")
def admin_marks_debug(
    days: int = Query(default=1, ge=1, le=31),
    id_usuario: int | None = Query(default=None, ge=1),
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Telemetría: lista de intentos recientes de marcación (para soporte).
    """
    _ensure(db)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Solo Admin/SuperAdmin.")
    n = int(days or 1)
    if n < 1:
        n = 1
    if n > 31:
        n = 31
    lim = int(limit or 200)
    if lim < 1:
        lim = 1
    if lim > 1000:
        lim = 1000
    params: dict[str, Any] = {"n": n, "lim": lim}
    where = ["d.created_at >= (now() - (:n || ' days')::interval)"]
    if id_usuario is not None:
        try:
            params["u"] = int(id_usuario)
            where.append("d.id_usuario = :u")
        except Exception:
            pass
    wsql = " AND ".join(where)
    rows = db.execute(
        text(
            f"""
            SELECT
              d.created_at, d.id_usuario,
              COALESCE(u.username,'') AS username,
              COALESCE(NULLIF(btrim(u.nombre),''), u.username) AS display,
              d.device_id, d.punto_code, d.method, d.tipo,
              d.ok, d.within_radius, d.used_fallback, d.distance_m, d.accuracy_m,
              d.err, d.ip
            FROM public.sgjo_marks_debug d
            LEFT JOIN public.usuarios u ON u.id_usuario=d.id_usuario
            WHERE {wsql}
            ORDER BY d.created_at DESC, d.id_debug DESC
            LIMIT :lim
            """
        ),
        params,
    ).mappings().all()
    return {"ok": True, "days": n, "id_usuario": id_usuario, "items": [dict(r) for r in rows]}


@router.get("/admin/devices")
def admin_devices(
    id_usuario: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    _ensure(db)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Solo Admin/SuperAdmin.")
    try:
        uid = int(id_usuario)
    except Exception:
        raise HTTPException(status_code=400, detail="id_usuario inválido")
    rows = db.execute(
        text(
            """
            SELECT d.id_device, d.id_usuario, d.device_id, COALESCE(d.device_name,'') AS device_name,
                   COALESCE(d.device_kind,'') AS device_kind,
                   d.enrolled_at, d.revoked_at,
                   COALESCE(u.username,'') AS username,
                   COALESCE(NULLIF(btrim(u.nombre),''), u.username) AS display
            FROM public.sgjo_dispositivos d
            LEFT JOIN public.usuarios u ON u.id_usuario = d.id_usuario
            WHERE d.id_usuario=:u
            ORDER BY (d.revoked_at IS NULL) DESC, d.enrolled_at DESC, d.id_device DESC
            LIMIT 200
            """
        ),
        {"u": uid},
    ).mappings().all()
    return {"ok": True, "items": [dict(r) for r in rows]}


@router.get("/admin/hours_summary")
def admin_hours_summary(
    id_usuario: int = Query(ge=1),
    from_date: str | None = Query(default=None),
    to_date: str | None = Query(default=None),
    db: Session = Depends(get_db),
    me: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Resumen simple de horas marcadas por día (IN/OUT OK).
    - Toma primera IN y última OUT del día (Chile).
    - Devuelve minutos y horas decimales.
    """
    _ensure(db)
    if not _is_admin(me):
        raise HTTPException(status_code=403, detail="Sin permiso")
    uid = int(id_usuario)
    fd = str(from_date or "").strip()[:10] or None
    td = str(to_date or "").strip()[:10] or None

    where = ["m.id_usuario=:u", "m.ok IS TRUE"]
    params: dict[str, Any] = {"u": uid}
    if fd:
        where.append("(m.created_at AT TIME ZONE 'America/Santiago')::date >= :fd::date")
        params["fd"] = fd
    if td:
        where.append("(m.created_at AT TIME ZONE 'America/Santiago')::date <= :td::date")
        params["td"] = td

    rows = db.execute(
        text(
            f"""
            WITH d AS (
              SELECT (created_at AT TIME ZONE 'America/Santiago')::date AS day,
                     MIN(created_at) FILTER (WHERE tipo='IN') AS in_at,
                     MAX(created_at) FILTER (WHERE tipo='OUT') AS out_at
              FROM public.sgjo_marcaciones m
              WHERE {' AND '.join(where)}
              GROUP BY 1
            )
            SELECT day,
                   in_at,
                   out_at,
                   CASE
                     WHEN in_at IS NOT NULL AND out_at IS NOT NULL THEN
                       GREATEST(0, EXTRACT(EPOCH FROM (out_at - in_at))::int / 60)
                     ELSE NULL
                   END AS minutes
            FROM d
            ORDER BY day DESC
            LIMIT 370
            """
        ),
        params,
    ).mappings().all()
    total_min = 0
    items = []
    for r in rows:
        m = r.get("minutes")
        if m is not None:
            try:
                total_min += int(m)
            except Exception:
                pass
        items.append(dict(r))
    return {"ok": True, "id_usuario": uid, "total_minutes": int(total_min), "total_hours": round(total_min / 60.0, 2), "items": items}


@router.post("/admin/devices/{id_device}/revoke")
def admin_device_revoke(
    id_device: int,
    payload: dict = Body(default_factory=dict),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    _ensure(db)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Solo Admin/SuperAdmin.")
    me_id = user.get("id")
    me_id_int = int(me_id) if str(me_id or "").isdigit() else None
    note = str(payload.get("note") or "").strip()[:200] or None
    upd = db.execute(
        text(
            """
            UPDATE public.sgjo_dispositivos
            SET revoked_at=now(), revoked_by=:by, revoked_note=:n
            WHERE id_device=:id AND revoked_at IS NULL
            """
        ),
        {"id": int(id_device), "by": me_id_int, "n": note},
    )
    db.commit()
    return {"ok": True, "revoked": int(getattr(upd, "rowcount", 0) or 0)}


@router.post("/admin/devices/prune")
def admin_devices_prune(
    id_usuario: int = Query(ge=1),
    keep_phone: int = Query(default=2, ge=0, le=10),
    keep_pc: int = Query(default=2, ge=0, le=10),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    _ensure(db)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Solo Admin/SuperAdmin.")
    me_id = user.get("id")
    me_id_int = int(me_id) if str(me_id or "").isdigit() else None
    try:
        out = _prune_user_devices_by_kind(
            db,
            id_usuario=int(id_usuario),
            keep_phone=int(keep_phone),
            keep_pc=int(keep_pc),
            revoked_by=me_id_int,
            note="auto-prune: keep latest devices by kind",
        )
        db.commit()
        return {"ok": True, "id_usuario": int(id_usuario), **out}
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"No pude podar dispositivos: {e}")


@router.post("/admin/devices/restore")
def admin_devices_restore(
    id_usuario: int = Query(ge=1),
    keep_phone: int = Query(default=2, ge=0, le=10),
    keep_pc: int = Query(default=2, ge=0, le=10),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Restaura (revoked_at=NULL) los últimos N dispositivos por tipo (PHONE/PC) para un usuario.
    Útil si se podaron de más y el colaborador quedó sin dispositivos activos.
    """
    _ensure(db)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Solo Admin/SuperAdmin.")
    uid = int(id_usuario)
    kp = max(0, min(int(keep_phone or 0), 10))
    kpc = max(0, min(int(keep_pc or 0), 10))

    rows = db.execute(
        text(
            """
            SELECT id_device, COALESCE(device_kind,'PC') AS device_kind
            FROM public.sgjo_dispositivos
            WHERE id_usuario=:u
            ORDER BY enrolled_at DESC, id_device DESC
            """
        ),
        {"u": uid},
    ).mappings().all()

    phone_ids: list[int] = []
    pc_ids: list[int] = []
    for r in rows:
        try:
            did = int(r.get("id_device") or 0)
        except Exception:
            continue
        kind = str(r.get("device_kind") or "PC").strip().upper()
        if kind == "PHONE":
            if len(phone_ids) < kp:
                phone_ids.append(did)
        else:
            if len(pc_ids) < kpc:
                pc_ids.append(did)

    to_restore = list({*phone_ids, *pc_ids})
    restored = 0
    if to_restore:
        upd = db.execute(
            text(
                """
                UPDATE public.sgjo_dispositivos
                SET revoked_at=NULL, revoked_by=NULL, revoked_note=NULL
                WHERE id_usuario=:u
                  AND id_device = ANY(:ids)
                """
            ),
            {"u": uid, "ids": to_restore},
        )
        restored = int(getattr(upd, "rowcount", 0) or 0)
    db.commit()

    # Guardrail final: dejar limpio por tipo.
    try:
        me_id = user.get("id")
        me_id_int = int(me_id) if str(me_id or "").isdigit() else None
        _prune_user_devices_by_kind(
            db,
            id_usuario=uid,
            keep_phone=kp,
            keep_pc=kpc,
            revoked_by=me_id_int,
            note="restore->prune keep latest by kind",
        )
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass

    return {"ok": True, "id_usuario": uid, "restored": restored, "kept_phone": kp, "kept_pc": kpc}


@router.get("/admin/devices/overlimit")
def admin_devices_overlimit(
    phone_limit: int = Query(default=2, ge=0, le=10),
    pc_limit: int = Query(default=2, ge=0, le=10),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    _ensure(db)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Solo Admin/SuperAdmin.")
    plim = int(phone_limit)
    clim = int(pc_limit)
    rows = db.execute(
        text(
            """
            SELECT d.id_usuario,
                   SUM(CASE WHEN COALESCE(d.device_kind,'PC')='PHONE' THEN 1 ELSE 0 END)::int AS phone_devices,
                   SUM(CASE WHEN COALESCE(d.device_kind,'PC')='PC' THEN 1 ELSE 0 END)::int AS pc_devices,
                   COUNT(*)::int AS active_devices,
                   COALESCE(u.username,'') AS username,
                   COALESCE(NULLIF(btrim(u.nombre),''), u.username) AS display
            FROM public.sgjo_dispositivos d
            LEFT JOIN public.usuarios u ON u.id_usuario = d.id_usuario
            WHERE d.revoked_at IS NULL
            GROUP BY d.id_usuario, u.username, u.nombre
            HAVING SUM(CASE WHEN COALESCE(d.device_kind,'PC')='PHONE' THEN 1 ELSE 0 END) > :plim
                OR SUM(CASE WHEN COALESCE(d.device_kind,'PC')='PC' THEN 1 ELSE 0 END) > :clim
            ORDER BY COUNT(*) DESC, d.id_usuario ASC
            LIMIT 500
            """
        ),
        {"plim": plim, "clim": clim},
    ).mappings().all()
    return {"ok": True, "phone_limit": plim, "pc_limit": clim, "items": [dict(r) for r in rows]}


@router.post("/admin/device_requests/{id_request}/approve")
def admin_device_request_approve(
    id_request: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    _ensure(db)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Solo Admin/SuperAdmin.")
    me_id = user.get("id")
    me_id_int = int(me_id) if str(me_id or "").isdigit() else None
    req = db.execute(
        text(
            """
            SELECT id_request, id_usuario, device_id, device_name, device_kind, ua_hash
            FROM public.sgjo_device_requests
            WHERE id_request=:id AND status='pending'
            LIMIT 1
            """
        ),
        {"id": int(id_request)},
    ).mappings().first()
    if not req:
        # Si ya fue aprobada/rechazada, devolvemos estado sin fallar (evita confusión en UI/QR).
        prev = db.execute(
            text(
                """
                SELECT id_request, status, decided_at
                FROM public.sgjo_device_requests
                WHERE id_request=:id
                LIMIT 1
                """
            ),
            {"id": int(id_request)},
        ).mappings().first()
        if prev:
            return {"ok": True, "already_decided": True, "status": prev.get("status"), "decided_at": str(prev.get("decided_at") or "")}
        raise HTTPException(status_code=404, detail="Solicitud no existe.")

    # Regla DB: existe constraint UNIQUE (id_usuario, device_id, status).
    # La tabla tiene UNIQUE(id_usuario, device_id, status). Si ya existe un row "approved"
    # para este mismo (usuario, device), lo movemos a un estado histórico único por request
    # (p.ej. "superseded_by_43") antes de aprobar el pending actual.
    #
    # Esto hace el approve idempotente y evita 500 por UniqueViolation.
    db.execute(
        text(
            """
            UPDATE public.sgjo_device_requests
            SET status=('superseded_by_' || CAST(:id AS text)),
                decided_at=COALESCE(decided_at, now()),
                decided_by=COALESCE(decided_by, :by),
                note=CASE
                      WHEN COALESCE(note,'')='' THEN ('AUTO_SUPERSEDED_BY=' || CAST(:id AS text))
                      ELSE (note || E'\n' || 'AUTO_SUPERSEDED_BY=' || CAST(:id AS text))
                    END
            WHERE id_usuario=:u
              AND device_id=:d
              AND status='approved'
              AND id_request<>:id
            """
        ),
        {
            "u": int(req["id_usuario"]),
            "d": str(req["device_id"]),
            "id": int(id_request),
            "by": me_id_int,
        },
    )

    db.execute(
        text(
            """
            INSERT INTO public.sgjo_dispositivos(id_usuario, device_id, device_name, device_kind, ua_hash)
            VALUES (:u,:d,:dn,:dk,:h)
            ON CONFLICT (id_usuario, device_id) DO UPDATE
            SET device_name=COALESCE(NULLIF(EXCLUDED.device_name,''), sgjo_dispositivos.device_name),
                device_kind=COALESCE(NULLIF(EXCLUDED.device_kind,''), sgjo_dispositivos.device_kind),
                ua_hash=EXCLUDED.ua_hash, revoked_at=NULL
            """
        ),
        {
            "u": int(req["id_usuario"]),
            "d": str(req["device_id"]),
            "dn": str(req.get("device_name") or ""),
            "dk": str(req.get("device_kind") or "")[:10],
            "h": str(req["ua_hash"]),
        },
    )
    db.execute(
        text(
            """
            UPDATE public.sgjo_device_requests
            SET status='approved', decided_at=now(), decided_by=:by
            WHERE id_request=:id
            """
        ),
        {"id": int(id_request), "by": me_id_int},
    )
    # Guardrail: deja solo 2 teléfonos + 2 PCs por usuario.
    try:
        _prune_user_devices_by_kind(
            db,
            id_usuario=int(req["id_usuario"]),
            keep_phone=2,
            keep_pc=2,
            revoked_by=me_id_int,
            note="auto-prune on approve",
        )
    except Exception:
        pass
    db.commit()
    return {"ok": True}


@router.post("/admin/device_requests/{id_request}/reject")
def admin_device_request_reject(
    id_request: int,
    payload: dict = Body(default_factory=dict),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    _ensure(db)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Solo Admin/SuperAdmin.")
    me_id = user.get("id")
    me_id_int = int(me_id) if str(me_id or "").isdigit() else None
    note = str(payload.get("note") or "").strip()[:300] or None
    upd = db.execute(
        text(
            """
            UPDATE public.sgjo_device_requests
            SET status='rejected', decided_at=now(), decided_by=:by, note=:n
            WHERE id_request=:id AND status='pending'
            """
        ),
        {"id": int(id_request), "by": me_id_int, "n": note},
    )
    db.commit()
    return {"ok": True, "updated": int(getattr(upd, "rowcount", 0) or 0)}


def _device_enrolled(db: Session, uid: int, device_id: str, user_agent: str) -> bool:
    try:
        # Nota: NO validamos por UA hash porque iOS puede cambiar el User-Agent
        # entre Safari/PWA/escáner QR, y eso rompe la marcación aun cuando el
        # dispositivo ya fue aprobado. El control real aquí es (id_usuario, device_id)
        # + revocación. Si cambia el UA, actualizamos el hash best-effort.
        h = ua_hash(user_agent or "")
        v = db.execute(
            text(
                """
                SELECT 1
                FROM public.sgjo_dispositivos
                WHERE id_usuario=:u AND device_id=:d AND revoked_at IS NULL
                LIMIT 1
                """
            ),
            {"u": int(uid), "d": device_id},
        ).scalar()
        if v and h:
            try:
                db.execute(
                    text(
                        """
                        UPDATE public.sgjo_dispositivos
                        SET ua_hash=:h
                        WHERE id_usuario=:u AND device_id=:d AND revoked_at IS NULL
                        """
                    ),
                    {"u": int(uid), "d": device_id, "h": h},
                )
                db.commit()
            except Exception:
                db.rollback()
        return bool(v)
    except Exception:
        return False


def _prune_user_devices(
    db: Session,
    *,
    id_usuario: int,
    keep: int = 2,
    revoked_by: int | None = None,
    note: str | None = None,
) -> dict[str, int]:
    """
    Deja solo los N dispositivos más recientes (aprobados) por usuario y revoca el resto.
    Motivo: iOS/Safari/iframes pueden perder storage y generar múltiples device_id distintos.
    """
    try:
        keep_n = max(1, min(10, int(keep)))
    except Exception:
        keep_n = 2

    ids = (
        db.execute(
            text(
                """
                SELECT id_device
                FROM public.sgjo_dispositivos
                WHERE id_usuario=:u AND revoked_at IS NULL
                ORDER BY enrolled_at DESC, id_device DESC
                """
            ),
            {"u": int(id_usuario)},
        )
        .scalars()
        .all()
    )
    ids = [int(x) for x in ids if str(x).isdigit()]
    if len(ids) <= keep_n:
        return {"kept": len(ids), "revoked": 0}

    to_revoke = ids[keep_n:]
    upd = db.execute(
        text(
            """
            UPDATE public.sgjo_dispositivos
            SET revoked_at=now(),
                revoked_by=COALESCE(:by, revoked_by),
                revoked_note=COALESCE(NULLIF(:n,''), revoked_note)
            WHERE id_usuario=:u
              AND revoked_at IS NULL
              AND id_device = ANY(:ids)
            """
        ),
        {"u": int(id_usuario), "ids": to_revoke, "by": revoked_by, "n": (note or "")[:200]},
    )
    return {"kept": keep_n, "revoked": int(getattr(upd, "rowcount", 0) or 0)}


def _prune_user_devices_by_kind(
    db: Session,
    *,
    id_usuario: int,
    keep_phone: int = 2,
    keep_pc: int = 2,
    revoked_by: int | None = None,
    note: str | None = None,
) -> dict[str, int]:
    """
    Deja solo N dispositivos activos por tipo (PHONE/PC) y revoca el resto.
    Los registros sin `device_kind` se consideran PC (por backfill en _ensure).
    """
    try:
        kp = max(0, min(10, int(keep_phone)))
    except Exception:
        kp = 2
    try:
        kpc = max(0, min(10, int(keep_pc)))
    except Exception:
        kpc = 2
    # Guardrail duro: nunca dejar al usuario con 0 dispositivos activos por accidente.
    # Si ambos límites llegan como 0, forzamos mantener al menos 1 PC (si existe).
    if kp == 0 and kpc == 0:
        kpc = 1

    def _revoke_over(kind: str, keep_n: int) -> int:
        if keep_n < 0:
            keep_n = 0
        ids = (
            db.execute(
                text(
                    """
                    SELECT id_device
                    FROM public.sgjo_dispositivos
                    WHERE id_usuario=:u
                      AND revoked_at IS NULL
                      AND COALESCE(device_kind,'PC') = :k
                    ORDER BY enrolled_at DESC, id_device DESC
                    """
                ),
                {"u": int(id_usuario), "k": kind},
            )
            .scalars()
            .all()
        )
        ids = [int(x) for x in ids if str(x).isdigit()]
        # Guardrail: si keep_n==0 pero hay dispositivos activos, mantener al menos 1 para no cortar marcación.
        if keep_n == 0 and len(ids) > 0:
            keep_n = 1
        if len(ids) <= keep_n:
            return 0
        to_revoke = ids[keep_n:]
        upd = db.execute(
            text(
                """
                UPDATE public.sgjo_dispositivos
                SET revoked_at=now(),
                    revoked_by=COALESCE(:by, revoked_by),
                    revoked_note=COALESCE(NULLIF(:n,''), revoked_note)
                WHERE id_usuario=:u
                  AND revoked_at IS NULL
                  AND id_device = ANY(:ids)
                """
            ),
            {"u": int(id_usuario), "ids": to_revoke, "by": revoked_by, "n": (note or "")[:200]},
        )
        return int(getattr(upd, "rowcount", 0) or 0)

    revoked = 0
    revoked += _revoke_over("PHONE", kp)
    revoked += _revoke_over("PC", kpc)
    return {"kept_phone": kp, "kept_pc": kpc, "revoked": revoked}


@router.post("/marcar")
def marcar(
    request: Request,
    payload: dict = Body(default_factory=dict),
    user_agent: str | None = Header(default=None),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    _ensure(db)
    dbg_device_id = str(payload.get("device_id") or "").strip()
    dbg_punto_code = str(payload.get("punto_code") or "").strip().upper()
    dbg_method = str(payload.get("method") or payload.get("metodo") or "").strip().upper()
    dbg_tipo = str(payload.get("tipo") or payload.get("kind") or "").strip().upper()
    dbg_ip = None
    try:
        if request is not None and getattr(request, "client", None):
            dbg_ip = str(request.client.host or "")[:80]
    except Exception:
        dbg_ip = None
    dbg_payload_json = None
    try:
        dbg_payload_json = json.dumps(payload or {}, ensure_ascii=False)[:4000]
    except Exception:
        dbg_payload_json = None

    uid = user.get("id")
    if not str(uid or "").isdigit():
        _log_sgjo_mark_debug(
            db=db,
            id_usuario=None,
            device_id=dbg_device_id,
            punto_code=dbg_punto_code,
            method=dbg_method,
            tipo=dbg_tipo,
            ok=False,
            within=None,
            used_fallback=None,
            distance_m=None,
            accuracy_m=payload.get("accuracy_m"),
            lat=payload.get("lat"),
            lng=payload.get("lng"),
            err="Usuario inválido",
            ua=user_agent,
            ip=dbg_ip,
            payload_json=dbg_payload_json,
        )
        raise HTTPException(status_code=401, detail="Usuario inválido")
    uid_int = int(uid)
    rut = get_user_rut(db, uid_int) or None
    if not rut:
        _log_sgjo_mark_debug(
            db=db,
            id_usuario=uid_int,
            device_id=dbg_device_id,
            punto_code=dbg_punto_code,
            method=dbg_method,
            tipo=dbg_tipo,
            ok=False,
            within=None,
            used_fallback=None,
            distance_m=None,
            accuracy_m=payload.get("accuracy_m"),
            lat=payload.get("lat"),
            lng=payload.get("lng"),
            err="Sin RUT RRHH",
            ua=user_agent,
            ip=dbg_ip,
            payload_json=dbg_payload_json,
        )
        raise HTTPException(status_code=400, detail="Tu usuario no tiene RUT configurado (RRHH).")
    role = str(user.get("role") or user.get("rol") or "")
    mod = modality_for_user(db, uid=uid_int, rut=rut or "", role=role, when=_now())
    modality = str(mod.get("modality") or "PRESENCIAL")

    device_id = str(payload.get("device_id") or "").strip()
    # UX: si el navegador perdió storage/cookies, igual permitimos marcar usando cualquier
    # dispositivo ya aprobado del usuario (último enrolado activo).
    if (not device_id) or (device_id.upper() == "AUTO"):
        device_id = (
            db.execute(
                text(
                    """
                    SELECT device_id
                    FROM public.sgjo_dispositivos
                    WHERE id_usuario=:u
                      AND revoked_at IS NULL
                    ORDER BY enrolled_at DESC, id_device DESC
                    LIMIT 1
                    """
                ),
                {"u": uid_int},
            ).scalar()
            or ""
        )
        device_id = str(device_id or "").strip()
        if not device_id:
            _log_sgjo_mark_debug(
                db=db,
                id_usuario=uid_int,
                device_id="",
                punto_code=dbg_punto_code,
                method=dbg_method,
                tipo=dbg_tipo,
                ok=False,
                within=None,
                used_fallback=None,
                distance_m=None,
                accuracy_m=payload.get("accuracy_m"),
                lat=payload.get("lat"),
                lng=payload.get("lng"),
                err="Sin dispositivo enrolado",
                ua=user_agent,
                ip=dbg_ip,
                payload_json=dbg_payload_json,
            )
            raise HTTPException(status_code=403, detail="No tienes un dispositivo enrolado. Enrola tu dispositivo 1 vez.")
    if not _device_enrolled(db, uid_int, device_id, user_agent or ""):
        _log_sgjo_mark_debug(
            db=db,
            id_usuario=uid_int,
            device_id=device_id,
            punto_code=dbg_punto_code,
            method=dbg_method,
            tipo=dbg_tipo,
            ok=False,
            within=None,
            used_fallback=None,
            distance_m=None,
            accuracy_m=payload.get("accuracy_m"),
            lat=payload.get("lat"),
            lng=payload.get("lng"),
            err="Dispositivo no enrolado",
            ua=user_agent,
            ip=dbg_ip,
            payload_json=dbg_payload_json,
        )
        raise HTTPException(status_code=403, detail="Dispositivo no enrolado")

    punto_code = str(payload.get("punto_code") or "").strip().upper()
    if not punto_code:
        _log_sgjo_mark_debug(
            db=db,
            id_usuario=uid_int,
            device_id=device_id,
            punto_code="",
            method=dbg_method,
            tipo=dbg_tipo,
            ok=False,
            within=None,
            used_fallback=None,
            distance_m=None,
            accuracy_m=payload.get("accuracy_m"),
            lat=payload.get("lat"),
            lng=payload.get("lng"),
            err="punto_code requerido",
            ua=user_agent,
            ip=dbg_ip,
            payload_json=dbg_payload_json,
        )
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
        _log_sgjo_mark_debug(
            db=db,
            id_usuario=uid_int,
            device_id=device_id,
            punto_code=punto_code,
            method=dbg_method,
            tipo=dbg_tipo,
            ok=False,
            within=None,
            used_fallback=None,
            distance_m=None,
            accuracy_m=payload.get("accuracy_m"),
            lat=payload.get("lat"),
            lng=payload.get("lng"),
            err="Punto no existe",
            ua=user_agent,
            ip=dbg_ip,
            payload_json=dbg_payload_json,
        )
        raise HTTPException(status_code=404, detail="Punto no existe")

    lat = payload.get("lat")
    lng = payload.get("lng")
    acc = payload.get("accuracy_m")
    method = str(payload.get("method") or "QR").strip().upper()
    if method == "GPS":
        method = "GEO"
    if method not in ("QR", "GEO", "BOTH"):
        method = "QR"

    # Enrolamiento por RRHH: puede_marcar + método permitido por colaborador.
    policy = _rrhh_marking_policy(db, rut or "")
    staff_method_norm = "BOTH"
    if policy is not None:
        if not bool(policy.get("puede_marcar")):
            raise HTTPException(status_code=403, detail="No habilitado para marcar.")
        staff_method_norm = str(policy.get("marcacion_method") or "BOTH").strip().upper()
        if staff_method_norm == "MIXTO":
            staff_method_norm = "BOTH"
        if staff_method_norm == "QR" and method != "QR":
            raise HTTPException(status_code=403, detail="Tu método permitido es solo QR.")
        if staff_method_norm in ("GPS", "GEO") and method == "QR":
            raise HTTPException(status_code=403, detail="Tu método permitido es solo GPS.")
        if staff_method_norm in ("GPS", "GEO") and not policy.get("telefono"):
            raise HTTPException(status_code=403, detail="Falta teléfono en RRHH para marcar con GPS.")

    distance_m = None
    within = None
    used_fb = False
    ok = True
    err = None

    # UX/operación: iOS/Safari a veces bloquea geolocalización. Para evitar "no puedo marcar"
    # en modo QR, permitimos marcar SIN GPS cuando:
    # - método elegido es QR, y
    # - el colaborador está autorizado para QR (staff_method != GEO-only), y
    # - NO es modalidad REMOTO (en remoto siempre within=True igual).
    allow_qr_without_gps = False
    try:
        staff_method = str((policy or {}).get("marcacion_method") or "BOTH").strip().upper()
        if staff_method == "MIXTO":
            staff_method = "BOTH"
        allow_qr_without_gps = (method == "QR") and (staff_method in ("QR", "BOTH"))
    except Exception:
        allow_qr_without_gps = (method == "QR")

    if lat is None or lng is None:
        if allow_qr_without_gps:
            ok = True
            within = True
            used_fb = True
            distance_m = None
            err = None
        else:
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
                # `accuracy` es "metros de error": mientras más bajo, mejor.
                acc_good = (acc_f is None) or (acc_f <= float(acc_thr))

                if modality == "REMOTO":
                    within = True
                else:
                    within = bool(within_hard or (within_soft and acc_good))
                    used_fb = bool((not within_hard) and within_soft and acc_good)

                # Regla operacional: si el usuario marca vía QR (QR físico), no bloqueamos por GPS fuera de rango.
                # En iOS/Android la ubicación puede ser imprecisa y disparar "Fuera de rango" aunque esté en sede.
                # En QR la evidencia es el código/punto, así que aceptamos y dejamos trazabilidad en distance_m.
                try:
                    if method == "QR" and staff_method_norm in ("QR", "BOTH"):
                        ok = True
                        err = None
                        within = True
                        used_fb = True
                except Exception:
                    pass

                if not within:
                    # Regla práctica: si el colaborador tiene BOTH y está marcando por GEO,
                    # permitimos registrar igual aunque esté fuera de rango (teletrabajo / excepciones),
                    # dejando trazabilidad para RRHH (within_radius=false, used_fallback=true).
                    if (method == "GEO") and (staff_method_norm in ("BOTH", "GEO", "GPS")):
                        ok = True
                        err = None
                        within = False
                        used_fb = True
                    else:
                        ok = False
                        err = "Fuera de rango"
        except Exception:
            ok = False
            err = "No pude calcular distancia"

    # tipo IN/OUT:
    # - Si viene explícito en payload => lo respetamos (valida duplicados básicos).
    # - Si no viene => alterna según última marcación OK (últimas 24h).
    tipo_in = str(payload.get("tipo") or payload.get("kind") or "").strip().upper()
    if tipo_in in ("ENTRADA", "IN"):
        tipo_in = "IN"
    elif tipo_in in ("SALIDA", "OUT"):
        tipo_in = "OUT"
    else:
        tipo_in = ""

    # Estado del día (Chile) para evitar dobles IN/OUT por error.
    tipos_hoy = []
    try:
        rows_hoy = db.execute(
            text(
                """
                SELECT tipo
                FROM public.sgjo_marcaciones
                WHERE id_usuario=:u
                  AND ok IS TRUE
                  AND ((created_at AT TIME ZONE 'America/Santiago')::date = (now() AT TIME ZONE 'America/Santiago')::date)
                ORDER BY created_at ASC
                """
            ),
            {"u": uid_int},
        ).mappings().all()
        tipos_hoy = [str(r.get("tipo") or "").upper() for r in rows_hoy]
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        tipos_hoy = []

    has_in_today = "IN" in tipos_hoy
    has_out_today = "OUT" in tipos_hoy

    if tipo_in:
        # Reglas estrictas: máximo 1 IN y 1 OUT por día (evita “marcó 5 veces”).
        if tipo_in == "IN" and has_in_today:
            raise HTTPException(status_code=400, detail="Ya marcaste ENTRADA hoy.")
        if tipo_in == "OUT" and has_out_today:
            raise HTTPException(status_code=400, detail="Ya marcaste SALIDA hoy.")
        if tipo_in == "OUT" and (not has_in_today) and (not has_out_today):
            raise HTTPException(status_code=400, detail="Primero debes marcar ENTRADA.")
        tipo = tipo_in
    else:
        # Si ya tiene IN y OUT OK hoy, no autogenerar más (evita ruido).
        if has_in_today and has_out_today:
            raise HTTPException(status_code=400, detail="Ya tienes ENTRADA y SALIDA hoy.")
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

    meta_obj: dict[str, Any] = {}
    try:
        if method == "GEO" and staff_method_norm in ("BOTH", "GEO", "GPS") and within is False and ok is True:
            meta_obj["geo_out_of_range_allowed"] = True
            meta_obj["modality"] = modality
    except Exception:
        pass

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
            "meta": json.dumps(meta_obj or {}),
        },
    )
    db.commit()

    # Telemetría (append-only): registrar intento con resultado final.
    _log_sgjo_mark_debug(
        db=db,
        id_usuario=uid_int,
        device_id=device_id,
        punto_code=punto_code,
        method=method,
        tipo=tipo,
        ok=bool(ok),
        within=(bool(within) if within is not None else None),
        used_fallback=bool(used_fb),
        distance_m=(float(distance_m) if distance_m is not None else None),
        accuracy_m=(float(acc) if acc is not None else None),
        lat=lat,
        lng=lng,
        err=err,
        ua=user_agent,
        ip=dbg_ip,
        payload_json=dbg_payload_json,
    )

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
        "presencial_dow": mod.get("presencial_dow"),
    }


@router.get("/device/status")
def device_status(
    device_id: str,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Estado del dispositivo actual para UX:
    - enrolled: existe en sgjo_dispositivos (aprobado)
    - pending: existe solicitud pendiente en sgjo_device_requests
    """
    _ensure(db)
    uid = user.get("id")
    if not str(uid or "").isdigit():
        raise HTTPException(status_code=401, detail="Usuario inválido")
    uid_int = int(uid)
    did = str(device_id or "").strip()
    if not did:
        raise HTTPException(status_code=400, detail="device_id requerido")
    enrolled = bool(
        db.execute(
            text(
                """
                SELECT 1 FROM public.sgjo_dispositivos
                WHERE id_usuario=:u AND device_id=:d AND revoked_at IS NULL
                LIMIT 1
                """
            ),
            {"u": uid_int, "d": did},
        ).scalar()
    )
    pending = bool(
        db.execute(
            text(
                """
                SELECT 1 FROM public.sgjo_device_requests
                WHERE id_usuario=:u AND device_id=:d AND status='pending'
                LIMIT 1
                """
            ),
            {"u": uid_int, "d": did},
        ).scalar()
    )
    return {"ok": True, "device_id": did, "enrolled": enrolled, "pending": pending}
