from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import text

from backend.core.db import get_connection
from backend.routers.auth import get_current_user

router = APIRouter(prefix="/ops/inventario", tags=["inventario"])

def _norm_expr(expr: str) -> str:
    return f"translate(lower({expr}), 'áéíóúüñÁÉÍÓÚÜÑ', 'aeiouunAEIOUUN')"

def _norm_param(param: str) -> str:
    return f"translate(lower({param}), 'áéíóúüñÁÉÍÓÚÜÑ', 'aeiouunAEIOUUN')"


def _ensure_tables(conn):
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS inv_categorias (
                id_categoria SERIAL PRIMARY KEY,
                nombre TEXT UNIQUE NOT NULL
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS inv_unidades (
                id_unidad SERIAL PRIMARY KEY,
                nombre TEXT UNIQUE NOT NULL,
                abreviatura TEXT
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS inv_proveedores (
                id_proveedor SERIAL PRIMARY KEY,
                nombre TEXT UNIQUE NOT NULL,
                direccion TEXT,
                telefono TEXT,
                email TEXT,
                contacto TEXT,
                created_at TIMESTAMP DEFAULT now(),
                updated_at TIMESTAMP DEFAULT now()
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS inv_productos (
                id_producto SERIAL PRIMARY KEY,
                sku TEXT UNIQUE NOT NULL,
                nombre TEXT NOT NULL,
                id_categoria INT REFERENCES inv_categorias(id_categoria),
                id_unidad INT REFERENCES inv_unidades(id_unidad),
                id_proveedor INT REFERENCES inv_proveedores(id_proveedor),
                precio NUMERIC(12,2),
                pack_cantidad NUMERIC(12,3),
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT now(),
                updated_at TIMESTAMP DEFAULT now()
            )
            """
        )
    )
    conn.execute(text("ALTER TABLE inv_productos ADD COLUMN IF NOT EXISTS pack_cantidad NUMERIC(12,3)"))
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS inv_stock (
                id_producto INT PRIMARY KEY REFERENCES inv_productos(id_producto) ON DELETE CASCADE,
                stock_inicial NUMERIC(12,3),
                stock_bodega NUMERIC(12,3),
                stock_delivery NUMERIC(12,3),
                stock_cocina NUMERIC(12,3),
                stock_total NUMERIC(12,3),
                stock_actual NUMERIC(12,3) DEFAULT 0,
                stock_min NUMERIC(12,3),
                stock_max NUMERIC(12,3),
                updated_at TIMESTAMP DEFAULT now()
            )
            """
        )
    )
    conn.execute(text("ALTER TABLE inv_stock ADD COLUMN IF NOT EXISTS stock_inicial NUMERIC(12,3)"))
    conn.execute(text("ALTER TABLE inv_stock ADD COLUMN IF NOT EXISTS stock_bodega NUMERIC(12,3)"))
    conn.execute(text("ALTER TABLE inv_stock ADD COLUMN IF NOT EXISTS stock_delivery NUMERIC(12,3)"))
    conn.execute(text("ALTER TABLE inv_stock ADD COLUMN IF NOT EXISTS stock_cocina NUMERIC(12,3)"))
    conn.execute(text("ALTER TABLE inv_stock ADD COLUMN IF NOT EXISTS stock_total NUMERIC(12,3)"))
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS inv_movimientos (
                id_movimiento SERIAL PRIMARY KEY,
                id_producto INT REFERENCES inv_productos(id_producto),
                tipo TEXT NOT NULL, -- ingreso/salida/ajuste
                subtipo TEXT, -- cocina/eventos/delivery/compra/merma
                cantidad NUMERIC(12,3) NOT NULL,
                unidad TEXT,
                fecha DATE NOT NULL DEFAULT CURRENT_DATE,
                referencia TEXT,
                centro_costo TEXT,
                marca TEXT,
                id_proveedor INT REFERENCES inv_proveedores(id_proveedor),
                nota TEXT,
                created_at TIMESTAMP DEFAULT now()
            )
            """
        )
    )
    conn.execute(text("ALTER TABLE inv_movimientos ADD COLUMN IF NOT EXISTS marca TEXT"))

    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS inv_producto_marcas (
                id_producto INT REFERENCES inv_productos(id_producto) ON DELETE CASCADE,
                marca TEXT NOT NULL,
                UNIQUE(id_producto, marca)
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS inv_centros_costo (
                id_centro SERIAL PRIMARY KEY,
                nombre TEXT UNIQUE NOT NULL
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS inv_tomas (
                id_toma SERIAL PRIMARY KEY,
                created_at TIMESTAMP DEFAULT now(),
                created_by INT,
                created_by_name TEXT,
                status TEXT NOT NULL DEFAULT 'PENDIENTE',
                note TEXT,
                approved_at TIMESTAMP
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS inv_toma_items (
                id_item SERIAL PRIMARY KEY,
                id_toma INT NOT NULL REFERENCES inv_tomas(id_toma) ON DELETE CASCADE,
                id_producto INT NOT NULL REFERENCES inv_productos(id_producto) ON DELETE CASCADE,
                stock_actual NUMERIC(14,2) NOT NULL DEFAULT 0
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS inv_toma_aprobaciones (
                id_aprob SERIAL PRIMARY KEY,
                id_toma INT NOT NULL REFERENCES inv_tomas(id_toma) ON DELETE CASCADE,
                rol TEXT NOT NULL,
                approved_by INT,
                approved_by_name TEXT,
                approved_at TIMESTAMP DEFAULT now(),
                UNIQUE (id_toma, rol)
            )
            """
        )
    )
    # seed centros de costo
    conn.execute(
        text(
            """
            INSERT INTO inv_centros_costo(nombre)
            VALUES ('GREENDIAMOND'), ('ROLFI')
            ON CONFLICT (nombre) DO NOTHING
            """
        )
    )
    conn.commit()


