from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import text

from backend.core.db import get_connection
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
                  tags_json text,
                  dedupe_key text NOT NULL,
                  created_at timestamp without time zone NOT NULL DEFAULT now(),
                  updated_at timestamp without time zone NOT NULL DEFAULT now()
                );
                """
            )
        )
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

