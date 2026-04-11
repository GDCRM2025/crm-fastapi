from __future__ import annotations

from datetime import datetime, timedelta, date, time
from typing import Any, Optional, Tuple, List, Dict
import os
from pathlib import Path
import json
from zoneinfo import ZoneInfo
from urllib.parse import urlencode, urlparse, parse_qs
import re
import unicodedata

from fastapi import APIRouter, Depends, Header, HTTPException, Body
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.db import get_db

try:
    from backend.routers.auth import get_current_user  # type: ignore
except Exception:
    def get_current_user():  # type: ignore
        return {"nombre": "admin", "role": "ADMIN", "marcas": []}

router = APIRouter(prefix="/tools", tags=["tools"])

# Google Calendar OAuth (opcional)
try:
    from google_auth_oauthlib.flow import Flow
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
except Exception:  # pragma: no cover
    Flow = None
    Credentials = None
    Request = None
    build = None

GCAL_SCOPES = ["https://www.googleapis.com/auth/calendar"]
GCAL_REDIRECT = "http://127.0.0.1:8000/tools/gcal/callback"
# Calendar default:
# - Prefer env var GCAL_DEFAULT_CAL (prod)
# - Else fallback hard-coded a Operaciones (evita agendar en el calendario equivocado)
GCAL_DEFAULT_CAL = "simonurrutia.m@gmail.com"


def _gcal_default_calendar_id(db: Session, override: str | None = None) -> str:
    """
    Determina a qué calendarId escribir/leer:
    1) override explícito
    2) env var GCAL_DEFAULT_CAL (config canonical en server)
    3) gcal_tokens.calendar_id (último token guardado) si NO es "primary"
    4) GCAL_DEFAULT_CAL (primary)
    """
    if override:
        v = str(override).strip()
        if v:
            return v
    env_v = (os.getenv("GCAL_DEFAULT_CAL") or "").strip()
    if env_v:
        return env_v
    try:
        row = db.execute(text("SELECT calendar_id FROM gcal_tokens ORDER BY id_token DESC LIMIT 1")).fetchone()
        v = (row[0] if row else None)
        v = str(v or "").strip()
        # Evitar que un token viejo con default 'primary' nos mande al calendario equivocado
        # (ej: el del ejecutivo que conectó OAuth).
        if v and v.lower() != "primary":
            return v
    except Exception:
        pass
    return (os.getenv("GCAL_DEFAULT_CAL") or GCAL_DEFAULT_CAL).strip() or "primary"


def _looks_like_perm_error(msg: str) -> bool:
    s = (msg or "").lower()
    return ("httperror 403" in s) or ("insufficientpermissions" in s) or ("forbidden" in s) or ("not have permission" in s)


def _looks_like_notfound_calendar(msg: str) -> bool:
    s = (msg or "").lower()
    return ("httperror 404" in s) or ("notfound" in s) or ("not found" in s)


def _gcal_client_file() -> str:
    env = os.getenv("GCAL_OAUTH_FILE")
    if env:
        return env
    base = Path(__file__).resolve().parents[2]
    return str(base / "keys" / "gcal_oauth.json")


def _gcal_redirect_uri() -> str:
    override = os.getenv("GCAL_REDIRECT_URI")
    if override:
        return override
    app = (os.getenv("APP_URL") or "").rstrip("/")
    if app:
        return f"{app}/crm/tools/gcal/callback"
    try:
        with open(_gcal_client_file(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if "installed" in data and data["installed"].get("redirect_uris"):
            return data["installed"]["redirect_uris"][0]
        if "web" in data and data["web"].get("redirect_uris"):
            return data["web"]["redirect_uris"][0]
    except Exception:
        pass
    return GCAL_REDIRECT


def _ensure_lead_calendar_cols(db: Session):
    try:
        db.execute(text("ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS calendar_event_id TEXT"))
        db.execute(text("ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS calendar_html_link TEXT"))
        db.execute(text("ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS calendar_start TIMESTAMP"))
        db.execute(text("ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS calendar_end TIMESTAMP"))
        db.execute(text("ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS pre_start TIMESTAMP"))
        db.execute(text("ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS pre_end TIMESTAMP"))
        db.execute(text("ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS pre_location TEXT"))
        db.execute(text("ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS pre_title TEXT"))
        db.execute(text("ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS pre_description TEXT"))
        db.execute(text("ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS pre_events_json TEXT"))
        db.execute(text("ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS calendar_event_ids_json TEXT"))
        db.execute(text("ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS calendar_html_links_json TEXT"))
        db.execute(text("ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS agenda_approved_by TEXT"))
        db.execute(text("ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS agenda_approved_at TIMESTAMP"))
        db.execute(text("ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS pendiente_agendar BOOLEAN DEFAULT FALSE"))
        db.commit()
    except Exception:
        db.rollback()

    for col in ("calendar_start", "calendar_end", "pre_start", "pre_end", "agenda_approved_at"):
        try:
            db.execute(
                text(
                    f"ALTER TABLE public.leads ALTER COLUMN {col} TYPE TIMESTAMP "
                    f"USING NULLIF({col}::text,'')::timestamp"
                )
            )
            db.commit()
        except Exception:
            db.rollback()


def _parse_event_time(raw: dict) -> Tuple[Optional[datetime], Optional[str]]:
    if not raw:
        return None, None
    if raw.get("dateTime"):
        dt = raw.get("dateTime")
        if dt.endswith("Z"):
            dt = dt.replace("Z", "+00:00")
        try:
            d = datetime.fromisoformat(dt)
            return d, d.date().isoformat()
        except Exception:
            return None, None
    if raw.get("date"):
        try:
            d = datetime.fromisoformat(raw.get("date") + "T00:00:00")
            return d, raw.get("date")
        except Exception:
            return None, None
    return None, None


def _extract_gcal_event_id_from_link(link: str) -> tuple[str | None, str | None]:
    """
    Intenta extraer (eventId, calendarId) desde un htmlLink de Google Calendar.

    Casos comunes:
    - https://www.google.com/calendar/event?eid=<base64url>
      donde el decode suele ser: "<eventId> <calendarId>"
    - Algunas variantes incluyen eventId directo en query.
    """
    try:
        s = (link or "").strip()
        if not s:
            return None, None
        u = urlparse(s)
        q = parse_qs(u.query or "")
        # 1) eventId explícito (si apareciera)
        for k in ("eventId", "eventid", "eidEventId"):
            if k in q and q[k]:
                return str(q[k][0]), None
        # 2) eid base64url (más común)
        eid = (q.get("eid") or [None])[0]
        if not eid:
            return None, None
        eid = str(eid)
        # base64url decode con padding
        pad = "=" * ((4 - (len(eid) % 4)) % 4)
        raw = base64.urlsafe_b64decode((eid + pad).encode("utf-8"))
        decoded = raw.decode("utf-8", errors="ignore").strip()
        if not decoded:
            return None, None
        parts = decoded.split()
        if not parts:
            return None, None
        event_id = parts[0].strip() or None
        cal_id = parts[1].strip() if len(parts) >= 2 else None
        return event_id, cal_id
    except Exception:
        return None, None


def _infer_marca_from_text(db: Session, text_in: str) -> Tuple[int, str]:
    txt = (text_in or "").upper()
    rows = db.execute(text("SELECT id_marca, COALESCE(nombre,marca) AS nombre FROM marcas ORDER BY 1")).mappings().all()
    for r in rows:
        nombre = (r.get("nombre") or "").upper()
        if nombre and nombre in txt:
            return int(r.get("id_marca")), r.get("nombre")
    return 0, ""


def _table_exists_pg(db: Session, table: str) -> bool:
    row = db.execute(
        text(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema='public' AND table_name=:t
            """
        ),
        {"t": table},
    ).first()
    return bool(row)


def _cols_pg(db: Session, table: str) -> set[str]:
    rows = db.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name=:t
            """
        ),
        {"t": table},
    ).fetchall()
    return {r[0] for r in rows}


def _ensure_gcal_tables(db: Session):
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS gcal_tokens (
                id_token SERIAL PRIMARY KEY,
                creds_json TEXT,
                calendar_id TEXT DEFAULT 'primary',
                updated_at TIMESTAMP DEFAULT now()
            )
            """
        )
    )
    db.commit()


def _load_gcal_creds(db: Session) -> Optional[Any]:
    if Credentials is None:
        return None
    _ensure_gcal_tables(db)
    row = db.execute(text("SELECT id_token, creds_json FROM gcal_tokens ORDER BY id_token DESC LIMIT 1")).fetchone()
    if not row or not row[1]:
        return None
    try:
        data = json.loads(row[1])
        creds = Credentials.from_authorized_user_info(data, GCAL_SCOPES)
        if creds and creds.expired and creds.refresh_token and Request:
            creds.refresh(Request())
            _save_gcal_creds(db, creds)
        return creds
    except Exception:
        return None


def _save_gcal_creds(db: Session, creds: Any):
    _ensure_gcal_tables(db)
    data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }
    db.execute(
        text("INSERT INTO gcal_tokens(creds_json, updated_at) VALUES (:c, now())"),
        {"c": json.dumps(data)},
    )
    db.commit()


def _gcal_service(db: Session):
    creds = _load_gcal_creds(db)
    if not creds or not build:
        return None
    return build("calendar", "v3", credentials=creds)


BASELINE_SALES = [
    {"mes": 1, "marca": "CAMALEON", "monto": 13338700, "empresa": 6, "particular": 22},
    {"mes": 1, "marca": "EXPRESS", "monto": 10568500, "empresa": 9, "particular": 21},
    {"mes": 1, "marca": "GOURMET", "monto": 14966420, "empresa": 10, "particular": 16},

    {"mes": 2, "marca": "CAMALEON", "monto": 4825000, "empresa": 3, "particular": 10},
    {"mes": 2, "marca": "EXPRESS", "monto": 6984500, "empresa": 12, "particular": 19},
    {"mes": 2, "marca": "GOURMET", "monto": 11899400, "empresa": 6, "particular": 14},

    {"mes": 3, "marca": "CAMALEON", "monto": 21472900, "empresa": 14, "particular": 28},
    {"mes": 3, "marca": "GOURMET", "monto": 11030050, "empresa": 16, "particular": 13},
    {"mes": 3, "marca": "EXPRESS", "monto": 20213540, "empresa": 5, "particular": 30},

    {"mes": 4, "marca": "CAMALEON", "monto": 34485115, "empresa": 25, "particular": 57},
    {"mes": 4, "marca": "GOURMET", "monto": 19835650, "empresa": 14, "particular": 8},
    {"mes": 4, "marca": "EXPRESS", "monto": 3565200, "empresa": 1, "particular": 1},

    {"mes": 5, "marca": "CAMALEON", "monto": 22746200, "empresa": 17, "particular": 28},
    {"mes": 5, "marca": "GOURMET", "monto": 16186700, "empresa": 15, "particular": 13},
    {"mes": 5, "marca": "EXPRESS", "monto": 7025300, "empresa": 8, "particular": 13},
    {"mes": 5, "marca": "DEL SABOR", "monto": 812825, "empresa": 0, "particular": 5},

    {"mes": 6, "marca": "CAMALEON", "monto": 15494528, "empresa": 13, "particular": 28},
    {"mes": 6, "marca": "GOURMET", "monto": 8878000, "empresa": 13, "particular": 11},
    {"mes": 6, "marca": "EXPRESS", "monto": 10929065, "empresa": 8, "particular": 21},
    {"mes": 6, "marca": "DEL SABOR", "monto": 4113671, "empresa": 0, "particular": 13},

    {"mes": 7, "marca": "CAMALEON", "monto": 14427530, "empresa": 14, "particular": 19},
    {"mes": 7, "marca": "GOURMET", "monto": 13048218, "empresa": 21, "particular": 6},
    {"mes": 7, "marca": "EXPRESS", "monto": 9053500, "empresa": 5, "particular": 5},
    {"mes": 7, "marca": "DEL SABOR", "monto": 1163520, "empresa": 1, "particular": 9},

    {"mes": 8, "marca": "CAMALEON", "monto": 25372576, "empresa": 30, "particular": 20},
    {"mes": 8, "marca": "DEL SABOR", "monto": 7527000, "empresa": 6, "particular": 13},
    {"mes": 8, "marca": "GOURMET", "monto": 7689600, "empresa": 10, "particular": 17},
    {"mes": 8, "marca": "EXPRESS", "monto": 17617600, "empresa": 12, "particular": 25},

    {"mes": 9, "marca": "CAMALEON", "monto": 30431825, "empresa": 14, "particular": 20},
    {"mes": 9, "marca": "DEL SABOR", "monto": 12685894, "empresa": 6, "particular": 8},
    {"mes": 9, "marca": "GOURMET", "monto": 23295700, "empresa": 19, "particular": 10},
    {"mes": 9, "marca": "EXPRESS", "monto": 16342500, "empresa": 18, "particular": 14},

    {"mes": 10, "marca": "CAMALEON", "monto": 29680950, "empresa": 18, "particular": 35},
    {"mes": 10, "marca": "DEL SABOR", "monto": 9640100, "empresa": 5, "particular": 16},
    {"mes": 10, "marca": "GOURMET", "monto": 13687600, "empresa": 10, "particular": 18},
    {"mes": 10, "marca": "EXPRESS", "monto": 16903420, "empresa": 19, "particular": 19},

    {"mes": 11, "marca": "CAMALEON", "monto": 40440299, "empresa": 20, "particular": 48},
    {"mes": 11, "marca": "DEL SABOR", "monto": 14112500, "empresa": 10, "particular": 35},
    {"mes": 11, "marca": "GOURMET", "monto": 25176900, "empresa": 12, "particular": 12},
    {"mes": 11, "marca": "EXPRESS", "monto": 13565600, "empresa": 11, "particular": 21},

    {"mes": 12, "marca": "CAMALEON", "monto": 60900692, "empresa": 34, "particular": 31},
    {"mes": 12, "marca": "DEL SABOR", "monto": 19366400, "empresa": 16, "particular": 27},
    {"mes": 12, "marca": "GOURMET", "monto": 39578050, "empresa": 33, "particular": 26},
    {"mes": 12, "marca": "EXPRESS", "monto": 59339050, "empresa": 16, "particular": 27},
]


def _gcal_link(title: str, start: datetime, end: datetime, details: str = "", location: str = "") -> str:
    def fmt(dt: datetime) -> str:
        return dt.strftime("%Y%m%dT%H%M%S")

    params = {
        "action": "TEMPLATE",
        "text": title or "Evento",
        "dates": f"{fmt(start)}/{fmt(end)}",
        "details": details or "",
        "location": location or "",
    }
    return "https://www.google.com/calendar/render?" + urlencode(params)


def _col_exists(db: Session, table: str, col: str) -> bool:
    r = db.execute(
        text(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='public' AND table_name=:t AND column_name=:c
            """
        ),
        {"t": table, "c": col},
    ).first()
    return bool(r)


def _estado_id(db: Session, name_like: str) -> int | None:
    r = db.execute(
        text("SELECT id_estado FROM estados_lead WHERE UPPER(nombre) LIKE :n LIMIT 1"),
        {"n": f"%{name_like.upper()}%"},
    ).fetchone()
    return int(r[0]) if r else None


def _is_admin(role: str) -> bool:
    return role in ("ADMIN", "SUPERADMIN", "JEFE DE OPERACIONES", "OPERACIONES")


def _resolve_uid_for_marcas(db: Session, me: dict) -> int | None:
    """
    Compat: algunos tokens traen `id` como username (string) y no como int.
    Si podemos mapear a public.usuarios.id_usuario, devolvemos int; si no, None.
    """
    raw = me.get("id")
    if str(raw or "").isdigit():
        return int(raw)
    if not _table_exists_pg(db, "usuarios"):
        return None
    cand = [
        str(me.get("username") or "").strip(),
        str(me.get("email") or "").strip(),
        str(me.get("sub") or "").strip(),
        str(me.get("id") or "").strip(),
        str(me.get("name") or "").strip(),
    ]
    cand = [c for c in cand if c]
    if not cand:
        return None
    try:
        for c in cand:
            v = db.execute(
                text(
                    """
                    SELECT id_usuario
                    FROM public.usuarios
                    WHERE email=:u OR username=:u
                    ORDER BY id_usuario
                    LIMIT 1
                    """
                ),
                {"u": c},
            ).scalar()
            if v is not None and str(v).isdigit():
                return int(v)
    except Exception:
        return None
    return None


def _fetch_marcas_ids(db: Session, me: dict) -> list[int]:
    """
    Preferir `me.marcas` del token.
    Fallback: usuarios_marcas (N:N) o usuarios.id_marca (legacy).
    """
    marcas = [int(x) for x in (me.get("marcas") or []) if str(x).isdigit()]
    if marcas:
        return marcas
    uid = _resolve_uid_for_marcas(db, me)
    if uid is None:
        return []
    # N:N
    join_table = None
    if _table_exists_pg(db, "usuarios_marcas"):
        join_table = "usuarios_marcas"
    elif _table_exists_pg(db, "usuario_marcas"):
        # legacy/ORM table name
        join_table = "usuario_marcas"

    if join_table:
        try:
            rows = db.execute(
                text(f"SELECT id_marca FROM public.{join_table} WHERE id_usuario=:u ORDER BY id_marca"),
                {"u": int(uid)},
            ).fetchall()
            out: list[int] = []
            for r in rows:
                try:
                    out.append(int(r[0]))
                except Exception:
                    pass
            if out:
                return out
        except Exception:
            pass
    # legacy single brand
    if _table_exists_pg(db, "usuarios") and _col_exists(db, "usuarios", "id_marca"):
        try:
            mid = db.execute(
                text("SELECT id_marca FROM public.usuarios WHERE id_usuario=:u LIMIT 1"),
                {"u": int(uid)},
            ).scalar()
            if mid is not None and str(mid).isdigit() and int(mid) > 0:
                return [int(mid)]
        except Exception:
            pass
    return []


def _lead_name_expr(db: Session) -> str:
    has_nombre = _col_exists(db, "leads", "nombre_cliente")
    has_cliente = _col_exists(db, "leads", "cliente")
    if has_nombre and has_cliente:
        return "COALESCE(l.nombre_cliente, l.cliente)"
    if has_nombre:
        return "l.nombre_cliente"
    if has_cliente:
        return "l.cliente"
    return "''"


def _lead_col(db: Session, col: str) -> str:
    return f"l.{col}" if _col_exists(db, "leads", col) else "NULL"


def _ensure_baseline(db: Session) -> None:
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS ventas_baseline (
                id_baseline SERIAL PRIMARY KEY,
                mes INT NOT NULL,
                marca TEXT NOT NULL,
                monto NUMERIC(14,2) NOT NULL DEFAULT 0,
                empresa INT NOT NULL DEFAULT 0,
                particular INT NOT NULL DEFAULT 0,
                UNIQUE (mes, marca)
            )
            """
        )
    )
    existing = db.execute(text("SELECT COUNT(*) FROM ventas_baseline")).scalar_one()
    if existing:
        return
    for row in BASELINE_SALES:
        db.execute(
            text(
                """
                INSERT INTO ventas_baseline(mes, marca, monto, empresa, particular)
                VALUES (:mes, :marca, :monto, :empresa, :particular)
                ON CONFLICT (mes, marca) DO NOTHING
                """
            ),
            row,
        )
    db.commit()


def _ensure_metas(db: Session) -> None:
    """
    Metas mensuales por marca (admin-only).
    - venta_base: base para la meta del mes (ej: ventas del mismo mes año anterior)
    - crecimiento_pct: % crecimiento (default 12)
    - meta: venta_base * (1 + crecimiento_pct/100)
    """
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS metas_marca_mensual (
              id_meta bigserial PRIMARY KEY,
              year integer NOT NULL,
              month integer NOT NULL,
              id_marca integer NOT NULL,
              venta_base numeric(16,2) NOT NULL DEFAULT 0,
              crecimiento_pct numeric(8,2) NOT NULL DEFAULT 12,
              meta numeric(16,2) NOT NULL DEFAULT 0,
              updated_at timestamp without time zone NOT NULL DEFAULT now(),
              updated_by text,
              UNIQUE (year, month, id_marca)
            )
            """
        )
    )
    db.commit()


def _is_admin_strict(role: str) -> bool:
    return role in ("ADMIN", "SUPERADMIN")


