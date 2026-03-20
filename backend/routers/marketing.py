from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import date, datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, Body
from sqlalchemy import text

from backend.core.db import get_connection
from backend.core.email import send_email, EmailConfigError
from backend.routers.auth import get_current_user

router = APIRouter(prefix="/marketing", tags=["marketing"])


def _role(user: dict) -> str:
    return str(user.get("role") or user.get("rol") or "").upper()


def _is_admin(role: str) -> bool:
    return role in ("ADMIN", "SUPERADMIN")


def _user_marcas(user: dict) -> list[int]:
    out: list[int] = []
    for x in (user.get("marcas") or []):
        try:
            out.append(int(x))
        except Exception:
            continue
    return out


def _ensure_schema() -> None:
    with get_connection() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS public.marketing_audiencia (
                  id_audiencia bigserial PRIMARY KEY,
                  yyyymm integer NOT NULL,
                  id_marca integer,
                  id_comuna integer,
                  id_lead_origen bigint,
                  fecha_evento date,
                  nombre text,
                  email text,
                  telefono text,
                  tipo_cliente text,
                  plataforma text,
                  estado text,
                  consentimiento boolean NOT NULL DEFAULT false,
                  legitimo_interes boolean NOT NULL DEFAULT true,
                  opt_out boolean NOT NULL DEFAULT false,
                  tags_json text,
                  dedupe_key text NOT NULL,
                  created_at timestamp without time zone NOT NULL DEFAULT now(),
                  updated_at timestamp without time zone NOT NULL DEFAULT now()
                );
                """
            )
        )
        # Si la tabla ya existía antes de agregar opt_out
        conn.execute(text("ALTER TABLE public.marketing_audiencia ADD COLUMN IF NOT EXISTS opt_out boolean NOT NULL DEFAULT false;"))
        conn.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_marketing_audiencia
                ON public.marketing_audiencia (yyyymm, id_marca, dedupe_key);
                """
            )
        )
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_marketing_audiencia_yyyymm ON public.marketing_audiencia(yyyymm);"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_marketing_audiencia_email ON public.marketing_audiencia(lower(email));"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_marketing_audiencia_tel ON public.marketing_audiencia(telefono);"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_marketing_audiencia_optout ON public.marketing_audiencia(opt_out);"))

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS public.marketing_campanas (
                  id_campana bigserial PRIMARY KEY,
                  created_at timestamp without time zone NOT NULL DEFAULT now(),
                  created_by text,
                  yyyymm integer NOT NULL,
                  id_marca integer,
                  subject text NOT NULL,
                  text_body text NOT NULL,
                  html_body text,
                  total_destinatarios integer NOT NULL DEFAULT 0,
                  enviados integer NOT NULL DEFAULT 0,
                  fallidos integer NOT NULL DEFAULT 0,
                  last_error text
                );
                """
            )
        )
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_marketing_campanas_yyyymm ON public.marketing_campanas(yyyymm);"))

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS public.marketing_envios (
                  id_envio bigserial PRIMARY KEY,
                  id_campana bigint NOT NULL REFERENCES public.marketing_campanas(id_campana) ON DELETE CASCADE,
                  sent_at timestamp without time zone NOT NULL DEFAULT now(),
                  email text NOT NULL,
                  ok boolean NOT NULL DEFAULT false,
                  error text
                );
                """
            )
        )
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_marketing_envios_campana ON public.marketing_envios(id_campana);"))


@router.post("/audiencia/opt_out")
def audiencia_opt_out(
    payload: dict = Body(...),
    user: dict = Depends(get_current_user),
):
    role = _role(user)
    if not _is_admin(role) and role not in ("FINANZAS", "EJECUTIVO DE VENTAS", "JEFE DE OPERACIONES", "OPERACIONES"):
        raise HTTPException(status_code=403, detail="Sin permiso para opt-out")
    _ensure_schema()
    email = str(payload.get("email") or "").strip().lower()
    telefono = re.sub(r"\D+", "", str(payload.get("telefono") or ""))
    if not email and not telefono:
        raise HTTPException(status_code=400, detail="Debes enviar email o telefono")
    with get_connection() as conn:
        if email:
            conn.execute(text("UPDATE public.marketing_audiencia SET opt_out=true, updated_at=now() WHERE lower(email)=:e"), {"e": email})
        if telefono:
            conn.execute(text("UPDATE public.marketing_audiencia SET opt_out=true, updated_at=now() WHERE telefono=:t"), {"t": telefono})
    return {"ok": True, "email": email or None, "telefono": telefono or None}


