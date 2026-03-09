from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional
import re
import unicodedata

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from sqlalchemy import text

from backend.core.db import get_connection
from backend.routers.auth import get_current_user

router = APIRouter(prefix="/assets", tags=["assets"])

def _norm_key(s: str) -> str:
    s = str(s or "").strip().lower()
    try:
        s = unicodedata.normalize("NFD", s)
        s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    except Exception:
        pass
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return s

def _suggest_code_prefix(nombre: str, tipo: str = "MAQUINARIA") -> str:
    """
    COD_TIPO sugerido para el catálogo de maquinaria/camiones.
    El usuario puede definir su propio COD_TIPO (ej: MaqAlg, CarCla, HorEle, ...).
    """
    n = _norm_key(nombre)
    t = str(tipo or "").strip().upper()

    # Camiones
    if t == "CAMION":
        return "Cam"

    # Maquinaria / carritos: catálogo estándar GD (editable por Admin)
    # Nota: estas llaves vienen del usuario: COD_TIPO / TIPO_MAQUINA.
    if "algodon" in n:
        return "MaqAlg"
    if "carrito gas" in n or ("carrito" in n and "gas" in n):
        return "CarGas"
    if "carrito clasico" in n or ("carrito" in n and "clasico" in n):
        return "CarCla"
    if "carrito express" in n or ("carrito" in n and "express" in n):
        return "CarExp"
    if "carro grande" in n:
        return "CarGra"
    if "carro pequeno" in n or "carro pequen" in n or "car pequeño" in (nombre or "").lower():
        return "CarPeq"
    if "carro mediano" in n:
        return "CarMed"
    if ("carro" in n or "carrito" in n) and "rojo" in n:
        return "CarRoj"
    if "frappera" in n:
        return "MaqFra"
    if "freidora" in n and "gas" in n:
        return "FreGas"
    if "freidora" in n and ("electrica" in n or "eléctrica" in (nombre or "")):
        return "FreEle"
    if "horno" in n and ("electrico" in n or "eléctrico" in (nombre or "")):
        return "HorEle"
    if "horno" in n and "industrial" in n:
        return "HorIns"
    if "juguera" in n or "jugueras" in n:
        return "Jug"
    if "percol" in n:
        return "Per"
    if "plancha" in n:
        return "Pla"
    if "popcorn" in n or "pop corn" in n or ("maquina" in n and "pop" in n):
        return "MaqPop"

    # Default: primeras 4-6 letras alfanuméricas (sin espacios)
    raw = re.sub(r"[^A-Za-z0-9]+", "", unicodedata.normalize("NFKD", (nombre or "")))
    raw = raw.strip()
    return (raw[:6] or "Tipo")

def _next_asset_code(conn, prefix: str) -> tuple[str, int]:
    """
    Retorna (code, next_n) usando el máximo sufijo numérico existente para ese prefijo.
    Acepta formatos antiguos tipo "POP-001" y nuevos tipo "POP01".
    """
    pref = (prefix or "").strip()
    if not pref:
        pref = "Tipo"
    max_n = conn.execute(
        text(
            """
            SELECT COALESCE(MAX(CAST(regexp_replace(code, '.*?(\\d+)$', '\\1') AS INT)), 0)
            FROM public.assets
            WHERE LOWER(COALESCE(code,'')) LIKE LOWER(:p)
            """
        ),
        {"p": pref + "%"},
    ).scalar() or 0
    start = int(max_n) + 1
    # 2 dígitos hasta 99; luego expandimos para evitar colisiones visuales.
    width = 2 if (start <= 99) else 3
    return f"{pref}{start:0{width}d}", start


def _role(user: dict) -> str:
    return str(user.get("role") or user.get("rol") or "").upper()

def _role_key(user: dict) -> str:
    """
    Normaliza el rol para comparaciones robustas (evita falsos 403 por tildes/espacios).
    Ej: "Jefe de Operaciones" -> "jefedeoperaciones"
    """
    raw = str(user.get("role") or user.get("rol") or "").strip().lower()
    if not raw:
        return ""
    raw = unicodedata.normalize("NFD", raw)
    raw = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
    raw = re.sub(r"[^a-z0-9]+", "", raw)
    return raw


