from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy import text

from backend.core.db import get_connection
from backend.routers.auth import get_current_user

router = APIRouter(prefix="/ops/recetas", tags=["recetas"])

def _table_exists(conn, name: str) -> bool:
    return bool(conn.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{name}"}).scalar())


def _col_exists(conn, table: str, col: str) -> bool:
    return bool(
        conn.execute(
            text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_schema='public' AND table_name=:t AND column_name=:c
                """
            ),
            {"t": table, "c": col},
        ).first()
    )


def _recalc_and_propagate_cost(conn, id_receta: int) -> None:
    """
    Recalcula costo unitario de la receta (total items / rendimiento) y lo propaga:
    - productos (venta): actualiza productos.costo para producto+marca
    - inventario (ingredientes): si es_sub_receta, upsert en inv_productos con unidad PORCION
    Best-effort: si faltan tablas/columnas, no revienta el flujo.
    """
    if not _table_exists(conn, "recetas") or not _table_exists(conn, "receta_items"):
        return

    receta = conn.execute(
        text(
            """
            SELECT id_receta, producto, marca, COALESCE(rendimiento, 0) AS rendimiento,
                   COALESCE(merma_pct, 0) AS merma_pct,
                   COALESCE(costos_extra, 0) AS costos_extra,
                   COALESCE(es_sub_receta, FALSE) AS es_sub_receta
            FROM recetas
            WHERE id_receta=:id
            """
        ),
        {"id": id_receta},
    ).mappings().first()
    if not receta:
        return

    # Total considerando merma por item:
    # costo_efectivo = cantidad * costo_unitario / (1 - merma_pct/100)
    # (si merma_pct es null/0 -> factor 1)
    total = conn.execute(
        text(
            """
            SELECT COALESCE(SUM(
              COALESCE(cantidad,0) * COALESCE(costo_unitario,0) *
              (CASE
                WHEN COALESCE(merma_pct,0) <= 0 THEN 1
                WHEN COALESCE(merma_pct,0) >= 95 THEN 20
                ELSE 1.0 / (1.0 - (COALESCE(merma_pct,0) / 100.0))
              END)
            ),0)
            FROM receta_items
            WHERE id_receta=:id
            """
        ),
        {"id": id_receta},
    ).scalar()

    try:
        total_n = float(total or 0)
    except Exception:
        total_n = 0.0

    rendimiento = receta.get("rendimiento") or 0
    try:
        rendimiento_n = float(rendimiento or 0)
    except Exception:
        rendimiento_n = 0.0
    if rendimiento_n <= 0:
        rendimiento_n = 1.0

    # Ajuste por merma y costos extra (si existen)
    merma_pct = receta.get("merma_pct") or 0
    try:
        merma_n = float(merma_pct or 0)
    except Exception:
        merma_n = 0.0
    if merma_n < 0:
        merma_n = 0.0
    if merma_n > 95:
        merma_n = 95.0

    extra = receta.get("costos_extra") or 0
    try:
        extra_n = float(extra or 0)
    except Exception:
        extra_n = 0.0

    effective_yield = rendimiento_n * (1.0 - (merma_n / 100.0))
    if effective_yield <= 0:
        effective_yield = 1.0

    unit_cost = (total_n + extra_n) / effective_yield

    producto = (receta.get("producto") or "").strip()
    marca = (receta.get("marca") or "").strip()
    es_sub = bool(receta.get("es_sub_receta"))

    # 1) Propaga a productos (venta)
    try:
        if _table_exists(conn, "productos") and _col_exists(conn, "productos", "costo"):
            conn.execute(
                text(
                    """
                    UPDATE productos
                    SET costo = :c
                    WHERE UPPER(producto) = UPPER(:p)
                      AND UPPER(COALESCE(marca,'')) = UPPER(:m)
                    """
                ),
                {"c": unit_cost, "p": producto, "m": marca},
            )
    except Exception:
        pass

    # 2) Si es sub-receta, upsert en inventario (ingredientes)
    try:
        if es_sub and _table_exists(conn, "inv_productos") and _table_exists(conn, "inv_unidades"):
            # Asegura unidad segun unidad_base (fallback: PORCION)
            unidad_base = (receta.get("unidad_base") or "").strip() or "PORCION"
            uni_id = conn.execute(
                text("SELECT id_unidad FROM inv_unidades WHERE UPPER(nombre)=UPPER(:n) LIMIT 1"),
                {"n": unidad_base},
            ).scalar()
            if not uni_id:
                ab = "".join([c for c in unidad_base.upper() if c.isalnum()])[:4] or "UNI"
                row = conn.execute(
                    text("INSERT INTO inv_unidades(nombre, abreviatura) VALUES (:n,:ab) RETURNING id_unidad"),
                    {"n": unidad_base, "ab": ab},
                ).first()
                uni_id = int(row[0]) if row else None

            existing = conn.execute(
                text("SELECT id_producto FROM inv_productos WHERE UPPER(nombre)=UPPER(:n) LIMIT 1"),
                {"n": producto},
            ).scalar()
            if existing:
                conn.execute(
                    text(
                        """
                        UPDATE inv_productos
                        SET precio=:p, id_unidad=:u, pack_cantidad=1, is_active=true
                        WHERE id_producto=:id
                        """
                    ),
                    {"p": unit_cost, "u": int(uni_id) if uni_id else None, "id": int(existing)},
                )
            else:
                idx = 1
                while True:
                    sku = f"SUB{idx:03d}"
                    ok = conn.execute(text("SELECT 1 FROM inv_productos WHERE sku=:s"), {"s": sku}).first()
                    if not ok:
                        break
                    idx += 1
                conn.execute(
                    text(
                        """
                        INSERT INTO inv_productos(sku, nombre, id_unidad, precio, pack_cantidad, is_active)
                        VALUES (:sku,:n,:u,:p,1,true)
                        """
                    ),
                    {"sku": sku, "n": producto, "u": int(uni_id) if uni_id else None, "p": unit_cost},
                )
    except Exception:
        pass


def _ensure_tables(conn):
    conn.execute(
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
    try:
        conn.execute(text("ALTER TABLE recetas ADD COLUMN IF NOT EXISTS es_sub_receta BOOLEAN NOT NULL DEFAULT FALSE"))
    except Exception:
        pass
    # columnas nuevas (compat)
    try:
        conn.execute(text("ALTER TABLE recetas ADD COLUMN IF NOT EXISTS merma_pct NUMERIC(5,2)"))
    except Exception:
        pass
    try:
        conn.execute(text("ALTER TABLE recetas ADD COLUMN IF NOT EXISTS costos_extra NUMERIC(12,2)"))
    except Exception:
        pass
    try:
        conn.execute(text("ALTER TABLE recetas ADD COLUMN IF NOT EXISTS unidad_base TEXT"))
    except Exception:
        pass
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS receta_items (
                id_item SERIAL PRIMARY KEY,
                id_receta INT NOT NULL REFERENCES recetas(id_receta) ON DELETE CASCADE,
                ingrediente TEXT NOT NULL,
                cantidad NUMERIC(12,4),
                unidad TEXT,
                costo_unitario NUMERIC(12,4),
                merma_pct NUMERIC(5,2),
                sub_receta_id INT,
                created_at TIMESTAMP DEFAULT now()
            )
            """
        )
    )
    # columnas nuevas (compat)
    try:
        conn.execute(text("ALTER TABLE receta_items ADD COLUMN IF NOT EXISTS sub_receta_id INT"))
    except Exception:
        pass
    try:
        conn.execute(text("ALTER TABLE receta_items ADD COLUMN IF NOT EXISTS merma_pct NUMERIC(5,2)"))
    except Exception:
        pass
    # constraint to avoid duplicates (best effort)
    try:
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_receta_item ON receta_items(id_receta, ingrediente)"
            )
        )
    except Exception:
        pass
    conn.commit()


