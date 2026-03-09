from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text

from backend.core.db import get_connection
from backend.routers.auth import get_current_user


router = APIRouter(tags=["productos"])

_ACC_FROM = "ÁÉÍÓÚÜÑáéíóúüñ"
_ACC_TO   = "AEIOUUNaeiouun"

_PRODUCTOS_COLS_CACHE: set[str] | None = None
_HAS_ID_MARCA_CACHE: bool | None = None
_ENSURED_PRODUCTOS_COLS = False

_MARCA_CODE_TO_ID: dict[str, int] | None = None


def _norm_py(s: str) -> str:
    return (
        (s or "")
        .strip()
        .translate(str.maketrans({a: b for a, b in zip(_ACC_FROM, _ACC_TO)}))
        .upper()
    )

def _norm_key_py(s: str) -> str:
    """
    Normalización fuerte para matching de marca:
    - sin acentos
    - uppercase
    - solo A-Z0-9 (fuera espacios, puntos, guiones, etc.)
    """
    s = _norm_py(s)
    return "".join(ch for ch in s if ("A" <= ch <= "Z") or ("0" <= ch <= "9"))


def _norm_sql(expr: str) -> str:
    # Normaliza a nivel SQL sin depender de la extensión unaccent.
    return f"translate(upper({expr}), '{_ACC_FROM}', '{_ACC_TO}')"

def _norm_key_sql(expr: str) -> str:
    """
    Normalización fuerte en SQL:
    - uppercase
    - sin acentos comunes (translate)
    - sin espacios/puntos/guiones/etc (regexp_replace)

    Esto es clave para casos reales donde "Camaleón" viene con encoding/acentos raros y
    termina como "CAMALEN" al limpiar, lo que rompe un LIKE '%CAMALEON%'.
    """
    return f"regexp_replace({_norm_sql(expr)}, '[^A-Z0-9]+', '', 'g')"


def _canon_code_py(marca_in: str | None) -> str | None:
    """
    Canoniza una entrada libre (Marca) a uno de los 4 códigos oficiales.
    """
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


def _code_like_snippet(code: str | None) -> str | None:
    """
    Fragmento para LIKE sobre _norm_key_sql(). Usamos fragmentos "seguros" para capturar
    variaciones de encoding (ej: Camaleón -> CAMALEN) sin mezclar marcas.
    """
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


def _fallback_marcas_ids_from_db(user: dict) -> list[int]:
    """
    Fallback para casos reales donde el JWT viene con `marcas=[]` aunque el usuario
    sí tiene marcas asignadas (ej: usuario multi-marca que recién cambió permisos,
    o token antiguo cacheado en el navegador/extension).

    Esto evita el bug: “No hay productos para esta marca” solo para algunos usuarios.
    """
    uid = user.get("id")
    try:
        with get_connection() as conn:
            id_usuario: int | None = None

            if isinstance(uid, int):
                id_usuario = uid
            elif isinstance(uid, str) and uid.isdigit():
                id_usuario = int(uid)
            else:
                # Muchas veces `sub` es `username` (ej: "greengd")
                row = conn.execute(
                    text(
                        """
                        SELECT id_usuario
                        FROM public.usuarios
                        WHERE username=:u OR email=:u
                        LIMIT 1
                        """
                    ),
                    {"u": str(uid or "").strip()},
                ).first()
                if row and row[0]:
                    id_usuario = int(row[0])

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
            out: list[int] = []
            for r in rows:
                try:
                    out.append(int(r[0]))
                except Exception:
                    pass
            return out
    except Exception:
        return []


def _ensure_productos_cols() -> None:
    """
    Asegura columnas clave para cotizador (ingredientes).
    Evita que se pierda la columna en ambientes antiguos.
    """
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
        colset = {r[0] for r in cols}
        if "ingredientes" not in colset:
            conn.execute(text("ALTER TABLE public.productos ADD COLUMN IF NOT EXISTS ingredientes TEXT"))
            conn.commit()
        # refresh caches
        _PRODUCTOS_COLS_CACHE = set(colset) | {"ingredientes"}
        _HAS_ID_MARCA_CACHE = ("id_marca" in _PRODUCTOS_COLS_CACHE)
    _ENSURED_PRODUCTOS_COLS = True

def _productos_cols() -> set[str]:
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
        _PRODUCTOS_COLS_CACHE = {r[0] for r in rows}
        _HAS_ID_MARCA_CACHE = ("id_marca" in _PRODUCTOS_COLS_CACHE)
    return _PRODUCTOS_COLS_CACHE

def _has_id_marca() -> bool:
    global _HAS_ID_MARCA_CACHE
    if _HAS_ID_MARCA_CACHE is None:
        _productos_cols()
    return bool(_HAS_ID_MARCA_CACHE)

def _load_marca_code_map() -> dict[str, int]:
    """
    Cachea mapeo canonical-code -> id_marca para resolver marca rápido.
    """
    global _MARCA_CODE_TO_ID
    if _MARCA_CODE_TO_ID is not None:
        return _MARCA_CODE_TO_ID
    out: dict[str, int] = {}
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