def _require_assets_access(user: dict) -> None:
    # Activos/maquinaria/camiones es Operaciones/Compras/Bodega/Admin.
    # No debe estar disponible para Ejecutivos/ventas (evita cambios accidentales).
    rk = _role_key(user)
    if not rk:
        raise HTTPException(403, "Sin permiso para Activos")

    # Match por keyword para cubrir variantes ("ADMINISTRADOR", "JEFEDEOPERACIONES", etc.).
    allowed = (
        ("admin" in rk)
        or ("superadmin" in rk)
        or ("operac" in rk)
        or ("compra" in rk)
        or ("bodeg" in rk)
    )
    if not allowed:
        raise HTTPException(403, "Sin permiso para Activos")


def _ensure_schema(conn) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.asset_categories (
              id_category BIGSERIAL PRIMARY KEY,
              nombre TEXT NOT NULL UNIQUE,
              tipo TEXT NOT NULL DEFAULT 'MAQUINARIA', -- MAQUINARIA | CAMION | OTRO
              grupo TEXT, -- CARROS | ELECTRODOMESTICO | MUEBLES | UTENSILIOS | OTRO (opcional)
              code_prefix TEXT,
              foto_url TEXT,
              is_active BOOLEAN NOT NULL DEFAULT TRUE,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )
    )
    # hardening: columnas nuevas para instalaciones existentes
    conn.execute(text("ALTER TABLE public.asset_categories ADD COLUMN IF NOT EXISTS tipo TEXT NOT NULL DEFAULT 'MAQUINARIA'"))
    conn.execute(text("ALTER TABLE public.asset_categories ADD COLUMN IF NOT EXISTS grupo TEXT"))
    conn.execute(text("ALTER TABLE public.asset_categories ADD COLUMN IF NOT EXISTS code_prefix TEXT"))
    conn.execute(text("ALTER TABLE public.asset_categories ADD COLUMN IF NOT EXISTS foto_url TEXT"))
    conn.execute(text("ALTER TABLE public.asset_categories ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE"))
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.assets (
              id_asset BIGSERIAL PRIMARY KEY,
              id_category BIGINT REFERENCES public.asset_categories(id_category) ON DELETE SET NULL,
              nombre TEXT NOT NULL,
              code TEXT UNIQUE,
              status TEXT NOT NULL DEFAULT 'OK',
              estado_reparacion TEXT,
              ubicacion TEXT,
              notas TEXT,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.asset_maintenance (
              id_maint BIGSERIAL PRIMARY KEY,
              id_asset BIGINT NOT NULL REFERENCES public.assets(id_asset) ON DELETE CASCADE,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              status TEXT NOT NULL,
              detalle TEXT,
              realizado_por TEXT,
              next_due DATE
            );
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.asset_assignments (
              id_assign BIGSERIAL PRIMARY KEY,
              id_asset BIGINT NOT NULL REFERENCES public.assets(id_asset) ON DELETE CASCADE,
              id_lead BIGINT REFERENCES public.leads(id_lead) ON DELETE SET NULL,
              from_at TIMESTAMPTZ,
              to_at TIMESTAMPTZ,
              status TEXT NOT NULL DEFAULT 'ASIGNADO',
              notes TEXT,
              conductor TEXT,
              operadores TEXT,
              created_by TEXT,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )
    )
    # hardening: columnas nuevas para trazabilidad (operadores/chofer) por evento
    conn.execute(text("ALTER TABLE public.asset_assignments ADD COLUMN IF NOT EXISTS conductor TEXT"))
    conn.execute(text("ALTER TABLE public.asset_assignments ADD COLUMN IF NOT EXISTS operadores TEXT"))
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.vehicle_checklists (
              id_check BIGSERIAL PRIMARY KEY,
              id_lead BIGINT REFERENCES public.leads(id_lead) ON DELETE SET NULL,
              tipo TEXT NOT NULL, -- ENTREGA | DEVOLUCION
              payload JSONB NOT NULL DEFAULT '{}'::jsonb,
              created_by TEXT,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )
    )


@router.get("/categories")
def list_categories(
    tipo: str | None = Query(default=None),
    all: bool = Query(default=False, description="Si true, incluye inactivos"),
    user: dict = Depends(get_current_user),
):
    _require_assets_access(user)
    tipo_norm = (tipo or "").strip().upper()
    with get_connection() as conn:
        _ensure_schema(conn)
        where = []
        params: Dict[str, Any] = {}
        if not all:
            where.append("is_active IS TRUE")
        if tipo_norm:
            where.append("UPPER(tipo)=:t")
            params["t"] = tipo_norm
        rows = conn.execute(
            text(
                f"""
                SELECT id_category, nombre, tipo, grupo, code_prefix, foto_url, is_active
                FROM public.asset_categories
                {"WHERE " + " AND ".join(where) if where else ""}
                ORDER BY nombre
                """
            ),
            params,
        ).fetchall()
        return {
            "ok": True,
            "items": [
                {
                    "id_category": int(r[0]),
                    "nombre": r[1],
                    "tipo": r[2],
                    "grupo": r[3],
                    "code_prefix": r[4],
                    "foto_url": r[5],
                    "is_active": bool(r[6]) if r[6] is not None else True,
                }
                for r in rows
            ],
        }