def _role_upper(me) -> str:
    return (me.get("role") or me.get("rol") or "").upper()

def _role_allowed(role: str, allowed_exact: tuple[str, ...], allowed_contains: tuple[str, ...] = ()) -> bool:
    r = (role or "").upper()
    if r in allowed_exact:
        return True
    return any(s and (s in r) for s in allowed_contains)

def _ensure_role_read(me):
    role = _role_upper(me)
    allowed_exact = (
        "ADMIN",
        "SUPERADMIN",
        "JEFE DE OPERACIONES",
        "OPERACIONES",
        "COMPRAS",
        "MICE",
        "BODEGUERO",
    )
    allowed_contains = (
        # Operación staff (read-only)
        "OPERADOR",
        "CONDUCTOR",
        "CHOFER",
    )
    if not _role_allowed(role, allowed_exact, allowed_contains):
        raise HTTPException(status_code=403, detail="No autorizado")


def _ensure_role_write(me):
    role = _role_upper(me)
    allowed_exact = (
        "ADMIN",
        "SUPERADMIN",
        "JEFE DE OPERACIONES",
        "OPERACIONES",
        "COMPRAS",
        "MICE",
        "BODEGUERO",
    )
    if not _role_allowed(role, allowed_exact):
        raise HTTPException(status_code=403, detail="No autorizado")