def _ensure_role(me):
    role = (me.get("role") or me.get("rol") or "").upper()
    if role not in (
        "ADMIN",
        "SUPERADMIN",
        "JEFE DE OPERACIONES",
        "OPERACIONES",
        "BODEGUERO",
        "COMPRAS",
        "MICE",
    ):
        raise HTTPException(status_code=403, detail="No autorizado")


class CategoriaIn(BaseModel):
    nombre: str


class UnidadIn(BaseModel):
    nombre: str
    abreviatura: Optional[str] = None


class ProveedorIn(BaseModel):
    nombre: str
    direccion: Optional[str] = None
    telefono: Optional[str] = None
    email: Optional[str] = None
    contacto: Optional[str] = None


class ProductoIn(BaseModel):
    sku: Optional[str] = None
    nombre: str
    id_categoria: Optional[int] = None
    id_unidad: Optional[int] = None
    id_proveedor: Optional[int] = None
    precio: Optional[float] = None
    pack_cantidad: Optional[float] = None
    is_active: Optional[bool] = True


class MarcasIn(BaseModel):
    marcas: list[str] = []


class StockIn(BaseModel):
    id_producto: int
    stock_actual: Optional[float] = None
    stock_inicial: Optional[float] = None
    stock_bodega: Optional[float] = None
    stock_delivery: Optional[float] = None
    stock_cocina: Optional[float] = None
    stock_total: Optional[float] = None
    nota: Optional[str] = None
    stock_min: Optional[float] = None
    stock_max: Optional[float] = None


class MovimientoIn(BaseModel):
    id_producto: int
    tipo: str
    subtipo: Optional[str] = None
    cantidad: float
    fecha: Optional[str] = None
    centro_costo: Optional[str] = None
    marca: Optional[str] = None
    nota: Optional[str] = None


def _gen_sku(conn, id_categoria: Optional[int]) -> str:
    prefix = "GEN"
    if id_categoria:
        row = conn.execute(
            text("SELECT nombre FROM inv_categorias WHERE id_categoria=:id"),
            {"id": id_categoria},
        ).first()
        if row and row[0]:
            raw = "".join([c for c in str(row[0]).upper() if c.isalnum()])
            prefix = raw[:3] if raw[:3] else "GEN"
    idx = 1
    while True:
        sku = f"{prefix}{idx:03d}"
        exists = conn.execute(text("SELECT 1 FROM inv_productos WHERE sku=:s"), {"s": sku}).first()
        if not exists:
            return sku
        idx += 1


