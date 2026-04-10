from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
import json
import datetime

from backend.db import get_db
from backend.routers.auth import get_current_user

router = APIRouter(prefix="/rrhh", tags=["rrhh"])

NOMINA_FIELDS = [
    "colaborador",
    "centro_costo",
    "situacion_contractual",
    "sueldo_fijo",
    "fecha_inicio",
    "hh_liquido",
    "hh_diario",
    "dias_trabajados",
    "a_pagar_liquido",
    "fijo_bruto",
    "comisiones_brutas",
    "pct_imposicion",
    "comision_neta",
    "comision_no_imponible",
    "adelantos",
    "total_pagar",
    "fecha_vencimiento",
    "estado",
    "fecha_regularizacion",
    "antiguedad",
    "dias_generados",
    "dias_tomados",
    "sobrante",
    "observaciones",
]


def _ensure_tables(db: Session) -> None:
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS rrhh_nomina (
              id_nomina SERIAL PRIMARY KEY,
              colaborador TEXT,
              centro_costo TEXT,
              situacion_contractual TEXT,
              sueldo_fijo NUMERIC,
              fecha_inicio DATE,
              hh_liquido NUMERIC,
              hh_diario NUMERIC,
              dias_trabajados NUMERIC,
              a_pagar_liquido NUMERIC,
              fijo_bruto NUMERIC,
              comisiones_brutas NUMERIC,
              pct_imposicion NUMERIC,
              comision_neta NUMERIC,
              comision_no_imponible NUMERIC,
              adelantos NUMERIC,
              total_pagar NUMERIC,
              fecha_vencimiento DATE,
              estado TEXT,
              fecha_regularizacion DATE,
              antiguedad TEXT,
              dias_generados NUMERIC,
              dias_tomados NUMERIC,
              sobrante NUMERIC,
              observaciones TEXT,
              raw JSONB
            )
            """
        )
    )
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS rrhh_adelantos (
              id_adelanto SERIAL PRIMARY KEY,
              colaborador TEXT NOT NULL,
              monto NUMERIC NOT NULL,
              fecha DATE DEFAULT CURRENT_DATE,
              motivo TEXT
            )
            """
        )
    )
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS rrhh_staff (
              id_staff SERIAL PRIMARY KEY,
              colaborador TEXT NOT NULL,
              rut TEXT,
              email TEXT,
              telefono TEXT,
              id_usuario INTEGER,
              rol TEXT,
              centro_costo TEXT,
              fecha_ingreso DATE,
              afp TEXT,
              afp_pct NUMERIC,
              salud_tipo TEXT,
              salud_pct NUMERIC,
              puede_marcar BOOLEAN DEFAULT TRUE,
              marcacion_method TEXT DEFAULT 'BOTH',
              is_active BOOLEAN DEFAULT TRUE,
              observaciones TEXT,
              ficha JSONB
            )
            """
        )
    )
    # Ensure new columns exist on older installs
    db.execute(text("ALTER TABLE rrhh_staff ADD COLUMN IF NOT EXISTS observaciones TEXT"))
    db.execute(text("ALTER TABLE rrhh_staff ADD COLUMN IF NOT EXISTS ficha JSONB"))
    db.execute(text("ALTER TABLE rrhh_staff ADD COLUMN IF NOT EXISTS id_usuario INTEGER"))
    db.execute(text("ALTER TABLE rrhh_staff ADD COLUMN IF NOT EXISTS presencial_dow SMALLINT"))
    db.execute(text("ALTER TABLE rrhh_staff ADD COLUMN IF NOT EXISTS modalidad_default TEXT"))
    db.execute(text("ALTER TABLE rrhh_staff ADD COLUMN IF NOT EXISTS puede_marcar BOOLEAN DEFAULT TRUE"))
    db.execute(text("ALTER TABLE rrhh_staff ADD COLUMN IF NOT EXISTS marcacion_method TEXT DEFAULT 'BOTH'"))
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS rrhh_inasistencias (
              id_inasistencia SERIAL PRIMARY KEY,
              colaborador TEXT NOT NULL,
              fecha DATE NOT NULL,
              dias NUMERIC DEFAULT 1,
              motivo TEXT
            )
            """
        )
    )
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS rrhh_vacaciones (
              id_vacacion SERIAL PRIMARY KEY,
              colaborador TEXT NOT NULL,
              fecha_inicio DATE NOT NULL,
              fecha_fin DATE NOT NULL,
              dias NUMERIC,
              observaciones TEXT
            )
            """
        )
    )
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS rrhh_solicitudes (
              id_solicitud SERIAL PRIMARY KEY,
              colaborador TEXT NOT NULL,
              tipo TEXT NOT NULL, -- adelanto / vacaciones / permiso
              fecha_inicio DATE,
              fecha_fin DATE,
              dias NUMERIC,
              monto NUMERIC,
              motivo TEXT,
              doc_tipo TEXT,
              id_usuario INTEGER,
              rut TEXT,
              estado TEXT DEFAULT 'pendiente',
              created_at TIMESTAMP DEFAULT now()
            )
            """
        )
    )
    # Extend (idempotente)
    db.execute(text("ALTER TABLE rrhh_solicitudes ADD COLUMN IF NOT EXISTS doc_tipo TEXT"))
    db.execute(text("ALTER TABLE rrhh_solicitudes ADD COLUMN IF NOT EXISTS id_usuario INTEGER"))
    db.execute(text("ALTER TABLE rrhh_solicitudes ADD COLUMN IF NOT EXISTS rut TEXT"))

    # Turnos (plantillas) + horarios teóricos
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS rrhh_turnos (
              id_turno SERIAL PRIMARY KEY,
              nombre TEXT UNIQUE NOT NULL,
              hora_entrada TEXT NOT NULL, -- HH:MM (24h)
              hora_salida TEXT NOT NULL,  -- HH:MM (24h)
              tolerancia_min INTEGER DEFAULT 0,
              colacion_auto BOOLEAN DEFAULT TRUE,
              colacion_ini TEXT DEFAULT '13:30',
              colacion_fin TEXT DEFAULT '14:30',
              is_active BOOLEAN DEFAULT TRUE,
              created_at TIMESTAMPTZ DEFAULT now(),
              updated_at TIMESTAMPTZ DEFAULT now()
            )
            """
        )
    )
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS rrhh_horarios (
              id_horario SERIAL PRIMARY KEY,
              id_staff INTEGER NOT NULL REFERENCES rrhh_staff(id_staff) ON DELETE CASCADE,
              id_turno INTEGER NOT NULL REFERENCES rrhh_turnos(id_turno) ON DELETE RESTRICT,
              desde DATE NOT NULL,
              hasta DATE,
              dow_mask INTEGER, -- bitmask 0=Mon..6=Sun. NULL=todos los días
              is_active BOOLEAN DEFAULT TRUE,
              created_at TIMESTAMPTZ DEFAULT now()
            )
            """
        )
    )
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS rrhh_afp (
              id_afp SERIAL PRIMARY KEY,
              nombre TEXT UNIQUE NOT NULL,
              pct_comision NUMERIC,
              is_active BOOLEAN DEFAULT TRUE,
              updated_at TIMESTAMP DEFAULT now()
            )
            """
        )
    )
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS rrhh_salud (
              id_salud SERIAL PRIMARY KEY,
              tipo TEXT NOT NULL, -- FONASA / ISAPRE
              nombre TEXT UNIQUE NOT NULL,
              pct_base NUMERIC,
              is_active BOOLEAN DEFAULT TRUE,
              updated_at TIMESTAMP DEFAULT now()
            )
            """
        )
    )
    db.commit()

    # Seed AFP commissions if empty
    try:
        cnt = db.execute(text("SELECT COUNT(*) FROM rrhh_afp")).scalar_one()
        if int(cnt or 0) == 0:
            data = [
                ("Capital", 1.44),
                ("Cuprum", 1.44),
                ("Habitat", 1.27),
                ("Modelo", 0.58),
                ("PlanVital", 1.16),
                ("Provida", 1.45),
                ("Uno", 0.46),
            ]
            for n, p in data:
                db.execute(
                    text("INSERT INTO rrhh_afp(nombre, pct_comision) VALUES (:n,:p)"),
                    {"n": n, "p": p},
                )
            db.commit()
    except Exception:
        db.rollback()

    # Seed salud (FONASA base 7%, ISAPRE base 7% editable)
    try:
        cnt = db.execute(text("SELECT COUNT(*) FROM rrhh_salud")).scalar_one()
        if int(cnt or 0) == 0:
            data = [
                ("FONASA", "FONASA", 7.0),
                ("ISAPRE", "ISAPRE (base)", 7.0),
            ]
            for t, n, p in data:
                db.execute(
                    text("INSERT INTO rrhh_salud(tipo, nombre, pct_base) VALUES (:t,:n,:p)"),
                    {"t": t, "n": n, "p": p},
                )
            db.commit()
    except Exception:
        db.rollback()