@router.post("/categories")
def create_category(payload: dict = Body(...), user: dict = Depends(get_current_user)):
    _require_assets_access(user)
    # alias: tipo_maquina -> nombre, cod_tipo -> code_prefix
    nombre = str(payload.get("tipo_maquina") or payload.get("nombre") or "").strip()
    if not nombre:
        raise HTTPException(400, "nombre requerido")
    tipo = str(payload.get("tipo") or "MAQUINARIA").strip().upper()[:32] or "MAQUINARIA"
    grupo = (str(payload.get("grupo") or "").strip() or None)
    code_prefix = (str(payload.get("cod_tipo") or payload.get("code_prefix") or "").strip() or None)
    foto_url = (str(payload.get("foto_url") or payload.get("foto") or "").strip() or None)
    if not code_prefix:
        code_prefix = _suggest_code_prefix(nombre, tipo)
    with get_connection() as conn:
        _ensure_schema(conn)
        # best-effort: no duplicar cod_tipo en 2 categorías distintas
        if code_prefix:
            other = conn.execute(
                text(
                    """
                    SELECT id_category, nombre
                    FROM public.asset_categories
                    WHERE LOWER(COALESCE(code_prefix,'')) = LOWER(:p)
                    LIMIT 1
                    """
                ),
                {"p": code_prefix},
            ).first()
            if other and str(other[1] or "") != nombre:
                raise HTTPException(400, f"COD_TIPO ya existe en otra categoría: {other[1]}")
        conn.execute(
            text(
                """
                INSERT INTO public.asset_categories(nombre,tipo,grupo,code_prefix,foto_url,is_active)
                VALUES (:n,:t,:g,:p,:f,TRUE)
                ON CONFLICT(nombre) DO UPDATE
                  SET tipo=EXCLUDED.tipo,
                      grupo=COALESCE(EXCLUDED.grupo, public.asset_categories.grupo),
                      code_prefix=COALESCE(EXCLUDED.code_prefix, public.asset_categories.code_prefix),
                      foto_url=COALESCE(EXCLUDED.foto_url, public.asset_categories.foto_url),
                      is_active=TRUE
                """
            ),
            {"n": nombre, "t": tipo, "g": grupo, "p": code_prefix, "f": foto_url},
        )
        conn.commit()
        return {"ok": True}


@router.put("/categories/{id_category}")
def update_category(id_category: int, payload: dict = Body(...), user: dict = Depends(get_current_user)):
    _require_assets_access(user)
    tipo = str(payload.get("tipo") or "").strip().upper()[:32] or None
    grupo = (str(payload.get("grupo") or "").strip() or None)
    code_prefix = (str(payload.get("cod_tipo") or payload.get("code_prefix") or "").strip() or None)
    foto_url = (str(payload.get("foto_url") or payload.get("foto") or "").strip() or None)
    is_active = payload.get("is_active")
    nombre = (str(payload.get("tipo_maquina") or payload.get("nombre") or "").strip() or None)
    with get_connection() as conn:
        _ensure_schema(conn)
        conn.execute(
            text(
                """
                UPDATE public.asset_categories
                SET nombre=COALESCE(:n, nombre),
                    tipo=COALESCE(:t, tipo),
                    grupo=COALESCE(:g, grupo),
                    code_prefix=COALESCE(:p, code_prefix),
                    foto_url=COALESCE(:f, foto_url),
                    is_active=COALESCE(:a, is_active)
                WHERE id_category=:id
                """
            ),
            {"id": int(id_category), "n": nombre, "t": tipo, "g": grupo, "p": code_prefix, "f": foto_url, "a": is_active},
        )
        conn.commit()
        return {"ok": True}


