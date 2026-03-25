import json
import ast
from datetime import date, datetime, timedelta


def _as_text(v):
    if v is None:
        return ""
    if isinstance(v, str):
        s = v
        # En algunos deployments, valores bytes terminan persistidos como str("b'...'")
        # (por ejemplo en title/productos). Intentamos “desenvolver” ese literal.
        ss = s.strip()
        if (ss.startswith("b'") and ss.endswith("'")) or (ss.startswith('b"') and ss.endswith('"')):
            try:
                lit = ast.literal_eval(ss)
                if isinstance(lit, (bytes, bytearray, memoryview)):
                    return _as_text(lit)
                if isinstance(lit, str):
                    return lit
            except Exception:
                pass
        return s
    if isinstance(v, (bytes, bytearray, memoryview)):
        b = bytes(v)
        for enc in ("utf-8", "latin-1"):
            try:
                return b.decode(enc)
            except Exception:
                continue
        return b.decode("utf-8", "ignore")
    return str(v)


def _safe_time_hhmm(value):
    try:
        if value is None:
            return None
        v = str(value).strip()
        if not v or v in ("--:--", "-:-", "TBD", "tbd", "null", "None"):
            return None
        if len(v) >= 5 and v[2] == ":":
            return v[:5]
        return None
    except Exception:
        return None

def _safe_int(value, default=0):
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default

from fastapi import APIRouter, Body, Depends, HTTPException, Path
from sqlalchemy import text

from backend.core.database import engine
from backend.core.activity_log import log_activity

try:
    from backend.routers.auth import get_current_user
except Exception:
    def get_current_user():
        return {"rol": "Admin"}


router = APIRouter(prefix="/leads", tags=["leads-agenda"])


def _ensure_system_notifs():
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


def _notify_roles(kind, roles, id_lead, title, body, payload):
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


def _notify_roles_once(kind, roles, id_lead, title, body, payload):
    if not roles:
        return []
    _ensure_system_notifs()
    p = json.dumps(payload or {}, ensure_ascii=False)
    inserted = []
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


def _emails_for_roles(roles):
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
        out = []
        for r in rows:
            e = (r[0] or "").strip()
            if "@" in e and "." in e:
                out.append(e)
        return sorted(set(out))
    except Exception:
        return []


def _ensure_lead_mice_items():
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
                )
                """
            )
        )
        cn.execute(text("CREATE INDEX IF NOT EXISTS idx_lead_mice_items_id_lead ON lead_mice_items(id_lead)"))
        cn.execute(text("ALTER TABLE lead_mice_items ADD COLUMN IF NOT EXISTS service_date DATE"))
        cn.execute(text("CREATE INDEX IF NOT EXISTS idx_lead_mice_items_lead_day ON lead_mice_items(id_lead, service_date)"))


def _parse_iso_date(value):
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


def _lead_mice_items_resumen(id_lead):
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


def _lead_mice_items_resumen_by_day(id_lead):
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
        sd_s = str(sd)[:10] if sd else None
        out.append({"service_date": sd_s, "producto": prod, "cantidad": qty})
    return out


@router.get("/{id_lead}/mice_items")
@router.get("/{id_lead}/mice_items")
def get_lead_mice_items(id_lead: int = Path(..., ge=1), user=Depends(get_current_user)):

    return {
        "ok": True,
        "items": _lead_mice_items_resumen(id_lead),
        "by_day": _lead_mice_items_resumen_by_day(id_lead),
    }


@router.put("/{id_lead}/mice_items")
@router.put("/{id_lead}/mice_items")
def put_lead_mice_items(id_lead: int = Path(..., ge=1), payload=Body(default_factory=dict), user=Depends(get_current_user)):

    items_in = payload.get("items") or []
    if not isinstance(items_in, list):
        raise HTTPException(400, detail="items debe ser una lista")

    default_day = None
    try:
        with engine.connect() as cn:
            d0 = cn.execute(text("SELECT fecha_evento FROM leads WHERE id_lead=:id"), {"id": id_lead}).scalar()
        default_day = _parse_iso_date(d0)
    except Exception:
        default_day = None

    cleaned = []
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
    created_by = str(user.get("username") or user.get("email") or user.get("nombre") or "").strip() or None

    with engine.begin() as cn:
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

        try:
            cols = set(_cols_for("leads"))
            if "notas" in cols:
                stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
                who = created_by or "usuario"
                note = "[%s] Cotización manual: productos cargados (%s items) por %s" % (stamp, len(cleaned), who)
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
                        cur_name = str(cn.execute(text("SELECT nombre FROM estados_lead WHERE id_estado=:e"), {"e": cur}).scalar() or "").upper()
                    except Exception:
                        cur_name = ""
                is_terminal = ("CONFIRM" in cur_name) or ("DECLIN" in cur_name) or ("CERR" in cur_name) or ("PERD" in cur_name)
                if not is_terminal and cur != cot_id:
                    cn.execute(text("UPDATE leads SET id_estado=:e, updated_at=now() WHERE id_lead=:id"), {"e": cot_id, "id": id_lead})
        except Exception:
            pass

    return {"ok": True, "items": len(cleaned)}


def _cols_for(table):
    q = text(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name=:t
        ORDER BY ordinal_position
        """
    )
    with engine.connect() as cn:
        rows = cn.execute(q, {"t": table}).fetchall()
        return [_as_text(r[0]).strip() for r in rows if r and _as_text(r[0]).strip()]


