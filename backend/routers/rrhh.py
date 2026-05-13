from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
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
import decimal

from backend.db import get_db
from backend.routers.auth import get_current_user
from backend.core import public_tokens

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


def _json_default(o: Any) -> Any:
    # Para snapshots/JSONB: evita crash por Decimal/dates.
    if isinstance(o, decimal.Decimal):
        try:
            return float(o)
        except Exception:
            return str(o)
    if isinstance(o, (datetime.date, datetime.datetime)):
        try:
            return o.isoformat()
        except Exception:
            return str(o)
    if isinstance(o, set):
        return list(o)
    return str(o)


def _json_dumps(o: Any) -> str:
    return json.dumps(o, ensure_ascii=False, default=_json_default)

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
              id_staff INTEGER,
              colaborador TEXT NOT NULL,
              fecha DATE NOT NULL,
              dias NUMERIC DEFAULT 1,
              motivo TEXT
            )
            """
        )
    )
    db.execute(text("ALTER TABLE rrhh_inasistencias ADD COLUMN IF NOT EXISTS id_staff INTEGER"))
    try:
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_rrhh_inasistencias_staff_fecha ON rrhh_inasistencias(id_staff, fecha)"))
    except Exception:
        pass
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
        # Propuesta de corrección (lo que RRHH sugiere aplicar si el colaborador aprueba)
        try:
            db.execute(text("ALTER TABLE rrhh_desvios ADD COLUMN IF NOT EXISTS proposed_in TEXT"))
            db.execute(text("ALTER TABLE rrhh_desvios ADD COLUMN IF NOT EXISTS proposed_out TEXT"))
        except Exception:
            pass
    except Exception:
        pass
    # Feriados configurables (Chile): usados por planificador mensual y reportes.
    try:
        db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS rrhh_feriados (
                  day DATE PRIMARY KEY,
                  name TEXT,
                  is_active BOOLEAN DEFAULT TRUE,
                  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_rrhh_feriados_active ON rrhh_feriados(is_active, day DESC)"))
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


@router.get("/feriados")
def list_feriados(
    year: int | None = Query(default=None, ge=2000, le=2100),
    db: Session = Depends(get_db),
    me: dict = Depends(get_current_user),
) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    where = ["is_active IS TRUE"]
    params: dict[str, Any] = {}
    if year:
        where.append("EXTRACT(YEAR FROM day)=:y")
        params["y"] = int(year)
    rows = db.execute(
        text(
            f"""
            SELECT day::text AS day, COALESCE(name,'') AS name
            FROM rrhh_feriados
            WHERE {' AND '.join(where)}
            ORDER BY day ASC
            """
        ),
        params,
    ).mappings().all()
    return {"ok": True, "items": [dict(r) for r in rows]}


@router.post("/feriados")
def upsert_feriado(body: dict, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    day = str(body.get("day") or "").strip()[:10]
    if not day:
        raise HTTPException(status_code=400, detail="day requerido (YYYY-MM-DD)")
    name = str(body.get("name") or "").strip()
    db.execute(
        text(
            """
            INSERT INTO rrhh_feriados(day,name,is_active,updated_at)
            VALUES (:d,:n,TRUE,now())
            ON CONFLICT (day) DO UPDATE
            SET name=EXCLUDED.name, is_active=TRUE, updated_at=now()
            """
        ),
        {"d": day, "n": name},
    )
    db.commit()
    return {"ok": True}


@router.delete("/feriados/{day}")
def delete_feriado(day: str, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    d = str(day or "").strip()[:10]
    if not d:
        raise HTTPException(status_code=400, detail="day inválido")
    db.execute(text("UPDATE rrhh_feriados SET is_active=FALSE, updated_at=now() WHERE day=:d"), {"d": d})
    db.commit()
    return {"ok": True}


@router.post("/horarios/plan_month")
def horarios_plan_month(body: dict, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    """
    Planifica un mes completo por día (drag&drop mensual).
    body:
      { id_staff:int, month_start:'YYYY-MM-01', days:{'YYYY-MM-DD': id_turno|null}, mode:'month' }
    Estrategia:
      - Inserta 1 fila por día (dow_mask=bit) con rango [week_start, week_end] por semana ISO.
      - Desactiva solo filas previas del mismo staff y mismo rango [week_start, week_end].
    """
    _ensure_tables(db)
    _require_rrhh_admin(me)
    try:
        id_staff = int(body.get("id_staff") or 0)
    except Exception:
        id_staff = 0
    if not id_staff:
        raise HTTPException(status_code=400, detail="id_staff requerido")
    ms = str(body.get("month_start") or body.get("start") or "").strip()[:10]
    if not ms:
        raise HTTPException(status_code=400, detail="month_start requerido (YYYY-MM-01)")
    try:
        d0 = datetime.date.fromisoformat(ms)
    except Exception:
        raise HTTPException(status_code=400, detail="month_start inválido")
    # Normalizar a primer día de mes
    d0 = datetime.date(d0.year, d0.month, 1)
    days = body.get("days") or {}
    if not isinstance(days, dict) or not days:
        raise HTTPException(status_code=400, detail="days requerido")

    # Validar turnos
    ids = sorted({int(v) for v in days.values() if v is not None and str(v).strip() != ""})
    if ids:
        ok_turnos = db.execute(
            text("SELECT id_turno FROM rrhh_turnos WHERE is_active IS TRUE AND id_turno = ANY(:ids)"),
            {"ids": ids},
        ).fetchall()
        ok_set = {int(r[0]) for r in ok_turnos}
        missing = [t for t in ids if t not in ok_set]
        if missing:
            raise HTTPException(status_code=400, detail=f"Turnos inválidos/inactivos: {missing}")

    # Agrupar por semana (lunes-domingo) usando ISO week start
    def week_start(day: datetime.date) -> datetime.date:
        return day - datetime.timedelta(days=day.weekday())

    # days_map: date -> (dow, turno)
    day_pairs: list[tuple[datetime.date, int, int]] = []
    for k, v in days.items():
        if v is None or str(v).strip() == "":
            continue
        try:
            dd = datetime.date.fromisoformat(str(k)[:10])
            tid = int(v)
            dow = int(dd.weekday())  # 0 Mon .. 6 Sun
            day_pairs.append((dd, dow, tid))
        except Exception:
            continue
    if not day_pairs:
        raise HTTPException(status_code=400, detail="days vacío: asigna al menos 1 día")

    # group by week_start
    by_week: dict[datetime.date, dict[int, int]] = {}
    for dd, dow, tid in day_pairs:
        ws = week_start(dd)
        by_week.setdefault(ws, {})[dow] = tid

    created = 0
    for ws, mapping in sorted(by_week.items(), key=lambda x: x[0]):
        we = ws + datetime.timedelta(days=6)
        # idempotente por semana: desactiva solo el rango exacto
        db.execute(
            text(
                """
                UPDATE rrhh_horarios
                SET is_active=FALSE
                WHERE is_active IS TRUE
                  AND id_staff=:s
                  AND desde=:ws
                  AND hasta=:we
                """
            ),
            {"s": int(id_staff), "ws": ws, "we": we},
        )
        for dow, tid in mapping.items():
            db.execute(
                text(
                    """
                    INSERT INTO rrhh_horarios(id_staff,id_turno,desde,hasta,dow_mask,is_active)
                    VALUES (:s,:t,:ws,:we,:m,TRUE)
                    """
                ),
                {"s": int(id_staff), "t": int(tid), "ws": ws, "we": we, "m": int(1 << int(dow))},
            )
            created += 1

    db.commit()
    return {"ok": True, "month_start": d0.isoformat(), "weeks": len(by_week), "created": created}


@router.get("/horarios/month")
def horarios_month(
    id_staff: int = Query(ge=1),
    month: str = Query(min_length=7, max_length=7),  # YYYY-MM
    db: Session = Depends(get_db),
    me: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Devuelve la planificación del mes como mapping día->id_turno.
    Expande rrhh_horarios por rango [desde,hasta] y dow_mask.
    """
    _ensure_tables(db)
    _require_rrhh_admin(me)
    ym = str(month or "").strip()
    try:
        y = int(ym.split("-")[0])
        m = int(ym.split("-")[1])
        if m < 1 or m > 12:
            raise ValueError()
        month_start = datetime.date(y, m, 1)
    except Exception:
        raise HTTPException(status_code=400, detail="month inválido (YYYY-MM)")
    # end exclusive
    month_end = (month_start.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)

    rows = db.execute(
        text(
            """
            SELECT id_turno, desde, hasta, dow_mask
            FROM rrhh_horarios
            WHERE is_active IS TRUE
              AND id_staff=:s
              AND desde <= :me
              AND COALESCE(hasta, :me) >= :ms
            """
        ),
        {"s": int(id_staff), "ms": month_start, "me": month_end - datetime.timedelta(days=1)},
    ).mappings().all()

    out: dict[str, int] = {}

    def _mask_days(mask: int | None) -> set[int]:
        if mask is None:
            return set(range(7))
        s = set()
        for k in range(7):
            if int(mask) & (1 << k):
                s.add(k)
        return s

    # Expand day by day inside month
    d = month_start
    while d < month_end:
        dow = int(d.weekday())  # 0 Mon..6 Sun
        for r in rows:
            tid = int(r.get("id_turno") or 0)
            if not tid:
                continue
            desde = r.get("desde")
            hasta = r.get("hasta")
            try:
                if desde and isinstance(desde, datetime.date) and d < desde:
                    continue
                if hasta and isinstance(hasta, datetime.date) and d > hasta:
                    continue
            except Exception:
                pass
            dm = r.get("dow_mask")
            if dow not in _mask_days(int(dm) if dm is not None else None):
                continue
            # last write wins (later rows can override)
            out[d.isoformat()] = tid
        d += datetime.timedelta(days=1)

    return {"ok": True, "id_staff": int(id_staff), "month": ym, "days": out}


