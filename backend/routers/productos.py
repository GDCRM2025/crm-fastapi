from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from backend.core.db import get_connection
from backend.routers.auth import get_current_user


router = APIRouter(tags=["productos"])

_ACC_FROM = "ÁÉÍÓÚÜÑáéíóúüñ"
_ACC_TO = "AEIOUUNaeiouun"

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
    # Normaliza a nivel SQL sin depender de unaccent.
    return f"translate(upper({expr}), '{_ACC_FROM}', '{_ACC_TO}')"


def _norm_key_sql(expr: str) -> str:
    """
    Normalización fuerte en SQL:
    - uppercase
    - sin acentos comunes
    - sin espacios/puntos/guiones/etc.
    """
    return f"regexp_replace({_norm_sql(expr)}, '[^A-Z0-9]+', '', 'g')"


def _canon_code_py(marca_in: str | None) -> str | None:
    """
    Canoniza una entrada libre (Marca) a códigos oficiales.
    """
    if not marca_in:
        return None
    k = _norm_key_py(str(marca_in))
    if "CAMALE" in k:
        return "CAMALEON"
    if "DELSAB" in k:
        return "DEL SABOR"
    if "GOURMET" in k:
        return "GOURMET"
    if "EXPRESS" in k:
        return "EXPRESS"
    if "BRONTOS" in k:
        return "BRONTOS"
    return None


def _code_like_snippet(code: str | None) -> str | None:
    """
    Fragmento seguro para matching por texto normalizado.
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
    if code == "BRONTOS":
        return "BRONTOS"
    return None


def _fallback_marcas_ids_from_db(user: dict) -> list[int]:
    """
    Fallback para JWT sin marcas embebidas.
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
    Asegura columnas clave para cotizador.
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
            colset.add("ingredientes")

        _PRODUCTOS_COLS_CACHE = set(colset)
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


def _has_col(name: str) -> bool:
    return name in _productos_cols()


def _has_id_marca() -> bool:
    global _HAS_ID_MARCA_CACHE
    if _HAS_ID_MARCA_CACHE is None:
        _productos_cols()
    return bool(_HAS_ID_MARCA_CACHE)


