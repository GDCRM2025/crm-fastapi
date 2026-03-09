from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Body, Depends, HTTPException, Path
from sqlalchemy import text

from backend.core.database import engine
from backend.core.activity_log import log_activity

# Auth (no rompe si no existe)
try:
    from backend.routers.auth import get_current_user  # type: ignore
except Exception:  # pragma: no cover
    def get_current_user():  # type: ignore
        return {"rol": "Admin"}


router = APIRouter(prefix="/leads", tags=["leads-agenda"])

def _ensure_system_notifs() -> None:
    """
    Notificaciones internas por rol (para avisar a Operaciones/Compras/Bodega/Admin).
    Tabla pequeña y con UNIQUE para no duplicar avisos por lead.
    """
    with engine.begin() as cn:
        cn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS public.system_notifs (
                  id BIGSERIAL PRIMARY KEY,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  kind TEXT NOT NULL,
                  role_target TEXT NOT NULL,
                  id_lead BIGINT,
                  title TEXT NOT NULL,
                  body TEXT NOT NULL,
                  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                  read_at TIMESTAMPTZ,
                  read_by TEXT,
                  UNIQUE(kind, role_target, id_lead)
                )
                """
            )
        )


def _notify_roles(kind: str, roles: List[str], *, id_lead: int, title: str, body: str, payload: dict) -> None:
    if not roles:
        return
    _ensure_system_notifs()
    p = json.dumps(payload or {}, ensure_ascii=False)
    with engine.begin() as cn:
        for r in roles:
            cn.execute(
                text(
                    """
                    INSERT INTO public.system_notifs(kind, role_target, id_lead, title, body, payload)
                    VALUES (:k, :rt, :id, :t, :b, CAST(:p AS JSONB))
                    ON CONFLICT (kind, role_target, id_lead) DO NOTHING
                    """
                ),
                {"k": kind, "rt": r, "id": int(id_lead), "t": title, "b": body, "p": p},
            )

def _notify_roles_once(kind: str, roles: List[str], *, id_lead: int, title: str, body: str, payload: dict) -> List[str]:
    """
    Inserta notifs internas y retorna los role_target donde fue insertado (primera vez).
    Lo usamos para evitar re-enviar correos si el lead ya notificó antes.
    """
    if not roles:
        return []
    _ensure_system_notifs()
    p = json.dumps(payload or {}, ensure_ascii=False)
    inserted: List[str] = []
    with engine.begin() as cn:
        for r in roles:
            rid = cn.execute(
                text(
                    """
                    INSERT INTO public.system_notifs(kind, role_target, id_lead, title, body, payload)
                    VALUES (:k, :rt, :id, :t, :b, CAST(:p AS JSONB))
                    ON CONFLICT (kind, role_target, id_lead) DO NOTHING
                    RETURNING id
                    """
                ),
                {"k": kind, "rt": r, "id": int(id_lead), "t": title, "b": body, "p": p},
            ).scalar()
            if rid:
                inserted.append(r)
    return inserted


def _emails_for_roles(roles: List[str]) -> List[str]:
    """
    Emails de usuarios activos por rol objetivo (ADMIN/OPERACIONES/COMPRAS/etc).
    Best-effort: si la tabla no tiene columnas esperadas, devuelve [].
    """
    if not roles:
        return []
    try:
        roles_u = [str(r).upper().strip() for r in roles if str(r).strip()]
        if not roles_u:
            return []
        with engine.connect() as cn:
            rows = cn.execute(
                text(
                    """
                    SELECT DISTINCT u.email
                    FROM public.usuarios u
                    LEFT JOIN public.roles r ON r.id_rol=u.id_rol
                    WHERE COALESCE(u.is_active, TRUE) = TRUE
                      AND u.email IS NOT NULL AND u.email <> ''
                      AND (
                        UPPER(COALESCE(r.nombre,'')) = ANY(:roles)
                        OR UPPER(COALESCE(u.rol,'')) = ANY(:roles)
                      )
                    """
                ),
                {"roles": roles_u},
            ).fetchall()
        out: List[str] = []
        for r in rows:
            e = (r[0] or "").strip()
            if "@" in e and "." in e:
                out.append(e)
        return sorted(set(out))
    except Exception:
        return []


def _ensure_lead_mice_items() -> None:
    """
    Permite flujo "cotización externa":
    el ejecutivo sube PDF + número + monto y carga manualmente los productos/cantidades
    para que el sistema pueda calcular montaje/OPS y alimentar MICE.
    """
    if not _table_exists("leads"):
        return
    with engine.begin() as cn:
        cn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS lead_mice_items (
                  id_item  SERIAL PRIMARY KEY,
                  id_lead  INTEGER NOT NULL REFERENCES leads(id_lead) ON DELETE CASCADE,
                  producto TEXT NOT NULL,
                  cantidad NUMERIC(12,3) NOT NULL DEFAULT 0,
                  service_date DATE,
                  created_at TIMESTAMP DEFAULT now(),
                  created_by TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_lead_mice_items_id_lead ON lead_mice_items(id_lead);
                """
            )
        )
        # Compat: columna para separar productos por día (multi-día).
        cn.execute(text("ALTER TABLE lead_mice_items ADD COLUMN IF NOT EXISTS service_date DATE"))
        cn.execute(text("CREATE INDEX IF NOT EXISTS idx_lead_mice_items_lead_day ON lead_mice_items(id_lead, service_date)"))


def _parse_iso_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    s = str(value).strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except Exception:
        return None


def _lead_mice_items_resumen(id_lead: int) -> List[Dict[str, Any]]:
    _ensure_lead_mice_items()
    q = text(
        """
        SELECT producto, SUM(cantidad)::float AS cantidad
        FROM lead_mice_items
        WHERE id_lead=:id
        GROUP BY producto
        ORDER BY producto
        """
    )
    with engine.connect() as cn:
        rows = cn.execute(q, {"id": id_lead}).mappings().all()
    out = []
    for r in rows:
        prod = str(r.get("producto") or "").strip()
        qty = float(r.get("cantidad") or 0)
        if not prod or qty <= 0:
            continue
        out.append({"producto": prod, "cantidad": qty})
    return out