@router.delete("/categories/{id_category}")
def delete_category(
    id_category: int = Path(..., ge=1),
    hard: bool = Query(False),
    user: dict = Depends(get_current_user),
):
    """
    CRUD categorías:
    - hard=false (default): desactiva (is_active=false)
    - hard=true: borra SOLO si no hay activos asociados
    """
    _require_assets_access(user)
    with get_connection() as conn:
        _ensure_schema(conn)
        ok = conn.execute(text("SELECT 1 FROM public.asset_categories WHERE id_category=:id"), {"id": int(id_category)}).scalar()
        if not ok:
            raise HTTPException(404, "Categoría no existe")

        n_assets = conn.execute(text("SELECT COUNT(*) FROM public.assets WHERE id_category=:id"), {"id": int(id_category)}).scalar() or 0
        if hard:
            if int(n_assets) > 0:
                raise HTTPException(400, "No puedo borrar: hay inventario asociado. Desactiva en vez de borrar.")
            conn.execute(text("DELETE FROM public.asset_categories WHERE id_category=:id"), {"id": int(id_category)})
        else:
            conn.execute(text("UPDATE public.asset_categories SET is_active=FALSE WHERE id_category=:id"), {"id": int(id_category)})
        conn.commit()
        return {"ok": True, "hard": bool(hard), "assets": int(n_assets)}


@router.get("")
def list_assets(
    q: str = Query("", max_length=120),
    id_category: int | None = Query(None),
    tipo: str | None = Query(default=None),
    user: dict = Depends(get_current_user),
):
    _require_assets_access(user)
    tipo_norm = (tipo or "").strip().upper()
    with get_connection() as conn:
        _ensure_schema(conn)
        where = ["1=1"]
        params: Dict[str, Any] = {}
        if q.strip():
            where.append("(UPPER(a.nombre) LIKE UPPER(:q) OR UPPER(COALESCE(a.code,'')) LIKE UPPER(:q))")
            params["q"] = f"%{q.strip()}%"
        if id_category:
            where.append("a.id_category = :c")
            params["c"] = int(id_category)
        if tipo_norm:
            where.append("UPPER(COALESCE(c.tipo,'')) = :t")
            params["t"] = tipo_norm
        rows = conn.execute(
            text(
                f"""
                SELECT a.id_asset, a.nombre, a.code, a.status, a.estado_reparacion, a.ubicacion,
                       a.id_category, c.nombre as categoria, c.tipo, c.code_prefix,
                       a.updated_at
                FROM public.assets a
                LEFT JOIN public.asset_categories c ON c.id_category=a.id_category
                WHERE {' AND '.join(where)}
                ORDER BY a.nombre
                LIMIT 2000
                """
            ),
            params,
        ).fetchall()
        return {
            "ok": True,
            "items": [
                {
                    "id_asset": int(r[0]),
                    "nombre": r[1],
                    "code": r[2],
                    "status": r[3],
                    "estado_reparacion": r[4],
                    "ubicacion": r[5],
                    "id_category": r[6],
                    "categoria": r[7],
                    "tipo": r[8],
                    "code_prefix": r[9],
                    "updated_at": r[10].isoformat() if hasattr(r[10], "isoformat") else str(r[10] or ""),
                }
                for r in rows
            ],
        }


@router.post("")
def create_asset(payload: dict = Body(...), user: dict = Depends(get_current_user)):
    _require_assets_access(user)
    nombre = str(payload.get("nombre") or "").strip()
    if not nombre:
        raise HTTPException(400, "nombre requerido")
    with get_connection() as conn:
        _ensure_schema(conn)
        id_category = int(payload.get("id_category")) if str(payload.get("id_category") or "").isdigit() else None
        code_in = (str(payload.get("code") or "").strip() or None)
        if not code_in and id_category:
            cat = conn.execute(
                text("SELECT nombre, code_prefix, tipo FROM public.asset_categories WHERE id_category=:id LIMIT 1"),
                {"id": id_category},
            ).mappings().first()
            if cat:
                # Importante: NO forzamos mayúsculas. COD_TIPO puede ser mixto (ej: CarCla).
                pref = (cat.get("code_prefix") or "").strip() or _suggest_code_prefix(
                    str(cat.get("nombre") or ""),
                    str(cat.get("tipo") or "MAQUINARIA"),
                )
                code_in, _ = _next_asset_code(conn, pref)
        rid = conn.execute(
            text(
                """
                INSERT INTO public.assets(id_category,nombre,code,status,estado_reparacion,ubicacion,notas,updated_at)
                VALUES (:c,:n,:code,:st,:er,:u,:no, now())
                RETURNING id_asset, code
                """
            ),
            {
                "c": id_category,
                "n": nombre,
                "code": code_in,
                "st": str(payload.get("status") or "OK").strip().upper()[:32] or "OK",
                "er": (str(payload.get("estado_reparacion") or "").strip() or None),
                "u": (str(payload.get("ubicacion") or "").strip() or None),
                "no": (str(payload.get("notas") or "").strip() or None),
            },
        ).first()
        conn.commit()
        out_id = int(rid[0]) if rid and rid[0] is not None else None
        out_code = rid[1] if rid and len(rid) > 1 else None
        return {"ok": True, "id_asset": out_id, "code": out_code}


