from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from datetime import date
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.core.db import get_connection
from backend.routers.auth import get_current_user

router = APIRouter(prefix="/finanzas", tags=["finanzas"])

def _has_column(conn, table: str, column: str) -> bool:
    try:
        r = conn.execute(
            text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema='public'
                  AND table_name=:t
                  AND column_name=:c
                LIMIT 1
                """
            ),
            {"t": table, "c": column},
        ).first()
        return bool(r)
    except DBAPIError:
        # Very defensive: if information_schema isn't accessible for any reason,
        # we prefer not to break the app import (Passenger would turn this into 404s).
        return True


def _add_column_if_missing(conn, table: str, column: str, col_def_sql: str) -> None:
    if _has_column(conn, table, column):
        return
    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_def_sql}"))


def _ensure_tables(conn):
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS plan_cuentas (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                classification TEXT,
                description TEXT,
                example_transactions TEXT,
                who_inputs TEXT,
                how_to_impute TEXT,
                parent_code TEXT REFERENCES plan_cuentas(code),
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT now(),
                updated_at TIMESTAMP DEFAULT now()
            )
            """
        )
    )
    conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_plan_cuentas_parent ON plan_cuentas(parent_code)")
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS fin_eventos (
                id_evento SERIAL PRIMARY KEY,
                id_lead INT,
                num_cotizacion TEXT,
                id_cotizacion INT,
                cliente TEXT,
                comuna TEXT,
                marca TEXT,
                tipo_cliente TEXT,
                fecha_evento DATE,
                monto_bruto NUMERIC(14,2) DEFAULT 0,
                monto_neto NUMERIC(14,2) DEFAULT 0,
                iva NUMERIC(14,2) DEFAULT 0,
                traslado NUMERIC(14,2) DEFAULT 0,
                abono NUMERIC(14,2) DEFAULT 0,
                saldo NUMERIC(14,2) DEFAULT 0,
                comision_pct NUMERIC(6,2) DEFAULT 0,
                comision_monto NUMERIC(14,2) DEFAULT 0,
                created_at TIMESTAMP DEFAULT now()
            )
            """
        )
    )
    # Backward-compatible schema upgrades (works even on older PostgreSQL versions).
    _add_column_if_missing(conn, "fin_eventos", "num_cotizacion", "TEXT")
    _add_column_if_missing(conn, "fin_eventos", "id_cotizacion", "INT")
    _add_column_if_missing(conn, "fin_eventos", "comuna", "TEXT")
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS fin_gastos (
                id_gasto SERIAL PRIMARY KEY,
                fecha DATE NOT NULL,
                cuenta_code TEXT,
                monto NUMERIC(14,2) NOT NULL DEFAULT 0,
                descripcion TEXT,
                proveedor TEXT,
                marca TEXT,
                centro_costo TEXT,
                tipo_doc TEXT,
                doc_num TEXT,
                pagado BOOLEAN NOT NULL DEFAULT FALSE,
                fecha_vencimiento DATE,
                fecha_pago DATE,
                created_at TIMESTAMP DEFAULT now()
            )
            """
        )
    )
    # hardening: columnas nuevas para reportabilidad/pagos (sin IF NOT EXISTS)
    _add_column_if_missing(conn, "fin_gastos", "doc_num", "TEXT")
    _add_column_if_missing(conn, "fin_gastos", "pagado", "BOOLEAN NOT NULL DEFAULT FALSE")
    _add_column_if_missing(conn, "fin_gastos", "fecha_vencimiento", "DATE")
    _add_column_if_missing(conn, "fin_gastos", "fecha_pago", "DATE")
    _add_column_if_missing(conn, "fin_gastos", "tipo_doc", "TEXT")

    # Centros de costo (simple, CRUD admin). Semillas: GREENDIAMOND, ROLFI
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS fin_centros_costo (
                id_cc SERIAL PRIMARY KEY,
                nombre TEXT NOT NULL UNIQUE,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT now()
            )
            """
        )
    )
    conn.execute(
        text(
            """
            INSERT INTO fin_centros_costo(nombre)
            VALUES ('GREENDIAMOND'), ('ROLFI')
            ON CONFLICT (nombre) DO NOTHING
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS fin_pagos (
                id_pago SERIAL PRIMARY KEY,
                id_evento INT NOT NULL REFERENCES fin_eventos(id_evento) ON DELETE CASCADE,
                fecha TIMESTAMP DEFAULT now(),
                monto NUMERIC(14,2) NOT NULL DEFAULT 0,
                metodo TEXT,
                referencia TEXT,
                doc_num TEXT
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS fin_saldos (
                id_saldo SERIAL PRIMARY KEY,
                cuenta_code TEXT,
                monto NUMERIC(14,2) NOT NULL DEFAULT 0,
                fecha DATE DEFAULT CURRENT_DATE,
                nota TEXT,
                created_at TIMESTAMP DEFAULT now()
            )
            """
        )
    )
    conn.commit()


def _ensure_brand_accounts(conn):
    try:
        marcas = conn.execute(
            text("SELECT id_marca, COALESCE(nombre, marca) AS nombre FROM marcas")
        ).mappings().all()
        for m in marcas:
            mid = m.get("id_marca")
            name = m.get("nombre") or ""
            if mid is None or not name:
                continue
            code = str(1100 + int(mid))
            exists = conn.execute(
                text("SELECT 1 FROM plan_cuentas WHERE code=:c"),
                {"c": code},
            ).scalar()
            if exists:
                continue
            # Parent 1000 (Caja y Bancos) si existe
            parent = conn.execute(
                text("SELECT 1 FROM plan_cuentas WHERE code='1000'")
            ).scalar()
            conn.execute(
                text(
                    """
                    INSERT INTO plan_cuentas(code, name, type, classification, parent_code, is_active)
                    VALUES (:c, :n, 'asset', 'Activo', :p, TRUE)
                    """
                ),
                {"c": code, "n": f"Banco - {name}", "p": "1000" if parent else None},
            )
        conn.commit()
    except Exception:
        conn.rollback()


def _ensure_role(me):
    role = (me.get("role") or me.get("rol") or "").upper()
    if role not in ("ADMIN", "SUPERADMIN", "FINANZAS"):
        raise HTTPException(status_code=403, detail="No autorizado")


def _ensure_roles(me, allowed):
    role = (me.get("role") or me.get("rol") or "").upper()
    # FINANZAS tiene acceso completo al módulo finanzas.
    if role == "FINANZAS":
        return role
    if role not in allowed:
        raise HTTPException(status_code=403, detail="No autorizado")
    return role


class CuentaIn(BaseModel):
    code: str
    name: str
    type: str
    classification: Optional[str] = None
    description: Optional[str] = None
    example_transactions: Optional[str] = None
    who_inputs: Optional[str] = None
    how_to_impute: Optional[str] = None
    parent_code: Optional[str] = None
    is_active: Optional[bool] = True


class EventoIn(BaseModel):
    id_lead: Optional[int] = None
    num_cotizacion: Optional[str] = None
    id_cotizacion: Optional[int] = None
    cliente: Optional[str] = None
    comuna: Optional[str] = None
    marca: Optional[str] = None
    tipo_cliente: Optional[str] = None
    fecha_evento: Optional[date] = None
    monto_bruto: Optional[float] = 0
    monto_neto: Optional[float] = 0
    iva: Optional[float] = 0
    traslado: Optional[float] = 0
    abono: Optional[float] = 0
    saldo: Optional[float] = 0
    comision_pct: Optional[float] = 0
    comision_monto: Optional[float] = 0


class GastoIn(BaseModel):
    fecha: date
    cuenta_code: Optional[str] = None
    monto: float
    descripcion: Optional[str] = None
    proveedor: Optional[str] = None
    marca: Optional[str] = None
    centro_costo: Optional[str] = None
    tipo_doc: Optional[str] = None
    doc_num: Optional[str] = None
    pagado: Optional[bool] = False
    fecha_vencimiento: Optional[date] = None
    fecha_pago: Optional[date] = None


class PagoIn(BaseModel):
    monto: float
    metodo: Optional[str] = None
    referencia: Optional[str] = None
    doc_num: Optional[str] = None
    fecha: Optional[date] = None


@router.get("/plan-cuentas")
def list_plan_cuentas(
    q: str = Query("", max_length=120),
    me=Depends(get_current_user),
):
    _ensure_role(me)
    with get_connection() as conn:
        _ensure_tables(conn)
        _ensure_brand_accounts(conn)
        if q:
            rows = conn.execute(
                text(
                    """
                    SELECT code, name, type, classification, description,
                           example_transactions, who_inputs, how_to_impute,
                           parent_code, is_active
                    FROM plan_cuentas
                    WHERE code ILIKE :q OR name ILIKE :q OR COALESCE(description,'') ILIKE :q
                    ORDER BY code::int NULLS LAST, code ASC
                    """
                ),
                {"q": f"%{q}%"},
            ).mappings().all()
        else:
            rows = conn.execute(
                text(
                    """
                    SELECT code, name, type, classification, description,
                           example_transactions, who_inputs, how_to_impute,
                           parent_code, is_active
                    FROM plan_cuentas
                    ORDER BY code::int NULLS LAST, code ASC
                    """
                )
            ).mappings().all()
    return {"ok": True, "items": list(rows)}


@router.post("/plan-cuentas")
def create_plan_cuenta(body: CuentaIn, me=Depends(get_current_user)):
    _ensure_role(me)
    code = (body.code or "").strip()
    name = (body.name or "").strip()
    if not code or not name:
        raise HTTPException(status_code=400, detail="code y name requeridos")
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute(
            text(
                """
                INSERT INTO plan_cuentas
                (code, name, type, classification, description, example_transactions,
                 who_inputs, how_to_impute, parent_code, is_active)
                VALUES
                (:code, :name, :type, :classification, :description, :example_transactions,
                 :who_inputs, :how_to_impute, :parent_code, :is_active)
                """
            ),
            {
                "code": code,
                "name": name,
                "type": (body.type or "").strip(),
                "classification": body.classification,
                "description": body.description,
                "example_transactions": body.example_transactions,
                "who_inputs": body.who_inputs,
                "how_to_impute": body.how_to_impute,
                "parent_code": (body.parent_code or None),
                "is_active": True if body.is_active is None else body.is_active,
            },
        )
        conn.commit()
    return {"ok": True}


@router.put("/plan-cuentas/{code}")
def update_plan_cuenta(code: str, body: CuentaIn, me=Depends(get_current_user)):
    _ensure_role(me)
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute(
            text(
                """
                UPDATE plan_cuentas
                SET name=:name,
                    type=:type,
                    classification=:classification,
                    description=:description,
                    example_transactions=:example_transactions,
                    who_inputs=:who_inputs,
                    how_to_impute=:how_to_impute,
                    parent_code=:parent_code,
                    is_active=:is_active,
                    updated_at=now()
                WHERE code=:code
                """
            ),
            {
                "code": code,
                "name": (body.name or "").strip(),
                "type": (body.type or "").strip(),
                "classification": body.classification,
                "description": body.description,
                "example_transactions": body.example_transactions,
                "who_inputs": body.who_inputs,
                "how_to_impute": body.how_to_impute,
                "parent_code": (body.parent_code or None),
                "is_active": True if body.is_active is None else body.is_active,
            },
        )
        conn.commit()
    return {"ok": True}


@router.delete("/plan-cuentas/{code}")
def delete_plan_cuenta(code: str, me=Depends(get_current_user)):
    _ensure_role(me)
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute(text("DELETE FROM plan_cuentas WHERE code=:code"), {"code": code})
        conn.commit()
    return {"ok": True}


@router.post("/eventos")
def create_evento(body: EventoIn, me=Depends(get_current_user)):
    _ensure_roles(me, {"ADMIN", "SUPERADMIN", "COMPRAS", "JEFE DE OPERACIONES"})
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute(
            text(
                """
                INSERT INTO fin_eventos
                (id_lead, num_cotizacion, id_cotizacion, cliente, comuna, marca, tipo_cliente, fecha_evento, monto_bruto, monto_neto, iva,
                 traslado, abono, saldo, comision_pct, comision_monto)
                VALUES
                (:id_lead, :num_cotizacion, :id_cotizacion, :cliente, :comuna, :marca, :tipo_cliente, :fecha_evento, :monto_bruto, :monto_neto, :iva,
                 :traslado, :abono, :saldo, :comision_pct, :comision_monto)
                """
            ),
            body.dict(),
        )
        conn.commit()
    return {"ok": True}


@router.get("/eventos")
def list_eventos(
    month: int = Query(0, ge=0, le=12),
    year: int = Query(0, ge=0, le=2100),
    me=Depends(get_current_user),
):
    _ensure_roles(me, {"ADMIN", "SUPERADMIN", "COMPRAS", "JEFE DE OPERACIONES"})
    with get_connection() as conn:
        _ensure_tables(conn)
        where = ""
        params = {}
        if month and year:
            where = "WHERE EXTRACT(MONTH FROM fecha_evento)=:m AND EXTRACT(YEAR FROM fecha_evento)=:y"
            params = {"m": month, "y": year}
        rows = conn.execute(
            text(
                f"""
                SELECT id_evento, id_lead, num_cotizacion, id_cotizacion, cliente, comuna, marca, tipo_cliente, fecha_evento,
                       monto_bruto, monto_neto, iva, traslado, abono, saldo, comision_pct, comision_monto, created_at
                FROM fin_eventos
                {where}
                ORDER BY fecha_evento DESC NULLS LAST, id_evento DESC
                LIMIT 500
                """
            ),
            params,
        ).mappings().all()
    return {"ok": True, "items": list(rows)}


@router.post("/gastos")
def create_gasto(body: GastoIn, me=Depends(get_current_user)):
    _ensure_roles(me, {"ADMIN", "SUPERADMIN", "COMPRAS"})
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute(
            text(
                """
                INSERT INTO fin_gastos
                (fecha, cuenta_code, monto, descripcion, proveedor, marca, centro_costo, tipo_doc, doc_num, pagado, fecha_vencimiento, fecha_pago)
                VALUES
                (:fecha, :cuenta_code, :monto, :descripcion, :proveedor, :marca, :centro_costo, :tipo_doc, :doc_num, COALESCE(:pagado,FALSE), :fecha_vencimiento, :fecha_pago)
                """
            ),
            body.dict(),
        )
        conn.commit()
    return {"ok": True}


@router.get("/gastos")
def list_gastos(
    month: int = Query(0, ge=0, le=12),
    year: int = Query(0, ge=0, le=2100),
    me=Depends(get_current_user),
):
    _ensure_roles(me, {"ADMIN", "SUPERADMIN", "COMPRAS"})
    with get_connection() as conn:
        _ensure_tables(conn)
        where = ""
        params = {}
        if month and year:
            where = "WHERE EXTRACT(MONTH FROM fecha)=:m AND EXTRACT(YEAR FROM fecha)=:y"
            params = {"m": month, "y": year}
        rows = conn.execute(
            text(
                f"""
                SELECT id_gasto, fecha, cuenta_code, monto, descripcion, proveedor, marca, centro_costo,
                       tipo_doc, doc_num, pagado, fecha_vencimiento, fecha_pago, created_at
                FROM fin_gastos
                {where}
                ORDER BY fecha DESC, id_gasto DESC
                LIMIT 500
                """
            ),
            params,
        ).mappings().all()
    return {"ok": True, "items": list(rows)}


@router.get("/centros_costo")
def list_centros_costo(me=Depends(get_current_user)):
    _ensure_roles(me, {"ADMIN", "SUPERADMIN"})
    with get_connection() as conn:
        _ensure_tables(conn)
        rows = conn.execute(
            text(
                """
                SELECT id_cc, nombre, is_active, created_at
                FROM fin_centros_costo
                ORDER BY nombre
                """
            )
        ).mappings().all()
    return {"ok": True, "items": list(rows)}


@router.post("/centros_costo")
def create_centro_costo(body: dict = Body(...), me=Depends(get_current_user)):
    _ensure_roles(me, {"ADMIN", "SUPERADMIN"})
    nombre = str(body.get("nombre") or "").strip().upper()
    if not nombre:
        raise HTTPException(400, "nombre requerido")
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute(
            text("INSERT INTO fin_centros_costo(nombre,is_active) VALUES (:n, TRUE) ON CONFLICT (nombre) DO NOTHING"),
            {"n": nombre},
        )
        conn.commit()
    return {"ok": True}


@router.put("/centros_costo/{id_cc}")
def update_centro_costo(id_cc: int, body: dict = Body(...), me=Depends(get_current_user)):
    _ensure_roles(me, {"ADMIN", "SUPERADMIN"})
    nombre = body.get("nombre")
    is_active = body.get("is_active")
    sets = []
    params = {"id": int(id_cc)}
    if nombre is not None:
        n = str(nombre or "").strip().upper()
        if not n:
            raise HTTPException(400, "nombre requerido")
        sets.append("nombre=:n")
        params["n"] = n
    if is_active is not None:
        sets.append("is_active=:a")
        params["a"] = bool(is_active)
    if not sets:
        return {"ok": True, "updated": False}
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute(text(f"UPDATE fin_centros_costo SET {', '.join(sets)} WHERE id_cc=:id"), params)
        conn.commit()
    return {"ok": True, "updated": True}


@router.delete("/centros_costo/{id_cc}")
def delete_centro_costo(id_cc: int, me=Depends(get_current_user)):
    _ensure_roles(me, {"ADMIN", "SUPERADMIN"})
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute(text("UPDATE fin_centros_costo SET is_active=FALSE WHERE id_cc=:id"), {"id": int(id_cc)})
        conn.commit()
    return {"ok": True}


@router.get("/cxp_resumen")
def cxp_resumen(me=Depends(get_current_user)):
    """
    Cuentas por pagar: resumen por proveedor (pendiente vs pagado).
    """
    _ensure_roles(me, {"ADMIN", "SUPERADMIN", "COMPRAS"})
    with get_connection() as conn:
        _ensure_tables(conn)
        rows = conn.execute(
            text(
                """
                SELECT COALESCE(NULLIF(btrim(proveedor),''),'(SIN PROVEEDOR)') AS proveedor,
                       SUM(CASE WHEN COALESCE(pagado,FALSE)=FALSE THEN COALESCE(monto,0) ELSE 0 END) AS pendiente,
                       SUM(CASE WHEN COALESCE(pagado,FALSE)=TRUE  THEN COALESCE(monto,0) ELSE 0 END) AS pagado,
                       COUNT(*) AS n
                FROM fin_gastos
                GROUP BY 1
                ORDER BY pendiente DESC, proveedor ASC
                LIMIT 200
                """
            )
        ).mappings().all()
    return {"ok": True, "items": list(rows)}


@router.get("/eventos/{id_evento}/pagos")
def list_pagos(id_evento: int, me=Depends(get_current_user)):
    _ensure_roles(me, {"ADMIN", "SUPERADMIN", "COMPRAS", "JEFE DE OPERACIONES"})
    with get_connection() as conn:
        _ensure_tables(conn)
        rows = conn.execute(
            text(
                """
                SELECT id_pago, id_evento, fecha, monto, metodo, referencia, doc_num
                FROM fin_pagos
                WHERE id_evento=:id
                ORDER BY fecha DESC, id_pago DESC
                """
            ),
            {"id": id_evento},
        ).mappings().all()
    return {"ok": True, "items": list(rows)}


@router.post("/eventos/{id_evento}/pagos")
def add_pago(id_evento: int, body: PagoIn, me=Depends(get_current_user)):
    _ensure_roles(me, {"ADMIN", "SUPERADMIN", "COMPRAS", "JEFE DE OPERACIONES"})
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute(
            text(
                """
                INSERT INTO fin_pagos(id_evento, fecha, monto, metodo, referencia, doc_num)
                VALUES (:id, COALESCE(:f, now()), :m, :met, :ref, :doc)
                """
            ),
            {
                "id": id_evento,
                "f": body.fecha,
                "m": body.monto,
                "met": body.metodo,
                "ref": body.referencia,
                "doc": body.doc_num,
            },
        )
        total = conn.execute(
            text("SELECT COALESCE(SUM(monto),0) FROM fin_pagos WHERE id_evento=:id"),
            {"id": id_evento},
        ).scalar()
        conn.execute(
            text(
                """
                UPDATE fin_eventos
                SET abono=:ab, saldo=COALESCE(monto_bruto,0) - :ab
                WHERE id_evento=:id
                """
            ),
            {"ab": total, "id": id_evento},
        )
        conn.commit()
    return {"ok": True, "abono": float(total or 0)}


@router.get("/pnl")
def pnl(
    month: int = Query(0, ge=0, le=12),
    year: int = Query(0, ge=0, le=2100),
    me=Depends(get_current_user),
):
    _ensure_roles(me, {"ADMIN", "SUPERADMIN", "COMPRAS", "JEFE DE OPERACIONES"})
    today = date.today()
    month = month or today.month
    year = year or today.year
    with get_connection() as conn:
        _ensure_tables(conn)
        _ensure_brand_accounts(conn)
        plan = conn.execute(
            text(
                """
                SELECT code, name, type, parent_code
                FROM plan_cuentas
                WHERE is_active IS TRUE
                ORDER BY code::int NULLS LAST, code ASC
                """
            )
        ).mappings().all()

        events = conn.execute(
            text(
                """
                SELECT marca, SUM(COALESCE(monto_neto, monto_bruto)) AS total
                FROM fin_eventos
                WHERE EXTRACT(MONTH FROM fecha_evento)=:m AND EXTRACT(YEAR FROM fecha_evento)=:y
                GROUP BY marca
                """
            ),
            {"m": month, "y": year},
        ).mappings().all()

        gastos = conn.execute(
            text(
                """
                SELECT cuenta_code, SUM(monto) AS total
                FROM fin_gastos
                WHERE EXTRACT(MONTH FROM fecha)=:m AND EXTRACT(YEAR FROM fecha)=:y
                GROUP BY cuenta_code
                """
            ),
            {"m": month, "y": year},
        ).mappings().all()

        # Sueldos (RRHH real: faltas + adelantos del mes)
        sueldos_total = 0.0
        try:
            staff_rows = conn.execute(
                text(
                    """
                    SELECT colaborador, ficha
                    FROM rrhh_staff
                    WHERE is_active IS TRUE
                    """
                )
            ).mappings().all()
            adel_rows = conn.execute(
                text(
                    """
                    SELECT lower(colaborador) AS c, COALESCE(SUM(monto),0) AS total
                    FROM rrhh_adelantos
                    WHERE EXTRACT(MONTH FROM fecha)=:m AND EXTRACT(YEAR FROM fecha)=:y
                    GROUP BY lower(colaborador)
                    """
                ),
                {"m": month, "y": year},
            ).fetchall()
            faltas_rows = conn.execute(
                text(
                    """
                    SELECT lower(colaborador) AS c, COALESCE(SUM(dias),0) AS dias
                    FROM rrhh_inasistencias
                    WHERE EXTRACT(MONTH FROM fecha)=:m AND EXTRACT(YEAR FROM fecha)=:y
                    GROUP BY lower(colaborador)
                    """
                ),
                {"m": month, "y": year},
            ).fetchall()
            adel_map = {str(r[0]): float(r[1] or 0) for r in adel_rows}
            faltas_map = {str(r[0]): float(r[1] or 0) for r in faltas_rows}

            def _num(v):
                if v is None:
                    return 0.0
                if isinstance(v, (int, float)):
                    return float(v)
                try:
                    return float(str(v).replace(".", "").replace(",", "."))
                except Exception:
                    return 0.0

            for r in staff_rows:
                ficha = r.get("ficha") or {}
                if isinstance(ficha, str):
                    try:
                        ficha = json.loads(ficha)
                    except Exception:
                        ficha = {}
                key = str((r.get("colaborador") or "")).lower()
                sueldo = _num(ficha.get("renta_liquida") or ficha.get("sueldo_fijo") or 0)
                faltas = float(faltas_map.get(key, 0) or 0)
                adel = float(adel_map.get(key, 0) or 0)
                diario = (sueldo / 30.0) if sueldo else 0.0
                total_pagar = max(0.0, sueldo - (faltas * diario) - adel)
                sueldos_total += total_pagar
        except Exception:
            sueldos_total = 0.0

    brand_account = {
        "CAMALEON": "4110",
        "GOURMET": "4120",
        "EXPRESS": "4130",
        "DEL SABOR": "4150",
    }

    balances = {}
    for e in events:
        code = brand_account.get((e["marca"] or "").upper(), "4000")
        balances[code] = balances.get(code, 0) + float(e["total"] or 0)
    for g in gastos:
        code = g["cuenta_code"] or "6000"
        balances[code] = balances.get(code, 0) + float(g["total"] or 0)
    if sueldos_total:
        balances["6210"] = balances.get("6210", 0) + float(sueldos_total or 0)

    # Comisiones por marca (gasto 6130) desde tabla comisiones
    try:
        com_rows = conn.execute(
            text("SELECT marca, porcentaje FROM comisiones WHERE is_active IS TRUE")
        ).mappings().all()
        com_map = {}
        default_pct = 0.0
        for r in com_rows:
            m = (r.get("marca") or "").upper().strip()
            pct = float(r.get("porcentaje") or 0)
            if not m:
                continue
            if m in ("*", "TODAS"):
                default_pct = pct or default_pct
            else:
                com_map[m] = pct
        com_total = 0.0
        for e in events:
            marca = (e.get("marca") or "").upper()
            pct = com_map.get(marca, default_pct)
            com_total += float(e.get("total") or 0) * (pct / 100.0)
        if com_total:
            balances["6130"] = balances.get("6130", 0) + float(com_total)
    except Exception:
        pass

    # acumular a padres
    by_code = {p["code"]: dict(p) for p in plan}
    for code, amt in list(balances.items()):
        cur = by_code.get(code)
        while cur and cur.get("parent_code"):
            parent = cur["parent_code"]
            balances[parent] = balances.get(parent, 0) + amt
            cur = by_code.get(parent)

    ingresos = sum(v for k, v in balances.items() if (by_code.get(k, {}).get("type") == "revenue"))
    gastos_total = sum(v for k, v in balances.items() if (by_code.get(k, {}).get("type") == "expense"))

    items = []
    for p in plan:
        if p["type"] not in ("revenue", "expense"):
            continue
        items.append({**p, "balance": balances.get(p["code"], 0)})

    return {
        "ok": True,
        "month": month,
        "year": year,
        "totals": {
            "ingresos": ingresos,
            "gastos": gastos_total,
            "resultado": ingresos - gastos_total,
        },
        "items": items,
    }


@router.get("/saldos")
def saldos_list(me=Depends(get_current_user)):
    _ensure_roles(me, {"ADMIN", "SUPERADMIN", "COMPRAS", "JEFE DE OPERACIONES"})
    with get_connection() as conn:
        _ensure_tables(conn)
        _ensure_brand_accounts(conn)
        rows = conn.execute(
            text(
                """
                SELECT id_saldo, cuenta_code, monto, fecha, nota, created_at
                FROM fin_saldos
                ORDER BY fecha DESC, id_saldo DESC
                """
            )
        ).mappings().all()
    return {"ok": True, "items": [dict(r) for r in rows]}


class SaldoIn(BaseModel):
    cuenta_code: str
    monto: Optional[float] = 0
    fecha: Optional[date] = None
    nota: Optional[str] = None


@router.post("/saldos")
def saldos_create(body: SaldoIn, me=Depends(get_current_user)):
    _ensure_roles(me, {"ADMIN", "SUPERADMIN", "COMPRAS", "JEFE DE OPERACIONES"})
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute(
            text(
                """
                INSERT INTO fin_saldos(cuenta_code, monto, fecha, nota)
                VALUES (:c, :m, COALESCE(:f, CURRENT_DATE), :n)
                """
            ),
            {"c": body.cuenta_code, "m": body.monto or 0, "f": body.fecha, "n": body.nota},
        )
        conn.commit()
    return {"ok": True}


@router.put("/saldos/{id_saldo}")
def saldos_update(id_saldo: int, body: SaldoIn, me=Depends(get_current_user)):
    _ensure_roles(me, {"ADMIN", "SUPERADMIN", "COMPRAS", "JEFE DE OPERACIONES"})
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute(
            text(
                """
                UPDATE fin_saldos
                SET cuenta_code=:c, monto=:m, fecha=COALESCE(:f, fecha), nota=:n
                WHERE id_saldo=:id
                """
            ),
            {"id": id_saldo, "c": body.cuenta_code, "m": body.monto or 0, "f": body.fecha, "n": body.nota},
        )
        conn.commit()
    return {"ok": True}


@router.delete("/saldos/{id_saldo}")
def saldos_delete(id_saldo: int, me=Depends(get_current_user)):
    _ensure_roles(me, {"ADMIN", "SUPERADMIN", "COMPRAS", "JEFE DE OPERACIONES"})
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute(text("DELETE FROM fin_saldos WHERE id_saldo=:id"), {"id": id_saldo})
        conn.commit()
    return {"ok": True}
