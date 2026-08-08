from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional, Any, Dict
from sqlalchemy import text
from pathlib import Path
from datetime import date
import shutil
import time
import re
import uuid
import json
import mimetypes

from backend.core.db import get_connection
from backend.routers.auth import get_current_user

router = APIRouter(prefix="/ops/inventario", tags=["inventario"])

BASE_DIR = Path(__file__).resolve().parents[2]
TRUCK_DOCS_DIR = BASE_DIR / "web" / "uploads" / "camiones"
MAX_DOC_BYTES = 10 * 1024 * 1024

DOC_TYPES = {
    "permiso_circulacion": "Permiso circulación",
    "revision_tecnica": "Revisión técnica",
    "soap": "SOAP",
    "seguro": "Seguro",
    "padron": "Padrón",
    "otro": "Otro",
}
DOC_ORDER = [
    "permiso_circulacion",
    "revision_tecnica",
    "soap",
    "seguro",
    "padron",
    "otro",
]
ALLOWED_DOC_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".doc", ".docx"}


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
                clasificacion TEXT,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT now(),
                updated_at TIMESTAMP DEFAULT now()
            )
            """
        )
    )
    conn.execute(text("ALTER TABLE inv_productos ADD COLUMN IF NOT EXISTS pack_cantidad NUMERIC(12,3)"))
    conn.execute(text("ALTER TABLE inv_productos ADD COLUMN IF NOT EXISTS clasificacion TEXT"))
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
                tipo TEXT NOT NULL,
                subtipo TEXT,
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

    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS inv_camion_documentos (
                id_documento SERIAL PRIMARY KEY,
                id_asset INT NOT NULL,
                tipo_doc TEXT NOT NULL,
                nombre_original TEXT,
                nombre_archivo TEXT NOT NULL,
                file_url TEXT NOT NULL,
                file_path TEXT,
                vencimiento DATE,
                uploaded_by INT,
                uploaded_by_name TEXT,
                created_at TIMESTAMP DEFAULT now(),
                updated_at TIMESTAMP DEFAULT now(),
                UNIQUE (id_asset, tipo_doc)
            )
            """
        )
    )
    conn.execute(text("ALTER TABLE inv_camion_documentos ADD COLUMN IF NOT EXISTS nombre_original TEXT"))
    conn.execute(text("ALTER TABLE inv_camion_documentos ADD COLUMN IF NOT EXISTS file_path TEXT"))
    conn.execute(text("ALTER TABLE inv_camion_documentos ADD COLUMN IF NOT EXISTS vencimiento DATE"))
    conn.execute(text("ALTER TABLE inv_camion_documentos ADD COLUMN IF NOT EXISTS uploaded_by INT"))
    conn.execute(text("ALTER TABLE inv_camion_documentos ADD COLUMN IF NOT EXISTS uploaded_by_name TEXT"))
    conn.execute(text("ALTER TABLE inv_camion_documentos ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT now()"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_inv_camion_documentos_asset ON inv_camion_documentos(id_asset)"))

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


def _asset_exists(conn, id_asset: int) -> bool:
    try:
        row = conn.execute(
            text("SELECT 1 FROM assets WHERE id_asset=:id LIMIT 1"),
            {"id": id_asset},
        ).first()
        return bool(row)
    except Exception:
        return True


def _normalize_doc_type(value: str) -> str:
    s = (value or "").strip().lower()
    s = s.replace(" ", "_").replace("-", "_")
    s = s.translate(str.maketrans("áéíóúüñ", "aeiouun"))
    s = re.sub(r"[^a-z0-9_]+", "", s)
    if s not in DOC_TYPES:
        raise HTTPException(status_code=400, detail="tipo_doc inválido")
    return s


def _normalize_date(value: Optional[str]) -> Optional[str]:
    s = str(value or "").strip()
    if not s:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        raise HTTPException(status_code=400, detail="vencimiento inválido, usa YYYY-MM-DD")
    return s


def _safe_unlink(path_str: Optional[str]) -> None:
    try:
        if path_str:
            Path(path_str).unlink(missing_ok=True)
    except Exception:
        pass


def _doc_ext(filename: str, content_type: Optional[str]) -> str:
    ext = Path(filename or "").suffix.lower().strip()
    if ext in ALLOWED_DOC_EXTS:
        return ext
    guess = mimetypes.guess_extension(content_type or "") or ""
    guess = guess.lower().strip()
    if guess == ".jpe":
        guess = ".jpg"
    if guess in ALLOWED_DOC_EXTS:
        return guess
    raise HTTPException(
        status_code=400,
        detail="Formato no permitido. Usa PDF, PNG, JPG, WEBP, DOC o DOCX.",
    )


def _doc_row_to_dict(row) -> dict:
    d = dict(row)
    d["tipo_label"] = DOC_TYPES.get(d.get("tipo_doc") or "", d.get("tipo_doc") or "")
    return d


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
    clasificacion: Optional[str] = None  # MICE | OPERACIONES | None
    is_active: Optional[bool] = True


def _norm_clasificacion(v: Optional[str]) -> Optional[str]:
    s = str(v or "").strip().upper()
    if not s:
        return None
    if s in ("OPS", "OPERACION", "OPERACIONES"):
        return "OPERACIONES"
    if s in ("MICE",):
        return "MICE"
    raise HTTPException(status_code=400, detail="clasificacion inválida (usa MICE u OPERACIONES)")


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
                text(
                    f"SELECT id_categoria, nombre FROM inv_categorias "
                    f"WHERE {_norm_expr('nombre')} LIKE {_norm_param(':q')} ORDER BY nombre"
                ),
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
                text(
                    f"SELECT id_unidad, nombre, abreviatura FROM inv_unidades "
                    f"WHERE {_norm_expr('nombre')} LIKE {_norm_param(':q')} "
                    f"OR {_norm_expr('abreviatura')} LIKE {_norm_param(':q')} "
                    f"ORDER BY nombre"
                ),
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
            text("UPDATE inv_unidades SET nombre=:n, abreviatura=:a WHERE id_unidad=:id"),
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
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0, le=500000),
    me=Depends(get_current_user),
):
    _ensure_role(me)
    with get_connection() as conn:
        _ensure_tables(conn)
        base_where = []
        params = {}
        if q:
            base_where.append(
                f"({_norm_expr('p.sku')} LIKE {_norm_param(':q')} "
                f"OR {_norm_expr('p.nombre')} LIKE {_norm_param(':q')})"
            )
            params["q"] = f"%{q}%"
        if marca:
            base_where.append(
                "EXISTS (SELECT 1 FROM inv_producto_marcas pm WHERE pm.id_producto=p.id_producto AND pm.marca=:marca)"
            )
            params["marca"] = marca
        where_sql = ("WHERE " + " AND ".join(base_where)) if base_where else ""
        q_sql = f"""
            SELECT p.id_producto, p.sku, p.nombre, p.id_categoria, p.id_unidad, p.id_proveedor,
                   p.precio, p.pack_cantidad, p.clasificacion, p.is_active,
                   c.nombre AS categoria, u.nombre AS unidad, pr.nombre AS proveedor
            FROM inv_productos p
            LEFT JOIN inv_categorias c ON c.id_categoria=p.id_categoria
            LEFT JOIN inv_unidades u ON u.id_unidad=p.id_unidad
            LEFT JOIN inv_proveedores pr ON pr.id_proveedor=p.id_proveedor
            {where_sql}
            ORDER BY c.nombre NULLS LAST, p.nombre, p.sku
            LIMIT :lim OFFSET :off
        """
        params["lim"] = int(limit)
        params["off"] = int(offset)
        rows = conn.execute(text(q_sql), params).mappings().all()
        total = conn.execute(text(f"SELECT count(*) FROM inv_productos p {where_sql}"), params).scalar()
    return {"ok": True, "items": list(rows), "limit": int(limit), "offset": int(offset), "total": int(total or 0)}