def _table_exists(table):
    q = text("SELECT to_regclass(:t)")
    with engine.connect() as cn:
        return bool(cn.execute(q, {"t": "public.%s" % table}).scalar())


def _pk_for(table):
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
            raise HTTPException(500, detail="No PK para %s" % table)
        return _as_text(r[0]).strip()


def _require_auth(user):
    return


def _qident(name):
    s = _as_text(name)
    return '"' + s.replace('"', '""') + '"'


def _filter_existing(table, data):
    cols = set(_cols_for(table))
    return {k: v for k, v in data.items() if k in cols}


def _insert_row(table, data):
    data2 = _filter_existing(table, data)
    if not data2:
        raise HTTPException(500, detail="No hay columnas válidas para insertar en %s" % table)
    try:
        pk = _pk_for(table)
    except HTTPException:
        pk = None

    keys = list(data2.keys())
    cols_sql = ", ".join(_qident(k) for k in keys)
    vals_sql = ", ".join([":%s" % k for k in keys])

    if pk:
        q = text('INSERT INTO %s (%s) VALUES (%s) RETURNING %s' % (_qident(table), cols_sql, vals_sql, _qident(pk)))
        with engine.begin() as cn:
            return cn.execute(q, data2).scalar_one()
    else:
        q = text('INSERT INTO %s (%s) VALUES (%s)' % (_qident(table), cols_sql, vals_sql))
        with engine.begin() as cn:
            cn.execute(q, data2)
        return None


def _update_row(table, where_pk, where_val, data):
    data2 = _filter_existing(table, data)
    if not data2:
        return
    sets = ", ".join(["%s=:%s" % (_qident(k), k) for k in data2.keys()])
    data2["__id"] = where_val
    q = text('UPDATE %s SET %s WHERE %s=:__id' % (_qident(table), sets, _qident(where_pk)))
    with engine.begin() as cn:
        res = cn.execute(q, data2)
        if res.rowcount == 0:
            raise HTTPException(404, detail="No existe")


def _find_estado_confirmado_id():
    q = text("SELECT id_estado, nombre FROM estados_lead WHERE LOWER(nombre)=LOWER('Confirmado') LIMIT 1")
    with engine.connect() as cn:
        r = cn.execute(q).fetchone()
        if r:
            return int(r[0])

    q2 = text("SELECT id_estado FROM estados_lead WHERE LOWER(nombre) LIKE 'confirm%' ORDER BY id_estado LIMIT 1")
    with engine.connect() as cn:
        r2 = cn.execute(q2).fetchone()
        if r2:
            return int(r2[0])

    raise HTTPException(500, detail="No existe estado 'Confirmado' en estados_lead")


def _get_estado_nombre(id_estado: int | None) -> str:
    if not id_estado:
        return ""
    try:
        q = text("SELECT nombre FROM estados_lead WHERE id_estado=:id LIMIT 1")
        with engine.connect() as cn:
            r = cn.execute(q, {"id": int(id_estado)}).fetchone()
        return _as_text(r[0]).strip() if r and r[0] is not None else ""
    except Exception:
        return ""


def _append_lead_notas(id_lead: int, note: str) -> None:
    note = _as_text(note).strip()
    if not note:
        return
    try:
        cols = _cols_for("leads")
        if "notas" not in cols:
            return
        with engine.begin() as cn:
            cn.execute(
                text(
                    """
                    UPDATE leads
                    SET notas = CASE
                      WHEN notas IS NULL OR notas='' THEN :n
                      ELSE notas || E'\n\n' || :n
                    END,
                    updated_at=now()
                    WHERE id_lead=:id
                    """
                ),
                {"n": note, "id": int(id_lead)},
            )
    except Exception:
        return


def _get_lead(id_lead):
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


def _get_marca_nombre(id_marca):
    if not id_marca:
        return "Sin Marca"
    cols = _cols_for("marcas")
    col = "nombre" if "nombre" in cols else ("marca" if "marca" in cols else None)
    if not col:
        return "Sin Marca"
    q = text("SELECT %s FROM marcas WHERE id_marca=:id LIMIT 1" % col)
    with engine.connect() as cn:
        r = cn.execute(q, {"id": id_marca}).fetchone()
        return _as_text(r[0]).strip() if r and r[0] is not None else "Sin Marca"


def _get_comuna_nombre(id_comuna):
    if not id_comuna:
        return ""
    cols = _cols_for("comunas")
    col = "nombre" if "nombre" in cols else ("comuna" if "comuna" in cols else None)
    if not col:
        return ""
    q = text("SELECT %s FROM comunas WHERE id_comuna=:id LIMIT 1" % col)
    with engine.connect() as cn:
        r = cn.execute(q, {"id": id_comuna}).fetchone()
        return _as_text(r[0]).strip() if r and r[0] is not None else ""