@router.put("/{id_asset}")
def update_asset(id_asset: int = Path(..., ge=1), payload: dict = Body(...), user: dict = Depends(get_current_user)):
    """
    CRUD: editar un activo (unidad).
    Nota: preferimos "soft delete" (status=BAJA) en vez de borrar historia por accidente.
    """
    _require_assets_access(user)
    with get_connection() as conn:
        _ensure_schema(conn)
        row = conn.execute(
            text("SELECT id_asset, id_category, code FROM public.assets WHERE id_asset=:id LIMIT 1"),
            {"id": int(id_asset)},
        ).mappings().first()
        if not row:
            raise HTTPException(404, "Activo no existe")

        data: dict[str, Any] = {}
        if "nombre" in payload:
            nombre = str(payload.get("nombre") or "").strip()
            if nombre:
                data["nombre"] = nombre[:220]
        if "id_category" in payload:
            v = payload.get("id_category")
            if v in ("", None, "null"):
                data["id_category"] = None
            elif str(v).isdigit():
                data["id_category"] = int(v)
        if "status" in payload:
            st = str(payload.get("status") or "").strip().upper()[:32]
            if st:
                data["status"] = st
        if "estado_reparacion" in payload:
            er = str(payload.get("estado_reparacion") or "").strip()
            data["estado_reparacion"] = er[:220] if er else None
        if "ubicacion" in payload:
            ub = str(payload.get("ubicacion") or "").strip()
            data["ubicacion"] = ub[:220] if ub else None
        if "notas" in payload:
            no = str(payload.get("notas") or "").strip()
            data["notas"] = no if no else None

        # Code: si viene vacío y hay categoría, autogeneramos.
        if "code" in payload:
            code_in = str(payload.get("code") or "").strip() or None
            if not code_in:
                id_cat = data.get("id_category", row.get("id_category"))
                if id_cat:
                    cat = conn.execute(
                        text("SELECT nombre, code_prefix, tipo FROM public.asset_categories WHERE id_category=:id LIMIT 1"),
                        {"id": int(id_cat)},
                    ).mappings().first()
                    if cat:
                        pref = (cat.get("code_prefix") or "").strip() or _suggest_code_prefix(
                            str(cat.get("nombre") or ""),
                            str(cat.get("tipo") or "MAQUINARIA"),
                        )
                        code_in, _ = _next_asset_code(conn, pref)
            data["code"] = code_in

        if not data:
            return {"ok": True, "updated": False}

        sets = ", ".join([f"{k}=:{k}" for k in data.keys()] + ["updated_at=now()"])
        data["id"] = int(id_asset)
        try:
            conn.execute(text(f"UPDATE public.assets SET {sets} WHERE id_asset=:id"), data)
            conn.commit()
        except Exception as e:
            # mensaje simple; típicamente será UNIQUE(code)
            raise HTTPException(400, f"No pude actualizar activo: {str(e).splitlines()[0]}")
        return {"ok": True, "updated": True}


@router.delete("/{id_asset}")
def delete_asset(
    id_asset: int = Path(..., ge=1),
    hard: bool = Query(False),
    user: dict = Depends(get_current_user),
):
    """
    Delete seguro:
    - hard=false (default): status=BAJA (mantiene historial)
    - hard=true: borra fila (cascada a maintenance/assignments)
    """
    _require_assets_access(user)
    who = str(user.get("nombre") or user.get("name") or user.get("username") or "").strip() or "OPS"
    with get_connection() as conn:
        _ensure_schema(conn)
        ok = conn.execute(text("SELECT 1 FROM public.assets WHERE id_asset=:id"), {"id": int(id_asset)}).scalar()
        if not ok:
            raise HTTPException(404, "Activo no existe")
        if hard:
            conn.execute(text("DELETE FROM public.assets WHERE id_asset=:id"), {"id": int(id_asset)})
            conn.commit()
            return {"ok": True, "hard": True}

        # Soft delete => BAJA + nota
        note = f"[BAJA {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} by {who}]"
        conn.execute(
            text(
                """
                UPDATE public.assets
                SET status='BAJA',
                    notas=CASE WHEN notas IS NULL OR btrim(notas)='' THEN :n
                               ELSE notas || E'\\n' || :n END,
                    updated_at=now()
                WHERE id_asset=:id
                """
            ),
            {"id": int(id_asset), "n": note},
        )
        conn.commit()
        return {"ok": True, "hard": False}


