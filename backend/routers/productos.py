from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text

from backend.core.db import get_connection
from backend.routers.auth import get_current_user


router = APIRouter(tags=["productos"])

_ACC_FROM = "ÁÉÍÓÚÜÑáéíóúüñ"
_ACC_TO = "AEIOUUNaeiouun"

_PRODUCTOS_COLS_CACHE = None
_HAS_ID_MARCA_CACHE = None
_ENSURED_PRODUCTOS_COLS = False
_MARCA_CODE_TO_ID = None


def _norm_py(s):
    return (
        (s or "")
        .strip()
        .translate(str.maketrans({a: b for a, b in zip(_ACC_FROM, _ACC_TO)}))
        .upper()
    )


def _norm_key_py(s):
    s = _norm_py(s)
    return "".join(ch for ch in s if ("A" <= ch <= "Z") or ("0" <= ch <= "9"))


def _norm_sql(expr):
    return "translate(upper(%s), '%s', '%s')" % (expr, _ACC_FROM, _ACC_TO)


def _norm_key_sql(expr):
    return "regexp_replace(%s, '[^A-Z0-9]+', '', 'g')" % _norm_sql(expr)


def _canon_code_py(marca_in):
    if not marca_in:
        return None
    k = _norm_key_py(str(marca_in)).upper()
    if "CAMALE" in k:
        return "CAMALEON"
    if "DELSAB" in k:
        return "DEL SABOR"
    if "GOURMET" in k:
        return "GOURMET"
    if "EXPRESS" in k:
        return "EXPRESS"
    return None


def _code_like_snippet(code):
    if not code:
        return None
    if code == "CAMALEON":
        return "CAMALE"
    if code == "DEL SABOR":
        return "DELSAB"
    if code == "GOURMET":
        return "GOURMET"
    if code == "EXPRESS":
        return "EXPRESS"
    return None


def _extract_user_brand_ids(user):
    raw = user.get("marcas") or user.get("brands") or []
    out = []

    if isinstance(raw, (list, tuple, set)):
        for x in raw:
            try:
                out.append(int(x))
            except Exception:
                pass
        return sorted(set(out))

    if isinstance(raw, str):
        parts = raw.replace(";", ",").split(",")
        for p in parts:
            p = p.strip()
            if p.isdigit():
                out.append(int(p))
        return sorted(set(out))

    return []


def _fallback_marcas_ids_from_db(user):
    """
    Fallback para usuarios multi-marca con token viejo o incompleto.
    """
    candidates = [
        user.get("id"),
        user.get("sub"),
        user.get("username"),
        user.get("email"),
    ]

    try:
        with get_connection() as conn:
            id_usuario = None

            for cand in candidates:
                if cand is None:
                    continue

                if isinstance(cand, int):
                    id_usuario = cand
                    break

                cand_s = str(cand).strip()
                if not cand_s:
                    continue

                if cand_s.isdigit():
                    id_usuario = int(cand_s)
                    break

                row = conn.execute(
                    text(
                        """
                        SELECT id_usuario
                        FROM public.usuarios
                        WHERE username=:u OR email=:u
                        LIMIT 1
                        """
                    ),
                    {"u": cand_s},
                ).first()
                if row and row[0]:
                    id_usuario = int(row[0])
                    break

            if not id_usuario:
                return []

            rows = conn.execute(
                text(
                    """
                    SELECT id_marca
                    FROM public.usuarios_marcas
                    WHERE id_usuario=:id
                    """
                ),
                {"id": id_usuario},
            ).fetchall()

            out = []
            for r in rows:
                try:
                    out.append(int(r[0]))
                except Exception:
                    pass
            return sorted(set(out))
    except Exception:
        return []


def _ensure_productos_cols():
    global _ENSURED_PRODUCTOS_COLS, _PRODUCTOS_COLS_CACHE, _HAS_ID_MARCA_CACHE
    if _ENSURED_PRODUCTOS_COLS:
        return

    with get_connection() as conn:
        cols = conn.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name='productos'
                """
            )
        ).fetchall()
        colset = set([r[0] for r in cols])

        if "ingredientes" not in colset:
            conn.execute(text("ALTER TABLE public.productos ADD COLUMN IF NOT EXISTS ingredientes TEXT"))
            conn.commit()

        _PRODUCTOS_COLS_CACHE = set(colset) | set(["ingredientes"])
        _HAS_ID_MARCA_CACHE = ("id_marca" in _PRODUCTOS_COLS_CACHE)

    _ENSURED_PRODUCTOS_COLS = True


def _productos_cols():
    global _PRODUCTOS_COLS_CACHE, _HAS_ID_MARCA_CACHE
    if _PRODUCTOS_COLS_CACHE is not None:
        return _PRODUCTOS_COLS_CACHE

    with get_connection() as conn:
        rows = conn.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name='productos'
                """
            )
        ).fetchall()
        _PRODUCTOS_COLS_CACHE = set([r[0] for r in rows])
        _HAS_ID_MARCA_CACHE = ("id_marca" in _PRODUCTOS_COLS_CACHE)

    return _PRODUCTOS_COLS_CACHE


def _has_id_marca():
    global _HAS_ID_MARCA_CACHE
    if _HAS_ID_MARCA_CACHE is None:
        _productos_cols()
    return bool(_HAS_ID_MARCA_CACHE)


