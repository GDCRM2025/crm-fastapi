from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, Any
from datetime import date
from decimal import Decimal
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.core.db import get_connection
from backend.routers.auth import get_current_user

router = APIRouter(prefix="/finanzas", tags=["finanzas"])

def _to_float(v):
    if v is None:
        return None
    if isinstance(v, Decimal):
        try:
            return float(v)
        except Exception:
            return float(str(v))
    return v


def _row_json(row: dict) -> dict:
    """
    Convierte tipos no serializables (Decimal) a JSON-safe.
    """
    out = {}
    for k, v in (row or {}).items():
        if isinstance(v, Decimal):
            out[k] = _to_float(v)
        else:
            out[k] = v
    return out


def _norm_text(v: Optional[str], *, max_len: int = 200, upper: bool = False) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    if upper:
        s = s.upper()
    if len(s) > max_len:
        s = s[:max_len]
    return s


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
    # Trazabilidad cuando no hay abono (OC/fecha estimada) + updated_at.
    _add_column_if_missing(conn, "fin_eventos", "abono_mode", "TEXT")
    _add_column_if_missing(conn, "fin_eventos", "abono_ref", "TEXT")
    _add_column_if_missing(conn, "fin_eventos", "abono_due_date", "DATE")
    _add_column_if_missing(conn, "fin_eventos", "updated_at", "TIMESTAMP DEFAULT now()")
    _add_column_if_missing(conn, "fin_eventos", "ejecutivo", "TEXT")
    _add_column_if_missing(conn, "fin_eventos", "requiere_documento", "BOOLEAN NOT NULL DEFAULT FALSE")
    _add_column_if_missing(conn, "fin_eventos", "factura_tipo_doc", "TEXT")
    _add_column_if_missing(conn, "fin_eventos", "factura_num", "TEXT")
    _add_column_if_missing(conn, "fin_eventos", "factura_fecha", "DATE")
    _add_column_if_missing(conn, "fin_eventos", "factura_rut", "TEXT")
    _add_column_if_missing(conn, "fin_eventos", "factura_razon_social", "TEXT")
    _add_column_if_missing(conn, "fin_eventos", "factura_direccion", "TEXT")
    _add_column_if_missing(conn, "fin_eventos", "factura_giro", "TEXT")
    _add_column_if_missing(conn, "fin_eventos", "factura_oc", "TEXT")
    _add_column_if_missing(conn, "fin_eventos", "factura_fecha_oc", "DATE")
    _add_column_if_missing(conn, "fin_eventos", "factura_hes", "TEXT")
    _add_column_if_missing(conn, "fin_eventos", "factura_fecha_hes", "DATE")
    _add_column_if_missing(conn, "fin_eventos", "factura_glosa", "TEXT")
    # 1 evento financiero por lead (si existe id_lead). Permite upsert estable desde "sync confirmados".
    try:
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_fin_eventos_id_lead ON fin_eventos(id_lead) WHERE id_lead IS NOT NULL"))
    except Exception:
        # no bloquear import si el motor no soporta (o permisos restringidos)
        pass
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
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
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
    _add_column_if_missing(conn, "fin_gastos", "is_active", "BOOLEAN NOT NULL DEFAULT TRUE")
    # indexes (no-op si ya existen)
    try:
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_fin_gastos_fecha ON fin_gastos(fecha)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_fin_gastos_pagado ON fin_gastos(pagado)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_fin_gastos_cuenta ON fin_gastos(cuenta_code)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_fin_gastos_cc ON fin_gastos(centro_costo)"))
    except Exception:
        pass

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
    if role not in ("ADMIN", "SUPERADMIN", "FINANZAS", "CONTROL DE GESTION"):
        raise HTTPException(status_code=403, detail="No autorizado")


def _ensure_roles(me, allowed):
    role = (me.get("role") or me.get("rol") or "").upper()
    # FINANZAS tiene acceso completo al módulo finanzas.
    if role in ("FINANZAS", "CONTROL DE GESTION"):
        return role
    if role not in allowed:
        raise HTTPException(status_code=403, detail="No autorizado")
    return role


FIN_EVENT_ROLES = {"ADMIN", "SUPERADMIN", "COMPRAS", "JEFE DE OPERACIONES", "EJECUTIVO", "EJECUTIVO DE VENTAS", "VENTAS"}


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
    ejecutivo: Optional[str] = None
    requiere_documento: Optional[bool] = None


class FacturacionEventoIn(BaseModel):
    requiere_documento: Optional[bool] = None
    factura_tipo_doc: Optional[str] = None
    factura_num: Optional[str] = None
    factura_fecha: Optional[date] = None
    factura_rut: Optional[str] = None
    factura_razon_social: Optional[str] = None
    factura_direccion: Optional[str] = None
    factura_giro: Optional[str] = None
    factura_oc: Optional[str] = None
    factura_fecha_oc: Optional[date] = None
    factura_hes: Optional[str] = None
    factura_fecha_hes: Optional[date] = None
    factura_glosa: Optional[str] = None


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


class GastoUpdate(BaseModel):
    fecha: Optional[date] = None
    cuenta_code: Optional[str] = None
    monto: Optional[float] = None
    descripcion: Optional[str] = None
    proveedor: Optional[str] = None
    marca: Optional[str] = None
    centro_costo: Optional[str] = None
    tipo_doc: Optional[str] = None
    doc_num: Optional[str] = None
    pagado: Optional[bool] = None
    fecha_vencimiento: Optional[date] = None
    fecha_pago: Optional[date] = None
    is_active: Optional[bool] = None


def _validate_centro_costo(conn, centro_costo: Optional[str]) -> Optional[str]:
    cc = _norm_text(centro_costo, max_len=80, upper=True)
    if not cc:
        return None
    ok = conn.execute(
        text(
            """
            SELECT 1
            FROM fin_centros_costo
            WHERE upper(nombre)=:n AND is_active IS TRUE
            LIMIT 1
            """
        ),
        {"n": cc},
    ).scalar()
    if not ok:
        raise HTTPException(status_code=400, detail="centro_costo inválido/inactivo")
    return cc


def _validate_marca(conn, marca: Optional[str]) -> Optional[str]:
    m = _norm_text(marca, max_len=80, upper=True)
    if not m:
        return None
    ok = conn.execute(
        text("SELECT 1 FROM marcas WHERE upper(COALESCE(nombre, marca))=:m LIMIT 1"),
        {"m": m},
    ).scalar()
    if not ok:
        raise HTTPException(status_code=400, detail="marca inválida")
    return m


def _validate_cuenta_code(conn, cuenta_code: Optional[str]) -> str:
    cuenta = _norm_text(cuenta_code, max_len=32, upper=False)
    if not cuenta:
        raise HTTPException(status_code=400, detail="cuenta_code requerido")
    cinfo = conn.execute(
        text("SELECT type, is_active FROM plan_cuentas WHERE code=:c LIMIT 1"),
        {"c": cuenta},
    ).mappings().first()
    if not cinfo:
        raise HTTPException(status_code=400, detail="cuenta_code no existe")
    if str(cinfo.get("type") or "").strip().lower() != "expense":
        raise HTTPException(status_code=400, detail="cuenta_code no es gasto (expense)")
    if cinfo.get("is_active") is False:
        raise HTTPException(status_code=400, detail="cuenta_code inactiva")
    return cuenta


