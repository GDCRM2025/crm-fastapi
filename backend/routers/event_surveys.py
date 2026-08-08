from __future__ import annotations

import os
import secrets
import unicodedata
from datetime import date, timedelta
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.core.activity_log import log_activity
from backend.core.quote_assets import drive_view, logo_for

try:
    from backend.routers.auth import get_current_user  # type: ignore
except Exception:  # pragma: no cover
    def get_current_user():  # type: ignore
        return {"id": "dev", "role": "ADMIN", "username": "dev", "name": "Dev"}


router = APIRouter(tags=["event_surveys"])


QUESTIONS = [
    ("food_quality", "Calidad de los alimentos"),
    ("service", "Servicio recibido"),
    ("setup", "Ambientación y montaje"),
    ("hygiene", "Higiene y presentación"),
    ("overall", "Experiencia general"),
    ("recommend", "Nos recomendaría"),
    ("return_probability", "Probabilidad de volver a contratarnos"),
]


def _questions_for_brand(value: Any) -> list[dict[str, str]]:
    brand = str(value or "nuestro equipo").strip().upper()
    return [
        {
            "key": "food_quality",
            "label": f"¿Cómo evaluarías el sabor, la temperatura y la presentación de la propuesta de {brand}?",
        },
        {
            "key": "service",
            "label": "¿Cómo fue la atención y coordinación antes y durante tu evento?",
        },
        {
            "key": "setup",
            "label": "¿El montaje y el servicio estuvieron listos en el horario acordado?",
        },
        {
            "key": "hygiene",
            "label": "¿Cómo evaluarías la presentación del equipo, los carros y el espacio de servicio?",
        },
        {
            "key": "overall",
            "label": "Considerando todo el evento, ¿qué tan bien cumplimos lo que acordamos contigo?",
        },
        {
            "key": "recommend",
            "label": f"¿Qué tan probable es que recomiendes {brand} a otra persona o empresa?",
        },
        {
            "key": "return_probability",
            "label": f"¿Qué tan probable es que vuelvas a contratar {brand} para un próximo evento?",
        },
    ]

BRAND_THEMES: dict[str, dict[str, str]] = {
    "green diamond": {"primary": "#587b0d", "secondary": "#27974e", "accent": "#96c266", "surface": "#f4f8ef"},
    "camaleon": {"primary": "#149108", "secondary": "#2bb515", "accent": "#41d921", "surface": "#f2fbef"},
    "gourmet": {"primary": "#323232", "secondary": "#efc12d", "accent": "#efc12d", "surface": "#faf8ef"},
    "express": {"primary": "#da4819", "secondary": "#231f20", "accent": "#f6a713", "surface": "#fff5ed"},
    "carritos express": {"primary": "#941d2a", "secondary": "#ededed", "accent": "#ededed", "surface": "#faf3f4"},
    "del sabor": {"primary": "#34a30a", "secondary": "#72400a", "accent": "#ffab32", "surface": "#fbfaef"},
    "carritos del sabor": {"primary": "#34a30a", "secondary": "#72400a", "accent": "#ffab32", "surface": "#fbfaef"},
    "brontos": {"primary": "#000000", "secondary": "#fbc02d", "accent": "#fbc02d", "surface": "#fff9e8"},
    "smashvill": {"primary": "#d12840", "secondary": "#000000", "accent": "#fdb131", "surface": "#fff1f3"},
    "petras box": {"primary": "#46ae47", "secondary": "#c17b53", "accent": "#eddaa3", "surface": "#f2f7f3"},
    "petras": {"primary": "#46ae47", "secondary": "#c17b53", "accent": "#eddaa3", "surface": "#f2f7f3"},
}

BRAND_REVIEW_URLS: dict[str, str] = {
    "camaleon": "https://www.google.com/maps?cid=11338759668499752981",
    "gourmet": "https://www.google.com/maps?cid=8199037924803345542",
    "express": "https://www.google.com/maps?cid=16181625780572243236",
    "carritos express": "https://www.google.com/maps?cid=16181625780572243236",
    "del sabor": "https://www.google.com/maps?cid=8654984101606170878",
    "carritos del sabor": "https://www.google.com/maps?cid=8654984101606170878",
    "brontos": "https://www.google.com/maps?cid=15211523047980271998",
    "petras": "https://www.google.com/maps?cid=2359268546212582947",
    "petras box": "https://www.google.com/maps?cid=2359268546212582947",
}