@router.post("/productos")
def create_producto(body: ProductoIn, me=Depends(get_current_user)):
    _ensure_role(me)
    if not body.nombre.strip():
        raise HTTPException(status_code=400, detail="nombre requerido")
    with get_connection() as conn:
        _ensure_tables(conn)
        clasif = _norm_clasificacion(body.clasificacion)
        sku = (body.sku or "").strip()
        if not sku:
            sku = _gen_sku(conn, body.id_categoria)
        row = conn.execute(
            text(
                """
                INSERT INTO inv_productos(sku, nombre, id_categoria, id_unidad, id_proveedor, precio, pack_cantidad, clasificacion, is_active)
                VALUES (:sku,:n,:cat,:uni,:prov,:precio,:pack,:clasif,:act)
                ON CONFLICT (sku) DO UPDATE
                SET nombre=EXCLUDED.nombre,
                    id_categoria=EXCLUDED.id_categoria,
                    id_unidad=EXCLUDED.id_unidad,
                    id_proveedor=EXCLUDED.id_proveedor,
                    precio=EXCLUDED.precio,
                    pack_cantidad=EXCLUDED.pack_cantidad,
                    clasificacion=EXCLUDED.clasificacion,
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
                "clasif": clasif,
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
                       p.clasificacion,
                       c.nombre AS categoria, u.nombre AS unidad, pr.nombre AS proveedor,
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
        conn.execute(
            text("INSERT INTO inv_stock(id_producto, stock_actual) VALUES (:id, 0) ON CONFLICT (id_producto) DO NOTHING"),
            {"id": body.id_producto},
        )
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
        conn.execute(
            text("INSERT INTO inv_stock(id_producto, stock_actual) VALUES (:id, 0) ON CONFLICT (id_producto) DO NOTHING"),
            {"id": body.id_producto},
        )
        sign = 1 if tipo == "ingreso" else -1
        conn.execute(
            text(
                "UPDATE inv_stock "
                "SET stock_actual = COALESCE(stock_actual,0) + (:delta), "
                "    stock_total = COALESCE(stock_total, stock_actual, 0) + (:delta), "
                "    updated_at=now() "
                "WHERE id_producto=:id"
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
        clasif = _norm_clasificacion(body.clasificacion)
        # Detectar cambio de precio/nombre para propagar a recetas (costo_unitario).
        prev = conn.execute(
            text("SELECT COALESCE(sku,''), COALESCE(nombre,''), precio FROM inv_productos WHERE id_producto=:id LIMIT 1"),
            {"id": id_producto},
        ).first()
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
                    clasificacion=:clasif,
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
                "clasif": clasif,
                "act": True if body.is_active is None else body.is_active,
            },
        )
        # Propagación a recetas: si cambia el precio del ingrediente en inventario,
        # actualizamos receta_items.costo_unitario para ingredientes que matchean por nombre (único).
        try:
            prev_sku = str(prev[0] if prev else "") if prev else ""
            prev_nombre = str(prev[1] if prev else "") if prev else ""
            prev_precio = float(prev[2]) if (prev and prev[2] is not None) else None
        except Exception:
            prev_sku = ""
            prev_nombre = ""
            prev_precio = None

        new_nombre = body.nombre.strip()
        try:
            new_precio = float(body.precio) if body.precio is not None else None
        except Exception:
            new_precio = None

        try:
            name_changed = (prev_nombre or "").strip().lower() != (new_nombre or "").strip().lower()
            price_changed = (prev_precio is None and new_precio is not None) or (prev_precio is not None and new_precio is not None and abs(prev_precio - new_precio) > 0.0001)
            if new_precio is not None and (name_changed or price_changed):
                # Si hay duplicados por nombre en inventario, no forzamos update (ambigüedad).
                dup_cnt = conn.execute(
                    text("SELECT COUNT(*) FROM inv_productos WHERE upper(nombre)=upper(:n)"),
                    {"n": new_nombre},
                ).scalar()
                if int(dup_cnt or 0) == 1:
                    # Actualiza items por nombre (caso estándar: ingrediente único).
                    conn.execute(
                        text(
                            """
                            UPDATE receta_items
                            SET costo_unitario=:p
                            WHERE upper(ingrediente)=upper(:n)
                            """
                        ),
                        {"p": new_precio, "n": new_nombre},
                    )
                    # Si cambió el nombre, también migra los items con el nombre antiguo (para no perder vínculo).
                    if name_changed and prev_nombre:
                        conn.execute(
                            text(
                                """
                                UPDATE receta_items
                                SET ingrediente=:n2, costo_unitario=:p
                                WHERE upper(ingrediente)=upper(:n1)
                                """
                            ),
                            {"n2": new_nombre, "n1": prev_nombre, "p": new_precio},
                        )
                    # Recalcular recetas afectadas y propagar costo a productos (venta).
                    try:
                        from backend.routers.recetas import _recalc_and_propagate_cost  # type: ignore

                        rec_ids = conn.execute(
                            text(
                                """
                                SELECT DISTINCT id_receta
                                FROM receta_items
                                WHERE upper(ingrediente)=upper(:n)
                                """
                            ),
                            {"n": new_nombre},
                        ).fetchall()
                        for (rid,) in rec_ids or []:
                            try:
                                _recalc_and_propagate_cost(conn, int(rid))
                            except Exception:
                                pass
                    except Exception:
                        pass
                else:
                    # Intento best-effort por SKU embebido en el nombre (si existe).
                    sku_try = (sku or prev_sku or "").strip()
                    if sku_try:
                        conn.execute(
                            text(
                                """
                                UPDATE receta_items
                                SET costo_unitario=:p
                                WHERE ingrediente ILIKE ('%' || :sku || '%')
                                """
                            ),
                            {"p": new_precio, "sku": sku_try},
                        )
        except Exception:
            # best-effort: no romper inventario por falla en recetas
            pass

        conn.commit()
    return {"ok": True}


@router.get("/camiones/{id_asset}/documentos")
def list_camion_documentos(id_asset: int, me=Depends(get_current_user)):
    _ensure_role(me)
    with get_connection() as conn:
        _ensure_tables(conn)
        if not _asset_exists(conn, id_asset):
            raise HTTPException(status_code=404, detail="Camión no existe")
        rows = conn.execute(
            text(
                """
                SELECT id_documento,
                       id_asset,
                       tipo_doc,
                       COALESCE(nombre_original, nombre_archivo) AS nombre_archivo,
                       file_url,
                       file_path,
                       vencimiento,
                       uploaded_by_name,
                       created_at,
                       updated_at
                FROM inv_camion_documentos
                WHERE id_asset=:id
                ORDER BY CASE tipo_doc
                    WHEN 'permiso_circulacion' THEN 1
                    WHEN 'revision_tecnica' THEN 2
                    WHEN 'soap' THEN 3
                    WHEN 'seguro' THEN 4
                    WHEN 'padron' THEN 5
                    ELSE 99
                END, id_documento
                """
            ),
            {"id": id_asset},
        ).mappings().all()
    return {"ok": True, "items": [_doc_row_to_dict(r) for r in rows]}


@router.post("/camiones/{id_asset}/documentos")
async def upload_camion_documento(
    id_asset: int,
    tipo_doc: str = Form(...),
    vencimiento: Optional[str] = Form(None),
    archivo: UploadFile = File(...),
    me=Depends(get_current_user),
):
    _ensure_role(me)
    tipo = _normalize_doc_type(tipo_doc)
    venc = _normalize_date(vencimiento)

    if not archivo or not (archivo.filename or "").strip():
        raise HTTPException(status_code=400, detail="archivo requerido")

    ext = _doc_ext(archivo.filename or "", getattr(archivo, "content_type", None))
    data = await archivo.read()
    if not data:
        raise HTTPException(status_code=400, detail="archivo vacío")
    if len(data) > MAX_DOC_BYTES:
        raise HTTPException(status_code=400, detail="archivo supera 10 MB")

    save_dir = TRUCK_DOCS_DIR / str(id_asset)
    save_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{tipo}_{uuid.uuid4().hex[:12]}{ext}"
    abs_path = save_dir / stored_name
    old_path = None

    try:
        abs_path.write_bytes(data)
        file_url = f"/web/uploads/camiones/{id_asset}/{stored_name}"

        with get_connection() as conn:
            _ensure_tables(conn)
            if not _asset_exists(conn, id_asset):
                raise HTTPException(status_code=404, detail="Camión no existe")

            prev = conn.execute(
                text(
                    """
                    SELECT file_path
                    FROM inv_camion_documentos
                    WHERE id_asset=:id AND tipo_doc=:t
                    """
                ),
                {"id": id_asset, "t": tipo},
            ).mappings().first()
            if prev:
                old_path = prev.get("file_path")

            row = conn.execute(
                text(
                    """
                    INSERT INTO inv_camion_documentos(
                        id_asset, tipo_doc, nombre_original, nombre_archivo, file_url, file_path,
                        vencimiento, uploaded_by, uploaded_by_name
                    )
                    VALUES (
                        :id_asset, :tipo_doc, :nombre_original, :nombre_archivo, :file_url, :file_path,
                        :vencimiento, :uploaded_by, :uploaded_by_name
                    )
                    ON CONFLICT (id_asset, tipo_doc)
                    DO UPDATE SET
                        nombre_original = EXCLUDED.nombre_original,
                        nombre_archivo = EXCLUDED.nombre_archivo,
                        file_url = EXCLUDED.file_url,
                        file_path = EXCLUDED.file_path,
                        vencimiento = EXCLUDED.vencimiento,
                        uploaded_by = EXCLUDED.uploaded_by,
                        uploaded_by_name = EXCLUDED.uploaded_by_name,
                        updated_at = now()
                    RETURNING id_documento,
                              id_asset,
                              tipo_doc,
                              COALESCE(nombre_original, nombre_archivo) AS nombre_archivo,
                              file_url,
                              file_path,
                              vencimiento,
                              uploaded_by_name,
                              created_at,
                              updated_at
                    """
                ),
                {
                    "id_asset": id_asset,
                    "tipo_doc": tipo,
                    "nombre_original": (archivo.filename or "").strip(),
                    "nombre_archivo": stored_name,
                    "file_url": file_url,
                    "file_path": str(abs_path),
                    "vencimiento": venc,
                    "uploaded_by": me.get("id") if str(me.get("id", "")).isdigit() else None,
                    "uploaded_by_name": me.get("name") or me.get("nombre") or me.get("username") or "",
                },
            ).mappings().one()
            conn.commit()

        if old_path and old_path != str(abs_path):
            _safe_unlink(old_path)

        return {"ok": True, "item": _doc_row_to_dict(row)}
    except HTTPException:
        _safe_unlink(str(abs_path))
        raise
    except Exception:
        _safe_unlink(str(abs_path))
        raise HTTPException(status_code=500, detail="No se pudo guardar el documento")


@router.delete("/camiones/{id_asset}/documentos/{id_documento}")
def delete_camion_documento(id_asset: int, id_documento: int, me=Depends(get_current_user)):
    _ensure_role(me)
    with get_connection() as conn:
        _ensure_tables(conn)
        row = conn.execute(
            text(
                """
                SELECT id_documento, file_path
                FROM inv_camion_documentos
                WHERE id_documento=:doc AND id_asset=:asset
                """
            ),
            {"doc": id_documento, "asset": id_asset},
        ).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="Documento no existe")
        conn.execute(
            text("DELETE FROM inv_camion_documentos WHERE id_documento=:doc"),
            {"doc": id_documento},
        )
        conn.commit()
    _safe_unlink(row.get("file_path"))
    return {"ok": True}




    # =========================
# CAMIONES · DOCUMENTOS / CHECKLISTS / CONDUCTORES
# =========================

TRUCK_DOC_ROOT = Path(__file__).resolve().parents[2] / "web" / "uploads" / "camiones"
TRUCK_DOC_TYPES = {
    "permiso_circulacion": "Permiso de circulación",
    "revision_tecnica": "Revisión técnica",
    "soap": "SOAP",
    "seguro": "Seguro",
}


class CamionChecklistIn(BaseModel):
    id_asset: int
    tipo: str
    fecha: str
    conductor: str
    payload: Dict[str, Any] = {}


def _truck_table_exists(conn, table: str) -> bool:
    try:
        return bool(conn.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}).scalar())
    except Exception:
        return False


def _truck_cols_for(conn, table: str) -> set[str]:
    try:
        rows = conn.execute(
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
    except Exception:
        return set()


def _truck_parse_date(raw: Optional[str]) -> Optional[date]:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except Exception:
        raise HTTPException(status_code=400, detail="fecha inválida; usa YYYY-MM-DD")


def _truck_safe_doc_type(tipo_doc: str) -> str:
    key = str(tipo_doc or "").strip().lower()
    if key not in TRUCK_DOC_TYPES:
        raise HTTPException(status_code=400, detail="tipo_doc inválido")
    return key


def _truck_public_url(id_asset: int, filename: str) -> str:
    return f"/web/uploads/camiones/{int(id_asset)}/{filename}"


def _truck_ensure_extras(conn) -> None:
    _ensure_tables(conn)

    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS inv_camion_docs (
                id_doc SERIAL PRIMARY KEY,
                id_asset INT NOT NULL,
                tipo_doc TEXT NOT NULL,
                fecha_vencimiento DATE,
                file_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_url TEXT NOT NULL,
                mime_type TEXT,
                uploaded_at TIMESTAMP DEFAULT now(),
                uploaded_by TEXT,
                UNIQUE(id_asset, tipo_doc)
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS inv_camion_checklists (
                id_checklist SERIAL PRIMARY KEY,
                id_asset INT NOT NULL,
                tipo TEXT NOT NULL,
                fecha DATE NOT NULL,
                conductor TEXT NOT NULL,
                payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMP DEFAULT now(),
                created_by TEXT
            )
            """
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_inv_camion_checklists_asset_tipo_fecha ON inv_camion_checklists(id_asset, tipo, fecha)"
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_inv_camion_checklists_conductor ON inv_camion_checklists(conductor)"
        )
    )
    conn.commit()