def _load_marca_code_map() -> dict[str, int]:
    """
    Cachea mapeo canonical-code -> id_marca.
    """
    global _MARCA_CODE_TO_ID
    if _MARCA_CODE_TO_ID is not None:
        return _MARCA_CODE_TO_ID

    out: dict[str, int] = {}
    try:
        with get_connection() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id_marca, COALESCE(nombre, marca, '') AS nombre, COALESCE(marca, '') AS alias
                    FROM public.marcas
                    ORDER BY id_marca
                    """
                )
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


def _marca_to_id(marca_in: str | None) -> int | None:
    if not marca_in:
        return None
    code = _canon_code_py(str(marca_in))
    if not code:
        return None
    mp = _load_marca_code_map()
    return mp.get(code)


def _producto_expr_sql(alias: str = "p") -> str:
    cols = _productos_cols()
    has_producto = "producto" in cols
    has_nombre = "nombre" in cols

    if has_producto and has_nombre:
        return (
            f"COALESCE("
            f"NULLIF(BTRIM({alias}.producto), ''), "
            f"NULLIF(BTRIM({alias}.nombre), '')"
            f")"
        )
    if has_producto:
        return f"NULLIF(BTRIM({alias}.producto), '')"
    if has_nombre:
        return f"NULLIF(BTRIM({alias}.nombre), '')"

    # fallback duro; idealmente no debiera pasar
    return "NULL"


def _marca_expr_sql(alias: str = "p", marca_alias: str = "m") -> str:
    cols = _productos_cols()
    if "marca" in cols and _has_id_marca():
        return (
            f"COALESCE("
            f"NULLIF(BTRIM({alias}.marca), ''), "
            f"NULLIF(BTRIM({marca_alias}.nombre), ''), "
            f"NULLIF(BTRIM({marca_alias}.marca), '')"
            f")"
        )
    if "marca" in cols:
        return f"NULLIF(BTRIM({alias}.marca), '')"
    if _has_id_marca():
        return (
            f"COALESCE("
            f"NULLIF(BTRIM({marca_alias}.nombre), ''), "
            f"NULLIF(BTRIM({marca_alias}.marca), '')"
            f")"
        )
    return "NULL"


def _ingredientes_expr_sql(alias: str = "p") -> str:
    return f"{alias}.ingredientes" if _has_col("ingredientes") else "NULL"


def _descripcion_expr_sql(alias: str = "p") -> str:
    cols = _productos_cols()
    if "descripcion" in cols and "ingredientes" in cols:
        return f"COALESCE(NULLIF(BTRIM({alias}.descripcion), ''), NULLIF(BTRIM({alias}.ingredientes), ''))"
    if "descripcion" in cols:
        return f"{alias}.descripcion"
    if "ingredientes" in cols:
        return f"{alias}.ingredientes"
    return "NULL"


def _costo_expr_sql(alias: str = "p") -> str:
    return f"{alias}.costo" if _has_col("costo") else "NULL"


def _is_active_expr_sql(alias: str = "p") -> str:
    return f"COALESCE({alias}.is_active, TRUE)" if _has_col("is_active") else "TRUE"


def _orden_expr_sql(alias: str = "p") -> str:
    return f"{alias}.orden" if _has_col("orden") else "NULL"


def _created_at_expr_sql(alias: str = "p") -> str:
    return f"{alias}.created_at" if _has_col("created_at") else "NULL"


def _updated_at_expr_sql(alias: str = "p") -> str:
    return f"{alias}.updated_at" if _has_col("updated_at") else "NULL"


def _base_select_sql() -> str:
    joins = "LEFT JOIN public.marcas m ON m.id_marca = p.id_marca" if _has_id_marca() else ""
    producto_expr = _producto_expr_sql("p")
    marca_expr = _marca_expr_sql("p", "m")
    ingredientes_expr = _ingredientes_expr_sql("p")
    descripcion_expr = _descripcion_expr_sql("p")
    costo_expr = _costo_expr_sql("p")
    active_expr = _is_active_expr_sql("p")
    orden_expr = _orden_expr_sql("p")
    created_expr = _created_at_expr_sql("p")
    updated_expr = _updated_at_expr_sql("p")

    return f"""
        SELECT
            p.id_producto AS id_producto,
            {producto_expr} AS producto,
            {ingredientes_expr} AS ingredientes,
            {marca_expr} AS marca,
            {costo_expr} AS costo,
            {active_expr} AS is_active,
            {descripcion_expr} AS descripcion,
            {orden_expr} AS orden,
            {created_expr} AS created_at,
            {updated_expr} AS updated_at
        FROM public.productos p
        {joins}
    """


def _row_to_item(r: Any) -> dict[str, Any]:
    return {
        "id_producto": r["id_producto"],
        "producto": r.get("producto"),
        "ingredientes": r.get("ingredientes"),
        "marca": r.get("marca"),
        "costo": float(r["costo"]) if r.get("costo") is not None else None,
        "is_active": bool(r["is_active"]) if r.get("is_active") is not None else True,
        "activo": "ACTIVO" if (bool(r["is_active"]) if r.get("is_active") is not None else True) else "INACTIVO",
        "descripcion": r.get("descripcion"),
        "orden": r.get("orden"),
        "created_at": r.get("created_at"),
        "updated_at": r.get("updated_at"),
    }


def _get_producto_by_id(conn: Any, id_producto: int) -> dict[str, Any] | None:
    sql = _base_select_sql() + " WHERE p.id_producto = :id LIMIT 1"
    row = conn.execute(text(sql), {"id": id_producto}).mappings().first()
    return _row_to_item(row) if row else None


class ProductoUpsert(BaseModel):
    producto: str = Field(min_length=1)
    ingredientes: str | None = None
    marca: str | None = None
    costo: float | None = None
    descripcion: str | None = None
    orden: int | None = None
    is_active: bool = True


class ProductoEstadoPatch(BaseModel):
    is_active: bool


@router.get("/productos")
@router.get("/web/productos")
@router.get("/products")
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
        marcas_ids = _fallback_marcas_ids_from_db(user)

    where: list[str] = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}

    producto_expr = _producto_expr_sql("p")
    marca_expr = _marca_expr_sql("p", "m")
    marca_key_expr = _norm_key_sql(f"COALESCE({marca_expr}, '')")

    # No devolver basura sin nombre usable.
    where.append(f"NULLIF(BTRIM(COALESCE({producto_expr}, '')), '') IS NOT NULL")

    if q:
        where.append(f"({producto_expr} ILIKE :q OR COALESCE({marca_expr}, '') ILIKE :q)")
        params["q"] = f"%{q}%"

    if marca:
        marca_id = _marca_to_id(marca) if _has_id_marca() else None
        canon = _canon_code_py(marca)
        snippet = _code_like_snippet(canon) or _norm_key_py(marca)
        if not snippet:
            return {"items": []}

        if marca_id is not None and _has_id_marca():
            where.append(
                f"(p.id_marca = :marca_id OR {marca_key_expr} LIKE :marca_key_like)"
            )
            params["marca_id"] = marca_id
            params["marca_key_like"] = f"%{snippet}%"
        else:
            where.append(f"{marca_key_expr} LIKE :marca_key_like")
            params["marca_key_like"] = f"%{snippet}%"

    elif only_own:
        if not marcas_ids:
            return {"items": []}

        with get_connection() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT COALESCE(nombre, marca, '') AS marca_ref
                    FROM public.marcas
                    WHERE id_marca = ANY(:m)
                    """
                ),
                {"m": marcas_ids},
            ).fetchall()

        marcas = [r[0] for r in rows if r and r[0]]
        marcas_codes = [_canon_code_py(m) for m in marcas]
        marcas_snips = [s for s in (_code_like_snippet(c) for c in marcas_codes) if s]
        marcas_like = [f"%{s}%" for s in marcas_snips]

        if _has_id_marca() and marcas_ids and marcas_like:
            where.append(
                f"(p.id_marca = ANY(:marcas_ids) OR {marca_key_expr} LIKE ANY(:marcas_like))"
            )
            params["marcas_ids"] = marcas_ids
            params["marcas_like"] = marcas_like
        elif _has_id_marca() and marcas_ids:
            where.append("p.id_marca = ANY(:marcas_ids)")
            params["marcas_ids"] = marcas_ids
        elif marcas_like:
            where.append(f"{marca_key_expr} LIKE ANY(:marcas_like)")
            params["marcas_like"] = marcas_like
        else:
            return {"items": []}

    if only_active:
        where.append(f"{_is_active_expr_sql('p')} IS TRUE")

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    sql = (
        _base_select_sql()
        + f"""
        {where_sql}
        ORDER BY COALESCE({_orden_expr_sql('p')}, 999999), {producto_expr}
        LIMIT :limit
        OFFSET :offset
        """
    )

    with get_connection() as conn:
        rows = conn.execute(text(sql), params).mappings().all()

    return {"items": [_row_to_item(r) for r in rows]}