@router.post("/horarios/clear_month")
def horarios_clear_month(body: dict, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    try:
        id_staff = int(body.get("id_staff") or 0)
    except Exception:
        id_staff = 0
    if not id_staff:
        raise HTTPException(status_code=400, detail="id_staff requerido")
    ym = str(body.get("month") or body.get("month_start") or "").strip()
    if not ym:
        raise HTTPException(status_code=400, detail="month requerido (YYYY-MM)")
    ym = ym[:7]
    try:
        y = int(ym.split("-")[0])
        m = int(ym.split("-")[1])
        if m < 1 or m > 12:
            raise ValueError()
        month_start = datetime.date(y, m, 1)
    except Exception:
        raise HTTPException(status_code=400, detail="month inválido (YYYY-MM)")
    month_end = (month_start.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
    # Desactiva horarios que intersecten el mes (solo los acotados por hasta, no plantillas indefinidas).
    db.execute(
        text(
            """
            UPDATE rrhh_horarios
            SET is_active=FALSE
            WHERE is_active IS TRUE
              AND id_staff=:s
              AND hasta IS NOT NULL
              AND desde < :me
              AND hasta >= :ms
            """
        ),
        {"s": int(id_staff), "ms": month_start, "me": month_end},
    )
    db.commit()
    return {"ok": True, "month": ym}


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
    id_usuario: int | None = None,
    limit: int = 100,
    offset: int = 0,
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
    if id_usuario is not None:
        try:
            where.append("id_usuario = :uid_filter")
            params["uid_filter"] = int(id_usuario)
        except Exception:
            pass
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
    lim = int(limit or 100)
    if lim < 1:
        lim = 1
    if lim > 500:
        lim = 500
    off = int(offset or 0)
    if off < 0:
        off = 0

    total = db.execute(
        text(
            f"""
            SELECT COUNT(1)
            FROM rrhh_desvios
            {where_sql}
            """
        ),
        params,
    ).scalar() or 0

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
            LIMIT :lim OFFSET :off
            """
        ),
        dict(params, lim=lim, off=off),
    ).mappings().all()
    return {"ok": True, "items": [dict(r) for r in rows], "total": int(total), "limit": lim, "offset": off}

@router.get("/desvios/report")
def report_desvios(
    estado: str | None = None,
    date: str | None = None,
    include_items: int = 1,
    db: Session = Depends(get_db),
    me: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Reporte diario de desvíos agrupado por colaborador, con impacto.
    Pensado para shared hosting: 1 query acotada por fecha.
    """
    _ensure_tables(db)
    if not _is_rrhh_admin(me):
        raise HTTPException(status_code=403, detail="No autorizado")

    try:
        if date:
            d = str(date)[:10]
        else:
            d0 = db.execute(text("SELECT (now() AT TIME ZONE 'America/Santiago')::date")).scalar()
            d = str(d0)[:10]
    except Exception:
        d = str(date or "")[:10] or ""

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

    where = ["fecha = :d"]
    params: dict[str, Any] = {"d": d}
    if estado:
        where.append("status = :st")
        params["st"] = str(estado).strip().lower()

    # scope por rol
    if (("SUPERADMIN" in role) or (role == "ADMIN") or ("FINAN" in role) or ("RRHH" in role) or ("RECURSOS" in role)):
        pass
    else:
        if jefe_staff_id:
            where.append("id_staff IN (SELECT id_staff FROM rrhh_staff WHERE jefe_id_staff=:j AND is_active IS TRUE)")
            params["j"] = int(jefe_staff_id)
        else:
            return {"ok": True, "date": d, "groups": [], "totals": {"desvios": 0, "colaboradores": 0}}

    rows = db.execute(
        text(
            f"""
            SELECT id_desvio, created_at, fecha, id_staff, id_usuario, rut, colaborador, rol, centro_costo,
                   tipo, status, decided_at, decided_by, note,
                   expected_in, expected_out, actual_in, actual_out, diff_in_min, diff_out_min,
                   impact_kind, impact_min
            FROM rrhh_desvios
            WHERE {' AND '.join(where)}
            ORDER BY colaborador ASC, id_desvio DESC
            """
        ),
        params,
    ).mappings().all()

    groups_map: dict[int, dict[str, Any]] = {}
    for r in rows:
        sid = int(r.get("id_staff") or 0) or 0
        if sid not in groups_map:
            groups_map[sid] = {
                "id_staff": sid,
                "colaborador": r.get("colaborador"),
                "rol": r.get("rol"),
                "centro_costo": r.get("centro_costo"),
                "counts": {"pendiente": 0, "aprobado": 0, "rechazado": 0, "otros": 0},
                "by_tipo": {},
                "impact": {"DESCUENTO_HORAS": 0, "HORAS_EXTRA": 0, "AUSENCIA": 0, "OTROS": 0},
                "impact_total_min": 0,
                "items": [],
            }
        g = groups_map[sid]
        st = str(r.get("status") or "").strip().lower()
        if st in ("pendiente", "aprobado", "rechazado"):
            g["counts"][st] = int(g["counts"].get(st) or 0) + 1
        else:
            g["counts"]["otros"] = int(g["counts"].get("otros") or 0) + 1

        tp = str(r.get("tipo") or "").strip().upper() or "OTRO"
        g["by_tipo"][tp] = int(g["by_tipo"].get(tp) or 0) + 1

        ik = str(r.get("impact_kind") or "").strip().upper()
        im = r.get("impact_min")
        try:
            im_i = int(im) if im is not None and str(im).strip() != "" else 0
        except Exception:
            im_i = 0
        if ik in ("DESCUENTO_HORAS", "HORAS_EXTRA", "AUSENCIA"):
            g["impact"][ik] = int(g["impact"].get(ik) or 0) + int(abs(im_i))
        else:
            g["impact"]["OTROS"] = int(g["impact"].get("OTROS") or 0) + int(abs(im_i))
        g["impact_total_min"] = int(g.get("impact_total_min") or 0) + int(abs(im_i))

        if int(include_items or 0) == 1:
            g["items"].append(dict(r))

    groups = list(groups_map.values())
    groups.sort(key=lambda x: (str(x.get("centro_costo") or ""), str(x.get("colaborador") or "")))

    totals = {
        "desvios": int(len(rows)),
        "colaboradores": int(len(groups)),
        "impact_total_min": int(sum(int(g.get("impact_total_min") or 0) for g in groups)),
        "impact_descuento_min": int(sum(int((g.get("impact") or {}).get("DESCUENTO_HORAS") or 0) for g in groups)),
        "impact_extra_min": int(sum(int((g.get("impact") or {}).get("HORAS_EXTRA") or 0) for g in groups)),
        "pendientes": int(sum(int((g.get("counts") or {}).get("pendiente") or 0) for g in groups)),
    }
    return {"ok": True, "date": d, "groups": groups, "totals": totals}

@router.post("/desvios/report_email")
def email_desvios_report(
    body: dict = Body(default_factory=dict),
    db: Session = Depends(get_db),
    me: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Envía por correo el reporte diario de desvíos (best-effort).
    Reemplaza cualquier necesidad de "push"/recordatorios automáticos.
    """
    _ensure_tables(db)
    _require_rrhh_admin(me)
    date = str(body.get("date") or "").strip() or None
    estado = str(body.get("estado") or "").strip() or None

    rep = report_desvios(estado=estado, date=date, include_items=1, db=db, me=me)
    if not rep.get("ok"):
        return rep

    d = str(rep.get("date") or "")
    totals = rep.get("totals") or {}
    groups = rep.get("groups") or []

    lines: list[str] = []
    lines.append(f"RRHH · Reporte de desvíos · {d}")
    lines.append("")
    lines.append(f"Colaboradores con desvíos: {totals.get('colaboradores', 0)}")
    lines.append(f"Desvíos total: {totals.get('desvios', 0)}")
    lines.append(f"Pendientes: {totals.get('pendientes', 0)}")
    lines.append(f"Impacto total (min): {totals.get('impact_total_min', 0)}")
    lines.append(f"Descuento (min): {totals.get('impact_descuento_min', 0)}")
    lines.append(f"Horas extra (min): {totals.get('impact_extra_min', 0)}")
    lines.append("")

    for g in groups:
        col = str(g.get("colaborador") or "")
        cc = str(g.get("centro_costo") or "")
        rol = str(g.get("rol") or "")
        counts = g.get("counts") or {}
        impact = g.get("impact") or {}
        lines.append(f"- {col} [{cc}] ({rol})")
        lines.append(f"  Pend:{counts.get('pendiente',0)} Apr:{counts.get('aprobado',0)} Rech:{counts.get('rechazado',0)}")
        lines.append(f"  Impacto: desc={impact.get('DESCUENTO_HORAS',0)}m extra={impact.get('HORAS_EXTRA',0)}m total={g.get('impact_total_min',0)}m")
        for it in (g.get("items") or []):
            try:
                tipo = str(it.get("tipo") or "")
                st = str(it.get("status") or "")
                ik = str(it.get("impact_kind") or "")
                im = it.get("impact_min")
                exp_in = it.get("expected_in") or ""
                exp_out = it.get("expected_out") or ""
                lines.append(f"    · {tipo} [{st}] {ik} {im}m (exp {exp_in}->{exp_out})")
            except Exception:
                continue
        lines.append("")

    subject = f"RRHH · Desvíos {d} · Pendientes {totals.get('pendientes',0)}"
    body_txt = "\n".join(lines).strip() + "\n"
    try:
        _notify_rrhh_admins(db, subject=subject, body=body_txt)
        return {"ok": True, "sent": True, "date": d, "to": "RRHH_NOTIFY_TO/CC + roles admin"}
    except Exception as e:
        return {"ok": True, "sent": False, "detail": str(e)}


def _staff_contact(db: Session, *, id_staff: int) -> dict[str, Any]:
    try:
        row = db.execute(
            text(
                """
                SELECT id_staff, id_usuario, colaborador,
                       COALESCE(NULLIF(btrim(email),''), NULL) AS email,
                       jefe_id_staff
                FROM rrhh_staff
                WHERE id_staff=:s
                LIMIT 1
                """
            ),
            {"s": int(id_staff)},
        ).mappings().first()
        return dict(row) if row else {}
    except Exception:
        return {}


def _user_email(db: Session, *, id_usuario: int | None) -> str | None:
    if not id_usuario:
        return None
    try:
        e = db.execute(
            text("SELECT COALESCE(NULLIF(btrim(email),''), NULL) FROM public.usuarios WHERE id_usuario=:u LIMIT 1"),
            {"u": int(id_usuario)},
        ).scalar()
        e = str(e or "").strip()
        return e or None
    except Exception:
        return None


def _notify_email(to_list: list[str], subject: str, body: str) -> None:
    try:
        from backend.core.email import send_email_group
        to_clean = [x.strip() for x in (to_list or []) if x and str(x).strip()]
        if not to_clean:
            return
        send_email_group(to_clean, subject, body)
    except Exception:
        return


def _apply_desvio_decision(
    db: Session,
    *,
    id_desvio: int,
    estado: str,
    note: str,
    decided_by: int | None,
    source: str,
    force: bool = False,
) -> dict[str, Any]:
    estado = str(estado or "").strip().lower()
    if estado not in ("aprobado", "rechazado"):
        raise HTTPException(status_code=400, detail="status inválido")
    note = str(note or "").strip()

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
    if str(row.get("status") or "").lower() in ("aprobado", "rechazado") and not force:
        return {"ok": True, "already": True}

    db.execute(
        text(
            """
            UPDATE rrhh_desvios
            SET status=:st, decided_at=now(), decided_by=:by, note=:n
            WHERE id_desvio=:id
            """
        ),
        {"id": int(id_desvio), "st": estado, "by": int(decided_by) if decided_by else None, "n": note or None},
    )

    tipo = str(row.get("tipo") or "").upper()
    impact_kind = str(row.get("impact_kind") or "").upper()
    impact_min = int(row.get("impact_min") or 0) if str(row.get("impact_min") or "").strip() != "" else 0
    id_staff = int(row.get("id_staff") or 0)
    fecha = str(row.get("fecha") or "")[:10]
    colaborador = str(row.get("colaborador") or "Colaborador")

    try:
        # AUTO_OUT: es una regularización automática (no implica ajuste de sueldo por sí misma).
        # La decisión sirve para auditoría y notificación; no crea rrhh_ajustes.
        if tipo == "AUTO_OUT":
            raise RuntimeError("SKIP_AJUSTES_AUTO_OUT")
        if estado == "rechazado":
            if tipo in ("MISSING_IN", "MISSING_OUT"):
                # Idempotente: evita duplicados si se aprieta 2 veces (o retry por red).
                db.execute(
                    text(
                        """
                        INSERT INTO rrhh_inasistencias(colaborador, fecha, dias, motivo)
                        SELECT :c, :f, 1, :m
                        WHERE NOT EXISTS (
                          SELECT 1 FROM rrhh_inasistencias WHERE colaborador=:c AND fecha=:f
                        )
                        """
                    ),
                    {"c": colaborador, "f": fecha, "m": f"Auto (desvío {tipo})"},
                )
                db.execute(
                    text(
                        """
                        INSERT INTO rrhh_ajustes(fecha, id_staff, id_desvio, kind, minutos, note)
                        SELECT :f,:s,:d,'AUSENCIA',0,:n
                        WHERE NOT EXISTS (
                          SELECT 1 FROM rrhh_ajustes WHERE id_desvio=:d AND kind='AUSENCIA'
                        )
                        """
                    ),
                    {"f": fecha, "s": id_staff, "d": int(id_desvio), "n": note or None},
                )
            else:
                mins = abs(int(impact_min or 0))
                db.execute(
                    text(
                        """
                        INSERT INTO rrhh_ajustes(fecha, id_staff, id_desvio, kind, minutos, note)
                        SELECT :f,:s,:d,'DESCUENTO_HORAS',:m,:n
                        WHERE NOT EXISTS (
                          SELECT 1 FROM rrhh_ajustes WHERE id_desvio=:d AND kind='DESCUENTO_HORAS'
                        )
                        """
                    ),
                    {"f": fecha, "s": id_staff, "d": int(id_desvio), "m": int(mins), "n": note or None},
                )
        else:
            if impact_kind == "HORAS_EXTRA" and tipo in ("EARLY_IN", "LATE_OUT"):
                mins = abs(int(impact_min or 0))
                db.execute(
                    text(
                        """
                        INSERT INTO rrhh_ajustes(fecha, id_staff, id_desvio, kind, minutos, note)
                        SELECT :f,:s,:d,'HORAS_EXTRA',:m,:n
                        WHERE NOT EXISTS (
                          SELECT 1 FROM rrhh_ajustes WHERE id_desvio=:d AND kind='HORAS_EXTRA'
                        )
                        """
                    ),
                    {"f": fecha, "s": id_staff, "d": int(id_desvio), "m": int(mins), "n": note or None},
                )
            else:
                db.execute(
                    text(
                        """
                        INSERT INTO rrhh_ajustes(fecha, id_staff, id_desvio, kind, minutos, note)
                        SELECT :f,:s,:d,'JUSTIFICADO',0,:n
                        WHERE NOT EXISTS (
                          SELECT 1 FROM rrhh_ajustes WHERE id_desvio=:d AND kind='JUSTIFICADO'
                        )
                        """
                    ),
                    {"f": fecha, "s": id_staff, "d": int(id_desvio), "n": note or None},
                )
    except Exception as _e:
        # SKIP_AJUSTES_AUTO_OUT: intencional
        _ = _e

    db.commit()

    # Notificar siempre (correo + alertas internas)
    try:
        subj = f"RRHH · Desvío {tipo} · {colaborador} · {estado.upper()}"
        txt = f"Fuente: {source}\nColaborador: {colaborador}\nFecha: {fecha}\nTipo: {tipo}\nEstado: {estado}\nObs: {note or ''}\n"
        _notify_rrhh_admins(db, subject=subj, body=txt)
    except Exception:
        pass

    # Si lo decide el colaborador, notificar a jefe directo + RRHH (correo claro).
    if str(source or "").upper().startswith("COLAB"):
        try:
            st_row = _staff_contact(db, id_staff=id_staff)
            jefe_id = st_row.get("jefe_id_staff")
            jefe_email = None
            if jefe_id:
                jefe = _staff_contact(db, id_staff=int(jefe_id))
                jefe_email = (jefe.get("email") or _user_email(db, id_usuario=jefe.get("id_usuario"))) if jefe else None
            col_email = (st_row.get("email") or _user_email(db, id_usuario=st_row.get("id_usuario"))) if st_row else None
            to = [x for x in [jefe_email, col_email] if x]
            if to:
                body2 = (
                    f"RRHH · Aprobación de desvío\n\n"
                    f"Colaborador: {colaborador}\n"
                    f"Fecha: {fecha}\n"
                    f"Tipo: {tipo}\n"
                    f"Decisión colaborador: {estado.upper()}\n"
                    f"Observación: {note or '(sin obs)'}\n"
                )
                _notify_email(to, f"RRHH · {colaborador} · {fecha} · {tipo} · {estado.upper()}", body2)
        except Exception:
            pass

    try:
        from backend.core.system_notifs import push_system_notif
        lid = int(row.get("id_usuario") or 0) or int(id_desvio)
        for role_target in ("RRHH", "FINANZAS", "ADMIN", "SUPERADMIN"):
            push_system_notif(db, kind="RRHH_DESVIO", role_target=role_target, id_lead=lid, title="RRHH · Desvío", body=f"{colaborador} · {tipo} · {estado}", payload={"id_desvio": int(id_desvio), "estado": estado, "tipo": tipo, "fecha": fecha})
    except Exception:
        pass
    return {"ok": True}


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
    note = str(body.get("note") or "").strip()
    uid = _user_id(me) or 0
    force = bool(body.get("force"))
    return _apply_desvio_decision(db, id_desvio=id_desvio, estado=estado, note=note, decided_by=int(uid) if uid else None, source="ADMIN", force=force)


@router.get("/desvios/me")
def my_desvios(
    status: str | None = "pendiente",
    limit: int = 80,
    db: Session = Depends(get_db),
    me: dict = Depends(get_current_user),
) -> dict[str, Any]:
    _ensure_tables(db)
    uid = _user_id(me) or 0
    if not uid:
        raise HTTPException(status_code=401, detail="No autorizado")
    st = str(status or "").strip().lower()
    where = ["id_usuario=:u"]
    params: dict[str, Any] = {"u": int(uid), "lim": max(1, min(200, int(limit)))}
    if st:
        where.append("status=:st")
        params["st"] = st
    rows = db.execute(
        text(
            f"""
            SELECT id_desvio, created_at, fecha, tipo, status,
                   expected_in, expected_out, actual_in, actual_out,
                   proposed_in, proposed_out,
                   impact_kind, impact_min, note
            FROM rrhh_desvios
            WHERE {' AND '.join(where)}
            ORDER BY fecha DESC, id_desvio DESC
            LIMIT :lim
            """
        ),
        params,
    ).mappings().all()
    return {"ok": True, "items": [dict(r) for r in rows]}


@router.post("/desvios/{id_desvio}/respond")
def respond_desvio(
    id_desvio: int,
    body: dict = Body(default_factory=dict),
    db: Session = Depends(get_db),
    me: dict = Depends(get_current_user),
) -> dict[str, Any]:
    _ensure_tables(db)
    uid = _user_id(me) or 0
    if not uid:
        raise HTTPException(status_code=401, detail="No autorizado")
    estado = str(body.get("status") or "").strip().lower()
    note = str(body.get("note") or "").strip()
    row_uid = db.execute(text("SELECT id_usuario FROM rrhh_desvios WHERE id_desvio=:id LIMIT 1"), {"id": int(id_desvio)}).scalar()
    if not row_uid:
        raise HTTPException(status_code=404, detail="Desvío no existe")
    if int(row_uid) != int(uid):
        raise HTTPException(status_code=403, detail="No autorizado")
    return _apply_desvio_decision(db, id_desvio=id_desvio, estado=estado, note=note, decided_by=int(uid), source="COLAB_PORTAL", force=False)


@router.post("/desvios/{id_desvio}/request_approval")
def request_desvio_approval(
    id_desvio: int,
    body: dict = Body(default_factory=dict),
    db: Session = Depends(get_db),
    me: dict = Depends(get_current_user),
) -> dict[str, Any]:
    _ensure_tables(db)
    if not _is_rrhh_admin(me):
        raise HTTPException(status_code=403, detail="No autorizado")
    row = db.execute(
        text(
            """
            SELECT id_desvio, fecha, id_staff, id_usuario, colaborador, tipo,
                   expected_in, expected_out, actual_in, actual_out,
                   proposed_in, proposed_out,
                   impact_kind, impact_min, status
            FROM rrhh_desvios
            WHERE id_desvio=:id
            LIMIT 1
            """
        ),
        {"id": int(id_desvio)},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Desvío no existe")
    if str(row.get("status") or "").lower() != "pendiente":
        return {"ok": True, "skipped": True, "detail": "No pendiente"}

    id_staff = int(row.get("id_staff") or 0)
    st_row = _staff_contact(db, id_staff=id_staff)
    col_email = (st_row.get("email") or _user_email(db, id_usuario=st_row.get("id_usuario"))) if st_row else None
    if not col_email:
        raise HTTPException(status_code=400, detail="Colaborador sin email")

    base = (os.getenv("APP_URL") or "").rstrip("/")
    tok_ok = public_tokens.sign({"k": "desvio", "id": int(id_desvio), "st": "aprobado"}, ttl_seconds=7 * 24 * 3600)
    tok_no = public_tokens.sign({"k": "desvio", "id": int(id_desvio), "st": "rechazado"}, ttl_seconds=7 * 24 * 3600)
    link_ok = f"{base}/crm/rrhh/desvios/action?t={tok_ok}" if base else f"/crm/rrhh/desvios/action?t={tok_ok}"
    link_no = f"{base}/crm/rrhh/desvios/action?t={tok_no}" if base else f"/crm/rrhh/desvios/action?t={tok_no}"
    portal = f"{base}/crm/web/views/rrhh_portal.html" if base else "/crm/web/views/rrhh_portal.html"

    # Guardar propuesta (si viene) antes de enviar correo
    prop_in = str(body.get("proposed_in") or "").strip() or None
    prop_out = str(body.get("proposed_out") or "").strip() or None
    try:
        if prop_in is not None or prop_out is not None:
            db.execute(
                text(
                    """
                    UPDATE rrhh_desvios
                    SET proposed_in=COALESCE(:pi, proposed_in),
                        proposed_out=COALESCE(:po, proposed_out)
                    WHERE id_desvio=:id
                    """
                ),
                {"id": int(id_desvio), "pi": prop_in, "po": prop_out},
            )
            db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass

    fecha = str(row.get("fecha") or "")[:10]
    tipo = str(row.get("tipo") or "")
    impact_kind = str(row.get("impact_kind") or "")
    impact_min = row.get("impact_min")
    exp = f"{row.get('expected_in') or '—'} → {row.get('expected_out') or '—'}"
    # propuesta final: prioridad body > ya guardado en BD > heurística simple desde actual/expected
    prop_in_final = prop_in or (str(row.get("proposed_in") or "").strip() or None)
    prop_out_final = prop_out or (str(row.get("proposed_out") or "").strip() or None)
    if not prop_in_final and row.get("actual_in"):
        try:
            prop_in_final = str(row.get("actual_in")).replace("T", " ")[:16]
        except Exception:
            prop_in_final = None
    if not prop_out_final and row.get("actual_out"):
        try:
            prop_out_final = str(row.get("actual_out")).replace("T", " ")[:16]
        except Exception:
            prop_out_final = None
    propuesta_txt = f"{prop_in_final or '—'} → {prop_out_final or '—'}"
    act_in = row.get("actual_in")
    act_out = row.get("actual_out")
    try:
        act_in = str(act_in).replace("T", " ")[:16] if act_in else None
    except Exception:
        act_in = None
    try:
        act_out = str(act_out).replace("T", " ")[:16] if act_out else None
    except Exception:
        act_out = None
    actual_txt = f"{act_in or '—'} → {act_out or '—'}"
    impact_txt = f"{impact_kind} {impact_min if impact_min is not None else ''}".strip()
    body_txt = (
        f"Hola {row.get('colaborador') or 'colaborador'},\n\n"
        f"RRHH detectó un posible desvío en tu marcación (hora servidor Chile / America/Santiago):\n\n"
        f"Fecha: {fecha}\n"
        f"Tipo de desvío: {tipo}\n\n"
        f"Resumen\n"
        f"- Esperado (turno): {exp}\n"
        f"- Marcación registrada: {actual_txt}\n"
        f"- Propuesta de corrección RRHH: {propuesta_txt}\n"
        f"- Impacto estimado: {impact_txt or '—'}\n\n"
        f"Acción requerida\n"
        f"1) APROBAR (si estás de acuerdo con la propuesta): {link_ok}\n"
        f"2) RECHAZAR (si NO estás de acuerdo): {link_no}\n\n"
        f"También puedes revisar y responder desde tu portal: {portal}\n"
    )
    _notify_email([col_email], f"RRHH · Revisión de desvío · {fecha} · {tipo}", body_txt)
    return {"ok": True, "sent": True, "to": col_email}


@router.get("/desvios/action", response_class=HTMLResponse)
def desvio_action(
    t: str,
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    _ensure_tables(db)
    ok, payload, err = public_tokens.verify(t)
    if not ok or not payload:
        return HTMLResponse(f"<h3>Link inválido</h3><p>{err}</p>", status_code=400)
    if str(payload.get("k") or "") != "desvio":
        return HTMLResponse("<h3>Link inválido</h3>", status_code=400)
    try:
        did = int(payload.get("id") or 0)
    except Exception:
        did = 0
    st = str(payload.get("st") or "").strip().lower()
    if not did or st not in ("aprobado", "rechazado"):
        return HTMLResponse("<h3>Link inválido</h3>", status_code=400)
    try:
        res = _apply_desvio_decision(db, id_desvio=did, estado=st, note="(confirmado por email)", decided_by=None, source="COLAB_EMAIL", force=False)
        if res.get("already"):
            return HTMLResponse("<h3>Listo</h3><p>Este desvío ya estaba decidido.</p>", status_code=200)
        return HTMLResponse("<h3>Listo</h3><p>Tu respuesta fue registrada.</p>", status_code=200)
    except Exception as e:
        return HTMLResponse(f"<h3>Error</h3><p>{str(e)}</p>", status_code=400)

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
def nomina_list(
    month: int | None = None,
    year: int | None = None,
    db: Session = Depends(get_db),
    me: dict = Depends(get_current_user),
) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    try:
        # Periodo (por defecto: mes actual). Permite revisar meses anteriores para nómina/ajustes.
        today = datetime.date.today()
        m = int(month) if month and int(month) >= 1 and int(month) <= 12 else int(today.month)
        y = int(year) if year and int(year) >= 2000 and int(year) <= 2100 else int(today.year)
        period_start = datetime.date(y, m, 1)
        if m == 12:
            period_end = datetime.date(y + 1, 1, 1)
        else:
            period_end = datetime.date(y, m + 1, 1)

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
                WHERE fecha >= :ps AND fecha < :pe
                GROUP BY colaborador
                """
            ),
            {"ps": period_start, "pe": period_end},
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

        # inasistencias del período
        faltas_staff_map = {}
        faltas_map = {}
        faltas_tokens_map = {}
        try:
            faltas_rows = db.execute(
                text(
                    """
                    SELECT id_staff, colaborador, COALESCE(SUM(dias),0) AS dias
                    FROM rrhh_inasistencias
                    WHERE fecha >= :from_d
                      AND fecha < :to_d
                    GROUP BY id_staff, colaborador
                    """
                )
            ,
                {"from_d": period_start, "to_d": period_end},
            ).fetchall()
            faltas_staff_map = {}
            faltas_map = {}
            faltas_tokens_map = {}
            for r in faltas_rows:
                id_staff = r[0]
                raw = str(r[1] or "")
                dias = float(r[2] or 0)
                if id_staff is not None:
                    try:
                        faltas_staff_map[int(id_staff)] = float(faltas_staff_map.get(int(id_staff), 0) or 0) + dias
                    except Exception:
                        pass
                k = _norm_person_key(raw)
                kt = _norm_person_tokens_key(raw)
                if k:
                    faltas_map[k] = float(faltas_map.get(k, 0) or 0) + dias
                if kt:
                    faltas_tokens_map[kt] = float(faltas_tokens_map.get(kt, 0) or 0) + dias
        except Exception:
            faltas_staff_map = {}
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
            faltas = 0.0
            try:
                if d.get("id_staff") is not None:
                    faltas = float(faltas_staff_map.get(int(d.get("id_staff")), 0) or 0)
            except Exception:
                faltas = 0.0
            if not faltas:
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
                "id_staff": d.get("id_staff"),
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
        return {"ok": True, "items": items, "period": {"year": y, "month": m, "from": str(period_start), "to": str(period_end)}}
    except Exception as e:
        db.rollback()
        return {"ok": False, "detail": str(e)}


def _ensure_nomina_snapshots(db: Session) -> None:
    try:
        db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS rrhh_nomina_snapshots (
                  id_snapshot SERIAL PRIMARY KEY,
                  period_yyyymm TEXT NOT NULL,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  created_by INTEGER,
                  payload JSONB NOT NULL DEFAULT '{}'::jsonb
                )
                """
            )
        )
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_rrhh_nomina_snapshots_period ON rrhh_nomina_snapshots(period_yyyymm)"))
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass


@router.post("/nomina/snapshot")
def nomina_snapshot(
    month: int | None = None,
    year: int | None = None,
    db: Session = Depends(get_db),
    me: dict = Depends(get_current_user),
) -> dict[str, Any]:
    _ensure_tables(db)
    _ensure_nomina_snapshots(db)
    _require_rrhh_admin(me)
    # Reusa cálculo existente (misma lógica), pero guarda snapshot.
    out = nomina_list(month=month, year=year, db=db, me=me)
    if not out or out.get("ok") is False:
        return out
    period = out.get("period") or {}
    y = int(period.get("year") or 0) or int(datetime.date.today().year)
    m = int(period.get("month") or 0) or int(datetime.date.today().month)
    yyyymm = f"{y:04d}{m:02d}"
    created_by = _user_id(me)
    try:
        row = db.execute(
            text(
                """
                INSERT INTO rrhh_nomina_snapshots(period_yyyymm, created_by, payload)
                VALUES (:p, :by, CAST(:pl AS JSONB))
                RETURNING id_snapshot
                """
            ),
            {"p": yyyymm, "by": created_by, "pl": _json_dumps(out)},
        ).fetchone()
        db.commit()
        sid = int(row[0]) if row and row[0] is not None else None
        return {"ok": True, "id_snapshot": sid, "period_yyyymm": yyyymm}
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        return {"ok": False, "detail": str(e)}


@router.get("/nomina/snapshots")
def nomina_snapshots_list(
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    me: dict = Depends(get_current_user),
) -> dict[str, Any]:
    _ensure_tables(db)
    _ensure_nomina_snapshots(db)
    _require_rrhh_admin(me)
    lim = max(1, min(int(limit or 50), 200))
    off = max(0, int(offset or 0))
    total = db.execute(text("SELECT COUNT(1) FROM rrhh_nomina_snapshots")).scalar() or 0
    rows = db.execute(
        text(
            """
            SELECT id_snapshot, period_yyyymm, created_at, created_by
            FROM rrhh_nomina_snapshots
            ORDER BY created_at DESC, id_snapshot DESC
            LIMIT :lim OFFSET :off
            """
        ),
        {"lim": lim, "off": off},
    ).mappings().all()
    return {"ok": True, "items": [dict(r) for r in rows], "total": int(total), "limit": lim, "offset": off}


@router.get("/nomina/snapshots/{id_snapshot}")
def nomina_snapshot_get(
    id_snapshot: int,
    db: Session = Depends(get_db),
    me: dict = Depends(get_current_user),
) -> dict[str, Any]:
    _ensure_tables(db)
    _ensure_nomina_snapshots(db)
    _require_rrhh_admin(me)
    row = db.execute(
        text(
            """
            SELECT id_snapshot, period_yyyymm, created_at, created_by, payload
            FROM rrhh_nomina_snapshots
            WHERE id_snapshot=:id
            LIMIT 1
            """
        ),
        {"id": int(id_snapshot)},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Snapshot no existe")
    return {"ok": True, "item": dict(row)}


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
    if str(os.getenv("CRM_RRHH_REMINDERS_ENABLED") or "").strip().lower() not in ("1", "true", "yes", "on"):
        raise HTTPException(status_code=403, detail="Reminders deshabilitados")
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
def solicitudes_me(
    include_all: int | None = 0,
    db: Session = Depends(get_db),
    me: dict = Depends(get_current_user),
) -> dict[str, Any]:
    _ensure_tables(db)
    uid = _user_id(me)
    rut = _user_rut(db, me)
    staff = _staff_for_user(db, me)
    colab = (staff or {}).get("colaborador") or me.get("name") or me.get("nombre") or me.get("username") or ""
    try:
        where = [
            "((id_usuario=:u) OR (:r <> '' AND lower(rut)=lower(:r)) OR (lower(colaborador)=lower(:c)))"
        ]
        if not include_all:
            where.append("created_at >= date_trunc('month', now()) AND created_at < (date_trunc('month', now()) + interval '1 month')")
        rows = db.execute(
            text(
                f"""
                SELECT id_solicitud, colaborador, tipo, doc_tipo, fecha_inicio, fecha_fin, dias, monto, motivo, estado, created_at
                FROM rrhh_solicitudes
                WHERE {' AND '.join(where)}
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


@router.post("/horarios/clear_week")
def horarios_clear_week(body: dict, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    """
    Limpia la planificación semanal (rango [week_start, week_start+6]) para un colaborador.
    No toca el "horario base" (hasta IS NULL).
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
    upd = db.execute(
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
    db.commit()
    return {"ok": True, "week_start": d0.isoformat(), "week_end": d6.isoformat(), "cleared": int(getattr(upd, "rowcount", 0) or 0)}


@router.post("/horarios/clear_template")
def horarios_clear_template(body: dict, db: Session = Depends(get_db), me: dict = Depends(get_current_user)) -> dict[str, Any]:
    """
    Limpia el horario base indefinido (hasta IS NULL) para un colaborador.
    """
    _ensure_tables(db)
    _require_rrhh_admin(me)
    try:
        id_staff = int(body.get("id_staff"))
    except Exception:
        raise HTTPException(status_code=400, detail="id_staff requerido")
    upd = db.execute(
        text(
            """
            UPDATE rrhh_horarios
            SET is_active=FALSE
            WHERE is_active IS TRUE
              AND id_staff=:s
              AND hasta IS NULL
            """
        ),
        {"s": id_staff},
    )
    db.commit()
    return {"ok": True, "cleared": int(getattr(upd, "rowcount", 0) or 0)}


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
            SELECT m.id_marcacion, m.created_at,
                   m.id_usuario,
                   COALESCE(NULLIF(btrim(s.colaborador),''), NULLIF(btrim(u.nombre),''), u.username, '') AS colaborador,
                   m.rut, m.tipo, m.method, m.ok, m.error, m.distance_m, m.within_radius, m.used_fallback
            FROM public.sgjo_marcaciones m
            LEFT JOIN public.usuarios u ON u.id_usuario = m.id_usuario
            LEFT JOIN LATERAL (
              SELECT colaborador
              FROM public.rrhh_staff
              WHERE id_usuario = m.id_usuario AND is_active IS TRUE
              ORDER BY id_staff DESC
              LIMIT 1
            ) s ON TRUE
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
        ficha = _json_dumps(ficha)
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

            ficha_json = _json_dumps(ficha)

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
        ficha = _json_dumps(ficha)
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
    id_staff = body.get("id_staff")
    colaborador = (body.get("colaborador") or "").strip()
    fecha = body.get("fecha")
    dias = body.get("dias") or 1
    motivo = (body.get("motivo") or "").strip()
    if not fecha:
        return {"ok": False, "detail": "fecha requerida"}
    if id_staff is not None:
        try:
            id_staff = int(id_staff)
        except Exception:
            return {"ok": False, "detail": "id_staff inválido"}
        staff = db.execute(
            text("SELECT colaborador FROM rrhh_staff WHERE id_staff=:id"),
            {"id": id_staff},
        ).fetchone()
        if staff and staff[0]:
            colaborador = str(staff[0]).strip()
    if not colaborador:
        return {"ok": False, "detail": "colaborador requerido"}
    # Si no viene id_staff, intentamos enlazarlo por nombre (evita que la nómina no descuente por diferencias de escritura).
    if id_staff is None and colaborador:
        try:
            # 1) match exacto case-insensitive
            sid = db.execute(
                text("SELECT id_staff FROM rrhh_staff WHERE lower(colaborador)=lower(:c) LIMIT 1"),
                {"c": colaborador},
            ).scalar()
            if sid is not None:
                id_staff = int(sid)
            else:
                # 2) match por normalización (tokens)
                target = _norm_person_tokens_key(colaborador) or _norm_person_key(colaborador)
                if target:
                    rows = db.execute(text("SELECT id_staff, colaborador FROM rrhh_staff")).fetchall()
                    for rr in rows:
                        nm = str(rr[1] or "").strip()
                        if not nm:
                            continue
                        k = _norm_person_tokens_key(nm) or _norm_person_key(nm)
                        if k and k == target:
                            id_staff = int(rr[0])
                            colaborador = nm  # canonical
                            break
        except Exception:
            id_staff = None
    db.execute(
        text(
            """
            INSERT INTO rrhh_inasistencias(id_staff, colaborador, fecha, dias, motivo)
            VALUES (:id_staff, :c, :f, :d, :m)
            """
        ),
        {"id_staff": id_staff, "c": colaborador, "f": fecha, "d": dias, "m": motivo},
    )
    db.commit()
    return {"ok": True}


@router.get("/inasistencias")
def inasistencia_list(
    colaborador: str | None = None,
    id_staff: int | None = None,
    db: Session = Depends(get_db),
    me: dict = Depends(get_current_user),
) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    try:
        if id_staff is not None:
            rows = db.execute(
                text(
                    """
                    SELECT id_inasistencia, id_staff, colaborador, fecha, dias, motivo
                    FROM rrhh_inasistencias
                    WHERE id_staff = :id_staff
                    ORDER BY fecha DESC, id_inasistencia DESC
                    """
                ),
                {"id_staff": int(id_staff)},
            ).mappings().all()
        elif colaborador:
            rows = db.execute(
                text(
                    """
                    SELECT id_inasistencia, id_staff, colaborador, fecha, dias, motivo
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
                    SELECT id_inasistencia, id_staff, colaborador, fecha, dias, motivo
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
    include_all: int | None = 0,
    db: Session = Depends(get_db),
    me: dict = Depends(get_current_user),
) -> dict[str, Any]:
    _ensure_tables(db)
    _require_rrhh_admin(me)
    try:
        where = []
        params: dict[str, Any] = {}
        if not include_all:
            where.append("created_at >= date_trunc('month', now()) AND created_at < (date_trunc('month', now()) + interval '1 month')")
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