@router.get("/catalogos")
def catalogos(me=Depends(get_current_user)):
    _ensure_role(me)
    with get_connection() as conn:
        _ensure_tables(conn)
        cats = conn.execute(text("SELECT id_categoria, nombre FROM inv_categorias ORDER BY nombre")).mappings().all()
        unis = conn.execute(text("SELECT id_unidad, nombre, abreviatura FROM inv_unidades ORDER BY nombre")).mappings().all()
        prov = conn.execute(text("SELECT id_proveedor, nombre FROM inv_proveedores ORDER BY nombre")).mappings().all()
        prods = conn.execute(
            text(
                """
                SELECT p.id_producto, p.sku, p.nombre, p.precio, p.pack_cantidad,
                       u.nombre AS unidad, c.nombre AS categoria
                FROM inv_productos p
                LEFT JOIN inv_unidades u ON u.id_unidad=p.id_unidad
                LEFT JOIN inv_categorias c ON c.id_categoria=p.id_categoria
                ORDER BY c.nombre NULLS LAST, p.nombre
                """
            )
        ).mappings().all()
        centros = conn.execute(text("SELECT nombre FROM inv_centros_costo ORDER BY nombre")).mappings().all()
        # marcas desde tabla marcas (si existe) + BRONTOS
        marcas = []
        try:
            marcas = conn.execute(text("SELECT DISTINCT COALESCE(nombre, marca) AS nombre FROM marcas ORDER BY 1")).mappings().all()
        except Exception:
            marcas = []
    marcas_list = [m["nombre"] for m in marcas] if marcas else []
    if "BRONTOS" not in marcas_list:
        marcas_list.append("BRONTOS")
    return {
        "ok": True,
        "categorias": list(cats),
        "unidades": list(unis),
        "proveedores": list(prov),
        "productos": list(prods),
        "marcas": marcas_list,
        "centros": [c["nombre"] for c in centros],
    }


@router.get("/categorias")
def list_categorias(q: str = Query("", max_length=120), me=Depends(get_current_user)):
    _ensure_role(me)
    with get_connection() as conn:
        _ensure_tables(conn)
        if q:
            rows = conn.execute(
                text(f"SELECT id_categoria, nombre FROM inv_categorias WHERE {_norm_expr('nombre')} LIKE {_norm_param(':q')} ORDER BY nombre"),
                {"q": f"%{q}%"},
            ).mappings().all()
        else:
            rows = conn.execute(
                text("SELECT id_categoria, nombre FROM inv_categorias ORDER BY nombre")
            ).mappings().all()
    return {"ok": True, "items": list(rows)}


@router.post("/categorias")
def create_categoria(body: CategoriaIn, me=Depends(get_current_user)):
    _ensure_role(me)
    if not body.nombre.strip():
        raise HTTPException(status_code=400, detail="nombre requerido")
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute(
            text("INSERT INTO inv_categorias(nombre) VALUES (:n) ON CONFLICT (nombre) DO NOTHING"),
            {"n": body.nombre.strip()},
        )
        conn.commit()
    return {"ok": True}


@router.delete("/categorias/{id_categoria}")
def delete_categoria(id_categoria: int, me=Depends(get_current_user)):
    _ensure_role(me)
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute(text("DELETE FROM inv_categorias WHERE id_categoria=:id"), {"id": id_categoria})
        conn.commit()
    return {"ok": True}


@router.put("/categorias/{id_categoria}")
def update_categoria(id_categoria: int, body: CategoriaIn, me=Depends(get_current_user)):
    _ensure_role(me)
    if not body.nombre.strip():
        raise HTTPException(status_code=400, detail="nombre requerido")
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute(
            text("UPDATE inv_categorias SET nombre=:n WHERE id_categoria=:id"),
            {"n": body.nombre.strip(), "id": id_categoria},
        )
        conn.commit()
    return {"ok": True}


@router.get("/unidades")
def list_unidades(q: str = Query("", max_length=120), me=Depends(get_current_user)):
    _ensure_role(me)
    with get_connection() as conn:
        _ensure_tables(conn)
        if q:
            rows = conn.execute(
                text(f"SELECT id_unidad, nombre, abreviatura FROM inv_unidades WHERE {_norm_expr('nombre')} LIKE {_norm_param(':q')} OR {_norm_expr('abreviatura')} LIKE {_norm_param(':q')} ORDER BY nombre"),
                {"q": f"%{q}%"},
            ).mappings().all()
        else:
            rows = conn.execute(
                text("SELECT id_unidad, nombre, abreviatura FROM inv_unidades ORDER BY nombre")
            ).mappings().all()
    return {"ok": True, "items": list(rows)}


@router.post("/unidades")
def create_unidad(body: UnidadIn, me=Depends(get_current_user)):
    _ensure_role(me)
    if not body.nombre.strip():
        raise HTTPException(status_code=400, detail="nombre requerido")
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute(
            text(
                """
                INSERT INTO inv_unidades(nombre, abreviatura)
                VALUES (:n, :a)
                ON CONFLICT (nombre) DO UPDATE SET abreviatura=EXCLUDED.abreviatura
                """
            ),
            {"n": body.nombre.strip(), "a": (body.abreviatura or None)},
        )
        conn.commit()
    return {"ok": True}