def _truck_conductores_activos(conn) -> list[str]:
    nombres: set[str] = set()

    # RRHH
    if _truck_table_exists(conn, "rrhh_staff"):
        cols = _truck_cols_for(conn, "rrhh_staff")
        name_col = "colaborador" if "colaborador" in cols else ("nombre" if "nombre" in cols else None)
        if name_col:
            role_filters = []
            for c in ("cargo", "rol", "puesto", "area"):
                if c in cols:
                    role_filters.append(f"UPPER(COALESCE({c},'')) LIKE '%CONDUCTOR%'")

            if role_filters:
                where = []
                if "is_active" in cols:
                    where.append("COALESCE(is_active, TRUE)=TRUE")
                where.append("(" + " OR ".join(role_filters) + ")")
                sql = f"""
                    SELECT DISTINCT NULLIF(BTRIM({name_col}), '') AS nombre
                    FROM rrhh_staff
                    WHERE {' AND '.join(where)}
                    ORDER BY 1
                """
                rows = conn.execute(text(sql)).fetchall()
                for r in rows:
                    if r and r[0]:
                        nombres.add(str(r[0]).strip())

    # Usuarios
    if _truck_table_exists(conn, "usuarios"):
        cols = _truck_cols_for(conn, "usuarios")
        name_col = None
        for c in ("nombre", "name", "username"):
            if c in cols:
                name_col = c
                break
        role_col = "rol" if "rol" in cols else ("role" if "role" in cols else None)
        if name_col and role_col:
            where = [f"UPPER(COALESCE({role_col},'')) LIKE '%CONDUCTOR%'"]
            if "is_active" in cols:
                where.insert(0, "COALESCE(is_active, TRUE)=TRUE")
            sql = f"""
                SELECT DISTINCT NULLIF(BTRIM({name_col}), '') AS nombre
                FROM usuarios
                WHERE {' AND '.join(where)}
                ORDER BY 1
            """
            rows = conn.execute(text(sql)).fetchall()
            for r in rows:
                if r and r[0]:
                    nombres.add(str(r[0]).strip())

    return sorted(nombres, key=lambda x: x.upper())


