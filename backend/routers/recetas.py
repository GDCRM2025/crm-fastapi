from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional, Any
from sqlalchemy import text
import json
import logging

from backend.core.db import get_connection
from backend.routers.auth import get_current_user

router = APIRouter(prefix="/ops/recetas", tags=["recetas"])
logger = logging.getLogger("crm.recetas")

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
    # Audit de cambios (mini historial).
    # payload se guarda como TEXT (JSON serializado) para máxima compatibilidad.
    # Nunca debe romper flujos si el entorno no soporta JSONB/casts/DDL.
    try:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS recetas_audit (
                    id_audit SERIAL PRIMARY KEY,
                    id_receta INT NOT NULL REFERENCES recetas(id_receta) ON DELETE CASCADE,
                    action TEXT NOT NULL,
                    username TEXT,
                    payload TEXT,
                    created_at TIMESTAMP DEFAULT now()
                )
                """
            )
        )
        # Si existía con payload JSONB, lo degradamos a TEXT (best-effort).
        try:
            conn.execute(text("ALTER TABLE recetas_audit ALTER COLUMN payload TYPE TEXT USING payload::text"))
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE recetas_audit ADD COLUMN IF NOT EXISTS payload TEXT"))
        except Exception:
            pass
    except Exception:
        pass
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
    try:
        conn.commit()
    except Exception:
        pass

def _audit(conn, id_receta: int, action: str, username: str | None, payload: dict[str, Any] | None = None) -> None:
    try:
        conn.execute(
            text(
                """
                INSERT INTO recetas_audit(id_receta, action, username, payload)
                VALUES (:r, :a, :u, :p)
                """
            ),
            {
                "r": int(id_receta),
                "a": str(action or "")[:80],
                "u": (str(username or "").strip() or None),
                "p": json.dumps(payload or {}, ensure_ascii=False),
            },
        )
    except Exception:
        try:
            logger.exception("recetas_audit insert failed (id_receta=%s action=%s)", id_receta, action)
        except Exception:
            pass


def _payload_to_obj(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return v
    s = str(v or "").strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return s

def _username(me) -> str:
    return str(me.get("username") or me.get("name") or me.get("id") or "").strip()


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
    if not _role_allowed(role, allowed_exact):
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
                "SELECT id_receta, producto, marca, rendimiento, merma_pct, costos_extra, unidad_base, es_sub_receta, is_active, created_at, updated_at FROM recetas WHERE id_receta=:id"
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
        last = None
        try:
            last = conn.execute(
                text(
                    """
                    SELECT id_audit, action, username, created_at
                    FROM recetas_audit
                    WHERE id_receta=:id
                    ORDER BY id_audit DESC
                    LIMIT 1
                    """
                ),
                {"id": id_receta},
            ).mappings().first()
        except Exception:
            last = None
    return {"ok": True, "receta": dict(receta), "items": list(items), "last_update": (dict(last) if last else None)}


@router.get("/{id_receta}/audit")
def receta_audit(id_receta: int, limit: int = 10, me=Depends(get_current_user)):
    _ensure_role_read(me)
    lim = max(1, min(50, int(limit or 10)))
    with get_connection() as conn:
        _ensure_tables(conn)
        try:
            # Siempre casteamos payload a TEXT para evitar problemas de serialización (JSONB/dict).
            rows = conn.execute(
                text(
                    f"""
                    SELECT id_audit, action, username, created_at,
                           COALESCE(payload::text,'') AS payload
                    FROM recetas_audit
                    WHERE id_receta=:id
                    ORDER BY id_audit DESC
                    LIMIT {lim}
                    """
                ),
                {"id": int(id_receta)},
            ).mappings().all()
        except Exception:
            rows = []
    items = []
    for r in rows:
        d = dict(r)
        d["payload"] = _payload_to_obj(d.get("payload"))
        items.append(d)
    return {"ok": True, "items": items}


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
        rid = int(row[0]) if row else 0
        _audit(
            conn,
            rid,
            "RECETA_CREATE",
            _username(me),
            {"producto": body.producto.strip(), "marca": body.marca or ""},
        )
        try:
            _recalc_and_propagate_cost(conn, rid)
        except Exception:
            pass
        conn.commit()
        return {"ok": True, "id": rid}


@router.put("/{id_receta}")
def update_receta(id_receta: int, body: RecetaIn, me=Depends(get_current_user)):
    _ensure_role_write(me)
    with get_connection() as conn:
        _ensure_tables(conn)
        before = None
        try:
            before = conn.execute(
                text("SELECT producto, COALESCE(marca,'') AS marca, rendimiento, merma_pct, costos_extra, unidad_base, es_sub_receta, is_active FROM recetas WHERE id_receta=:id"),
                {"id": id_receta},
            ).mappings().first()
        except Exception:
            before = None
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
        _audit(
            conn,
            int(id_receta),
            "RECETA_UPDATE",
            _username(me),
            {
                "before": dict(before) if before else None,
                "after": {
                    "producto": body.producto.strip(),
                    "marca": body.marca or "",
                    "rendimiento": body.rendimiento,
                    "merma_pct": body.merma_pct,
                    "costos_extra": body.costos_extra,
                    "unidad_base": body.unidad_base,
                    "es_sub_receta": bool(body.es_sub_receta),
                    "is_active": True if body.is_active is None else body.is_active,
                },
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
        try:
            row = conn.execute(
                text("SELECT producto, COALESCE(marca,'') AS marca FROM recetas WHERE id_receta=:id"),
                {"id": id_receta},
            ).mappings().first()
            _audit(conn, int(id_receta), "RECETA_DELETE", _username(me), {"receta": dict(row) if row else None})
        except Exception:
            pass
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
    try:
        with get_connection() as conn:
            _ensure_tables(conn)
            _audit(
                conn,
                int(id_receta),
                "ITEM_UPSERT",
                _username(me),
                {
                    "ingrediente": body.ingrediente.strip(),
                    "cantidad": body.cantidad,
                    "unidad": body.unidad,
                    "costo_unitario": body.costo_unitario,
                    "merma_pct": body.merma_pct,
                    "sub_receta_id": body.sub_receta_id,
                },
            )
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
    except Exception as e:
        try:
            logger.exception("add_item failed (id_receta=%s)", id_receta)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Error al guardar ingrediente: {e}")


@router.put("/{id_receta}/items/{id_item}")
def update_item(id_receta: int, id_item: int, body: ItemIn, me=Depends(get_current_user)):
    _ensure_role_write(me)
    try:
        with get_connection() as conn:
            _ensure_tables(conn)
            _audit(
                conn,
                int(id_receta),
                "ITEM_UPDATE",
                _username(me),
                {
                    "id_item": int(id_item),
                    "ingrediente": body.ingrediente.strip(),
                    "cantidad": body.cantidad,
                    "unidad": body.unidad,
                    "costo_unitario": body.costo_unitario,
                    "merma_pct": body.merma_pct,
                    "sub_receta_id": body.sub_receta_id,
                },
            )
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
    except Exception as e:
        try:
            logger.exception("update_item failed (id_receta=%s id_item=%s)", id_receta, id_item)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Error al actualizar ingrediente: {e}")


@router.delete("/{id_receta}/items/{id_item}")
def delete_item(id_receta: int, id_item: int, me=Depends(get_current_user)):
    _ensure_role_write(me)
    with get_connection() as conn:
        _ensure_tables(conn)
        _audit(conn, int(id_receta), "ITEM_DELETE", _username(me), {"id_item": int(id_item)})
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