def _infer_marca_from_cotizacion(id_cotizacion):
    """
    Si el lead no tiene marca, intentamos inferirla desde cotizaciones.
    Soporta esquemas donde la PK no necesariamente se llama id_cotizacion.
    """
    if not id_cotizacion:
        return None
    try:
        if not _table_exists("cotizaciones"):
            return None
    except Exception:
        return None

    cols = set(_cols_for("cotizaciones"))
    pk = None
    try:
        pk = _pk_for("cotizaciones")
    except Exception:
        pk = "id_cotizacion" if "id_cotizacion" in cols else ("id" if "id" in cols else None)
    if not pk:
        return None

    # Caso 1: FK a marcas
    if "id_marca" in cols:
        q = text(f"SELECT id_marca FROM cotizaciones WHERE {_qident(pk)}=:id LIMIT 1")
        with engine.connect() as cn:
            r = cn.execute(q, {"id": int(id_cotizacion)}).fetchone()
            mid = r[0] if r else None
        if mid:
            m = _get_marca_nombre(mid)
            return m if m and m != "Sin Marca" else None

    # Caso 2: nombre de marca directo en cotizaciones
    for col in ("marca", "nombre_marca", "brand", "brand_name"):
        if col in cols:
            q = text(f"SELECT {_qident(col)} FROM cotizaciones WHERE {_qident(pk)}=:id LIMIT 1")
            with engine.connect() as cn:
                r = cn.execute(q, {"id": int(id_cotizacion)}).fetchone()
                if r and r[0] is not None:
                    t = _as_text(r[0]).strip()
                    return t or None

    return None


def _list_cotizaciones_for_lead(lead):
    cols = set(_cols_for("cotizaciones"))
    where = ""
    params = {}

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

    q = text("SELECT * FROM cotizaciones %s ORDER BY 1 DESC" % where)
    with engine.connect() as cn:
        rows = cn.execute(q, params).mappings().all()

    out = []
    for r in rows:
        d = dict(r)
        if "id_cotizacion" not in d:
            d["id_cotizacion"] = list(d.values())[0]
        out.append(d)
    return out


def _cotizacion_detalle_resumen(id_cotizacion):
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
        return [{"producto": _as_text(r["producto"]).strip(), "cantidad": float(r["cantidad"] or 0)} for r in rows]

    cols = set(_cols_for("cotizaciones_detalle"))

    prod_col = None
    for c in ("producto", "nombre_producto", "descripcion", "detalle", "producto_nombre", "nombre"):
        if c in cols:
            prod_col = c
            break

    qty_col = None
    for c in ("cantidad", "qty", "unidades", "cantidad_producto", "cant"):
        if c in cols:
            qty_col = c
            break

    fk_col = None
    for c in ("id_cotizacion", "cotizacion_id"):
        if c in cols:
            fk_col = c
            break

    if not prod_col or not qty_col or not fk_col:
        raise HTTPException(500, detail="cotizaciones_detalle no tiene columnas esperadas")

    q = text(
        """
        SELECT {prod_col} AS producto, SUM({qty_col})::float AS cantidad
        FROM cotizaciones_detalle
        WHERE {fk_col} = :id
        GROUP BY {prod_col}
        ORDER BY {prod_col}
        """.format(prod_col=prod_col, qty_col=qty_col, fk_col=fk_col)
    )
    with engine.connect() as cn:
        rows = cn.execute(q, {"id": id_cotizacion}).mappings().all()

    out = []
    for r in rows:
        out.append({"producto": _as_text(r["producto"]).strip(), "cantidad": float(r["cantidad"] or 0)})
    return out


class _Reglas:
    SALADO = ["burger", "churrasco", "hot dog", "hotdog", "hamburguesa", "wrap", "lomito", " as ", "as xl", "as italiano", "as luco", "mechada", "mini", "notburger"]
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


def _cat_for_producto(nombre):
    p = _as_text(nombre).lower()
    if any(w in p for w in _Reglas.SALADO):
        return "salado"
    if any(w in p for w in _Reglas.POP):
        return "pop"
    if any(w in p for w in _Reglas.ALG):
        return "alg"
    if any(w in p for w in _Reglas.BRO):
        return "bro"
    if any(w in p for w in _Reglas.PIZZA):
        return "pizza"
    if any(w in p for w in _Reglas.FRITO):
        return "frito"
    if any(w in p for w in _Reglas.CHUR):
        return "chur"
    if any(w in p for w in _Reglas.HEL):
        return "hel"
    if any(w in p for w in _Reglas.GRAN):
        return "gran"
    if any(w in p for w in _Reglas.DONUT):
        return "donut"
    if any(w in p for w in _Reglas.BEBD):
        return "bebd"
    return None