@router.get("/metas")
def metas_get(
    year: int | None = None,
    month: int | None = None,
    db: Session = Depends(get_db),
    me=Depends(get_current_user),
):
    role = (me.get("role") or me.get("rol") or "").upper()
    if not _is_admin_strict(role):
        raise HTTPException(status_code=403, detail="Solo admin puede ver metas")
    _ensure_metas(db)

    y = int(year or date.today().year)
    m = int(month or date.today().month)
    if m < 1 or m > 12:
        raise HTTPException(status_code=400, detail="month inválido (1-12)")
    rows = db.execute(
        text(
            """
            SELECT m.year, m.month, m.id_marca, COALESCE(ma.nombre, ma.marca,'') AS marca,
                   COALESCE(m.venta_base,0)::float AS venta_base,
                   COALESCE(m.crecimiento_pct,12)::float AS crecimiento_pct,
                   COALESCE(m.meta,0)::float AS meta,
                   COALESCE(m.updated_by,'') AS updated_by,
                   m.updated_at
            FROM metas_marca_mensual m
            LEFT JOIN marcas ma ON ma.id_marca=m.id_marca
            WHERE m.year=:y AND m.month=:m
            ORDER BY COALESCE(ma.nombre, ma.marca,'') ASC
            """
        ),
        {"y": y, "m": m},
    ).mappings().all()
    return {"ok": True, "year": y, "month": m, "items": list(rows)}


@router.put("/metas")
def metas_upsert(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    me=Depends(get_current_user),
):
    role = (me.get("role") or me.get("rol") or "").upper()
    if not _is_admin_strict(role):
        raise HTTPException(status_code=403, detail="Solo admin puede editar metas")
    _ensure_metas(db)

    try:
        y = int(payload.get("year") or date.today().year)
        m = int(payload.get("month") or date.today().month)
        if m < 1 or m > 12:
            raise ValueError("month inválido (1-12)")
        id_marca = int(payload.get("id_marca") or 0)
        if id_marca <= 0:
            raise ValueError("id_marca inválido")
        venta = float(
            payload.get("venta_base")
            or payload.get("venta_mes_pasado")
            or payload.get("venta_anio_pasado")
            or 0
        )
        crec = float(payload.get("crecimiento_pct") or 12)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Payload inválido: {e}")

    meta = round(venta * (1.0 + (crec / 100.0)), 2)
    by = (me.get("username") or me.get("id") or me.get("nombre") or "admin")

    db.execute(
        text(
            """
            INSERT INTO metas_marca_mensual(year, month, id_marca, venta_base, crecimiento_pct, meta, updated_by, updated_at)
            VALUES (:y, :m, :id_marca, :venta, :crec, :meta, :by, now())
            ON CONFLICT (year, month, id_marca) DO UPDATE
              SET venta_base=EXCLUDED.venta_base,
                  crecimiento_pct=EXCLUDED.crecimiento_pct,
                  meta=EXCLUDED.meta,
                  updated_by=EXCLUDED.updated_by,
                  updated_at=now()
            """
        ),
        {"y": y, "m": m, "id_marca": id_marca, "venta": venta, "crec": crec, "meta": meta, "by": by},
    )
    db.commit()
    return {"ok": True, "year": y, "month": m, "id_marca": id_marca, "meta": meta}


@router.post("/metas/init")
def metas_init(
    year: int | None = None,
    month: int | None = None,
    db: Session = Depends(get_db),
    me=Depends(get_current_user),
):
    role = (me.get("role") or me.get("rol") or "").upper()
    if not _is_admin_strict(role):
        raise HTTPException(status_code=403, detail="Solo admin puede inicializar metas")
    _ensure_metas(db)
    y = int(year or date.today().year)
    m = int(month or date.today().month)
    if m < 1 or m > 12:
        raise HTTPException(status_code=400, detail="month inválido (1-12)")
    by = (me.get("username") or me.get("id") or me.get("nombre") or "admin")

    marcas = db.execute(text("SELECT id_marca FROM marcas ORDER BY id_marca ASC")).fetchall()
    created = 0
    for r in marcas:
        mid = int(r[0])
        db.execute(
            text(
                """
                INSERT INTO metas_marca_mensual(year, month, id_marca, venta_base, crecimiento_pct, meta, updated_by, updated_at)
                VALUES (:y, :m, :id_marca, 0, 12, 0, :by, now())
                ON CONFLICT (year, month, id_marca) DO NOTHING
                """
            ),
            {"y": y, "m": m, "id_marca": mid, "by": by},
        )
        created += 1
    db.commit()
    return {"ok": True, "year": y, "month": m, "created": created}

@router.get("/gcal/status")
def gcal_status(db: Session = Depends(get_db)):
    creds = _load_gcal_creds(db)
    return {"ok": True, "connected": bool(creds)}


@router.get("/gcal/calendars")
def gcal_calendars(db: Session = Depends(get_db), me=Depends(get_current_user)):
    if build is None:
        raise HTTPException(status_code=500, detail="Google API no disponible")
    svc = _gcal_service(db)
    if not svc:
        raise HTTPException(status_code=400, detail="Google Calendar no conectado")
    try:
        items = svc.calendarList().list(maxResults=250).execute().get("items") or []
        out = []
        for it in items:
            out.append({
                "id": it.get("id"),
                "summary": it.get("summary"),
                "primary": bool(it.get("primary", False)),
            })
        return {"ok": True, "items": out}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"No se pudo listar calendarios: {e}")


@router.get("/gcal/stats")
def gcal_stats(
    days: int = 14,
    calendar_id: str | None = None,
    db: Session = Depends(get_db),
):
    if build is None:
        raise HTTPException(status_code=500, detail="Google API no disponible")
    svc = _gcal_service(db)
    if not svc:
        raise HTTPException(status_code=400, detail="Google Calendar no conectado")

    tz = ZoneInfo("America/Santiago")
    now = datetime.now(tz)
    time_min = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    time_max = (now + timedelta(days=days)).replace(hour=23, minute=59, second=59, microsecond=0).isoformat()

    cal_id = _gcal_default_calendar_id(db, calendar_id)
    evs = svc.events().list(
        calendarId=cal_id,
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime",
        maxResults=2500,
    ).execute()
    events = evs.get("items") or []

    counts: dict[str, int] = {}
    filtered = []
    for ev in events:
        title = (ev.get("summary") or "").strip()
        if re.search(r"\b(vacaciones|cumplea(?:n|ñ)os|feriado|holiday)\b", title.lower()):
            continue
        _, start_date = _parse_event_time(ev.get("start") or {})
        if not start_date:
            continue
        counts[start_date] = counts.get(start_date, 0) + 1
        filtered.append({"date": start_date, "title": title, "id": ev.get("id")})

    return {"ok": True, "counts": counts, "events": filtered}


@router.get("/gcal/events")
def gcal_events(
    from_date: str | None = None,
    to_date: str | None = None,
    calendar_id: str | None = None,
    limit: int = 2500,
    db: Session = Depends(get_db),
    me=Depends(get_current_user),
):
    if build is None:
        raise HTTPException(status_code=500, detail="Google API no disponible")
    svc = _gcal_service(db)
    if not svc:
        raise HTTPException(status_code=400, detail="Google Calendar no conectado")

    tz = ZoneInfo("America/Santiago")
    now = datetime.now(tz)
    if from_date:
        time_min = datetime.fromisoformat(from_date + "T00:00:00").replace(tzinfo=tz).isoformat()
    else:
        time_min = (now - timedelta(days=45)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    if to_date:
        time_max = datetime.fromisoformat(to_date + "T23:59:59").replace(tzinfo=tz).isoformat()
    else:
        time_max = (now + timedelta(days=45)).replace(hour=23, minute=59, second=59, microsecond=0).isoformat()

    cal_id = _gcal_default_calendar_id(db, calendar_id)
    try:
        evs = svc.events().list(
            calendarId=cal_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=max(1, min(5000, int(limit))),
        ).execute()
        events = evs.get("items") or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"No se pudo leer calendar: {e}")

    out = []
    for ev in events:
        eid = ev.get("id")
        if not eid:
            continue
        summary = (ev.get("summary") or "").strip()
        start = ev.get("start") or {}
        end = ev.get("end") or {}

        all_day = bool(start.get("date")) and not bool(start.get("dateTime"))
        if all_day:
            start_s = start.get("date")
            end_s = end.get("date") or start_s
        else:
            start_s = start.get("dateTime") or ""
            end_s = end.get("dateTime") or ""
        out.append(
            {
                "id": eid,
                "title": summary,
                "start": start_s,
                "end": end_s,
                "all_day": all_day,
                "location": ev.get("location") or "",
                "description": ev.get("description") or "",
                "html_link": ev.get("htmlLink") or "",
            }
        )

    return {"ok": True, "calendar_id": cal_id, "items": out}


@router.get("/gcal/start")
def gcal_start(db: Session = Depends(get_db), me=Depends(get_current_user)):
    if Flow is None:
        raise HTTPException(status_code=500, detail="Google OAuth no disponible")
    role = (me.get("role") or me.get("rol") or "").upper()
    if role != "SUPERADMIN":
        raise HTTPException(status_code=403, detail="Solo SUPERADMIN puede conectar Google Calendar")
    client_file = _gcal_client_file()
    if not os.path.exists(client_file):
        raise HTTPException(status_code=400, detail="Archivo OAuth no encontrado")
    flow = Flow.from_client_secrets_file(
        client_file, scopes=GCAL_SCOPES, redirect_uri=_gcal_redirect_uri()
    )
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    _ensure_gcal_tables(db)
    db.execute(text("DELETE FROM gcal_tokens WHERE creds_json IS NULL"))
    db.execute(text("INSERT INTO gcal_tokens(creds_json, updated_at) VALUES (:s, now())"), {"s": json.dumps({"state": state})})
    db.commit()
    return {"ok": True, "auth_url": auth_url}


def _consume_gcal_state(db: Session, state: str) -> bool:
    """
    Valida que el callback sea consecuencia de un /gcal/start reciente (SUPERADMIN).
    Guardamos el state en gcal_tokens.creds_json como {"state": "..."}.
    """
    _ensure_gcal_tables(db)
    try:
        rows = db.execute(
            text("SELECT id_token, creds_json FROM gcal_tokens ORDER BY id_token DESC LIMIT 25")
        ).fetchall()
    except Exception:
        return False
    ok_id = None
    for rid, raw in rows:
        try:
            data = json.loads(raw or "")
            if isinstance(data, dict) and str(data.get("state") or "") == str(state or ""):
                ok_id = int(rid)
                break
        except Exception:
            continue
    if not ok_id:
        return False
    try:
        db.execute(text("DELETE FROM gcal_tokens WHERE id_token=:id"), {"id": ok_id})
        db.commit()
    except Exception:
        pass
    return True


@router.get("/gcal/callback")
def gcal_callback(code: str, state: str | None = None, db: Session = Depends(get_db)):
    if Flow is None:
        raise HTTPException(status_code=500, detail="Google OAuth no disponible")
    if not state or not _consume_gcal_state(db, state):
        raise HTTPException(status_code=400, detail="Callback inválido o expirado. Reintenta conectar desde SUPERADMIN.")
    client_file = _gcal_client_file()
    flow = Flow.from_client_secrets_file(
        client_file, scopes=GCAL_SCOPES, redirect_uri=_gcal_redirect_uri()
    )
    flow.fetch_token(code=code)
    creds = flow.credentials
    _save_gcal_creds(db, creds)
    return {"ok": True, "message": "Google Calendar conectado. Puedes cerrar esta ventana."}


@router.post("/gcal/sync")
def gcal_sync(
    days: int = 30,
    calendar_id: str | None = None,
    db: Session = Depends(get_db),
    me=Depends(get_current_user),
):
    if build is None:
        raise HTTPException(status_code=500, detail="Google API no disponible")
    svc = _gcal_service(db)
    if not svc:
        raise HTTPException(status_code=400, detail="Google Calendar no conectado")

    _ensure_lead_calendar_cols(db)

    tz = ZoneInfo("America/Santiago")
    now = datetime.now(tz)
    time_min = (now - timedelta(days=1)).isoformat()
    time_max = (now + timedelta(days=days)).isoformat()
    events = []
    try:
        cal_id = calendar_id or os.getenv("GCAL_DEFAULT_CAL") or GCAL_DEFAULT_CAL
        evs = svc.events().list(
            calendarId=cal_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=2500,
        ).execute()
        events = evs.get("items") or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"No se pudo leer calendar: {e}")

    confirmado_id = _estado_id(db, "CONFIRM")
    if not confirmado_id:
        raise HTTPException(status_code=400, detail="Estado CONFIRMADO no existe")

    created = 0
    linked = 0
    skipped = 0
    name_expr = _lead_name_expr(db)
    pre_title_expr = _lead_col(db, "pre_title")

    def _norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", (s or "").lower())

    def _score(a: str, b: str) -> float:
        a = _norm(a)
        b = _norm(b)
        if not a or not b:
            return 0.0
        if a == b:
            return 1.0
        aset = set([a[i:i+4] for i in range(max(1, len(a) - 3))])
        bset = set([b[i:i+4] for i in range(max(1, len(b) - 3))])
        if not aset or not bset:
            return 0.0
        return len(aset & bset) / max(len(aset), len(bset))

    parsed_events = []
    for ev in events:
        event_id = ev.get("id")
        if not event_id:
            continue
        summary_raw = (ev.get("summary") or "").strip()
        if re.search(r"\b(vacaciones|cumplea(?:n|ñ)os|feriado|holiday)\b", summary_raw.lower()):
            skipped += 1
            continue
        start_dt, start_date = _parse_event_time(ev.get("start") or {})
        end_dt, _ = _parse_event_time(ev.get("end") or {})
        parsed_events.append({
            "id": event_id,
            "summary": summary_raw,
            "description": ev.get("description") or "",
            "location": ev.get("location") or "",
            "start_dt": start_dt,
            "end_dt": end_dt,
            "start_date": start_date,
            "html_link": ev.get("htmlLink") or "",
        })
        exists = db.execute(
            text("SELECT id_lead FROM leads WHERE calendar_event_id=:eid LIMIT 1"),
            {"eid": event_id},
        ).first()
        if exists:
            cal_start = "CASE WHEN calendar_start IS NULL THEN NULL WHEN calendar_start::text ~ '^[0-9]{4}-' THEN calendar_start::timestamp ELSE NULL END"
            cal_end = "CASE WHEN calendar_end IS NULL THEN NULL WHEN calendar_end::text ~ '^[0-9]{4}-' THEN calendar_end::timestamp ELSE NULL END"
            pre_start = "CASE WHEN pre_start IS NULL THEN NULL WHEN pre_start::text ~ '^[0-9]{4}-' THEN pre_start::timestamp ELSE NULL END"
            pre_end = "CASE WHEN pre_end IS NULL THEN NULL WHEN pre_end::text ~ '^[0-9]{4}-' THEN pre_end::timestamp ELSE NULL END"
            appr_at = "CASE WHEN agenda_approved_at IS NULL THEN NULL WHEN agenda_approved_at::text ~ '^[0-9]{4}-' THEN agenda_approved_at::timestamp ELSE NULL END"
            db.execute(
                text(
                    """
                    UPDATE leads
                    SET calendar_html_link = COALESCE(calendar_html_link, :link),
                        calendar_start = COALESCE({cal_start}, :cs),
                        calendar_end = COALESCE({cal_end}, :ce),
                        pre_start = COALESCE({pre_start}, :ps),
                        pre_end = COALESCE({pre_end}, :pe),
                        pre_location = COALESCE(pre_location, :ploc),
                        pre_title = COALESCE(pre_title, :ptitle),
                        pre_description = COALESCE(pre_description, :pdesc),
                        agenda_approved_at = COALESCE({appr_at}, now()),
                        pendiente_agendar = FALSE,
                        updated_at = now()
                    WHERE calendar_event_id = :eid
                    """
                    .format(
                        cal_start=cal_start,
                        cal_end=cal_end,
                        pre_start=pre_start,
                        pre_end=pre_end,
                        appr_at=appr_at,
                    )
                ),
                {
                    "eid": event_id,
                    "link": ev.get("htmlLink") or "",
                    "cs": _parse_event_time(ev.get("start") or {})[0],
                    "ce": _parse_event_time(ev.get("end") or {})[0],
                    "ps": _parse_event_time(ev.get("start") or {})[0],
                    "pe": _parse_event_time(ev.get("end") or {})[0],
                    "ploc": ev.get("location") or "",
                    "ptitle": (ev.get("summary") or "Evento sin título").strip(),
                    "pdesc": ev.get("description") or "",
                },
            )
            skipped += 1
            continue

        html_link = ev.get("htmlLink") or ""
        if html_link:
            row = db.execute(
                text("SELECT id_lead FROM leads WHERE calendar_event_id IS NULL AND calendar_html_link=:link LIMIT 1"),
                {"link": html_link},
            ).first()
            if row:
                db.execute(
                    text(
                        """
                        UPDATE leads
                        SET calendar_event_id = :eid,
                            calendar_start = :cs,
                            calendar_end = :ce,
                            pre_start = COALESCE(pre_start, :ps),
                            pre_end = COALESCE(pre_end, :pe),
                            pre_location = COALESCE(pre_location, :ploc),
                            pre_title = COALESCE(pre_title, :ptitle),
                            pre_description = COALESCE(pre_description, :pdesc),
                            agenda_approved_at = COALESCE(agenda_approved_at, now()),
                            pendiente_agendar = FALSE,
                            updated_at = now()
                        WHERE id_lead = :id
                        """
                    ),
                    {
                        "id": int(row[0]),
                        "eid": event_id,
                        "cs": _parse_event_time(ev.get("start") or {})[0],
                        "ce": _parse_event_time(ev.get("end") or {})[0],
                        "ps": _parse_event_time(ev.get("start") or {})[0],
                        "pe": _parse_event_time(ev.get("end") or {})[0],
                        "ploc": ev.get("location") or "",
                        "ptitle": (ev.get("summary") or "Evento sin título").strip(),
                        "pdesc": ev.get("description") or "",
                    },
                )
                linked += 1
                continue

        summary = summary_raw or "Evento sin título"
        desc = ev.get("description") or ""
        loc = ev.get("location") or ""
        html_link = ev.get("htmlLink") or ""

        id_marca, marca_name = _infer_marca_from_text(db, f"{summary} {desc} {loc}")
        nota = f"Creado desde Google Calendar. EventID={event_id}"
        if not id_marca:
            nota += " | Marca no detectada"

        lead_id = None
        if start_date:
            params = {"fecha": start_date, "conf": confirmado_id}
            where = "l.fecha_evento = :fecha AND l.id_estado = :conf AND (l.calendar_event_id IS NULL OR l.calendar_event_id = '')"
            if id_marca:
                where += " AND l.id_marca = :id_marca"
                params["id_marca"] = id_marca
            rows = db.execute(
                text(f"SELECT l.id_lead, {name_expr} AS nombre, {pre_title_expr} AS pre_title FROM leads l WHERE {where}"),
                params,
            ).mappings().all()
            if rows:
                s_clean = summary
                if marca_name:
                    pattern = r"\b" + re.escape(marca_name) + r"\b"
                    s_clean = re.sub(pattern, "", s_clean, flags=re.IGNORECASE).strip()
                scored = []
                for r in rows:
                    title = r.get("pre_title") or r.get("nombre") or ""
                    sc = _score(s_clean, title)
                    scored.append((sc, r["id_lead"]))
                scored.sort(reverse=True)
                if scored:
                    top = scored[0][0]
                    if len(scored) == 1:
                        if top >= 0.25:
                            lead_id = int(scored[0][1])
                    else:
                        if top >= 0.30 and (top - scored[1][0] >= 0.10):
                            lead_id = int(scored[0][1])

        if lead_id:
            db.execute(
                text(
                    """
                    UPDATE leads
                    SET id_estado = :conf,
                        calendar_event_id = :eid,
                        calendar_html_link = :link,
                        calendar_start = :cs,
                        calendar_end = :ce,
                        pre_start = :ps,
                        pre_end = :pe,
                        pre_location = :ploc,
                        pre_title = :ptitle,
                        pre_description = :pdesc,
                        agenda_approved_at = COALESCE(agenda_approved_at, now()),
                        pendiente_agendar = FALSE,
                        updated_at = now()
                    WHERE id_lead = :id
                    """
                ),
                {
                    "id": lead_id,
                    "conf": confirmado_id,
                    "eid": event_id,
                    "link": html_link,
                    "cs": start_dt,
                    "ce": end_dt,
                    "ps": start_dt,
                    "pe": end_dt,
                    "ploc": loc,
                    "ptitle": summary,
                    "pdesc": desc,
                },
            )
            linked += 1
            continue

        db.execute(
            text(
                """
                INSERT INTO public.leads(
                  cliente,email,telefono,direccion,id_marca,id_estado,id_comuna,id_tipo_cliente,
                  fecha_evento,monto_cotizado,plataforma,notas,num_cotizacion,created_at,updated_at,
                  calendar_event_id,calendar_html_link,calendar_start,calendar_end,
                  pre_start,pre_end,pre_location,pre_title,pre_description,agenda_approved_at,pendiente_agendar
                ) VALUES (
                  :cliente,NULL,NULL,NULL,:id_marca,:id_estado,0,NULL,
                  :fecha_evento,0,'Google Calendar',:notas,NULL, now(), now(),
                  :eid,:link,:cs,:ce,
                  :ps,:pe,:ploc,:ptitle,:pdesc, now(), FALSE
                )
                """
            ),
            {
                "cliente": summary,
                "id_marca": id_marca,
                "id_estado": confirmado_id,
                "fecha_evento": start_date,
                "notas": nota,
                "eid": event_id,
                "link": html_link,
                "cs": start_dt,
                "ce": end_dt,
                "ps": start_dt,
                "pe": end_dt,
                "ploc": loc,
                "ptitle": summary,
                "pdesc": desc,
            },
        )
        created += 1
    db.commit()

    if parsed_events:
        ev_by_date_brand: dict[tuple, list[dict]] = {}
        for evd in parsed_events:
            if not evd.get("start_date"):
                continue
            id_m, _ = _infer_marca_from_text(db, f"{evd.get('summary','')} {evd.get('description','')} {evd.get('location','')}")
            key = (str(evd["start_date"]), id_m or 0)
            ev_by_date_brand.setdefault(key, []).append({**evd, "id_marca": id_m})

        rows = db.execute(
            text(
                """
                SELECT id_lead, fecha_evento, id_marca, COALESCE(pre_title, cliente) AS titulo
                FROM leads
                WHERE id_estado = :conf
                  AND (calendar_event_id IS NULL OR calendar_event_id = '')
                  AND (calendar_html_link IS NULL OR calendar_html_link = '')
                  AND fecha_evento IS NOT NULL
                """
            ),
            {"conf": confirmado_id},
        ).mappings().all()

        for l in rows:
            fecha = str(l.get("fecha_evento"))
            id_marca = l.get("id_marca") or 0
            candidates = ev_by_date_brand.get((fecha, id_marca)) or []
            if not candidates and id_marca:
                candidates = ev_by_date_brand.get((fecha, 0)) or []
            if len(candidates) != 1:
                continue
            evd = candidates[0]
            db.execute(
                text(
                    """
                    UPDATE leads
                    SET calendar_event_id=:eid,
                        calendar_html_link=:link,
                        calendar_start=:cs,
                        calendar_end=:ce,
                        pre_start=COALESCE(pre_start,:ps),
                        pre_end=COALESCE(pre_end,:pe),
                        pre_title=COALESCE(pre_title,:ptitle),
                        pre_location=COALESCE(pre_location,:ploc),
                        pre_description=COALESCE(pre_description,:pdesc),
                        agenda_approved_at=COALESCE(agenda_approved_at, now()),
                        pendiente_agendar=FALSE,
                        updated_at=now()
                    WHERE id_lead=:id
                    """
                ),
                {
                    "id": l["id_lead"],
                    "eid": evd["id"],
                    "link": evd.get("html_link") or "",
                    "cs": evd.get("start_dt"),
                    "ce": evd.get("end_dt"),
                    "ps": evd.get("start_dt"),
                    "pe": evd.get("end_dt"),
                    "ptitle": (evd.get("summary") or "").strip(),
                    "ploc": evd.get("location") or "",
                    "pdesc": evd.get("description") or "",
                },
            )
        db.commit()
    return {"ok": True, "created": created, "linked": linked, "skipped": skipped, "total": len(events)}


