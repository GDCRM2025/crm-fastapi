from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
import json
import datetime
import os
import threading
import re
import unicodedata

from backend.db import get_db
from backend.routers.auth import get_current_user

router = APIRouter(prefix="/rrhh", tags=["rrhh"])

_RRHH_ENSURED = False
_RRHH_ENSURE_LOCK = threading.Lock()

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

def _norm_person_key(name: str) -> str:
    """
    Normaliza nombres para matching (RRHH) entre fuentes heterogéneas:
    - trim + colapsa espacios
    - lower
    - elimina tildes/diacríticos
    """
    s = str(name or "").strip()
    if not s:
        return ""
    s = re.sub(r"\s+", " ", s)
    try:
        s = unicodedata.normalize("NFD", s)
        s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    except Exception:
        pass
    return s.lower().strip()

def _norm_person_tokens_key(name: str) -> str:
    s = _norm_person_key(name)
    if not s:
        return ""
    toks = [t for t in re.split(r"\s+", s) if t]
    toks.sort()
    return " ".join(toks)


def _ensure_tables(db: Session) -> None:
    # Evitar DDL repetido en hot paths (puede causar locks/colas con Passenger).
    global _RRHH_ENSURED
    if _RRHH_ENSURED:
        return
    with _RRHH_ENSURE_LOCK:
        if _RRHH_ENSURED:
            return
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
    db.execute(text("ALTER TABLE rrhh_staff ADD COLUMN IF NOT EXISTS jefe_id_staff INTEGER"))
    try:
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_rrhh_staff_jefe ON rrhh_staff(jefe_id_staff)"))
    except Exception:
        pass
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
    # Desvíos vs turno teórico (pendiente aprobación)
    try:
        db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS rrhh_desvios (
                  id_desvio BIGSERIAL PRIMARY KEY,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  fecha DATE NOT NULL,
                  id_staff INTEGER NOT NULL REFERENCES rrhh_staff(id_staff) ON DELETE CASCADE,
                  id_usuario INTEGER,
                  rut TEXT,
                  colaborador TEXT,
                  rol TEXT,
                  centro_costo TEXT,
                  tipo TEXT NOT NULL,              -- MISSING_IN/MISSING_OUT/LATE_IN/EARLY_IN/EARLY_OUT/LATE_OUT
                  status TEXT NOT NULL DEFAULT 'pendiente', -- pendiente/aprobado/rechazado
                  decided_at TIMESTAMPTZ,
                  decided_by INTEGER,
                  note TEXT,
                  expected_in TEXT,
                  expected_out TEXT,
                  actual_in TIMESTAMPTZ,
                  actual_out TIMESTAMPTZ,
                  diff_in_min INTEGER,
                  diff_out_min INTEGER,
                  impact_kind TEXT,                -- AUSENCIA/DESCUENTO_HORAS/HORAS_EXTRA/JUSTIFICADO
                  impact_min INTEGER,
                  meta JSONB NOT NULL DEFAULT '{}'::jsonb,
                  UNIQUE(fecha, id_staff, tipo)
                )
                """
            )
        )
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_rrhh_desvios_fecha ON rrhh_desvios(fecha DESC)"))
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_rrhh_desvios_staff ON rrhh_desvios(id_staff, fecha DESC)"))
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_rrhh_desvios_status ON rrhh_desvios(status, fecha DESC)"))
    except Exception:
        pass
    # Ajustes por desvíos (para nómina/contabilidad)
    try:
        db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS rrhh_ajustes (
                  id_ajuste BIGSERIAL PRIMARY KEY,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  fecha DATE NOT NULL,
                  id_staff INTEGER NOT NULL REFERENCES rrhh_staff(id_staff) ON DELETE CASCADE,
                  id_desvio BIGINT,
                  kind TEXT NOT NULL,              -- AUSENCIA/DESCUENTO_HORAS/HORAS_EXTRA
                  minutos INTEGER NOT NULL DEFAULT 0,
                  note TEXT
                )
                """
            )
        )
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_rrhh_ajustes_staff_fecha ON rrhh_ajustes(id_staff, fecha DESC)"))
    except Exception:
        pass
    db.commit()
    _RRHH_ENSURED = True


def _tol_minutes_for_role(role: str) -> int:
    r = str(role or "").upper()
    # Ejecutivos (remoto): tolerancia baja
    if "EJECUTIV" in r:
        return 10
    # Patio / bodega / compras: tolerancia alta (3 horas)
    if ("PATIO" in r) or ("BODEG" in r) or ("COMPRA" in r) or ("OPERAC" in r) or ("DISE" in r) or ("MARKET" in r):
        return 180
    return 60


def _parse_hhmm(v: str) -> tuple[int, int] | None:
    try:
        s = str(v or "").strip()
        if not s:
            return None
        m = __import__("re").match(r"^\s*(\d{1,2}):(\d{2})\s*$", s)
        if not m:
            return None
        hh = int(m.group(1)); mm = int(m.group(2))
        if hh < 0 or hh > 23 or mm < 0 or mm > 59:
            return None
        return hh, mm
    except Exception:
        return None


def _minutes(a: "datetime.datetime", b: "datetime.datetime") -> int:
    try:
        return int(round((a - b).total_seconds() / 60.0))
    except Exception:
        return 0


def _sgjo_marks_for_date(db: Session, *, id_usuario: int, rut: str, on_date: "datetime.date") -> dict[str, Any]:
    """
    Retorna la primera IN y última OUT del día (Chile), best-effort.
    """
    try:
        rows = db.execute(
            text(
                """
                SELECT tipo, created_at
                FROM public.sgjo_marcaciones
                WHERE ok IS TRUE
                  AND (
                    (id_usuario=:u)
                    OR (:rut <> '' AND lower(COALESCE(rut,''))=lower(:rut))
                  )
                  AND ((created_at AT TIME ZONE 'America/Santiago')::date = :d)
                ORDER BY created_at ASC
                """
            ),
            {"u": int(id_usuario), "rut": str(rut or ""), "d": on_date},
        ).mappings().all()
        ins = [r for r in rows if str(r.get("tipo") or "").upper() == "IN"]
        outs = [r for r in rows if str(r.get("tipo") or "").upper() == "OUT"]
        return {
            "in": (ins[0].get("created_at") if ins else None),
            "out": (outs[-1].get("created_at") if outs else None),
        }
    except Exception:
        return {"in": None, "out": None}