def _load_marca_code_map():
    global _MARCA_CODE_TO_ID
    if _MARCA_CODE_TO_ID is not None:
        return _MARCA_CODE_TO_ID

    out = {}
    try:
        with get_connection() as conn:
            rows = conn.execute(
                text("SELECT id_marca, COALESCE(nombre,marca,'') AS nombre, COALESCE(marca,'') AS alias FROM public.marcas ORDER BY id_marca")
            ).fetchall()

        for rid, nombre, alias in rows:
            try:
                mid = int(rid)
            except Exception:
                continue

            for s in (str(nombre or ""), str(alias or "")):
                code = _canon_code_py(s)
                if code and code not in out:
                    out[code] = mid
    except Exception:
        out = {}

    _MARCA_CODE_TO_ID = out
    return out


def _invalidate_marca_code_map():
    global _MARCA_CODE_TO_ID
    _MARCA_CODE_TO_ID = None


def _role(user):
    return (user.get("role") or user.get("rol") or "").upper()


def _is_admin(role):
    return role in ("ADMIN", "SUPERADMIN", "JEFE DE OPERACIONES", "OPERACIONES")


class ProductoUpsert(BaseModel):
    producto = Field(min_length=1)
    ingredientes = None
    marca = None
    costo = None
    descripcion = None
    orden = None
    is_active = True


@router.get("/productos")
@router.get("/web/productos")
@router.get("/products")
@router.get("/web/products")
def list_productos(
    q=None,
    only_active=False,
    marca=None,
    limit=Query(200, ge=1, le=5000),
    offset=Query(0, ge=0),
    user=Depends(get_current_user),
):
    _ensure_productos_cols()

    role = _role(user)
    marcas_ids = _extract_user_brand_ids(user)
    only_own = not _is_admin(role)

    if only_own and not marcas_ids:
        marcas_ids = _fallback_marcas_ids_from_db(user)

    where = []
    params = {"limit": int(limit), "offset": int(offset)}
    has_id_marca = _has_id_marca()

    def _marca_to_id(marca_in):
        if not marca_in:
            return None
        code = _canon_code_py(str(marca_in))
        if not code:
            return None
        mp = _load_marca_code_map()
        return mp.get(code)

    if q:
        where.append("(producto ILIKE :q OR COALESCE(marca,'') ILIKE :q)")
        params["q"] = "%%%s%%" % q

    if marca:
        marca_id = _marca_to_id(marca) if has_id_marca else None
        canon = _canon_code_py(marca)
        snippet = _code_like_snippet(canon) or _norm_key_py(marca)

        if not snippet:
            return {"items": []}

        marca_key_expr = _norm_key_sql("COALESCE(marca,'')")

        if marca_id:
            where.append("(id_marca = :marca_id OR %s LIKE :marca_key_like)" % marca_key_expr)
            params["marca_id"] = int(marca_id)
            params["marca_key_like"] = "%%%s%%" % snippet
        else:
            where.append("%s LIKE :marca_key_like" % marca_key_expr)
            params["marca_key_like"] = "%%%s%%" % snippet

    elif only_own:
        if not marcas_ids:
            return {"items": []}

        with get_connection() as conn:
            rows = conn.execute(
                text("SELECT nombre, marca FROM marcas WHERE id_marca = ANY(:m)"),
                {"m": marcas_ids},
            ).fetchall()

        marcas = [r[0] or r[1] for r in rows if (r[0] or r[1])]
        marcas_codes = [_canon_code_py(m) for m in marcas]
        marcas_snips = [s for s in [_code_like_snippet(c) for c in marcas_codes] if s]
        marcas_like = ["%%%s%%" % s for s in marcas_snips]
        marca_expr = _norm_key_sql("COALESCE(marca,'')")

        if has_id_marca and marcas_ids and marcas_like:
            where.append("(id_marca = ANY(:marcas_ids) OR (id_marca IS NULL AND %s LIKE ANY(:marcas_like)))" % marca_expr)
            params["marcas_ids"] = marcas_ids
            params["marcas_like"] = marcas_like
        elif has_id_marca and marcas_ids:
            where.append("id_marca = ANY(:marcas_ids)")
            params["marcas_ids"] = marcas_ids
        elif marcas_like:
            where.append("%s LIKE ANY(:marcas_like)" % marca_expr)
            params["marcas_like"] = marcas_like
        else:
            return {"items": []}

    if only_active:
        where.append("is_active IS TRUE")

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    with get_connection() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id_producto, producto, ingredientes, marca, costo, is_active, descripcion, orden, created_at, updated_at
                FROM public.productos
                {where_sql}
                ORDER BY COALESCE(orden, 999999), producto
                LIMIT :limit
                OFFSET :offset
                """.format(where_sql=where_sql)
            ),
            params,
        ).mappings().all()

    items = []
    for r in rows:
        items.append(
            {
                "id_producto": r["id_producto"],
                "producto": r.get("producto"),
                "ingredientes": r.get("ingredientes"),
                "marca": r.get("marca"),
                "costo": float(r["costo"]) if r.get("costo") is not None else None,
                "is_active": bool(r["is_active"]) if r.get("is_active") is not None else True,
                "activo": "ACTIVO" if r.get("is_active") else "INACTIVO",
                "descripcion": r.get("descripcion"),
                "orden": r.get("orden"),
            }
        )

    return {"items": items}