def _invalidate_marca_code_map() -> None:
    global _MARCA_CODE_TO_ID
    _MARCA_CODE_TO_ID = None

def _role(user: dict) -> str:
    return (user.get("role") or user.get("rol") or "").upper()

def _is_admin(role: str) -> bool:
    return role in ("ADMIN", "SUPERADMIN", "JEFE DE OPERACIONES", "OPERACIONES")


class ProductoUpsert(BaseModel):
    producto: str = Field(min_length=1)
    ingredientes: str | None = None
    marca: str | None = None
    costo: float | None = None
    descripcion: str | None = None
    orden: int | None = None
    is_active: bool = True


@router.get("/productos")
@router.get("/web/productos")
@router.get("/products")        # alias por si el front usa inglés
@router.get("/web/products")
def list_productos(
    q: str | None = None,
    only_active: bool = False,
    marca: str | None = None,
    limit: int = Query(200, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
):
    _ensure_productos_cols()
    role = _role(user)
    marcas_ids = [int(x) for x in (user.get("marcas") or []) if str(x).isdigit()]
    only_own = not _is_admin(role)
    if only_own and not marcas_ids:
        # fallback: token antiguo / marcas no embebidas en JWT
        marcas_ids = _fallback_marcas_ids_from_db(user)
    where = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}

    has_id_marca = _has_id_marca()

    def _marca_to_id(marca_in: str | None) -> int | None:
        if not marca_in:
            return None
        code = _canon_code_py(str(marca_in))
        if not code:
            return None
        mp = _load_marca_code_map()
        return mp.get(code)

    if q:
        where.append("(producto ILIKE :q OR COALESCE(marca,'') ILIKE :q)")
        params["q"] = f"%{q}%"
    if marca:
        marca_id = _marca_to_id(marca) if has_id_marca else None
        canon = _canon_code_py(marca)
        snippet = _code_like_snippet(canon) or _norm_key_py(marca)
        if not snippet:
            # Evitar que un `marca` inválido termine como LIKE '%%' (match todo).
            return {"items": []}
        # matching fuerte por texto (key) para productos legacy.
        marca_key_expr = _norm_key_sql("COALESCE(marca,'')")
        if marca_id:
            # Importante: hay productos legacy sin id_marca (NULL) pero con `marca` (texto).
            # Si filtramos solo por id_marca, puede devolver 1 producto (o 0) aunque la marca tenga catálogo.
            where.append(
                # También incluimos productos con id_marca "malo" pero marca textual correcta.
                f"(id_marca = :marca_id OR {marca_key_expr} LIKE :marca_key_like)"
            )
            params["marca_id"] = marca_id
            params["marca_key_like"] = f"%{snippet}%"
        else:
            # fallback sin id_marca: comparo normalizado (sin acentos)
            where.append(f"{marca_key_expr} LIKE :marca_key_like")
            params["marca_key_like"] = f"%{snippet}%"
    elif only_own:
        if not marcas_ids:
            return {"items": []}
        with get_connection() as conn:
            rows = conn.execute(
                text("SELECT nombre, marca FROM marcas WHERE id_marca = ANY(:m)"),
                {"m": marcas_ids},
            ).fetchall()
        marcas = [r[0] or r[1] for r in rows if (r[0] or r[1])]
        # Para productos legacy (id_marca NULL) muchas veces `marca` viene con sufijos o encoding raro.
        # Usamos matching por "key" + fragmentos seguros (CAMALE / DELSAB / ...).
        marcas_codes = [_canon_code_py(m) for m in marcas]
        marcas_snips = [s for s in (_code_like_snippet(c) for c in marcas_codes) if s]
        marcas_like = [f"%{s}%" for s in marcas_snips]

        # Importante: hay ambientes con productos legacy sin id_marca (NULL) pero con columna `marca` (texto).
        # Si filtramos solo por id_marca, un usuario puede ver “0 productos”. Entonces mezclamos ambas vías.
        marca_expr = _norm_key_sql("COALESCE(marca,'')")
        if has_id_marca and marcas_ids and marcas_like:
            where.append(
                f"(id_marca = ANY(:marcas_ids) OR (id_marca IS NULL AND {marca_expr} LIKE ANY(:marcas_like)))"
            )
            params["marcas_ids"] = marcas_ids
            params["marcas_like"] = marcas_like
        elif has_id_marca and marcas_ids:
            where.append("id_marca = ANY(:marcas_ids)")
            params["marcas_ids"] = marcas_ids
        elif marcas_like:
            where.append(f"{marca_expr} LIKE ANY(:marcas_like)")
            params["marcas_like"] = marcas_like
        else:
            return {"items": []}
    if only_active:
        where.append("is_active IS TRUE")

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    with get_connection() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT id_producto, producto, ingredientes, marca, costo, is_active, descripcion, orden, created_at, updated_at
                FROM public.productos
                {where_sql}
                ORDER BY COALESCE(orden, 999999), producto
                LIMIT :limit
                OFFSET :offset
                """
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