def _detect_desvios_for_staff(db: Session, *, staff: dict[str, Any], on_date: "datetime.date") -> list[dict[str, Any]]:
    """
    Crea/actualiza rrhh_desvios (idempotente) para un colaborador en una fecha.
    Retorna lista de desvíos detectados (pendientes o existentes).
    """
    out: list[dict[str, Any]] = []
    try:
        id_staff = int(staff.get("id_staff") or 0)
        id_usuario = int(staff.get("id_usuario") or 0) if str(staff.get("id_usuario") or "").isdigit() else 0
        rol = str(staff.get("rol") or "")
        tol = _tol_minutes_for_role(rol)
        if id_staff <= 0 or id_usuario <= 0:
            return []
        shift = _today_theoretical_shift(db, id_staff, on_date)
        if not shift:
            return []
        he = _parse_hhmm(str(shift.get("hora_entrada") or ""))
        hs = _parse_hhmm(str(shift.get("hora_salida") or ""))
        if not he or not hs:
            return []
        hh_in, mm_in = he
        hh_out, mm_out = hs
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/Santiago")
        exp_in = _dt.combine(on_date, _dt.min.time().replace(hour=hh_in, minute=mm_in), tzinfo=tz)
        exp_out = _dt.combine(on_date, _dt.min.time().replace(hour=hh_out, minute=mm_out), tzinfo=tz)
        marks = _sgjo_marks_for_date(db, id_usuario=id_usuario, rut=str(staff.get("rut") or ""), on_date=on_date)
        act_in = marks.get("in")
        act_out = marks.get("out")

        def upsert(tipo: str, *, diff_in: int | None, diff_out: int | None, impact_kind: str, impact_min: int | None):
            db.execute(
                text(
                    """
                    INSERT INTO rrhh_desvios(
                      fecha, id_staff, id_usuario, rut, colaborador, rol, centro_costo,
                      tipo, status,
                      expected_in, expected_out, actual_in, actual_out,
                      diff_in_min, diff_out_min,
                      impact_kind, impact_min,
                      meta
                    ) VALUES (
                      :f,:s,:u,:rut,:c,:r,:cc,
                      :t,'pendiente',
                      :ein,:eout,:ain,:aout,
                      :di,:do,
                      :ik,:im,
                      jsonb_build_object('tol_min', :tol)
                    )
                    ON CONFLICT (fecha, id_staff, tipo) DO UPDATE SET
                      id_usuario=EXCLUDED.id_usuario,
                      rut=EXCLUDED.rut,
                      colaborador=EXCLUDED.colaborador,
                      rol=EXCLUDED.rol,
                      centro_costo=EXCLUDED.centro_costo,
                      expected_in=EXCLUDED.expected_in,
                      expected_out=EXCLUDED.expected_out,
                      actual_in=EXCLUDED.actual_in,
                      actual_out=EXCLUDED.actual_out,
                      diff_in_min=EXCLUDED.diff_in_min,
                      diff_out_min=EXCLUDED.diff_out_min,
                      impact_kind=EXCLUDED.impact_kind,
                      impact_min=EXCLUDED.impact_min,
                      meta=EXCLUDED.meta,
                      -- No pisa status si ya fue decidido
                      status=CASE WHEN rrhh_desvios.status IN ('aprobado','rechazado') THEN rrhh_desvios.status ELSE 'pendiente' END
                    """
                ),
                {
                    "f": on_date,
                    "s": id_staff,
                    "u": id_usuario,
                    "rut": str(staff.get("rut") or ""),
                    "c": str(staff.get("colaborador") or ""),
                    "r": str(rol or ""),
                    "cc": str(staff.get("centro_costo") or ""),
                    "t": tipo,
                    "ein": f"{hh_in:02d}:{mm_in:02d}",
                    "eout": f"{hh_out:02d}:{mm_out:02d}",
                    "ain": act_in,
                    "aout": act_out,
                    "di": diff_in,
                    "do": diff_out,
                    "ik": impact_kind,
                    "im": impact_min,
                    "tol": int(tol),
                },
            )

        # Missing
        if act_in is None:
            upsert("MISSING_IN", diff_in=None, diff_out=None, impact_kind="AUSENCIA", impact_min=None)
            out.append({"tipo": "MISSING_IN"})
        if act_out is None:
            upsert("MISSING_OUT", diff_in=None, diff_out=None, impact_kind="AUSENCIA", impact_min=None)
            out.append({"tipo": "MISSING_OUT"})

        # Diffs
        if act_in is not None:
            try:
                act_in_dt = act_in.astimezone(tz) if hasattr(act_in, "astimezone") else act_in
                di = _minutes(act_in_dt, exp_in)
                if abs(int(di)) > int(tol):
                    if di > 0:
                        upsert("LATE_IN", diff_in=int(di), diff_out=None, impact_kind="DESCUENTO_HORAS", impact_min=int(di))
                        out.append({"tipo": "LATE_IN", "min": int(di)})
                    else:
                        upsert("EARLY_IN", diff_in=int(di), diff_out=None, impact_kind="HORAS_EXTRA", impact_min=int(abs(di)))
                        out.append({"tipo": "EARLY_IN", "min": int(di)})
            except Exception:
                pass
        if act_out is not None:
            try:
                act_out_dt = act_out.astimezone(tz) if hasattr(act_out, "astimezone") else act_out
                do = _minutes(act_out_dt, exp_out)
                # do < 0 => salió antes; do > 0 => salió después
                if abs(int(do)) > int(tol):
                    if do < 0:
                        upsert("EARLY_OUT", diff_in=None, diff_out=int(do), impact_kind="DESCUENTO_HORAS", impact_min=int(abs(do)))
                        out.append({"tipo": "EARLY_OUT", "min": int(do)})
                    else:
                        upsert("LATE_OUT", diff_in=None, diff_out=int(do), impact_kind="HORAS_EXTRA", impact_min=int(do))
                        out.append({"tipo": "LATE_OUT", "min": int(do)})
            except Exception:
                pass

        db.commit()
        return out
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return []