@router.get("/agenda")
def agenda(db: Session = Depends(get_db), me=Depends(get_current_user)):
    name_expr = _lead_name_expr(db)
    role = (me.get("role") or me.get("rol") or "").upper()
    only_own = not _is_admin(role)
    marcas = _fetch_marcas_ids(db, me)
    marca_sql = ""
    marca_params: dict[str, Any] = {}
    if only_own and marcas:
        marca_sql = " AND l.id_marca = ANY(:marcas) "
        marca_params["marcas"] = marcas

    has_pre_start = _col_exists(db, "leads", "pre_start")
    has_agenda_approved_at = _col_exists(db, "leads", "agenda_approved_at")
    has_pendiente_agendar = _col_exists(db, "leads", "pendiente_agendar")
    has_calendar_link = _col_exists(db, "leads", "calendar_html_link")
    has_calendar_id = _col_exists(db, "leads", "calendar_event_id")

    pre_title_expr = _lead_col(db, "pre_title")
    pre_start_expr = _lead_col(db, "pre_start")
    pre_end_expr = _lead_col(db, "pre_end")
    pre_location_expr = _lead_col(db, "pre_location")
    pre_products_expr = _lead_col(db, "pre_products_text")
    pre_montaje_expr = _lead_col(db, "pre_montaje_text")
    pre_ops_expr = _lead_col(db, "pre_ops")
    calendar_expr = _lead_col(db, "calendar_html_link")
    agenda_by_expr = _lead_col(db, "agenda_approved_by")
    agenda_at_expr = _lead_col(db, "agenda_approved_at")

    por_aprobar = []
    if has_pre_start:
        where = "l.pre_start IS NOT NULL"
        if has_agenda_approved_at:
            where += " AND l.agenda_approved_at IS NULL"
        por_aprobar = db.execute(text(f"""
            SELECT l.id_lead, {name_expr} AS nombre_cliente, l.telefono, l.email, l.fecha_evento, l.monto_cotizado,
                   {pre_title_expr} AS pre_title, {pre_start_expr} AS pre_start, {pre_end_expr} AS pre_end,
                   {pre_location_expr} AS pre_location, {pre_products_expr} AS pre_products_text,
                   {pre_montaje_expr} AS pre_montaje_text, {pre_ops_expr} AS pre_ops,
                   m.nombre AS marca, c.nombre AS comuna, e.nombre AS estado
            FROM leads l
            LEFT JOIN marcas m ON m.id_marca=l.id_marca
            LEFT JOIN comunas c ON c.id_comuna=l.id_comuna
            LEFT JOIN estados_lead e ON e.id_estado=l.id_estado
            WHERE {where} {marca_sql}
            ORDER BY l.pre_start ASC NULLS LAST, l.id_lead DESC
        """), marca_params).mappings().all()

    confirmados_sin_preagenda = []
    confirmado_id = _estado_id(db, "CONFIRM")
    if confirmado_id:
        where_parts = ["l.id_estado=:conf"]
        params = {"conf": confirmado_id, **marca_params}
        if has_calendar_link:
            where_parts.append("(l.calendar_html_link IS NULL OR l.calendar_html_link = '')")
        if has_calendar_id:
            where_parts.append("(l.calendar_event_id IS NULL OR l.calendar_event_id = '')")
        pre_parts = []
        if has_pendiente_agendar:
            pre_parts.append("l.pendiente_agendar = TRUE")
        if has_pre_start:
            pre_parts.append("l.pre_start IS NULL")
        if pre_parts:
            where_parts.append("(" + " OR ".join(pre_parts) + ")")
        where = " AND ".join(where_parts)
        confirmados_sin_preagenda = db.execute(text(f"""
            SELECT l.id_lead, {name_expr} AS nombre_cliente, l.telefono, l.email, l.fecha_evento, l.monto_cotizado,
                   m.nombre AS marca, c.nombre AS comuna, e.nombre AS estado
            FROM leads l
            LEFT JOIN marcas m ON m.id_marca=l.id_marca
            LEFT JOIN comunas c ON c.id_comuna=l.id_comuna
            LEFT JOIN estados_lead e ON e.id_estado=l.id_estado
            WHERE {where} {marca_sql}
            ORDER BY l.id_lead DESC
        """), params).mappings().all()

    agendados = []
    if has_agenda_approved_at or has_calendar_link:
        where = "1=0"
        if has_agenda_approved_at and has_calendar_link:
            where = "l.agenda_approved_at IS NOT NULL OR (l.calendar_html_link IS NOT NULL AND l.calendar_html_link <> '')"
        elif has_agenda_approved_at:
            where = "l.agenda_approved_at IS NOT NULL"
        elif has_calendar_link:
            where = "(l.calendar_html_link IS NOT NULL AND l.calendar_html_link <> '')"
        agendados = db.execute(text(f"""
            SELECT l.id_lead, {name_expr} AS nombre_cliente, l.telefono, l.email, l.fecha_evento, l.monto_cotizado,
                   {calendar_expr} AS calendar_html_link, {agenda_by_expr} AS agenda_approved_by, {agenda_at_expr} AS agenda_approved_at,
                   m.nombre AS marca, c.nombre AS comuna, e.nombre AS estado
            FROM leads l
            LEFT JOIN marcas m ON m.id_marca=l.id_marca
            LEFT JOIN comunas c ON c.id_comuna=l.id_comuna
            LEFT JOIN estados_lead e ON e.id_estado=l.id_estado
            WHERE {where} {marca_sql}
            ORDER BY l.agenda_approved_at DESC NULLS LAST, l.id_lead DESC
        """), marca_params).mappings().all()

    return {
        "ok": True,
        "por_aprobar": list(por_aprobar),
        "confirmados_sin_preagenda": list(confirmados_sin_preagenda),
        "por_agendar": list(confirmados_sin_preagenda),
        "agendados_no_confirmados": list(por_aprobar),
        "agendados": list(agendados),
        "counts": {
            "por_aprobar": len(por_aprobar),
            "confirmados_sin_preagenda": len(confirmados_sin_preagenda),
            "por_agendar": len(confirmados_sin_preagenda),
            "agendados_no_confirmados": len(por_aprobar),
            "agendados": len(agendados),
        },
    }


@router.get("/dashboard/ops_alertas")
def dashboard_ops_alertas(
    days_ahead: int = 30,
    id_marca: int | None = None,
    db: Session = Depends(get_db),
    me=Depends(get_current_user),
):
    """
    Alertas operacionales para eventos confirmados próximos:
    - Falta teléfono
    - Falta dirección (pre_direccion o direccion)
    - Falta horario (pre_start/pre_end)
    """
    tz = ZoneInfo("America/Santiago")
    today = datetime.now(tz).date()
    try:
        days_ahead = int(days_ahead)
    except Exception:
        days_ahead = 30
    if days_ahead < 3:
        days_ahead = 3
    if days_ahead > 120:
        days_ahead = 120
    end_day = today + timedelta(days=days_ahead)

    role = (me.get("role") or me.get("rol") or "").upper()
    marcas = _fetch_marcas_ids(db, me)
    only_own = not _is_admin(role)
    if only_own and not marcas:
        return {"ok": True, "range": {"from": str(today), "to": str(end_day)}, "counts": {"tel": 0, "dir": 0, "hr": 0}, "items": {"tel": [], "dir": [], "hr": []}}

    confirmado_id = _estado_id(db, "CONFIRM")
    if not confirmado_id:
        return {"ok": True, "range": {"from": str(today), "to": str(end_day)}, "counts": {"tel": 0, "dir": 0, "hr": 0}, "items": {"tel": [], "dir": [], "hr": []}}

    name_expr = _lead_name_expr(db)
    pre_start_expr = _lead_col(db, "pre_start")
    pre_end_expr = _lead_col(db, "pre_end")
    pre_dir_expr = _lead_col(db, "pre_direccion")
    dir_expr = _lead_col(db, "direccion")

    marca_sql = ""
    params: dict[str, Any] = {"conf": confirmado_id, "d1": today, "d2": end_day}
    if only_own and marcas:
        marca_sql = " AND l.id_marca = ANY(:marcas) "
        params["marcas"] = marcas
        if id_marca and int(id_marca) in set(marcas):
            marca_sql += " AND l.id_marca = :id_marca "
            params["id_marca"] = int(id_marca)
    elif id_marca:
        marca_sql = " AND l.id_marca = :id_marca "
        params["id_marca"] = int(id_marca)

    base_where = f"""
      l.id_estado = :conf
      AND l.fecha_evento::date BETWEEN :d1 AND :d2
      {marca_sql}
    """

    def _rows(where_extra: str) -> list[dict]:
        q = f"""
          SELECT l.id_lead,
                 {name_expr} AS cliente,
                 l.telefono,
                 l.fecha_evento,
                 {pre_start_expr} AS pre_start,
                 {pre_end_expr} AS pre_end,
                 COALESCE({pre_dir_expr}, {dir_expr}, '') AS direccion,
                 COALESCE(m.nombre, m.marca, '') AS marca,
                 COALESCE(c.nombre, '') AS comuna
          FROM leads l
          LEFT JOIN marcas m ON m.id_marca = l.id_marca
          LEFT JOIN comunas c ON c.id_comuna = l.id_comuna
          WHERE {base_where}
            {where_extra}
          ORDER BY l.fecha_evento ASC NULLS LAST, l.id_lead DESC
          LIMIT 30
        """
        return [dict(r) for r in db.execute(text(q), params).mappings().all()]

    def _count(where_extra: str) -> int:
        q = f"""
          SELECT COUNT(*)::int AS n
          FROM leads l
          WHERE {base_where}
            {where_extra}
        """
        try:
            return int(db.execute(text(q), params).scalar_one() or 0)
        except Exception:
            return 0

    tel_where = " AND (l.telefono IS NULL OR btrim(l.telefono) = '') "
    dir_where = f" AND (COALESCE({pre_dir_expr}, {dir_expr}, '') IS NULL OR btrim(COALESCE({pre_dir_expr}, {dir_expr}, '')) = '') "
    hr_where = f" AND ({pre_start_expr} IS NULL OR {pre_end_expr} IS NULL) "

    items_tel = _rows(tel_where)
    items_dir = _rows(dir_where)
    items_hr = _rows(hr_where)

    return {
        "ok": True,
        "range": {"from": str(today), "to": str(end_day)},
        "counts": {
            "tel": _count(tel_where),
            "dir": _count(dir_where),
            "hr": _count(hr_where),
        },
        "items": {"tel": items_tel, "dir": items_dir, "hr": items_hr},
    }