def _norm_brand(value: Any) -> str:
    normalized = unicodedata.normalize("NFD", str(value or "").strip().lower())
    return " ".join("".join(ch for ch in normalized if unicodedata.category(ch) != "Mn").replace("_", " ").split())


def _theme_for_brand(value: Any) -> dict[str, str]:
    key = _norm_brand(value)
    if key in BRAND_THEMES:
        return dict(BRAND_THEMES[key])
    for alias, theme in BRAND_THEMES.items():
        if key and (alias in key or key in alias):
            return dict(theme)
    return {"primary": "#0b7f43", "secondary": "#102033", "accent": "#38bdf8", "surface": "#f4f8f6"}


def _review_url_for_brand(value: Any) -> str:
    key = _norm_brand(value)
    if key in BRAND_REVIEW_URLS:
        return BRAND_REVIEW_URLS[key]
    for alias, url in BRAND_REVIEW_URLS.items():
        if key and (alias in key or key in alias):
            return url
    return ""


def _role(user: dict) -> str:
    return str(user.get("role") or user.get("rol") or "").upper()


def _is_admin(role: str) -> bool:
    return role in ("ADMIN", "SUPERADMIN") or "ADMIN" in role


def _uid(user: dict) -> Optional[int]:
    raw = user.get("id")
    return int(raw) if str(raw or "").isdigit() else None


def _uname(user: dict) -> str:
    return str(user.get("username") or user.get("email") or user.get("name") or user.get("id") or "").strip()[:200]


def _user_keys(user: dict) -> list[str]:
    vals = [user.get("id"), user.get("username"), user.get("email"), user.get("name"), user.get("nombre")]
    out: list[str] = []
    for v in vals:
        s = str(v or "").strip().lower()
        if s and s not in out:
            out.append(s)
    return out


def _table_exists(db: Session, table: str) -> bool:
    try:
        return bool(db.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"public.{table}"}).scalar())
    except Exception:
        return False


def _col_exists(db: Session, table: str, col: str) -> bool:
    try:
        return bool(
            db.execute(
                text(
                    """
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema='public' AND table_name=:t AND column_name=:c
                    LIMIT 1
                    """
                ),
                {"t": table, "c": col},
            ).scalar()
        )
    except Exception:
        return False


def _estado_id(db: Session, like: str) -> Optional[int]:
    try:
        v = db.execute(
            text("SELECT id_estado FROM public.estados_lead WHERE UPPER(nombre) LIKE :n ORDER BY id_estado LIMIT 1"),
            {"n": f"%{like.upper()}%"},
        ).scalar()
        return int(v) if v is not None else None
    except Exception:
        return None


def _lead_name_expr(db: Session) -> str:
    if _col_exists(db, "leads", "nombre_cliente"):
        return "COALESCE(NULLIF(btrim(l.nombre_cliente),''), '')"
    if _col_exists(db, "leads", "cliente"):
        return "COALESCE(NULLIF(btrim(l.cliente),''), '')"
    return "''"


def _base_url() -> str:
    base = (os.getenv("APP_URL") or "https://greendiamond.cl/crm").strip().rstrip("/")
    if base and not base.endswith("/crm") and "greendiamond.cl" in base:
        base += "/crm"
    return base


def _ensure_table(db: Session) -> None:
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.event_surveys (
              id_survey BIGSERIAL PRIMARY KEY,
              token TEXT NOT NULL UNIQUE,
              id_lead BIGINT NOT NULL,
              event_day DATE,
              cliente TEXT,
              email TEXT,
              telefono TEXT,
              marca TEXT,
              comuna TEXT,
              sent_at TIMESTAMPTZ,
              sent_by TEXT,
              responded_at TIMESTAMPTZ,
              ratings JSONB NOT NULL DEFAULT '{}'::jsonb,
              comment TEXT,
              google_review_clicked_at TIMESTAMPTZ,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_event_surveys_lead_day ON public.event_surveys(id_lead,event_day)"))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_event_surveys_responded ON public.event_surveys(responded_at)"))
    if _table_exists(db, "marcas"):
        try:
            # Un fallo de DDL (permisos o esquema heredado) no puede dejar
            # abortada toda la transacción que genera la encuesta.
            with db.begin_nested():
                db.execute(text("ALTER TABLE public.marcas ADD COLUMN IF NOT EXISTS color_primary TEXT"))
                db.execute(text("ALTER TABLE public.marcas ADD COLUMN IF NOT EXISTS color_secondary TEXT"))
                db.execute(text("ALTER TABLE public.marcas ADD COLUMN IF NOT EXISTS google_review_url TEXT"))
        except Exception:
            pass