@router.delete("/unidades/{id_unidad}")
def delete_unidad(id_unidad: int, me=Depends(get_current_user)):
    _ensure_role(me)
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute(text("DELETE FROM inv_unidades WHERE id_unidad=:id"), {"id": id_unidad})
        conn.commit()
    return {"ok": True}


@router.put("/unidades/{id_unidad}")
def update_unidad(id_unidad: int, body: UnidadIn, me=Depends(get_current_user)):
    _ensure_role(me)
    if not body.nombre.strip():
        raise HTTPException(status_code=400, detail="nombre requerido")
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute(
            text(
                "UPDATE inv_unidades SET nombre=:n, abreviatura=:a WHERE id_unidad=:id"
            ),
            {"n": body.nombre.strip(), "a": (body.abreviatura or None), "id": id_unidad},
        )
        conn.commit()
    return {"ok": True}


@router.get("/proveedores")
def list_proveedores(q: str = Query("", max_length=120), me=Depends(get_current_user)):
    _ensure_role(me)
    with get_connection() as conn:
        _ensure_tables(conn)
        if q:
            rows = conn.execute(
                text(
                    f"""
                    SELECT id_proveedor, nombre, direccion, telefono, email, contacto
                    FROM inv_proveedores
                    WHERE {_norm_expr('nombre')} LIKE {_norm_param(':q')}
                       OR {_norm_expr("COALESCE(contacto,'')")} LIKE {_norm_param(':q')}
                    ORDER BY nombre
                    """
                ),
                {"q": f"%{q}%"},
            ).mappings().all()
        else:
            rows = conn.execute(
                text(
                    """
                    SELECT id_proveedor, nombre, direccion, telefono, email, contacto
                    FROM inv_proveedores
                    ORDER BY nombre
                    """
                )
            ).mappings().all()
    return {"ok": True, "items": list(rows)}


@router.post("/proveedores")
def create_proveedor(body: ProveedorIn, me=Depends(get_current_user)):
    _ensure_role(me)
    if not body.nombre.strip():
        raise HTTPException(status_code=400, detail="nombre requerido")
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute(
            text(
                """
                INSERT INTO inv_proveedores(nombre, direccion, telefono, email, contacto)
                VALUES (:n,:d,:t,:e,:c)
                ON CONFLICT (nombre) DO UPDATE
                SET direccion=EXCLUDED.direccion,
                    telefono=EXCLUDED.telefono,
                    email=EXCLUDED.email,
                    contacto=EXCLUDED.contacto,
                    updated_at=now()
                """
            ),
            {
                "n": body.nombre.strip(),
                "d": body.direccion,
                "t": body.telefono,
                "e": body.email,
                "c": body.contacto,
            },
        )
        conn.commit()
    return {"ok": True}


@router.delete("/proveedores/{id_proveedor}")
def delete_proveedor(id_proveedor: int, me=Depends(get_current_user)):
    _ensure_role(me)
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute(text("DELETE FROM inv_proveedores WHERE id_proveedor=:id"), {"id": id_proveedor})
        conn.commit()
    return {"ok": True}


@router.put("/proveedores/{id_proveedor}")
def update_proveedor(id_proveedor: int, body: ProveedorIn, me=Depends(get_current_user)):
    _ensure_role(me)
    if not body.nombre.strip():
        raise HTTPException(status_code=400, detail="nombre requerido")
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute(
            text(
                """
                UPDATE inv_proveedores
                SET nombre=:n,
                    direccion=:d,
                    telefono=:t,
                    email=:e,
                    contacto=:c,
                    updated_at=now()
                WHERE id_proveedor=:id
                """
            ),
            {
                "n": body.nombre.strip(),
                "d": body.direccion,
                "t": body.telefono,
                "e": body.email,
                "c": body.contacto,
                "id": id_proveedor,
            },
        )
        conn.commit()
    return {"ok": True}