@router.get("/dashboard")
def dashboard(
    id_marca: int | None = None,
    db: Session = Depends(get_db),
    me=Depends(get_current_user),
):
    tz = ZoneInfo("America/Santiago")
    now = datetime.now(tz)
    today = now.date()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    week_num = week_start.isocalendar().week
    week_days = [(week_start + timedelta(days=i)).isoformat() for i in range(7)]

    role = (me.get("role") or me.get("rol") or "").upper()
    marcas = _fetch_marcas_ids(db, me)
    only_own = not _is_admin(role)
    if only_own and not marcas:
        return {
            "ok": True,
            "week": {"start": str(week_start), "end": str(week_end), "number": week_num},
            "pipeline": [],
            "tasks": {"preagenda": 0, "confirmados_sin_preagenda": 0},
            "events": [],
            "kpis": {"venta_dia": 0, "venta_semana": 0, "cierre_pct": 0, "total_semana": 0, "confirmados_semana": 0},
        }

    if _col_exists(db, "leads", "fecha_ingreso"):
        date_col = "fecha_ingreso"
    elif _col_exists(db, "leads", "created_at"):
        date_col = "created_at"
    else:
        date_col = "fecha_evento"
    name_expr = _lead_name_expr(db)
    pre_start_expr = _lead_col(db, "pre_start")
    pre_end_expr = _lead_col(db, "pre_end")
    pre_location_expr = _lead_col(db, "pre_location")
    pre_title_expr = _lead_col(db, "pre_title")
    cal_expr = _lead_col(db, "calendar_html_link")
    has_pre_start = _col_exists(db, "leads", "pre_start")
    has_pre_end = _col_exists(db, "leads", "pre_end")
    confirmado_id = _estado_id(db, "CONFIRM")
    params = {"ws": week_start, "we": week_end}

    marca_sql = ""
    if only_own and marcas:
        marca_sql = " AND l.id_marca = ANY(:marcas) "
        params["marcas"] = marcas
        if id_marca and int(id_marca) in set(marcas):
            marca_sql += " AND l.id_marca = :id_marca "
            params["id_marca"] = int(id_marca)
    elif id_marca:
        marca_sql = " AND l.id_marca = :id_marca "
        params["id_marca"] = int(id_marca)

    brand_rows = db.execute(
        text("SELECT id_marca, COALESCE(nombre, marca) AS nombre FROM marcas")
    ).mappings().all()
    brand_map = {int(r["id_marca"]): r["nombre"] for r in brand_rows if r.get("id_marca")}

    q_pipe = f"""
        SELECT COALESCE(e.id_estado, 9999)::int AS id_estado,
               COALESCE(e.nombre,'') AS estado,
               COALESCE(e.color,'#64748b') AS color,
               COUNT(*)::int AS cantidad,
               COALESCE(SUM(l.monto_cotizado),0)::float AS monto
        FROM leads l
        LEFT JOIN estados_lead e ON e.id_estado=l.id_estado
        WHERE 1=1
        {marca_sql}
        GROUP BY e.id_estado, e.nombre, e.color
        ORDER BY COALESCE(e.id_estado, 9999) ASC, cantidad DESC
    """
    pipeline = db.execute(text(q_pipe), params).mappings().all()

    q_pipe_week = f"""
        SELECT COALESCE(e.id_estado, 9999)::int AS id_estado,
               COALESCE(e.nombre,'') AS estado,
               COALESCE(e.color,'#64748b') AS color,
               COUNT(*)::int AS cantidad,
               COALESCE(SUM(l.monto_cotizado),0)::float AS monto
        FROM leads l
        LEFT JOIN estados_lead e ON e.id_estado=l.id_estado
        WHERE l.{date_col}::date BETWEEN :ws AND :we
        {marca_sql}
        GROUP BY e.id_estado, e.nombre, e.color
        ORDER BY COALESCE(e.id_estado, 9999) ASC, cantidad DESC
    """
    pipeline_week = db.execute(text(q_pipe_week), params).mappings().all()

    month_start = date(today.year, today.month, 1)
    month_end = date(today.year, today.month, 28) + timedelta(days=4)
    month_end = month_end.replace(day=1) - timedelta(days=1)
    q_pipe_month = f"""
        SELECT COALESCE(e.id_estado, 9999)::int AS id_estado,
               COALESCE(e.nombre,'') AS estado,
               COALESCE(e.color,'#64748b') AS color,
               COUNT(*)::int AS cantidad,
               COALESCE(SUM(l.monto_cotizado),0)::float AS monto
        FROM leads l
        LEFT JOIN estados_lead e ON e.id_estado=l.id_estado
        WHERE l.{date_col}::date BETWEEN :ms AND :me
        {marca_sql}
        GROUP BY e.id_estado, e.nombre, e.color
        ORDER BY COALESCE(e.id_estado, 9999) ASC, cantidad DESC
    """
    pipeline_month = db.execute(
        text(q_pipe_month),
        {**params, "ms": month_start, "me": month_end},
    ).mappings().all()

    q_pre = f"""
        SELECT COUNT(*)::int AS n
        FROM leads l
        WHERE l.pre_start IS NOT NULL AND l.agenda_approved_at IS NULL
        {marca_sql}
    """
    if _col_exists(db, "leads", "calendar_event_id"):
        q_pre = q_pre.replace("WHERE", "WHERE (l.calendar_event_id IS NULL OR l.calendar_event_id='') AND")
    if _col_exists(db, "leads", "calendar_html_link"):
        q_pre = q_pre.replace("WHERE", "WHERE (l.calendar_html_link IS NULL OR l.calendar_html_link='') AND")
    preagenda = db.execute(text(q_pre), params).scalar_one()

    conf_sin = 0
    conf_where = ""
    conf_params = dict(params)
    if confirmado_id:
        conf_where = "l.id_estado=:conf"
        conf_params["conf"] = confirmado_id
    else:
        conf_where = "EXISTS (SELECT 1 FROM estados_lead e WHERE e.id_estado=l.id_estado AND UPPER(e.nombre) LIKE :confname)"
        conf_params["confname"] = "%CONFIRM%"

    if conf_where:
        q_conf = f"""
            SELECT COUNT(*)::int AS n
            FROM leads l
            WHERE {conf_where} AND (l.pendiente_agendar IS TRUE OR l.pre_start IS NULL)
            {marca_sql}
        """
        if _col_exists(db, "leads", "calendar_event_id"):
            q_conf = q_conf.replace("WHERE", "WHERE (l.calendar_event_id IS NULL OR l.calendar_event_id='') AND")
        if _col_exists(db, "leads", "calendar_html_link"):
            q_conf = q_conf.replace("WHERE", "WHERE (l.calendar_html_link IS NULL OR l.calendar_html_link='') AND")
        conf_sin = db.execute(text(q_conf), conf_params).scalar_one()

    events = []
    if conf_where:
        pre_title_expr = _lead_col(db, "pre_title")
        pre_direccion_expr = _lead_col(db, "pre_direccion")
        direccion_expr = _lead_col(db, "direccion")
        q_ev = f"""
            SELECT l.id_lead, {name_expr} AS nombre_cliente, l.fecha_evento,
                   {pre_start_expr} AS pre_start, {pre_end_expr} AS pre_end, {pre_location_expr} AS pre_location,
                   {pre_direccion_expr} AS pre_direccion,
                   {direccion_expr} AS direccion,
                   {cal_expr} AS calendar_html_link,
                   l.calendar_event_id,
                   {pre_title_expr} AS pre_title,
                   COALESCE(m.nombre,m.marca,'') AS marca,
                   COALESCE(c.nombre,'') AS comuna
            FROM leads l
            LEFT JOIN marcas m ON m.id_marca=l.id_marca
            LEFT JOIN comunas c ON c.id_comuna=l.id_comuna
            WHERE {conf_where}
              AND l.fecha_evento BETWEEN :ws AND :we
            {marca_sql}
            ORDER BY l.fecha_evento ASC, l.id_lead DESC
        """
        rows = db.execute(text(q_ev), conf_params).mappings().all()
        seen = set()

        def _n(s: str | None) -> str:
            return re.sub(r"[^a-z0-9]+", "", (s or "").strip().lower())

        for r in rows:
            key = r.get("calendar_event_id") or ""
            if key:
                if key in seen:
                    continue
                seen.add(key)
            else:
                tup = (
                    str(r.get("fecha_evento") or ""),
                    _n(r.get("pre_title") or r.get("nombre_cliente") or ""),
                    _n(r.get("marca") or ""),
                    _n(r.get("comuna") or ""),
                )
                if tup in seen:
                    continue
                seen.add(tup)
            link = r.get("calendar_html_link") or ""
            if not link and has_pre_start and has_pre_end and r.get("pre_start") and r.get("pre_end"):
                try:
                    def _mk_loc(comuna_pref: str | None, comuna: str | None) -> str:
                        # Regla: LOCATION = comuna (la dirección va en descripción / pre_direccion).
                        c1 = (comuna_pref or "").strip()
                        c2 = (comuna or "").strip()
                        return c1 or c2 or "COMUNA TBD"

                    link = _gcal_link(
                        f"Evento {r.get('nombre_cliente') or ''}",
                        r["pre_start"],
                        r["pre_end"],
                        details="Evento confirmado",
                        location=_mk_loc(r.get("pre_location") or "", r.get("comuna") or ""),
                    )
                except Exception:
                    link = ""
            events.append({
                "id_lead": r.get("id_lead"),
                "cliente": r.get("nombre_cliente"),
                "fecha_evento": str(r.get("fecha_evento") or ""),
                "marca": r.get("marca"),
                "comuna": r.get("comuna"),
                "calendar_html_link": link,
            })

    total_semana = db.execute(
        text(f"SELECT COUNT(*)::int FROM leads l WHERE l.{date_col}::date BETWEEN :ws AND :we {marca_sql}"),
        params,
    ).scalar_one()

    leads_hoy = db.execute(
        text(f"SELECT COUNT(*)::int FROM leads l WHERE l.{date_col}::date = :today {marca_sql}"),
        {**params, "today": today},
    ).scalar_one()

    confirmados_semana = 0
    venta_semana = 0
    venta_dia = 0
    sales_daily: list[dict] = []
    if conf_where:
        confirmados_semana = db.execute(
            text(f"""
                SELECT COUNT(*)::int
                FROM leads l
                WHERE {conf_where} AND l.{date_col}::date BETWEEN :ws AND :we
                {marca_sql}
            """),
            conf_params,
        ).scalar_one()

        if _table_exists_pg(db, "activity_log"):
            try:
                tzname = "America/Santiago"
                rows = db.execute(
                    text(
                        f"""
                        WITH conf AS (
                          SELECT entity_id::bigint AS id_lead,
                                 MAX(created_at AT TIME ZONE :tz) AS confirmed_local
                          FROM public.activity_log
                          WHERE entity_type='lead'
                            AND action IN ('EVENT_CONFIRMED_AGENDED')
                            AND entity_id IS NOT NULL
                          GROUP BY entity_id
                        )
                        SELECT
                          (c.confirmed_local::date) AS dia,
                          l.id_marca,
                          COALESCE(SUM(l.monto_cotizado),0)::float AS monto
                        FROM conf c
                        JOIN leads l ON l.id_lead = c.id_lead
                        WHERE c.confirmed_local::date BETWEEN :ws AND :we
                        {marca_sql}
                        GROUP BY 1,2
                        ORDER BY 1 ASC, 2 ASC
                        """
                    ),
                    {**params, "tz": tzname},
                ).mappings().all()
                for r in rows:
                    mid = r.get("id_marca")
                    name = brand_map.get(int(mid)) if mid is not None else ""
                    if not name:
                        continue
                    sales_daily.append({
                        "dia": str(r.get("dia")),
                        "id_marca": int(mid) if mid is not None else None,
                        "marca": str(name).upper(),
                        "monto": float(r.get("monto") or 0),
                    })
                venta_semana = float(sum([float(x.get("monto") or 0) for x in sales_daily]) or 0)
                venta_dia = float(sum([float(x.get("monto") or 0) for x in sales_daily if x.get("dia") == str(today)]) or 0)
            except Exception:
                sales_daily = []

        if not sales_daily:
            try:
                rows = db.execute(
                    text(
                        f"""
                        SELECT l.updated_at::date AS dia,
                               l.id_marca,
                               COALESCE(SUM(l.monto_cotizado),0)::float AS monto
                        FROM leads l
                        WHERE {conf_where}
                          AND l.updated_at::date BETWEEN :ws AND :we
                          {marca_sql}
                        GROUP BY 1,2
                        ORDER BY 1 ASC, 2 ASC
                        """
                    ),
                    conf_params,
                ).mappings().all()
                for r in rows:
                    mid = r.get("id_marca")
                    name = brand_map.get(int(mid)) if mid is not None else ""
                    if not name:
                        continue
                    sales_daily.append({
                        "dia": str(r.get("dia")),
                        "id_marca": int(mid) if mid is not None else None,
                        "marca": str(name).upper(),
                        "monto": float(r.get("monto") or 0),
                    })
                venta_semana = float(sum([float(x.get("monto") or 0) for x in sales_daily]) or 0)
                venta_dia = float(sum([float(x.get("monto") or 0) for x in sales_daily if x.get("dia") == str(today)]) or 0)
            except Exception:
                venta_semana = db.execute(
                    text(f"""
                        SELECT COALESCE(SUM(l.monto_cotizado),0)::float
                        FROM leads l
                        WHERE {conf_where} AND l.updated_at::date BETWEEN :ws AND :we
                        {marca_sql}
                    """),
                    conf_params,
                ).scalar_one()
                venta_dia = db.execute(
                    text(f"""
                        SELECT COALESCE(SUM(l.monto_cotizado),0)::float
                        FROM leads l
                        WHERE {conf_where} AND l.updated_at::date = :today
                        {marca_sql}
                    """),
                    {**conf_params, "today": today},
                ).scalar_one()

        # Fuente oficial de venta (si existe): fin_eventos.monto_bruto por fecha_evento (semana actual).
        if _table_exists_pg(db, "fin_eventos"):
            try:
                q_sales = f"""
                    SELECT fe.fecha_evento::date AS dia,
                           l.id_marca,
                           COALESCE(SUM(fe.monto_bruto),0)::float AS monto
                    FROM fin_eventos fe
                    JOIN leads l ON l.id_lead=fe.id_lead
                    WHERE fe.fecha_evento::date BETWEEN :ws AND :we
                      AND {conf_where}
                      {marca_sql}
                    GROUP BY 1,2
                    ORDER BY 1 ASC, 2 ASC
                """
                rows = db.execute(text(q_sales), conf_params).mappings().all()
                sales_daily = []
                for r in rows:
                    mid = r.get("id_marca")
                    name = brand_map.get(int(mid)) if mid is not None else ""
                    if not name:
                        continue
                    sales_daily.append({
                        "dia": str(r.get("dia")),
                        "id_marca": int(mid) if mid is not None else None,
                        "marca": str(name).upper(),
                        "monto": float(r.get("monto") or 0),
                    })
                venta_semana = float(sum([float(x.get("monto") or 0) for x in sales_daily]) or 0)
                venta_dia = float(sum([float(x.get("monto") or 0) for x in sales_daily if x.get("dia") == str(today)]) or 0)
            except Exception:
                pass

    cierre_pct = (confirmados_semana / total_semana * 100) if total_semana else 0

    _ensure_baseline(db)
    try:
        _ensure_metas(db)
    except Exception:
        pass
    month = today.month

    allowed_names = None
    if only_own and marcas:
        allowed_names = {brand_map.get(m) for m in marcas if brand_map.get(m)}

    baseline_rows = db.execute(
        text("SELECT marca, monto, empresa, particular FROM ventas_baseline WHERE mes=:m"),
        {"m": month},
    ).mappings().all()
    if allowed_names is not None:
        baseline_rows = [b for b in baseline_rows if b.get("marca") in allowed_names]

    actual_rows = []
    if conf_where:
        if _table_exists_pg(db, "activity_log"):
            try:
                tzname = "America/Santiago"
                actual_rows = db.execute(
                    text(
                        f"""
                        WITH conf AS (
                          SELECT entity_id::bigint AS id_lead,
                                 MAX(created_at AT TIME ZONE :tz) AS confirmed_local
                          FROM public.activity_log
                          WHERE entity_type='lead'
                            AND action IN ('EVENT_CONFIRMED_AGENDED')
                            AND entity_id IS NOT NULL
                          GROUP BY entity_id
                        )
                        SELECT l.id_marca, COALESCE(SUM(l.monto_cotizado),0)::float AS monto
                        FROM conf c
                        JOIN leads l ON l.id_lead = c.id_lead
                        WHERE c.confirmed_local::date BETWEEN :ms AND :me
                        {marca_sql}
                        GROUP BY l.id_marca
                        """
                    ),
                    {**params, "ms": month_start, "me": month_end, "tz": tzname},
                ).mappings().all()
            except Exception:
                actual_rows = []

        if not actual_rows:
            actual_rows = db.execute(
                text(
                    f"""
                    SELECT l.id_marca, COALESCE(SUM(l.monto_cotizado),0)::float AS monto
                    FROM leads l
                    WHERE {conf_where}
                      AND l.updated_at::date BETWEEN :ms AND :me
                      {marca_sql}
                    GROUP BY l.id_marca
                    """
                ),
                {**conf_params, "ms": month_start, "me": month_end},
            ).mappings().all()
    actual_map = {}
    for r in actual_rows:
        mid = r.get("id_marca")
        name = brand_map.get(int(mid)) if mid is not None else None
        if name:
            actual_map[name] = float(r.get("monto") or 0)

    # Metas mensuales (si existen), por marca.
    metas_by_marca: dict[str, dict[str, Any]] = {}
    try:
        if _table_exists_pg(db, "metas_marca_mensual") and _table_exists_pg(db, "marcas"):
            rows = db.execute(
                text(
                    """
                    SELECT COALESCE(m.nombre, m.marca, '') AS marca,
                           COALESCE(mm.venta_base,0)::float AS venta_base,
                           COALESCE(mm.crecimiento_pct,0)::float AS crecimiento_pct,
                           COALESCE(mm.meta,0)::float AS meta
                    FROM metas_marca_mensual mm
                    JOIN marcas m ON m.id_marca = mm.id_marca
                    WHERE mm.year=:y AND mm.month=:m
                    """
                ),
                {"y": int(today.year), "m": int(month)},
            ).mappings().all()
            for r in rows:
                name = str(r.get("marca") or "").strip()
                if not name:
                    continue
                metas_by_marca[name] = {
                    "venta_base": float(r.get("venta_base") or 0),
                    "crecimiento_pct": float(r.get("crecimiento_pct") or 0),
                    "meta": float(r.get("meta") or 0),
                }
    except Exception:
        metas_by_marca = {}

    sales_compare = []
    for b in baseline_rows:
        name = b.get("marca")
        base = float(b.get("monto") or 0)
        # target legacy: 12% sobre baseline. Si hay meta configurada, usarla.
        meta_row = metas_by_marca.get(str(name or "").strip(), {})
        target = float(meta_row.get("meta") or 0) or round(base * 1.12, 2)
        actual = float(actual_map.get(name, 0))
        vs_base = (actual / base * 100) if base else 0
        vs_target = (actual / target * 100) if target else 0
        sales_compare.append({
            "marca": name,
            # Backward compat con frontend (reportes.html): baseline/meta
            "base": base,
            "baseline": base,
            "target": target,
            "meta": target,
            "actual": actual,
            "vs_base": round(vs_base, 2),
            "vs_target": round(vs_target, 2),
            "vs_meta": round(vs_target, 2),
            "empresa": int(b.get("empresa") or 0),
            "particular": int(b.get("particular") or 0),
            "meta_cfg": meta_row or None,
        })

    commissions = []
    if _is_admin(role):
        com_rows = db.execute(
            text("SELECT marca, porcentaje FROM comisiones WHERE is_active IS TRUE")
        ).mappings().all()
        com_map = {}
        default_pct = 3.0
        for r in com_rows:
            m = (r.get("marca") or "").upper().strip()
            pct = float(r.get("porcentaje") or 0)
            if not m:
                continue
            if m in ("*", "TODAS"):
                default_pct = pct or default_pct
            else:
                com_map[m] = pct

        for b in baseline_rows:
            name = b.get("marca")
            if not name:
                continue
            actual = float(actual_map.get(name, 0))
            pct = com_map.get(str(name).upper(), default_pct)
            commissions.append({
                "marca": name,
                "porcentaje": round(pct, 2),
                "venta": actual,
                "comision": round(actual * (pct / 100.0), 2),
            })

    if conf_where and not sales_daily:
        rows = db.execute(
            text(
                f"""
                SELECT l.fecha_evento::date AS dia,
                       l.id_marca,
                       COALESCE(SUM(l.monto_cotizado),0)::float AS monto
                FROM leads l
                WHERE {conf_where}
                  AND l.fecha_evento BETWEEN :ws AND :we
                  {marca_sql}
                GROUP BY l.fecha_evento::date, l.id_marca
                ORDER BY l.fecha_evento::date ASC
                """
            ),
            conf_params,
        ).mappings().all()
        for r in rows:
            mid = r.get("id_marca")
            name = brand_map.get(int(mid)) if mid is not None else ""
            if name:
                sales_daily.append({"dia": str(r.get("dia")), "marca": str(name).upper(), "monto": float(r.get("monto") or 0)})

    return {
        "ok": True,
        "week": {"start": str(week_start), "end": str(week_end), "number": week_num},
        "week_days": week_days,
        "pipeline": list(pipeline),
        "pipeline_week": list(pipeline_week),
        "pipeline_month": list(pipeline_month),
        "tasks": {"preagenda": int(preagenda), "confirmados_sin_preagenda": int(conf_sin)},
        "events": events,
        "kpis": {
            "venta_dia": float(venta_dia or 0),
            "venta_semana": float(venta_semana or 0),
            "cierre_pct": float(round(cierre_pct, 2)),
            "total_semana": int(total_semana),
            "confirmados_semana": int(confirmados_semana),
            "leads_hoy": int(leads_hoy),
        },
        "sales_compare": sales_compare,
        "commissions": commissions,
        "sales_daily": sales_daily,
        "month": month,
    }