def _lead_mice_items_resumen_by_day(id_lead: int) -> List[Dict[str, Any]]:
    """
    Retorna items agrupados por día:
      [{ "service_date": "YYYY-MM-DD" | None, "producto": "...", "cantidad": N }, ...]
    """
    _ensure_lead_mice_items()
    q = text(
        """
        SELECT service_date, producto, SUM(cantidad)::float AS cantidad
        FROM lead_mice_items
        WHERE id_lead=:id
        GROUP BY service_date, producto
        ORDER BY service_date NULLS FIRST, producto
        """
    )
    with engine.connect() as cn:
        rows = cn.execute(q, {"id": id_lead}).mappings().all()
    out = []
    for r in rows:
        prod = str(r.get("producto") or "").strip()
        qty = float(r.get("cantidad") or 0)
        if not prod or qty <= 0:
            continue
        sd = r.get("service_date")
        sd_s = None
        try:
            if sd:
                sd_s = str(sd)[:10]
        except Exception:
            sd_s = None
        out.append({"service_date": sd_s, "producto": prod, "cantidad": qty})
    return out


@router.get("/{id_lead}/mice_items")
def get_lead_mice_items(
    id_lead: int = Path(..., ge=1),
    user: dict = Depends(get_current_user),
):
    _require_auth(user)
    # Retornamos el resumen agregado (producto + cantidad)
    return {"ok": True, "items": _lead_mice_items_resumen(id_lead), "by_day": _lead_mice_items_resumen_by_day(id_lead)}


@router.put("/{id_lead}/mice_items")
def put_lead_mice_items(
    id_lead: int = Path(..., ge=1),
    payload: Dict[str, Any] = Body(default_factory=dict),
    user: dict = Depends(get_current_user),
):
    """
    Body:
      { "items": [ { "producto": "Hot Dog", "cantidad": 120 }, ... ] }
    Reemplaza los items manuales del lead (idempotente).
    """
    _require_auth(user)
    items_in = payload.get("items") or []
    if not isinstance(items_in, list):
        raise HTTPException(400, detail="items debe ser una lista")

    # default día: fecha_evento del lead (si existe)
    default_day = None
    try:
        with engine.connect() as cn:
            d0 = cn.execute(text("SELECT fecha_evento FROM leads WHERE id_lead=:id"), {"id": id_lead}).scalar()
        default_day = _parse_iso_date(d0)
    except Exception:
        default_day = None

    # Validación mínima
    cleaned: List[Dict[str, Any]] = []
    for it in items_in:
        if not isinstance(it, dict):
            continue
        prod = str(it.get("producto") or "").strip()
        if not prod:
            continue
        try:
            qty = float(it.get("cantidad") or 0)
        except Exception:
            qty = 0
        if qty <= 0:
            continue
        sd = _parse_iso_date(it.get("service_date") or it.get("fecha") or it.get("dia") or it.get("day"))
        if not sd:
            sd = default_day
        cleaned.append({"producto": prod, "cantidad": qty, "service_date": sd})

    if not cleaned:
        raise HTTPException(400, detail="Debes agregar al menos 1 producto con cantidad > 0")

    _ensure_lead_mice_items()
    created_by = (user.get("username") or user.get("email") or user.get("nombre") or "").strip() or None
    with engine.begin() as cn:
        # asegura que el lead existe
        ok = cn.execute(text("SELECT 1 FROM leads WHERE id_lead=:id"), {"id": id_lead}).scalar()
        if not ok:
            raise HTTPException(404, detail="Lead no existe")
        cn.execute(text("DELETE FROM lead_mice_items WHERE id_lead=:id"), {"id": id_lead})
        for it in cleaned:
            cn.execute(
                text(
                    """
                    INSERT INTO lead_mice_items(id_lead, producto, cantidad, service_date, created_by)
                    VALUES (:id, :p, :c, :sd, :by)
                    """
                ),
                {"id": id_lead, "p": it["producto"], "c": it["cantidad"], "sd": it.get("service_date"), "by": created_by},
            )

        # Trazabilidad en notas del lead (si existe la columna).
        try:
            cols = set(_cols_for("leads"))
            if "notas" in cols:
                stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
                who = created_by or "usuario"
                note = f"[{stamp}] Cotización manual: productos cargados ({len(cleaned)} items) por {who}"
                cn.execute(
                    text(
                        """
                        UPDATE leads
                        SET notas = CASE
                          WHEN notas IS NULL OR notas='' THEN :n
                          ELSE notas || E'\\n' || :n
                        END,
                        updated_at=now()
                        WHERE id_lead=:id
                        """
                    ),
                    {"n": note, "id": id_lead},
                )
        except Exception:
            pass

        # Regla negocio: si cargan productos manuales (cotización externa),
        # el lead pasa a COTIZADO automáticamente (si existe el estado y no está en un estado terminal).
        try:
            cot_id = None
            try:
                cot_id = cn.execute(
                    text("SELECT id_estado FROM public.estados_lead WHERE UPPER(nombre) LIKE '%COTIZ%' ORDER BY id_estado LIMIT 1")
                ).scalar()
                cot_id = int(cot_id) if cot_id is not None else None
            except Exception:
                cot_id = None

            if cot_id:
                cur = cn.execute(text("SELECT id_estado FROM leads WHERE id_lead=:id"), {"id": id_lead}).scalar()
                cur = int(cur) if cur is not None else None
                cur_name = ""
                if cur:
                    try:
                        cur_name = str(
                            cn.execute(text("SELECT nombre FROM estados_lead WHERE id_estado=:e"), {"e": cur}).scalar() or ""
                        ).upper()
                    except Exception:
                        cur_name = ""
                is_terminal = ("CONFIRM" in cur_name) or ("DECLIN" in cur_name) or ("CERR" in cur_name) or ("PERD" in cur_name)
                if not is_terminal and cur != cot_id:
                    cn.execute(text("UPDATE leads SET id_estado=:e, updated_at=now() WHERE id_lead=:id"), {"e": cot_id, "id": id_lead})
        except Exception:
            # no bloqueamos guardado de productos manuales por un tema de estado
            pass
    return {"ok": True, "items": len(cleaned)}