@router.get("/category_stats")
def category_stats(tipo: str | None = Query(default=None), user: dict = Depends(get_current_user)):
    """
    Conteo por categoría + prefijo (para Inventario por unidad).
    """
    _require_assets_access(user)
    tipo_norm = (tipo or "").strip().upper()
    with get_connection() as conn:
        _ensure_schema(conn)
        where = ["c.is_active IS TRUE"]
        params: Dict[str, Any] = {}
        if tipo_norm:
            where.append("UPPER(c.tipo)=:t")
            params["t"] = tipo_norm
        rows = conn.execute(
            text(
                f"""
                SELECT
                  c.id_category,
                  c.nombre,
                  c.tipo,
                  c.grupo,
                  c.code_prefix,
                  c.foto_url,
                  COUNT(a.id_asset)::int AS n
                FROM public.asset_categories c
                LEFT JOIN public.assets a ON a.id_category=c.id_category
                WHERE {' AND '.join(where)}
                GROUP BY 1,2,3,4,5,6
                ORDER BY c.nombre
                """
            ),
            params,
        ).fetchall()
        return {
            "ok": True,
            "items": [
                {
                    "id_category": int(r[0]),
                    "nombre": r[1],
                    "tipo": r[2],
                    "grupo": r[3],
                    "code_prefix": r[4],
                    "foto_url": r[5],
                    "cantidad": int(r[6] or 0),
                }
                for r in rows
            ],
        }


@router.post("/bulk_create")
def bulk_create_assets(payload: dict = Body(...), user: dict = Depends(get_current_user)):
    """
    Crea N activos por unidad para una categoría, asignando códigos secuenciales:
    {code_prefix}-{NNN}
    """
    _require_assets_access(user)
    id_category = payload.get("id_category")
    qty = payload.get("qty") or payload.get("cantidad") or 1
    if not str(id_category or "").isdigit():
        raise HTTPException(400, "id_category requerido")
    try:
        qty_i = int(qty)
    except Exception:
        qty_i = 1
    qty_i = max(1, min(200, qty_i))

    base_name = str(payload.get("base_name") or payload.get("nombre") or "Unidad").strip()[:120] or "Unidad"
    # COD_TIPO puede ser mixto (ej: MaqAlg). Preservamos el casing.
    prefix = (str(payload.get("code_prefix") or "").strip() or None)

    with get_connection() as conn:
        _ensure_schema(conn)
        cat = conn.execute(
            text("SELECT nombre, code_prefix FROM public.asset_categories WHERE id_category=:id LIMIT 1"),
            {"id": int(id_category)},
        ).mappings().first()
        if not cat:
            raise HTTPException(404, "Categoría no existe")
        cat_name = (cat.get("nombre") or "").strip() or "Categoria"
        prefix = prefix or (cat.get("code_prefix") or "").strip() or None
        if not prefix:
            prefix = _suggest_code_prefix(cat_name, "MAQUINARIA")

        # Encuentra el máximo sufijo numérico ya usado para ese prefijo (acepta POP-001 y POP01).
        max_n = conn.execute(
            text(
                """
                SELECT COALESCE(MAX(CAST(regexp_replace(code, '.*?(\\d+)$', '\\1') AS INT)), 0)
                FROM public.assets
                WHERE LOWER(COALESCE(code,'')) LIKE LOWER(:p)
                """
            ),
            {"p": prefix + "%"},
        ).scalar() or 0
        start = int(max_n) + 1
        width = 2 if (start + qty_i - 1) <= 99 else 3

        created = []
        for i in range(qty_i):
            n = start + i
            code = f"{prefix}{n:0{width}d}"
            nombre = f"{cat_name} {n}"
            # Permite override de nombre base: "Carrito Clásico" etc.
            if base_name and base_name.lower() != "unidad":
                nombre = f"{base_name} {n}"
            rid = conn.execute(
                text(
                    """
                    INSERT INTO public.assets(id_category,nombre,code,status,updated_at)
                    VALUES (:c,:n,:code,'OK',now())
                    RETURNING id_asset
                    """
                ),
                {"c": int(id_category), "n": nombre, "code": code},
            ).scalar()
            created.append({"id_asset": int(rid), "nombre": nombre, "code": code})

        # guarda prefix en categoría si estaba vacío
        try:
            conn.execute(
                text(
                    """
                    UPDATE public.asset_categories
                    SET code_prefix=COALESCE(code_prefix,:p)
                    WHERE id_category=:id
                    """
                ),
                {"id": int(id_category), "p": prefix},
            )
        except Exception:
            pass

        conn.commit()
        return {"ok": True, "created": created}