@router.post("/desvios/detect")
def detect_desvios(
    body: dict = Body(default_factory=dict),
    db: Session = Depends(get_db),
    me: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Detecta desvíos vs turno teórico.
    - SUPERADMIN/ADMIN/FINANZAS/RRHH: todos
    - JEFE/OPERACIONES/COMPRAS: solo su equipo (jefe_id_staff)
    """
    _ensure_tables(db)
    if not _is_rrhh_admin(me):
        raise HTTPException(status_code=403, detail="No autorizado")
    from datetime import date as _date
    ds = str(body.get("date") or "").strip()
    if ds:
        try:
            on_date = _date.fromisoformat(ds[:10])
        except Exception:
            raise HTTPException(status_code=400, detail="date inválida (YYYY-MM-DD)")
    else:
        on_date = db.execute(text("SELECT (now() AT TIME ZONE 'America/Santiago')::date")).scalar()
        on_date = _date.fromisoformat(str(on_date))

    role = _role_key(me)
    uid = _user_id(me) or 0
    # staff_id del jefe (si está vinculado)
    jefe_staff_id = None
    try:
        if uid:
            jefe_staff_id = db.execute(
                text("SELECT id_staff FROM rrhh_staff WHERE id_usuario=:u AND is_active IS TRUE ORDER BY id_staff DESC LIMIT 1"),
                {"u": int(uid)},
            ).scalar()
            jefe_staff_id = int(jefe_staff_id) if jefe_staff_id else None
    except Exception:
        jefe_staff_id = None

    where = "WHERE is_active IS TRUE"
    params: dict[str, Any] = {}
    if (("SUPERADMIN" in role) or (role == "ADMIN") or ("FINAN" in role) or ("RRHH" in role) or ("RECURSOS" in role)):
        pass
    else:
        # jefe/ops/compras: solo su equipo
        if jefe_staff_id:
            where += " AND jefe_id_staff=:j"
            params["j"] = int(jefe_staff_id)
        else:
            return {"ok": True, "date": str(on_date), "count": 0, "items": []}

    staff_rows = db.execute(
        text(
            f"""
            SELECT id_staff, colaborador, rut, id_usuario, rol, centro_costo
            FROM rrhh_staff
            {where}
            ORDER BY lower(colaborador) ASC, id_staff ASC
            """
        ),
        params,
    ).mappings().all()

    items = []
    for st in staff_rows:
        det = _detect_desvios_for_staff(db, staff=dict(st), on_date=on_date)
        if det:
            items.append({"id_staff": int(st["id_staff"]), "colaborador": st.get("colaborador"), "detected": det})
    return {"ok": True, "date": str(on_date), "count": len(items), "items": items}


@router.get("/desvios")
def list_desvios(
    estado: str | None = None,
    date: str | None = None,
    db: Session = Depends(get_db),
    me: dict = Depends(get_current_user),
) -> dict[str, Any]:
    _ensure_tables(db)
    if not _is_rrhh_admin(me):
        raise HTTPException(status_code=403, detail="No autorizado")
    role = _role_key(me)
    uid = _user_id(me) or 0
    jefe_staff_id = None
    try:
        if uid:
            jefe_staff_id = db.execute(
                text("SELECT id_staff FROM rrhh_staff WHERE id_usuario=:u AND is_active IS TRUE ORDER BY id_staff DESC LIMIT 1"),
                {"u": int(uid)},
            ).scalar()
            jefe_staff_id = int(jefe_staff_id) if jefe_staff_id else None
    except Exception:
        jefe_staff_id = None

    where = []
    params: dict[str, Any] = {}
    if estado:
        where.append("status = :st")
        params["st"] = str(estado).strip().lower()
    if date:
        where.append("fecha = :d")
        params["d"] = str(date)[:10]
    # scope por rol
    if (("SUPERADMIN" in role) or (role == "ADMIN") or ("FINAN" in role) or ("RRHH" in role) or ("RECURSOS" in role)):
        pass
    else:
        if jefe_staff_id:
            where.append("id_staff IN (SELECT id_staff FROM rrhh_staff WHERE jefe_id_staff=:j AND is_active IS TRUE)")
            params["j"] = int(jefe_staff_id)
        else:
            return {"ok": True, "items": []}

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    rows = db.execute(
        text(
            f"""
            SELECT id_desvio, created_at, fecha, id_staff, id_usuario, rut, colaborador, rol, centro_costo,
                   tipo, status, decided_at, decided_by, note,
                   expected_in, expected_out, actual_in, actual_out, diff_in_min, diff_out_min,
                   impact_kind, impact_min
            FROM rrhh_desvios
            {where_sql}
            ORDER BY fecha DESC, status ASC, colaborador ASC, id_desvio DESC
            LIMIT 500
            """
        ),
        params,
    ).mappings().all()
    return {"ok": True, "items": [dict(r) for r in rows]}


@router.put("/desvios/{id_desvio}")
def decide_desvio(
    id_desvio: int,
    body: dict,
    db: Session = Depends(get_db),
    me: dict = Depends(get_current_user),
) -> dict[str, Any]:
    _ensure_tables(db)
    if not _is_rrhh_admin(me):
        raise HTTPException(status_code=403, detail="No autorizado")
    estado = str(body.get("status") or "").strip().lower()
    if estado not in ("aprobado", "rechazado"):
        raise HTTPException(status_code=400, detail="status inválido")
    note = str(body.get("note") or "").strip()
    uid = _user_id(me) or 0

    row = db.execute(
        text(
            """
            SELECT id_desvio, fecha, id_staff, id_usuario, colaborador, tipo, impact_kind, impact_min, status
            FROM rrhh_desvios
            WHERE id_desvio=:id
            """
        ),
        {"id": int(id_desvio)},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Desvío no existe")
    if str(row.get("status") or "").lower() in ("aprobado", "rechazado") and not body.get("force"):
        return {"ok": True, "already": True}

    db.execute(
        text(
            """
            UPDATE rrhh_desvios
            SET status=:st, decided_at=now(), decided_by=:by, note=:n
            WHERE id_desvio=:id
            """
        ),
        {"id": int(id_desvio), "st": estado, "by": int(uid) if uid else None, "n": note or None},
    )

    # Impacto (reglas usuario: 4=C, 5=C).
    tipo = str(row.get("tipo") or "").upper()
    impact_kind = str(row.get("impact_kind") or "").upper()
    impact_min = int(row.get("impact_min") or 0) if str(row.get("impact_min") or "").strip() != "" else 0
    id_staff = int(row.get("id_staff") or 0)
    fecha = str(row.get("fecha") or "")[:10]
    colaborador = str(row.get("colaborador") or "Colaborador")

    try:
        if estado == "rechazado":
            # No aprobado => ausencias o descuento horas
            if tipo in ("MISSING_IN", "MISSING_OUT"):
                # ausencia día completo
                db.execute(
                    text(
                        """
                        INSERT INTO rrhh_inasistencias(colaborador, fecha, dias, motivo)
                        VALUES (:c, :f, 1, :m)
                        """
                    ),
                    {"c": colaborador, "f": fecha, "m": f"Auto (desvío {tipo})"},
                )
                db.execute(
                    text(
                        """
                        INSERT INTO rrhh_ajustes(fecha, id_staff, id_desvio, kind, minutos, note)
                        VALUES (:f,:s,:d,'AUSENCIA',0,:n)
                        """
                    ),
                    {"f": fecha, "s": id_staff, "d": int(id_desvio), "n": note or None},
                )
            else:
                # descuento proporcional por minutos
                mins = abs(int(impact_min or 0))
                db.execute(
                    text(
                        """
                        INSERT INTO rrhh_ajustes(fecha, id_staff, id_desvio, kind, minutos, note)
                        VALUES (:f,:s,:d,'DESCUENTO_HORAS',:m,:n)
                        """
                    ),
                    {"f": fecha, "s": id_staff, "d": int(id_desvio), "m": int(mins), "n": note or None},
                )
        else:
            # aprobado => justificado o horas extra según caso
            if impact_kind == "HORAS_EXTRA" and tipo in ("EARLY_IN", "LATE_OUT"):
                mins = abs(int(impact_min or 0))
                db.execute(
                    text(
                        """
                        INSERT INTO rrhh_ajustes(fecha, id_staff, id_desvio, kind, minutos, note)
                        VALUES (:f,:s,:d,'HORAS_EXTRA',:m,:n)
                        """
                    ),
                    {"f": fecha, "s": id_staff, "d": int(id_desvio), "m": int(mins), "n": note or None},
                )
            else:
                # justificado: no ajuste
                db.execute(
                    text(
                        """
                        INSERT INTO rrhh_ajustes(fecha, id_staff, id_desvio, kind, minutos, note)
                        VALUES (:f,:s,:d,'JUSTIFICADO',0,:n)
                        """
                    ),
                    {"f": fecha, "s": id_staff, "d": int(id_desvio), "n": note or None},
                )
    except Exception:
        pass

    db.commit()

    # Notificar siempre (correo + alertas internas)
    try:
        subj = f"RRHH · Desvío {tipo} · {colaborador} · {estado.upper()}"
        txt = f"Colaborador: {colaborador}\nFecha: {fecha}\nTipo: {tipo}\nEstado: {estado}\nObs: {note or ''}\n"
        _notify_rrhh_admins(db, subject=subj, body=txt)
    except Exception:
        pass
    try:
        from backend.core.system_notifs import push_system_notif
        lid = int(row.get("id_usuario") or 0) or int(id_desvio)
        push_system_notif(db, kind="RRHH_DESVIO", role_target="RRHH", id_lead=lid, title="RRHH · Desvío", body=f"{colaborador} · {tipo} · {estado}", payload={"id_desvio": int(id_desvio), "estado": estado, "tipo": tipo, "fecha": fecha})
        push_system_notif(db, kind="RRHH_DESVIO", role_target="FINANZAS", id_lead=lid, title="RRHH · Desvío", body=f"{colaborador} · {tipo} · {estado}", payload={"id_desvio": int(id_desvio), "estado": estado, "tipo": tipo, "fecha": fecha})
        push_system_notif(db, kind="RRHH_DESVIO", role_target="ADMIN", id_lead=lid, title="RRHH · Desvío", body=f"{colaborador} · {tipo} · {estado}", payload={"id_desvio": int(id_desvio), "estado": estado, "tipo": tipo, "fecha": fecha})
        push_system_notif(db, kind="RRHH_DESVIO", role_target="SUPERADMIN", id_lead=lid, title="RRHH · Desvío", body=f"{colaborador} · {tipo} · {estado}", payload={"id_desvio": int(id_desvio), "estado": estado, "tipo": tipo, "fecha": fecha})
    except Exception:
        pass
    return {"ok": True}

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
    # Admin RRHH (gestión operativa: turnos/horarios/marcaciones/solicitudes)
    if ("SUPERADMIN" in r) or (r == "ADMIN") or (r == "SUPER ADMIN") or (r == "SUPER_ADMIN"):
        return True
    if ("FINAN" in r) or (r == "RRHH") or ("RECURSOS HUMANOS" in r):
        return True
    if ("JEFE" in r) or ("OPERACIONES" in r) or ("COMPRAS" in r):
        return True
    return False

def _is_rrhh_super(user: dict) -> bool:
    # Super RRHH (datos maestros: colaboradores + vincular usuarios + nómina completa)
    r = _role_key(user)
    return ("SUPERADMIN" in r) or (r == "ADMIN") or ("FINAN" in r) or (r == "RRHH") or ("RECURSOS HUMANOS" in r)

def _require_rrhh_admin(user: dict) -> None:
    if not _is_rrhh_admin(user):
        raise HTTPException(status_code=403, detail="Solo Admin/SuperAdmin.")

def _require_rrhh_super(user: dict) -> None:
    if not _is_rrhh_super(user):
        raise HTTPException(status_code=403, detail="Solo SuperAdmin/Admin/RRHH/Finanzas.")

def _notify_rrhh_admins(db: Session, *, subject: str, body: str) -> None:
    """
    Notifica por correo a RRHH + Admin/SuperAdmin/Finanzas (best-effort).
    """
    try:
        from backend.core.email import send_email_group

        to_list: list[str] = []
        rrhh_to = (os.getenv("RRHH_NOTIFY_TO") or "").strip()
        if rrhh_to:
            to_list.append(rrhh_to)
        rrhh_cc = [x.strip() for x in str(os.getenv("RRHH_NOTIFY_CC") or "").split(",") if x.strip()]
        to_list.extend(rrhh_cc)

        rows = db.execute(
            text(
                """
                SELECT DISTINCT COALESCE(NULLIF(btrim(email),''), NULL) AS email
                FROM public.usuarios
                WHERE COALESCE(is_active, TRUE) IS TRUE
                  AND (
                    upper(COALESCE(rol,'')) LIKE '%ADMIN%'
                    OR upper(COALESCE(rol,'')) LIKE '%SUPER%'
                    OR upper(COALESCE(rol,'')) LIKE '%FINAN%'
                    OR upper(COALESCE(rol,'')) LIKE '%RRHH%'
                    OR upper(COALESCE(rol,'')) LIKE '%RECURSOS%'
                  )
                """
            )
        ).fetchall()
        for (em,) in rows or []:
            if em and str(em).strip():
                to_list.append(str(em).strip())

        # de-dup y limpieza
        to_list = list(dict.fromkeys([x for x in to_list if x]))
        if not to_list:
            return
        send_email_group(to_list, subject, body)
    except Exception:
        return


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

        # adelantos por colaborador (robusto a acentos/espacios/orden)
        adel_rows = db.execute(
            text(
                """
                SELECT colaborador, COALESCE(SUM(monto),0) AS total
                FROM rrhh_adelantos
                GROUP BY colaborador
                """
            )
        ).fetchall()
        adel_map: dict[str, float] = {}
        adel_tokens_map: dict[str, float] = {}
        for r in adel_rows:
            raw = str(r[0] or "")
            total = float(r[1] or 0)
            k = _norm_person_key(raw)
            kt = _norm_person_tokens_key(raw)
            if k:
                adel_map[k] = float(adel_map.get(k, 0) or 0) + total
            if kt:
                adel_tokens_map[kt] = float(adel_tokens_map.get(kt, 0) or 0) + total

        # Si existen adelantos de colaboradores que no están en rrhh_staff, los incluimos igual (para que nómina no "oculte" pagos).
        staff_keys = {_norm_person_key(r.get("colaborador") or "") for r in staff_rows}
        for k in sorted(set(adel_map.keys()) - set(staff_keys)):
            if not str(k or "").strip():
                continue
            staff_rows.append(
                {
                    "id_staff": None,
                    "colaborador": str(k).title(),
                    "centro_costo": "",
                    "rol": "",
                    "afp": "",
                    "afp_pct": None,
                    "salud_tipo": "",
                    "salud_pct": None,
                    "fecha_ingreso": None,
                    "observaciones": "Auto: colaborador no está en rrhh_staff (tiene adelantos).",
                    "ficha": {},
                }
            )

        # inasistencias del mes actual
        faltas_map = {}
        faltas_tokens_map = {}
        try:
            faltas_rows = db.execute(
                text(
                    """
                    SELECT colaborador, COALESCE(SUM(dias),0) AS dias
                    FROM rrhh_inasistencias
                    WHERE fecha >= date_trunc('month', current_date)
                      AND fecha < (date_trunc('month', current_date) + interval '1 month')
                    GROUP BY colaborador
                    """
                )
            ).fetchall()
            faltas_map = {}
            faltas_tokens_map = {}
            for r in faltas_rows:
                raw = str(r[0] or "")
                dias = float(r[1] or 0)
                k = _norm_person_key(raw)
                kt = _norm_person_tokens_key(raw)
                if k:
                    faltas_map[k] = float(faltas_map.get(k, 0) or 0) + dias
                if kt:
                    faltas_tokens_map[kt] = float(faltas_tokens_map.get(kt, 0) or 0) + dias
        except Exception:
            faltas_map = {}
            faltas_tokens_map = {}

        # vacaciones acumuladas/tomadas
        vac_map = {}
        vac_tokens_map = {}
        try:
            vac_rows = db.execute(
                text(
                    """
                    SELECT colaborador, COALESCE(SUM(dias),0) AS dias
                    FROM rrhh_vacaciones
                    GROUP BY colaborador
                    """
                )
            ).fetchall()
            vac_map = {}
            vac_tokens_map = {}
            for r in vac_rows:
                raw = str(r[0] or "")
                dias = float(r[1] or 0)
                k = _norm_person_key(raw)
                kt = _norm_person_tokens_key(raw)
                if k:
                    vac_map[k] = float(vac_map.get(k, 0) or 0) + dias
                if kt:
                    vac_tokens_map[kt] = float(vac_tokens_map.get(kt, 0) or 0) + dias
        except Exception:
            vac_map = {}
            vac_tokens_map = {}

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
            key = _norm_person_key(d.get("colaborador") or "")
            key_tokens = _norm_person_tokens_key(d.get("colaborador") or "")
            faltas = float(faltas_map.get(key, 0) or 0)
            if not faltas and key_tokens:
                faltas = float(faltas_tokens_map.get(key_tokens, 0) or 0)
            sueldo = _num(
                ficha.get("hh_liquido")
                or ficha.get("renta_liquida")
                or ficha.get("sueldo_fijo")
                or 0
            )
            adel = float(adel_map.get(key, 0) or 0)
            if not adel and key_tokens:
                adel = float(adel_tokens_map.get(key_tokens, 0) or 0)
            vac_tomadas = float(vac_map.get(key, 0) or 0)
            if not vac_tomadas and key_tokens:
                vac_tomadas = float(vac_tokens_map.get(key_tokens, 0) or 0)
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


@router.get("/push/status")
def rrhh_push_status(db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    """
    Control de compliance de notificaciones:
    lista colaboradores RRHH con su usuario linkeado y si tienen suscripciones push activas.
    """
    _ensure_tables(db)
    _require_rrhh_admin(me)
    try:
        rows = db.execute(
            text(
                """
                SELECT s.id_staff, s.colaborador, s.id_usuario,
                       COALESCE(s.rol,'') AS rol,
                       COALESCE(s.centro_costo,'') AS centro_costo,
                       COALESCE(cnt.cnt,0)::int AS push_active
                FROM rrhh_staff s
                LEFT JOIN (
                  SELECT user_id, COUNT(*)::int AS cnt
                  FROM public.push_subscriptions
                  WHERE active IS TRUE
                  GROUP BY user_id
                ) cnt ON cnt.user_id = s.id_usuario
                WHERE s.is_active IS TRUE
                ORDER BY COALESCE(s.centro_costo,''), COALESCE(s.rol,''), s.colaborador ASC, s.id_staff ASC
                """
            )
        ).mappings().all()
        items = [dict(r) for r in rows]
        missing = [x for x in items if int(x.get("id_usuario") or 0) > 0 and int(x.get("push_active") or 0) <= 0]
        unlinked = [x for x in items if not x.get("id_usuario")]
        return {"ok": True, "items": items, "missing_push": len(missing), "unlinked_users": len(unlinked)}
    except Exception as e:
        db.rollback()
        return {"ok": False, "detail": str(e)}


@router.post("/reminders/run")
def rrhh_reminders_run(
    dry: int | None = None,
    force: int | None = None,
    db: Session = Depends(get_db),
    me: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Ejecuta el motor de recordatorios IN/OUT manualmente (para pruebas).
    En producción corre automáticamente via middleware, pero este endpoint ayuda a depurar.
    """
    _ensure_tables(db)
    _require_rrhh_admin(me)
    try:
        from backend.core.rrhh_reminders import run_rrhh_mark_reminders

        return run_rrhh_mark_reminders(dry_run=bool(int(dry or 0)), force_run=bool(int(force or 0)))
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        return {"ok": False, "detail": str(e)}


@router.get("/reminders/state")
def rrhh_reminders_state(
    date: str | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
    me: dict = Depends(get_current_user),
) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    try:
        if date:
            d = str(date)[:10]
        else:
            d = db.execute(text("SELECT (now() AT TIME ZONE 'America/Santiago')::date")).scalar()
            d = str(d)[:10]
        rows = db.execute(
            text(
                """
                SELECT fecha, id_staff, kind, attempt, last_sent_at, updated_at
                FROM rrhh_reminder_state
                WHERE fecha=:d
                ORDER BY updated_at DESC NULLS LAST, id_staff ASC
                LIMIT :lim
                """
            ),
            {"d": d, "lim": max(1, min(2000, int(limit)))},
        ).mappings().all()
        return {"ok": True, "date": d, "items": [dict(r) for r in rows]}
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
        # Resumen (semanal/mensual) por pares IN/OUT. Best-effort; no bloquea si falla.
        summary: dict[str, Any] | None = None
        try:
            tz = "America/Santiago"
            today = datetime.date.today()
            week_start = today - datetime.timedelta(days=today.weekday())
            week_end = week_start + datetime.timedelta(days=6)
            month_start = datetime.date(today.year, today.month, 1)

            q = text(
                """
                WITH marks AS (
                  SELECT (created_at AT TIME ZONE :tz) AS ts,
                         (created_at AT TIME ZONE :tz)::date AS d,
                         UPPER(COALESCE(tipo,'')) AS tipo
                  FROM public.sgjo_marcaciones
                  WHERE id_usuario=:u
                    AND ok IS TRUE
                    AND created_at >= (:month_start::date - INTERVAL '1 day')
                ),
                per_day AS (
                  SELECT d,
                         MIN(ts) FILTER (WHERE tipo='IN')  AS tin,
                         MAX(ts) FILTER (WHERE tipo='OUT') AS tout
                  FROM marks
                  GROUP BY d
                ),
                hours AS (
                  SELECT d,
                         CASE
                           WHEN tin IS NULL OR tout IS NULL OR tout <= tin THEN 0
                           ELSE GREATEST(0, EXTRACT(EPOCH FROM (tout - tin))/3600.0)
                         END AS h_raw
                  FROM per_day
                ),
                hours2 AS (
                  SELECT d,
                         CASE WHEN h_raw >= 6 THEN GREATEST(0, h_raw - 1.0) ELSE h_raw END AS h
                  FROM hours
                )
                SELECT
                  COALESCE(SUM(h) FILTER (WHERE d BETWEEN :ws AND :we), 0)::float AS week_hours,
                  COALESCE(SUM(h) FILTER (WHERE d BETWEEN :ms AND :today), 0)::float AS month_hours
                FROM hours2
                """
            )
            row = db.execute(
                q,
                {
                    "tz": tz,
                    "u": int(uid),
                    "ws": week_start.isoformat(),
                    "we": week_end.isoformat(),
                    "ms": month_start.isoformat(),
                    "today": today.isoformat(),
                    "month_start": month_start.isoformat(),
                },
            ).mappings().first()
            summary = {
                "week": {"from": week_start.isoformat(), "to": week_end.isoformat()},
                "month": {"from": month_start.isoformat(), "to": today.isoformat()},
                "week_hours": float((row or {}).get("week_hours") or 0),
                "month_hours": float((row or {}).get("month_hours") or 0),
            }
        except Exception:
            summary = None
        return {"ok": True, "items": [dict(r) for r in rows], "summary": summary}
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
        # Aviso por correo + alerta interna (best-effort, pero NO silencioso).
        try:
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
                + "\nEstado: pendiente\n"
                + f"Usuario CRM ID: {uid}\n"
                + f"RUT: {data.get('rut') or ''}\n"
            )
            _notify_rrhh_admins(db, subject=subj, body=txt)
        except Exception:
            # No frenes la solicitud.
            pass
        try:
            from backend.core.system_notifs import push_system_notif
            # id_lead se usa como "id" genérico para dedupe; aquí usamos id_usuario.
            lid = int(uid)
            title = "RRHH · Nueva solicitud"
            body_txt = f"{colaborador} · {tipo} · pendiente"
            push_system_notif(db, kind="RRHH_SOLICITUD", role_target="RRHH", id_lead=lid, title=title, body=body_txt, payload={"tipo": tipo, "colaborador": colaborador, "estado": "pendiente", "id_usuario": lid})
            push_system_notif(db, kind="RRHH_SOLICITUD", role_target="ADMIN", id_lead=lid, title=title, body=body_txt, payload={"tipo": tipo, "colaborador": colaborador, "estado": "pendiente", "id_usuario": lid})
            push_system_notif(db, kind="RRHH_SOLICITUD", role_target="SUPERADMIN", id_lead=lid, title=title, body=body_txt, payload={"tipo": tipo, "colaborador": colaborador, "estado": "pendiente", "id_usuario": lid})
            # Adelantos/quincenas también se notifican a finanzas.
            if tipo == "adelanto":
                push_system_notif(db, kind="RRHH_SOLICITUD", role_target="FINANZAS", id_lead=lid, title=title, body=body_txt, payload={"tipo": tipo, "colaborador": colaborador, "estado": "pendiente", "id_usuario": lid})
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
    try:
        _notify_rrhh_admins(
            db,
            subject=f"RRHH · Turno actualizado/creado · {nombre}",
            body=(
                f"Turno: {nombre}\n"
                f"Entrada: {hora_entrada}\n"
                f"Salida: {hora_salida}\n"
                f"Tolerancia (min): {int(body.get('tolerancia_min') or 0)}\n"
                f"Colación auto: {bool(body.get('colacion_auto', True))}\n"
                f"Colación: {(body.get('colacion_ini') or '13:30')}–{(body.get('colacion_fin') or '14:30')}\n"
            ),
        )
    except Exception:
        pass
    return {"ok": True}