@router.get("/productos/{id_producto}")
def get_producto(
    id_producto: int,
    user: dict = Depends(get_current_user),
):
    _ensure_productos_cols()
    with get_connection() as conn:
        item = _get_producto_by_id(conn, id_producto)
    if not item:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    return item


@router.post("/productos")
def create_producto(
    payload: ProductoUpsert,
    user: dict = Depends(get_current_user),
):
    _ensure_productos_cols()

    role = _role(user)
    if not _is_admin(role):
        raise HTTPException(status_code=403, detail="Sin permisos para crear productos")

    cols = _productos_cols()

    if "producto" not in cols and "nombre" not in cols:
        raise HTTPException(status_code=500, detail="La tabla productos no tiene columna producto/nombre")

    nombre_producto = (payload.producto or "").strip()
    if not nombre_producto:
        raise HTTPException(status_code=400, detail="El nombre del producto es obligatorio")

    marca_code = _canon_code_py(payload.marca)
    marca_text = marca_code or _norm_py(payload.marca or "") or None
    marca_id = _marca_to_id(marca_text)

    data: dict[str, Any] = {}

    if "producto" in cols:
        data["producto"] = nombre_producto
    if "nombre" in cols:
        data["nombre"] = nombre_producto
    if "ingredientes" in cols:
        data["ingredientes"] = payload.ingredientes
    if "descripcion" in cols:
        data["descripcion"] = payload.descripcion or payload.ingredientes
    if "marca" in cols:
        data["marca"] = marca_text
    if "id_marca" in cols:
        data["id_marca"] = marca_id
    if "costo" in cols:
        data["costo"] = payload.costo
    if "orden" in cols:
        data["orden"] = payload.orden
    if "is_active" in cols:
        data["is_active"] = bool(payload.is_active)

    insert_cols = list(data.keys())
    if not insert_cols:
        raise HTTPException(status_code=500, detail="No hay columnas válidas para insertar en productos")

    placeholders = ", ".join(f":{c}" for c in insert_cols)
    columns_sql = ", ".join(insert_cols)

    with get_connection() as conn:
        new_id = conn.execute(
            text(
                f"""
                INSERT INTO public.productos ({columns_sql})
                VALUES ({placeholders})
                RETURNING id_producto
                """
            ),
            data,
        ).scalar_one()
        conn.commit()

        item = _get_producto_by_id(conn, int(new_id))

    return {"ok": True, "item": item}