@router.get("/dashboard/reportes")
def dashboard_reportes(
    fecha_inicio: Optional[str] = None,
    fecha_termino: Optional[str] = None,
    periodo: Optional[str] = None,  # mtd | ytd | range
    id_marca: int | None = None,
    top_n: int = 20,
    productos_order: str = "monto",
    comunas_order: str = "monto",
    clientes_order: str = "monto",
    db: Session = Depends(get_db),
    me=Depends(get_current_user),
):
    # IMPORTANTE: este endpoint no debe botar el frontend. Si algo falla en reportería
    # (tablas/columnas faltantes o SQL incompatibles), devolvemos payload vacío con ok=true.
    try:
        tz = ZoneInfo("America/Santiago")
        today = datetime.now(tz).date()

        role = (me.get("role") or me.get("rol") or "").upper()
        marcas = _fetch_marcas_ids(db, me)
        only_own = not _is_admin(role)

        # Base de fecha por defecto: fecha_evento.
        if _col_exists(db, "leads", "fecha_evento"):
            date_col = "fecha_evento"
        elif _col_exists(db, "leads", "fecha_ingreso"):
            date_col = "fecha_ingreso"
        else:
            date_col = "created_at"

        def _parse_date(s: str) -> date:
            return datetime.strptime(s, "%Y-%m-%d").date()

        p = (periodo or "").strip().lower() or "range"
        ini_d: date | None = None
        fin_d: date | None = None
        if fecha_inicio:
            try:
                ini_d = _parse_date(str(fecha_inicio))
            except Exception:
                ini_d = None
        if fecha_termino:
            try:
                fin_d = _parse_date(str(fecha_termino))
            except Exception:
                fin_d = None
        if p in ("mtd", "mes", "month"):
            ini_d = date(today.year, today.month, 1)
            fin_d = today
        elif p in ("ytd", "anio", "year"):
            ini_d = date(today.year, 1, 1)
            fin_d = today
        if ini_d and not fin_d:
            fin_d = today
        if fin_d and not ini_d:
            ini_d = date(fin_d.year, fin_d.month, 1)
        if not ini_d or not fin_d:
            ini_d = date(today.year, today.month, 1)
            fin_d = today

        ly_ini = date(ini_d.year - 1, ini_d.month, ini_d.day)
        ly_fin = date(fin_d.year - 1, fin_d.month, fin_d.day)

        where_parts = ["1=1"]
        params: dict[str, Any] = {}
        where_parts.append(f"DATE(l.{date_col}) >= :ini")
        where_parts.append(f"DATE(l.{date_col}) <= :fin")
        params["ini"] = ini_d.isoformat()
        params["fin"] = fin_d.isoformat()

        if id_marca:
            try:
                mid = int(id_marca)
            except Exception:
                mid = 0
            if mid > 0:
                if only_own and marcas and (mid not in set(marcas)):
                    raise HTTPException(status_code=403, detail="No autorizado para ver esta marca")
                where_parts.append("l.id_marca = :id_marca")
                params["id_marca"] = mid
        if only_own and marcas:
            where_parts.append("l.id_marca = ANY(:marcas)")
            params["marcas"] = marcas
        where_sql = " AND ".join(where_parts)

        confirmado_id = _estado_id(db, "CONFIRM")
        conf_where = "1=1"
        if confirmado_id:
            conf_where = "l.id_estado=:conf"
            params["conf"] = confirmado_id

        funnel: list[dict] = []
        try:
            q_funnel = f"""
                SELECT COALESCE(e.nombre,'Sin estado') AS estado,
                       COUNT(*)::int AS cantidad,
                       COALESCE(SUM(l.monto_cotizado),0) AS monto
                FROM leads l
                LEFT JOIN estados_lead e ON e.id_estado=l.id_estado
                WHERE {where_sql}
                GROUP BY e.nombre
                ORDER BY cantidad DESC
            """
            funnel = db.execute(text(q_funnel), params).mappings().all()
        except Exception:
            funnel = []

        diarios_col = "fecha_evento" if _col_exists(db, "leads", "fecha_evento") else date_col
        eventos_diarios: list[dict] = []
        try:
            q_diarios = f"""
                SELECT DATE(l.{diarios_col}) AS dia,
                       COUNT(*)::int AS cantidad,
                       COALESCE(SUM(l.monto_cotizado),0) AS monto
                FROM leads l
                WHERE {where_sql}
                  AND l.{diarios_col} IS NOT NULL
                  AND {conf_where}
                GROUP BY DATE(l.{diarios_col})
                ORDER BY dia ASC
            """
            eventos_diarios = db.execute(text(q_diarios), params).mappings().all()
        except Exception:
            eventos_diarios = []

        name_expr = _lead_name_expr(db)
        clientes: list[dict] = []
        try:
            lim = max(5, min(100, int(top_n or 20)))
            if _table_exists_pg(db, "fin_eventos"):
                q_clientes = f"""
                    SELECT COALESCE(fe.cliente, {name_expr}, '—') AS cliente,
                           COUNT(*)::int AS cantidad,
                           COALESCE(SUM(fe.monto_bruto),0)::float AS monto
                    FROM fin_eventos fe
                    JOIN leads l ON l.id_lead=fe.id_lead
                    WHERE {where_sql}
                      AND fe.fecha_evento IS NOT NULL
                      AND {conf_where}
                    GROUP BY 1
                    ORDER BY monto DESC
                    LIMIT {lim}
                """
                clientes = db.execute(text(q_clientes), params).mappings().all()
            else:
                q_clientes = f"""
                    SELECT {name_expr} AS cliente,
                           COUNT(*)::int AS cantidad,
                           COALESCE(SUM(l.monto_cotizado),0) AS monto
                    FROM leads l
                    WHERE {where_sql}
                      AND {conf_where}
                    GROUP BY {name_expr}
                    ORDER BY monto DESC
                    LIMIT {lim}
                """
                clientes = db.execute(text(q_clientes), params).mappings().all()
        except Exception:
            clientes = []

        comunas: list[dict] = []
        try:
            lim = max(5, min(100, int(top_n or 20)))
            if _table_exists_pg(db, "fin_eventos"):
                q_comunas = f"""
                    SELECT COALESCE(fe.comuna, c.nombre,'—') AS comuna,
                           COUNT(*)::int AS cantidad,
                           COALESCE(SUM(fe.monto_bruto),0)::float AS monto
                    FROM fin_eventos fe
                    JOIN leads l ON l.id_lead=fe.id_lead
                    LEFT JOIN comunas c ON c.id_comuna=l.id_comuna
                    WHERE {where_sql}
                      AND fe.fecha_evento IS NOT NULL
                      AND {conf_where}
                    GROUP BY 1
                    ORDER BY monto DESC
                    LIMIT {lim}
                """
                comunas = db.execute(text(q_comunas), params).mappings().all()
            else:
                q_comunas = f"""
                    SELECT COALESCE(c.nombre,'—') AS comuna,
                           COUNT(*)::int AS cantidad,
                           COALESCE(SUM(l.monto_cotizado),0) AS monto
                    FROM leads l
                    LEFT JOIN comunas c ON c.id_comuna=l.id_comuna
                    WHERE {where_sql}
                      AND {conf_where}
                    GROUP BY c.nombre
                    ORDER BY monto DESC
                    LIMIT {lim}
                """
                comunas = db.execute(text(q_comunas), params).mappings().all()
        except Exception:
            comunas = []

        # Tipo de cliente (empresa/particular u otros)
        tipos_rows: list[dict] = []
        tipos_rows_conf: list[dict] = []
        try:
            tipo_expr = None
            join_sql = ""
            if _col_exists(db, "leads", "id_tipo_cliente") and _table_exists_pg(db, "tipos_cliente"):
                tipo_expr = "COALESCE(tc.nombre,'—')"
                join_sql = "LEFT JOIN tipos_cliente tc ON tc.id_tipo_cliente = l.id_tipo_cliente"
            elif _col_exists(db, "leads", "tipo_cliente"):
                tipo_expr = "COALESCE(l.tipo_cliente,'—')"
            if tipo_expr:
                q_tipo = f"""
                    SELECT {tipo_expr} AS tipo_cliente,
                           COUNT(*)::int AS cantidad,
                           COALESCE(SUM(l.monto_cotizado),0)::float AS monto
                    FROM leads l
                    {join_sql}
                    WHERE {where_sql}
                    GROUP BY {tipo_expr}
                    ORDER BY cantidad DESC
                """
                tipos_rows = db.execute(text(q_tipo), params).mappings().all()

                q_tipo_conf = q_tipo.replace(f"WHERE {where_sql}", f"WHERE {where_sql} AND {conf_where}")
                tipos_rows_conf = db.execute(text(q_tipo_conf), params).mappings().all()
        except Exception:
            tipos_rows = []
            tipos_rows_conf = []

        # Venta por marca (torta) para el rango/confirmados
        ventas_por_marca: list[dict] = []
        try:
            if _table_exists_pg(db, "fin_eventos"):
                q_vm = f"""
                    SELECT COALESCE(m.nombre, m.marca,'—') AS marca,
                           COUNT(*)::int AS cantidad,
                           COALESCE(SUM(fe.monto_bruto),0)::float AS monto
                    FROM fin_eventos fe
                    JOIN leads l ON l.id_lead=fe.id_lead
                    LEFT JOIN marcas m ON m.id_marca=l.id_marca
                    WHERE {where_sql}
                      AND fe.fecha_evento IS NOT NULL
                      AND {conf_where}
                    GROUP BY COALESCE(m.nombre, m.marca,'—')
                    ORDER BY monto DESC
                """
                ventas_por_marca = db.execute(text(q_vm), params).mappings().all()
            else:
                q_vm = f"""
                    SELECT COALESCE(m.nombre, m.marca,'—') AS marca,
                           COUNT(*)::int AS cantidad,
                           COALESCE(SUM(l.monto_cotizado),0)::float AS monto
                    FROM leads l
                    LEFT JOIN marcas m ON m.id_marca=l.id_marca
                    WHERE {where_sql}
                      AND {conf_where}
                    GROUP BY COALESCE(m.nombre, m.marca,'—')
                    ORDER BY monto DESC
                """
                ventas_por_marca = db.execute(text(q_vm), params).mappings().all()
        except Exception:
            ventas_por_marca = []

        top_n_i = int(top_n or 20)
        top_n_i = max(5, min(100, top_n_i))
        prod_order = "monto" if str(productos_order or "").lower() not in ("cantidad", "qty", "count") else "cantidad"
        com_order = "monto" if str(comunas_order or "").lower() not in ("cantidad", "qty", "count") else "cantidad"
        cli_order = "monto" if str(clientes_order or "").lower() not in ("cantidad", "qty", "count") else "cantidad"

        top_productos: list[dict] = []
        try:
            if _table_exists_pg(db, "cotizacion_items") and _table_exists_pg(db, "cotizaciones"):
                cols_items = _cols_pg(db, "cotizacion_items")
                cols_cot = _cols_pg(db, "cotizaciones")
                prod_col = "producto" if "producto" in cols_items else ("nombre_producto" if "nombre_producto" in cols_items else None)
                qty_col = "cantidad" if "cantidad" in cols_items else None
                total_col = "total_linea" if "total_linea" in cols_items else ("subtotal" if "subtotal" in cols_items else None)
                date_cot = "fecha" if "fecha" in cols_cot else ("created_at" if "created_at" in cols_cot else "updated_at")
                has_id_lead = "id_lead" in cols_cot
                marca_expr = "COALESCE(i.marca, m.nombre, m.marca,'')" if "marca" in cols_items else "COALESCE(m.nombre, m.marca,'')"
                if prod_col and qty_col and total_col and has_id_lead:
                    q_prod = f"""
                        SELECT i.{prod_col} AS producto,
                               {marca_expr} AS marca,
                               SUM(COALESCE(i.{qty_col},0))::float AS cantidad,
                               SUM(COALESCE(i.{total_col},0))::float AS monto
                        FROM cotizacion_items i
                        JOIN cotizaciones c ON c.id_cotizacion=i.id_cotizacion
                        LEFT JOIN leads l ON l.id_lead=c.id_lead
                        LEFT JOIN marcas m ON m.id_marca=l.id_marca
                        WHERE 1=1
                          AND l.{date_col}::date BETWEEN :ini AND :fin
                          AND {conf_where}
                          {("AND l.id_marca = ANY(:marcas)" if (only_own and marcas) else "")}
                        GROUP BY i.{prod_col}, {marca_expr}
                        ORDER BY {prod_order} DESC
                        LIMIT {top_n_i}
                    """
                    try:
                        top_productos = db.execute(text(q_prod), params).mappings().all()
                    except Exception:
                        top_productos = []
            elif _table_exists_pg(db, "cotizaciones_detalle") and _table_exists_pg(db, "cotizaciones"):
                cols_det = _cols_pg(db, "cotizaciones_detalle")
                cols_cot = _cols_pg(db, "cotizaciones")
                prod_col = "nombre_producto" if "nombre_producto" in cols_det else ("producto" if "producto" in cols_det else None)
                qty_col = "cantidad" if "cantidad" in cols_det else None
                total_col = "subtotal" if "subtotal" in cols_det else None
                date_cot = "fecha" if "fecha" in cols_cot else ("created_at" if "created_at" in cols_cot else "updated_at")
                has_id_lead = "id_lead" in cols_cot
                if prod_col and qty_col and total_col and has_id_lead:
                    q_prod = f"""
                        SELECT d.{prod_col} AS producto,
                               COALESCE(m.nombre, m.marca,'') AS marca,
                               SUM(COALESCE(d.{qty_col},0))::float AS cantidad,
                               SUM(COALESCE(d.{total_col},0))::float AS monto
                        FROM cotizaciones_detalle d
                        JOIN cotizaciones c ON c.id_cotizacion=d.id_cotizacion
                        LEFT JOIN leads l ON l.id_lead=c.id_lead
                        LEFT JOIN marcas m ON m.id_marca=l.id_marca
                        WHERE 1=1
                          AND l.{date_col}::date BETWEEN :ini AND :fin
                          AND {conf_where}
                          {("AND l.id_marca = ANY(:marcas)" if (only_own and marcas) else "")}
                        GROUP BY d.{prod_col}, COALESCE(m.nombre, m.marca,'')
                        ORDER BY {prod_order} DESC
                        LIMIT {top_n_i}
                    """
                    try:
                        top_productos = db.execute(text(q_prod), params).mappings().all()
                    except Exception:
                        top_productos = []
        except Exception:
            top_productos = []

        # Reordenar comunas/clientes si pidieron por cantidad
        try:
            if com_order == "cantidad" and comunas:
                comunas = sorted(list(comunas), key=lambda x: (int(x.get("cantidad") or 0), float(x.get("monto") or 0)), reverse=True)[:top_n_i]
            else:
                comunas = list(comunas)[:top_n_i]
        except Exception:
            comunas = list(comunas)[:top_n_i] if comunas else []

        try:
            if cli_order == "cantidad" and clientes:
                clientes = sorted(list(clientes), key=lambda x: (int(x.get("cantidad") or 0), float(x.get("monto") or 0)), reverse=True)[:top_n_i]
            else:
                clientes = list(clientes)[:top_n_i]
        except Exception:
            clientes = list(clientes)[:top_n_i] if clientes else []

        # KPIs + comparativo vs venta_base/meta (metas_marca_mensual) del mes del rango.
        _ensure_baseline(db)
        try:
            _ensure_metas(db)
        except Exception:
            pass

        leads_total = 0
        confirmados_total = 0
        cierre_pct = 0.0
        try:
            leads_total = int(db.execute(text(f"SELECT COUNT(*)::int FROM leads l WHERE {where_sql}"), params).scalar_one() or 0)
            confirmados_total = int(
                db.execute(text(f"SELECT COUNT(*)::int FROM leads l WHERE {where_sql} AND {conf_where}"), params).scalar_one() or 0
            )
            cierre_pct = (confirmados_total / leads_total * 100.0) if leads_total else 0.0
        except Exception:
            leads_total = 0
            confirmados_total = 0
            cierre_pct = 0.0

        venta_total = 0.0
        try:
            if _table_exists_pg(db, "fin_eventos"):
                venta_total = float(
                    db.execute(
                        text(
                            f"""
                            SELECT COALESCE(SUM(fe.monto_bruto),0)::float
                            FROM fin_eventos fe
                            JOIN leads l ON l.id_lead=fe.id_lead
                            WHERE {where_sql}
                              AND fe.fecha_evento IS NOT NULL
                              AND {conf_where}
                            """
                        ),
                        params,
                    ).scalar_one()
                    or 0
                )
            else:
                venta_total = float(
                    db.execute(
                        text(f"SELECT COALESCE(SUM(l.monto_cotizado),0)::float FROM leads l WHERE {where_sql} AND {conf_where}"),
                        params,
                    ).scalar_one()
                    or 0
                )
        except Exception:
            venta_total = 0.0

        ventas_comparativo: list[dict] = []
        try:
            ym_year = int(ini_d.year)
            ym_month = int(ini_d.month)

            metas_map: dict[int, dict] = {}
            if _table_exists_pg(db, "metas_marca_mensual"):
                mrows = db.execute(
                    text(
                        """
                        SELECT id_marca,
                               COALESCE(venta_base,0)::float AS venta_base,
                               COALESCE(meta,0)::float AS meta
                        FROM metas_marca_mensual
                        WHERE year=:y AND month=:m
                        """
                    ),
                    {"y": ym_year, "m": ym_month},
                ).mappings().all()
                for r in mrows:
                    try:
                        metas_map[int(r["id_marca"])] = {
                            "venta_base": float(r.get("venta_base") or 0),
                            "meta": float(r.get("meta") or 0),
                        }
                    except Exception:
                        continue

            baseline_map: dict[str, float] = {}
            try:
                brows = db.execute(
                    text("SELECT marca, COALESCE(monto,0)::float AS monto FROM ventas_baseline WHERE mes=:m"),
                    {"m": ym_month},
                ).mappings().all()
                for r in brows:
                    baseline_map[str(r.get("marca") or "").strip().upper()] = float(r.get("monto") or 0)
            except Exception:
                baseline_map = {}

            actual_by_marca: dict[int, float] = {}
            if _table_exists_pg(db, "fin_eventos"):
                arows = db.execute(
                    text(
                        f"""
                        SELECT l.id_marca, COALESCE(SUM(fe.monto_bruto),0)::float AS monto
                        FROM fin_eventos fe
                        JOIN leads l ON l.id_lead=fe.id_lead
                        WHERE {where_sql}
                          AND fe.fecha_evento IS NOT NULL
                          AND {conf_where}
                        GROUP BY l.id_marca
                        """
                    ),
                    params,
                ).mappings().all()
                for r in arows:
                    if r.get("id_marca") is None:
                        continue
                    actual_by_marca[int(r["id_marca"])] = float(r.get("monto") or 0)
            else:
                arows = db.execute(
                    text(
                        f"""
                        SELECT l.id_marca, COALESCE(SUM(l.monto_cotizado),0)::float AS monto
                        FROM leads l
                        WHERE {where_sql} AND {conf_where}
                        GROUP BY l.id_marca
                        """
                    ),
                    params,
                ).mappings().all()
                for r in arows:
                    if r.get("id_marca") is None:
                        continue
                    actual_by_marca[int(r["id_marca"])] = float(r.get("monto") or 0)

            brands = db.execute(text("SELECT id_marca, COALESCE(nombre, marca,'') AS marca FROM marcas")).mappings().all()
            for b in brands:
                try:
                    mid = int(b.get("id_marca") or 0)
                except Exception:
                    continue
                if mid <= 0:
                    continue
                if only_own and marcas and (mid not in set(marcas)):
                    continue
                if id_marca and mid != int(id_marca):
                    continue
                name = str(b.get("marca") or "").strip().upper()
                actual = float(actual_by_marca.get(mid, 0.0))
                base = float(metas_map.get(mid, {}).get("venta_base") or baseline_map.get(name, 0.0) or 0.0)
                meta = float(metas_map.get(mid, {}).get("meta") or (base * 1.12 if base else 0.0))
                share = (actual / venta_total * 100.0) if venta_total else 0.0
                vs_base = ((actual / base - 1.0) * 100.0) if base else None
                vs_meta = ((actual / meta) * 100.0) if meta else None
                ventas_comparativo.append(
                    {
                        "id_marca": mid,
                        "marca": name,
                        "actual": round(actual, 2),
                        "base": round(base, 2),
                        "meta": round(meta, 2),
                        "share_pct": round(share, 2),
                        "vs_base_pct": (round(vs_base, 2) if vs_base is not None else None),
                        "vs_meta_pct": (round(vs_meta, 2) if vs_meta is not None else None),
                    }
                )
            ventas_comparativo.sort(key=lambda x: float(x.get("actual") or 0), reverse=True)
        except Exception:
            ventas_comparativo = []

        return {
            "ok": True,
            "range": {"from": ini_d.isoformat(), "to": fin_d.isoformat(), "periodo": (p or "range")},
            "range_ly": {"from": ly_ini.isoformat(), "to": ly_fin.isoformat()},
            "kpis": {
                "venta_total": round(float(venta_total or 0), 2),
                "leads_total": int(leads_total or 0),
                "confirmados_total": int(confirmados_total or 0),
                "cierre_pct": round(float(cierre_pct or 0), 2),
            },
            "ventas_comparativo": list(ventas_comparativo),
            "funnel": list(funnel),
            "eventos_diarios": list(eventos_diarios),
            "top_productos": list(top_productos),
            "clientes": list(clientes),
            "comunas": list(comunas),
            "tipo_cliente": list(tipos_rows),
            "tipo_cliente_confirmados": list(tipos_rows_conf),
            "ventas_por_marca": list(ventas_por_marca),
            "params": {
                "top_n": top_n_i,
                "productos_order": prod_order,
                "comunas_order": com_order,
                "clientes_order": cli_order,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        # "Never 500": respuesta mínima para que el frontend no caiga.
        return {
            "ok": True,
            "funnel": [],
            "eventos_diarios": [],
            "top_productos": [],
            "clientes": [],
            "comunas": [],
            "tipo_cliente": [],
            "tipo_cliente_confirmados": [],
            "ventas_por_marca": [],
            "_error": str(e),
        }


@router.get("/dashboard/leads_hoy")
def dashboard_leads_hoy(
    id_marca: int | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    me=Depends(get_current_user),
):
    """
    Lista de leads creados hoy (para Reportes). Visible según permisos (admin ve todo; no-admin solo sus marcas).
    """
    role = (me.get("role") or me.get("rol") or "").upper()
    marcas = _fetch_marcas_ids(db, me)
    only_own = not _is_admin(role)

    tz = ZoneInfo("America/Santiago")
    today = datetime.now(tz).date()

    # date col
    if _col_exists(db, "leads", "fecha_ingreso"):
        date_col = "fecha_ingreso"
    elif _col_exists(db, "leads", "created_at"):
        date_col = "created_at"
    else:
        date_col = "updated_at"

    lim = int(limit or 50)
    lim = max(10, min(200, lim))

    marca_sql = ""
    params: dict[str, Any] = {"d": str(today), "lim": lim}
    if only_own and marcas:
        marca_sql = " AND l.id_marca = ANY(:marcas) "
        params["marcas"] = marcas
    if id_marca:
        try:
            mid = int(id_marca)
        except Exception:
            mid = 0
        if mid > 0:
            if only_own and marcas and (mid not in set(marcas)):
                raise HTTPException(status_code=403, detail="No autorizado para ver esta marca")
            marca_sql += " AND l.id_marca = :id_marca "
            params["id_marca"] = mid

    name_expr = _lead_name_expr(db)
    tel_expr = _lead_col(db, "telefono")
    monto_expr = _lead_col(db, "monto_cotizado")

    rows = db.execute(
        text(
            f"""
            SELECT l.id_lead::bigint AS id_lead,
                   {name_expr} AS cliente,
                   COALESCE(m.nombre, m.marca,'') AS marca,
                   COALESCE(c.nombre,'—') AS comuna,
                   COALESCE(e.nombre,'') AS estado,
                   {tel_expr} AS telefono,
                   COALESCE({monto_expr},0)::float AS monto
            FROM leads l
            LEFT JOIN marcas m ON m.id_marca=l.id_marca
            LEFT JOIN comunas c ON c.id_comuna=l.id_comuna
            LEFT JOIN estados_lead e ON e.id_estado=l.id_estado
            WHERE DATE(l.{date_col}) = :d
            {marca_sql}
            ORDER BY l.id_lead DESC
            LIMIT :lim
            """
        ),
        params,
    ).mappings().all()
    return {"ok": True, "date": str(today), "items": list(rows)}


@router.get("/dashboard/sales_ids")
def dashboard_sales_ids(
    desde: str,
    hasta: str,
    id_marca: int | None = None,
    db: Session = Depends(get_db),
    me=Depends(get_current_user),
):
    role = (me.get("role") or me.get("rol") or "").upper()
    marcas = _fetch_marcas_ids(db, me)
    only_own = not _is_admin(role)

    try:
        d1 = date.fromisoformat(str(desde)[:10])
        d2 = date.fromisoformat(str(hasta)[:10])
    except Exception:
        raise HTTPException(status_code=400, detail="Fechas inválidas (usa YYYY-MM-DD)")
    if d2 < d1:
        d1, d2 = d2, d1

    if only_own and not marcas:
        return {"ok": True, "ids": []}

    marca_sql = ""
    params: dict[str, Any] = {"d1": d1, "d2": d2, "tz": "America/Santiago"}
    if only_own and marcas:
        marca_sql = " AND l.id_marca = ANY(:marcas) "
        params["marcas"] = marcas
        if id_marca and int(id_marca) in set(marcas):
            marca_sql += " AND l.id_marca = :id_marca "
            params["id_marca"] = int(id_marca)
        elif id_marca:
            raise HTTPException(status_code=403, detail="No autorizado para ver esta marca")
    elif id_marca:
        try:
            mid = int(id_marca)
        except Exception:
            mid = 0
        if mid > 0:
            marca_sql = " AND l.id_marca = :id_marca "
            params["id_marca"] = mid

    if not _table_exists_pg(db, "activity_log"):
        return {"ok": True, "ids": []}

    rows = db.execute(
        text(
            f"""
            WITH conf AS (
              SELECT entity_id::bigint AS id_lead,
                     MAX(created_at AT TIME ZONE :tz) AS confirmed_local
              FROM public.activity_log
              WHERE entity_type='lead'
                AND action IN ('EVENT_CONFIRMED_AGENDED')
                AND entity_id IS NOT NULL
              GROUP BY entity_id
            )
            SELECT l.id_lead::bigint AS id_lead
            FROM conf c
            JOIN leads l ON l.id_lead=c.id_lead
            WHERE c.confirmed_local::date BETWEEN :d1 AND :d2
            {marca_sql}
            ORDER BY c.confirmed_local DESC NULLS LAST, l.id_lead DESC
            LIMIT 2000
            """
        ),
        params,
    ).fetchall()
    ids = [int(r[0]) for r in rows if r and str(r[0] or "").isdigit()]
    return {"ok": True, "ids": ids}


@router.get("/dashboard/events")
def dashboard_events(
    week_offset: int = 0,
    id_marca: int | None = None,
    db: Session = Depends(get_db),
    me=Depends(get_current_user),
):
    tz = ZoneInfo("America/Santiago")
    now = datetime.now(tz)
    today = now.date()
    week_start = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
    week_end = week_start + timedelta(days=6)
    week_num = week_start.isocalendar().week

    # Nota (producto): los eventos confirmados de la semana se muestran para todos los usuarios.
    # No aplicamos restricciones por "marcas" del usuario (solo filtramos si se solicita id_marca).

    if _col_exists(db, "leads", "fecha_ingreso"):
        date_col = "fecha_ingreso"
    elif _col_exists(db, "leads", "created_at"):
        date_col = "created_at"
    else:
        date_col = "fecha_evento"
    name_expr = _lead_name_expr(db)
    pre_start_expr = _lead_col(db, "pre_start")
    pre_end_expr = _lead_col(db, "pre_end")
    pre_location_expr = _lead_col(db, "pre_location")
    pre_title_expr = _lead_col(db, "pre_title")
    pre_products_expr = _lead_col(db, "pre_products_text")
    pre_ops_expr = _lead_col(db, "pre_ops")
    cal_expr = _lead_col(db, "calendar_html_link")
    confirmado_id = _estado_id(db, "CONFIRM")

    params = {"ws": week_start, "we": week_end}
    marca_sql = ""
    if id_marca:
        try:
            mid = int(id_marca)
        except Exception:
            mid = 0
        if mid > 0:
            marca_sql = " AND l.id_marca = :id_marca "
            params["id_marca"] = mid

    conf_where = ""
    if confirmado_id:
        conf_where = "l.id_estado=:conf"
        params["conf"] = confirmado_id
    else:
        conf_where = "EXISTS (SELECT 1 FROM estados_lead e WHERE e.id_estado=l.id_estado AND UPPER(e.nombre) LIKE :confname)"
        params["confname"] = "%CONFIRM%"

    q_ev = f"""
        SELECT l.id_lead, {name_expr} AS cliente, l.fecha_evento,
               {pre_start_expr} AS pre_start, {pre_end_expr} AS pre_end, {pre_location_expr} AS pre_location,
               {pre_products_expr} AS pre_products_text,
               {pre_ops_expr} AS pre_ops,
               {cal_expr} AS calendar_html_link,
               l.calendar_event_id,
               {pre_title_expr} AS pre_title,
               COALESCE(m.nombre,m.marca,'') AS marca,
               COALESCE(c.nombre,'') AS comuna
        FROM leads l
        LEFT JOIN marcas m ON m.id_marca=l.id_marca
        LEFT JOIN comunas c ON c.id_comuna=l.id_comuna
        WHERE {conf_where}
          AND l.fecha_evento BETWEEN :ws AND :we
        {marca_sql}
        ORDER BY l.fecha_evento ASC, l.id_lead DESC
    """
    rows = db.execute(text(q_ev), params).mappings().all()

    def _n(s: str | None) -> str:
        raw = (s or "").strip().lower()
        raw = unicodedata.normalize("NFD", raw)
        raw = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
        return re.sub(r"[^a-z0-9]+", "", raw)

    def _title_key(title: str | None, marca: str | None) -> str:
        t = _n(title)
        m = _n(marca)
        if m and t:
            t = t.replace(m, "")
        return t

    events_by_key: dict[tuple, dict] = {}
    for r in rows:
        title = r.get("pre_title") or r.get("cliente")
        if re.search(r"\b(vacaciones|cumplea(?:n|ñ)os|feriado|holiday)\b", (title or "").lower()):
            continue
        key = (
            str(r.get("fecha_evento") or ""),
            _title_key(title, r.get("marca")),
        )
        score = (
            1 if r.get("calendar_event_id") else 0,
            1 if (r.get("calendar_html_link") or "") else 0,
            1 if r.get("pre_title") else 0,
        )
        item = {
            "id_lead": r.get("id_lead"),
            "cliente": title,
            "fecha_evento": str(r.get("fecha_evento")) if r.get("fecha_evento") else None,
            "marca": r.get("marca"),
            "comuna": r.get("comuna"),
            "pre_start": r.get("pre_start"),
            "pre_end": r.get("pre_end"),
            "pre_products_text": r.get("pre_products_text") or "",
            "pre_ops": r.get("pre_ops"),
            "calendar_html_link": r.get("calendar_html_link") or "",
            "calendar_event_id": r.get("calendar_event_id") or "",
        }
        prev = events_by_key.get(key)
        if not prev:
            events_by_key[key] = {**item, "_score": score}
        else:
            if score > prev["_score"]:
                events_by_key[key] = {**item, "_score": score}
    events = []
    for v in events_by_key.values():
        v.pop("_score", None)
        events.append(v)
    return {"ok": True, "week": {"start": str(week_start), "end": str(week_end), "number": week_num}, "events": events}


@router.get("/calendar/events_list")
def calendar_events_list(
    from_date: str | None = None,
    to_date: str | None = None,
    q: str | None = None,
    id_marca: int | None = None,
    limit: int = 500,
    offset: int = 0,
    db: Session = Depends(get_db),
    me=Depends(get_current_user),
):
    """
    Lista "Eventos en Calendar" (para módulo de ejecutivos):
    - Ejecutivos: solo sus marcas.
    - Admin: todo (y puede filtrar por marca).

    Devuelve data suficiente para abrir lead y editar confirmado (/tools/agenda/{id}/edit_confirmed).
    """
    role = (me.get("role") or me.get("rol") or "").upper().strip()
    is_admin = _is_admin(role)
    is_exec = ("EJECUTIVO" in role) or (role == "2")
    if not (is_admin or is_exec):
        raise HTTPException(status_code=403, detail="No autorizado")

    marcas = _fetch_marcas_ids(db, me)
    only_own = not is_admin
    if only_own and not marcas:
        return {"ok": True, "items": [], "count": 0}

    tz = ZoneInfo("America/Santiago")
    today = datetime.now(tz).date()
    try:
        d1 = date.fromisoformat(str(from_date)[:10]) if from_date else (today - timedelta(days=7))
    except Exception:
        raise HTTPException(status_code=400, detail="from_date inválida (YYYY-MM-DD)")
    try:
        d2 = date.fromisoformat(str(to_date)[:10]) if to_date else (today + timedelta(days=90))
    except Exception:
        raise HTTPException(status_code=400, detail="to_date inválida (YYYY-MM-DD)")
    if d2 < d1:
        d1, d2 = d2, d1
    # No mostramos eventos pasados (y tampoco se deben editar).
    if d1 < today:
        d1 = today

    lim = max(50, min(1000, int(limit or 500)))
    off = max(0, int(offset or 0))

    name_expr = _lead_name_expr(db)
    tel_expr = _lead_col(db, "telefono")
    dir_expr = _lead_col(db, "direccion")
    pre_start_expr = _lead_col(db, "pre_start")
    pre_end_expr = _lead_col(db, "pre_end")
    pre_desc_expr = _lead_col(db, "pre_description")
    cal_start_expr = _lead_col(db, "calendar_start")
    cal_end_expr = _lead_col(db, "calendar_end")
    cal_link_expr = _lead_col(db, "calendar_html_link")
    cal_eid_expr = _lead_col(db, "calendar_event_id")
    pre_title_expr = _lead_col(db, "pre_title")
    pre_loc_expr = _lead_col(db, "pre_location")
    pre_ops_expr = _lead_col(db, "pre_ops")
    pre_montaje_expr = _lead_col(db, "pre_montaje_text")

    where = [
        "(l.calendar_start IS NOT NULL OR l.calendar_html_link IS NOT NULL OR l.calendar_event_id IS NOT NULL OR l.agenda_approved_at IS NOT NULL)",
        "l.fecha_evento IS NOT NULL",
        "l.fecha_evento::date BETWEEN :d1 AND :d2",
    ]
    params: dict[str, Any] = {"d1": d1, "d2": d2, "lim": lim, "off": off}

    if only_own and marcas:
        where.append("l.id_marca = ANY(:marcas)")
        params["marcas"] = marcas

    if id_marca:
        try:
            mid = int(id_marca)
        except Exception:
            mid = 0
        if mid > 0:
            if only_own and marcas and (mid not in set(marcas)):
                raise HTTPException(status_code=403, detail="No autorizado para ver esta marca")
            where.append("l.id_marca = :id_marca")
            params["id_marca"] = mid

    if q and str(q).strip():
        qq = str(q).strip()
        params["q"] = f"%{qq}%"
        where.append(
            "("
            "CAST(l.id_lead AS text) ILIKE :q "
            f"OR {name_expr} ILIKE :q "
            f"OR {pre_title_expr} ILIKE :q "
            "OR COALESCE(c.nombre,'') ILIKE :q "
            "OR COALESCE(m.nombre,m.marca,'') ILIKE :q "
            ")"
        )

    where_sql = " AND ".join(where)

    rows = db.execute(
        text(
            f"""
            SELECT
              l.id_lead::bigint AS id_lead,
              {name_expr} AS cliente,
              l.fecha_evento,
              COALESCE(m.nombre,m.marca,'') AS marca,
              COALESCE(c.nombre,'') AS comuna,
              {tel_expr} AS telefono,
              {dir_expr} AS direccion,
              {pre_start_expr} AS pre_start,
              {pre_end_expr} AS pre_end,
              {pre_desc_expr} AS pre_description,
              {pre_montaje_expr} AS pre_montaje_text,
              {pre_title_expr} AS pre_title,
              {pre_loc_expr} AS pre_location,
              {pre_ops_expr} AS pre_ops,
              {cal_start_expr} AS start_at,
              {cal_end_expr} AS end_at,
              {cal_link_expr} AS calendar_html_link,
              {cal_eid_expr} AS calendar_event_id
            FROM public.leads l
            LEFT JOIN public.marcas m ON m.id_marca=l.id_marca
            LEFT JOIN public.comunas c ON c.id_comuna=l.id_comuna
            WHERE {where_sql}
            ORDER BY
              (
                (CASE WHEN NULLIF(btrim(COALESCE({dir_expr}::text,'')),'') IS NULL THEN 1 ELSE 0 END)
                + (CASE WHEN NULLIF(btrim(COALESCE({tel_expr}::text,'')),'') IS NULL THEN 1 ELSE 0 END)
                + (CASE WHEN ({pre_start_expr} IS NULL OR {pre_end_expr} IS NULL) THEN 1 ELSE 0 END)
              ) DESC,
              l.fecha_evento ASC,
              l.calendar_start ASC NULLS LAST,
              l.id_lead DESC
            LIMIT :lim OFFSET :off
            """
        ),
        params,
    ).mappings().all()

    return {"ok": True, "from": str(d1), "to": str(d2), "items": list(rows), "count": len(rows)}


# =========================
# MICE & PLACE (Reporte Cocina/Compras)
# =========================
def _ensure_ops_recetas_tables(db: Session) -> None:
    try:
        db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS recetas (
                  id_receta SERIAL PRIMARY KEY,
                  producto TEXT NOT NULL,
                  marca TEXT,
                  rendimiento NUMERIC(10,2),
                  merma_pct NUMERIC(5,2),
                  costos_extra NUMERIC(12,2),
                  unidad_base TEXT,
                  es_sub_receta BOOLEAN NOT NULL DEFAULT FALSE,
                  is_active BOOLEAN NOT NULL DEFAULT TRUE,
                  created_at TIMESTAMP DEFAULT now(),
                  updated_at TIMESTAMP DEFAULT now()
                )
                """
            )
        )
        db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS receta_items (
                  id_item SERIAL PRIMARY KEY,
                  id_receta INT NOT NULL REFERENCES recetas(id_receta) ON DELETE CASCADE,
                  ingrediente TEXT NOT NULL,
                  cantidad NUMERIC(12,4),
                  unidad TEXT,
                  costo_unitario NUMERIC(12,4),
                  sub_receta_id INT,
                  created_at TIMESTAMP DEFAULT now()
                )
                """
            )
        )
        db.commit()
    except Exception:
        db.rollback()