# =========================
# Helpers DB (introspección)
# =========================
def _cols_for(table: str) -> List[str]:
    q = text(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name=:t
        ORDER BY ordinal_position
        """
    )
    with engine.connect() as cn:
        return [r[0] for r in cn.execute(q, {"t": table}).fetchall()]


def _table_exists(table: str) -> bool:
    q = text("SELECT to_regclass(:t)")
    with engine.connect() as cn:
        return bool(cn.execute(q, {"t": f"public.{table}"}).scalar())


def _pk_for(table: str) -> str:
    q = text(
        """
        SELECT kcu.column_name AS pk
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        WHERE tc.constraint_type = 'PRIMARY KEY'
          AND tc.table_schema = 'public'
          AND tc.table_name = :t
        ORDER BY kcu.ordinal_position
        LIMIT 1
        """
    )
    with engine.connect() as cn:
        r = cn.execute(q, {"t": table}).fetchone()
        if not r:
            raise HTTPException(500, detail=f"No PK para {table}")
        return str(r[0])


def _require_auth(user: dict) -> None:
    # si quieres endurecer:
    # if not user: raise HTTPException(401, "No autenticado")
    return


def _qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _filter_existing(table: str, data: Dict[str, Any]) -> Dict[str, Any]:
    cols = set(_cols_for(table))
    return {k: v for k, v in data.items() if k in cols}


def _insert_row(table: str, data: Dict[str, Any]) -> Any:
    data2 = _filter_existing(table, data)
    if not data2:
        raise HTTPException(500, detail=f"No hay columnas válidas para insertar en {table}")
    try:
        pk = _pk_for(table)
    except HTTPException:
        pk = None

    keys = list(data2.keys())
    cols_sql = ", ".join(_qident(k) for k in keys)
    vals_sql = ", ".join(f":{k}" for k in keys)

    if pk:
        q = text(f'INSERT INTO {_qident(table)} ({cols_sql}) VALUES ({vals_sql}) RETURNING {_qident(pk)}')
        with engine.begin() as cn:
            return cn.execute(q, data2).scalar_one()
    else:
        q = text(f'INSERT INTO {_qident(table)} ({cols_sql}) VALUES ({vals_sql})')
        with engine.begin() as cn:
            cn.execute(q, data2)
        return None


def _update_row(table: str, where_pk: str, where_val: Any, data: Dict[str, Any]) -> None:
    data2 = _filter_existing(table, data)
    if not data2:
        return
    sets = ", ".join(f"{_qident(k)}=:{k}" for k in data2.keys())
    data2["__id"] = where_val
    q = text(f'UPDATE {_qident(table)} SET {sets} WHERE {_qident(where_pk)}=:__id')
    with engine.begin() as cn:
        res = cn.execute(q, data2)
        if res.rowcount == 0:
            raise HTTPException(404, detail="No existe")


# =========================
# Helpers negocio
# =========================
def _find_estado_confirmado_id() -> int:
    q = text("SELECT id_estado, nombre FROM estados_lead WHERE LOWER(nombre)=LOWER('Confirmado') LIMIT 1")
    with engine.connect() as cn:
        r = cn.execute(q).fetchone()
        if r:
            return int(r[0])

    # fallback: por si está con variante
    q2 = text("SELECT id_estado FROM estados_lead WHERE LOWER(nombre) LIKE 'confirm%' ORDER BY id_estado LIMIT 1")
    with engine.connect() as cn:
        r2 = cn.execute(q2).fetchone()
        if r2:
            return int(r2[0])

    raise HTTPException(500, detail="No existe estado 'Confirmado' en estados_lead")


def _get_lead(id_lead: int) -> Dict[str, Any]:
    q = text(
        """
        SELECT *
        FROM leads
        WHERE id_lead = :id
        """
    )
    with engine.connect() as cn:
        r = cn.execute(q, {"id": id_lead}).mappings().first()
        if not r:
            raise HTTPException(404, detail="Lead no existe")
        return dict(r)


def _get_marca_nombre(id_marca: Optional[int]) -> str:
    if not id_marca:
        return "Sin Marca"
    cols = _cols_for("marcas")
    col = "nombre" if "nombre" in cols else ("marca" if "marca" in cols else None)
    if not col:
        return "Sin Marca"
    q = text(f"SELECT {col} FROM marcas WHERE id_marca=:id LIMIT 1")
    with engine.connect() as cn:
        r = cn.execute(q, {"id": id_marca}).fetchone()
        return str(r[0]) if r else "Sin Marca"


def _get_comuna_nombre(id_comuna: Optional[int]) -> str:
    if not id_comuna:
        return "Sin Comuna"
    cols = _cols_for("comunas")
    col = "nombre" if "nombre" in cols else ("comuna" if "comuna" in cols else None)
    if not col:
        return "Sin Comuna"
    q = text(f"SELECT {col} FROM comunas WHERE id_comuna=:id LIMIT 1")
    with engine.connect() as cn:
        r = cn.execute(q, {"id": id_comuna}).fetchone()
        return str(r[0]) if r else "Sin Comuna"


def _list_cotizaciones_for_lead(lead: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Intenta relacionar cotizaciones con lead:
    - si cotizaciones tiene id_lead, usa eso
    - si no, usa codigo_cliente o email o nombre_cliente
    """
    cols = set(_cols_for("cotizaciones"))
    where = ""
    params: Dict[str, Any] = {}

    if "id_lead" in cols:
        where = "WHERE id_lead=:id_lead"
        params["id_lead"] = lead["id_lead"]
    elif "codigo_cliente" in cols and lead.get("codigo_cliente"):
        where = "WHERE codigo_cliente=:cc"
        params["cc"] = lead["codigo_cliente"]
    elif "email" in cols and lead.get("email"):
        where = "WHERE email=:em"
        params["em"] = lead["email"]
    elif "nombre_cliente" in cols and lead.get("nombre_cliente"):
        where = "WHERE nombre_cliente=:nc"
        params["nc"] = lead["nombre_cliente"]
    else:
        return []

    # columnas típicas: id_cotizacion, total, created_at/fecha, estado
    sel = ["*"]
    q = text(f"SELECT {', '.join(sel)} FROM cotizaciones {where} ORDER BY 1 DESC")
    with engine.connect() as cn:
        rows = cn.execute(q, params).mappings().all()

    # normaliza para UI
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        # intenta descubrir PK
        if "id_cotizacion" not in d:
            # fallback: primera col como id
            d["id_cotizacion"] = list(d.values())[0]
        out.append(d)
    return out


def _cotizacion_detalle_resumen(id_cotizacion: int) -> List[Dict[str, Any]]:
    # Preferimos cotizacion_items (nuevo)
    if _table_exists("cotizacion_items"):
        q = text(
            """
            SELECT producto, SUM(cantidad)::float AS cantidad
            FROM cotizacion_items
            WHERE id_cotizacion = :id
            GROUP BY producto
            ORDER BY producto
            """
        )
        with engine.connect() as cn:
            rows = cn.execute(q, {"id": id_cotizacion}).mappings().all()
        return [{"producto": str(r["producto"]), "cantidad": float(r["cantidad"] or 0)} for r in rows]

    cols = set(_cols_for("cotizaciones_detalle"))

    # nombre producto
    prod_col = None
    for c in ("producto", "nombre_producto", "descripcion", "detalle", "producto_nombre", "nombre"):
        if c in cols:
            prod_col = c
            break

    # cantidad
    qty_col = None
    for c in ("cantidad", "qty", "unidades", "cantidad_producto", "cant"):
        if c in cols:
            qty_col = c
            break

    # FK cotización
    fk_col = None
    for c in ("id_cotizacion", "cotizacion_id"):
        if c in cols:
            fk_col = c
            break

    if not prod_col or not qty_col or not fk_col:
        raise HTTPException(
            500,
            detail="cotizaciones_detalle no tiene columnas esperadas (producto/cantidad/id_cotizacion)",
        )

    q = text(
        f"""
        SELECT {prod_col} AS producto, SUM({qty_col})::float AS cantidad
        FROM cotizaciones_detalle
        WHERE {fk_col} = :id
        GROUP BY {prod_col}
        ORDER BY {prod_col}
        """
    )
    with engine.connect() as cn:
        rows = cn.execute(q, {"id": id_cotizacion}).mappings().all()

    out = []
    for r in rows:
        out.append({"producto": str(r["producto"]), "cantidad": float(r["cantidad"] or 0)})
    return out


# =========================
# Montaje (reglas actuales)
# =========================
class _Reglas:
    # Nota: "burger" es clave para capturar "Burger Clásica" (Del Sabor/Express).
    SALADO = ["burger", "churrasco", "hot dog", "hotdog", "hamburguesa", "wrap", "lomito", " as ", "as xl", "as italiano", "mechada", "mini", "notburger"]
    POP = ["pop corn", "cabritas"]
    ALG = ["algodón", "algodon"]
    BRO = ["brocheta", "waffle"]
    PIZZA = ["pizza", "pizzeta"]
    FRITO = ["nuggets", "papas fritas", "empanadas"]
    CHUR = ["churros", "medialuna"]
    HEL = ["helado soft", "helado sundae", "helado", "soft", "sundae"]
    GRAN = ["slush", "granizado"]
    DONUT = ["donut"]
    BEBD = ["bebida en lata", "jugo en lata", "bebestible", "bebida", "jugos"]


def _cat_for_producto(nombre: str) -> Optional[str]:
    p = (nombre or "").lower()
    if any(w in p for w in _Reglas.SALADO): return "salado"
    if any(w in p for w in _Reglas.POP): return "pop"
    if any(w in p for w in _Reglas.ALG): return "alg"
    if any(w in p for w in _Reglas.BRO): return "bro"
    if any(w in p for w in _Reglas.PIZZA): return "pizza"
    if any(w in p for w in _Reglas.FRITO): return "frito"
    if any(w in p for w in _Reglas.CHUR): return "chur"
    if any(w in p for w in _Reglas.HEL): return "hel"
    if any(w in p for w in _Reglas.GRAN): return "gran"
    if any(w in p for w in _Reglas.DONUT): return "donut"
    if any(w in p for w in _Reglas.BEBD): return "bebd"
    return None


def _ceil_div(q: float, div: float) -> int:
    if div <= 0:
        return 0
    n = int(q // div)
    return n if (q % div) == 0 else n + 1


def _calcular_montaje_single(items: List[Dict[str, Any]]) -> Tuple[Dict[str, int], int, str, str]:
    """
    Retorna:
      montaje_map: {equipo: unidades}
      ops: int
      montaje_text (con iconos)
      products_text (con bullets)
    """
    # Agregación por categoría para evitar dobles conteos (ej: Cabritas + Pop corn)
    # y para que OPS no sea "suma de máquinas" (una estación puede tener 2 equipos pero 1 operador).
    montaje: Dict[str, int] = {}
    ops = 0
    cat_qty: Dict[str, float] = {}

    def add_eq(eq: str, n: int):
        if n <= 0:
            return
        montaje[eq] = (montaje.get(eq, 0) + n)

    # products text + suma por categoría
    lines_prod = ["PRODUCTOS"]
    for it in items:
        qty = float(it.get("cantidad") or 0)
        prod = str(it.get("producto") or "").strip()
        if not prod or qty <= 0:
            continue
        qty_str = str(int(qty)) if abs(qty - int(qty)) < 1e-9 else str(qty)
        lines_prod.append(f"• {qty_str} {prod}")

        cat = _cat_for_producto(prod)
        if not cat:
            continue
        cat_qty[cat] = float(cat_qty.get(cat, 0.0) + qty)

    # Reglas por categoría (stations)
    salado = cat_qty.get("salado", 0.0)
    if salado > 0:
        n = _ceil_div(salado, 150)
        add_eq("Carro Clásico", n)
        ops += n  # 1 operador por estación

    pop = cat_qty.get("pop", 0.0)
    if pop > 0:
        n = _ceil_div(pop, 150)
        add_eq("Carro Rojo", n)
        add_eq("Máquina Cabritas", n)
        ops += n  # 1 operador por estación (carro + máquina)

    alg = cat_qty.get("alg", 0.0)
    if alg > 0:
        n = _ceil_div(alg, 150)
        add_eq("Máquina Algodón", n)
        ops += n

    bro = cat_qty.get("bro", 0.0)
    if bro > 0:
        add_eq("Carro Rojo", 1)
        add_eq("Mesa", 1)
        ops += 1

    pizza = cat_qty.get("pizza", 0.0)
    if pizza > 0:
        add_eq("Horno Eléctrico", 1)
        add_eq("Estación Entrega", 1)
        add_eq("Mesa", 1)
        ops += 1

    frito = cat_qty.get("frito", 0.0)
    if frito > 0:
        add_eq("Freidora", 1)
        add_eq("Mesa", 1)
        ops += 1

    chur = cat_qty.get("chur", 0.0)
    if chur > 0:
        add_eq("Horno", 1)
        add_eq("Mesa", 1)
        ops += 1

    hel = cat_qty.get("hel", 0.0)
    if hel > 0:
        add_eq("Máquina Helados", 1)
        add_eq("Base", 1)
        add_eq("Mesa", 1)
        ops += 1

    gran = cat_qty.get("gran", 0.0)
    if gran > 0:
        add_eq("Máquina Granizados", 1)
        add_eq("Mesa", 1)
        ops += 1

    donut = cat_qty.get("donut", 0.0)
    if donut > 0:
        add_eq("Mesa Donuts", 1)
        ops += 1

    bebd = cat_qty.get("bebd", 0.0)
    if bebd > 0:
        add_eq("Mesa", 1)
        ops += 1

    # montaje text (compacto estilo calendario)
    lines_m = []
    # orden preferente (para que se lea “bien”)
    pref_order = [
        "Carro Clásico",
        "Carro Rojo",
        "Máquina Cabritas",
        "Máquina Algodón",
        "Horno Eléctrico",
        "Freidora",
        "Horno",
        "Máquina Helados",
        "Máquina Granizados",
        "Mesa Donuts",
        "Mesa",
        "Base",
        "Estación Entrega",
    ]
    for k in pref_order:
        if k in montaje:
            n = montaje[k]
            # Siempre mostrar cantidad para evitar ambigüedad ("¿es 1 o faltó?")
            lines_m.append(f"{n} x {k}")

    # fallback por si aparece otro equipo
    for k in montaje.keys():
        if k not in pref_order:
            n = montaje[k]
            lines_m.append(f"{n} x {k}")

    montaje_text = "Montaje sugerido\n" + ("\n".join(lines_m) if lines_m else "—")
    products_text = "\n".join(lines_prod)
    return montaje, ops, montaje_text, products_text


def _calcular_montaje(items: List[Dict[str, Any]]) -> Tuple[Dict[str, int], int, str, str]:
    """
    Soporta multi-día (service_date/fecha/dia por item).

    Para eventos de varios días:
      - montaje/ops se calculan "max por día" (no se suman, porque no son simultáneos)
      - products_text incluye desglose por día
    """
    def _item_day(it: Dict[str, Any]) -> Optional[date]:
        d = it.get("service_date") or it.get("fecha") or it.get("dia") or it.get("day")
        return _parse_iso_date(d)

    # agrupar por día
    by_day: Dict[Optional[date], List[Dict[str, Any]]] = {}
    for it in items or []:
        by_day.setdefault(_item_day(it), []).append(it)

    # Si hay 0-1 día distinto, usar cálculo simple
    uniq_days = sorted([d for d in by_day.keys() if d is not None])
    if len(uniq_days) <= 1:
        return _calcular_montaje_single(items)

    # multi día: calcular por día y tomar max
    max_montaje: Dict[str, int] = {}
    max_ops = 0
    per_day_lines = []

    # products text por día
    products_lines = ["PRODUCTOS"]
    # acumulador por día/producto (para ordenar y evitar duplicados raros)
    for d in sorted(uniq_days):
        day_items = by_day.get(d) or []
        # resumen por producto
        prod_sum: Dict[str, float] = {}
        for it in day_items:
            prod = str(it.get("producto") or "").strip()
            if not prod:
                continue
            try:
                qty = float(it.get("cantidad") or 0)
            except Exception:
                qty = 0
            if qty <= 0:
                continue
            prod_sum[prod] = float(prod_sum.get(prod, 0.0) + qty)
        for prod in sorted(prod_sum.keys()):
            qty = prod_sum[prod]
            qty_str = str(int(qty)) if abs(qty - int(qty)) < 1e-9 else str(qty)
            products_lines.append(f"• {d.isoformat()}: {qty_str} {prod}")

    for d in sorted(uniq_days):
        m, ops, _, _ = _calcular_montaje_single(by_day.get(d) or [])
        if ops > max_ops:
            max_ops = ops
        for k, n in m.items():
            if n > (max_montaje.get(k) or 0):
                max_montaje[k] = int(n)
        # compact line por día
        eqs = ", ".join([f"{int(v)}x {k}" for k, v in sorted(m.items())]) if m else "—"
        per_day_lines.append(f"• {d.isoformat()}: OPS {ops} — {eqs}")

    # montaje text general (max)
    pref_order = [
        "Carro Clásico",
        "Carro Rojo",
        "Máquina Cabritas",
        "Máquina Algodón",
        "Horno Eléctrico",
        "Freidora",
        "Horno",
        "Máquina Helados",
        "Máquina Granizados",
        "Mesa Donuts",
        "Mesa",
        "Base",
        "Estación Entrega",
    ]
    lines_m = []
    for k in pref_order:
        if k in max_montaje:
            lines_m.append(f"{max_montaje[k]} x {k}")
    for k in max_montaje.keys():
        if k not in pref_order:
            lines_m.append(f"{max_montaje[k]} x {k}")

    montaje_text = "Montaje sugerido (max por día)\n" + ("\n".join(lines_m) if lines_m else "—")
    if per_day_lines:
        montaje_text += "\n\nDetalle por día\n" + "\n".join(per_day_lines)

    return max_montaje, max_ops, montaje_text, "\n".join(products_lines)


def _parse_hhmm(s: str) -> Tuple[int, int]:
    s = (s or "").strip()
    if len(s) != 5 or s[2] != ":":
        raise ValueError("HH:MM inválido")
    hh = int(s[:2])
    mm = int(s[3:])
    if hh < 0 or hh > 23 or mm < 0 or mm > 59:
        raise ValueError("HH:MM inválido")
    return hh, mm


def _build_event(
    lead: Dict[str, Any],
    comuna: str,
    marca: str,
    items: List[Dict[str, Any]],
    montaje_text: str,
    ops: int,
    telefono: str,
    direccion: str,
    start_time: Optional[str],
    end_time: Optional[str],
    hr_tbd: bool,
    created_by: str | None = None,
) -> Dict[str, Any]:
    # Fecha (soporta multi-día si los items traen service_date/fecha/dia)
    fe = lead.get("fecha_evento")
    if not fe:
        raise HTTPException(400, detail="Lead sin fecha_evento")
    base_day = date.fromisoformat(str(fe)[:10]) if isinstance(fe, str) else fe

    days = []
    for it in items or []:
        d = _parse_iso_date(it.get("service_date") or it.get("fecha") or it.get("dia") or it.get("day"))
        if d:
            days.append(d)
    if days:
        start_day = min(days)
        end_day = max(days)
    else:
        start_day = base_day
        end_day = base_day

    # Hora / all-day si HR TBD
    if hr_tbd:
        dt_start = datetime.combine(start_day, datetime.min.time())
        # all-day: hasta el día siguiente del último día
        dt_end = datetime.combine(end_day, datetime.min.time()) + timedelta(days=1)
        hr_label = "HR TBD"
    else:
        if not start_time or not end_time:
            raise HTTPException(400, detail="Faltan horas (start_time/end_time) o marca hr_tbd=true")
        sh, sm = _parse_hhmm(start_time)
        eh, em = _parse_hhmm(end_time)

        dt_start = datetime(start_day.year, start_day.month, start_day.day, sh, sm)

        # regla pedida: si termina 00:00, se fuerza 23:59 mismo día
        if end_time.strip() == "00:00":
            dt_end = datetime(end_day.year, end_day.month, end_day.day, 23, 59)
        else:
            dt_end = datetime(end_day.year, end_day.month, end_day.day, eh, em)
            # Solo aplicamos cruce de medianoche si es el mismo día
            if end_day == start_day and dt_end <= dt_start:
                dt_end = dt_end + timedelta(days=1)

        hr_label = f"{start_time} - {end_time}"

    # Título con fallback
    dir_label = direccion.strip() if direccion.strip() else "DIR TBD"
    nombre = lead.get("nombre_cliente") or lead.get("cliente") or "(Sin nombre)"
    if hr_tbd:
        title = f"{nombre} - {marca} HR TBD"
    else:
        title = f"{nombre} - {marca}"

    # Descripción estilo Calendar
    # bullets ya vienen listos
    _, _, _, products_text = _calcular_montaje(items)
    prod_lines = [ln for ln in products_text.split("\n")[1:] if ln.strip()]
    if not prod_lines:
        prod_lines = ["• —"]

    m_lines_raw = [ln.strip() for ln in montaje_text.replace("Montaje sugerido", "").split("\n") if ln.strip()]
    m_lines = [f"• {ln}" if not ln.startswith("•") else ln for ln in m_lines_raw] or ["• —"]

    desc_lines = [
        "🛒 PRODUCTOS:",
        *prod_lines,
        "",
        "🧰 MONTAJE:",
        *m_lines,
        "",
        f"👥 OPS: {ops}",
        f"TELEFONO: {telefono.strip()}",
        f"DIRECCION: {direccion.strip()}, {comuna}",
        "",
        "Green Diamond",
        f"Creado por: {created_by or 'CRM'}",
    ]
    description = "\n".join(desc_lines).strip()

    return {
        "title": title,
        "start_at": dt_start.isoformat(),
        "end_at": dt_end.isoformat(),
        "location": comuna,
        "description": description,
        "ops": ops,
        "montaje_text": montaje_text,
        "products_text": products_text,
    }


# =========================
# Endpoint principal del flujo
# =========================
@router.post("/{id_lead}/move")
def move_lead_and_maybe_agenda(
    id_lead: int = Path(..., ge=1),
    payload: Dict[str, Any] = Body(default_factory=dict),
    user: dict = Depends(get_current_user),
):
    """
    Payload:
      - id_estado: int (obligatorio)
      - agendar: bool | null
      - id_cotizacion: int | null
      - quote_source: "manual" | "cotizador"   (opcional; default=auto)
      - dry_run: bool (opcional; si true, NO escribe en BD, solo devuelve preview)
      - override_ops: int (opcional)
      - override_montaje_text: str (opcional)
      - override_title: str (opcional)
      - override_location: str (opcional)
      - override_description: str (opcional)
      - telefono: str
      - direccion: str
      - start_time: "HH:MM"
      - end_time: "HH:MM"
      - hr_tbd: bool
    """
    _require_auth(user)

    try:
        id_estado = payload.get("id_estado")
        if id_estado is None:
            raise HTTPException(400, detail="Falta id_estado")

        lead = _get_lead(id_lead)
        confirmado_id = _find_estado_confirmado_id()
        dry_run = bool(payload.get("dry_run", False))

        agendar = payload.get("agendar", None)
        should_update_now = int(id_estado) != confirmado_id or agendar is False

        # Preview: solo se usa para confirmado+agendar (sin escribir en BD).
        if dry_run:
            if int(id_estado) != confirmado_id:
                raise HTTPException(400, detail="dry_run solo aplica para Confirmado")
            if agendar is not True:
                raise HTTPException(400, detail="dry_run requiere agendar=true")

        # 1) mover estado (si no requiere pre-agenda)
        if should_update_now:
            _update_row("leads", "id_lead", id_lead, {"id_estado": int(id_estado)})

        # 2) si NO es confirmado, termina
        if int(id_estado) != confirmado_id:
            return {"ok": True, "ask_agendar": False}

        # 3) traer cotizaciones candidatas (cotizador).
        cotizaciones = _list_cotizaciones_for_lead(lead)

        # Bloquear confirmado SOLO si es cotización externa (no hay cotizaciones del cotizador).
        monto = float(lead.get("monto_cotizado") or 0) if lead else 0
        num = (lead.get("num_cotizacion") or "").strip() if lead else ""
        if not cotizaciones and (monto <= 0 or not num):
            raise HTTPException(status_code=400, detail="No se puede CONFIRMAR sin monto y número de cotización (cotización externa).")

        # 4) si el frontend aún no decidió agendar -> preguntar y entregar opciones
        if agendar is None:
            return {
                "ok": True,
                "ask_agendar": True,
                "lead": {
                    "id_lead": lead["id_lead"],
                    "nombre_cliente": lead.get("nombre_cliente"),
                    "fecha_evento": str(lead.get("fecha_evento")),
                    "id_comuna": lead.get("id_comuna"),
                    "id_marca": lead.get("id_marca"),
                    "telefono": lead.get("telefono"),
                    "direccion": lead.get("direccion"),
                },
                "cotizaciones": cotizaciones,
            }

        # 5) si NO quiere agendar ahora, deja pendiente (para reintentar luego)
        if agendar is False:
            # Igual dejamos snapshot de productos/montaje/ops para MICE.
            quote_source = str(payload.get("quote_source") or "").strip().lower()
            id_cot_tmp = payload.get("id_cotizacion") if quote_source != "manual" else None
            items: List[Dict[str, Any]] = []
            if id_cot_tmp:
                try:
                    items = _cotizacion_detalle_resumen(int(id_cot_tmp))
                except Exception:
                    items = []
            if not items:
                # Manual: usamos items con service_date (multi-día)
                items = _lead_mice_items_resumen_by_day(id_lead)
            if not items:
                # No bloqueamos confirmar sin agendar: el ejecutivo puede confirmar y cargar MICE después.
                # Dejamos marcado pendiente para que el sistema lo muestre y lo obligue antes de agendar.
                _update_row(
                    "leads",
                    "id_lead",
                    id_lead,
                    {
                        "pendiente_agendar": True,
                        "pre_products_text": None,
                        "pre_montaje_text": None,
                        "pre_ops": None,
                    },
                )
                return {"ok": True, "ask_agendar": False, "agendado": False, "warning": "NO_MICE_ITEMS"}
            _, ops, montaje_text, products_text = _calcular_montaje(items)
            _update_row(
                "leads",
                "id_lead",
                id_lead,
                {
                    "pendiente_agendar": True,
                    "pre_products_text": products_text,
                    "pre_montaje_text": montaje_text,
                    "pre_ops": ops,
                },
            )
            return {"ok": True, "ask_agendar": False, "agendado": False}

        # 6) agendar: seleccionar cotización aprobada
        quote_source = str(payload.get("quote_source") or "").strip().lower()  # "manual" | "cotizador" | ""
        id_cot = payload.get("id_cotizacion")

        # Si el usuario eligió explícitamente "manual", NO auto-seleccionamos cotización del cotizador.
        if quote_source == "manual":
            id_cot = None

        # Si hay múltiples cotizaciones del cotizador, el usuario DEBE elegir cuál es la aprobada.
        # (evita tomar "la última" o una antigua por error).
        if quote_source != "manual" and cotizaciones and len(cotizaciones) > 1 and not id_cot:
            raise HTTPException(400, detail="Debes seleccionar la cotización aprobada (id_cotizacion).")

        # si solo hay 1, autoselecciona
        if not id_cot and cotizaciones and quote_source != "manual":
            # Preferimos la cotización vigente del lead si existe.
            lead_vig = lead.get("id_cotizacion_vigente")
            if lead_vig:
                try:
                    lead_vig = int(lead_vig)
                except Exception:
                    lead_vig = None
            if lead_vig:
                hit = next((c for c in cotizaciones if int(c.get("id_cotizacion") or 0) == int(lead_vig)), None)
                if hit:
                    id_cot = hit.get("id_cotizacion")

            # Si no hay vigente, tomamos la más nueva (max revision/version o max id).
            if not id_cot:
                def _rank(c: Dict[str, Any]) -> Tuple[int, int]:
                    rev = c.get("revision")
                    if rev is None:
                        rev = c.get("version")
                    try:
                        r = int(rev or 0)
                    except Exception:
                        r = 0
                    try:
                        cid = int(c.get("id_cotizacion") or 0)
                    except Exception:
                        cid = 0
                    return (r, cid)
                best = max(cotizaciones, key=_rank)
                id_cot = best.get("id_cotizacion")

        # Si no hay cotización (caso cotización externa), permitimos avanzar si existen items manuales.
        if not id_cot:
            items_manual = _lead_mice_items_resumen_by_day(id_lead)
            if not items_manual:
                raise HTTPException(400, detail="No hay cotización seleccionada ni productos manuales para calcular montaje")

        telefono = str(payload.get("telefono") or lead.get("telefono") or "").strip()
        direccion = str(payload.get("direccion") or lead.get("direccion") or "").strip()
        start_time = payload.get("start_time")
        end_time = payload.get("end_time")
        hr_tbd = bool(payload.get("hr_tbd", False))

        # Si faltan horarios, marcamos HR TBD
        if not start_time or not end_time:
            hr_tbd = True

        # Si falta teléfono/dirección, usar "POR CONFIRMAR"
        if not telefono:
            telefono = "POR CONFIRMAR"
        if not direccion:
            direccion = "POR CONFIRMAR"

        # 7) calcular productos + montaje
        items: List[Dict[str, Any]] = []
        if id_cot and quote_source != "manual":
            items = _cotizacion_detalle_resumen(int(id_cot))
        if not items:
            items = _lead_mice_items_resumen_by_day(id_lead)
        if not items:
            raise HTTPException(400, detail="No hay productos para calcular montaje. Carga productos para MICE.")
        montaje_map, ops, montaje_text, products_text = _calcular_montaje(items)

        # Overrides (el usuario puede editar antes de crear en calendar)
        try:
            if payload.get("override_ops") is not None:
                ops_o = int(payload.get("override_ops"))
                if ops_o >= 0:
                    ops = ops_o
        except Exception:
            pass
        try:
            if payload.get("override_montaje_text") is not None:
                mt = str(payload.get("override_montaje_text") or "").strip()
                if mt:
                    montaje_text = mt
        except Exception:
            pass

        # 8) construir evento (título/desc)
        comuna = _get_comuna_nombre(lead.get("id_comuna"))
        marca = _get_marca_nombre(lead.get("id_marca"))
        ev = _build_event(
            lead=lead,
            comuna=comuna,
            marca=marca,
            items=items,
            montaje_text=montaje_text,
            ops=ops,
            telefono=telefono,
            direccion=direccion,
            start_time=start_time,
            end_time=end_time,
            hr_tbd=hr_tbd,
            created_by=str(payload.get("created_by") or "") or None,
        )

        # Overrides de título/ubicación/descripcion
        try:
            t = str(payload.get("override_title") or "").strip()
            if t:
                ev["title"] = t
        except Exception:
            pass
        try:
            loc_o = str(payload.get("override_location") or "").strip()
            if loc_o:
                ev["location"] = loc_o
        except Exception:
            pass
        try:
            desc_o = str(payload.get("override_description") or "").strip()
            if desc_o:
                ev["description"] = desc_o
        except Exception:
            pass

        if dry_run:
            return {
                "ok": True,
                "preview": True,
                "quote_source": quote_source or ("cotizador" if id_cot else "manual"),
                "id_cotizacion": int(id_cot) if id_cot else None,
                "items": items,
                "evento": {
                    **ev,
                    "ops": ops,
                    "montaje_text": montaje_text,
                    "products_text": products_text,
                },
            }

        # 9) persistencia en eventos_calendario (solo columnas existentes)
        evento_id = None
        if _table_exists("eventos_calendario"):
            # Si ya existe un evento para este lead, lo actualizamos (no duplicar).
            try:
                pk_ev = _pk_for("eventos_calendario")
                cols_ev = set(_cols_for("eventos_calendario"))

                # Map flexible de columnas (hosting tiene varias versiones del schema)
                data_ev: Dict[str, Any] = {}
                if "id_lead" in cols_ev:
                    data_ev["id_lead"] = id_lead
                if "title" in cols_ev:
                    data_ev["title"] = ev["title"]
                if "titulo" in cols_ev:
                    data_ev["titulo"] = ev["title"]
                if "start_at" in cols_ev:
                    data_ev["start_at"] = ev["start_at"]
                if "fecha_inicio" in cols_ev:
                    data_ev["fecha_inicio"] = ev["start_at"]
                if "end_at" in cols_ev:
                    data_ev["end_at"] = ev["end_at"]
                if "fecha_termino" in cols_ev:
                    data_ev["fecha_termino"] = ev["end_at"]
                if "location" in cols_ev:
                    data_ev["location"] = ev["location"]
                if "lugar" in cols_ev:
                    data_ev["lugar"] = ev["location"]
                if "description" in cols_ev:
                    data_ev["description"] = ev["description"]
                if "descripcion" in cols_ev:
                    data_ev["descripcion"] = ev["description"]
                if "id_cotizacion" in cols_ev:
                    data_ev["id_cotizacion"] = int(id_cot) if id_cot else None
                if "ops" in cols_ev:
                    data_ev["ops"] = ops
                if "estado" in cols_ev:
                    data_ev["estado"] = "agendado"

                with engine.connect() as cn:
                    row_ev = cn.execute(
                        text(f"SELECT {pk_ev} FROM eventos_calendario WHERE id_lead=:id ORDER BY {pk_ev} DESC LIMIT 1"),
                        {"id": id_lead},
                    ).fetchone()
                if row_ev and row_ev[0] is not None:
                    evento_id = row_ev[0]
                    _update_row(
                        "eventos_calendario",
                        pk_ev,
                        evento_id,
                        data_ev,
                    )
                else:
                    evento_id = _insert_row(
                        "eventos_calendario",
                        data_ev,
                    )
            except Exception:
                # si algo falla con la tabla, no rompemos el flujo (igual guardamos snapshot en leads)
                evento_id = None

        # 10) actualizar lead (marca agendado + snapshot)
        # Guardamos también el ID del evento (si existe una columna compatible en leads).
        try:
            cols_lead = set(_cols_for("leads"))
        except Exception:
            cols_lead = set()
        extra_ev_ref = {}
        if evento_id is not None:
            if "id_evento" in cols_lead:
                extra_ev_ref["id_evento"] = int(evento_id)
            elif "id_evento_calendario" in cols_lead:
                extra_ev_ref["id_evento_calendario"] = int(evento_id)
        # Compat: calendario interno puede leer desde leads.calendar_start/calendar_end
        # (si eventos_calendario no existe o falla). Guardamos aquí para que Operaciones
        # SIEMPRE vea el evento, aunque cambie el schema.
        if "calendar_start" in cols_lead:
            extra_ev_ref["calendar_start"] = ev["start_at"]
        if "calendar_end" in cols_lead:
            extra_ev_ref["calendar_end"] = ev["end_at"]
        _update_row(
            "leads",
            "id_lead",
            id_lead,
            {
                "id_estado": int(id_estado),
                "pendiente_agendar": False,
                # Importante: NO sobrescribir num_cotizacion (es el N° real de la cotización del cliente).
                # Si hay una cotización del cotizador, guardamos su id en id_cotizacion_vigente (si existe la columna).
                **({"id_cotizacion_vigente": int(id_cot)} if id_cot else {}),
                **extra_ev_ref,
                "pre_products_text": ev["products_text"],
                "pre_montaje_text": ev["montaje_text"],
                "pre_ops": ops,
                "pre_title": ev["title"],
                "pre_start": ev["start_at"],
                "pre_end": ev["end_at"],
                "pre_location": ev["location"],
                "pre_telefono": telefono,
                "pre_direccion": direccion,
                "pre_description": ev["description"],
            },
        )

        # agregar nota con cotización confirmada (si existe columna notas)
        try:
            cols = _cols_for("leads")
            if "notas" in cols:
                stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
                if quote_source == "manual" or not id_cot:
                    note = f"[CONFIRMADO {stamp}] Cotización confirmada: MANUAL"
                else:
                    note = f"[CONFIRMADO {stamp}] Cotización confirmada: {id_cot}"
                # append with SQL to preserve existing notes
                with engine.connect() as cn:
                    cn.execute(
                        text(
                            """
                            UPDATE leads
                            SET notas = CASE
                              WHEN notas IS NULL OR notas='' THEN :n
                              ELSE notas || E'\\n' || :n
                            END,
                            updated_at=now()
                            WHERE id_lead=:id
                            """
                        ),
                        {"n": note, "id": id_lead},
                    )
                    cn.commit()
        except Exception:
            pass

        # 11) Notificación interna (sin montos) para roles de Operaciones/Compras/Bodega/Admin.
        # Se dispara cuando el evento queda efectivamente agendado.
        try:
            cliente = str(lead.get("cliente") or lead.get("nombre_cliente") or "").strip()
            marca_txt = _get_marca_nombre(int(lead.get("id_marca") or 0) or 0)
            comuna_txt = _get_comuna_nombre(int(lead.get("id_comuna") or 0) or 0)
            fecha_txt = str(lead.get("fecha_evento") or "").strip()

            resumen = "\n".join(
                [
                    f"Cliente: {cliente}",
                    f"Marca: {marca_txt}",
                    f"Comuna: {comuna_txt}",
                    f"Fecha evento: {fecha_txt}",
                    f"OPS: {ops}",
                    "",
                    "Productos:",
                    (ev.get("products_text") or "").strip(),
                    "",
                    "Montaje / Observaciones:",
                    (ev.get("montaje_text") or "").strip(),
                ]
            ).strip()

            title = f"Nuevo evento agendado · {marca_txt} · {cliente}".strip(" ·")
            roles = [
                "ADMIN",
                "SUPERADMIN",
                "OPERACIONES",
                "JEFE DE OPERACIONES",
                "MICE",
                "JEFE DE COMPRAS",
                "BODEGUERO",
                "COMPRAS",
            ]
            inserted_roles = _notify_roles_once(
                "EVENT_AGENDADO",
                roles,
                id_lead=int(id_lead),
                title=title,
                body=resumen,
                payload={
                    "id_lead": int(id_lead),
                    "id_evento": int(evento_id) if evento_id is not None else None,
                    "marca": marca_txt,
                    "cliente": cliente,
                    "comuna": comuna_txt,
                    "fecha_evento": fecha_txt,
                },
            )

            # Email (best-effort, sin montos). Enviamos SOLO si es la primera vez (inserted_roles).
            if inserted_roles:
                try:
                    to = _emails_for_roles(inserted_roles)
                    if to:
                        from backend.core.email import send_email_group  # type: ignore
                        footer = "\n\n--\nCRM Green Diamond\nMensaje automático (sin montos)\n"
                        try:
                            send_email_group(to, title, resumen + footer)
                        except Exception:
                            # No cortamos el flujo por email.
                            pass
                except Exception:
                    pass
        except Exception:
            pass

        # Activity log (BD): confirmado + agendado
        try:
            with engine.begin() as cn:
                log_activity(
                    cn,
                    username=str(user.get("username") or user.get("email") or user.get("name") or ""),
                    user_id=int(user.get("id") or 0) if str(user.get("id") or "").isdigit() else None,
                    role=str(user.get("role") or user.get("rol") or ""),
                    action="EVENT_CONFIRMED_AGENDED",
                    entity_type="lead",
                    entity_id=int(id_lead),
                    meta={"id_evento": int(evento_id) if evento_id is not None else None},
                )
        except Exception:
            pass

        return {
            "ok": True,
            "ask_agendar": False,
            "agendado": True,
            "evento": {
                "id_evento": evento_id,
                **ev,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, detail=str(e))