@router.get("/{id_asset}/history")
def asset_history(id_asset: int = Path(..., ge=1), user: dict = Depends(get_current_user)):
    """
    Ficha: mantenciones + asignaciones (con resumen del lead si existe).
    """
    _require_assets_access(user)
    with get_connection() as conn:
        _ensure_schema(conn)
        a = conn.execute(
            text(
                """
                SELECT a.id_asset,a.nombre,a.code,a.status,a.estado_reparacion,a.ubicacion,a.notas,
                       c.nombre AS categoria, c.tipo, c.code_prefix
                FROM public.assets a
                LEFT JOIN public.asset_categories c ON c.id_category=a.id_category
                WHERE a.id_asset=:id
                LIMIT 1
                """
            ),
            {"id": int(id_asset)},
        ).mappings().first()
        if not a:
            raise HTTPException(404, "Activo no existe")

        maint = conn.execute(
            text(
                """
                SELECT created_at,status,detalle,realizado_por,next_due
                FROM public.asset_maintenance
                WHERE id_asset=:id
                ORDER BY created_at DESC
                LIMIT 200
                """
            ),
            {"id": int(id_asset)},
        ).fetchall()
        assigns = conn.execute(
            text(
                """
                SELECT aa.created_at, aa.status, aa.notes, aa.conductor, aa.operadores, aa.id_lead,
                       l.cliente, l.fecha_evento, l.id_marca, l.id_comuna,
                       COALESCE(m.nombre,m.marca,'') AS marca,
                       COALESCE(co.nombre,'') AS comuna
                FROM public.asset_assignments aa
                LEFT JOIN public.leads l ON l.id_lead=aa.id_lead
                LEFT JOIN public.marcas m ON m.id_marca=l.id_marca
                LEFT JOIN public.comunas co ON co.id_comuna=l.id_comuna
                WHERE aa.id_asset=:id
                ORDER BY aa.created_at DESC
                LIMIT 300
                """
            ),
            {"id": int(id_asset)},
        ).fetchall()

        return {
            "ok": True,
            "asset": dict(a),
            "maintenance": [
                {
                    "created_at": r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0] or ""),
                    "status": r[1],
                    "detalle": r[2],
                    "realizado_por": r[3],
                    "next_due": r[4].isoformat() if hasattr(r[4], "isoformat") else (str(r[4]) if r[4] else None),
                }
                for r in maint
            ],
            "assignments": [
                {
                    "created_at": r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0] or ""),
                    "status": r[1],
                    "notes": r[2],
                    "conductor": r[3],
                    "operadores": r[4],
                    "id_lead": int(r[5]) if r[5] is not None else None,
                    "cliente": r[6],
                    "fecha_evento": str(r[7] or ""),
                    "id_marca": int(r[8]) if r[8] is not None else None,
                    "id_comuna": int(r[9]) if r[9] is not None else None,
                    "marca": r[10],
                    "comuna": r[11],
                }
                for r in assigns
            ],
        }


@router.get("/{id_asset}/maintenance")
def list_maintenance(id_asset: int = Path(..., ge=1), user: dict = Depends(get_current_user)):
    _require_assets_access(user)
    with get_connection() as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            text(
                """
                SELECT id_maint, created_at, status, detalle, realizado_por, next_due
                FROM public.asset_maintenance
                WHERE id_asset=:id
                ORDER BY created_at DESC
                LIMIT 200
                """
            ),
            {"id": int(id_asset)},
        ).fetchall()
        return {
            "ok": True,
            "items": [
                {
                    "id_maint": int(r[0]),
                    "created_at": r[1].isoformat() if hasattr(r[1], "isoformat") else str(r[1] or ""),
                    "status": r[2],
                    "detalle": r[3],
                    "realizado_por": r[4],
                    "next_due": r[5].isoformat() if hasattr(r[5], "isoformat") else (str(r[5]) if r[5] else None),
                }
                for r in rows
            ],
        }