def _ensure_lead_mice_items_pg(db: Session) -> None:
    try:
        db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS lead_mice_items (
                  id_item  SERIAL PRIMARY KEY,
                  id_lead  INTEGER NOT NULL REFERENCES leads(id_lead) ON DELETE CASCADE,
                  producto TEXT NOT NULL,
                  cantidad NUMERIC(12,3) NOT NULL DEFAULT 0,
                  service_date DATE,
                  created_at TIMESTAMP DEFAULT now(),
                  created_by TEXT
                )
                """
            )
        )
        db.execute(text("CREATE INDEX IF NOT EXISTS idx_lead_mice_items_lead_day ON lead_mice_items(id_lead, service_date)"))
        db.commit()
    except Exception:
        db.rollback()


def _mice_manual_by_day(db: Session, id_lead: int) -> List[Dict[str, Any]]:
    _ensure_lead_mice_items_pg(db)
    rows = db.execute(
        text(
            """
            SELECT service_date, producto, SUM(cantidad)::float AS cantidad
            FROM lead_mice_items
            WHERE id_lead=:id
            GROUP BY service_date, producto
            ORDER BY service_date NULLS FIRST, producto
            """
        ),
        {"id": id_lead},
    ).mappings().all()
    out: List[Dict[str, Any]] = []
    for r in rows:
        p = str(r.get("producto") or "").strip()
        if not p:
            continue
        try:
            qty = float(r.get("cantidad") or 0)
        except Exception:
            qty = 0.0
        if qty <= 0:
            continue
        sd = r.get("service_date")
        sd_s = str(sd)[:10] if sd else None
        out.append({"service_date": sd_s, "producto": p, "cantidad": qty})
    return out


def _cot_items(db: Session, id_cot: int) -> List[Dict[str, Any]]:
    if not _table_exists_pg(db, "cotizacion_items"):
        return []
    rows = db.execute(
        text(
            """
            SELECT producto, SUM(cantidad)::float AS cantidad
            FROM cotizacion_items
            WHERE id_cotizacion=:id
            GROUP BY producto
            ORDER BY producto
            """
        ),
        {"id": id_cot},
    ).mappings().all()
    out: List[Dict[str, Any]] = []
    for r in rows:
        p = str(r.get("producto") or "").strip()
        if not p:
            continue
        out.append({"producto": p, "cantidad": float(r.get("cantidad") or 0)})
    return out


def _lead_best_cot_id(db: Session, lead_row: dict) -> Optional[int]:
    cols = _cols_pg(db, "leads")
    if "id_cotizacion_vigente" in cols and lead_row.get("id_cotizacion_vigente"):
        try:
            return int(lead_row.get("id_cotizacion_vigente"))
        except Exception:
            pass
    if _table_exists_pg(db, "cotizaciones"):
        cid = db.execute(
            text("SELECT id_cotizacion FROM cotizaciones WHERE id_lead=:id ORDER BY id_cotizacion DESC LIMIT 1"),
            {"id": int(lead_row.get("id_lead") or 0)},
        ).scalar()
        if cid is not None:
            try:
                return int(cid)
            except Exception:
                return None
    return None


def _load_receta(db: Session, producto: str, marca: str) -> Optional[Dict[str, Any]]:
    _ensure_ops_recetas_tables(db)
    row = db.execute(
        text(
            """
            SELECT id_receta,
                   COALESCE(rendimiento, 1)::float AS rendimiento,
                   COALESCE(merma_pct, 0)::float AS merma_pct,
                   COALESCE(unidad_base,'') AS unidad_base
            FROM recetas
            WHERE is_active = TRUE
              AND UPPER(producto)=UPPER(:p)
              AND UPPER(COALESCE(marca,''))=UPPER(:m)
            ORDER BY id_receta DESC
            LIMIT 1
            """
        ),
        {"p": (producto or "").strip(), "m": (marca or "").strip()},
    ).mappings().first()
    return dict(row) if row else None


def _receta_items(db: Session, id_receta: int) -> List[Dict[str, Any]]:
    _ensure_ops_recetas_tables(db)
    rows = db.execute(
        text(
            """
            SELECT ingrediente, COALESCE(cantidad,0)::float AS cantidad, COALESCE(unidad,'') AS unidad, sub_receta_id
            FROM receta_items
            WHERE id_receta=:id
            ORDER BY ingrediente
            """
        ),
        {"id": int(id_receta)},
    ).mappings().all()
    out = []
    for r in rows:
        ing = str(r.get("ingrediente") or "").strip()
        if not ing:
            continue
        out.append(
            {
                "ingrediente": ing,
                "cantidad": float(r.get("cantidad") or 0),
                "unidad": str(r.get("unidad") or "").strip(),
                "sub_receta_id": r.get("sub_receta_id"),
            }
        )
    return out


def _expand_ingredientes_for_producto(
    db: Session,
    producto: str,
    marca: str,
    unidades: float,
    *,
    _seen: Optional[set[int]] = None,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    _seen = _seen or set()
    rec = _load_receta(db, producto, marca)
    if not rec:
        return ([], "no_receta")
    rid = int(rec.get("id_receta") or 0)
    if rid in _seen:
        return ([], "ciclo_subreceta")
    _seen.add(rid)

    rendimiento = float(rec.get("rendimiento") or 1.0) or 1.0
    merma_pct = float(rec.get("merma_pct") or 0.0) or 0.0
    if merma_pct < 0:
        merma_pct = 0.0
    if merma_pct > 95:
        merma_pct = 95.0
    effective_yield = rendimiento * (1.0 - (merma_pct / 100.0))
    if effective_yield <= 0:
        effective_yield = 1.0
    factor = float(unidades or 0) / effective_yield

    out: List[Dict[str, Any]] = []
    for it in _receta_items(db, rid):
        qty = float(it.get("cantidad") or 0.0) * factor
        if qty <= 0:
            continue
        sub_id = it.get("sub_receta_id")
        if sub_id:
            try:
                sub_id_i = int(sub_id)
            except Exception:
                sub_id_i = 0
            if sub_id_i > 0:
                sub_rec = db.execute(
                    text("SELECT producto, COALESCE(marca,'') AS marca FROM recetas WHERE id_receta=:id"),
                    {"id": sub_id_i},
                ).mappings().first()
                if sub_rec:
                    sub_prod = str(sub_rec.get("producto") or "").strip()
                    sub_marca = str(sub_rec.get("marca") or "").strip() or marca
                    sub_items, _ = _expand_ingredientes_for_producto(db, sub_prod, sub_marca, qty, _seen=_seen)
                    out.extend(sub_items)
                    continue
        out.append({"ingrediente": it["ingrediente"], "unidad": it.get("unidad") or "", "cantidad": qty})
    return (out, None)


@router.get("/mice/day")
def mice_day_report(
    day: str,
    include_ingredients: int = 1,
    db: Session = Depends(get_db),
    me=Depends(get_current_user),
):
    role = (me.get("role") or me.get("rol") or "").upper()
    if not _is_admin(role) and role not in ("JEFE DE OPERACIONES", "OPERACIONES", "COMPRAS", "MICE", "BODEGUERO"):
        raise HTTPException(403, detail="No autorizado")

    day_s = str(day or "").strip()[:10]
    try:
        day_d = date.fromisoformat(day_s)
    except Exception:
        raise HTTPException(400, detail="day debe ser YYYY-MM-DD")

    inc_ing = True
    try:
        inc_ing = bool(int(include_ingredients))
    except Exception:
        inc_ing = True

    confirmado_id = _estado_id(db, "CONFIRM")
    name_expr = _lead_name_expr(db)

    params: Dict[str, Any] = {"d": day_d}
    if confirmado_id:
        conf_where = "l.id_estado=:conf"
        params["conf"] = confirmado_id
    else:
        conf_where = "EXISTS (SELECT 1 FROM estados_lead e WHERE e.id_estado=l.id_estado AND UPPER(e.nombre) LIKE :confname)"
        params["confname"] = "%CONFIRM%"

    cols_leads = _cols_pg(db, "leads")
    cot_sel = "l.id_cotizacion_vigente" if "id_cotizacion_vigente" in cols_leads else "NULL::int AS id_cotizacion_vigente"

    rows = db.execute(
        text(
            f"""
            SELECT l.id_lead,
                   {name_expr} AS cliente,
                   l.fecha_evento,
                   {cot_sel},
                   COALESCE(m.nombre,m.marca,'') AS marca,
                   COALESCE(c.nombre,'') AS comuna
            FROM leads l
            LEFT JOIN marcas m ON m.id_marca=l.id_marca
            LEFT JOIN comunas c ON c.id_comuna=l.id_comuna
            WHERE {conf_where}
              AND l.fecha_evento = :d
            ORDER BY l.id_lead DESC
            """
        ),
        params,
    ).mappings().all()

    leads_out: List[Dict[str, Any]] = []
    totals: Dict[str, float] = {}
    missing_recetas: List[Dict[str, Any]] = []
    ing_totals: Dict[Tuple[str, str], float] = {}

    for r in rows:
        lead = dict(r)
        id_lead = int(lead.get("id_lead") or 0)
        marca = str(lead.get("marca") or "").strip()

        manual = _mice_manual_by_day(db, id_lead)
        items: List[Dict[str, Any]] = []
        manual_days = {x.get("service_date") for x in manual if x.get("service_date")}
        if manual_days:
            items = [{"producto": x["producto"], "cantidad": float(x["cantidad"])} for x in manual if x.get("service_date") == day_s]
            src = "manual"
        else:
            items = [{"producto": x["producto"], "cantidad": float(x["cantidad"])} for x in manual]
            src = "manual" if items else ""

        if not items:
            cid = _lead_best_cot_id(db, lead)
            if cid:
                items = _cot_items(db, cid)
                src = "cotizador"
        if not items:
            src = "none"

        ing_ev: Dict[Tuple[str, str], float] = {}
        miss_ev: List[Dict[str, Any]] = []
        breakdown: List[Dict[str, Any]] = []

        for it in items:
            p = str(it.get("producto") or "").strip()
            if not p:
                continue
            try:
                qn = float(it.get("cantidad") or 0)
            except Exception:
                qn = 0.0
            if qn <= 0:
                continue
            totals[p] = totals.get(p, 0.0) + qn

            if inc_ing:
                ing, miss = _expand_ingredientes_for_producto(db, p, marca, qn)
                if miss:
                    rec_miss = {"producto": p, "marca": marca, "reason": miss, "id_lead": id_lead}
                    missing_recetas.append(rec_miss)
                    miss_ev.append(rec_miss)
                    breakdown.append({"producto": p, "cantidad": qn, "ingredientes": []})
                    continue
                for ii in ing:
                    key = (str(ii.get("ingrediente") or "").strip(), str(ii.get("unidad") or "").strip())
                    try:
                        ing_totals[key] = ing_totals.get(key, 0.0) + float(ii.get("cantidad") or 0)
                        ing_ev[key] = ing_ev.get(key, 0.0) + float(ii.get("cantidad") or 0)
                    except Exception:
                        pass
                breakdown.append(
                    {
                        "producto": p,
                        "cantidad": qn,
                        "ingredientes": [
                            {
                                "ingrediente": str(ii.get("ingrediente") or "").strip(),
                                "unidad": str(ii.get("unidad") or "").strip(),
                                "cantidad": float(ii.get("cantidad") or 0),
                            }
                            for ii in ing
                            if str(ii.get("ingrediente") or "").strip()
                        ],
                    }
                )
            else:
                breakdown.append({"producto": p, "cantidad": qn, "ingredientes": []})

        leads_out.append(
            {
                "id_lead": id_lead,
                "cliente": lead.get("cliente"),
                "marca": marca,
                "comuna": lead.get("comuna"),
                "items_source": src,
                "items": items,
                "breakdown": breakdown,
                "ingredients": [
                    {"ingrediente": k[0], "unidad": k[1], "cantidad": float(v)}
                    for k, v in sorted(ing_ev.items(), key=lambda x: (x[0][0].lower(), x[0][1].lower()))
                    if v > 0
                ]
                if inc_ing
                else [],
                "missing_recetas": miss_ev if inc_ing else [],
            }
        )

    products_total = [{"producto": k, "cantidad": float(v)} for k, v in sorted(totals.items(), key=lambda x: x[0].lower()) if v > 0]
    ingredients_total: List[Dict[str, Any]] = []
    if inc_ing:
        ingredients_total = [
            {"ingrediente": k[0], "unidad": k[1], "cantidad": float(v)}
            for k, v in sorted(ing_totals.items(), key=lambda x: (x[0][0].lower(), x[0][1].lower()))
            if v > 0
        ]

    return {
        "ok": True,
        "day": day_s,
        "leads": leads_out,
        "products_total": products_total,
        "ingredients_total": ingredients_total,
        "missing_recetas": missing_recetas if inc_ing else [],
    }


@router.get("/mice/week")
def mice_week_report(
    start: str,
    include_ingredients: int = 1,
    db: Session = Depends(get_db),
    me=Depends(get_current_user),
):
    role = (me.get("role") or me.get("rol") or "").upper()
    if not _is_admin(role) and role not in ("JEFE DE OPERACIONES", "OPERACIONES", "COMPRAS", "MICE", "BODEGUERO"):
        raise HTTPException(403, detail="No autorizado")

    start_s = str(start or "").strip()[:10]
    try:
        start_d = date.fromisoformat(start_s)
    except Exception:
        raise HTTPException(400, detail="start debe ser YYYY-MM-DD")

    inc_ing = True
    try:
        inc_ing = bool(int(include_ingredients))
    except Exception:
        inc_ing = True

    days = [(start_d + timedelta(days=i)).isoformat() for i in range(7)]
    end_d = start_d + timedelta(days=6)

    confirmado_id = _estado_id(db, "CONFIRM")
    name_expr = _lead_name_expr(db)
    cols_leads = _cols_pg(db, "leads")
    cot_sel = "l.id_cotizacion_vigente" if "id_cotizacion_vigente" in cols_leads else "NULL::int AS id_cotizacion_vigente"

    params: Dict[str, Any] = {"d1": start_d, "d2": end_d}
    if confirmado_id:
        conf_where = "l.id_estado=:conf"
        params["conf"] = confirmado_id
    else:
        conf_where = "EXISTS (SELECT 1 FROM estados_lead e WHERE e.id_estado=l.id_estado AND UPPER(e.nombre) LIKE :confname)"
        params["confname"] = "%CONFIRM%"

    rows = db.execute(
        text(
            f"""
            SELECT l.id_lead,
                   {name_expr} AS cliente,
                   l.fecha_evento,
                   {cot_sel},
                   COALESCE(m.nombre,m.marca,'') AS marca,
                   COALESCE(c.nombre,'') AS comuna
            FROM leads l
            LEFT JOIN marcas m ON m.id_marca=l.id_marca
            LEFT JOIN comunas c ON c.id_comuna=l.id_comuna
            WHERE {conf_where}
              AND l.fecha_evento BETWEEN :d1 AND :d2
            ORDER BY l.fecha_evento ASC, l.id_lead DESC
            """
        ),
        params,
    ).mappings().all()

    prod_by_day: Dict[Tuple[str, str], float] = {}
    ing_by_day: Dict[Tuple[str, str, str], float] = {}
    missing_recetas: List[Dict[str, Any]] = []

    leads_out: List[Dict[str, Any]] = []

    for r in rows:
        lead = dict(r)
        id_lead = int(lead.get("id_lead") or 0)
        marca = str(lead.get("marca") or "").strip()
        day_ev = ""
        try:
            fev = lead.get("fecha_evento")
            if isinstance(fev, date):
                day_ev = fev.isoformat()
            else:
                day_ev = str(fev or "")[:10]
        except Exception:
            day_ev = str(lead.get("fecha_evento") or "")[:10]
        if not day_ev:
            continue

        manual = _mice_manual_by_day(db, id_lead)
        items: List[Dict[str, Any]] = []
        manual_days = {x.get("service_date") for x in manual if x.get("service_date")}
        if manual_days:
            items = [{"producto": x["producto"], "cantidad": float(x["cantidad"])} for x in manual if x.get("service_date") == day_ev]
            src = "manual"
        else:
            items = [{"producto": x["producto"], "cantidad": float(x["cantidad"])} for x in manual]
            src = "manual" if items else ""

        if not items:
            cid = _lead_best_cot_id(db, lead)
            if cid:
                items = _cot_items(db, cid)
                src = "cotizador"
        if not items:
            src = "none"

        ing_ev: Dict[Tuple[str, str], float] = {}
        miss_ev: List[Dict[str, Any]] = []
        breakdown: List[Dict[str, Any]] = []

        for it in items:
            p = str(it.get("producto") or "").strip()
            if not p:
                continue
            try:
                qn = float(it.get("cantidad") or 0)
            except Exception:
                qn = 0.0
            if qn <= 0:
                continue
            prod_by_day[(day_ev, p)] = prod_by_day.get((day_ev, p), 0.0) + qn

            if inc_ing:
                ing, miss = _expand_ingredientes_for_producto(db, p, marca, qn)
                if miss:
                    rec_miss = {"producto": p, "marca": marca, "reason": miss, "id_lead": id_lead, "day": day_ev}
                    missing_recetas.append(rec_miss)
                    miss_ev.append(rec_miss)
                    breakdown.append({"producto": p, "cantidad": qn, "ingredientes": []})
                    continue
                for ii in ing:
                    ing_name = str(ii.get("ingrediente") or "").strip()
                    unit = str(ii.get("unidad") or "").strip()
                    if not ing_name:
                        continue
                    try:
                        ing_by_day[(day_ev, ing_name, unit)] = ing_by_day.get((day_ev, ing_name, unit), 0.0) + float(ii.get("cantidad") or 0)
                        ing_ev[(ing_name, unit)] = ing_ev.get((ing_name, unit), 0.0) + float(ii.get("cantidad") or 0)
                    except Exception:
                        pass
                breakdown.append(
                    {
                        "producto": p,
                        "cantidad": qn,
                        "ingredientes": [
                            {
                                "ingrediente": str(ii.get("ingrediente") or "").strip(),
                                "unidad": str(ii.get("unidad") or "").strip(),
                                "cantidad": float(ii.get("cantidad") or 0),
                            }
                            for ii in ing
                            if str(ii.get("ingrediente") or "").strip()
                        ],
                    }
                )
            else:
                breakdown.append({"producto": p, "cantidad": qn, "ingredientes": []})

        leads_out.append(
            {
                "id_lead": id_lead,
                "day": day_ev,
                "cliente": lead.get("cliente"),
                "marca": marca,
                "comuna": lead.get("comuna"),
                "items_source": src,
                "items": items,
                "breakdown": breakdown,
                "ingredients": [
                    {"ingrediente": k[0], "unidad": k[1], "cantidad": float(v)}
                    for k, v in sorted(ing_ev.items(), key=lambda x: (x[0][0].lower(), x[0][1].lower()))
                    if v > 0
                ]
                if inc_ing
                else [],
                "missing_recetas": miss_ev if inc_ing else [],
            }
        )

    products = sorted({p for (_d, p) in prod_by_day.keys()}, key=lambda x: x.lower())
    products_grid: List[Dict[str, Any]] = []
    for p in products:
        row = {"producto": p, "total": 0.0}
        for d in days:
            q = float(prod_by_day.get((d, p), 0.0))
            row[d] = q
            row["total"] += q
        if row["total"] > 0:
            products_grid.append(row)

    ingredients_grid: List[Dict[str, Any]] = []
    if inc_ing:
        ings = sorted({(i, u) for (_d, i, u) in ing_by_day.keys()}, key=lambda x: (x[0].lower(), x[1].lower()))
        for ing_name, unit in ings:
            row = {"ingrediente": ing_name, "unidad": unit, "total": 0.0}
            for d in days:
                q = float(ing_by_day.get((d, ing_name, unit), 0.0))
                row[d] = q
                row["total"] += q
            if row["total"] > 0:
                ingredients_grid.append(row)

    return {
        "ok": True,
        "start": start_s,
        "days": days,
        "products_grid": products_grid,
        "ingredients_grid": ingredients_grid,
        "missing_recetas": missing_recetas if inc_ing else [],
        "leads": leads_out,
    }


@router.put("/agenda/{id_lead}/approve")
def approve_agenda(
    id_lead: int,
    x_user: str | None = Header(default=None, alias="X-USER"),
    db: Session = Depends(get_db),
    me=Depends(get_current_user),
):
    # Defaults: evita UnboundLocalError si algo falla antes de asignar.
    connected: bool = False
    links: list[str] = []
    event_ids: list[str | None] = []
    first_link: str | None = None
    first_eid: str | None = None
    gcal_error: str | None = None
    try:
        _ensure_lead_calendar_cols(db)

        name_expr = _lead_name_expr(db)
        lead_extra: dict[str, Any] = {}
        ventas_telefono: str | None = None
        cliente_telefono: str | None = None
        cliente_direccion: str | None = None
        num_cotizacion: str | None = None
        monto_cotizado: float | None = None
        id_cotizacion_vigente: int | None = None
        id_marca: int | None = None
        id_comuna: int | None = None
        id_usuario_key: str | None = None

        row = (
            db.execute(
                text(
                    f"""
                        SELECT
                            l.id_lead,
                            l.pre_title,
                            l.pre_start,
                            l.pre_end,
                            l.pre_location,
                            l.pre_description,
                            l.pre_events_json,
                            l.fecha_evento,
                            {name_expr} AS lead_nombre
                        FROM leads l
                    WHERE l.id_lead=:id
                    LIMIT 1
                    """
                ),
                {"id": id_lead},
            )
            .mappings()
            .first()
        )

        if not row:
            raise HTTPException(status_code=404, detail="Lead no existe")

        # Extra info (best-effort) para WhatsApp y UI.
        try:
            cols_lead = _cols_pg(db, "leads")
            sel = []
            for c in (
                "telefono",
                "direccion",
                "num_cotizacion",
                "monto_cotizado",
                "id_cotizacion_vigente",
                "id_marca",
                "id_comuna",
                "id_usuario",
            ):
                if c in cols_lead:
                    sel.append(f"l.{c} AS {c}")
            if sel:
                lead_extra = (
                    db.execute(text(f"SELECT {', '.join(sel)} FROM leads l WHERE l.id_lead=:id LIMIT 1"), {"id": id_lead})
                    .mappings()
                    .first()
                    or {}
                )
        except Exception:
            lead_extra = {}

        try:
            cliente_telefono = str(lead_extra.get("telefono") or "").strip() or None
        except Exception:
            cliente_telefono = None
        try:
            cliente_direccion = str(lead_extra.get("direccion") or "").strip() or None
        except Exception:
            cliente_direccion = None
        try:
            num_cotizacion = str(lead_extra.get("num_cotizacion") or "").strip() or None
        except Exception:
            num_cotizacion = None
        try:
            monto_cotizado = float(lead_extra.get("monto_cotizado")) if lead_extra.get("monto_cotizado") is not None else None
        except Exception:
            monto_cotizado = None
        try:
            id_cotizacion_vigente = int(lead_extra.get("id_cotizacion_vigente")) if lead_extra.get("id_cotizacion_vigente") is not None else None
        except Exception:
            id_cotizacion_vigente = None
        try:
            id_marca = int(lead_extra.get("id_marca")) if lead_extra.get("id_marca") is not None else None
        except Exception:
            id_marca = None
        try:
            id_comuna = int(lead_extra.get("id_comuna")) if lead_extra.get("id_comuna") is not None else None
        except Exception:
            id_comuna = None
        try:
            id_usuario_key = str(lead_extra.get("id_usuario") or "").strip() or None
        except Exception:
            id_usuario_key = None

        # Teléfono de ventas (ejecutivo asignado al lead), best-effort.
        try:
            if id_usuario_key and _table_exists_pg(db, "usuarios"):
                ucols = _cols_pg(db, "usuarios")
                tel_col = "telefono" if "telefono" in ucols else ("phone" if "phone" in ucols else None)
                if tel_col:
                    params_u: dict[str, Any] = {"u": id_usuario_key}
                    if str(id_usuario_key).isdigit() and "id_usuario" in ucols:
                        q_u = text(f"SELECT {tel_col} FROM public.usuarios WHERE id_usuario=:uid LIMIT 1")
                        v = db.execute(q_u, {"uid": int(id_usuario_key)}).scalar()
                    else:
                        q_u = text(
                            f"""
                            SELECT {tel_col}
                            FROM public.usuarios
                            WHERE lower(email)=lower(:u) OR lower(username)=lower(:u)
                            LIMIT 1
                            """
                        )
                        v = db.execute(q_u, params_u).scalar()
                    ventas_telefono = str(v or "").strip() or None
        except Exception:
            ventas_telefono = None

        if not row.get("pre_start") or not row.get("pre_end"):
            fe = row.get("fecha_evento")
            if not fe:
                raise HTTPException(status_code=400, detail="No hay pre-agenda para aprobar")
            fe_date = fe.date() if isinstance(fe, datetime) else fe
            tz = ZoneInfo("America/Santiago")
            start = datetime.combine(fe_date, time(10, 0), tzinfo=tz)
            end = start + timedelta(hours=2)
            db.execute(
                text(
                    """
                    UPDATE leads
                    SET pre_start = :ps,
                        pre_end = :pe,
                        pre_title = COALESCE(pre_title, :ptitle),
                        pre_location = COALESCE(pre_location, ''),
                        pre_description = COALESCE(pre_description, '')
                    WHERE id_lead = :id
                    """
                ),
                {
                    "id": id_lead,
                    "ps": start,
                    "pe": end,
                    "ptitle": row.get("pre_title") or row.get("lead_nombre") or f"Evento Lead {id_lead}",
                },
            )
            db.commit()
            row = {**row, "pre_start": start, "pre_end": end}

        title = row.get("pre_title") or row.get("lead_nombre") or f"Evento Lead {id_lead}"
        start = row.get("pre_start")
        end = row.get("pre_end")
        if not start or not end:
            raise HTTPException(status_code=400, detail="No hay pre-agenda para aprobar")
        loc = row.get("pre_location") or ""
        details = row.get("pre_description") or ""

        tz = ZoneInfo("America/Santiago")

        def _as_dt(v) -> datetime | None:
            if v is None:
                return None
            if isinstance(v, datetime):
                return v if v.tzinfo else v.replace(tzinfo=tz)
            s = str(v).strip()
            if not s:
                return None
            try:
                if s.endswith("Z"):
                    s = s[:-1] + "+00:00"
                d = datetime.fromisoformat(s)
                if d.tzinfo is None:
                    d = d.replace(tzinfo=tz)
                return d.astimezone(tz)
            except Exception:
                return None

        plan = None
        try:
            raw_plan = (row.get("pre_events_json") or "").strip()
            if raw_plan:
                plan = json.loads(raw_plan)
        except Exception:
            plan = None

        to_create: list[dict] = []
        if isinstance(plan, list) and plan:
            for e in plan:
                if not isinstance(e, dict):
                    continue
                st = _as_dt(e.get("start_at"))
                en = _as_dt(e.get("end_at"))
                if not st or not en:
                    continue
                to_create.append(
                    {
                        "day": str(e.get("day") or st.date().isoformat()),
                        "title": str(e.get("title") or title),
                        "location": str(e.get("location") or loc),
                        "description": str(e.get("description") or details or ""),
                        "start": st,
                        "end": en,
                    }
                )
        else:
            st = _as_dt(start)
            en = _as_dt(end)
            if st and en:
                to_create = [
                    {
                        "day": st.date().isoformat(),
                        "title": title,
                        "location": loc,
                        "description": details or "",
                        "start": st,
                        "end": en,
                    }
                ]

        # Si no hay nada válido, caemos al evento compat
        if not to_create:
            st = _as_dt(start) or datetime.now(tz)
            en = _as_dt(end) or (st + timedelta(hours=2))
            to_create = [{"day": st.date().isoformat(), "title": title, "location": loc, "description": details or "", "start": st, "end": en}]

        links: list[str] = []
        event_ids: list[str | None] = []
        calendar_ids: list[str] = []
        connected = False
        gcal_error = None

        svc = _gcal_service(db)
        if svc:
            connected = True
            cal_id = _gcal_default_calendar_id(db, None)
            for ev2 in to_create:
                lead_key = f"{id_lead}:{ev2.get('day')}"
                cal_id_used = cal_id
                try:
                    # 1) Intento encontrar evento existente (idempotencia).
                    items = []
                    try:
                        found = (
                            svc.events()
                            .list(
                                calendarId=cal_id,
                                privateExtendedProperty=f"lead_key={lead_key}",
                                maxResults=1,
                                singleEvents=True,
                            )
                            .execute()
                        )
                        items = found.get("items") or []
                    except Exception as e_list:
                        # Si falla el query, seguimos con insert/patch directo.
                        gcal_error = f"{type(e_list).__name__}: {e_list}"
                        items = []

                    body = {
                        "summary": ev2["title"],
                        "location": ev2["location"],
                        "description": ev2.get("description") or "",
                        "start": {"dateTime": ev2["start"].isoformat(), "timeZone": "America/Santiago"},
                        "end": {"dateTime": ev2["end"].isoformat(), "timeZone": "America/Santiago"},
                        "extendedProperties": {"private": {"lead_id": str(id_lead), "lead_key": lead_key}},
                    }

                    def _do_patch(_cal_id: str, _eid: str):
                        return svc.events().patch(calendarId=_cal_id, eventId=_eid, body=body).execute()

                    def _do_insert(_cal_id: str):
                        return svc.events().insert(calendarId=_cal_id, body=body).execute()

                    # 2) Patch si existe; si no, insert.
                    # Importante: NO hacemos fallback a "primary" en producción, porque eso termina
                    # agendando en el calendario equivocado (ej: el del ejecutivo). Si falla por permisos,
                    # dejamos el lead como pendiente_agendar=True y reportamos error.
                    if items:
                        eid = items[0].get("id")
                        try:
                            patched = _do_patch(cal_id, eid)
                            cal_id_used = cal_id
                        except Exception as e_patch:
                            msg = str(e_patch)
                            if _looks_like_perm_error(msg) or _looks_like_notfound_calendar(msg):
                                raise HTTPException(status_code=400, detail=f"Google Calendar sin permisos o no existe: {msg}")
                            raise
                        event_ids.append(eid)
                        calendar_ids.append(cal_id_used)
                        links.append(
                            patched.get("htmlLink")
                            or items[0].get("htmlLink")
                            or _gcal_link(ev2["title"], ev2["start"], ev2["end"], details=ev2.get("description") or "", location=ev2["location"])
                        )
                    else:
                        try:
                            created = _do_insert(cal_id)
                            cal_id_used = cal_id
                        except Exception as e_ins:
                            msg = str(e_ins)
                            # Retry simple (transitorio) 1 vez
                            try:
                                import time as _t
                                _t.sleep(0.8)
                            except Exception:
                                pass
                            try:
                                created = _do_insert(cal_id)
                                cal_id_used = cal_id
                            except Exception:
                                if _looks_like_perm_error(msg) or _looks_like_notfound_calendar(msg):
                                    raise HTTPException(status_code=400, detail=f"Google Calendar sin permisos o no existe: {msg}")
                                raise
                        event_ids.append(created.get("id"))
                        calendar_ids.append(cal_id_used)
                        links.append(
                            created.get("htmlLink")
                            or _gcal_link(ev2["title"], ev2["start"], ev2["end"], details=ev2.get("description") or "", location=ev2["location"])
                        )
                except Exception as e:
                    gcal_error = f"calendarId={_gcal_default_calendar_id(db, None)} :: {str(e)}"
                    event_ids.append(None)
                    calendar_ids.append(cal_id_used)
                    links.append(_gcal_link(ev2["title"], ev2["start"], ev2["end"], details=ev2.get("description") or "", location=ev2["location"]))
        else:
            gcal_error = "Google Calendar no conectado"
            for ev2 in to_create:
                links.append(_gcal_link(ev2["title"], ev2["start"], ev2["end"], details=ev2.get("description") or "", location=ev2["location"]))
                event_ids.append(None)
                calendar_ids.append("")

        first_link = links[0] if links else _gcal_link(title, to_create[0]["start"], to_create[0]["end"], details=details, location=loc)
        first_eid = event_ids[0] if event_ids else None

        # Solo marcamos como "agendado" (agenda_approved_at / pendiente_agendar=FALSE) si el evento
        # quedó efectivamente creado/actualizado en Google Calendar (event_id real).
        # Si falla, NO persistimos un calendar_html_link de template (fallback), porque eso engaña al
        # sistema y lo saca de "pendientes".
        ok_ids = [str(eid).strip() for eid in (event_ids or []) if eid is not None and str(eid).strip()]
        ok_calendar = bool(connected) and bool(event_ids) and (len(ok_ids) == len(event_ids))

        db.execute(
            text(
                """
                UPDATE leads
                SET calendar_start=:s,
                    calendar_end=:e,
                    calendar_html_link=CASE WHEN :ok THEN :lnk ELSE calendar_html_link END,
                    calendar_event_id=CASE WHEN :ok THEN :eid ELSE calendar_event_id END,
                    calendar_event_ids_json=:eids,
                    calendar_html_links_json=:lnks,
                    agenda_approved_by=CASE WHEN :ok THEN :by ELSE agenda_approved_by END,
                    agenda_approved_at=CASE WHEN :ok THEN now() ELSE agenda_approved_at END,
                    pendiente_agendar=CASE WHEN :ok THEN FALSE ELSE TRUE END,
                    updated_at=now()
                WHERE id_lead=:id
                """
            ),
            {
                "s": to_create[0]["start"],
                "e": to_create[0]["end"],
                "lnk": first_link,
                "eid": first_eid,
                "eids": json.dumps(event_ids, ensure_ascii=False),
                "lnks": json.dumps(links, ensure_ascii=False),
                "by": (x_user or me.get("name") or me.get("nombre") or "admin"),
                "id": id_lead,
                "ok": bool(ok_calendar),
            },
        )

        try:
            if _table_exists_pg(db, "eventos_calendario"):
                cols_ev = _cols_pg(db, "eventos_calendario")
                if "id_evento" in cols_ev and "id_lead" in cols_ev and "estado" in cols_ev:
                    aprobado_por_col = "aprobado_por" if "aprobado_por" in cols_ev else None
                    updated_col = "updated_at" if "updated_at" in cols_ev else None
                    sets = ["estado='aprobado'"]
                    if aprobado_por_col:
                        sets.append(f"{aprobado_por_col}=:by")
                    if updated_col:
                        sets.append(f"{updated_col}=now()")
                    db.execute(
                        text(
                            f"""
                            UPDATE eventos_calendario
                            SET {", ".join(sets)}
                            WHERE id_evento = (
                                SELECT id_evento FROM eventos_calendario
                                WHERE id_lead=:id AND estado='pendiente'
                                ORDER BY id_evento DESC
                                LIMIT 1
                            )
                            """
                        ),
                        {"id": id_lead, "by": (x_user or me.get("nombre") or "admin")},
                    )
        except Exception:
            pass

        db.commit()
        return {
            "ok": True,
            "connected": connected,
            "calendar_html_link": first_link,
            "calendar_event_id": first_eid,
            "calendar_html_links": links,
            "calendar_event_ids": event_ids,
            "calendar_ids": calendar_ids,
            "gcal_error": gcal_error,
            "lead": {
                "id_lead": int(id_lead),
                "telefono": cliente_telefono,
                "direccion": cliente_direccion,
                "ventas_telefono": ventas_telefono,
                "id_usuario": id_usuario_key,
                "num_cotizacion": num_cotizacion,
                "monto_cotizado": monto_cotizado,
                "id_cotizacion_vigente": id_cotizacion_vigente,
                "id_marca": id_marca,
                "id_comuna": id_comuna,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        return JSONResponse(status_code=500, content={"ok": False, "where": "tools.approve_agenda", "error": str(e)})


@router.put("/agenda/{id_lead}/edit_confirmed")
def edit_confirmed_event(
    id_lead: int,
    payload: dict[str, Any] = Body(default_factory=dict),
    x_user: str | None = Header(default=None, alias="X-USER"),
    db: Session = Depends(get_db),
    me=Depends(get_current_user),
):
    """
    Edita datos de un evento ya confirmado y re-sincroniza Google Calendar.
    Solo Ejecutivos (y Admin) pueden hacerlo.

    - Deja un rastro mínimo en `leads.notas` (timestamp + usuario + campos).
    - Crea notificación interna (system_notifs) para Operaciones/MICE/Admin.
    """
    from datetime import datetime

    role = str(me.get("role") or me.get("rol") or "").upper().strip()
    is_admin = ("ADMIN" in role) or (role == "1")
    is_exec = ("EJECUTIVO" in role) or (role == "2")
    if not (is_admin or is_exec):
        raise HTTPException(status_code=403, detail="No autorizado")

    _ensure_lead_calendar_cols(db)
    cols = _cols_pg(db, "leads")

    lead = (
        db.execute(
            text(
                """
                SELECT id_lead,
                       fecha_evento,
                       COALESCE(calendar_event_id,'') AS calendar_event_id,
                       COALESCE(calendar_html_link,'') AS calendar_html_link,
                       COALESCE(calendar_event_ids_json,'') AS calendar_event_ids_json,
                       COALESCE(calendar_html_links_json,'') AS calendar_html_links_json,
                       COALESCE(pre_title,'') AS pre_title,
                       pre_start,
                       pre_end,
                       COALESCE(pre_location,'') AS pre_location,
                       COALESCE(pre_description,'') AS pre_description,
                       COALESCE(pre_events_json,'') AS pre_events_json
                FROM public.leads
                WHERE id_lead=:id
                LIMIT 1
                """
            ),
            {"id": int(id_lead)},
        )
        .mappings()
        .first()
    )
    if not lead:
        raise HTTPException(status_code=404, detail="Lead no existe")
    if not str(lead.get("calendar_event_id") or "").strip() and not str(lead.get("calendar_html_link") or "").strip():
        raise HTTPException(status_code=400, detail="Lead no está agendado en Calendar")

    # No permitir editar eventos pasados.
    try:
        tz = ZoneInfo("America/Santiago")
        fe = lead.get("fecha_evento")
        fe_date = None
        if isinstance(fe, datetime):
            fe_date = fe.astimezone(tz).date() if fe.tzinfo else fe.date()
        elif isinstance(fe, date):
            fe_date = fe
        elif fe:
            fe_date = date.fromisoformat(str(fe)[:10])
        if fe_date and fe_date < datetime.now(tz).date():
            raise HTTPException(status_code=409, detail="No se puede editar un evento pasado.")
    except HTTPException:
        raise
    except Exception:
        pass

    allowed = [
        "telefono",
        "direccion",
        "pre_title",
        "pre_location",
        "pre_description",
        "pre_start",
        "pre_end",
        "pre_events_json",
        "pre_montaje_text",
    ]
    sets: list[str] = []
    params: dict[str, Any] = {"id": int(id_lead)}
    changes: dict[str, Any] = {}
    for k in allowed:
        if k not in payload:
            continue
        if k not in cols:
            continue
        sets.append(f"{k} = :{k}")
        params[k] = payload.get(k)
        changes[k] = payload.get(k)
    if sets:
        sets.append("updated_at = now()")
        db.execute(text(f"UPDATE public.leads SET {', '.join(sets)} WHERE id_lead=:id"), params)

    # Historial (notas)
    try:
        who = (x_user or str(me.get("username") or me.get("email") or me.get("name") or me.get("id") or "")).strip()[:120] or "usuario"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        keys = ", ".join(list(changes.keys()))[:220] if changes else ""
        line = f"[EVENTO_EDIT] {ts} · {who}" + (f" · {keys}" if keys else "")
        db.execute(
            text(
                """
                UPDATE public.leads
                SET notas = CASE
                  WHEN COALESCE(notas,'') = '' THEN :b
                  ELSE notas || E'\n\n' || :b
                END,
                updated_at = now()
                WHERE id_lead=:id
                """
            ),
            {"id": int(id_lead), "b": line},
        )
    except Exception:
        pass

    # Re-sync Calendar (update-only): NO debe crear eventos nuevos.
    connected = False
    gcal_error: str | None = None
    links: list[str] = []
    event_ids: list[str | None] = []
    first_link: str | None = None
    first_eid: str | None = None
    try:
        tz = ZoneInfo("America/Santiago")

        def _as_dt(v) -> datetime | None:
            if v is None:
                return None
            if isinstance(v, datetime):
                return v if v.tzinfo else v.replace(tzinfo=tz)
            s = str(v).strip()
            if not s:
                return None
            try:
                if s.endswith("Z"):
                    s = s[:-1] + "+00:00"
                d = datetime.fromisoformat(s)
                if d.tzinfo is None:
                    d = d.replace(tzinfo=tz)
                return d.astimezone(tz)
            except Exception:
                return None

        # Releer el lead tras UPDATE (si solo editas título, el dict `lead` de arriba queda stale).
        lead2 = (
            db.execute(
                text(
                    """
                    SELECT id_lead,
                           COALESCE(calendar_event_id,'') AS calendar_event_id,
                           COALESCE(calendar_html_link,'') AS calendar_html_link,
                           COALESCE(calendar_event_ids_json,'') AS calendar_event_ids_json,
                           COALESCE(calendar_html_links_json,'') AS calendar_html_links_json,
                           COALESCE(pre_title,'') AS pre_title,
                           pre_start,
                           pre_end,
                           COALESCE(pre_location,'') AS pre_location,
                           COALESCE(pre_description,'') AS pre_description,
                           COALESCE(pre_events_json,'') AS pre_events_json
                    FROM public.leads
                    WHERE id_lead=:id
                    LIMIT 1
                    """
                ),
                {"id": int(id_lead)},
            )
            .mappings()
            .first()
        )
        lead_used = lead2 or lead

        title = str(lead_used.get("pre_title") or "").strip() or f"Evento Lead {id_lead}"
        loc = str(lead_used.get("pre_location") or "").strip()
        details = str(lead_used.get("pre_description") or "").strip()
        st0 = _as_dt(lead_used.get("pre_start"))
        en0 = _as_dt(lead_used.get("pre_end"))

        plan = None
        try:
            raw_plan = str(lead_used.get("pre_events_json") or "").strip()
            if raw_plan:
                plan = json.loads(raw_plan)
        except Exception:
            plan = None

        to_update: list[dict[str, Any]] = []
        if isinstance(plan, list) and plan:
            for e in plan:
                if not isinstance(e, dict):
                    continue
                st = _as_dt(e.get("start_at"))
                en = _as_dt(e.get("end_at"))
                if not st or not en:
                    continue
                to_update.append(
                    {
                        "day": str(e.get("day") or st.date().isoformat()),
                        # Al editar un confirmado, el "título" debe reflejar el lead (pre_title),
                        # aunque `pre_events_json` tenga títulos legacy.
                        "title": title,
                        # También forzamos location/description del lead para que el cambio se refleje.
                        "location": loc,
                        "description": details or "",
                        "start": st,
                        "end": en,
                    }
                )
        else:
            if st0 and en0:
                to_update = [
                    {
                        "day": st0.date().isoformat(),
                        "title": title,
                        "location": loc,
                        "description": details or "",
                        "start": st0,
                        "end": en0,
                    }
                ]
        if not to_update:
            raise HTTPException(status_code=400, detail="No hay pre-agenda válida para re-sincronizar")

        # Determinar eventIds existentes (preferir JSON; fallback: columna + decode desde htmlLink).
        existing_ids: list[str] = []
        existing_links: list[str] = []
        try:
            raw_ids = str(lead_used.get("calendar_event_ids_json") or "").strip()
            if raw_ids:
                v = json.loads(raw_ids)
                if isinstance(v, list):
                    existing_ids = [str(x).strip() for x in v if str(x or "").strip()]
        except Exception:
            existing_ids = []
        try:
            raw_ln = str(lead_used.get("calendar_html_links_json") or "").strip()
            if raw_ln:
                v2 = json.loads(raw_ln)
                if isinstance(v2, list):
                    existing_links = [str(x).strip() for x in v2 if str(x or "").strip()]
        except Exception:
            existing_links = []

        if not existing_ids:
            one = str(lead_used.get("calendar_event_id") or "").strip()
            if one:
                existing_ids = [one]

        cal_id = os.getenv("GCAL_DEFAULT_CAL") or GCAL_DEFAULT_CAL
        if not existing_ids:
            candidates = []
            one_link = str(lead_used.get("calendar_html_link") or "").strip()
            if one_link:
                candidates.append(one_link)
            candidates.extend(existing_links)
            for lk in candidates:
                eid2, cal2 = _extract_gcal_event_id_from_link(lk)
                if eid2 and eid2 not in existing_ids:
                    existing_ids.append(eid2)
                if cal2:
                    cal_id = cal2

        # En edit_confirmed NO creamos eventos nuevos: si no hay eventId suficiente, error claro.
        if len(existing_ids) != len(to_update):
            if not (len(existing_ids) == 1 and len(to_update) == 1):
                raise HTTPException(
                    status_code=409,
                    detail=f"No se puede actualizar Calendar sin crear eventos nuevos: se requieren {len(to_update)} eventId(s) pero hay {len(existing_ids)}.",
                )

        svc = _gcal_service(db)
        if not svc:
            gcal_error = "Google Calendar no conectado"
            for ev2 in to_update:
                links.append(_gcal_link(ev2["title"], ev2["start"], ev2["end"], details=ev2.get("description") or "", location=ev2["location"]))
                event_ids.append(None)
        else:
            connected = True
            for i, ev2 in enumerate(to_update):
                eid = existing_ids[i] if len(existing_ids) == len(to_update) else existing_ids[0]
                body = {
                    "summary": ev2["title"],
                    "location": ev2["location"],
                    "description": ev2.get("description") or "",
                    "start": {"dateTime": ev2["start"].isoformat(), "timeZone": "America/Santiago"},
                    "end": {"dateTime": ev2["end"].isoformat(), "timeZone": "America/Santiago"},
                    "extendedProperties": {"private": {"lead_id": str(id_lead), "lead_key": f"{id_lead}:{ev2.get('day')}"}},
                }
                patched = svc.events().patch(calendarId=cal_id, eventId=eid, body=body).execute()
                event_ids.append(eid)
                links.append(
                    patched.get("htmlLink")
                    or _gcal_link(ev2["title"], ev2["start"], ev2["end"], details=ev2.get("description") or "", location=ev2["location"])
                )

        first_link = links[0] if links else None
        first_eid = (event_ids[0] if event_ids else None)
        if first_link and first_eid:
            db.execute(
                text(
                    """
                    UPDATE public.leads
                    SET calendar_start=:s,
                        calendar_end=:e,
                        calendar_html_link=:lnk,
                        calendar_event_id=:eid,
                        calendar_event_ids_json=:eids,
                        calendar_html_links_json=:lnks,
                        updated_at=now()
                    WHERE id_lead=:id
                    """
                ),
                {
                    "s": to_update[0]["start"],
                    "e": to_update[0]["end"],
                    "lnk": first_link,
                    "eid": first_eid,
                    "eids": json.dumps(event_ids, ensure_ascii=False),
                    "lnks": json.dumps(links, ensure_ascii=False),
                    "id": int(id_lead),
                },
            )
            try:
                db.commit()
            except Exception:
                try:
                    db.rollback()
                except Exception:
                    pass
    except HTTPException:
        raise
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        gcal_error = str(e)

    gcal = {
        "ok": True,
        "connected": connected,
        "calendar_html_link": first_link,
        "calendar_event_id": first_eid,
        "calendar_html_links": links,
        "calendar_event_ids": event_ids,
        "gcal_error": gcal_error,
    }

    # Notifs internas
    try:
        from backend.core.system_notifs import push_system_notif

        cn = db.connection()
        title = f"Evento modificado · Lead #{int(id_lead)}"
        body = f"{(x_user or me.get('username') or me.get('email') or me.get('name') or 'usuario')} modificó un evento confirmado."
        payload_notif = {"id_lead": int(id_lead), "changes": list(changes.keys()), "calendar_event_id": gcal.get("calendar_event_id")}
        for rt in ("OPERACIONES", "MICE", "ADMIN"):
            push_system_notif(cn, kind="EVENTO_MODIFICADO", role_target=rt, id_lead=int(id_lead), title=title, body=body, payload=payload_notif)
        try:
            db.commit()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
    except Exception:
        pass

    # Activity log best-effort
    try:
        cn = db.connection()
        log_activity(
            cn,
            username=str(me.get("username") or me.get("email") or me.get("name") or me.get("id") or "").strip()[:200],
            user_id=int(me.get("id") or 0) if str(me.get("id") or "").isdigit() else None,
            role=role,
            action="EVENT_MODIFIED",
            entity_type="lead",
            entity_id=int(id_lead),
            meta={"changes": list(changes.keys()), "calendar_event_id": gcal.get("calendar_event_id")},
        )
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass

    return {"ok": True, "gcal": gcal, "changes": changes}
