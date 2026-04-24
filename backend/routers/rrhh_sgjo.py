from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
import threading
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
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
            db.commit()
        except Exception:
            db.rollback()
        _SGJO_ENSURED = True


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
                    INSERT INTO public.sgjo_device_requests(id_usuario, device_id, ua_hash, status)
                    VALUES (:u,:d,:h,'pending')
                    """
                ),
                {"u": uid_int, "d": device_id, "h": h},
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
            subj = f"RRHH · Solicitud enrolamiento dispositivo · {user.get('name') or user.get('username')}"
            body = (
                f"Usuario ID: {uid_int}\n"
                f"Usuario: {user.get('username')}\n"
                f"Nombre: {user.get('name') or user.get('nombre')}\n"
                f"Device ID: {device_id}\n"
                f"UA hash: {h}\n\n"
                f"Aprueba desde RRHH → Puntos + QR → 'Solicitudes de dispositivos'.\n"
            )
            send_email_group(to_list, subj, body)
    except Exception:
        pass

    return {"ok": False, "pending_approval": True, "detail": "Solicitud enviada. Un admin debe aprobar este dispositivo."}


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
                   u.username, COALESCE(NULLIF(btrim(u.nombre),''), u.username) AS display
            FROM public.sgjo_device_requests r
            LEFT JOIN public.usuarios u ON u.id_usuario = r.id_usuario
            {where}
            ORDER BY r.created_at DESC, r.id_request DESC
            LIMIT 200
            """
        ),
        params,
    ).mappings().all()
    return {"ok": True, "items": [dict(r) for r in rows]}


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
            SELECT id_request, id_usuario, device_id, ua_hash
            FROM public.sgjo_device_requests
            WHERE id_request=:id AND status='pending'
            LIMIT 1
            """
        ),
        {"id": int(id_request)},
    ).mappings().first()
    if not req:
        raise HTTPException(status_code=404, detail="Solicitud no existe.")

    db.execute(
        text(
            """
            INSERT INTO public.sgjo_dispositivos(id_usuario, device_id, ua_hash)
            VALUES (:u,:d,:h)
            ON CONFLICT (id_usuario, device_id) DO UPDATE
            SET ua_hash=EXCLUDED.ua_hash, revoked_at=NULL
            """
        ),
        {"u": int(req["id_usuario"]), "d": str(req["device_id"]), "h": str(req["ua_hash"])},
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
    if not rut:
        raise HTTPException(status_code=400, detail="Tu usuario no tiene RUT configurado (RRHH).")
    role = str(user.get("role") or user.get("rol") or "")
    mod = modality_for_user(db, uid=uid_int, rut=rut or "", role=role, when=_now())
    modality = str(mod.get("modality") or "PRESENCIAL")

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
    if method == "GPS":
        method = "GEO"
    if method not in ("QR", "GEO", "BOTH"):
        method = "QR"

    # Enrolamiento por RRHH: puede_marcar + método permitido por colaborador.
    policy = _rrhh_marking_policy(db, rut or "")
    if policy is not None:
        if not bool(policy.get("puede_marcar")):
            raise HTTPException(status_code=403, detail="No habilitado para marcar.")
        staff_method = str(policy.get("marcacion_method") or "BOTH").strip().upper()
        if staff_method == "MIXTO":
            staff_method = "BOTH"
        if staff_method == "QR" and method != "QR":
            raise HTTPException(status_code=403, detail="Tu método permitido es solo QR.")
        if staff_method in ("GPS", "GEO") and method == "QR":
            raise HTTPException(status_code=403, detail="Tu método permitido es solo GPS.")
        if staff_method in ("GPS", "GEO") and not policy.get("telefono"):
            raise HTTPException(status_code=403, detail="Falta teléfono en RRHH para marcar con GPS.")

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
            # `accuracy` es "metros de error": mientras más bajo, mejor.
            acc_good = (acc_f is None) or (acc_f <= float(acc_thr))

            if modality == "REMOTO":
                within = True
            else:
                within = bool(within_hard or (within_soft and acc_good))
                used_fb = bool((not within_hard) and within_soft and acc_good)
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
        "presencial_dow": mod.get("presencial_dow"),
    }