def _scope_where(db: Session, user: dict, params: Dict[str, Any]) -> str:
    if _is_admin(_role(user)):
        return "TRUE"
    parts: list[str] = []
    marcas = [int(x) for x in (user.get("marcas") or []) if str(x).isdigit()]
    if marcas and _col_exists(db, "leads", "id_marca"):
        parts.append("l.id_marca = ANY(:marcas)")
        params["marcas"] = marcas
    keys = _user_keys(user)
    if keys and _col_exists(db, "leads", "id_usuario"):
        parts.append("lower(NULLIF(btrim(COALESCE(l.id_usuario::text,'')) ,'')) = ANY(:user_keys)")
        params["user_keys"] = keys
    return "(" + " OR ".join(parts) + ")" if parts else "FALSE"


def _ensure_for_lead(db: Session, row: dict) -> dict:
    _ensure_table(db)
    existing = db.execute(
        text("SELECT * FROM public.event_surveys WHERE id_lead=:id AND event_day=:d ORDER BY id_survey DESC LIMIT 1"),
        {"id": int(row["id_lead"]), "d": row.get("event_day")},
    ).mappings().first()
    if existing:
        return dict(existing)
    token = secrets.token_urlsafe(18)
    rec = db.execute(
        text(
            """
            INSERT INTO public.event_surveys(token,id_lead,event_day,cliente,email,telefono,marca,comuna)
            VALUES(:token,:id_lead,:event_day,:cliente,:email,:telefono,:marca,:comuna)
            RETURNING *
            """
        ),
        {
            "token": token,
            "id_lead": int(row["id_lead"]),
            "event_day": row.get("event_day"),
            "cliente": row.get("cliente") or "",
            "email": row.get("email") or "",
            "telefono": row.get("telefono") or "",
            "marca": row.get("marca") or "",
            "comuna": row.get("comuna") or "",
        },
    ).mappings().first()
    return dict(rec or {})


def _public_link(token: str) -> str:
    return f"{_base_url()}/web/views/satisfaccion.html?token={token}&v=20260728-brand-themes1"


def _wsp_url(phone: str, text_msg: str) -> str:
    import urllib.parse

    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if digits and not digits.startswith("56") and len(digits) == 9:
        digits = "56" + digits
    return f"https://wa.me/{digits}?text={urllib.parse.quote(text_msg)}" if digits else ""