def _normalize_pago(*, pagado: bool, fecha_pago: Optional[date]) -> tuple[bool, Optional[date]]:
    if pagado and not fecha_pago:
        return True, date.today()
    if (not pagado) and fecha_pago:
        return False, None
    return pagado, fecha_pago


class PagoIn(BaseModel):
    monto: float
    metodo: Optional[str] = None
    referencia: Optional[str] = None
    doc_num: Optional[str] = None
    fecha: Optional[date] = None


def _estado_confirm_id(conn) -> int | None:
    """
    Busca el id_estado para CONFIRMADO. Preferimos match por nombre.
    """
    try:
        row = conn.execute(
            text(
                """
                SELECT id_estado
                FROM public.estados_lead
                WHERE upper(nombre) LIKE 'CONFIRM%'
                ORDER BY id_estado
                LIMIT 1
                """
            )
        ).first()
        if row and row[0] is not None:
            return int(row[0])
    except Exception:
        return None
    return None


@router.get("/plan-cuentas")
def list_plan_cuentas(
    q: str = Query("", max_length=120),
    me=Depends(get_current_user),
):
    role = _ensure_roles(me, {"ADMIN", "SUPERADMIN", "COMPRAS", "JEFE DE OPERACIONES"})
    with get_connection() as conn:
        _ensure_tables(conn)
        _ensure_brand_accounts(conn)
        # COMPRAS / OPS: solo lectura de cuentas de gasto activas (no exponer todo el plan)
        restrict_to_expense = role not in ("ADMIN", "SUPERADMIN", "FINANZAS")
        if q:
            base_where = "WHERE (code ILIKE :q OR name ILIKE :q OR COALESCE(description,'') ILIKE :q)"
            if restrict_to_expense:
                base_where += " AND type='expense' AND COALESCE(is_active,TRUE) IS TRUE"
            rows = conn.execute(
                text(
                    """
                    SELECT code, name, type, classification, description,
                           example_transactions, who_inputs, how_to_impute,
                           parent_code, is_active
                    FROM plan_cuentas
                    """
                    + base_where
                    + """
                    ORDER BY
                      CASE
                        WHEN code ~ '^[0-9]+(\\.[0-9]+)*$'
                        THEN string_to_array(code, '.')::int[]
                      END NULLS LAST,
                      code ASC
                    """
                ),
                {"q": f"%{q}%"},
            ).mappings().all()
        else:
            base_where = ""
            if restrict_to_expense:
                base_where = "WHERE type='expense' AND COALESCE(is_active,TRUE) IS TRUE"
            rows = conn.execute(
                text(
                    """
                    SELECT code, name, type, classification, description,
                           example_transactions, who_inputs, how_to_impute,
                           parent_code, is_active
                    FROM plan_cuentas
                    """
                    + base_where
                    + """
                    ORDER BY
                      CASE
                        WHEN code ~ '^[0-9]+(\\.[0-9]+)*$'
                        THEN string_to_array(code, '.')::int[]
                      END NULLS LAST,
                      code ASC
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
    _ensure_roles(me, FIN_EVENT_ROLES)
    with get_connection() as conn:
        _ensure_tables(conn)
        payload = body.dict()
        if payload.get("requiere_documento") is None:
            payload["requiere_documento"] = bool("EMP" in str(payload.get("tipo_cliente") or "").upper() or "FACT" in str(payload.get("tipo_cliente") or "").upper())

        # Upsert por id_lead (si ya está registrado, actualiza el último evento del lead).
        id_evento = None
        if payload.get("id_lead"):
            row = conn.execute(
                text(
                    """
                    SELECT id_evento
                    FROM fin_eventos
                    WHERE id_lead=:id
                    ORDER BY id_evento DESC
                    LIMIT 1
                    """
                ),
                {"id": int(payload["id_lead"])},
            ).mappings().first()
            if row:
                id_evento = int(row["id_evento"])

        if id_evento:
            conn.execute(
                text(
                    """
                    UPDATE fin_eventos
                    SET num_cotizacion=:num_cotizacion,
                        id_cotizacion=:id_cotizacion,
                        cliente=:cliente,
                        comuna=:comuna,
                        marca=:marca,
                        tipo_cliente=:tipo_cliente,
                        fecha_evento=:fecha_evento,
                        monto_bruto=:monto_bruto,
                        monto_neto=:monto_neto,
                        iva=:iva,
                        traslado=:traslado,
                        comision_pct=:comision_pct,
                        comision_monto=:comision_monto,
                        ejecutivo=:ejecutivo,
                        requiere_documento=:requiere_documento,
                        updated_at=now()
                    WHERE id_evento=:id_evento
                    """
                ),
                {**payload, "id_evento": id_evento},
            )
        else:
            # Canon: abono/saldo se derivan de fin_pagos.
            abono_inicial = float(payload.get("abono") or 0)
            payload["abono"] = 0
            payload["saldo"] = float(payload.get("monto_bruto") or 0)
            row = conn.execute(
                text(
                    """
                    INSERT INTO fin_eventos
                    (id_lead, num_cotizacion, id_cotizacion, cliente, comuna, marca, tipo_cliente, fecha_evento, monto_bruto, monto_neto, iva,
                     traslado, abono, saldo, comision_pct, comision_monto, ejecutivo, requiere_documento, updated_at)
                    VALUES
                    (:id_lead, :num_cotizacion, :id_cotizacion, :cliente, :comuna, :marca, :tipo_cliente, :fecha_evento, :monto_bruto, :monto_neto, :iva,
                     :traslado, :abono, :saldo, :comision_pct, :comision_monto, :ejecutivo, :requiere_documento, now())
                    RETURNING id_evento
                    """
                ),
                payload,
            ).fetchone()
            id_evento = int(row[0]) if row else None

            # Si el UI mandó "abono" al registrar el evento, lo guardamos como pago inicial.
            if id_evento and abono_inicial and abono_inicial > 0:
                conn.execute(
                    text(
                        """
                        INSERT INTO fin_pagos(id_evento, fecha, monto, metodo, referencia, doc_num)
                        VALUES (:id, now(), :m, 'ABONO INICIAL', 'Registro evento', NULL)
                        """
                    ),
                    {"id": id_evento, "m": abono_inicial},
                )

        # Recalcular abono/saldo desde pagos (para que el saldo sea real siempre).
        if id_evento:
            # Backward-compat: si había `abono` legacy en fin_eventos pero aún no existen pagos,
            # lo migramos 1 vez a fin_pagos para que el saldo no se “reseteé” a 0.
            try:
                n_pagos = conn.execute(
                    text("SELECT COUNT(*) FROM fin_pagos WHERE id_evento=:id"),
                    {"id": id_evento},
                ).scalar()
                if int(n_pagos or 0) == 0:
                    legacy = conn.execute(
                        text("SELECT COALESCE(abono,0) AS abono FROM fin_eventos WHERE id_evento=:id"),
                        {"id": id_evento},
                    ).mappings().first()
                    legacy_ab = float((legacy or {}).get("abono") or 0)
                    if legacy_ab > 0:
                        conn.execute(
                            text(
                                """
                                INSERT INTO fin_pagos(id_evento, fecha, monto, metodo, referencia, doc_num)
                                VALUES (:id, now(), :m, 'MIGRADO', 'Migración abono legacy', NULL)
                                """
                            ),
                            {"id": id_evento, "m": legacy_ab},
                        )
            except Exception:
                # no bloquear creación por migración
                pass
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
    return {"ok": True, "id_evento": id_evento}


@router.get("/eventos/by_lead")
def get_evento_by_lead(
    id_lead: int = Query(..., ge=1),
    me=Depends(get_current_user),
):
    _ensure_roles(me, FIN_EVENT_ROLES)
    with get_connection() as conn:
        _ensure_tables(conn)
        row = conn.execute(
            text(
                """
                SELECT id_evento, id_lead, num_cotizacion, id_cotizacion, cliente, comuna, marca, tipo_cliente, fecha_evento,
                       monto_bruto, monto_neto, iva, traslado, abono, saldo, comision_pct, comision_monto,
                       abono_mode, abono_ref, abono_due_date, ejecutivo, requiere_documento,
                       factura_tipo_doc, factura_num, factura_fecha, factura_rut, factura_razon_social, factura_direccion,
                       factura_giro, factura_oc, factura_fecha_oc, factura_hes, factura_fecha_hes, factura_glosa,
                       created_at, updated_at
                FROM fin_eventos
                WHERE id_lead=:id
                ORDER BY id_evento DESC
                LIMIT 1
                """
            ),
            {"id": int(id_lead)},
        ).mappings().first()
    return {"ok": True, "item": _row_json(dict(row)) if row else None}


@router.get("/eventos")
def list_eventos(
    month: int = Query(0, ge=0, le=12),
    year: int = Query(0, ge=0, le=2100),
    all: bool = Query(False, alias="all"),
    only_pending: bool = Query(False),
    me=Depends(get_current_user),
):
    _ensure_roles(me, FIN_EVENT_ROLES)
    with get_connection() as conn:
        _ensure_tables(conn)
        where = ""
        params = {}
        if not all and not (month and year):
            try:
                today = date.today()
                month = int(today.month)
                year = int(today.year)
            except Exception:
                month = 0
                year = 0
        if month and year:
            where = "WHERE EXTRACT(MONTH FROM fecha_evento)=:m AND EXTRACT(YEAR FROM fecha_evento)=:y"
            params = {"m": month, "y": year}
        if only_pending:
            where = (where + " AND " if where else "WHERE ") + "COALESCE(saldo,0) > 0"
        rows = conn.execute(
            text(
                f"""
                SELECT id_evento, id_lead, num_cotizacion, id_cotizacion, cliente, comuna, marca, tipo_cliente, fecha_evento,
                       monto_bruto, monto_neto, iva, traslado, abono, saldo, comision_pct, comision_monto,
                       abono_mode, abono_ref, abono_due_date, ejecutivo, requiere_documento,
                       factura_tipo_doc, factura_num, factura_fecha, factura_rut, factura_razon_social, factura_direccion,
                       factura_giro, factura_oc, factura_fecha_oc, factura_hes, factura_fecha_hes, factura_glosa,
                       (SELECT COUNT(*) FROM fin_pagos p WHERE p.id_evento=fin_eventos.id_evento) AS pago_count,
                       (SELECT p.monto FROM fin_pagos p WHERE p.id_evento=fin_eventos.id_evento ORDER BY p.fecha ASC, p.id_pago ASC LIMIT 1) AS abono_1,
                       (SELECT p.fecha FROM fin_pagos p WHERE p.id_evento=fin_eventos.id_evento ORDER BY p.fecha DESC, p.id_pago DESC LIMIT 1) AS ultimo_pago_fecha,
                       CASE
                         WHEN COALESCE(abono_mode,'')<>'' THEN abono_mode
                         WHEN COALESCE(saldo,0)<=0 THEN 'pagado'
                         WHEN COALESCE(abono,0)>0 THEN 'abono'
                         ELSE ''
                       END AS condicion_pago,
                       created_at
                FROM fin_eventos
                {where}
                ORDER BY fecha_evento DESC NULLS LAST, id_evento DESC
                LIMIT 500
                """
            ),
            params,
        ).mappings().all()
    return {"ok": True, "items": [_row_json(dict(r)) for r in rows]}


@router.get("/eventos/resumen")
def eventos_resumen(
    month: int = Query(0, ge=0, le=12),
    year: int = Query(0, ge=0, le=2100),
    me=Depends(get_current_user),
):
    _ensure_roles(me, FIN_EVENT_ROLES)
    with get_connection() as conn:
        _ensure_tables(conn)
        if not (month and year):
            today = date.today()
            month, year = today.month, today.year
        params = {"m": int(month), "y": int(year)}
        where = "WHERE EXTRACT(MONTH FROM fecha_evento)=:m AND EXTRACT(YEAR FROM fecha_evento)=:y"
        total = conn.execute(
            text(
                f"""
                SELECT COUNT(*) AS n,
                       SUM(COALESCE(monto_bruto,0)) AS bruto,
                       SUM(COALESCE(monto_neto,0)) AS neto,
                       SUM(COALESCE(iva,0)) AS iva,
                       SUM(COALESCE(traslado,0)) AS traslado,
                       SUM(COALESCE(abono,0)) AS abono,
                       SUM(COALESCE(saldo,0)) AS saldo
                FROM fin_eventos
                {where}
                """
            ),
            params,
        ).mappings().first()
        by_marca = conn.execute(
            text(
                f"""
                SELECT COALESCE(NULLIF(btrim(marca),''),'(SIN MARCA)') AS label,
                       COUNT(*) AS n,
                       SUM(COALESCE(monto_bruto,0)) AS bruto,
                       SUM(COALESCE(monto_neto,0)) AS neto,
                       SUM(COALESCE(iva,0)) AS iva,
                       SUM(COALESCE(traslado,0)) AS traslado,
                       SUM(COALESCE(abono,0)) AS abono,
                       SUM(COALESCE(saldo,0)) AS saldo
                FROM fin_eventos
                {where}
                GROUP BY 1
                ORDER BY bruto DESC NULLS LAST, label ASC
                """
            ),
            params,
        ).mappings().all()
        by_ejecutivo = conn.execute(
            text(
                f"""
                SELECT COALESCE(NULLIF(btrim(ejecutivo),''),'(SIN EJECUTIVO)') AS label,
                       COUNT(*) AS n,
                       SUM(COALESCE(monto_bruto,0)) AS bruto,
                       SUM(COALESCE(monto_neto,0)) AS neto,
                       SUM(COALESCE(iva,0)) AS iva,
                       SUM(COALESCE(traslado,0)) AS traslado,
                       SUM(COALESCE(abono,0)) AS abono,
                       SUM(COALESCE(saldo,0)) AS saldo
                FROM fin_eventos
                {where}
                GROUP BY 1
                ORDER BY bruto DESC NULLS LAST, label ASC
                """
            ),
            params,
        ).mappings().all()
    return {
        "ok": True,
        "month": int(month),
        "year": int(year),
        "total": _row_json(dict(total or {})),
        "by_marca": [_row_json(dict(r)) for r in by_marca],
        "by_ejecutivo": [_row_json(dict(r)) for r in by_ejecutivo],
    }


@router.get("/eventos/pendientes_anteriores")
def eventos_pendientes_anteriores(
    month: int = Query(0, ge=0, le=12),
    year: int = Query(0, ge=0, le=2100),
    me=Depends(get_current_user),
):
    _ensure_roles(me, FIN_EVENT_ROLES)
    with get_connection() as conn:
        _ensure_tables(conn)
        if not (month and year):
            today = date.today()
            month, year = today.month, today.year
        params = {"m": int(month), "y": int(year)}
        rows = conn.execute(
            text(
                """
                SELECT to_char(date_trunc('month', fecha_evento), 'YYYY-MM') AS mes,
                       COUNT(*) AS n,
                       SUM(COALESCE(monto_bruto,0)) AS bruto,
                       SUM(COALESCE(abono,0)) AS abono,
                       SUM(COALESCE(saldo,0)) AS saldo
                FROM fin_eventos
                WHERE COALESCE(saldo,0) > 0
                  AND fecha_evento < make_date(:y, :m, 1)
                GROUP BY 1
                ORDER BY mes DESC
                """
            ),
            params,
        ).mappings().all()
    return {"ok": True, "items": [_row_json(dict(r)) for r in rows]}


@router.get("/eventos/facturacion")
def eventos_facturacion(
    month: int = Query(0, ge=0, le=12),
    year: int = Query(0, ge=0, le=2100),
    me=Depends(get_current_user),
):
    _ensure_roles(me, FIN_EVENT_ROLES)
    with get_connection() as conn:
        _ensure_tables(conn)
        if not (month and year):
            today = date.today()
            month, year = today.month, today.year
        rows = conn.execute(
            text(
                """
                SELECT
                    id_evento,
                    id_lead,
                    num_cotizacion,
                    id_cotizacion,
                    fecha_evento,
                    marca,
                    cliente,
                    comuna,
                    ejecutivo,
                    monto_neto,
                    iva,
                    monto_bruto,
                    traslado,
                    abono,
                    saldo,
                    tipo_cliente,
                    requiere_documento,
                    factura_rut,
                    factura_razon_social,
                    factura_oc,
                    factura_fecha_oc,
                    factura_hes,
                    factura_fecha_hes,
                    factura_tipo_doc,
                    factura_fecha,
                    factura_num,
                    factura_direccion,
                    factura_giro,
                    factura_glosa,
                    created_at,
                    updated_at
                FROM fin_eventos
                WHERE EXTRACT(MONTH FROM fecha_evento)=:m
                  AND EXTRACT(YEAR FROM fecha_evento)=:y
                  AND (
                    COALESCE(requiere_documento,FALSE) IS TRUE
                    OR COALESCE(tipo_cliente,'') ILIKE '%EMP%'
                    OR COALESCE(tipo_cliente,'') ILIKE '%FACT%'
                    OR COALESCE(factura_tipo_doc,'') <> ''
                    OR COALESCE(factura_num,'') <> ''
                  )
                ORDER BY fecha_evento ASC NULLS LAST, marca ASC NULLS LAST, cliente ASC NULLS LAST, id_evento ASC
                """
            ),
            {"m": int(month), "y": int(year)},
        ).mappings().all()
    return {"ok": True, "month": int(month), "year": int(year), "items": [_row_json(dict(r)) for r in rows]}


@router.put("/eventos/{id_evento}/facturacion")
def update_facturacion_evento(
    id_evento: int,
    body: FacturacionEventoIn,
    me=Depends(get_current_user),
):
    _ensure_roles(me, FIN_EVENT_ROLES)
    payload = {
        "id": int(id_evento),
        "requiere_documento": bool(body.requiere_documento) if body.requiere_documento is not None else None,
        "factura_tipo_doc": _norm_text(body.factura_tipo_doc, max_len=40, upper=True),
        "factura_num": _norm_text(body.factura_num, max_len=80),
        "factura_fecha": body.factura_fecha,
        "factura_rut": _norm_text(body.factura_rut, max_len=40, upper=True),
        "factura_razon_social": _norm_text(body.factura_razon_social, max_len=200, upper=True),
        "factura_direccion": _norm_text(body.factura_direccion, max_len=250),
        "factura_giro": _norm_text(body.factura_giro, max_len=250, upper=True),
        "factura_oc": _norm_text(body.factura_oc, max_len=120, upper=True),
        "factura_fecha_oc": body.factura_fecha_oc,
        "factura_hes": _norm_text(body.factura_hes, max_len=120, upper=True),
        "factura_fecha_hes": body.factura_fecha_hes,
        "factura_glosa": _norm_text(body.factura_glosa, max_len=500),
    }
    with get_connection() as conn:
        _ensure_tables(conn)
        current = conn.execute(
            text("SELECT id_evento, requiere_documento FROM fin_eventos WHERE id_evento=:id LIMIT 1"),
            {"id": int(id_evento)},
        ).mappings().first()
        if not current:
            raise HTTPException(status_code=404, detail="Evento no encontrado")
        if payload["requiere_documento"] is None:
            payload["requiere_documento"] = bool(current.get("requiere_documento"))

        if payload["requiere_documento"]:
            missing = []
            if not payload["factura_tipo_doc"]:
                missing.append("tipo documento")
            if not payload["factura_rut"]:
                missing.append("RUT")
            if not payload["factura_razon_social"]:
                missing.append("razón social")
            if not payload["factura_direccion"]:
                missing.append("dirección")
            if not payload["factura_giro"]:
                missing.append("giro")
            if missing:
                raise HTTPException(status_code=400, detail="Faltan datos de facturación: " + ", ".join(missing))

        row = conn.execute(
            text(
                """
                UPDATE fin_eventos
                SET requiere_documento=:requiere_documento,
                    factura_tipo_doc=:factura_tipo_doc,
                    factura_num=:factura_num,
                    factura_fecha=:factura_fecha,
                    factura_rut=:factura_rut,
                    factura_razon_social=:factura_razon_social,
                    factura_direccion=:factura_direccion,
                    factura_giro=:factura_giro,
                    factura_oc=:factura_oc,
                    factura_fecha_oc=:factura_fecha_oc,
                    factura_hes=:factura_hes,
                    factura_fecha_hes=:factura_fecha_hes,
                    factura_glosa=:factura_glosa,
                    updated_at=now()
                WHERE id_evento=:id
                RETURNING id_evento, id_lead, num_cotizacion, fecha_evento, marca, cliente, comuna, ejecutivo,
                          monto_neto, iva, monto_bruto, traslado, abono, saldo, tipo_cliente, requiere_documento,
                          factura_rut, factura_razon_social, factura_oc, factura_fecha_oc, factura_hes, factura_fecha_hes,
                          factura_tipo_doc, factura_fecha, factura_num, factura_direccion, factura_giro, factura_glosa,
                          created_at, updated_at
                """
            ),
            payload,
        ).mappings().first()
        conn.commit()
    return {"ok": True, "item": _row_json(dict(row or {}))}


@router.post("/eventos/sync_confirmados")
def sync_confirmados_a_fin_eventos(
    month: int = Query(..., ge=1, le=12),
    year: int = Query(..., ge=2000, le=2100),
    me=Depends(get_current_user),
):
    """
    Sincroniza (idempotente) leads CONFIRMADOS del mes a fin_eventos.
    Fuente de venta: leads.monto_cotizado (monto_bruto).
    NO toca abonos/saldo existentes (si el evento ya fue gestionado en finanzas).
    """
    _ensure_roles(me, FIN_EVENT_ROLES)
    with get_connection() as conn:
        _ensure_tables(conn)
        conf_id = _estado_confirm_id(conn)
        if not conf_id:
            raise HTTPException(status_code=500, detail="No encontré estado CONFIRMADO en estados_lead")

        # Detectar columnas opcionales en leads (muy defensivo).
        has_num_cot = _has_column(conn, "leads", "num_cotizacion")
        has_id_cot = _has_column(conn, "leads", "id_cotizacion")
        has_id_cot_vig = _has_column(conn, "leads", "id_cotizacion_vigente")
        has_nombre_cliente = _has_column(conn, "leads", "nombre_cliente")
        has_cliente = _has_column(conn, "leads", "cliente")
        has_nombre = _has_column(conn, "leads", "nombre")
        has_id_comuna = _has_column(conn, "leads", "id_comuna")
        has_id_marca = _has_column(conn, "leads", "id_marca")
        has_tipo_cli = _has_column(conn, "leads", "tipo_cliente")
        has_id_tipo_cli = _has_column(conn, "leads", "id_tipo_cliente")
        has_tipos_cliente = bool(conn.execute(text("SELECT to_regclass('public.tipos_cliente')")).scalar())
        has_tipos_cliente_id = has_tipos_cliente and _has_column(conn, "tipos_cliente", "id_tipo_cliente")
        has_tipos_cliente_nombre = has_tipos_cliente and _has_column(conn, "tipos_cliente", "nombre")
        has_tipos_cliente_tipo = has_tipos_cliente and _has_column(conn, "tipos_cliente", "tipo")
        has_com_pct = _has_column(conn, "leads", "comision_pct")
        has_monto_cotizado = _has_column(conn, "leads", "monto_cotizado")
        has_lead_user = _has_column(conn, "leads", "id_usuario")
        has_usuarios = bool(conn.execute(text("SELECT to_regclass('public.usuarios')")).scalar())
        has_usuarios_id = has_usuarios and _has_column(conn, "usuarios", "id_usuario")
        has_usuario_nombre = has_usuarios and _has_column(conn, "usuarios", "nombre")
        has_usuario_display = has_usuarios and _has_column(conn, "usuarios", "display")
        has_usuario_username = has_usuarios and _has_column(conn, "usuarios", "username")
        has_usuario_email = has_usuarios and _has_column(conn, "usuarios", "email")
        has_cot = bool(conn.execute(text("SELECT to_regclass('public.cotizaciones')")).scalar())
        has_marcas = bool(conn.execute(text("SELECT to_regclass('public.marcas')")).scalar())
        has_comunas = bool(conn.execute(text("SELECT to_regclass('public.comunas')")).scalar())
        has_marca_nombre = has_marcas and _has_column(conn, "marcas", "nombre")
        has_marca_marca = has_marcas and _has_column(conn, "marcas", "marca")
        has_comuna_nombre = has_comunas and _has_column(conn, "comunas", "nombre")
        has_cot_id = has_cot and _has_column(conn, "cotizaciones", "id_cotizacion")
        has_cot = bool(has_cot and has_cot_id)
        has_cot_numero = has_cot and _has_column(conn, "cotizaciones", "numero")
        has_cot_id_lead = has_cot and _has_column(conn, "cotizaciones", "id_lead")
        has_cot_created_at = has_cot and _has_column(conn, "cotizaciones", "created_at")
        has_cot_subtotal_prod = has_cot and _has_column(conn, "cotizaciones", "subtotal_productos")
        has_cot_subtotal = has_cot and _has_column(conn, "cotizaciones", "subtotal")
        has_cot_traslado = has_cot and _has_column(conn, "cotizaciones", "traslado")
        has_cot_iva = has_cot and _has_column(conn, "cotizaciones", "iva")
        has_cot_total = has_cot and _has_column(conn, "cotizaciones", "total")
        has_cot_tipo = has_cot and _has_column(conn, "cotizaciones", "tipo_cliente")

        # Cliente: construir solo con columnas existentes. Si SQL referencia una columna inexistente,
        # PostgreSQL falla al parsear todo el sync.
        name_parts = []
        if has_nombre_cliente:
            name_parts.append("NULLIF(btrim(l.nombre_cliente::text),'')")
        if has_cliente:
            name_parts.append("NULLIF(btrim(l.cliente::text),'')")
        if has_nombre:
            name_parts.append("NULLIF(btrim(l.nombre::text),'')")
        name_expr = "COALESCE(" + ", ".join(name_parts + ["'—'"]) + ")"
        join_comuna = "LEFT JOIN public.comunas c ON c.id_comuna=l.id_comuna" if (has_id_comuna and has_comunas) else ""
        comuna_expr = "COALESCE(NULLIF(btrim(c.nombre),''), '—')" if (has_id_comuna and has_comuna_nombre) else "'—'"
        join_marca = "LEFT JOIN public.marcas m ON m.id_marca=l.id_marca" if (has_id_marca and has_marcas) else ""
        marca_parts = []
        if has_id_marca and has_marca_nombre:
            marca_parts.append("NULLIF(btrim(m.nombre::text),'')")
        if has_id_marca and has_marca_marca:
            marca_parts.append("NULLIF(btrim(m.marca::text),'')")
        marca_expr = "COALESCE(" + ", ".join(marca_parts + ["'—'"]) + ")" if marca_parts else "'—'"

        tipo_expr = "'—'"
        join_tipo = ""
        # Tabla: public.tipos_cliente (ojo: plural). En algunos despliegues el join estaba mal escrito y rompe el sync.
        if has_id_tipo_cli and has_tipos_cliente_id:
            join_tipo = "LEFT JOIN public.tipos_cliente tc ON tc.id_tipo_cliente=l.id_tipo_cliente"
            tipo_parts = []
            if has_tipos_cliente_nombre:
                tipo_parts.append("NULLIF(btrim(tc.nombre::text),'')")
            if has_tipos_cliente_tipo:
                tipo_parts.append("NULLIF(btrim(tc.tipo::text),'')")
            tipo_expr = "COALESCE(" + ", ".join(tipo_parts + ["'—'"]) + ")"
        elif has_tipo_cli:
            tipo_expr = "COALESCE(NULLIF(btrim(l.tipo_cliente),''), '—')"

        join_user = ""
        ejecutivo_expr = "'—'"
        if has_lead_user and has_usuarios and has_usuarios_id:
            join_user = "LEFT JOIN public.usuarios u ON u.id_usuario::text = l.id_usuario::text"
            ejecutivo_parts = []
            if has_usuario_nombre:
                ejecutivo_parts.append("NULLIF(btrim(u.nombre::text),'')")
            if has_usuario_display:
                ejecutivo_parts.append("NULLIF(btrim(u.display::text),'')")
            if has_usuario_username:
                ejecutivo_parts.append("NULLIF(btrim(u.username::text),'')")
            if has_usuario_email:
                ejecutivo_parts.append("NULLIF(btrim(u.email::text),'')")
            ejecutivo_expr = "COALESCE(" + ", ".join(ejecutivo_parts + ["'—'"]) + ")"

        num_cot_expr = "NULL"
        if has_num_cot:
            num_cot_expr = "NULLIF(btrim(l.num_cotizacion::text),'')"
        id_cot_expr = "NULL"
        if has_id_cot:
            id_cot_expr = "l.id_cotizacion"
        if has_id_cot_vig:
            id_cot_expr = "l.id_cotizacion_vigente"

        com_pct_expr = "0"
        if has_com_pct:
            com_pct_expr = "COALESCE(l.comision_pct,0)"

        # Upsert DEFENSIVO (sin depender de UNIQUE INDEX).
        # Canon:
        # - monto_neto = productos + traslado (SIN IVA)
        # - monto_bruto = monto_neto + IVA (si aplica EMPRESA)
        # - saldo = monto_bruto - abono
        join_cot = ""
        quote_exists_expr = "FALSE"
        if has_cot and has_cot_id:
            cot_where = []
            cot_order = []
            if id_cot_expr != "NULL":
                cot_where.append(f"q0.id_cotizacion = {id_cot_expr}")
                cot_order.append(f"CASE WHEN q0.id_cotizacion = {id_cot_expr} THEN 0 ELSE 1 END")
            if has_cot_id_lead:
                cot_where.append("q0.id_lead = l.id_lead")
                cot_order.append("CASE WHEN q0.id_lead = l.id_lead THEN 1 ELSE 2 END")
            if has_cot_numero and num_cot_expr != "NULL":
                cot_where.append(f"NULLIF(btrim(q0.numero::text),'') = {num_cot_expr}")
                cot_order.append(f"CASE WHEN NULLIF(btrim(q0.numero::text),'') = {num_cot_expr} THEN 1 ELSE 2 END")
            if cot_where:
                order_sql = ", ".join(cot_order + (["q0.created_at DESC NULLS LAST"] if has_cot_created_at else []) + ["q0.id_cotizacion DESC"])
                join_cot = f"""
              LEFT JOIN LATERAL (
                SELECT q0.*
                FROM public.cotizaciones q0
                WHERE {" OR ".join(cot_where)}
                ORDER BY {order_sql}
                LIMIT 1
              ) q ON TRUE
                """
                quote_exists_expr = "q.id_cotizacion IS NOT NULL"
        if not join_cot:
            has_cot = False
            has_cot_numero = False
            has_cot_subtotal_prod = False
            has_cot_subtotal = False
            has_cot_traslado = False
            has_cot_iva = False
            has_cot_total = False
            has_cot_tipo = False
        if has_cot_subtotal_prod and has_cot_subtotal:
            prod_expr = "COALESCE(q.subtotal_productos::numeric, q.subtotal::numeric, 0::numeric)"
        elif has_cot_subtotal_prod:
            prod_expr = "COALESCE(q.subtotal_productos::numeric, 0::numeric)"
        elif has_cot_subtotal:
            prod_expr = "COALESCE(q.subtotal::numeric, 0::numeric)"
        else:
            prod_expr = "0::numeric"
        traslado_q_expr = "COALESCE(q.traslado::numeric, 0::numeric)" if has_cot_traslado else "0::numeric"
        tipo_expr2 = "COALESCE(NULLIF(btrim(UPPER(q.tipo_cliente)),''), %s)" % tipo_expr if has_cot_tipo else tipo_expr
        is_emp_expr = "(%s ILIKE '%%EMP%%' OR %s ILIKE '%%FACT%%')" % (tipo_expr2, tipo_expr2)
        lead_monto_expr = "COALESCE(l.monto_cotizado::numeric,0::numeric)" if has_monto_cotizado else "0::numeric"
        quote_neto_expr = f"({prod_expr} + {traslado_q_expr})"
        if has_cot_iva:
            quote_iva_expr = f"CASE WHEN {is_emp_expr} THEN COALESCE(q.iva::numeric, ROUND(({quote_neto_expr}) * 0.19, 2)) ELSE 0::numeric END"
        else:
            quote_iva_expr = f"CASE WHEN {is_emp_expr} THEN ROUND(({quote_neto_expr}) * 0.19, 2) ELSE 0::numeric END"
        fallback_neto_expr = f"CASE WHEN {is_emp_expr} THEN ROUND(({lead_monto_expr}) / 1.19, 2) ELSE ({lead_monto_expr}) END"
        fallback_iva_expr = f"CASE WHEN {is_emp_expr} THEN (({lead_monto_expr}) - ({fallback_neto_expr})) ELSE 0 END"
        neto_expr = f"(CASE WHEN {quote_exists_expr} THEN ({quote_neto_expr}) ELSE ({fallback_neto_expr}) END)::numeric(14,2)"
        iva_expr = f"(CASE WHEN {quote_exists_expr} THEN ({quote_iva_expr}) ELSE ({fallback_iva_expr}) END)::numeric(14,2)"
        traslado_expr = f"(CASE WHEN {quote_exists_expr} THEN ({traslado_q_expr}) ELSE 0 END)::numeric(14,2)"
        bruto_expr = f"(CASE WHEN {quote_exists_expr} THEN (({quote_neto_expr}) + ({quote_iva_expr})) ELSE ({lead_monto_expr}) END)::numeric(14,2)"
        numero_expr = "NULLIF(btrim(q.numero::text),'')" if has_cot_numero else "NULL"

        q = f"""
            WITH src AS (
              SELECT
                l.id_lead,
                COALESCE({numero_expr}, {num_cot_expr}) AS num_cotizacion,
                {id_cot_expr} AS id_cotizacion,
                {name_expr} AS cliente,
                {comuna_expr} AS comuna,
                {marca_expr} AS marca,
                {tipo_expr2} AS tipo_cliente,
                l.fecha_evento::date AS fecha_evento,
                {bruto_expr} AS monto_bruto,
                {neto_expr} AS monto_neto,
                {iva_expr} AS iva,
                {traslado_expr}::numeric(14,2) AS traslado,
                {com_pct_expr}::numeric(6,2) AS comision_pct,
                (({neto_expr}) * COALESCE({com_pct_expr},0) / 100.0)::numeric(14,2) AS comision_monto,
                ({bruto_expr})::numeric(14,2) AS saldo_init,
                {ejecutivo_expr} AS ejecutivo,
                CASE WHEN {is_emp_expr} THEN TRUE ELSE FALSE END AS requiere_documento
              FROM public.leads l
              {join_comuna}
              {join_marca}
              {join_cot}
              {join_tipo}
              {join_user}
              WHERE l.id_estado=:conf
                AND l.fecha_evento IS NOT NULL
                AND EXTRACT(MONTH FROM l.fecha_evento)=:m
                AND EXTRACT(YEAR FROM l.fecha_evento)=:y
            ),
            upd AS (
              UPDATE fin_eventos f
              SET num_cotizacion = s.num_cotizacion,
                  id_cotizacion  = s.id_cotizacion,
                  cliente        = s.cliente,
                  comuna         = s.comuna,
                  marca          = s.marca,
                  tipo_cliente   = s.tipo_cliente,
                  fecha_evento   = s.fecha_evento,
                  monto_bruto    = s.monto_bruto,
                  monto_neto     = s.monto_neto,
                  iva            = s.iva,
                  traslado       = s.traslado,
                  comision_pct   = s.comision_pct,
                  comision_monto = s.comision_monto,
                  saldo          = GREATEST(0, s.monto_bruto - COALESCE(f.abono,0)),
                  ejecutivo      = s.ejecutivo,
                  requiere_documento = s.requiere_documento,
                  updated_at     = now()
              FROM src s
              WHERE f.id_lead = s.id_lead
              RETURNING f.id_lead
            ),
            ins AS (
              INSERT INTO fin_eventos(
                id_lead,num_cotizacion,id_cotizacion,cliente,comuna,marca,tipo_cliente,fecha_evento,
                monto_bruto,monto_neto,iva,traslado,comision_pct,comision_monto,saldo,ejecutivo,requiere_documento,updated_at
              )
              SELECT
                s.id_lead,s.num_cotizacion,s.id_cotizacion,s.cliente,s.comuna,s.marca,s.tipo_cliente,s.fecha_evento,
                s.monto_bruto,s.monto_neto,s.iva,s.traslado,s.comision_pct,s.comision_monto,s.saldo_init,s.ejecutivo,s.requiere_documento,now()
              FROM src s
              WHERE NOT EXISTS (SELECT 1 FROM fin_eventos f WHERE f.id_lead = s.id_lead)
              RETURNING id_lead
            )
            SELECT (SELECT count(*) FROM upd) AS updated,
                   (SELECT count(*) FROM ins) AS created;
        """
        res = conn.execute(text(q), {"conf": conf_id, "m": int(month), "y": int(year)}).mappings().first()
        try:
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        created = int((res or {}).get("created") or 0)
        updated = int((res or {}).get("updated") or 0)
        return {"ok": True, "month": int(month), "year": int(year), "created": created, "updated": updated}


@router.post("/gastos")
def create_gasto(body: GastoIn, me=Depends(get_current_user)):
    _ensure_roles(me, {"ADMIN", "SUPERADMIN", "COMPRAS"})
    with get_connection() as conn:
        _ensure_tables(conn)
        cuenta = _validate_cuenta_code(conn, body.cuenta_code)

        monto = float(body.monto or 0)
        if not (monto > 0):
            raise HTTPException(status_code=400, detail="monto debe ser > 0")

        if body.fecha_vencimiento and body.fecha_vencimiento < body.fecha:
            raise HTTPException(status_code=400, detail="fecha_vencimiento no puede ser < fecha")

        payload = {
            "fecha": body.fecha,
            "cuenta_code": cuenta,
            "monto": monto,
            "descripcion": _norm_text(body.descripcion, max_len=300),
            "proveedor": _norm_text(body.proveedor, max_len=200),
            "marca": _validate_marca(conn, body.marca),
            "centro_costo": _validate_centro_costo(conn, body.centro_costo),
            "tipo_doc": _norm_text(body.tipo_doc, max_len=40, upper=True),
            "doc_num": _norm_text(body.doc_num, max_len=80),
            "pagado": bool(body.pagado or False),
            "fecha_vencimiento": body.fecha_vencimiento,
            "fecha_pago": body.fecha_pago,
        }
        payload["pagado"], payload["fecha_pago"] = _normalize_pago(
            pagado=payload["pagado"], fecha_pago=payload["fecha_pago"]
        )

        row = conn.execute(
            text(
                """
                INSERT INTO fin_gastos
                (fecha, cuenta_code, monto, descripcion, proveedor, marca, centro_costo, tipo_doc, doc_num, pagado, fecha_vencimiento, fecha_pago, is_active)
                VALUES
                (:fecha, :cuenta_code, :monto, :descripcion, :proveedor, :marca, :centro_costo, :tipo_doc, :doc_num, COALESCE(:pagado,FALSE), :fecha_vencimiento, :fecha_pago, TRUE)
                RETURNING id_gasto
                """
            ),
            payload,
        ).first()
        conn.commit()
    return {"ok": True, "id_gasto": int(row[0]) if row and row[0] is not None else None}


@router.get("/gastos")
def list_gastos(
    month: int = Query(0, ge=0, le=12),
    year: int = Query(0, ge=0, le=2100),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0, le=200000),
    pagado: int | None = Query(default=None, ge=0, le=1),
    cuenta_code: str = Query("", max_length=40),
    marca: str = Query("", max_length=80),
    centro_costo: str = Query("", max_length=120),
    q: str = Query("", max_length=120),
    me=Depends(get_current_user),
):
    _ensure_roles(me, {"ADMIN", "SUPERADMIN", "COMPRAS"})
    with get_connection() as conn:
        _ensure_tables(conn)
        where_parts = []
        params: dict[str, Any] = {}
        where_parts.append("COALESCE(is_active, TRUE) IS TRUE")
        if month and year:
            where_parts.append("EXTRACT(MONTH FROM fecha)=:m AND EXTRACT(YEAR FROM fecha)=:y")
            params = {"m": month, "y": year}
        if pagado is not None:
            where_parts.append("pagado IS " + ("TRUE" if int(pagado) == 1 else "FALSE"))
        cc = str(cuenta_code or "").strip()
        if cc:
            params["cuenta_code"] = cc
            where_parts.append("cuenta_code = :cuenta_code")
        mm = str(marca or "").strip()
        if mm:
            params["marca"] = mm
            where_parts.append("marca = :marca")
        cco = str(centro_costo or "").strip()
        if cco:
            params["centro_costo"] = cco
            where_parts.append("centro_costo = :centro_costo")
        qq = str(q or "").strip()
        if qq:
            params["q"] = f"%{qq}%"
            where_parts.append("(COALESCE(descripcion,'') ILIKE :q OR COALESCE(proveedor,'') ILIKE :q OR COALESCE(doc_num,'') ILIKE :q)")
        where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        total = conn.execute(text(f"SELECT COUNT(1) FROM fin_gastos {where}"), params).scalar()
        rows = conn.execute(
            text(
                f"""
                SELECT id_gasto, fecha, cuenta_code, monto, descripcion, proveedor, marca, centro_costo,
                       tipo_doc, doc_num, pagado, fecha_vencimiento, fecha_pago, is_active, created_at
                FROM fin_gastos
                {where}
                ORDER BY fecha DESC, id_gasto DESC
                LIMIT :lim OFFSET :off
                """
            ),
            {**params, "lim": int(limit), "off": int(offset)},
        ).mappings().all()
    items = [_row_json(dict(r)) for r in rows]
    return {
        "ok": True,
        "month": int(month),
        "year": int(year),
        "limit": int(limit),
        "offset": int(offset),
        "total": int(total or 0),
        "items": items,
    }


@router.put("/gastos/{id_gasto}")
def update_gasto(id_gasto: int, body: GastoUpdate, me=Depends(get_current_user)):
    _ensure_roles(me, {"ADMIN", "SUPERADMIN", "COMPRAS"})
    with get_connection() as conn:
        _ensure_tables(conn)
        cur = conn.execute(
            text(
                """
                SELECT id_gasto, fecha, cuenta_code, monto, pagado, fecha_pago, is_active
                FROM fin_gastos
                WHERE id_gasto=:id
                LIMIT 1
                """
            ),
            {"id": int(id_gasto)},
        ).mappings().first()
        if not cur:
            raise HTTPException(status_code=404, detail="Gasto no existe")

        sets = []
        params: dict[str, Any] = {"id": int(id_gasto)}

        if body.fecha is not None:
            sets.append("fecha=:fecha")
            params["fecha"] = body.fecha

        if body.cuenta_code is not None:
            sets.append("cuenta_code=:cuenta_code")
            params["cuenta_code"] = _validate_cuenta_code(conn, body.cuenta_code)

        if body.monto is not None:
            m = float(body.monto or 0)
            if not (m > 0):
                raise HTTPException(status_code=400, detail="monto debe ser > 0")
            sets.append("monto=:monto")
            params["monto"] = m

        if body.descripcion is not None:
            sets.append("descripcion=:descripcion")
            params["descripcion"] = _norm_text(body.descripcion, max_len=300)
        if body.proveedor is not None:
            sets.append("proveedor=:proveedor")
            params["proveedor"] = _norm_text(body.proveedor, max_len=200)
        if body.marca is not None:
            sets.append("marca=:marca")
            params["marca"] = _validate_marca(conn, body.marca)
        if body.centro_costo is not None:
            sets.append("centro_costo=:centro_costo")
            params["centro_costo"] = _validate_centro_costo(conn, body.centro_costo)
        if body.tipo_doc is not None:
            sets.append("tipo_doc=:tipo_doc")
            params["tipo_doc"] = _norm_text(body.tipo_doc, max_len=40, upper=True)
        if body.doc_num is not None:
            sets.append("doc_num=:doc_num")
            params["doc_num"] = _norm_text(body.doc_num, max_len=80)

        # vencimiento/pago (mantener coherencia)
        new_fecha = body.fecha if body.fecha is not None else cur["fecha"]
        new_venc = body.fecha_vencimiento if body.fecha_vencimiento is not None else None
        if body.fecha_vencimiento is not None:
            if new_venc and new_fecha and new_venc < new_fecha:
                raise HTTPException(status_code=400, detail="fecha_vencimiento no puede ser < fecha")
            sets.append("fecha_vencimiento=:fecha_vencimiento")
            params["fecha_vencimiento"] = body.fecha_vencimiento

        pagado_val = cur.get("pagado")
        fecha_pago_val = cur.get("fecha_pago")
        if body.pagado is not None:
            pagado_val = bool(body.pagado)
        if body.fecha_pago is not None:
            fecha_pago_val = body.fecha_pago
        pagado_val, fecha_pago_val = _normalize_pago(pagado=bool(pagado_val), fecha_pago=fecha_pago_val)
        if body.pagado is not None:
            sets.append("pagado=:pagado")
            params["pagado"] = pagado_val
        if body.fecha_pago is not None or body.pagado is not None:
            sets.append("fecha_pago=:fecha_pago")
            params["fecha_pago"] = fecha_pago_val

        if body.is_active is not None:
            sets.append("is_active=:is_active")
            params["is_active"] = bool(body.is_active)

        if not sets:
            return {"ok": True, "updated": False}

        conn.execute(text(f"UPDATE fin_gastos SET {', '.join(sets)} WHERE id_gasto=:id"), params)
        conn.commit()
    return {"ok": True, "updated": True}


@router.delete("/gastos/{id_gasto}")
def delete_gasto(id_gasto: int, me=Depends(get_current_user)):
    _ensure_roles(me, {"ADMIN", "SUPERADMIN", "COMPRAS"})
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute(
            text("UPDATE fin_gastos SET is_active=FALSE WHERE id_gasto=:id"),
            {"id": int(id_gasto)},
        )
        conn.commit()
    return {"ok": True}


@router.get("/centros_costo")
def list_centros_costo(me=Depends(get_current_user)):
    _ensure_roles(me, FIN_EVENT_ROLES)
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


@router.get("/cxc_resumen")
def cxc_resumen(
    month: int = Query(0, ge=0, le=12),
    year: int = Query(0, ge=0, le=2100),
    me=Depends(get_current_user),
):
    """
    Cuentas por cobrar: resumen por cliente (saldo pendiente).
    Fuente: fin_eventos.saldo (>0).
    """
    _ensure_roles(me, {"ADMIN", "SUPERADMIN", "JEFE DE OPERACIONES", "COMPRAS"})
    with get_connection() as conn:
        _ensure_tables(conn)
        where = "WHERE COALESCE(saldo,0) > 0"
        params = {}
        if month and year:
            where += " AND EXTRACT(MONTH FROM fecha_evento)=:m AND EXTRACT(YEAR FROM fecha_evento)=:y"
            params = {"m": month, "y": year}
        rows = conn.execute(
            text(
                f"""
                SELECT COALESCE(NULLIF(btrim(cliente),''),'(SIN CLIENTE)') AS cliente,
                       COALESCE(NULLIF(btrim(marca),''),'(SIN MARCA)') AS marca,
                       SUM(COALESCE(saldo,0)) AS saldo_pendiente,
                       SUM(COALESCE(monto_bruto,0)) AS total_bruto,
                       COUNT(*) AS n,
                       MAX(fecha_evento) AS ultimo_evento
                FROM fin_eventos
                {where}
                GROUP BY 1,2
                ORDER BY saldo_pendiente DESC, cliente ASC
                LIMIT 300
                """
            ),
            params,
        ).mappings().all()
    return {"ok": True, "items": list(rows)}


@router.get("/eventos/{id_evento}/pagos")
def list_pagos(id_evento: int, me=Depends(get_current_user)):
    _ensure_roles(me, FIN_EVENT_ROLES)
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
    _ensure_roles(me, FIN_EVENT_ROLES)
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
                ORDER BY
                  CASE
                    WHEN code ~ '^[0-9]+(\\.[0-9]+)*$'
                    THEN string_to_array(code, '.')::int[]
                  END NULLS LAST,
                  code ASC
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