@router.put("/productos/{id_producto}")
def update_producto(
    id_producto: int,
    payload: ProductoUpsert,
    user: dict = Depends(get_current_user),
):
    _ensure_productos_cols()

    role = _role(user)
    if not _is_admin(role):
        raise HTTPException(status_code=403, detail="Sin permisos para editar productos")

    cols = _productos_cols()

    nombre_producto = (payload.producto or "").strip()
    if not nombre_producto:
        raise HTTPException(status_code=400, detail="El nombre del producto es obligatorio")

    marca_code = _canon_code_py(payload.marca)
    marca_text = marca_code or _norm_py(payload.marca or "") or None
    marca_id = _marca_to_id(marca_text)

    data: dict[str, Any] = {"id_producto": id_producto}

    sets: list[str] = []

    if "producto" in cols:
        sets.append("producto = :producto")
        data["producto"] = nombre_producto
    if "nombre" in cols:
        sets.append("nombre = :nombre")
        data["nombre"] = nombre_producto
    if "ingredientes" in cols:
        sets.append("ingredientes = :ingredientes")
        data["ingredientes"] = payload.ingredientes
    if "descripcion" in cols:
        sets.append("descripcion = :descripcion")
        data["descripcion"] = payload.descripcion or payload.ingredientes
    if "marca" in cols:
        sets.append("marca = :marca")
        data["marca"] = marca_text
    if "id_marca" in cols:
        sets.append("id_marca = :id_marca")
        data["id_marca"] = marca_id
    if "costo" in cols:
        sets.append("costo = :costo")
        data["costo"] = payload.costo
    if "orden" in cols:
        sets.append("orden = :orden")
        data["orden"] = payload.orden
    if "is_active" in cols:
        sets.append("is_active = :is_active")
        data["is_active"] = bool(payload.is_active)
    if "updated_at" in cols:
        sets.append("updated_at = NOW()")

    if not sets:
        raise HTTPException(status_code=500, detail="No hay columnas válidas para actualizar en productos")

    with get_connection() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM public.productos WHERE id_producto = :id LIMIT 1"),
            {"id": id_producto},
        ).first()
        if not exists:
            raise HTTPException(status_code=404, detail="Producto no encontrado")

        conn.execute(
            text(
                f"""
                UPDATE public.productos
                SET {", ".join(sets)}
                WHERE id_producto = :id_producto
                """
            ),
            data,
        )
        conn.commit()

        item = _get_producto_by_id(conn, id_producto)

    return {"ok": True, "item": item}


@router.patch("/productos/{id_producto}/active")
def set_producto_active(
    id_producto: int,
    payload: ProductoEstadoPatch,
    user: dict = Depends(get_current_user),
):
    _ensure_productos_cols()

    role = _role(user)
    if not _is_admin(role):
        raise HTTPException(status_code=403, detail="Sin permisos para cambiar estado de productos")

    if not _has_col("is_active"):
        raise HTTPException(status_code=500, detail="La tabla productos no tiene columna is_active")

    sets = ["is_active = :is_active"]
    if _has_col("updated_at"):
        sets.append("updated_at = NOW()")

    with get_connection() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM public.productos WHERE id_producto = :id LIMIT 1"),
            {"id": id_producto},
        ).first()
        if not exists:
            raise HTTPException(status_code=404, detail="Producto no encontrado")

        conn.execute(
            text(
                f"""
                UPDATE public.productos
                SET {", ".join(sets)}
                WHERE id_producto = :id
                """
            ),
            {"id": id_producto, "is_active": bool(payload.is_active)},
        )
        conn.commit()

        item = _get_producto_by_id(conn, id_producto)

    return {"ok": True, "item": item}