@router.get("/productos")
def list_productos(
    q: str = Query("", max_length=120),
    marca: str = Query("", max_length=80),
    me=Depends(get_current_user),
):
    _ensure_role(me)
    with get_connection() as conn:
        _ensure_tables(conn)
        base_where = []
        params = {}
        if q:
            base_where.append(f"({_norm_expr('p.sku')} LIKE {_norm_param(':q')} OR {_norm_expr('p.nombre')} LIKE {_norm_param(':q')})")
            params["q"] = f"%{q}%"
        if marca:
            base_where.append("EXISTS (SELECT 1 FROM inv_producto_marcas pm WHERE pm.id_producto=p.id_producto AND pm.marca=:marca)")
            params["marca"] = marca
        where_sql = ("WHERE " + " AND ".join(base_where)) if base_where else ""
        q_sql = f"""
            SELECT p.id_producto, p.sku, p.nombre, p.precio, p.pack_cantidad, p.is_active,
                   c.nombre AS categoria, u.nombre AS unidad, pr.nombre AS proveedor
            FROM inv_productos p
            LEFT JOIN inv_categorias c ON c.id_categoria=p.id_categoria
            LEFT JOIN inv_unidades u ON u.id_unidad=p.id_unidad
            LEFT JOIN inv_proveedores pr ON pr.id_proveedor=p.id_proveedor
            {where_sql}
            ORDER BY c.nombre NULLS LAST, p.nombre, p.sku
        """
        rows = conn.execute(text(q_sql), params).mappings().all()
    return {"ok": True, "items": list(rows)}


@router.post("/productos")
def create_producto(body: ProductoIn, me=Depends(get_current_user)):
    _ensure_role(me)
    if not body.nombre.strip():
        raise HTTPException(status_code=400, detail="nombre requerido")
    with get_connection() as conn:
        _ensure_tables(conn)
        sku = (body.sku or "").strip()
        if not sku:
            sku = _gen_sku(conn, body.id_categoria)
        row = conn.execute(
            text(
                """
                INSERT INTO inv_productos(sku, nombre, id_categoria, id_unidad, id_proveedor, precio, pack_cantidad, is_active)
                VALUES (:sku,:n,:cat,:uni,:prov,:precio,:pack,:act)
                ON CONFLICT (sku) DO UPDATE
                SET nombre=EXCLUDED.nombre,
                    id_categoria=EXCLUDED.id_categoria,
                    id_unidad=EXCLUDED.id_unidad,
                    id_proveedor=EXCLUDED.id_proveedor,
                    precio=EXCLUDED.precio,
                    pack_cantidad=EXCLUDED.pack_cantidad,
                    is_active=EXCLUDED.is_active,
                    updated_at=now()
                RETURNING id_producto, sku
                """
            ),
            {
                "sku": sku,
                "n": body.nombre.strip(),
                "cat": body.id_categoria,
                "uni": body.id_unidad,
                "prov": body.id_proveedor,
                "precio": body.precio,
                "pack": body.pack_cantidad,
                "act": True if body.is_active is None else body.is_active,
            },
        ).first()
        conn.commit()
    return {"ok": True, "id_producto": int(row[0]) if row else None, "sku": row[1] if row else sku}


@router.delete("/productos/{id_producto}")
def delete_producto(id_producto: int, me=Depends(get_current_user)):
    _ensure_role(me)
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute(text("DELETE FROM inv_productos WHERE id_producto=:id"), {"id": id_producto})
        conn.commit()
    return {"ok": True}


@router.put("/productos/{id_producto}/marcas")
def update_producto_marcas(id_producto: int, body: MarcasIn, me=Depends(get_current_user)):
    _ensure_role(me)
    marcas = [m.strip().upper() for m in (body.marcas or []) if m and m.strip()]
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute(text("DELETE FROM inv_producto_marcas WHERE id_producto=:id"), {"id": id_producto})
        for m in marcas:
            conn.execute(
                text("INSERT INTO inv_producto_marcas(id_producto, marca) VALUES (:id,:m)"),
                {"id": id_producto, "m": m},
            )
        conn.commit()
    return {"ok": True}


@router.get("/stock")
def list_stock(q: str = Query("", max_length=120), me=Depends(get_current_user)):
    _ensure_role(me)
    with get_connection() as conn:
        _ensure_tables(conn)
        where = ""
        params = {}
        if q:
            where = f"WHERE {_norm_expr('p.sku')} LIKE {_norm_param(':q')} OR {_norm_expr('p.nombre')} LIKE {_norm_param(':q')}"
            params["q"] = f"%{q}%"
        rows = conn.execute(
            text(
                f"""
                SELECT p.id_producto, p.sku, p.nombre, p.precio, p.pack_cantidad,
                       c.nombre AS categoria, u.nombre AS unidad,
                       pr.nombre AS proveedor,
                       p.id_categoria, p.id_unidad, p.id_proveedor,
                       s.stock_inicial, s.stock_bodega, s.stock_delivery, s.stock_cocina,
                       COALESCE(s.stock_total, s.stock_actual, 0) AS stock_total,
                       s.stock_actual, s.stock_min, s.stock_max,
                       CASE
                         WHEN s.stock_min IS NOT NULL AND s.stock_max IS NOT NULL AND COALESCE(s.stock_total, s.stock_actual, 0) < s.stock_min
                         THEN (s.stock_max - COALESCE(s.stock_total, s.stock_actual, 0))
                         ELSE 0
                       END AS a_reponer
                FROM inv_productos p
                LEFT JOIN inv_categorias c ON c.id_categoria=p.id_categoria
                LEFT JOIN inv_unidades u ON u.id_unidad=p.id_unidad
                LEFT JOIN inv_proveedores pr ON pr.id_proveedor=p.id_proveedor
                LEFT JOIN inv_stock s ON s.id_producto=p.id_producto
                {where}
                ORDER BY c.nombre NULLS LAST, p.nombre, p.sku
                """
            ),
            params,
        ).mappings().all()
    return {"ok": True, "items": list(rows)}