@router.get("/surveys/pending")
def surveys_pending(
    day: str = Query(""),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    try:
        d = date.fromisoformat(day) if day else (date.today() - timedelta(days=1))
    except Exception:
        raise HTTPException(400, "day inválido (YYYY-MM-DD)")
    if not _table_exists(db, "leads"):
        return {"ok": True, "day": d.isoformat(), "items": []}
    conf = _estado_id(db, "CONFIRM")
    if not conf:
        return {"ok": True, "day": d.isoformat(), "items": []}
    has_id_marca = _col_exists(db, "leads", "id_marca")
    has_id_comuna = _col_exists(db, "leads", "id_comuna")
    join_m = has_id_marca and _table_exists(db, "marcas")
    join_c = has_id_comuna and _table_exists(db, "comunas")
    if join_m:
        marca_cols = {
            str(x[0])
            for x in db.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema='public' AND table_name='marcas'
                    """
                )
            ).fetchall()
        }
        if "nombre" in marca_cols and "marca" in marca_cols:
            marca_sel = "COALESCE(NULLIF(m.nombre,''),NULLIF(m.marca,''),'')"
        elif "nombre" in marca_cols:
            marca_sel = "COALESCE(m.nombre,'')"
        else:
            marca_sel = "COALESCE(m.marca,'')"
    else:
        marca_sel = "''"
    comuna_sel = "COALESCE(c.nombre,'')" if join_c else "''"
    joins = (" LEFT JOIN public.marcas m ON m.id_marca=l.id_marca " if join_m else "") + (
        " LEFT JOIN public.comunas c ON c.id_comuna=l.id_comuna " if join_c else ""
    )
    params: Dict[str, Any] = {"conf": int(conf), "d": d.isoformat()}
    scope = _scope_where(db, user, params)
    email_expr = "COALESCE(l.email,'')" if _col_exists(db, "leads", "email") else "''"
    tel_expr = "COALESCE(l.telefono,'')" if _col_exists(db, "leads", "telefono") else "''"
    name_expr = _lead_name_expr(db)
    rows = db.execute(
        text(
            f"""
            SELECT l.id_lead::bigint AS id_lead, DATE(l.fecha_evento) AS event_day,
                   {name_expr} AS cliente, {email_expr} AS email, {tel_expr} AS telefono,
                   {marca_sel} AS marca, {comuna_sel} AS comuna
            FROM public.leads l
            {joins}
            WHERE l.id_estado=:conf AND DATE(l.fecha_evento)=:d AND {scope}
            ORDER BY l.id_lead ASC
            """
        ),
        params,
    ).mappings().all()
    items = []
    for r in rows:
        sv = _ensure_for_lead(db, dict(r))
        link = _public_link(str(sv.get("token") or ""))
        brand = str(r.get("marca") or "nosotros").strip()
        msg = (
            f"Hola {r.get('cliente') or ''}, muchas gracias por realizar tu evento con {brand}. "
            f"Nos ayudas muchísimo respondiendo esta encuesta breve para seguir mejorando la experiencia: {link}"
        )
        items.append({
            **dict(r),
            "token": sv.get("token"),
            "responded_at": str(sv.get("responded_at") or ""),
            "sent_at": str(sv.get("sent_at") or ""),
            "survey_url": link,
            "whatsapp_text": msg,
            "whatsapp_url": _wsp_url(str(r.get("telefono") or ""), msg),
        })
    try:
        db.commit()
    except Exception:
        db.rollback()
    return {"ok": True, "day": d.isoformat(), "items": items}


@router.post("/surveys/{token}/mark_sent")
def survey_mark_sent(token: str, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    _ensure_table(db)
    who = _uname(user)
    db.execute(
        text("UPDATE public.event_surveys SET sent_at=now(), sent_by=:u, updated_at=now() WHERE token=:t"),
        {"t": token, "u": who},
    )
    db.commit()
    return {"ok": True}


@router.get("/surveys/summary")
def surveys_summary(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    _ensure_table(db)
    params: Dict[str, Any] = {"days": int(days)}
    scope = _scope_where(db, user, params)
    rows = db.execute(
        text(
            f"""
            SELECT COUNT(*)::int AS total,
                   COUNT(*) FILTER (WHERE s.responded_at IS NOT NULL)::int AS responded,
                   AVG((s.ratings->>'overall')::numeric) FILTER (WHERE s.ratings ? 'overall') AS avg_overall,
                   AVG((s.ratings->>'recommend')::numeric) FILTER (WHERE s.ratings ? 'recommend') AS avg_recommend
            FROM public.event_surveys s
            JOIN public.leads l ON l.id_lead=s.id_lead
            WHERE s.created_at >= now() - make_interval(days => :days)
              AND {scope}
            """
        ),
        params,
    ).mappings().first() or {}
    latest = db.execute(
        text(
            f"""
            SELECT s.id_survey, s.id_lead, s.event_day, s.cliente, s.marca, s.comuna,
                   s.responded_at, s.ratings, s.comment, s.google_review_clicked_at
            FROM public.event_surveys s
            JOIN public.leads l ON l.id_lead=s.id_lead
            WHERE s.created_at >= now() - make_interval(days => :days)
              AND {scope}
            ORDER BY COALESCE(s.responded_at, s.created_at) DESC
            LIMIT 80
            """
        ),
        params,
    ).mappings().all()
    return {"ok": True, "days": int(days), "summary": dict(rows), "items": [dict(x) for x in latest]}


@router.get("/public/surveys/{token}")
def public_survey_get(token: str, db: Session = Depends(get_db)):
    _ensure_table(db)
    has_marcas = _table_exists(db, "marcas")
    join = ""
    brand_cols = "NULL::text AS logo_url, NULL::text AS logo_path, NULL::text AS color_primary, NULL::text AS color_secondary, NULL::text AS google_review_url"
    if has_marcas:
        marca_cols = {
            str(x[0])
            for x in db.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema='public' AND table_name='marcas'
                    """
                )
            ).fetchall()
        }
        brand_name = "COALESCE(m.nombre,m.marca,'')" if {"nombre", "marca"} <= marca_cols else (
            "COALESCE(m.nombre,'')" if "nombre" in marca_cols else "COALESCE(m.marca,'')"
        )
        join = """
        LEFT JOIN public.marcas m
          ON upper({brand_name}) = upper(COALESCE(s.marca,''))
        """.format(brand_name=brand_name)
        logo_url = "COALESCE(m.logo_url,'')" if "logo_url" in marca_cols else "''"
        logo_path = "COALESCE(m.logo_path,'')" if "logo_path" in marca_cols else "''"
        primary = "COALESCE(m.color_primary,'')" if "color_primary" in marca_cols else "''"
        secondary = "COALESCE(m.color_secondary,'')" if "color_secondary" in marca_cols else "''"
        review_url = "COALESCE(m.google_review_url,'')" if "google_review_url" in marca_cols else "''"
        brand_cols = f"""
        {logo_url} AS logo_url,
        {logo_path} AS logo_path,
        {primary} AS color_primary,
        {secondary} AS color_secondary,
        {review_url} AS google_review_url
        """
    r = db.execute(
        text(
            f"""
            SELECT s.token,s.id_lead,s.event_day,s.cliente,s.marca,s.comuna,s.responded_at,s.ratings,
                   {brand_cols}
            FROM public.event_surveys s
            {join}
            WHERE s.token=:t
            LIMIT 1
            """
        ),
        {"t": token},
    ).mappings().first()
    if not r:
        raise HTTPException(404, "Encuesta no encontrada")
    survey = dict(r)
    theme = _theme_for_brand(survey.get("marca"))
    survey["color_primary"] = str(survey.get("color_primary") or theme["primary"])
    survey["color_secondary"] = str(survey.get("color_secondary") or theme["secondary"])
    survey["color_accent"] = theme["accent"]
    survey["color_surface"] = theme["surface"]
    survey["google_review_url"] = str(
        survey.get("google_review_url") or _review_url_for_brand(survey.get("marca"))
    )
    logo = str(survey.get("logo_url") or survey.get("logo_path") or "").strip()
    if "drive.google.com" in logo:
        logo = drive_view(logo)
    if not logo:
        logo = logo_for(str(survey.get("marca") or ""), prefer_local=True)
    survey["logo_url"] = logo
    return {"ok": True, "survey": survey, "questions": _questions_for_brand(survey.get("marca"))}