class RecetaIn(BaseModel):
    producto: str
    marca: Optional[str] = None
    rendimiento: Optional[float] = None
    merma_pct: Optional[float] = None
    costos_extra: Optional[float] = None
    unidad_base: Optional[str] = None
    es_sub_receta: Optional[bool] = False
    is_active: Optional[bool] = True


class ItemIn(BaseModel):
    ingrediente: str
    cantidad: Optional[float] = None
    unidad: Optional[str] = None
    costo_unitario: Optional[float] = None
    merma_pct: Optional[float] = None
    sub_receta_id: Optional[int] = None


@router.get("")
def list_recetas(
    q: str = Query("", max_length=120),
    marca: str = Query("", max_length=80),
    active_only: bool = Query(False),
    me=Depends(get_current_user),
):
    _ensure_role_read(me)
    with get_connection() as conn:
        _ensure_tables(conn)
        where = []
        params = {}
        if q:
            where.append("(producto ILIKE :q OR COALESCE(marca,'') ILIKE :q)")
            params["q"] = f"%{q}%"
        if marca:
            where.append("UPPER(COALESCE(marca,'')) = UPPER(:m)")
            params["m"] = marca.strip()
        if active_only:
            where.append("COALESCE(is_active, TRUE) = TRUE")

        where_sql = "WHERE " + " AND ".join(where) if where else ""
        sql = f"""
            SELECT id_receta, producto, marca, rendimiento, merma_pct, costos_extra, unidad_base, es_sub_receta, is_active
            FROM recetas
            {where_sql}
            ORDER BY producto ASC
        """
        rows = conn.execute(text(sql), params).mappings().all()
    return {"ok": True, "items": list(rows)}


@router.get("/{id_receta}")
def get_receta(id_receta: int, me=Depends(get_current_user)):
    _ensure_role_read(me)
    with get_connection() as conn:
        _ensure_tables(conn)
        receta = conn.execute(
            text(
                "SELECT id_receta, producto, marca, rendimiento, merma_pct, costos_extra, unidad_base, es_sub_receta, is_active FROM recetas WHERE id_receta=:id"
            ),
            {"id": id_receta},
        ).mappings().first()
        if not receta:
            raise HTTPException(status_code=404, detail="Receta no existe")
        items = conn.execute(
            text(
                """
                SELECT id_item, ingrediente, cantidad, unidad, costo_unitario, merma_pct, sub_receta_id
                FROM receta_items
                WHERE id_receta=:id
                ORDER BY id_item ASC
                """
            ),
            {"id": id_receta},
        ).mappings().all()
    return {"ok": True, "receta": dict(receta), "items": list(items)}


@router.post("")
def create_receta(body: RecetaIn, me=Depends(get_current_user)):
    _ensure_role_write(me)
    if not body.producto.strip():
        raise HTTPException(status_code=400, detail="producto requerido")
    with get_connection() as conn:
        _ensure_tables(conn)
        row = conn.execute(
            text(
                """
                INSERT INTO recetas(producto, marca, rendimiento, merma_pct, costos_extra, unidad_base, es_sub_receta, is_active)
                VALUES (:p, :m, :r, :merma, :extra, :ub, :s, :a)
                RETURNING id_receta
                """
            ),
            {
                "p": body.producto.strip(),
                "m": (body.marca or None),
                "r": body.rendimiento,
                "merma": body.merma_pct,
                "extra": body.costos_extra,
                "ub": (body.unidad_base or None),
                "s": bool(body.es_sub_receta),
                "a": True if body.is_active is None else body.is_active,
            },
        ).fetchone()
        try:
            _recalc_and_propagate_cost(conn, int(row[0]))
        except Exception:
            pass
        conn.commit()
        return {"ok": True, "id": int(row[0])}