@router.get("/camiones/conductores/activos")
def camion_conductores_activos(me=Depends(get_current_user)):
    _ensure_role(me)
    with get_connection() as conn:
        _truck_ensure_extras(conn)
        items = _truck_conductores_activos(conn)
    return {"ok": True, "items": items}


@router.get("/camiones/{id_asset}/conductores-entrega")
def camion_conductores_entrega(
    id_asset: int,
    fecha: str = Query("", max_length=10),
    me=Depends(get_current_user),
):
    _ensure_role(me)
    with get_connection() as conn:
        _truck_ensure_extras(conn)

        params: Dict[str, Any] = {"id": int(id_asset)}
        where = ["id_asset=:id", "UPPER(tipo)='ENTREGA'"]

        if fecha:
            d = _truck_parse_date(fecha)
            where.append("fecha=:f")
            params["f"] = d

        rows = conn.execute(
            text(
                f"""
                SELECT DISTINCT conductor
                FROM inv_camion_checklists
                WHERE {' AND '.join(where)}
                ORDER BY conductor
                """
            ),
            params,
        ).fetchall()

        items = [str(r[0]).strip() for r in rows if r and r[0]]

    return {"ok": True, "items": items}


@router.get("/camiones/{id_asset}/documentos")
def camion_documentos(id_asset: int, me=Depends(get_current_user)):
    _ensure_role(me)
    with get_connection() as conn:
        _truck_ensure_extras(conn)
        rows = conn.execute(
            text(
                """
                SELECT id_doc, id_asset, tipo_doc, fecha_vencimiento, file_name, file_url, mime_type, uploaded_at, uploaded_by
                FROM inv_camion_docs
                WHERE id_asset=:id
                ORDER BY tipo_doc
                """
            ),
            {"id": int(id_asset)},
        ).mappings().all()
    return {"ok": True, "items": [dict(r) for r in rows]}