@router.post("/stock")
def ajustar_stock(body: StockIn, me=Depends(get_current_user)):
    _ensure_role(me)
    with get_connection() as conn:
        _ensure_tables(conn)
        target = body.stock_total if body.stock_total is not None else body.stock_actual
        if target is None:
            target = 0
        # ensure stock row
        conn.execute(
            text("INSERT INTO inv_stock(id_producto, stock_actual) VALUES (:id, 0) ON CONFLICT (id_producto) DO NOTHING"),
            {"id": body.id_producto},
        )
        # update stock
        conn.execute(
            text(
                """
                UPDATE inv_stock
                SET stock_total=:st,
                    stock_actual=COALESCE(:s, stock_actual),
                    updated_at=now()
                WHERE id_producto=:id
                """
            ),
            {"id": body.id_producto, "s": body.stock_actual, "st": target},
        )
        # log movimiento
        conn.execute(
            text(
                """
                INSERT INTO inv_movimientos(id_producto, tipo, subtipo, cantidad, nota)
                VALUES (:id, 'ajuste', 'inventario', :c, :n)
                """
            ),
            {"id": body.id_producto, "c": target, "n": body.nota},
        )
        conn.commit()
    return {"ok": True}


@router.put("/stock/{id_producto}")
def update_stock(id_producto: int, body: StockIn, me=Depends(get_current_user)):
    _ensure_role(me)
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute(
            text("INSERT INTO inv_stock(id_producto, stock_actual) VALUES (:id, 0) ON CONFLICT (id_producto) DO NOTHING"),
            {"id": id_producto},
        )
        total = body.stock_total
        if total is None:
            parts = [body.stock_bodega, body.stock_delivery, body.stock_cocina]
            if any(p is not None for p in parts):
                total = sum([p or 0 for p in parts])
            elif body.stock_actual is not None:
                total = body.stock_actual
        conn.execute(
            text(
                """
                UPDATE inv_stock
                SET stock_actual=COALESCE(:s, stock_actual),
                    stock_inicial=COALESCE(:si, stock_inicial),
                    stock_bodega=COALESCE(:sb, stock_bodega),
                    stock_delivery=COALESCE(:sd, stock_delivery),
                    stock_cocina=COALESCE(:sc, stock_cocina),
                    stock_total=COALESCE(:st, stock_total),
                    stock_min=COALESCE(:mn, stock_min),
                    stock_max=COALESCE(:mx, stock_max),
                    updated_at=now()
                WHERE id_producto=:id
                """
            ),
            {
                "id": id_producto,
                "s": body.stock_actual,
                "si": body.stock_inicial,
                "sb": body.stock_bodega,
                "sd": body.stock_delivery,
                "sc": body.stock_cocina,
                "st": total,
                "mn": getattr(body, "stock_min", None),
                "mx": getattr(body, "stock_max", None),
            },
        )
        conn.commit()
    return {"ok": True}


class StockBulkIn(BaseModel):
    items: list[StockIn] = []
    replace_pending: bool = False