@router.delete("/turnos/{id_turno}")
def turnos_delete(id_turno: int, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    try:
        tid = int(id_turno)
    except Exception:
        raise HTTPException(status_code=400, detail="id_turno inválido")

    in_use = db.execute(
        text(
            """
            SELECT COUNT(*)::int
            FROM rrhh_horarios
            WHERE is_active IS TRUE AND id_turno=:t
            """
        ),
        {"t": tid},
    ).scalar()
    if int(in_use or 0) > 0:
        raise HTTPException(status_code=400, detail="Turno en uso en horarios activos. Elimina/desactiva esos horarios primero.")

    db.execute(text("UPDATE rrhh_turnos SET is_active=FALSE, updated_at=now() WHERE id_turno=:t"), {"t": tid})
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

    # Validación: no superar 42 horas semanales (con colación auto=1h si aplica).
    try:
        t = db.execute(
            text(
                """
                SELECT hora_entrada, hora_salida, COALESCE(colacion_auto, TRUE) AS colacion_auto
                FROM rrhh_turnos
                WHERE id_turno=:id AND is_active IS TRUE
                LIMIT 1
                """
            ),
            {"id": int(id_turno)},
        ).mappings().first()
        if t:
            he = str(t.get("hora_entrada") or "")
            hs = str(t.get("hora_salida") or "")

            def _hm(s: str) -> int:
                parts = (s or "").strip().split(":")
                if len(parts) < 2:
                    return 0
                return int(parts[0]) * 60 + int(parts[1])

            m_in = _hm(he)
            m_out = _hm(hs)
            if m_out <= m_in:
                m_out += 24 * 60
            dur_min = max(0, m_out - m_in)
            if bool(t.get("colacion_auto")):
                dur_min = max(0, dur_min - 60)

            if dow_mask is None:
                day_count = 7
            else:
                day_count = sum(1 for k in range(7) if int(dow_mask) & (1 << k))

            weekly_hours = (dur_min / 60.0) * float(day_count or 0)
            if weekly_hours > 42.0 + 1e-6:
                raise HTTPException(status_code=400, detail=f"Horario excede 42h/semana (≈ {weekly_hours:.1f}h). Ajusta días o el turno.")
    except HTTPException:
        raise
    except Exception:
        # Best-effort: no bloquea si falla el cálculo.
        pass

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
    try:
        st = db.execute(
            text(
                """
                SELECT colaborador, COALESCE(centro_costo,''), COALESCE(rol,''), id_usuario
                FROM rrhh_staff
                WHERE id_staff=:id
                LIMIT 1
                """
            ),
            {"id": int(id_staff)},
        ).first()
        tn = db.execute(
            text("SELECT nombre, hora_entrada, hora_salida FROM rrhh_turnos WHERE id_turno=:id LIMIT 1"),
            {"id": int(id_turno)},
        ).first()
        # Notificar al colaborador (si está linkeado a un usuario del sistema).
        try:
            uid = int(st[3]) if (st and st[3]) else None
        except Exception:
            uid = None
        if uid:
            try:
                from backend.core.webpush import send_webpush_to_users

                title = "RRHH · Nuevo horario asignado"
                body_txt = (
                    f"{(tn[0] if tn else 'Turno')} · {(tn[1] if tn else '')}-{(tn[2] if tn else '')}\n"
                    f"Desde: {desde}\n"
                    f"Hasta: {hasta or 'Indefinido'}"
                )
                send_webpush_to_users(
                    user_ids=[uid],
                    title=title,
                    body=body_txt[:180],
                    url="/crm/web/views/rrhh_portal.html?v=rrhh-horario",
                    tag=f"rrhh-horario-{uid}-{id_staff}",
                )
            except Exception:
                pass
        _notify_rrhh_admins(
            db,
            subject="RRHH · Horario asignado",
            body=(
                f"Colaborador: {(st[0] if st else id_staff)}\n"
                f"Centro de costo: {(st[1] if st else '')}\n"
                f"Rol: {(st[2] if st else '')}\n"
                f"Turno: {(tn[0] if tn else id_turno)} ({(tn[1] if tn else '')}-{(tn[2] if tn else '')})\n"
                f"Desde: {desde}\n"
                f"Hasta: {hasta or 'Indefinido'}\n"
                f"Días mask: {dow_mask if dow_mask is not None else 'Todos'}\n"
            ),
        )
    except Exception:
        pass
    return {"ok": True}


@router.post("/horarios/plan_week")
def horarios_plan_week(body: dict, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    """
    Asigna horarios por semana (día a día) sin destruir el "horario base" (si existe).
    Estrategia:
    - Inserta 1 fila por día (dow_mask=bit) con rango [week_start, week_end].
    - Desactiva solamente filas previas del mismo staff y mismo rango [week_start, week_end]
      (para que re-planificar la semana sea idempotente).
    """
    _ensure_tables(db)
    _require_rrhh_admin(me)
    try:
        id_staff = int(body.get("id_staff"))
    except Exception:
        raise HTTPException(status_code=400, detail="id_staff requerido")
    week_start = (body.get("week_start") or "").strip()
    if not week_start:
        raise HTTPException(status_code=400, detail="week_start requerido (YYYY-MM-DD)")
    try:
        d0 = datetime.date.fromisoformat(week_start[:10])
    except Exception:
        raise HTTPException(status_code=400, detail="week_start inválido")
    d6 = d0 + datetime.timedelta(days=6)

    days = body.get("days") or {}
    # Acepta {0: id_turno, 1: id_turno, ...} o {"0": id_turno, ...} o lista len=7.
    mapping: dict[int, int] = {}
    if isinstance(days, list):
        for i, v in enumerate(days[:7]):
            try:
                if v is None or str(v).strip() == "":
                    continue
                mapping[int(i)] = int(v)
            except Exception:
                continue
    elif isinstance(days, dict):
        for k, v in days.items():
            try:
                if v is None or str(v).strip() == "":
                    continue
                mapping[int(k)] = int(v)
            except Exception:
                continue

    if not mapping:
        raise HTTPException(status_code=400, detail="days vacío: asigna al menos 1 día")

    # Validar turnos existen y activos
    ids = sorted(set(int(x) for x in mapping.values()))
    ok_turnos = db.execute(
        text("SELECT id_turno FROM rrhh_turnos WHERE is_active IS TRUE AND id_turno = ANY(:ids)"),
        {"ids": ids},
    ).fetchall()
    ok_set = {int(r[0]) for r in ok_turnos}
    missing = [t for t in ids if t not in ok_set]
    if missing:
        raise HTTPException(status_code=400, detail=f"Turnos inválidos/inactivos: {missing}")

    # Desactiva planificación anterior de esa semana (mismo rango), sin tocar el horario base.
    db.execute(
        text(
            """
            UPDATE rrhh_horarios
            SET is_active=FALSE
            WHERE is_active IS TRUE
              AND id_staff=:s
              AND desde=:d0
              AND hasta=:d6
            """
        ),
        {"s": id_staff, "d0": d0, "d6": d6},
    )

    created = 0
    for dow, id_turno in mapping.items():
        if dow < 0 or dow > 6:
            continue
        db.execute(
            text(
                """
                INSERT INTO rrhh_horarios(id_staff,id_turno,desde,hasta,dow_mask,is_active)
                VALUES (:s,:t,:d0,:d6,:m,TRUE)
                """
            ),
            {"s": id_staff, "t": int(id_turno), "d0": d0, "d6": d6, "m": int(1 << int(dow))},
        )
        created += 1

    db.commit()

    # Notifica al colaborador si está linkeado
    try:
        st = db.execute(
            text("SELECT colaborador, id_usuario FROM rrhh_staff WHERE id_staff=:s LIMIT 1"),
            {"s": int(id_staff)},
        ).first()
        uid = int(st[1]) if (st and st[1]) else None
        if uid:
            try:
                from backend.core.webpush import send_webpush_to_users

                title = "RRHH · Horario semanal asignado"
                body_txt = f"Semana {d0.isoformat()} → {d6.isoformat()}"
                send_webpush_to_users(
                    user_ids=[uid],
                    title=title,
                    body=body_txt,
                    url="/crm/web/views/rrhh_portal.html?v=rrhh-horario-week",
                    tag=f"rrhh-week-{uid}-{d0.isoformat()}",
                )
            except Exception:
                pass
    except Exception:
        pass

    return {"ok": True, "week_start": d0.isoformat(), "week_end": d6.isoformat(), "created": created}


@router.post("/horarios/plan_template")
def horarios_plan_template(body: dict, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    """
    Asigna un "horario base" indefinido (repite semanalmente desde una fecha).
    Estrategia:
    - Desactiva filas previas indefinidas (hasta IS NULL) del staff.
    - Inserta 1 fila por día (dow_mask=bit) con desde=start y hasta=NULL.
    """
    _ensure_tables(db)
    _require_rrhh_admin(me)
    try:
        id_staff = int(body.get("id_staff"))
    except Exception:
        raise HTTPException(status_code=400, detail="id_staff requerido")

    start = (body.get("start") or body.get("desde") or body.get("week_start") or "").strip()
    if not start:
        raise HTTPException(status_code=400, detail="start requerido (YYYY-MM-DD)")
    try:
        d0 = datetime.date.fromisoformat(start[:10])
    except Exception:
        raise HTTPException(status_code=400, detail="start inválido")

    days = body.get("days") or {}
    mapping: dict[int, int] = {}
    if isinstance(days, list):
        for i, v in enumerate(days[:7]):
            try:
                if v is None or str(v).strip() == "":
                    continue
                mapping[int(i)] = int(v)
            except Exception:
                continue
    elif isinstance(days, dict):
        for k, v in days.items():
            try:
                if v is None or str(v).strip() == "":
                    continue
                mapping[int(k)] = int(v)
            except Exception:
                continue

    if not mapping:
        raise HTTPException(status_code=400, detail="days vacío: asigna al menos 1 día")

    ids = sorted(set(int(x) for x in mapping.values()))
    ok_turnos = db.execute(
        text("SELECT id_turno FROM rrhh_turnos WHERE is_active IS TRUE AND id_turno = ANY(:ids)"),
        {"ids": ids},
    ).fetchall()
    ok_set = {int(r[0]) for r in ok_turnos}
    missing = [t for t in ids if t not in ok_set]
    if missing:
        raise HTTPException(status_code=400, detail=f"Turnos inválidos/inactivos: {missing}")

    # Reemplaza el "base" indefinido actual (si existe)
    db.execute(
        text(
            """
            UPDATE rrhh_horarios
            SET is_active=FALSE
            WHERE is_active IS TRUE
              AND id_staff=:s
              AND hasta IS NULL
            """
        ),
        {"s": int(id_staff)},
    )

    created = 0
    for dow, id_turno in mapping.items():
        if dow < 0 or dow > 6:
            continue
        db.execute(
            text(
                """
                INSERT INTO rrhh_horarios(id_staff,id_turno,desde,hasta,dow_mask,is_active)
                VALUES (:s,:t,:d0,NULL,:m,TRUE)
                """
            ),
            {"s": id_staff, "t": int(id_turno), "d0": d0, "m": int(1 << int(dow))},
        )
        created += 1

    db.commit()

    # Notifica al colaborador si está linkeado
    try:
        st = db.execute(
            text("SELECT colaborador, id_usuario FROM rrhh_staff WHERE id_staff=:s LIMIT 1"),
            {"s": int(id_staff)},
        ).first()
        uid = int(st[1]) if (st and st[1]) else None
        if uid:
            try:
                from backend.core.webpush import send_webpush_to_users

                title = "RRHH · Horario indefinido asignado"
                body_txt = f"Desde {d0.isoformat()} (semanal)"
                send_webpush_to_users(
                    user_ids=[uid],
                    title=title,
                    body=body_txt,
                    url="/crm/web/views/rrhh_portal.html?v=rrhh-horario-template",
                    tag=f"rrhh-template-{uid}-{d0.isoformat()}",
                )
            except Exception:
                pass
    except Exception:
        pass

    return {"ok": True, "start": d0.isoformat(), "created": created}


@router.delete("/horarios/{id_horario}")
def horarios_delete(id_horario: int, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    try:
        hid = int(id_horario)
    except Exception:
        raise HTTPException(status_code=400, detail="id_horario inválido")
    db.execute(text("UPDATE rrhh_horarios SET is_active=FALSE WHERE id_horario=:id"), {"id": hid})
    db.commit()
    return {"ok": True}


@router.post("/horarios/{id_horario}/delete")
def horarios_delete_post(id_horario: int, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    # Fallback para entornos donde DELETE puede fallar/interceptarse.
    return horarios_delete(id_horario=id_horario, db=db, me=me)


@router.get("/horarios")
def horarios_list(id_staff: int | None = None, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    if id_staff:
        rows = db.execute(
            text(
                """
                SELECT h.*,
                       t.nombre AS turno_nombre, t.hora_entrada, t.hora_salida, t.tolerancia_min,
                       t.colacion_auto, t.colacion_ini, t.colacion_fin
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
                SELECT h.*,
                       t.nombre AS turno_nombre, t.hora_entrada, t.hora_salida, t.tolerancia_min,
                       t.colacion_auto, t.colacion_ini, t.colacion_fin
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
    _require_rrhh_super(me)
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

@router.get("/staff/assignable")
def staff_assignable(db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    """
    Lista acotada de colaboradores para asignar horarios/turnos.
    Visible para Admin RRHH (incluye Jefe Operaciones/Compras/Operaciones).
    """
    _ensure_tables(db)
    _require_rrhh_admin(me)
    rows = db.execute(
        text(
            """
            SELECT id_staff, colaborador, centro_costo, rol, COALESCE(is_active, TRUE) AS is_active, id_usuario, rut
            FROM rrhh_staff
            WHERE COALESCE(is_active, TRUE) IS TRUE
            ORDER BY lower(colaborador) ASC, id_staff ASC
            """
        )
    ).mappings().all()
    return {"ok": True, "items": [dict(r) for r in rows]}


@router.get("/staff/{id_staff}")
def staff_get(id_staff: int, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_super(me)
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
    _require_rrhh_super(me)
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
    _require_rrhh_super(me)
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
    _require_rrhh_super(me)
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
    _require_rrhh_super(me)
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
    _require_rrhh_super(me)
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
    _require_rrhh_super(me)
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
    _require_rrhh_super(me)
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
        prev = db.execute(
            text(
                """
                SELECT id_solicitud, colaborador, tipo, doc_tipo, fecha_inicio, fecha_fin, dias, monto, motivo, estado, id_usuario, rut, created_at
                FROM rrhh_solicitudes
                WHERE id_solicitud = :id
                """
            ),
            {"id": int(id_solicitud)},
        ).mappings().first()
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

        # Si se aprueba un adelanto, reflejar inmediatamente en rrhh_adelantos (nómina).
        try:
            new_estado = str(body.get("estado") or "").strip().lower()
            tipo = str((prev or {}).get("tipo") or "").strip().lower()
            if tipo == "adelanto" and new_estado in ("aprobada", "aprobado", "aprobado_rrhh", "approved"):
                colab = str((prev or {}).get("colaborador") or "").strip()
                monto = (prev or {}).get("monto")
                if colab and monto is not None:
                    fecha = (prev or {}).get("fecha_inicio") or None
                    # Evitar duplicados (mismo colaborador+monto+fecha).
                    exists = db.execute(
                        text(
                            """
                            SELECT 1
                            FROM rrhh_adelantos
                            WHERE lower(btrim(colaborador)) = lower(btrim(:c))
                              AND COALESCE(monto,0) = COALESCE(:m,0)
                              AND (:f IS NULL OR fecha = :f)
                            LIMIT 1
                            """
                        ),
                        {"c": colab, "m": monto, "f": fecha},
                    ).scalar()
                    if not exists:
                        db.execute(
                            text(
                                """
                                INSERT INTO rrhh_adelantos(colaborador, monto, fecha, motivo)
                                VALUES (:c, :m, COALESCE(:f, CURRENT_DATE), :mot)
                                """
                            ),
                            {
                                "c": colab,
                                "m": monto,
                                "f": fecha,
                                "mot": f"Auto: solicitud aprobada #{int(id_solicitud)}",
                            },
                        )
                        db.commit()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass

        # Notificaciones (correo + alertas) cuando cambia estado.
        try:
            new_estado = str(body.get("estado") or "").strip().lower()
            old_estado = str((prev or {}).get("estado") or "").strip().lower()
            if new_estado and new_estado != old_estado:
                colaborador = str((prev or {}).get("colaborador") or "").strip() or "Colaborador"
                tipo = str((prev or {}).get("tipo") or "").strip() or "solicitud"
                subj = f"RRHH · Solicitud {tipo} · {colaborador} · {new_estado.upper()}"
                txt = (
                    f"Colaborador: {colaborador}\n"
                    f"Tipo: {tipo}\n"
                    f"Estado: {new_estado}\n"
                    + (f"Monto: {(prev or {}).get('monto')}\n" if (prev or {}).get("monto") else "")
                    + (f"Días: {(prev or {}).get('dias')}\n" if (prev or {}).get("dias") else "")
                    + (f"Fechas: {(prev or {}).get('fecha_inicio') or ''} → {(prev or {}).get('fecha_fin') or ''}\n" if ((prev or {}).get("fecha_inicio") or (prev or {}).get("fecha_fin")) else "")
                    + (f"Motivo/obs: {(body.get('motivo') or (prev or {}).get('motivo') or '')}\n")
                    + f"Solicitud ID: {id_solicitud}\n"
                )
                _notify_rrhh_admins(db, subject=subj, body=txt)

                # Correo al usuario solicitante (si tenemos email en usuarios/rrhh_staff)
                try:
                    uid = int((prev or {}).get("id_usuario") or 0)
                    email = None
                    if uid:
                        email = db.execute(
                            text("SELECT COALESCE(NULLIF(btrim(email),''), NULL) FROM public.usuarios WHERE id_usuario=:id LIMIT 1"),
                            {"id": uid},
                        ).scalar()
                    if not email:
                        st = db.execute(
                            text("SELECT COALESCE(NULLIF(btrim(email),''), NULL) FROM rrhh_staff WHERE is_active IS TRUE AND lower(colaborador)=lower(:c) ORDER BY id_staff DESC LIMIT 1"),
                            {"c": colaborador},
                        ).scalar()
                        email = st
                    if email:
                        from backend.core.email import send_email
                        send_email(str(email).strip(), subj, txt)
                except Exception:
                    pass

                # Alertas internas
                try:
                    from backend.core.system_notifs import push_system_notif
                    lid = int((prev or {}).get("id_usuario") or 0) or int(id_solicitud)
                    title = "RRHH · Solicitud actualizada"
                    body_txt = f"{colaborador} · {tipo} · {new_estado}"
                    payload = {"tipo": tipo, "colaborador": colaborador, "estado": new_estado, "id_solicitud": int(id_solicitud)}
                    push_system_notif(db, kind="RRHH_SOLICITUD_UPD", role_target="RRHH", id_lead=lid, title=title, body=body_txt, payload=payload)
                    push_system_notif(db, kind="RRHH_SOLICITUD_UPD", role_target="ADMIN", id_lead=lid, title=title, body=body_txt, payload=payload)
                    push_system_notif(db, kind="RRHH_SOLICITUD_UPD", role_target="SUPERADMIN", id_lead=lid, title=title, body=body_txt, payload=payload)
                    if str(tipo).strip().lower() == "adelanto":
                        push_system_notif(db, kind="RRHH_SOLICITUD_UPD", role_target="FINANZAS", id_lead=lid, title=title, body=body_txt, payload=payload)
                except Exception:
                    pass
        except Exception:
            pass
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