@router.post("/{id_asset}/maintenance")
def add_maintenance(id_asset: int = Path(..., ge=1), payload: dict = Body(...), user: dict = Depends(get_current_user)):
    _require_assets_access(user)
    status = str(payload.get("status") or "").strip().upper()
    if not status:
        raise HTTPException(400, "status requerido")
    detalle = (str(payload.get("detalle") or "").strip() or None)
    realizado_por = (str(payload.get("realizado_por") or payload.get("by") or (user.get("name") or user.get("username") or "")).strip() or None)
    with get_connection() as conn:
        _ensure_schema(conn)
        conn.execute(
            text(
                """
                INSERT INTO public.asset_maintenance(id_asset,status,detalle,realizado_por,next_due)
                VALUES (:id,:st,:d,:p,:n)
                """
            ),
            {"id": int(id_asset), "st": status[:32], "d": detalle, "p": realizado_por, "n": payload.get("next_due")},
        )
        # actualizar estado visible del activo si lo mandan
        if payload.get("asset_status") or payload.get("estado_reparacion"):
            conn.execute(
                text(
                    """
                    UPDATE public.assets
                    SET status=COALESCE(:s,status),
                        estado_reparacion=COALESCE(:er,estado_reparacion),
                        updated_at=now()
                    WHERE id_asset=:id
                    """
                ),
                {
                    "id": int(id_asset),
                    "s": (str(payload.get("asset_status") or "").strip().upper()[:32] or None),
                    "er": (str(payload.get("estado_reparacion") or "").strip() or None),
                },
            )
        conn.commit()
        return {"ok": True}


@router.post("/{id_asset}/assign")
def assign_to_lead(id_asset: int = Path(..., ge=1), payload: dict = Body(...), user: dict = Depends(get_current_user)):
    _require_assets_access(user)
    id_lead = payload.get("id_lead")
    if not str(id_lead or "").isdigit():
        raise HTTPException(400, "id_lead requerido")
    conductor = (str(payload.get("conductor") or "").strip() or None)
    operadores = (str(payload.get("operadores") or "").strip() or None)
    with get_connection() as conn:
        _ensure_schema(conn)
        conn.execute(
            text(
                """
                INSERT INTO public.asset_assignments(id_asset,id_lead,from_at,to_at,status,notes,conductor,operadores,created_by)
                VALUES (:a,:l,:f,:t,:s,:n,:c,:o,:by)
                """
            ),
            {
                "a": int(id_asset),
                "l": int(id_lead),
                "f": payload.get("from_at"),
                "t": payload.get("to_at"),
                "s": str(payload.get("status") or "ASIGNADO").strip().upper()[:32],
                "n": (str(payload.get("notes") or "").strip() or None),
                "c": conductor,
                "o": operadores,
                "by": (str(user.get("name") or user.get("username") or "").strip() or None),
            },
        )
        conn.commit()
        return {"ok": True}


@router.get("/lead/{id_lead}")
def assets_for_lead(id_lead: int = Path(..., ge=1), user: dict = Depends(get_current_user)):
    _require_assets_access(user)
    with get_connection() as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            text(
                """
                SELECT aa.id_assign, aa.created_at, aa.status, aa.notes,
                       a.id_asset, a.nombre, a.code, a.status as asset_status,
                       c.nombre as categoria
                FROM public.asset_assignments aa
                JOIN public.assets a ON a.id_asset=aa.id_asset
                LEFT JOIN public.asset_categories c ON c.id_category=a.id_category
                WHERE aa.id_lead=:id
                ORDER BY aa.created_at DESC
                """
            ),
            {"id": int(id_lead)},
        ).fetchall()
        return {
            "ok": True,
            "items": [
                {
                    "id_assign": int(r[0]),
                    "created_at": r[1].isoformat() if hasattr(r[1], "isoformat") else str(r[1] or ""),
                    "status": r[2],
                    "notes": r[3],
                    "id_asset": int(r[4]),
                    "nombre": r[5],
                    "code": r[6],
                    "asset_status": r[7],
                    "categoria": r[8],
                }
                for r in rows
            ],
        }