def _role_key(user: dict) -> str:
    raw = str(user.get("role") or user.get("rol") or "").strip().upper()
    return raw

def _is_rrhh_admin(user: dict) -> bool:
    r = _role_key(user)
    return ("SUPERADMIN" in r) or (r == "ADMIN") or (r == "SUPER ADMIN") or (r == "SUPER_ADMIN")

def _require_rrhh_admin(user: dict) -> None:
    if not _is_rrhh_admin(user):
        raise HTTPException(status_code=403, detail="Solo Admin/SuperAdmin.")


class LinkUserIn(BaseModel):
    id_usuario: int

def _user_id(user: dict) -> int | None:
    uid = user.get("id") or user.get("id_usuario") or user.get("user_id")
    try:
        return int(uid) if str(uid).isdigit() else None
    except Exception:
        return None

def _user_rut(db: Session, user: dict) -> str:
    uid = _user_id(user)
    if not uid:
        return ""
    try:
        cols = db.execute(text("""
          SELECT column_name FROM information_schema.columns
          WHERE table_schema='public' AND table_name='usuarios'
        """)).fetchall()
        colset = {c[0] for c in cols}
        if "rut" not in colset:
            return ""
        v = db.execute(text("SELECT COALESCE(rut,'') FROM public.usuarios WHERE id_usuario=:id LIMIT 1"), {"id": uid}).scalar()
        return str(v or "").strip()
    except Exception:
        return ""

def _staff_for_user(db: Session, user: dict) -> dict[str, Any] | None:
    rut = _user_rut(db, user)
    email = str(user.get("email") or "").strip()
    username = str(user.get("username") or "").strip()
    try:
        if rut:
            row = db.execute(
                text(
                    """
                    SELECT *
                    FROM rrhh_staff
                    WHERE is_active IS TRUE AND lower(rut)=lower(:r)
                    ORDER BY id_staff DESC
                    LIMIT 1
                    """
                ),
                {"r": rut},
            ).mappings().first()
            if row:
                return dict(row)
        if email:
            row = db.execute(
                text(
                    """
                    SELECT *
                    FROM rrhh_staff
                    WHERE is_active IS TRUE AND lower(email)=lower(:e)
                    ORDER BY id_staff DESC
                    LIMIT 1
                    """
                ),
                {"e": email},
            ).mappings().first()
            if row:
                return dict(row)
        # fallback suave por nombre usuario (último recurso)
        if username:
            row = db.execute(
                text(
                    """
                    SELECT *
                    FROM rrhh_staff
                    WHERE is_active IS TRUE AND lower(colaborador)=lower(:c)
                    ORDER BY id_staff DESC
                    LIMIT 1
                    """
                ),
                {"c": username},
            ).mappings().first()
            if row:
                return dict(row)
    except Exception:
        return None
    return None

def _dow_mask_allows(mask: int | None, dow: int) -> bool:
    if mask is None:
        return True
    try:
        m = int(mask)
    except Exception:
        return True
    if dow < 0 or dow > 6:
        return True
    return bool(m & (1 << dow))

def _today_theoretical_shift(db: Session, staff_id: int, on_date: datetime.date) -> dict[str, Any] | None:
    try:
        rows = db.execute(
            text(
                """
                SELECT h.id_horario, h.desde, h.hasta, h.dow_mask,
                       t.id_turno, t.nombre, t.hora_entrada, t.hora_salida, t.tolerancia_min,
                       t.colacion_auto, t.colacion_ini, t.colacion_fin
                FROM rrhh_horarios h
                JOIN rrhh_turnos t ON t.id_turno=h.id_turno
                WHERE h.is_active IS TRUE
                  AND t.is_active IS TRUE
                  AND h.id_staff=:s
                  AND h.desde <= :d
                  AND (h.hasta IS NULL OR h.hasta >= :d)
                ORDER BY h.desde DESC, h.id_horario DESC
                """
            ),
            {"s": int(staff_id), "d": on_date},
        ).mappings().all()
        dow = int(on_date.weekday())
        for r in rows:
            if _dow_mask_allows(r.get("dow_mask"), dow):
                return dict(r)
        return None
    except Exception:
        return None