@router.post("/camiones/{id_asset}/documentos/upload")
def camion_documento_upload(
    id_asset: int,
    tipo_doc: str = Form(...),
    fecha_vencimiento: str = Form(...),
    archivo: UploadFile = File(...),
    me=Depends(get_current_user),
):
    _ensure_role(me)

    tipo = _truck_safe_doc_type(tipo_doc)
    fecha = _truck_parse_date(fecha_vencimiento)
    if not fecha:
        raise HTTPException(status_code=400, detail="Debes elegir la fecha de vencimiento")

    if not archivo or not archivo.filename:
        raise HTTPException(status_code=400, detail="Archivo requerido")

    ext = Path(archivo.filename).suffix.lower().strip()
    if ext not in {".pdf", ".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(status_code=400, detail="Formato no permitido. Usa PDF/JPG/PNG/WEBP")

    safe_name = f"{tipo}_{int(time.time())}{ext}"
    asset_dir = TRUCK_DOC_ROOT / str(int(id_asset))
    asset_dir.mkdir(parents=True, exist_ok=True)

    dest = asset_dir / safe_name
    with dest.open("wb") as f:
        shutil.copyfileobj(archivo.file, f)

    file_url = _truck_public_url(id_asset, safe_name)
    uploaded_by = (
        me.get("name")
        or me.get("nombre")
        or me.get("username")
        or me.get("email")
        or "CRM"
    )

    with get_connection() as conn:
        _truck_ensure_extras(conn)

        prev = conn.execute(
            text(
                """
                SELECT file_path
                FROM inv_camion_docs
                WHERE id_asset=:id AND tipo_doc=:t
                """
            ),
            {"id": int(id_asset), "t": tipo},
        ).scalar()

        conn.execute(
            text(
                """
                INSERT INTO inv_camion_docs(
                    id_asset, tipo_doc, fecha_vencimiento, file_name, file_path, file_url, mime_type, uploaded_by
                )
                VALUES (:id, :t, :fv, :fn, :fp, :fu, :mt, :ub)
                ON CONFLICT (id_asset, tipo_doc) DO UPDATE SET
                    fecha_vencimiento=EXCLUDED.fecha_vencimiento,
                    file_name=EXCLUDED.file_name,
                    file_path=EXCLUDED.file_path,
                    file_url=EXCLUDED.file_url,
                    mime_type=EXCLUDED.mime_type,
                    uploaded_at=now(),
                    uploaded_by=EXCLUDED.uploaded_by
                """
            ),
            {
                "id": int(id_asset),
                "t": tipo,
                "fv": fecha,
                "fn": safe_name,
                "fp": str(dest),
                "fu": file_url,
                "mt": (archivo.content_type or None),
                "ub": uploaded_by,
            },
        )
        conn.commit()

    try:
        if prev and str(prev) != str(dest):
            oldp = Path(str(prev))
            if oldp.exists():
                oldp.unlink()
    except Exception:
        pass

    return {
        "ok": True,
        "item": {
            "id_asset": int(id_asset),
            "tipo_doc": tipo,
            "fecha_vencimiento": str(fecha),
            "file_name": safe_name,
            "file_url": file_url,
        },
    }


@router.delete("/camiones/{id_asset}/documentos/{tipo_doc}")
def camion_documento_delete(id_asset: int, tipo_doc: str, me=Depends(get_current_user)):
    _ensure_role(me)
    tipo = _truck_safe_doc_type(tipo_doc)

    with get_connection() as conn:
        _truck_ensure_extras(conn)

        row = conn.execute(
            text(
                """
                SELECT file_path
                FROM inv_camion_docs
                WHERE id_asset=:id AND tipo_doc=:t
                """
            ),
            {"id": int(id_asset), "t": tipo},
        ).first()

        conn.execute(
            text("DELETE FROM inv_camion_docs WHERE id_asset=:id AND tipo_doc=:t"),
            {"id": int(id_asset), "t": tipo},
        )
        conn.commit()

    try:
        if row and row[0]:
            p = Path(str(row[0]))
            if p.exists():
                p.unlink()
    except Exception:
        pass

    return {"ok": True}


@router.get("/camiones/checklists")
def camion_checklists(
    id_asset: int = Query(..., ge=1),
    tipo: str = Query(..., max_length=20),
    fecha: str = Query("", max_length=10),
    conductor: str = Query("", max_length=120),
    me=Depends(get_current_user),
):
    _ensure_role(me)
    t = str(tipo or "").strip().upper()
    if t not in {"ENTREGA", "DEVOLUCION"}:
        raise HTTPException(status_code=400, detail="tipo inválido")

    with get_connection() as conn:
        _truck_ensure_extras(conn)

        where = ["id_asset=:id", "UPPER(tipo)=:t"]
        params: Dict[str, Any] = {"id": int(id_asset), "t": t}

        if fecha:
            params["f"] = _truck_parse_date(fecha)
            where.append("fecha=:f")

        if conductor.strip():
            params["c"] = conductor.strip()
            where.append("conductor=:c")

        rows = conn.execute(
            text(
                f"""
                SELECT id_checklist, id_asset, tipo, fecha, conductor, payload, created_at, created_by
                FROM inv_camion_checklists
                WHERE {' AND '.join(where)}
                ORDER BY fecha DESC, created_at DESC, id_checklist DESC
                """
            ),
            params,
        ).mappings().all()

    return {"ok": True, "items": [dict(r) for r in rows]}


@router.post("/camiones/checklists")
def camion_checklists_create(body: CamionChecklistIn, me=Depends(get_current_user)):
    _ensure_role(me)

    tipo = str(body.tipo or "").strip().upper()
    if tipo not in {"ENTREGA", "DEVOLUCION"}:
        raise HTTPException(status_code=400, detail="tipo inválido")

    fecha = _truck_parse_date(body.fecha)
    if not fecha:
        raise HTTPException(status_code=400, detail="fecha requerida")

    conductor = str(body.conductor or "").strip()
    if not conductor:
        raise HTTPException(status_code=400, detail="conductor requerido")

    payload = body.payload or {}
    created_by = (
        me.get("name")
        or me.get("nombre")
        or me.get("username")
        or me.get("email")
        or "CRM"
    )

    with get_connection() as conn:
        _truck_ensure_extras(conn)

        row = conn.execute(
            text(
                """
                INSERT INTO inv_camion_checklists(id_asset, tipo, fecha, conductor, payload, created_by)
                VALUES (:id, :t, :f, :c, CAST(:p AS JSONB), :u)
                RETURNING id_checklist
                """
            ),
            {
                "id": int(body.id_asset),
                "t": tipo,
                "f": fecha,
                "c": conductor,
                "p": json.dumps(payload, ensure_ascii=False),
                "u": created_by,
            },
        ).first()
        conn.commit()

    return {"ok": True, "id_checklist": int(row[0]) if row else None}