@router.put("/{id_receta}")
def update_receta(id_receta: int, body: RecetaIn, me=Depends(get_current_user)):
    _ensure_role_write(me)
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute(
            text(
                """
                UPDATE recetas
                SET producto=:p,
                    marca=:m,
                    rendimiento=:r,
                    merma_pct=:merma,
                    costos_extra=:extra,
                    unidad_base=:ub,
                    es_sub_receta=:s,
                    is_active=:a,
                    updated_at=now()
                WHERE id_receta=:id
                """
            ),
            {
                "p": body.producto.strip(),
                "m": (body.marca or None),
                "r": body.rendimiento,
                "merma": body.merma_pct,
                "extra": body.costos_extra,
                "ub": (body.unidad_base or None),
                "s": bool(body.es_sub_receta),
                "a": True if body.is_active is None else body.is_active,
                "id": id_receta,
            },
        )
        try:
            _recalc_and_propagate_cost(conn, int(id_receta))
        except Exception:
            pass
        conn.commit()
    return {"ok": True}


@router.delete("/{id_receta}")
def delete_receta(id_receta: int, me=Depends(get_current_user)):
    _ensure_role_write(me)
    with get_connection() as conn:
        _ensure_tables(conn)
        # intenta resetear costo de producto (venta) antes de borrar
        try:
            row = conn.execute(
                text("SELECT producto, COALESCE(marca,'') FROM recetas WHERE id_receta=:id"),
                {"id": id_receta},
            ).first()
            if row and _table_exists(conn, "productos") and _col_exists(conn, "productos", "costo"):
                conn.execute(
                    text(
                        """
                        UPDATE productos
                        SET costo = 0
                        WHERE UPPER(producto) = UPPER(:p)
                          AND UPPER(COALESCE(marca,'')) = UPPER(:m)
                        """
                    ),
                    {"p": str(row[0] or ""), "m": str(row[1] or "")},
                )
        except Exception:
            pass
        conn.execute(text("DELETE FROM recetas WHERE id_receta=:id"), {"id": id_receta})
        conn.commit()
    return {"ok": True}


@router.post("/{id_receta}/items")
def add_item(id_receta: int, body: ItemIn, me=Depends(get_current_user)):
    _ensure_role_write(me)
    if not body.ingrediente.strip():
        raise HTTPException(status_code=400, detail="ingrediente requerido")
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute(
            text(
                """
                INSERT INTO receta_items(id_receta, ingrediente, cantidad, unidad, costo_unitario, merma_pct, sub_receta_id)
                VALUES (:r, :i, :c, :u, :cu, :m, :sr)
                ON CONFLICT (id_receta, ingrediente) DO UPDATE
                SET cantidad=EXCLUDED.cantidad,
                    unidad=EXCLUDED.unidad,
                    costo_unitario=EXCLUDED.costo_unitario,
                    merma_pct=EXCLUDED.merma_pct,
                    sub_receta_id=EXCLUDED.sub_receta_id
                """
            ),
            {
                "r": id_receta,
                "i": body.ingrediente.strip(),
                "c": body.cantidad,
                "u": (body.unidad or None),
                "cu": body.costo_unitario,
                "m": body.merma_pct,
                "sr": body.sub_receta_id,
            },
        )
        try:
            _recalc_and_propagate_cost(conn, int(id_receta))
        except Exception:
            pass
        conn.commit()
    return {"ok": True}


@router.put("/{id_receta}/items/{id_item}")
def update_item(id_receta: int, id_item: int, body: ItemIn, me=Depends(get_current_user)):
    _ensure_role_write(me)
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute(
            text(
                """
                UPDATE receta_items
                SET ingrediente=:i,
                    cantidad=:c,
                    unidad=:u,
                    costo_unitario=:cu,
                    merma_pct=:m,
                    sub_receta_id=:sr
                WHERE id_item=:id AND id_receta=:r
                """
            ),
            {
                "i": body.ingrediente.strip(),
                "c": body.cantidad,
                "u": (body.unidad or None),
                "cu": body.costo_unitario,
                "m": body.merma_pct,
                "sr": body.sub_receta_id,
                "id": id_item,
                "r": id_receta,
            },
        )
        try:
            _recalc_and_propagate_cost(conn, int(id_receta))
        except Exception:
            pass
        conn.commit()
    return {"ok": True}


@router.delete("/{id_receta}/items/{id_item}")
def delete_item(id_receta: int, id_item: int, me=Depends(get_current_user)):
    _ensure_role_write(me)
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute(
            text("DELETE FROM receta_items WHERE id_item=:id AND id_receta=:r"),
            {"id": id_item, "r": id_receta},
        )
        try:
            _recalc_and_propagate_cost(conn, int(id_receta))
        except Exception:
            pass
        conn.commit()
    return {"ok": True}