@router.get("/campanas")
def campanas_list(
    yyyymm: str | None = None,
    limit: int = 50,
    offset: int = 0,
    user: dict = Depends(get_current_user),
):
    role = _role(user)
    if not _is_admin(role) and role not in ("FINANZAS", "EJECUTIVO DE VENTAS", "JEFE DE OPERACIONES", "OPERACIONES"):
        raise HTTPException(status_code=403, detail="Sin permiso para marketing")
    _ensure_schema()
    where = []
    params: dict[str, Any] = {"limit": int(max(1, min(200, limit))), "offset": int(max(0, offset))}
    if yyyymm:
        params["y"] = _parse_yyyymm(yyyymm)
        where.append("yyyymm=:y")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    with get_connection() as conn:
        total = conn.execute(text(f"SELECT COUNT(*) FROM public.marketing_campanas {where_sql}"), params).scalar_one()
        rows = conn.execute(
            text(
                f"""
                SELECT id_campana, created_at, created_by, yyyymm, id_marca, subject,
                       total_destinatarios, enviados, fallidos, last_error
                FROM public.marketing_campanas
                {where_sql}
                ORDER BY id_campana DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        ).mappings().all()
    return {"ok": True, "total": int(total), "items": list(rows)}


@router.post("/campanas/send")
def campanas_send(
    payload: dict = Body(...),
    user: dict = Depends(get_current_user),
):
    """
    Envío de campaña simple por SMTP usando el snapshot mensual.
    - Privacidad: se envía 1 correo por destinatario.
    - Recomendado: usar limit para enviar en lotes (p.ej. 50).
    """
    role = _role(user)
    if not _is_admin(role) and role not in ("FINANZAS", "EJECUTIVO DE VENTAS", "JEFE DE OPERACIONES", "OPERACIONES"):
        raise HTTPException(status_code=403, detail="Sin permiso para marketing")

    y = _parse_yyyymm(str(payload.get("yyyymm") or ""))
    id_marca = payload.get("id_marca")
    try:
        id_marca_i = int(id_marca) if id_marca not in (None, "", 0) else None
    except Exception:
        id_marca_i = None

    if not _is_admin(role):
        marcas = _user_marcas(user)
        if not marcas:
            raise HTTPException(status_code=403, detail="Sin marcas asignadas")
        if id_marca_i and id_marca_i not in set(marcas):
            raise HTTPException(status_code=403, detail="No autorizado para esta marca")

    subject = str(payload.get("subject") or "").strip()
    text_body = str(payload.get("text") or payload.get("text_body") or "").strip()
    html_body = payload.get("html") or payload.get("html_body")
    html_body = str(html_body).strip() if html_body is not None else None
    dry_run = bool(payload.get("dry_run") or False)
    limit = int(payload.get("limit") or 50)
    limit = max(1, min(200, limit))

    if not subject or not text_body:
        raise HTTPException(status_code=400, detail="subject y text_body son requeridos")

    _ensure_schema()

    where = ["yyyymm=:y", "opt_out IS FALSE", "email IS NOT NULL", "TRIM(email)<>''", "(legitimo_interes IS TRUE OR consentimiento IS TRUE)"]
    params: dict[str, Any] = {"y": y, "limit": limit}
    if id_marca_i:
        where.append("id_marca=:m")
        params["m"] = id_marca_i

    # no-admin con varias marcas: limitar si no se especifica id_marca
    if not _is_admin(role):
        marcas = _user_marcas(user)
        where.append("id_marca = ANY(:marcas)")
        params["marcas"] = marcas

    where_sql = " AND ".join(where)
    created_by = str(user.get("username") or user.get("id") or user.get("name") or "")

    with get_connection() as conn:
        recips = conn.execute(
            text(
                f"""
                SELECT id_audiencia, COALESCE(nombre,'') AS nombre, lower(trim(email)) AS email
                FROM public.marketing_audiencia
                WHERE {where_sql}
                ORDER BY id_audiencia ASC
                LIMIT :limit
                """
            ),
            params,
        ).mappings().all()

        camp = conn.execute(
            text(
                """
                INSERT INTO public.marketing_campanas(yyyymm, id_marca, subject, text_body, html_body, created_by, total_destinatarios)
                VALUES (:y, :m, :sub, :txt, :html, :by, :n)
                RETURNING id_campana
                """
            ),
            {"y": y, "m": id_marca_i, "sub": subject, "txt": text_body, "html": html_body, "by": created_by, "n": len(recips)},
        ).scalar_one()

        enviados = 0
        fallidos = 0
        last_err = ""

        for r in recips:
            addr = str(r.get("email") or "").strip()
            if not addr:
                continue
            if dry_run:
                enviados += 1
                conn.execute(
                    text("INSERT INTO public.marketing_envios(id_campana, email, ok, error) VALUES (:c,:e,true,:err)"),
                    {"c": camp, "e": addr, "err": "DRY_RUN"},
                )
                continue
            try:
                send_email(addr, subject, text_body, html=html_body)
                enviados += 1
                conn.execute(
                    text("INSERT INTO public.marketing_envios(id_campana, email, ok) VALUES (:c,:e,true)"),
                    {"c": camp, "e": addr},
                )
            except (EmailConfigError, Exception) as e:
                fallidos += 1
                last_err = str(e)
                conn.execute(
                    text("INSERT INTO public.marketing_envios(id_campana, email, ok, error) VALUES (:c,:e,false,:err)"),
                    {"c": camp, "e": addr, "err": last_err[:2000]},
                )

        conn.execute(
            text(
                """
                UPDATE public.marketing_campanas
                SET enviados=:ok, fallidos=:bad, last_error=:err
                WHERE id_campana=:c
                """
            ),
            {"ok": enviados, "bad": fallidos, "err": (last_err[:2000] if last_err else None), "c": camp},
        )

    return {"ok": True, "id_campana": int(camp), "dry_run": dry_run, "total": len(recips), "enviados": enviados, "fallidos": fallidos}


def _parse_yyyymm(v: str) -> int:
    s = str(v or "").strip()
    if not re.fullmatch(r"\d{6}", s or ""):
        raise HTTPException(status_code=400, detail="yyyymm inválido (usa YYYYMM)")
    return int(s)


def _hash_key(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:20]


@router.post("/audiencia/snapshot")
def snapshot_audiencia(
    yyyymm: str = Query(..., description="Mes objetivo (YYYYMM) según fecha_evento"),
    id_marca: int | None = None,
    dry_run: bool = False,
    user: dict = Depends(get_current_user),
):
    """
    Crea/actualiza un snapshot mensual de contactos para marketing.
    Reglas:
    - Mes se define por fecha_evento
    - Legitimo interés por defecto (legitimo_interes=true)
    - Dedupe por email si existe, si no por teléfono (digits)
    - Unique: (yyyymm, id_marca, dedupe_key)
    """
    role = _role(user)
    if not _is_admin(role) and role not in ("FINANZAS", "EJECUTIVO DE VENTAS", "JEFE DE OPERACIONES", "OPERACIONES"):
        raise HTTPException(status_code=403, detail="Sin permiso para marketing")
    if not _is_admin(role):
        # no-admin solo puede operar sobre sus marcas
        marcas = _user_marcas(user)
        if not marcas:
            raise HTTPException(status_code=403, detail="Sin marcas asignadas")
        if id_marca and int(id_marca) not in set(marcas):
            raise HTTPException(status_code=403, detail="No autorizado para esta marca")

    y = _parse_yyyymm(yyyymm)
    _ensure_schema()

    with get_connection() as conn:
        # trae leads del mes por fecha_evento; asume columnas estándar ya existentes en este proyecto.
        marca_filter = ""
        params: dict[str, Any] = {"yyyymm": y}
        if id_marca:
            marca_filter = " AND l.id_marca = :id_marca "
            params["id_marca"] = int(id_marca)

        # Resolvemos nombre/estado/marca/comuna en SQL, y hacemos dedupe_key en Python (más control).
        q = f"""
          SELECT
            l.id_lead,
            l.fecha_evento::date AS fecha_evento,
            l.id_marca,
            l.id_comuna,
            COALESCE(l.cliente, l.nombre_cliente, '') AS nombre,
            COALESCE(l.email,'') AS email,
            COALESCE(l.telefono,'') AS telefono,
            COALESCE(tc.nombre, '') AS tipo_cliente,
            COALESCE(l.plataforma,'') AS plataforma,
            COALESCE(e.nombre,'') AS estado
          FROM public.leads l
          LEFT JOIN public.tipo_cliente tc ON tc.id_tipo_cliente = l.id_tipo_cliente
          LEFT JOIN public.estados_lead e ON e.id_estado = l.id_estado
          WHERE l.fecha_evento IS NOT NULL
            AND (EXTRACT(YEAR FROM l.fecha_evento)::int * 100 + EXTRACT(MONTH FROM l.fecha_evento)::int) = :yyyymm
            AND COALESCE(l.is_deleted,false)=false
            {marca_filter}
          ORDER BY l.id_lead DESC
        """
        rows = conn.execute(text(q), params).mappings().all()

        to_upsert: list[dict[str, Any]] = []
        for r in rows:
            email = (r.get("email") or "").strip().lower()
            tel_raw = (r.get("telefono") or "").strip()
            tel_digits = re.sub(r"\D+", "", tel_raw)
            base = ""
            if email and "@" in email:
                base = f"e:{email}"
            elif tel_digits:
                base = f"t:{tel_digits}"
            else:
                # si no hay dato de contacto, igual lo dejamos (para push futuro) pero con key por lead
                base = f"l:{int(r.get('id_lead') or 0)}"
            dedupe_key = _hash_key(base)

            to_upsert.append(
                {
                    "yyyymm": y,
                    "id_marca": int(r.get("id_marca") or 0) or None,
                    "id_comuna": int(r.get("id_comuna") or 0) or None,
                    "id_lead_origen": int(r.get("id_lead") or 0) or None,
                    "fecha_evento": r.get("fecha_evento"),
                    "nombre": (r.get("nombre") or "").strip() or None,
                    "email": email or None,
                    "telefono": tel_raw or None,
                    "tipo_cliente": (r.get("tipo_cliente") or "").strip() or None,
                    "plataforma": (r.get("plataforma") or "").strip() or None,
                    "estado": (r.get("estado") or "").strip() or None,
                    "dedupe_key": dedupe_key,
                }
            )

        if dry_run:
            return {"ok": True, "yyyymm": y, "rows": len(rows), "upserts": len(to_upsert)}

        upserted = 0
        for it in to_upsert:
            conn.execute(
                text(
                    """
                    INSERT INTO public.marketing_audiencia
                      (yyyymm,id_marca,id_comuna,id_lead_origen,fecha_evento,nombre,email,telefono,tipo_cliente,plataforma,estado,dedupe_key,consentimiento,legitimo_interes,updated_at)
                    VALUES
                      (:yyyymm,:id_marca,:id_comuna,:id_lead_origen,:fecha_evento,:nombre,:email,:telefono,:tipo_cliente,:plataforma,:estado,:dedupe_key,false,true,now())
                    ON CONFLICT (yyyymm,id_marca,dedupe_key)
                    DO UPDATE SET
                      id_lead_origen = EXCLUDED.id_lead_origen,
                      fecha_evento = EXCLUDED.fecha_evento,
                      nombre = COALESCE(EXCLUDED.nombre, public.marketing_audiencia.nombre),
                      email = COALESCE(EXCLUDED.email, public.marketing_audiencia.email),
                      telefono = COALESCE(EXCLUDED.telefono, public.marketing_audiencia.telefono),
                      tipo_cliente = COALESCE(EXCLUDED.tipo_cliente, public.marketing_audiencia.tipo_cliente),
                      plataforma = COALESCE(EXCLUDED.plataforma, public.marketing_audiencia.plataforma),
                      estado = COALESCE(EXCLUDED.estado, public.marketing_audiencia.estado),
                      updated_at = now()
                    """
                ),
                it,
            )
            upserted += 1

        return {"ok": True, "yyyymm": y, "rows": len(rows), "upserted": upserted}


@router.get("/audiencia")
def list_audiencia(
    yyyymm: str = Query(..., description="YYYYMM"),
    id_marca: int | None = None,
    q: str = "",
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
):
    role = _role(user)
    if not _is_admin(role) and role not in ("FINANZAS", "EJECUTIVO DE VENTAS", "JEFE DE OPERACIONES", "OPERACIONES"):
        raise HTTPException(status_code=403, detail="Sin permiso para marketing")
    y = _parse_yyyymm(yyyymm)
    _ensure_schema()

    if not _is_admin(role):
        marcas = _user_marcas(user)
        if not marcas:
            return {"ok": True, "total": 0, "items": []}
        if id_marca and int(id_marca) not in set(marcas):
            raise HTTPException(status_code=403, detail="No autorizado para esta marca")

    with get_connection() as conn:
        where = ["yyyymm = :yyyymm"]
        params: dict[str, Any] = {"yyyymm": y, "limit": limit, "offset": offset}
        if id_marca:
            where.append("id_marca = :id_marca")
            params["id_marca"] = int(id_marca)
        if q.strip():
            where.append("(lower(coalesce(nombre,'')) LIKE :q OR lower(coalesce(email,'')) LIKE :q OR coalesce(telefono,'') LIKE :q)")
            params["q"] = f"%{q.strip().lower()}%"
        where_sql = " WHERE " + " AND ".join(where)
        total = conn.execute(text(f"SELECT COUNT(*) FROM public.marketing_audiencia {where_sql}"), params).scalar_one()
        rows = conn.execute(
            text(
                f"""
                SELECT id_audiencia, yyyymm, id_marca, id_comuna, id_lead_origen, fecha_evento,
                       nombre, email, telefono, tipo_cliente, plataforma, estado,
                       consentimiento, legitimo_interes, created_at, updated_at
                FROM public.marketing_audiencia
                {where_sql}
                ORDER BY fecha_evento DESC NULLS LAST, id_audiencia DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        ).mappings().all()
        return {"ok": True, "total": int(total), "items": list(rows)}


@router.get("/audiencia/export.csv")
def export_audiencia_csv(
    yyyymm: str = Query(..., description="YYYYMM"),
    id_marca: int | None = None,
    user: dict = Depends(get_current_user),
):
    role = _role(user)
    if not _is_admin(role) and role not in ("FINANZAS", "EJECUTIVO DE VENTAS", "JEFE DE OPERACIONES", "OPERACIONES"):
        raise HTTPException(status_code=403, detail="Sin permiso para marketing")
    y = _parse_yyyymm(yyyymm)
    _ensure_schema()

    if not _is_admin(role):
        marcas = _user_marcas(user)
        if not marcas:
            raise HTTPException(status_code=403, detail="Sin marcas asignadas")
        if id_marca and int(id_marca) not in set(marcas):
            raise HTTPException(status_code=403, detail="No autorizado para esta marca")

    with get_connection() as conn:
        where = ["a.yyyymm = :yyyymm"]
        params: dict[str, Any] = {"yyyymm": y}
        if id_marca:
            where.append("a.id_marca = :id_marca")
            params["id_marca"] = int(id_marca)
        where_sql = " WHERE " + " AND ".join(where)
        rows = conn.execute(
            text(
                f"""
                SELECT
                  a.yyyymm,
                  COALESCE(m.nombre, m.marca,'') AS marca,
                  COALESCE(c.nombre,'') AS comuna,
                  a.nombre, a.email, a.telefono,
                  a.tipo_cliente, a.plataforma, a.estado,
                  a.consentimiento, a.legitimo_interes,
                  a.fecha_evento
                FROM public.marketing_audiencia a
                LEFT JOIN public.marcas m ON m.id_marca = a.id_marca
                LEFT JOIN public.comunas c ON c.id_comuna = a.id_comuna
                {where_sql}
                ORDER BY a.fecha_evento DESC NULLS LAST, a.id_audiencia DESC
                """
            ),
            params,
        ).mappings().all()

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["yyyymm", "marca", "comuna", "nombre", "email", "telefono", "tipo_cliente", "plataforma", "estado", "consentimiento", "legitimo_interes", "fecha_evento"])
    for r in rows:
        w.writerow(
            [
                r.get("yyyymm"),
                r.get("marca"),
                r.get("comuna"),
                r.get("nombre"),
                r.get("email"),
                r.get("telefono"),
                r.get("tipo_cliente"),
                r.get("plataforma"),
                r.get("estado"),
                bool(r.get("consentimiento")),
                bool(r.get("legitimo_interes")),
                str(r.get("fecha_evento") or ""),
            ]
        )
    csv_text = out.getvalue()
    filename = f"audiencia_{y}.csv"
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