def _ceil_div(q, div):
    if div <= 0:
        return 0
    n = int(q // div)
    return n if (q % div) == 0 else n + 1


def _calcular_montaje_single(items):
    montaje = {}
    ops = 0
    cat_qty = {}

    def add_eq(eq, n):
      if n <= 0:
          return
      montaje[eq] = montaje.get(eq, 0) + n

    lines_prod = ["PRODUCTOS"]
    for it in items:
        qty = float(it.get("cantidad") or 0)
        prod = _as_text(it.get("producto") or "").strip()
        if not prod or qty <= 0:
            continue
        qty_str = str(int(qty)) if abs(qty - int(qty)) < 1e-9 else str(qty)
        lines_prod.append("• %s %s" % (qty_str, prod))

        cat = _cat_for_producto(prod)
        if not cat:
            continue
        cat_qty[cat] = float(cat_qty.get(cat, 0.0) + qty)

    salado = cat_qty.get("salado", 0.0)
    if salado > 0:
        n = _ceil_div(salado, 150)
        add_eq("Carro Clásico", n)
        ops += n

    pop = cat_qty.get("pop", 0.0)
    if pop > 0:
        n = _ceil_div(pop, 150)
        add_eq("Carro Rojo", n)
        add_eq("Máquina Cabritas", n)
        ops += n

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

    lines_m = []
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
            lines_m.append("%s x %s" % (montaje[k], k))

    for k in montaje.keys():
        if k not in pref_order:
            lines_m.append("%s x %s" % (montaje[k], k))

    montaje_text = "Montaje sugerido\n" + ("\n".join(lines_m) if lines_m else "—")
    products_text = "\n".join(lines_prod)
    return montaje, ops, montaje_text, products_text


def _calcular_montaje(items):
    def _item_day(it):
        d = it.get("service_date") or it.get("fecha") or it.get("dia") or it.get("day")
        return _parse_iso_date(d)

    by_day = {}
    for it in items or []:
        by_day.setdefault(_item_day(it), []).append(it)

    uniq_days = sorted([d for d in by_day.keys() if d is not None])
    if len(uniq_days) <= 1:
        return _calcular_montaje_single(items)

    max_montaje = {}
    max_ops = 0
    per_day_lines = []
    products_lines = ["PRODUCTOS"]

    for d in sorted(uniq_days):
        day_items = by_day.get(d) or []
        prod_sum = {}
        for it in day_items:
            prod = _as_text(it.get("producto") or "").strip()
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
            products_lines.append("• %s: %s %s" % (d.isoformat(), qty_str, prod))

    for d in sorted(uniq_days):
        m, ops, _, _ = _calcular_montaje_single(by_day.get(d) or [])
        if ops > max_ops:
            max_ops = ops
        for k, n in m.items():
            if n > (max_montaje.get(k) or 0):
                max_montaje[k] = int(n)
        eqs = ", ".join(["%sx %s" % (int(v), k) for k, v in sorted(m.items())]) if m else "—"
        per_day_lines.append("• %s: OPS %s — %s" % (d.isoformat(), ops, eqs))

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
            lines_m.append("%s x %s" % (max_montaje[k], k))
    for k in max_montaje.keys():
        if k not in pref_order:
            lines_m.append("%s x %s" % (max_montaje[k], k))

    montaje_text = "Montaje sugerido (max por día)\n" + ("\n".join(lines_m) if lines_m else "—")
    if per_day_lines:
        montaje_text += "\n\nDetalle por día\n" + "\n".join(per_day_lines)

    return max_montaje, max_ops, montaje_text, "\n".join(products_lines)


def _parse_hhmm(s):
    s = (s or "").strip()
    if len(s) != 5 or s[2] != ":":
        raise ValueError("HH:MM inválido")
    hh = int(s[:2])
    mm = int(s[3:])
    if hh < 0 or hh > 23 or mm < 0 or mm > 59:
        raise ValueError("HH:MM inválido")
    return hh, mm


def _is_missing_dir(value):
    v = _as_text(value).strip()
    if not v:
        return True
    return v.upper() in ("POR CONFIRMAR", "DIR TBD", "DIRECCION TBD")


def _title_suffix(missing_time, missing_dir):
    if missing_time and missing_dir:
        return "HR Y DIR TBD"
    if missing_time:
        return "HR TBD"
    if missing_dir:
        return "DIR TBD"
    return ""


def _build_event(
    lead,
    comuna,
    marca,
    items,
    montaje_text,
    ops,
    telefono,
    direccion,
    start_time,
    end_time,
    hr_tbd,
    agenda_notes: str | None = None,
):
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

    start_time = _safe_time_hhmm(start_time)
    end_time = _safe_time_hhmm(end_time)
    missing_time = bool(hr_tbd) or (start_time is None) or (end_time is None)



    missing_dir = _is_missing_dir(direccion)

    if missing_time:
        dt_start = datetime(start_day.year, start_day.month, start_day.day, 11, 0)
        dt_end = datetime(end_day.year, end_day.month, end_day.day, 13, 0)
        hr_label = "11:00 - 13:00"
    else:
        sh, sm = _parse_hhmm(start_time)
        eh, em = _parse_hhmm(end_time)

        dt_start = datetime(start_day.year, start_day.month, start_day.day, sh, sm)

        if str(end_time).strip() == "00:00":
            dt_end = datetime(end_day.year, end_day.month, end_day.day, 23, 59)
        else:
            dt_end = datetime(end_day.year, end_day.month, end_day.day, eh, em)
            if end_day == start_day and dt_end <= dt_start:
                dt_end = dt_end + timedelta(days=1)

        hr_label = "%s - %s" % (start_time, end_time)

    cliente = _as_text(lead.get("nombre_cliente") or lead.get("cliente") or "(Sin nombre)").strip() or "(Sin nombre)"
    marca_txt = _as_text(marca).strip() if marca else "Sin Marca"

    suffix = _title_suffix(missing_time, missing_dir)
    title = "%s - %s" % (cliente, marca_txt)
    if suffix:
        title = "%s - %s" % (title, suffix)

    # Regla: LOCATION debe ser SOLO la comuna (la dirección completa va en la descripción).
    # Si no hay comuna, dejamos marcador para que sea visible en calendario.
    location = _as_text(comuna).strip()
    if not location or location.strip().lower() in ("sin comuna", "s/comuna", "scomuna"):
        location = "COMUNA TBD"

    _, _, _, products_text = _calcular_montaje(items)
    prod_lines = [ln for ln in products_text.split("\n")[1:] if ln.strip()]
    if not prod_lines:
        prod_lines = ["• —"]

    m_lines_raw = [ln.strip() for ln in montaje_text.replace("Montaje sugerido", "").split("\n") if ln.strip()]
    m_lines = [("• " + ln) if not ln.startswith("•") else ln for ln in m_lines_raw] or ["• —"]

    # Regla: la dirección completa va en la descripción (sin forzar comuna acá).
    dir_label = "DIR TBD" if missing_dir else _as_text(direccion).strip()
    phone_label = _as_text(telefono).strip() or "POR CONFIRMAR"

    # Formato requerido para Calendar:
    # - NOTAS arriba (destacadas)
    # - Espacio entre encabezado y primer item en PRODUCTOS/MONTAJE
    # - Espacio entre OPS y contacto
    notes = _as_text(agenda_notes).strip()
    desc_lines = []
    if notes:
        desc_lines += [
            "🟨 NOTAS (IMPORTANTE):",
            notes,
            "",
        ]

    desc_lines += [
        "🛒 PRODUCTOS:",
        "",
        *prod_lines,
        "",
        "🧰 MONTAJE:",
        "",
        *m_lines,
        "",
        "👥 OPS: %s" % ops,
        "",
        "📞 TELEFONO: %s" % phone_label,
        "📍 DIRECCION: %s" % dir_label,
    ]
    description = "\n".join(desc_lines).strip()

    return {
        # Compat: el campo `day` se usa en UI (tabs por día) y en pre_events_json.
        # En esta función no existe una variable `day`; el día "base" del evento
        # es `start_day` (derivado de items/service_date o fecha_evento).
        "day": start_day.isoformat(),
        "title": title,
        "start_at": dt_start.isoformat(),
        "end_at": dt_end.isoformat(),
        "location": location,
        "description": description,
        "ops": ops,
        "montaje_text": montaje_text,
        "products_text": products_text,
    }


def _items_grouped_by_day(items: list[dict], fallback_day: date) -> list[tuple[date, list[dict]]]:
    by: dict[date, list[dict]] = {}
    for it in items or []:
        d = _parse_iso_date(it.get("service_date") or it.get("fecha") or it.get("dia") or it.get("day"))
        if not d:
            d = fallback_day
        by.setdefault(d, []).append(it)
    return sorted(by.items(), key=lambda x: x[0])


def _build_event_for_day(
    *,
    lead: dict,
    day: date,
    comuna: str,
    marca: str,
    items_day: list[dict],
    telefono: str,
    direccion: str,
    start_time: str | None,
    end_time: str | None,
    hr_tbd: bool,
    agenda_notes: str | None = None,
    override_title: str | None = None,
    override_location: str | None = None,
    override_description: str | None = None,
    override_ops: int | None = None,
    override_montaje_text: str | None = None,
    day_label: str | None = None,
) -> dict:
    _, ops_sug, montaje_sug, _products_text = _calcular_montaje(items_day)
    ops = int(override_ops) if override_ops is not None else ops_sug
    # Regla negocio: SIEMPRE debe haber operadores (>=1), incluso en carga manual multi-día.
    try:
        ops = int(ops or 0)
    except Exception:
        ops = 0
    if ops < 1:
        ops = 1
    montaje_text = (override_montaje_text or "").strip() or montaje_sug

    lead2 = dict(lead or {})
    lead2["fecha_evento"] = day.isoformat()
    ev = _build_event(
        lead=lead2,
        comuna=comuna,
        marca=marca,
        items=items_day,
        montaje_text=montaje_text,
        ops=ops,
        telefono=telefono,
        direccion=direccion,
        start_time=start_time,
        end_time=end_time,
        hr_tbd=hr_tbd,
        agenda_notes=agenda_notes,
    )

    # En Google Calendar ya se ve la fecha/hora en el encabezado; no repetimos en la descripción.

    if override_title:
        ev["title"] = override_title
    if override_location:
        ev["location"] = override_location
    if override_description:
        ev["description"] = override_description

    ev["day"] = day.isoformat()
    return ev


@router.post("/{id_lead}/move")
def move_lead_and_maybe_agenda(
    id_lead: int = Path(..., ge=1),
    payload=Body(default_factory=dict),
    user=Depends(get_current_user),
):

    _require_auth(user)

    try:
        id_estado = payload.get("id_estado")
        if id_estado is None:
            raise HTTPException(400, detail="Falta id_estado")

        lead = _get_lead(id_lead)
        old_estado_id = None
        try:
            old_estado_id = int(lead.get("id_estado") or 0)
        except Exception:
            old_estado_id = None
        confirmado_id = _find_estado_confirmado_id()
        dry_run = bool(payload.get("dry_run", False))
        agendar = payload.get("agendar", None)
        should_update_now = int(id_estado) != confirmado_id or agendar is False

        if dry_run:
            if int(id_estado) != confirmado_id:
                raise HTTPException(400, detail="dry_run solo aplica para Confirmado")
            if agendar is not True:
                raise HTTPException(400, detail="dry_run requiere agendar=true")

        if should_update_now:
            _update_row("leads", "id_lead", id_lead, {"id_estado": int(id_estado)})
            # Auditoría: registrar cambio de estado inmediato en historial (notas).
            if not dry_run:
                try:
                    who = str(user.get("username") or user.get("email") or user.get("id") or "").strip() or "CRM"
                    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
                    old_name = _get_estado_nombre(old_estado_id) or (str(old_estado_id) if old_estado_id else "")
                    new_name = _get_estado_nombre(int(id_estado)) or str(id_estado)
                    if old_name and old_name == new_name:
                        # no-op
                        pass
                    else:
                        if old_name:
                            msg = "[ESTADO %s] %s → %s · por %s" % (stamp, old_name, new_name, who)
                        else:
                            msg = "[ESTADO %s] %s · por %s" % (stamp, new_name, who)
                        _append_lead_notas(id_lead, msg)
                except Exception:
                    pass

        if int(id_estado) != confirmado_id:
            return {"ok": True, "ask_agendar": False}

        cotizaciones = _list_cotizaciones_for_lead(lead)

        monto = float(lead.get("monto_cotizado") or 0) if lead else 0
        num = str(lead.get("num_cotizacion") or "").strip() if lead else ""
        # Importante UX: el `dry_run` se usa para previsualización de agenda (tabs por día).
        # No bloqueamos el preview por falta de meta manual, pero sí bloqueamos la confirmación real.
        if not cotizaciones and (not dry_run) and (monto <= 0 or not num):
            raise HTTPException(
                status_code=400,
                detail="No se puede CONFIRMAR sin monto y número de cotización (cotización externa).",
            )

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

        if agendar is False:
            quote_source = str(payload.get("quote_source") or "").strip().lower()
            id_cot_tmp = payload.get("id_cotizacion") if quote_source != "manual" else None

            items = []
            if id_cot_tmp:
                try:
                    items = _cotizacion_detalle_resumen(int(id_cot_tmp))
                except Exception:
                    items = []

            if not items:
                items = _lead_mice_items_resumen_by_day(id_lead)

            if not items:
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

        quote_source = str(payload.get("quote_source") or "").strip().lower()
        id_cot = payload.get("id_cotizacion")

        if quote_source == "manual":
            id_cot = None

        if quote_source != "manual" and cotizaciones and len(cotizaciones) > 1 and not id_cot:
            raise HTTPException(400, detail="Debes seleccionar la cotización aprobada (id_cotizacion).")

        if not id_cot and cotizaciones and quote_source != "manual":
            lead_vig = lead.get("id_cotizacion_vigente")
            if lead_vig:
                try:
                    lead_vig = int(lead_vig)
                except Exception:
                    lead_vig = None
            if lead_vig:
                hit = None
                for c in cotizaciones:
                    try:
                        cid = int(c.get("id_cotizacion") or 0)
                    except Exception:
                        cid = 0
                    if cid == int(lead_vig):
                        hit = c
                        break
                if hit:
                    id_cot = hit.get("id_cotizacion")

            if not id_cot:
                def _rank(c):
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

        if not id_cot:
            items_manual = _lead_mice_items_resumen_by_day(id_lead)
            if not items_manual:
                raise HTTPException(400, detail="No hay cotización seleccionada ni productos manuales para calcular montaje")

        telefono = str(payload.get("telefono") or lead.get("telefono") or "").strip()
        direccion = str(payload.get("direccion") or lead.get("direccion") or "").strip()
        agenda_notes = str(payload.get("agenda_notes") or payload.get("notas_agenda") or payload.get("notas") or "").strip() or None
        start_time = _safe_time_hhmm(payload.get("start_time"))
        end_time = _safe_time_hhmm(payload.get("end_time"))
        hr_tbd = bool(payload.get("hr_tbd", False))
        if not start_time or not end_time:
            hr_tbd = True
            start_time = None
            end_time = None


        items = []
        if id_cot and quote_source != "manual":
            items = _cotizacion_detalle_resumen(int(id_cot))
        if not items:
            items = _lead_mice_items_resumen_by_day(id_lead)
        if not items:
            raise HTTPException(400, detail="No hay productos para calcular montaje. Carga productos para MICE.")

        _, ops, montaje_text, products_text = _calcular_montaje(items)

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

        comuna = _get_comuna_nombre(lead.get("id_comuna"))
        marca = _get_marca_nombre(lead.get("id_marca"))
        if (not marca or marca == "Sin Marca") and id_cot:
            marca2 = _infer_marca_from_cotizacion(id_cot)
            if marca2:
                marca = marca2

        # overrides globales
        override_title = str(payload.get("override_title") or "").strip() or None
        override_location = str(payload.get("override_location") or "").strip() or None
        override_description = str(payload.get("override_description") or "").strip() or None

        # overrides por día: { "YYYY-MM-DD": {ops:int, montaje_text:"..."} }
        overrides_by_day: dict[str, dict] = {}
        try:
            raw_obd = payload.get("override_by_day")
            if isinstance(raw_obd, dict):
                overrides_by_day = {str(k): (v or {}) for k, v in raw_obd.items()}
            elif isinstance(raw_obd, list):
                for r in raw_obd:
                    if not isinstance(r, dict):
                        continue
                    d = str(r.get("day") or r.get("fecha") or r.get("service_date") or "").strip()
                    if not d:
                        continue
                    overrides_by_day[d] = r
        except Exception:
            overrides_by_day = {}

        # overrides globales (compat)
        override_ops_global: int | None = None
        try:
            if payload.get("override_ops") is not None and str(payload.get("override_ops")).strip() != "":
                override_ops_global = int(payload.get("override_ops"))
        except Exception:
            override_ops_global = None

        override_montaje_global: str | None = None
        try:
            if payload.get("override_montaje_text") is not None:
                mt = str(payload.get("override_montaje_text") or "").strip()
                if mt:
                    override_montaje_global = mt
        except Exception:
            override_montaje_global = None

        base_day = date.fromisoformat(str(lead.get("fecha_evento"))[:10]) if lead.get("fecha_evento") else date.today()
        grouped = _items_grouped_by_day(items, base_day)
        total_days = len(grouped) if grouped else 1

        eventos: list[dict] = []
        for idx, (day, items_day) in enumerate(grouped or [(base_day, items)], start=1):
            obd = overrides_by_day.get(day.isoformat(), {}) if overrides_by_day else {}

            ops_day = None
            try:
                if obd.get("ops") is not None and str(obd.get("ops")).strip() != "":
                    ops_day = int(obd.get("ops"))
            except Exception:
                ops_day = None

            mt_day = None
            try:
                if obd.get("montaje_text") is not None:
                    mt_day = str(obd.get("montaje_text") or "").strip() or None
            except Exception:
                mt_day = None

            day_label = f"Día {idx}/{total_days} · {day.isoformat()}" if total_days > 1 else day.isoformat()

            eventos.append(
                _build_event_for_day(
                    lead=lead,
                    day=day,
                    comuna=comuna,
                    marca=marca,
                    items_day=items_day,
                    telefono=telefono,
                    direccion=direccion,
                    start_time=start_time,
                    end_time=end_time,
                    hr_tbd=hr_tbd,
                    agenda_notes=agenda_notes,
                    override_title=override_title,
                    override_location=override_location,
                    override_description=override_description,
                    override_ops=(ops_day if ops_day is not None else override_ops_global),
                    override_montaje_text=(mt_day if mt_day is not None else override_montaje_global),
                    day_label=day_label,
                )
            )

        ev = eventos[0] if eventos else None
        if not ev:
            raise HTTPException(500, detail="No se pudo construir el evento para agendar")

        if dry_run:
            # Dry-run: NO escribe en BD, solo devuelve preview (multi-día si aplica).
            return {
                "ok": True,
                "preview": True,
                "quote_source": quote_source or ("cotizador" if id_cot else "manual"),
                "id_cotizacion": int(id_cot) if id_cot else None,
                "items": items,
                "eventos": [
                    {
                        "id_evento": None,
                        "day": e.get("day"),
                        "title": e.get("title"),
                        "start_at": e.get("start_at"),
                        "end_at": e.get("end_at"),
                        "location": e.get("location"),
                        "description": e.get("description"),
                        "ops": e.get("ops"),
                        "montaje_text": e.get("montaje_text"),
                        "products_text": e.get("products_text"),
                    }
                    for e in (eventos or [])
                ],
                # Back-compat (primer día)
                "evento": {
                    "id_evento": None,
                    "day": ev.get("day"),
                    "title": ev["title"],
                    "start_at": ev["start_at"],
                    "end_at": ev["end_at"],
                    "location": ev["location"],
                    "description": ev["description"],
                    "ops": ev.get("ops"),
                    "montaje_text": ev.get("montaje_text"),
                    "products_text": ev.get("products_text"),
                },
            }

        evento_id = None
        if _table_exists("eventos_calendario"):
            try:
                pk_ev = _pk_for("eventos_calendario")
                cols_ev = set(_cols_for("eventos_calendario"))

                data_ev = {}
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
                    data_ev["ops"] = int(ev.get("ops")) if ev.get("ops") is not None else int(ops)
                if "estado" in cols_ev:
                    data_ev["estado"] = "agendado"

                with engine.connect() as cn:
                    row_ev = cn.execute(
                        text("SELECT %s FROM eventos_calendario WHERE id_lead=:id ORDER BY %s DESC LIMIT 1" % (pk_ev, pk_ev)),
                        {"id": id_lead},
                    ).fetchone()

                if row_ev and row_ev[0] is not None:
                    evento_id = row_ev[0]
                    _update_row("eventos_calendario", pk_ev, evento_id, data_ev)
                else:
                    evento_id = _insert_row("eventos_calendario", data_ev)
            except Exception:
                evento_id = None

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

        if "calendar_start" in cols_lead:
            extra_ev_ref["calendar_start"] = ev["start_at"]
        if "calendar_end" in cols_lead:
            extra_ev_ref["calendar_end"] = ev["end_at"]

        pre_events_json = None
        try:
            if "pre_events_json" not in cols_lead:
                with engine.begin() as cn:
                    cn.execute(text("ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS pre_events_json TEXT"))
                cols_lead = set(_cols_for("leads"))
            pre_events_json = json.dumps(eventos, ensure_ascii=False)
        except Exception:
            pre_events_json = None

        _update_row(
            "leads",
            "id_lead",
            id_lead,
            {
                "id_estado": int(id_estado),
                "pendiente_agendar": False,
                **({"id_cotizacion_vigente": int(id_cot)} if id_cot else {}),
                **extra_ev_ref,
                "pre_products_text": ev["products_text"],
                "pre_montaje_text": ev["montaje_text"],
                "pre_ops": int(ev.get("ops")) if ev.get("ops") is not None else int(ops),
                "pre_title": ev["title"],
                "pre_start": ev["start_at"],
                "pre_end": ev["end_at"],
                "pre_location": ev["location"],
                "pre_telefono": telefono if telefono else "POR CONFIRMAR",
                "pre_direccion": direccion if direccion else "DIR TBD",
                "pre_description": ev["description"],
                **({"pre_events_json": pre_events_json} if pre_events_json else {}),
            },
        )

        try:
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            who = str(user.get("username") or user.get("email") or user.get("id") or "").strip() or "CRM"
            if quote_source == "manual" or not id_cot:
                note = "[CONFIRMADO %s] Cotización confirmada: MANUAL · por %s" % (stamp, who)
            else:
                note = "[CONFIRMADO %s] Cotización confirmada: %s · por %s" % (stamp, id_cot, who)
            _append_lead_notas(id_lead, note)
        except Exception:
            pass

        try:
            cliente = str(lead.get("cliente") or lead.get("nombre_cliente") or "").strip()
            marca_txt = _get_marca_nombre(int(lead.get("id_marca") or 0) or 0)
            comuna_txt = _get_comuna_nombre(int(lead.get("id_comuna") or 0) or 0)
            fecha_txt = str(lead.get("fecha_evento") or "").strip()

            resumen = "\n".join(
                [
                    "Cliente: %s" % cliente,
                    "Marca: %s" % marca_txt,
                    "Comuna: %s" % comuna_txt,
                    "Fecha evento: %s" % fecha_txt,
                    "OPS: %s" % ops,
                    "",
                    "Productos:",
                    (ev.get("products_text") or "").strip(),
                    "",
                    "Montaje / Observaciones:",
                    (ev.get("montaje_text") or "").strip(),
                ]
            ).strip()

            title = "Nuevo evento agendado · %s · %s" % (marca_txt, cliente)
            roles = [
                "ADMIN",
                "SUPERADMIN",
                "1",
                "OPERACIONES",
                "JEFE DE OPERACIONES",
                "3",
                "MICE",
                "8",
                "JEFE DE COMPRAS",
                "BODEGUERO",
                "4",
                "COMPRAS",
                "5",
                # Operadores (por si usan roles numéricos)
                "7",
                "9",
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

            if inserted_roles:
                try:
                    to = _emails_for_roles(inserted_roles)
                    if to:
                        from backend.core.email import send_email_group
                        footer = "\n\n--\nCRM Green Diamond\nMensaje automático (sin montos)\n"
                        try:
                            send_email_group(to, title, resumen + footer)
                        except Exception:
                            pass
                except Exception:
                    pass
        except Exception:
            pass

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
            # Multi-día (si aplica). Útil para UI (tabs por día / WhatsApp resumen).
            "eventos": [
                {
                    "id_evento": evento_id,
                    "day": e.get("day"),
                    "title": e.get("title"),
                    "start_at": e.get("start_at"),
                    "end_at": e.get("end_at"),
                    "location": e.get("location"),
                    "description": e.get("description"),
                    "ops": e.get("ops"),
                    "montaje_text": e.get("montaje_text"),
                    "products_text": e.get("products_text"),
                }
                for e in (eventos or [])
            ],
            "evento": {
                "id_evento": evento_id,
                "title": ev["title"],
                "start_at": ev["start_at"],
                "end_at": ev["end_at"],
                "location": ev["location"],
                "description": ev["description"],
                "ops": ev["ops"],
                "montaje_text": ev["montaje_text"],
                "products_text": ev["products_text"],
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        raise HTTPException(
            500,
            detail={
                "where": "move_lead_and_maybe_agenda",
                "type": e.__class__.__name__,
                "msg": str(e),
                "trace": traceback.format_exc().splitlines()[-8:],
            },
        )