@router.post("/stock/bulk")
def bulk_stock(body: StockBulkIn, me=Depends(get_current_user)):
    _ensure_role(me)
    with get_connection() as conn:
        _ensure_tables(conn)
        created_by = me.get("id") if str(me.get("id", "")).isdigit() else None
        created_by_name = me.get("name") or me.get("nombre") or me.get("username") or ""
        id_toma = None

        # Si el usuario vuelve a subir el CSV para "modificar", reemplazamos su última toma pendiente.
        if body.replace_pending and created_by is not None:
            prev = conn.execute(
                text(
                    """
                    SELECT id_toma
                    FROM inv_tomas
                    WHERE status='PENDIENTE' AND created_by=:u
                    ORDER BY id_toma DESC
                    LIMIT 1
                    """
                ),
                {"u": created_by},
            ).first()
            if prev and prev[0]:
                id_toma = int(prev[0])
                conn.execute(text("DELETE FROM inv_toma_items WHERE id_toma=:t"), {"t": id_toma})

        if id_toma is None:
            row = conn.execute(
                text(
                    """
                    INSERT INTO inv_tomas(created_by, created_by_name, status)
                    VALUES (:u, :n, 'PENDIENTE')
                    RETURNING id_toma
                    """
                ),
                {"u": created_by, "n": created_by_name},
            ).first()
            id_toma = int(row[0]) if row else None

        for it in body.items:
            target = it.stock_total if it.stock_total is not None else it.stock_actual
            if target is None:
                target = 0
            conn.execute(
                text(
                    """
                    INSERT INTO inv_toma_items(id_toma, id_producto, stock_actual)
                    VALUES (:t, :id, :s)
                    """
                ),
                {"t": id_toma, "id": it.id_producto, "s": target},
            )
        conn.commit()
    return {"ok": True, "id_toma": id_toma, "status": "PENDIENTE", "items": len(body.items)}


@router.get("/tomas")
def list_tomas(me=Depends(get_current_user)):
    _ensure_role(me)
    with get_connection() as conn:
        _ensure_tables(conn)
        tomas = conn.execute(
            text(
                """
                SELECT t.id_toma, t.created_at, t.created_by_name, t.status, t.approved_at,
                       (SELECT COUNT(*) FROM inv_toma_items ti WHERE ti.id_toma=t.id_toma) AS items_count
                FROM inv_tomas t
                ORDER BY t.id_toma DESC
                LIMIT 50
                """
            )
        ).mappings().all()
        aprob = conn.execute(
            text(
                """
                SELECT id_toma, rol, approved_by_name, approved_at
                FROM inv_toma_aprobaciones
                """
            )
        ).mappings().all()
    aprob_by = {}
    for a in aprob:
        aprob_by.setdefault(a["id_toma"], []).append(dict(a))
    out = []
    for t in tomas:
        item = dict(t)
        item["aprobaciones"] = aprob_by.get(t["id_toma"], [])
        out.append(item)
    return {"ok": True, "items": out}


@router.post("/tomas/{id_toma}/approve")
def approve_toma(id_toma: int, me=Depends(get_current_user)):
    _ensure_role(me)
    role = (me.get("role") or me.get("rol") or "").upper()
    if role not in ("ADMIN", "SUPERADMIN", "JEFE DE OPERACIONES", "COMPRAS"):
        raise HTTPException(status_code=403, detail="No autorizado para aprobar")
    with get_connection() as conn:
        _ensure_tables(conn)
        role_key = "ADMIN" if role == "SUPERADMIN" else role
        conn.execute(
            text(
                """
                INSERT INTO inv_toma_aprobaciones(id_toma, rol, approved_by, approved_by_name)
                VALUES (:t, :r, :u, :n)
                ON CONFLICT (id_toma, rol) DO NOTHING
                """
            ),
            {
                "t": id_toma,
                "r": role_key,
                "u": me.get("id") if str(me.get("id", "")).isdigit() else None,
                "n": me.get("name") or me.get("nombre") or me.get("username") or "",
            },
        )
        req_roles = {"ADMIN", "JEFE DE OPERACIONES", "COMPRAS"}
        got = conn.execute(
            text("SELECT DISTINCT rol FROM inv_toma_aprobaciones WHERE id_toma=:t"),
            {"t": id_toma},
        ).fetchall()
        got_roles = {r[0] for r in got}
        if req_roles.issubset(got_roles):
            items = conn.execute(
                text("SELECT id_producto, stock_actual FROM inv_toma_items WHERE id_toma=:t"),
                {"t": id_toma},
            ).mappings().all()
            for it in items:
                conn.execute(
                    text("INSERT INTO inv_stock(id_producto, stock_actual) VALUES (:id, 0) ON CONFLICT (id_producto) DO NOTHING"),
                    {"id": it["id_producto"]},
                )
                conn.execute(
                    text(
                        """
                        UPDATE inv_stock
                        SET stock_total=:s,
                            stock_actual=:s,
                            updated_at=now()
                        WHERE id_producto=:id
                        """
                    ),
                    {"id": it["id_producto"], "s": it["stock_actual"]},
                )
                conn.execute(
                    text(
                        """
                        INSERT INTO inv_movimientos(id_producto, tipo, subtipo, cantidad, nota)
                        VALUES (:id, 'ajuste', 'toma', :c, :n)
                        """
                    ),
                    {"id": it["id_producto"], "c": it["stock_actual"], "n": f"Toma inventario #{id_toma}"},
                )
            conn.execute(
                text("UPDATE inv_tomas SET status='APROBADO', approved_at=now() WHERE id_toma=:t"),
                {"t": id_toma},
            )
        conn.commit()
    return {"ok": True}