@router.get("/nomina")
def nomina_list(db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    try:
        # Base: colaboradores activos (rrhh_staff)
        staff_rows = db.execute(
            text(
                """
                SELECT id_staff, colaborador, centro_costo, rol, afp, afp_pct, salud_tipo, salud_pct,
                       fecha_ingreso, observaciones, ficha
                FROM rrhh_staff
                WHERE is_active IS TRUE
                ORDER BY COALESCE(centro_costo,''), COALESCE(rol,''), colaborador ASC NULLS LAST, id_staff ASC
                """
            )
        ).mappings().all()

        # adelantos por colaborador
        adel_rows = db.execute(
            text(
                """
                SELECT lower(colaborador) AS c, COALESCE(SUM(monto),0) AS total
                FROM rrhh_adelantos
                GROUP BY lower(colaborador)
                """
            )
        ).fetchall()
        adel_map = {str(r[0]): float(r[1] or 0) for r in adel_rows}

        # inasistencias del mes actual
        faltas_map = {}
        try:
            faltas_rows = db.execute(
                text(
                    """
                    SELECT lower(colaborador) AS c, COALESCE(SUM(dias),0) AS dias
                    FROM rrhh_inasistencias
                    WHERE fecha >= date_trunc('month', current_date)
                      AND fecha < (date_trunc('month', current_date) + interval '1 month')
                    GROUP BY lower(colaborador)
                    """
                )
            ).fetchall()
            faltas_map = {str(r[0]): float(r[1] or 0) for r in faltas_rows}
        except Exception:
            faltas_map = {}

        # vacaciones acumuladas/tomadas
        vac_map = {}
        try:
            vac_rows = db.execute(
                text(
                    """
                    SELECT lower(colaborador) AS c, COALESCE(SUM(dias),0) AS dias
                    FROM rrhh_vacaciones
                    GROUP BY lower(colaborador)
                    """
                )
            ).fetchall()
            vac_map = {str(r[0]): float(r[1] or 0) for r in vac_rows}
        except Exception:
            vac_map = {}

        items = []
        def _num(v):
            if v is None:
                return 0.0
            if isinstance(v, (int, float)):
                return float(v)
            try:
                return float(str(v).replace(".", "").replace(",", "."))
            except Exception:
                return 0.0

        for r in staff_rows:
            d = dict(r)
            ficha = d.get("ficha") or {}
            if isinstance(ficha, str):
                try:
                    ficha = json.loads(ficha)
                except Exception:
                    ficha = {}
            key = str((d.get("colaborador") or "")).lower()
            faltas = float(faltas_map.get(key, 0) or 0)
            sueldo = _num(
                ficha.get("hh_liquido")
                or ficha.get("renta_liquida")
                or ficha.get("sueldo_fijo")
                or 0
            )
            adel = float(adel_map.get(key, 0) or 0)
            vac_tomadas = float(vac_map.get(key, 0) or 0)
            fecha_ingreso = d.get("fecha_ingreso")
            vac_acumuladas = 0.0
            if fecha_ingreso:
                try:
                    if isinstance(fecha_ingreso, str):
                        fecha_ingreso = datetime.date.fromisoformat(fecha_ingreso)
                    days = (datetime.date.today() - fecha_ingreso).days
                    vac_acumuladas = max(0.0, (days / 30.0) * 1.25)
                except Exception:
                    vac_acumuladas = 0.0
            vac_disponibles = max(0.0, vac_acumuladas - vac_tomadas)
            dias = max(0, 30 - faltas)
            diario = (sueldo / 30.0) if sueldo else 0.0
            descuento_faltas = diario * faltas
            total_pagar = max(0.0, sueldo - descuento_faltas - adel)
            items.append({
                "colaborador": d.get("colaborador"),
                "centro_costo": d.get("centro_costo"),
                "cargo": ficha.get("cargo") or d.get("rol"),
                "jefe_directo": ficha.get("jefe_directo") or ficha.get("jefe") or None,
                "situacion_contractual": ficha.get("situacion_contractual") or None,
                "estado": ficha.get("estado_laboral") or ("VIGENTE" if d.get("is_active") else "INACTIVO"),
                "sueldo_fijo": sueldo,
                "hh_liquido": sueldo,
                "hh_diario": round((sueldo / 30.0), 0) if sueldo else 0,
                "afp": d.get("afp"),
                "afp_pct": d.get("afp_pct"),
                "salud_tipo": d.get("salud_tipo"),
                "salud_pct": d.get("salud_pct"),
                "faltas_mes": faltas,
                "dias_trabajados": dias,
                "adelantos_total": adel,
                "vac_acumuladas": round(vac_acumuladas, 2),
                "vac_tomadas": round(vac_tomadas, 2),
                "vac_disponibles": round(vac_disponibles, 2),
                "total_pagar": round(total_pagar, 0),
                "saldo": round(total_pagar, 0),
            })
        return {"ok": True, "items": items}
    except Exception as e:
        db.rollback()
        return {"ok": False, "detail": str(e)}


# -----------------------------
# RRHH Portal (colaborador)
# -----------------------------

@router.get("/portal/me")
def rrhh_portal_me(db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    staff = _staff_for_user(db, me)
    on = datetime.date.today()
    shift = None
    if staff and staff.get("id_staff"):
        shift = _today_theoretical_shift(db, int(staff["id_staff"]), on)
    return {
        "ok": True,
        "user": {
            "id_usuario": _user_id(me),
            "email": me.get("email"),
            "username": me.get("username"),
            "role": me.get("role") or me.get("rol"),
            "rut": _user_rut(db, me) or None,
        },
        "staff": staff,
        "today": on.isoformat(),
        "turno_hoy": shift,
    }


@router.get("/marcaciones/me")
def marcaciones_me(
    limit: int = 60,
    db: Session = Depends(get_db),
    me: dict = Depends(get_current_user),
) -> dict[str, Any]:
    _ensure_tables(db)
    uid = _user_id(me)
    if not uid:
        raise HTTPException(status_code=401, detail="Usuario inválido")
    try:
        rows = db.execute(
            text(
                """
                SELECT id_marcacion, created_at, tipo, method, id_sede, id_punto, distance_m, within_radius, used_fallback, ok, error
                FROM public.sgjo_marcaciones
                WHERE id_usuario=:u
                ORDER BY created_at DESC
                LIMIT :lim
                """
            ),
            {"u": int(uid), "lim": max(1, min(500, int(limit)))},
        ).mappings().all()
        return {"ok": True, "items": [dict(r) for r in rows]}
    except Exception as e:
        db.rollback()
        return {"ok": False, "detail": str(e)}


@router.get("/solicitudes/me")
def solicitudes_me(db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    uid = _user_id(me)
    rut = _user_rut(db, me)
    staff = _staff_for_user(db, me)
    colab = (staff or {}).get("colaborador") or me.get("name") or me.get("nombre") or me.get("username") or ""
    try:
        rows = db.execute(
            text(
                """
                SELECT id_solicitud, colaborador, tipo, doc_tipo, fecha_inicio, fecha_fin, dias, monto, motivo, estado, created_at
                FROM rrhh_solicitudes
                WHERE (id_usuario=:u)
                   OR (:r <> '' AND lower(rut)=lower(:r))
                   OR (lower(colaborador)=lower(:c))
                ORDER BY created_at DESC, id_solicitud DESC
                LIMIT 200
                """
            ),
            {"u": int(uid or 0), "r": str(rut or ""), "c": str(colab or "")},
        ).mappings().all()
        return {"ok": True, "items": [dict(r) for r in rows]}
    except Exception as e:
        db.rollback()
        return {"ok": False, "detail": str(e)}


@router.post("/solicitudes/me")
def solicitudes_me_create(body: dict, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    uid = _user_id(me)
    if not uid:
        raise HTTPException(status_code=401, detail="Usuario inválido")
    staff = _staff_for_user(db, me) or {}
    colaborador = (staff.get("colaborador") or me.get("name") or me.get("nombre") or me.get("username") or "").strip()
    tipo = (body.get("tipo") or "").strip().lower()
    if tipo not in ("adelanto", "vacaciones", "permiso", "documento", "regularizacion"):
        raise HTTPException(status_code=400, detail="tipo inválido")
    doc_tipo = (body.get("doc_tipo") or "").strip() if tipo == "documento" else None
    if tipo == "documento" and not doc_tipo:
        raise HTTPException(status_code=400, detail="doc_tipo requerido")
    data = {
        "colaborador": colaborador or "Colaborador",
        "tipo": tipo,
        "doc_tipo": doc_tipo,
        "fecha_inicio": body.get("fecha_inicio"),
        "fecha_fin": body.get("fecha_fin"),
        "dias": body.get("dias"),
        "monto": body.get("monto"),
        "motivo": body.get("motivo"),
        "id_usuario": int(uid),
        "rut": _user_rut(db, me) or None,
        "estado": "pendiente",
    }
    try:
        db.execute(
            text(
                """
                INSERT INTO rrhh_solicitudes(
                  colaborador,tipo,doc_tipo,fecha_inicio,fecha_fin,dias,monto,motivo,id_usuario,rut,estado,created_at
                ) VALUES (
                  :colaborador,:tipo,:doc_tipo,:fecha_inicio,:fecha_fin,:dias,:monto,:motivo,:id_usuario,:rut,:estado,now()
                )
                """
            ),
            data,
        )
        db.commit()
        # Aviso por correo a RRHH (best-effort).
        try:
            from backend.core.email import send_email_group

            rrhh_to = (os.getenv("RRHH_NOTIFY_TO") or "c.grez@clavetributariacontadores.cl").strip()
            rrhh_cc = [x.strip() for x in str(os.getenv("RRHH_NOTIFY_CC") or "").split(",") if x.strip()]
            adelanto_to = (os.getenv("RRHH_ADELANTO_TO") or "simonurrutia@greendiamond.cl,oscar@greendiamond.cl").strip()
            adelanto_list = [x.strip() for x in adelanto_to.split(",") if x.strip()]

            to_list = []
            if tipo == "adelanto":
                to_list = adelanto_list or ([rrhh_to] if rrhh_to else [])
            else:
                to_list = ([rrhh_to] if rrhh_to else []) + rrhh_cc

            if to_list:
                subj = f"RRHH · Solicitud {tipo} · {colaborador}"
                txt = (
                    f"Colaborador: {colaborador}\n"
                    f"Tipo: {tipo}\n"
                    + (f"Documento: {doc_tipo}\n" if doc_tipo else "")
                    + (f"Fecha inicio: {data.get('fecha_inicio')}\n" if data.get("fecha_inicio") else "")
                    + (f"Fecha fin: {data.get('fecha_fin')}\n" if data.get("fecha_fin") else "")
                    + (f"Días: {data.get('dias')}\n" if data.get("dias") else "")
                    + (f"Monto: {data.get('monto')}\n" if data.get("monto") else "")
                    + (f"Motivo: {data.get('motivo')}\n" if data.get("motivo") else "")
                    + f"\nEstado: pendiente\n"
                    + f"Usuario CRM ID: {uid}\n"
                    + f"RUT: {data.get('rut') or ''}\n"
                )
                send_email_group(to_list, subj, txt)
        except Exception:
            pass
        return {"ok": True}
    except Exception as e:
        db.rollback()
        return {"ok": False, "detail": str(e)}


# -----------------------------
# Turnos (admin)
# -----------------------------

@router.get("/turnos")
def turnos_list(db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    rows = db.execute(
        text("SELECT * FROM rrhh_turnos WHERE is_active IS TRUE ORDER BY nombre ASC, id_turno ASC")
    ).mappings().all()
    return {"ok": True, "items": [dict(r) for r in rows]}


@router.post("/turnos")
def turnos_create(body: dict, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    nombre = (body.get("nombre") or "").strip()
    hora_entrada = (body.get("hora_entrada") or "").strip()
    hora_salida = (body.get("hora_salida") or "").strip()
    if not nombre or not hora_entrada or not hora_salida:
        raise HTTPException(status_code=400, detail="nombre/hora_entrada/hora_salida requeridos")
    db.execute(
        text(
            """
            INSERT INTO rrhh_turnos(nombre,hora_entrada,hora_salida,tolerancia_min,colacion_auto,colacion_ini,colacion_fin,is_active,updated_at)
            VALUES (:n,:he,:hs,:tol,:ca,:ci,:cf,TRUE,now())
            ON CONFLICT (nombre) DO UPDATE
              SET hora_entrada=EXCLUDED.hora_entrada,
                  hora_salida=EXCLUDED.hora_salida,
                  tolerancia_min=EXCLUDED.tolerancia_min,
                  colacion_auto=EXCLUDED.colacion_auto,
                  colacion_ini=EXCLUDED.colacion_ini,
                  colacion_fin=EXCLUDED.colacion_fin,
                  is_active=TRUE,
                  updated_at=now()
            """
        ),
        {
            "n": nombre,
            "he": hora_entrada,
            "hs": hora_salida,
            "tol": int(body.get("tolerancia_min") or 0),
            "ca": bool(body.get("colacion_auto", True)),
            "ci": (body.get("colacion_ini") or "13:30"),
            "cf": (body.get("colacion_fin") or "14:30"),
        },
    )
    db.commit()
    return {"ok": True}


@router.post("/horarios")
def horarios_assign(body: dict, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    try:
        id_staff = int(body.get("id_staff"))
        id_turno = int(body.get("id_turno"))
    except Exception:
        raise HTTPException(status_code=400, detail="id_staff/id_turno requeridos")
    desde = body.get("desde")
    if not desde:
        raise HTTPException(status_code=400, detail="desde requerido")
    hasta = body.get("hasta")
    dow_mask = body.get("dow_mask")
    try:
        dow_mask = int(dow_mask) if dow_mask is not None and str(dow_mask).strip() != "" else None
    except Exception:
        dow_mask = None
    db.execute(
        text(
            """
            INSERT INTO rrhh_horarios(id_staff,id_turno,desde,hasta,dow_mask,is_active)
            VALUES (:s,:t,:d,:h,:m,TRUE)
            """
        ),
        {"s": id_staff, "t": id_turno, "d": desde, "h": hasta, "m": dow_mask},
    )
    db.commit()
    return {"ok": True}


@router.get("/horarios")
def horarios_list(id_staff: int | None = None, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    if id_staff:
        rows = db.execute(
            text(
                """
                SELECT h.*, t.nombre AS turno_nombre, t.hora_entrada, t.hora_salida
                FROM rrhh_horarios h
                JOIN rrhh_turnos t ON t.id_turno=h.id_turno
                WHERE h.is_active IS TRUE AND h.id_staff=:s
                ORDER BY h.desde DESC, h.id_horario DESC
                """
            ),
            {"s": int(id_staff)},
        ).mappings().all()
    else:
        rows = db.execute(
            text(
                """
                SELECT h.*, t.nombre AS turno_nombre, t.hora_entrada, t.hora_salida
                FROM rrhh_horarios h
                JOIN rrhh_turnos t ON t.id_turno=h.id_turno
                WHERE h.is_active IS TRUE
                ORDER BY h.desde DESC, h.id_horario DESC
                LIMIT 500
                """
            )
        ).mappings().all()
    return {"ok": True, "items": [dict(r) for r in rows]}


@router.get("/marcaciones")
def marcaciones_list(
    from_date: str | None = None,
    to_date: str | None = None,
    id_usuario: int | None = None,
    rut: str | None = None,
    tipo: str | None = None,
    ok: int | None = None,
    limit: int = 300,
    db: Session = Depends(get_db),
    me: dict = Depends(get_current_user),
) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    where = []
    params: dict[str, Any] = {"lim": max(1, min(2000, int(limit)))}
    if from_date:
        where.append("m.created_at::date >= :fd")
        params["fd"] = from_date
    if to_date:
        where.append("m.created_at::date <= :td")
        params["td"] = to_date
    if id_usuario is not None:
        try:
            params["uid"] = int(id_usuario)
            where.append("m.id_usuario = :uid")
        except Exception:
            pass
    if rut:
        params["rut"] = str(rut).strip()
        if params["rut"]:
            where.append("COALESCE(m.rut,'') = :rut")
    if tipo:
        t = str(tipo).strip().upper()
        if t:
            params["tipo"] = t
            where.append("upper(COALESCE(m.tipo,'')) = :tipo")
    if ok is not None:
        try:
            params["ok"] = bool(int(ok))
            where.append("COALESCE(m.ok,FALSE) = :ok")
        except Exception:
            pass
    w = ("WHERE " + " AND ".join(where)) if where else ""
    rows = db.execute(
        text(
            f"""
            SELECT m.id_marcacion, m.created_at, m.id_usuario, m.rut, m.tipo, m.method, m.ok, m.error, m.distance_m, m.within_radius, m.used_fallback
            FROM public.sgjo_marcaciones m
            {w}
            ORDER BY m.created_at DESC
            LIMIT :lim
            """
        ),
        params,
    ).mappings().all()
    return {"ok": True, "items": [dict(r) for r in rows]}


@router.get("/nomina/fields")
def nomina_fields() -> dict[str, Any]:
    return {"ok": True, "fields": NOMINA_FIELDS}


@router.post("/nomina")
def nomina_create(body: dict, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    data = {k: body.get(k) for k in NOMINA_FIELDS if k in body}
    if not data.get("colaborador"):
        return {"ok": False, "detail": "colaborador requerido"}
    cols = ", ".join(data.keys())
    vals = ", ".join([f":{k}" for k in data.keys()])
    q = text(f"INSERT INTO rrhh_nomina({cols}) VALUES ({vals}) RETURNING id_nomina")
    rid = db.execute(q, data).scalar_one()
    db.commit()
    return {"ok": True, "id_nomina": rid}


@router.put("/nomina/{id_nomina}")
def nomina_update(id_nomina: int, body: dict, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    data = {k: body.get(k) for k in NOMINA_FIELDS if k in body}
    if not data:
        return {"ok": False, "detail": "sin cambios"}
    sets = ", ".join([f"{k}=:{k}" for k in data.keys()])
    data["id_nomina"] = id_nomina
    db.execute(text(f"UPDATE rrhh_nomina SET {sets} WHERE id_nomina=:id_nomina"), data)
    db.commit()
    return {"ok": True}


@router.delete("/nomina/{id_nomina}")
def nomina_delete(id_nomina: int, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    db.execute(text("DELETE FROM rrhh_nomina WHERE id_nomina=:id"), {"id": id_nomina})
    db.commit()
    return {"ok": True}


@router.post("/nomina/clear")
def nomina_clear(db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    db.execute(text("DELETE FROM rrhh_nomina"))
    db.commit()
    return {"ok": True}


@router.get("/adelantos")
def adelantos_list(colaborador: str | None = None, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    try:
        params: dict[str, Any] = {}
        where = ""
        if colaborador:
            where = "WHERE colaborador ILIKE :c"
            params["c"] = f"%{colaborador}%"
        rows = db.execute(
            text(
                f"""
                SELECT id_adelanto, colaborador, monto, fecha, motivo
                FROM rrhh_adelantos
                {where}
                ORDER BY fecha DESC, id_adelanto DESC
                """
            ),
            params,
        ).mappings().all()
        return {"ok": True, "items": [dict(r) for r in rows]}
    except Exception as e:
        db.rollback()
        return {"ok": False, "detail": str(e)}


@router.post("/adelantos")
def adelanto_create(body: dict, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    colaborador = (body.get("colaborador") or "").strip()
    monto = body.get("monto")
    fecha = body.get("fecha")
    motivo = (body.get("motivo") or "").strip()
    if not colaborador:
        return {"ok": False, "detail": "colaborador requerido"}
    if monto is None:
        return {"ok": False, "detail": "monto requerido"}
    db.execute(
        text(
            """
            INSERT INTO rrhh_adelantos(colaborador, monto, fecha, motivo)
            VALUES (:c, :m, COALESCE(:f, CURRENT_DATE), :mot)
            """
        ),
        {"c": colaborador, "m": monto, "f": fecha, "mot": motivo},
    )
    db.commit()
    return {"ok": True}


@router.put("/adelantos/{id_adelanto}")
def adelanto_update(id_adelanto: int, body: dict, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    try:
        db.execute(
            text(
                """
                UPDATE rrhh_adelantos
                SET fecha = COALESCE(:fecha, fecha),
                    monto = COALESCE(:monto, monto),
                    motivo = COALESCE(:motivo, motivo)
                WHERE id_adelanto = :id
                """
            ),
            {
                "id": id_adelanto,
                "fecha": body.get("fecha"),
                "monto": body.get("monto"),
                "motivo": body.get("motivo"),
            },
        )
        db.commit()
        return {"ok": True}
    except Exception as e:
        db.rollback()
        return {"ok": False, "detail": str(e)}


@router.delete("/adelantos/{id_adelanto}")
def adelanto_delete(id_adelanto: int, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    db.execute(text("DELETE FROM rrhh_adelantos WHERE id_adelanto=:id"), {"id": id_adelanto})
    db.commit()
    return {"ok": True}


@router.get("/staff")
def staff_list(db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    try:
        rows = db.execute(
            text(
                """
                SELECT *
                FROM rrhh_staff
                ORDER BY COALESCE(centro_costo,''), COALESCE(rol,''), colaborador ASC NULLS LAST, id_staff ASC
                """
            )
        ).mappings().all()
        items = []
        for r in rows:
            d = dict(r)
            ficha = d.get("ficha")
            if isinstance(ficha, str):
                try:
                    d["ficha"] = json.loads(ficha)
                except Exception:
                    d["ficha"] = {}
            items.append(d)
        return {"ok": True, "items": items}
    except Exception as e:
        db.rollback()
        return {"ok": False, "detail": str(e)}


@router.get("/staff/{id_staff}")
def staff_get(id_staff: int, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    row = db.execute(
        text("SELECT * FROM rrhh_staff WHERE id_staff=:id LIMIT 1"),
        {"id": int(id_staff)},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Colaborador no existe")
    d = dict(row)
    ficha = d.get("ficha")
    if isinstance(ficha, str):
        try:
            d["ficha"] = json.loads(ficha)
        except Exception:
            d["ficha"] = {}
    return {"ok": True, "item": d}


@router.get("/users")
def users_list(
    q: str = "",
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    me: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Lista usuarios del sistema para vincularlos a RRHH.
    Solo Admin/SuperAdmin.
    """
    _ensure_tables(db)
    _require_rrhh_admin(me)
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    q0 = (q or "").strip()
    where = ""
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if q0:
        where = "WHERE (COALESCE(u.nombre,'') ILIKE :q OR COALESCE(u.email,'') ILIKE :q OR COALESCE(u.username,'') ILIKE :q)"
        params["q"] = f"%{q0}%"
    rows = db.execute(
        text(
            f"""
            SELECT
              u.id_usuario,
              COALESCE(NULLIF(btrim(u.nombre),''), NULLIF(btrim(u.username),''), NULLIF(btrim(u.email),''), u.id_usuario::text) AS display,
              COALESCE(u.nombre,'') AS nombre,
              COALESCE(u.email,'') AS email,
              COALESCE(u.username,'') AS username,
              COALESCE(u.rol,'') AS rol,
              COALESCE(u.telefono,'') AS telefono,
              COALESCE(u.is_active, TRUE) AS is_active
            FROM public.usuarios u
            {where}
            ORDER BY lower(COALESCE(u.nombre, u.username, u.email, u.id_usuario::text)) ASC, u.id_usuario ASC
            LIMIT :limit OFFSET :offset
            """
        ),
        params,
    ).mappings().all()
    return {"ok": True, "items": [dict(r) for r in rows]}


@router.post("/staff/{id_staff}/link_user")
def staff_link_user(
    id_staff: int,
    body: LinkUserIn,
    db: Session = Depends(get_db),
    me: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Vincula un usuario CRM existente a un colaborador RRHH y sincroniza datos básicos.
    Solo Admin/SuperAdmin.
    """
    _ensure_tables(db)
    _require_rrhh_admin(me)
    row_staff = db.execute(
        text("SELECT id_staff FROM rrhh_staff WHERE id_staff=:id LIMIT 1"),
        {"id": int(id_staff)},
    ).fetchone()
    if not row_staff:
        raise HTTPException(status_code=404, detail="Colaborador no existe")

    u = db.execute(
        text(
            """
            SELECT
              id_usuario,
              COALESCE(nombre,'') AS nombre,
              COALESCE(email,'') AS email,
              COALESCE(username,'') AS username,
              COALESCE(rol,'') AS rol,
              COALESCE(telefono,'') AS telefono
            FROM public.usuarios
            WHERE id_usuario=:u
            LIMIT 1
            """
        ),
        {"u": int(body.id_usuario)},
    ).mappings().first()
    if not u:
        raise HTTPException(status_code=404, detail="Usuario no existe")

    db.execute(
        text(
            """
            UPDATE rrhh_staff
            SET id_usuario=:u,
                email = COALESCE(NULLIF(:email,''), email),
                telefono = COALESCE(NULLIF(:tel,''), telefono),
                rol = COALESCE(NULLIF(:rol,''), rol)
            WHERE id_staff=:id
            """
        ),
        {
            "id": int(id_staff),
            "u": int(u["id_usuario"]),
            "email": str(u.get("email") or "").strip(),
            "tel": str(u.get("telefono") or "").strip(),
            "rol": str(u.get("rol") or "").strip(),
        },
    )
    db.commit()
    return {"ok": True, "id_staff": int(id_staff), "id_usuario": int(u["id_usuario"])}


@router.delete("/staff/{id_staff}/link_user")
def staff_unlink_user(
    id_staff: int,
    db: Session = Depends(get_db),
    me: dict = Depends(get_current_user),
) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    db.execute(text("UPDATE rrhh_staff SET id_usuario=NULL WHERE id_staff=:id"), {"id": int(id_staff)})
    db.commit()
    return {"ok": True, "id_staff": int(id_staff)}


@router.get("/staff/{id_staff}/ficha")
def staff_ficha(id_staff: int, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> HTMLResponse:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    row = db.execute(
        text(
            """
            SELECT id_staff, colaborador, rut, email, telefono, rol, centro_costo, fecha_ingreso,
                   afp, afp_pct, salud_tipo, salud_pct, observaciones, ficha
            FROM rrhh_staff
            WHERE id_staff = :id
            """
        ),
        {"id": id_staff},
    ).mappings().first()
    if not row:
        return HTMLResponse("<h3>No se encontró colaborador.</h3>", status_code=404)
    d = dict(row)
    ficha = d.get("ficha") or {}
    if isinstance(ficha, str):
        try:
            ficha = json.loads(ficha)
        except Exception:
            ficha = {}

    def v(key, fallback="—"):
        val = ficha.get(key)
        if val is None or val == "":
            return fallback
        return str(val)

    html = f"""
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8"/>
  <title>Ficha ingreso · {d.get("colaborador") or ""}</title>
  <style>
    body{{ font-family: Arial, sans-serif; margin:24px; color:#111; }}
    .head{{ display:flex; justify-content:space-between; align-items:center; }}
    .btn{{ padding:8px 12px; border:1px solid #111; background:#111; color:#fff; border-radius:8px; cursor:pointer; }}
    h1{{ font-size:20px; margin:0 0 12px 0; }}
    h2{{ font-size:14px; margin:16px 0 6px 0; text-transform:uppercase; letter-spacing:.08em; }}
    table{{ width:100%; border-collapse:collapse; font-size:12px; }}
    td{{ padding:6px 8px; border:1px solid #ddd; }}
    .muted{{ color:#666; }}
  </style>
</head>
<body>
  <div class="head">
    <div>
      <h1>Ficha de Ingreso</h1>
      <div class="muted">{d.get("colaborador") or "—"} · {d.get("rut") or "—"}</div>
    </div>
    <button class="btn" onclick="window.print()">Imprimir PDF</button>
  </div>

  <h2>Antecedentes Cargo</h2>
  <table>
    <tr><td>Área</td><td>{v("area")}</td><td>Cargo</td><td>{v("cargo")}</td></tr>
    <tr><td>Renta líquida</td><td>{v("renta_liquida")}</td><td>Entrevistador</td><td>{v("entrevistador")}</td></tr>
    <tr><td>Fecha entrevista</td><td>{v("fecha_entrevista")}</td><td>Fecha ingreso</td><td>{d.get("fecha_ingreso") or "—"}</td></tr>
  </table>

  <h2>Antecedentes Personales</h2>
  <table>
    <tr><td>Nombres</td><td>{v("nombres")}</td><td>Apellidos</td><td>{v("apellido_paterno")} {v("apellido_materno")}</td></tr>
    <tr><td>Fecha nac.</td><td>{v("fecha_nacimiento")}</td><td>Nacionalidad</td><td>{v("nacionalidad")}</td></tr>
    <tr><td>Género</td><td>{v("genero")}</td><td>Estado civil</td><td>{v("estado_civil")}</td></tr>
    <tr><td>Teléfono</td><td>{d.get("telefono") or "—"}</td><td>Email</td><td>{d.get("email") or "—"}</td></tr>
    <tr><td>Dirección</td><td colspan="3">{v("calle")} {v("numero")} {v("depto")} {v("direccion_extra")}, {v("comuna")}, {v("ciudad")}, {v("pais")}</td></tr>
    <tr><td>Emergencia</td><td>{v("emergencia_contacto")}</td><td>Tel. emergencia</td><td>{v("emergencia_telefono")}</td></tr>
  </table>

  <h2>Antecedentes Familiares</h2>
  <table>
    <tr><td>Parentesco</td><td>{v("fam_parentesco")}</td><td>Nombre</td><td>{v("fam_nombre")}</td></tr>
    <tr><td>RUT</td><td>{v("fam_rut")}</td><td>Fecha nac.</td><td>{v("fam_nacimiento")}</td></tr>
    <tr><td>Carga</td><td>{v("fam_carga")}</td><td>Actividad</td><td>{v("fam_actividad")}</td></tr>
  </table>

  <h2>Tallas</h2>
  <table>
    <tr><td>Pantalón</td><td>{v("talla_pantalon")}</td><td>Chaqueta</td><td>{v("talla_chaqueta")}</td></tr>
    <tr><td>Polera</td><td>{v("talla_polera")}</td><td>Calzado</td><td>{v("talla_calzado")}</td></tr>
    <tr><td>Guantes</td><td>{v("talla_guantes")}</td><td></td><td></td></tr>
  </table>

  <h2>Afiliaciones</h2>
  <table>
    <tr><td>AFP</td><td>{d.get("afp") or "—"} ({d.get("afp_pct") or "—"}%)</td><td>Salud</td><td>{d.get("salud_tipo") or "—"} ({d.get("salud_pct") or "—"}%)</td></tr>
  </table>

  <h2>Datos Bancarios</h2>
  <table>
    <tr><td>Banco</td><td>{v("banco")}</td><td>Tipo cuenta</td><td>{v("tipo_cuenta")}</td></tr>
    <tr><td>N° cuenta</td><td>{v("cuenta_numero")}</td><td></td><td></td></tr>
  </table>
</body>
</html>
"""
    return HTMLResponse(html)


@router.get("/afp")
def afp_list(db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    rows = db.execute(
        text("SELECT id_afp, nombre, pct_comision FROM rrhh_afp WHERE is_active IS TRUE ORDER BY nombre")
    ).mappings().all()
    return {"ok": True, "items": [dict(r) for r in rows]}


@router.get("/salud")
def salud_list(db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    rows = db.execute(
        text("SELECT id_salud, tipo, nombre, pct_base FROM rrhh_salud WHERE is_active IS TRUE ORDER BY tipo, nombre")
    ).mappings().all()
    return {"ok": True, "items": [dict(r) for r in rows]}


@router.post("/staff")
def staff_create(body: dict, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    colaborador = (body.get("colaborador") or "").strip()
    if not colaborador:
        return {"ok": False, "detail": "colaborador requerido"}
    ficha = body.get("ficha")
    if isinstance(ficha, (dict, list)):
        ficha = json.dumps(ficha, ensure_ascii=False)
    data = {
        "colaborador": colaborador,
        "rut": body.get("rut"),
        "email": body.get("email"),
        "telefono": body.get("telefono"),
        "rol": body.get("rol"),
        "centro_costo": body.get("centro_costo"),
        "fecha_ingreso": body.get("fecha_ingreso"),
        "afp": body.get("afp"),
        "afp_pct": body.get("afp_pct"),
        "salud_tipo": body.get("salud_tipo"),
        "salud_pct": body.get("salud_pct"),
        "puede_marcar": body.get("puede_marcar", True),
        "marcacion_method": (body.get("marcacion_method") or "BOTH"),
        "is_active": body.get("is_active", True),
        "observaciones": body.get("observaciones"),
        "ficha": ficha,
    }
    try:
        db.execute(
            text(
                """
                INSERT INTO rrhh_staff(
                  colaborador,rut,email,telefono,rol,centro_costo,fecha_ingreso,
                  afp,afp_pct,salud_tipo,salud_pct,puede_marcar,marcacion_method,is_active,observaciones,ficha
                ) VALUES (
                  :colaborador,:rut,:email,:telefono,:rol,:centro_costo,:fecha_ingreso,
                  :afp,:afp_pct,:salud_tipo,:salud_pct,:puede_marcar,:marcacion_method,:is_active,:observaciones,CAST(:ficha AS JSONB)
                )
                RETURNING id_staff
                """
            ),
            data,
        ).scalar_one()
        db.commit()
        return {"ok": True}
    except Exception as e:
        db.rollback()
        return {"ok": False, "detail": str(e)}


@router.post("/staff/bulk_upsert")
def staff_bulk_upsert(body: dict, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    """
    Upsert masivo de colaboradores.
    - Match principal: lower(colaborador) + centro_costo (si viene).
    - Datos extra se guardan en rrhh_staff.ficha (JSONB) para evitar migraciones agresivas.

    Payload:
      { "items": [ { colaborador, centro_costo?, rol?, rut?, email?, telefono?,
                    fecha_ingreso?, is_active?, afp?, afp_pct?, salud_tipo?, salud_pct?,
                    ficha?: {...}, hh_liquido?, situacion_contractual?, jefe_directo?, area?, cargo? } ] }
    """
    _ensure_tables(db)
    _require_rrhh_admin(me)
    items = body.get("items") or []
    if not isinstance(items, list) or not items:
        return {"ok": False, "detail": "items requerido"}

    def _as_num(v):
        if v is None or v == "":
            return None
        if isinstance(v, (int, float)):
            return float(v)
        try:
            s = str(v).strip()
            s = s.replace("$", "").replace(".", "").replace(",", ".")
            return float(s)
        except Exception:
            return None

    upserted = 0
    created = 0
    updated = 0
    errors: list[dict[str, Any]] = []

    for it in items:
        try:
            if not isinstance(it, dict):
                continue
            colaborador = (it.get("colaborador") or "").strip()
            if not colaborador:
                continue
            centro = (it.get("centro_costo") or "").strip() or None

            ficha = it.get("ficha") or {}
            if isinstance(ficha, str):
                try:
                    ficha = json.loads(ficha)
                except Exception:
                    ficha = {}
            if not isinstance(ficha, dict):
                ficha = {}

            # Campos comunes (se guardan en ficha por defecto)
            hh_liq = _as_num(it.get("hh_liquido"))
            if hh_liq is None:
                hh_liq = _as_num(ficha.get("hh_liquido") or ficha.get("renta_liquida") or ficha.get("sueldo_fijo"))
            if hh_liq is not None:
                ficha["hh_liquido"] = hh_liq
                ficha.setdefault("renta_liquida", hh_liq)

            for k in ("situacion_contractual", "jefe_directo", "area", "cargo"):
                if it.get(k) is not None and str(it.get(k)).strip() != "":
                    ficha[k] = it.get(k)

            ficha_json = json.dumps(ficha, ensure_ascii=False)

            # Busca existente
            row = db.execute(
                text(
                    """
                    SELECT id_staff
                    FROM rrhh_staff
                    WHERE lower(colaborador) = lower(:c)
                      AND (:cc IS NULL OR lower(centro_costo) = lower(:cc))
                    ORDER BY id_staff DESC
                    LIMIT 1
                    """
                ),
                {"c": colaborador, "cc": centro},
            ).mappings().first()

            base = {
                "colaborador": colaborador,
                "rut": it.get("rut"),
                "email": it.get("email"),
                "telefono": it.get("telefono"),
                "rol": it.get("rol"),
                "centro_costo": centro,
                "fecha_ingreso": it.get("fecha_ingreso"),
                "afp": it.get("afp"),
                "afp_pct": _as_num(it.get("afp_pct")),
                "salud_tipo": it.get("salud_tipo"),
                "salud_pct": _as_num(it.get("salud_pct")),
                "puede_marcar": bool(it.get("puede_marcar", True)),
                "marcacion_method": (it.get("marcacion_method") or "BOTH"),
                "is_active": bool(it.get("is_active", True)),
                "observaciones": it.get("observaciones"),
                "ficha": ficha_json,
            }

            if row and row.get("id_staff"):
                base["id_staff"] = int(row["id_staff"])
                db.execute(
                    text(
                        """
                        UPDATE rrhh_staff
                        SET colaborador=:colaborador,
                            rut=COALESCE(:rut, rut),
                            email=COALESCE(:email, email),
                            telefono=COALESCE(:telefono, telefono),
                            rol=COALESCE(:rol, rol),
                            centro_costo=COALESCE(:centro_costo, centro_costo),
                            fecha_ingreso=COALESCE(:fecha_ingreso, fecha_ingreso),
                            afp=COALESCE(:afp, afp),
                            afp_pct=COALESCE(:afp_pct, afp_pct),
                            salud_tipo=COALESCE(:salud_tipo, salud_tipo),
                            salud_pct=COALESCE(:salud_pct, salud_pct),
                            puede_marcar=COALESCE(:puede_marcar, puede_marcar),
                            marcacion_method=COALESCE(:marcacion_method, marcacion_method),
                            is_active=COALESCE(:is_active, is_active),
                            observaciones=COALESCE(:observaciones, observaciones),
                            ficha=CAST(:ficha AS JSONB)
                        WHERE id_staff=:id_staff
                        """
                    ),
                    base,
                )
                updated += 1
            else:
                db.execute(
                    text(
                        """
                        INSERT INTO rrhh_staff(
                          colaborador,rut,email,telefono,rol,centro_costo,fecha_ingreso,
                          afp,afp_pct,salud_tipo,salud_pct,puede_marcar,marcacion_method,is_active,observaciones,ficha
                        ) VALUES (
                          :colaborador,:rut,:email,:telefono,:rol,:centro_costo,:fecha_ingreso,
                          :afp,:afp_pct,:salud_tipo,:salud_pct,:puede_marcar,:marcacion_method,:is_active,:observaciones,CAST(:ficha AS JSONB)
                        )
                        """
                    ),
                    base,
                )
                created += 1
            upserted += 1
        except Exception as e:
            errors.append({"colaborador": (it or {}).get("colaborador"), "detail": str(e)})
            db.rollback()

    db.commit()
    return {"ok": True, "upserted": upserted, "created": created, "updated": updated, "errors": errors}


@router.put("/staff/{id_staff}")
def staff_update(id_staff: int, body: dict, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    prev = {}
    try:
        prev = (
            db.execute(
                text(
                    """
                    SELECT email, puede_marcar, marcacion_method
                    FROM rrhh_staff
                    WHERE id_staff=:id
                    LIMIT 1
                    """
                ),
                {"id": int(id_staff)},
            ).mappings().first()
            or {}
        )
    except Exception:
        prev = {}
    ficha = body.get("ficha")
    if isinstance(ficha, (dict, list)):
        ficha = json.dumps(ficha, ensure_ascii=False)
    data = {
        "id_staff": id_staff,
        "colaborador": body.get("colaborador"),
        "rut": body.get("rut"),
        "email": body.get("email"),
        "telefono": body.get("telefono"),
        "rol": body.get("rol"),
        "centro_costo": body.get("centro_costo"),
        "fecha_ingreso": body.get("fecha_ingreso"),
        "afp": body.get("afp"),
        "afp_pct": body.get("afp_pct"),
        "salud_tipo": body.get("salud_tipo"),
        "salud_pct": body.get("salud_pct"),
        "puede_marcar": body.get("puede_marcar", True),
        "marcacion_method": (body.get("marcacion_method") or "BOTH"),
        "is_active": body.get("is_active", True),
        "observaciones": body.get("observaciones"),
        "ficha": ficha,
    }
    try:
        db.execute(
            text(
                """
                UPDATE rrhh_staff
                SET colaborador=:colaborador,
                    rut=:rut,
                    email=:email,
                    telefono=:telefono,
                    rol=:rol,
                    centro_costo=:centro_costo,
                    fecha_ingreso=:fecha_ingreso,
                    afp=:afp,
                    afp_pct=:afp_pct,
                    salud_tipo=:salud_tipo,
                    salud_pct=:salud_pct,
                    puede_marcar=:puede_marcar,
                    marcacion_method=:marcacion_method,
                    is_active=:is_active,
                    observaciones=:observaciones,
                    ficha=CAST(:ficha AS JSONB)
                WHERE id_staff=:id_staff
                """
            ),
            data,
        )
        db.commit()
        # Si se habilitó marcación (o cambió método), avisa al colaborador para enrolar su teléfono.
        try:
            prev_puede = bool(prev.get("puede_marcar", True))
            new_puede = bool(data.get("puede_marcar", True))
            prev_m = str(prev.get("marcacion_method") or "BOTH").strip().upper()
            new_m = str(data.get("marcacion_method") or "BOTH").strip().upper()
            email = str(data.get("email") or prev.get("email") or "").strip()
            if email and new_puede and ((not prev_puede) or (prev_m != new_m)):
                from backend.core.email import send_email

                app_url = (os.getenv("APP_URL") or "https://greendiamond.cl").strip().rstrip("/")
                link = f"{app_url}/crm/web/views/rrhh_sgjo_marcacion.html"
                subj = "RRHH · Enrolamiento de dispositivo (SGJO)"
                txt = (
                    "Se habilitó tu marcación en el CRM.\n\n"
                    f"Método permitido: {new_m}\n"
                    "Paso 1: Ingresa al CRM.\n"
                    "Paso 2: RRHH → Marcar.\n"
                    "Paso 3: Presiona “Enrolar dispositivo”.\n\n"
                    f"Link directo: {link}\n"
                    "\nSi cambiaste de teléfono, repite el enrolamiento desde el nuevo dispositivo.\n"
                )
                send_email(email, subj, txt)
        except Exception:
            pass
        return {"ok": True}
    except Exception as e:
        db.rollback()
        return {"ok": False, "detail": str(e)}


@router.delete("/staff/{id_staff}")
def staff_delete(id_staff: int, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    db.execute(text("DELETE FROM rrhh_staff WHERE id_staff=:id"), {"id": id_staff})
    db.commit()
    return {"ok": True}


@router.post("/inasistencias")
def inasistencia_create(body: dict, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    colaborador = (body.get("colaborador") or "").strip()
    fecha = body.get("fecha")
    dias = body.get("dias") or 1
    motivo = (body.get("motivo") or "").strip()
    if not colaborador or not fecha:
        return {"ok": False, "detail": "colaborador y fecha requeridos"}
    db.execute(
        text(
            """
            INSERT INTO rrhh_inasistencias(colaborador, fecha, dias, motivo)
            VALUES (:c, :f, :d, :m)
            """
        ),
        {"c": colaborador, "f": fecha, "d": dias, "m": motivo},
    )
    db.commit()
    return {"ok": True}


@router.get("/inasistencias")
def inasistencia_list(colaborador: str | None = None, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    try:
        if colaborador:
            rows = db.execute(
                text(
                    """
                    SELECT id_inasistencia, colaborador, fecha, dias, motivo
                    FROM rrhh_inasistencias
                    WHERE lower(colaborador) = lower(:c)
                    ORDER BY fecha DESC, id_inasistencia DESC
                    """
                ),
                {"c": colaborador},
            ).mappings().all()
        else:
            rows = db.execute(
                text(
                    """
                    SELECT id_inasistencia, colaborador, fecha, dias, motivo
                    FROM rrhh_inasistencias
                    ORDER BY fecha DESC, id_inasistencia DESC
                    """
                )
            ).mappings().all()
        return {"ok": True, "items": [dict(r) for r in rows]}
    except Exception as e:
        db.rollback()
        return {"ok": False, "detail": str(e)}


@router.put("/inasistencias/{id_inasistencia}")
def inasistencia_update(id_inasistencia: int, body: dict, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    try:
        db.execute(
            text(
                """
                UPDATE rrhh_inasistencias
                SET fecha = COALESCE(:fecha, fecha),
                    dias = COALESCE(:dias, dias),
                    motivo = COALESCE(:motivo, motivo)
                WHERE id_inasistencia = :id
                """
            ),
            {
                "id": id_inasistencia,
                "fecha": body.get("fecha"),
                "dias": body.get("dias"),
                "motivo": body.get("motivo"),
            },
        )
        db.commit()
        return {"ok": True}
    except Exception as e:
        db.rollback()
        return {"ok": False, "detail": str(e)}


@router.delete("/inasistencias/{id_inasistencia}")
def inasistencia_delete(id_inasistencia: int, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    db.execute(text("DELETE FROM rrhh_inasistencias WHERE id_inasistencia=:id"), {"id": id_inasistencia})
    db.commit()
    return {"ok": True}


@router.get("/solicitudes")
def solicitudes_list(
    estado: str | None = None,
    colaborador: str | None = None,
    db: Session = Depends(get_db),
    me: dict = Depends(get_current_user),
) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    try:
        where = []
        params: dict[str, Any] = {}
        if estado:
            where.append("estado = :estado")
            params["estado"] = estado
        if colaborador:
            where.append("lower(colaborador) = lower(:colaborador)")
            params["colaborador"] = colaborador
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        rows = db.execute(
            text(
                f"""
                SELECT id_solicitud, colaborador, tipo, fecha_inicio, fecha_fin, dias, monto, motivo, estado, created_at
                FROM rrhh_solicitudes
                {where_sql}
                ORDER BY created_at DESC, id_solicitud DESC
                """
            ),
            params,
        ).mappings().all()
        return {"ok": True, "items": [dict(r) for r in rows]}
    except Exception as e:
        db.rollback()
        return {"ok": False, "detail": str(e)}


@router.post("/solicitudes")
def solicitudes_create(body: dict, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    colaborador = (body.get("colaborador") or "").strip()
    tipo = (body.get("tipo") or "").strip()
    if not colaborador or not tipo:
        return {"ok": False, "detail": "colaborador y tipo requeridos"}
    db.execute(
        text(
            """
            INSERT INTO rrhh_solicitudes(
              colaborador, tipo, fecha_inicio, fecha_fin, dias, monto, motivo, estado
            ) VALUES (
              :c, :t, :fi, :ff, :d, :m, :mot, :e
            )
            """
        ),
        {
            "c": colaborador,
            "t": tipo,
            "fi": body.get("fecha_inicio"),
            "ff": body.get("fecha_fin"),
            "d": body.get("dias"),
            "m": body.get("monto"),
            "mot": body.get("motivo"),
            "e": body.get("estado") or "pendiente",
        },
    )
    db.commit()
    return {"ok": True}


@router.put("/solicitudes/{id_solicitud}")
def solicitudes_update(id_solicitud: int, body: dict, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    try:
        db.execute(
            text(
                """
                UPDATE rrhh_solicitudes
                SET estado = COALESCE(:estado, estado),
                    motivo = COALESCE(:motivo, motivo)
                WHERE id_solicitud = :id
                """
            ),
            {"id": id_solicitud, "estado": body.get("estado"), "motivo": body.get("motivo")},
        )
        db.commit()
        return {"ok": True}
    except Exception as e:
        db.rollback()
        return {"ok": False, "detail": str(e)}


@router.get("/solicitudes/{id_solicitud}/pdf")
def solicitud_pdf(id_solicitud: int, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> HTMLResponse:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    row = db.execute(
        text(
            """
            SELECT id_solicitud, colaborador, tipo, fecha_inicio, fecha_fin, dias, monto, motivo, estado, created_at
            FROM rrhh_solicitudes
            WHERE id_solicitud = :id
            """
        ),
        {"id": id_solicitud},
    ).mappings().first()
    if not row:
        return HTMLResponse("<h3>No se encontró solicitud.</h3>", status_code=404)
    d = dict(row)
    html = f"""
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8"/>
  <title>Solicitud RRHH · {d.get("tipo")}</title>
  <style>
    body{{ font-family: Arial, sans-serif; margin:24px; color:#111; }}
    .head{{ display:flex; justify-content:space-between; align-items:center; }}
    .btn{{ padding:8px 12px; border:1px solid #111; background:#111; color:#fff; border-radius:8px; cursor:pointer; }}
    h1{{ font-size:18px; margin:0 0 12px 0; }}
    table{{ width:100%; border-collapse:collapse; font-size:12px; }}
    td{{ padding:6px 8px; border:1px solid #ddd; }}
  </style>
</head>
<body>
  <div class="head">
    <h1>Solicitud {d.get("tipo")}</h1>
    <button class="btn" onclick="window.print()">Imprimir PDF</button>
  </div>
  <table>
    <tr><td>Colaborador</td><td>{d.get("colaborador")}</td></tr>
    <tr><td>Tipo</td><td>{d.get("tipo")}</td></tr>
    <tr><td>Fecha inicio</td><td>{d.get("fecha_inicio") or "—"}</td></tr>
    <tr><td>Fecha fin</td><td>{d.get("fecha_fin") or "—"}</td></tr>
    <tr><td>Días</td><td>{d.get("dias") or "—"}</td></tr>
    <tr><td>Monto</td><td>{d.get("monto") or "—"}</td></tr>
    <tr><td>Motivo</td><td>{d.get("motivo") or "—"}</td></tr>
    <tr><td>Estado</td><td>{d.get("estado")}</td></tr>
    <tr><td>Fecha creación</td><td>{d.get("created_at")}</td></tr>
  </table>
</body>
</html>
"""
    return HTMLResponse(html)


@router.post("/vacaciones")
def vacaciones_create(body: dict, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    colaborador = (body.get("colaborador") or "").strip()
    fi = body.get("fecha_inicio")
    ff = body.get("fecha_fin")
    dias = body.get("dias")
    obs = (body.get("observaciones") or "").strip()
    if not colaborador or not fi or not ff:
        return {"ok": False, "detail": "colaborador y fechas requeridas"}
    db.execute(
        text(
            """
            INSERT INTO rrhh_vacaciones(colaborador, fecha_inicio, fecha_fin, dias, observaciones)
            VALUES (:c, :fi, :ff, :d, :o)
            """
        ),
        {"c": colaborador, "fi": fi, "ff": ff, "d": dias, "o": obs},
    )
    db.commit()
    return {"ok": True}