@router.post("/public/surveys/{token}/google_review_click")
def public_survey_google_click(token: str, db: Session = Depends(get_db)):
    _ensure_table(db)
    row = db.execute(
        text(
            """
            UPDATE public.event_surveys
            SET google_review_clicked_at=now(), updated_at=now()
            WHERE token=:token
            RETURNING id_lead
            """
        ),
        {"token": token},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Encuesta no encontrada")
    db.commit()
    return {"ok": True}


@router.post("/public/surveys/{token}")
def public_survey_submit(token: str, payload: dict = Body(...), db: Session = Depends(get_db)):
    _ensure_table(db)
    ratings = payload.get("ratings") or {}
    if not isinstance(ratings, dict):
        raise HTTPException(400, "ratings inválido")
    clean: Dict[str, int] = {}
    for k, _label in QUESTIONS:
        try:
            n = int(ratings.get(k))
        except Exception:
            n = 0
        if n < 1 or n > 5:
            raise HTTPException(400, "Responde todas las preguntas con nota de 1 a 5.")
        clean[k] = n
    comment = str(payload.get("comment") or "").strip()[:2000]
    row = db.execute(
        text(
            """
            UPDATE public.event_surveys
            SET ratings=CAST(:ratings AS jsonb), comment=NULLIF(:comment,''), responded_at=now(), updated_at=now()
            WHERE token=:token
            RETURNING id_lead
            """
        ),
        {"token": token, "ratings": json_dumps(clean), "comment": comment},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Encuesta no encontrada")
    try:
        log_activity(
            db.connection(),
            username="cliente",
            user_id=None,
            role="PUBLIC",
            action="SURVEY_SUBMITTED",
            entity_type="lead",
            entity_id=int(row.get("id_lead") or 0),
            meta={"ratings": clean},
        )
    except Exception:
        pass
    db.commit()
    return {"ok": True}


def json_dumps(obj: Any) -> str:
    import json

    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return "{}"