@router.get("/movimientos")
def list_movimientos(q: str = Query("", max_length=120), me=Depends(get_current_user)):
    _ensure_role(me)
    with get_connection() as conn:
        _ensure_tables(conn)
        where = ""
        params = {}
        if q:
            where = f"WHERE {_norm_expr('p.sku')} LIKE {_norm_param(':q')} OR {_norm_expr('p.nombre')} LIKE {_norm_param(':q')}"
            params["q"] = f"%{q}%"
        rows = conn.execute(
            text(
                f"""
                SELECT m.id_movimiento, m.fecha, m.tipo, m.subtipo, m.cantidad, m.centro_costo, m.marca,
                       m.nota, p.nombre AS producto, p.sku
                FROM inv_movimientos m
                LEFT JOIN inv_productos p ON p.id_producto=m.id_producto
                {where}
                ORDER BY m.fecha DESC, m.id_movimiento DESC
                LIMIT 300
                """
            ),
            params,
        ).mappings().all()
    return {"ok": True, "items": list(rows)}


@router.post("/movimientos")
def create_movimiento(body: MovimientoIn, me=Depends(get_current_user)):
    _ensure_role(me)
    tipo = (body.tipo or "").lower()
    if tipo not in ("ingreso", "salida", "ajuste"):
        raise HTTPException(status_code=400, detail="tipo inválido")
    if not body.id_producto:
        raise HTTPException(status_code=400, detail="producto requerido")
    qty = float(body.cantidad or 0)
    if qty <= 0:
        raise HTTPException(status_code=400, detail="cantidad inválida")
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute(
            text(
                """
                INSERT INTO inv_movimientos(id_producto, tipo, subtipo, cantidad, fecha, centro_costo, marca, nota)
                VALUES (:id, :t, :st, :c, COALESCE(:f, CURRENT_DATE), :cc, :m, :n)
                """
            ),
            {
                "id": body.id_producto,
                "t": tipo,
                "st": body.subtipo,
                "c": qty,
                "f": body.fecha,
                "cc": body.centro_costo,
                "m": body.marca,
                "n": body.nota,
            },
        )
        # update stock
        conn.execute(
            text("INSERT INTO inv_stock(id_producto, stock_actual) VALUES (:id, 0) ON CONFLICT (id_producto) DO NOTHING"),
            {"id": body.id_producto},
        )
        sign = 1 if tipo == "ingreso" else -1
        conn.execute(
            text(
                "UPDATE inv_stock SET stock_actual = COALESCE(stock_actual,0) + (:delta), stock_total = COALESCE(stock_total, stock_actual, 0) + (:delta), updated_at=now() WHERE id_producto=:id"
            ),
            {"delta": qty * sign, "id": body.id_producto},
        )
        conn.commit()
    return {"ok": True}


@router.get("/centros-costo")
def list_centros_costo(me=Depends(get_current_user)):
    _ensure_role(me)
    with get_connection() as conn:
        _ensure_tables(conn)
        rows = conn.execute(text("SELECT id_centro, nombre FROM inv_centros_costo ORDER BY nombre")).mappings().all()
    return {"ok": True, "items": list(rows)}


@router.put("/productos/{id_producto}")
def update_producto(id_producto: int, body: ProductoIn, me=Depends(get_current_user)):
    _ensure_role(me)
    if not body.nombre.strip():
        raise HTTPException(status_code=400, detail="nombre requerido")
    with get_connection() as conn:
        _ensure_tables(conn)
        sku = (body.sku or "").strip()
        if not sku:
            sku = _gen_sku(conn, body.id_categoria)
        conn.execute(
            text(
                """
                UPDATE inv_productos
                SET sku=:sku,
                    nombre=:n,
                    id_categoria=:cat,
                    id_unidad=:uni,
                    id_proveedor=:prov,
                    precio=:precio,
                    pack_cantidad=:pack,
                    is_active=:act,
                    updated_at=now()
                WHERE id_producto=:id
                """
            ),
            {
                "id": id_producto,
                "sku": sku,
                "n": body.nombre.strip(),
                "cat": body.id_categoria,
                "uni": body.id_unidad,
                "prov": body.id_proveedor,
                "precio": body.precio,
                "pack": body.pack_cantidad,
                "act": True if body.is_active is None else body.is_active,
            },
        )
        conn.commit()
    return {"ok": True}
