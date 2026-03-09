from fastapi import APIRouter, HTTPException, Body, Depends
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from pathlib import Path
import traceback
import secrets
from backend.core.db import get_connection
from backend.core.quote_assets import logo_for, normalize_marca

try:
    from backend.routers.auth import get_current_user  # type: ignore
except Exception:  # pragma: no cover
    def get_current_user():  # type: ignore
        return {"role": "Admin", "marcas": []}

router = APIRouter(prefix="/cotizador")
BUILD = "COTIZADOR-2026-03-09-01"

# Numeración por marca (inicio)
BASE_SERIES = {
    "CAMALEON": 12875,
    "DEL SABOR": 4569,
    "GOURMET": 9872,
    "EXPRESS": 6725,
}


def _estado_id(conn, like_upper: str) -> int | None:
    try:
        r = conn.execute(
            text("SELECT id_estado FROM estados_lead WHERE UPPER(nombre) LIKE :n ORDER BY id_estado LIMIT 1"),
            {"n": like_upper},
        ).scalar()
        return int(r) if r is not None else None
    except Exception:
        return None


def table_exists(conn, table: str) -> bool:
    return bool(conn.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}).scalar())


def col_exists(conn, table: str, col: str) -> bool:
    return bool(
        conn.execute(
            text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name=:t AND column_name=:c
                LIMIT 1
                """
            ),
            {"t": table, "c": col},
        ).first()
    )


def ensure_series_table(conn) -> None:
    conn.execute(text("""
      CREATE TABLE IF NOT EXISTS cotizacion_series (
        marca TEXT PRIMARY KEY,
        next_num INTEGER NOT NULL
      )
    """))


def next_num_for_marca(conn, marca: str) -> int:
    ensure_series_table(conn)
    key = (marca or "").strip().upper()
    if not key:
        key = "GENERICA"
    base = BASE_SERIES.get(key, 1)
    row = conn.execute(text("SELECT next_num FROM cotizacion_series WHERE marca=:m"), {"m": key}).fetchone()
    if not row:
        # inicia en base
        conn.execute(text("INSERT INTO cotizacion_series(marca, next_num) VALUES (:m, :n)"), {"m": key, "n": base})
        num = base
    else:
        num = int(row[0])
    # incrementa
    conn.execute(text("UPDATE cotizacion_series SET next_num=:n WHERE marca=:m"), {"n": num + 1, "m": key})
    return num


def cols_for(conn, table: str) -> set[str]:
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


def filter_existing(cols: set[str], data: dict) -> dict:
    return {k: v for k, v in data.items() if k in cols}


def safe_query(conn, sql: str):
    try:
        return conn.execute(text(sql)).mappings().all()
    except ProgrammingError:
        return []


def _producto_expr_from_cols(cols: set[str]) -> str:
    """
    Devuelve una expresión SQL segura para el nombre del producto.
    Prioriza `producto`, pero cae a `nombre` en esquemas legacy.
    """
    has_producto = "producto" in cols
    has_nombre = "nombre" in cols

    if has_producto and has_nombre:
        return "COALESCE(NULLIF(BTRIM(producto),''), NULLIF(BTRIM(nombre),''), '')"
    if has_producto:
        return "COALESCE(NULLIF(BTRIM(producto),''), '')"
    if has_nombre:
        return "COALESCE(NULLIF(BTRIM(nombre),''), '')"
    return "''"


@router.get("/version")
def version():
    return {"ok": True, "build": BUILD}


def _log500(where: str, payload: dict, err: Exception) -> str:
    """
    Loguea un stacktrace para debugging en producción y devuelve un RID para rastreo.
    """
    rid = secrets.token_hex(4)
    try:
        dbg = Path(__file__).resolve().parents[2] / "data" / "debug"
        dbg.mkdir(parents=True, exist_ok=True)
        p = {k: payload.get(k) for k in ("id_lead", "marca", "cliente", "nombre_cliente", "fecha_evento", "tipo_cliente")}
        # No logueamos items completos para no inflar logs; solo conteo y 3 ids.
        its = payload.get("items") or []
        try:
            p["items_count"] = len(its)
            p["items_sample"] = [{"id_producto": x.get("id_producto"), "cantidad": x.get("cantidad")} for x in (its[:3] if isinstance(its, list) else [])]
        except Exception:
            pass
        (dbg / "cotizador_500.log").open("a", encoding="utf-8").write(
            f"\n=== RID={rid} where={where} ===\n"
            f"payload={p}\n"
            f"err={type(err).__name__}: {str(err)[:800]}\n"
            f"{traceback.format_exc()}\n"
        )
    except Exception:
        pass
    return rid


def _product_snapshot(conn, id_producto: int) -> dict | None:
    """
    Devuelve snapshot del producto para guardar en cotizacion_items,
    sin asumir columnas fijas.
    Acepta producto en `producto` o en `nombre`.
    """
    pid = int(id_producto or 0)
    if pid <= 0:
        return None

    # Preferimos productos_vw si existe.
    if table_exists(conn, "productos_vw"):
        try:
            vw_cols = cols_for(conn, "productos_vw")
            prod_expr = _producto_expr_from_cols(vw_cols)

            desc_parts = []
            if "descripcion" in vw_cols:
                desc_parts.append("NULLIF(descripcion,'')")
            if "ingredientes" in vw_cols:
                desc_parts.append("NULLIF(ingredientes,'')")
            desc_expr = "COALESCE(" + ", ".join(desc_parts + ["''"]) + ")" if desc_parts else "''"

            marca_expr = "COALESCE(marca,'')" if "marca" in vw_cols else "''"

            row = conn.execute(
                text(
                    f"""
                    SELECT {prod_expr} AS producto,
                           {desc_expr} AS descripcion,
                           {marca_expr} AS marca
                    FROM public.productos_vw
                    WHERE id_producto=:p
                    LIMIT 1
                    """
                ),
                {"p": pid},
            ).mappings().first()

            if row and str(row.get("producto") or "").strip():
                return dict(row)
        except Exception:
            pass

    # Fallback: tabla productos.
    cols = cols_for(conn, "productos") if table_exists(conn, "productos") else set()
    if not cols:
        return None

    prod_expr = _producto_expr_from_cols(cols)

    desc_parts = []
    if "descripcion" in cols:
        desc_parts.append("NULLIF(descripcion,'')")
    if "ingredientes" in cols:
        desc_parts.append("NULLIF(ingredientes,'')")
    desc_expr = "COALESCE(" + ", ".join(desc_parts + ["''"]) + ")" if desc_parts else "''"

    marca_expr = "COALESCE(marca,'')" if "marca" in cols else "''"

    try:
        row = conn.execute(
            text(
                f"""
                SELECT {prod_expr} AS producto,
                       {desc_expr} AS descripcion,
                       {marca_expr} AS marca
                FROM public.productos
                WHERE id_producto=:p
                LIMIT 1
                """
            ),
            {"p": pid},
        ).mappings().first()
        if row and str(row.get("producto") or "").strip():
            return dict(row)
        return None
    except Exception:
        return None


@router.get("/catalogos")
def catalogos(user: dict = Depends(get_current_user)):
    with get_connection() as conn:
        marcas = []
        if table_exists(conn, "marcas"):
            if col_exists(conn, "marcas", "marca"):
                marcas = safe_query(conn, """
                  SELECT id_marca, marca, is_active,
                         COALESCE(logo_url,'') AS logo_url,
                         COALESCE(logo_path,'') AS logo_path
                  FROM public.marcas
                  WHERE is_active=true
                  ORDER BY marca ASC
                """)
            elif col_exists(conn, "marcas", "nombre"):
                marcas = safe_query(conn, """
                  SELECT id_marca, nombre AS marca, is_active,
                         COALESCE(logo_url,'') AS logo_url,
                         COALESCE(logo_path,'') AS logo_path
                  FROM public.marcas
                  WHERE is_active=true
                  ORDER BY nombre ASC
                """)

        marcas = [dict(m) for m in (marcas or [])]
        for m in marcas:
            key = normalize_marca(m.get("marca") or "")
            if key:
                if not (m.get("logo_url") or "").strip():
                    m["logo_url"] = logo_for(key, prefer_local=False)
                if not (m.get("logo_path") or "").strip():
                    m["logo_path"] = logo_for(key, prefer_local=True)

        # filtra marcas por rol (ejecutivo ve solo sus marcas)
        role = (user.get("role") or user.get("rol") or "").upper()
        user_marcas = user.get("marcas") or []
        if role in ("EJECUTIVO DE VENTAS", "VENDEDOR") and user_marcas:
            marcas = [m for m in marcas if int(m.get("id_marca") or 0) in set(user_marcas)]

        comunas = []
        if table_exists(conn, "comunas"):
            has_cost = col_exists(conn, "comunas", "costo_traslado")
            cost_expr = "COALESCE(costo_traslado,0) AS costo_traslado" if has_cost else "0 AS costo_traslado"
            if col_exists(conn, "comunas", "nombre"):
                comunas = safe_query(conn, f"""
                  SELECT id_comuna, nombre, neto, bruto, is_active, {cost_expr}
                  FROM public.comunas
                  WHERE is_active=true
                  ORDER BY nombre ASC
                """)
            elif col_exists(conn, "comunas", "comuna"):
                comunas = safe_query(conn, f"""
                  SELECT id_comuna, comuna AS nombre, neto, bruto, is_active, {cost_expr}
                  FROM public.comunas
                  WHERE is_active=true
                  ORDER BY comuna ASC
                """)

        productos = []

        # prefer view productos_vw, fallback a productos
        if table_exists(conn, "productos_vw"):
            vw_cols = cols_for(conn, "productos_vw")
            prod_col = _producto_expr_from_cols(vw_cols)

            if "descripcion" in vw_cols and "ingredientes" in vw_cols:
                desc_col = "COALESCE(NULLIF(descripcion,''), ingredientes)"
            elif "descripcion" in vw_cols:
                desc_col = "descripcion"
            elif "ingredientes" in vw_cols:
                desc_col = "ingredientes"
            else:
                desc_col = "''"

            ing_col = "ingredientes" if "ingredientes" in vw_cols else "''"
            marca_col = "marca" if "marca" in vw_cols else "''"
            costo_col = "COALESCE(costo,0)" if "costo" in vw_cols else "0"
            active_col = "COALESCE(is_active,true)" if "is_active" in vw_cols else "true"
            orden_col = "orden" if "orden" in vw_cols else "NULL"

            productos = safe_query(conn, f"""
              SELECT id_producto,
                     {prod_col} AS producto,
                     {desc_col} AS descripcion,
                     {ing_col} AS ingredientes,
                     {marca_col} AS marca,
                     {costo_col} AS costo,
                     {active_col} AS is_active,
                     {orden_col} AS orden
              FROM public.productos_vw
              WHERE {active_col}=true
                AND NULLIF(BTRIM({prod_col}), '') IS NOT NULL
              ORDER BY COALESCE({orden_col},999999) ASC, {prod_col} ASC
            """)

        elif table_exists(conn, "productos"):
            cols = cols_for(conn, "productos")
            prod_col = _producto_expr_from_cols(cols)

            if "descripcion" in cols and "ingredientes" in cols:
                desc_col = "COALESCE(NULLIF(descripcion,''), ingredientes)"
            elif "descripcion" in cols:
                desc_col = "descripcion"
            elif "ingredientes" in cols:
                desc_col = "ingredientes"
            else:
                desc_col = "''"

            ing_col = "ingredientes" if "ingredientes" in cols else "''"
            marca_col = "marca" if "marca" in cols else "''"
            costo_col = "COALESCE(costo,0)" if "costo" in cols else "0"
            active_col = "COALESCE(is_active,true)" if "is_active" in cols else "true"
            orden_col = "orden" if "orden" in cols else "NULL"

            productos = safe_query(conn, f"""
              SELECT id_producto,
                     {prod_col} AS producto,
                     {desc_col} AS descripcion,
                     {ing_col} AS ingredientes,
                     {marca_col} AS marca,
                     {costo_col} AS costo,
                     {active_col} AS is_active,
                     {orden_col} AS orden
              FROM public.productos
              WHERE {active_col}=true
                AND NULLIF(BTRIM({prod_col}), '') IS NOT NULL
              ORDER BY COALESCE({orden_col},999999) ASC, {prod_col} ASC
            """)

        # filtra productos por marcas asignadas
        if role in ("EJECUTIVO DE VENTAS", "VENDEDOR") and user_marcas:
            allowed_names = {str(m.get("marca") or "").upper() for m in marcas}
            productos = [p for p in productos if str(p.get("marca") or "").upper() in allowed_names]

        return {"marcas": list(marcas), "comunas": list(comunas), "productos": list(productos)}


@router.post("/cotizar")
def cotizar(payload: dict = Body(...)):
    """
    Payload esperado (mínimo):
    {
      "id_lead": 16,
      "items": [{"id_producto": 1, "cantidad": 2, "precio_unitario": 1000}],
      "traslado": 0,
      "descuento_valor": 0,
      "descuento_tipo": null
    }
    """
    try:
        id_lead = int(payload.get("id_lead") or 0)
        items = payload.get("items") or []
        if not id_lead or not items:
            raise HTTPException(400, "id_lead e items son requeridos")

        traslado = float(payload.get("traslado") or 0)
        descuento_valor = float(payload.get("descuento_valor") or 0)
        descuento_tipo = (payload.get("descuento_tipo") or "").strip()

        # calcula totales
        subtotal = 0.0
        for it in items:
            cant = int(it.get("cantidad") or 0)
            pu = float(it.get("precio_unitario") or 0)
            if cant <= 0:
                raise HTTPException(400, "cantidad inválida")
            subtotal += cant * pu

        if descuento_tipo == "%":
            desc = subtotal * (descuento_valor / 100.0)
        else:
            desc = descuento_valor

        neto = max(0.0, subtotal - desc)
        tipo_cliente = str(payload.get("tipo_cliente") or "").strip().upper()
        is_empresa = ("EMP" in tipo_cliente)
        base_iva = neto + traslado
        iva = round(base_iva * 0.19, 2) if is_empresa else 0.0
        total = round(base_iva + iva, 2)

        with get_connection() as conn:
            ok = conn.execute(text("SELECT 1 FROM public.leads WHERE id_lead=:id"), {"id": id_lead}).first()
            if not ok:
                raise HTTPException(404, "Lead no existe")

            marca_key = (payload.get("marca") or "").strip()
            if not marca_key:
                marca_cols = cols_for(conn, "marcas") if table_exists(conn, "marcas") else set()
                if "marca" in marca_cols:
                    sql = "SELECT COALESCE(m.marca,'') FROM public.marcas m JOIN public.leads l ON l.id_marca=m.id_marca WHERE l.id_lead=:id"
                elif "nombre" in marca_cols:
                    sql = "SELECT COALESCE(m.nombre,'') FROM public.marcas m JOIN public.leads l ON l.id_marca=m.id_marca WHERE l.id_lead=:id"
                else:
                    sql = ""
                if sql:
                    mk = conn.execute(text(sql), {"id": id_lead}).scalar()
                    marca_key = (mk or "").strip()

            numero = next_num_for_marca(conn, marca_key)

            cot_cols = cols_for(conn, "cotizaciones")
            if "version" not in cot_cols:
                conn.execute(text("ALTER TABLE public.cotizaciones ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 0"))
                cot_cols = cols_for(conn, "cotizaciones")

            id_cot = conn.execute(text("""
          INSERT INTO public.cotizaciones(
            id_lead, numero, fecha,
            traslado, descuento_valor, descuento_tipo,
            subtotal_productos, iva, total,
            created_at, updated_at, estado,
            nombre_cliente, marca, fecha_evento, tipo_cliente, version
          ) VALUES (
            :id_lead, :numero, now(),
            :traslado, :descuento_valor, :descuento_tipo,
            :subtotal, :iva, :total,
            now(), now(), 'EMITIDA',
            :nombre_cliente, :marca, :fecha_evento, :tipo_cliente, 0
          )
          RETURNING id_cotizacion
        """), {
            "id_lead": id_lead,
            "numero": int(numero),
            "traslado": traslado,
            "descuento_valor": descuento_valor,
            "descuento_tipo": descuento_tipo or None,
            "subtotal": neto,
            "iva": iva,
            "total": total,
            "nombre_cliente": payload.get("cliente") or payload.get("nombre_cliente"),
            "marca": payload.get("marca"),
            "fecha_evento": payload.get("fecha_evento"),
            "tipo_cliente": payload.get("tipo_cliente"),
            }).scalar_one()

            for it in items:
                id_prod = int(it.get("id_producto") or 0)
                cant = int(it.get("cantidad") or 0)
                pu = float(it.get("precio_unitario") or 0)

                prod = _product_snapshot(conn, id_prod)
                if not prod or not (prod.get("producto") or "").strip():
                    raise HTTPException(400, f"Producto inválido: {id_prod}")

                desc = prod.get("descripcion") or ""
                conn.execute(text("""
              INSERT INTO public.cotizacion_items(
                id_cotizacion, id_producto, producto, descripcion, cantidad, precio_unitario, total_linea, marca
              ) VALUES (
                :id_cot, :id_prod, :producto, :descripcion, :cantidad, :pu, :total_linea, :marca
              )
            """), {
                    "id_cot": int(id_cot),
                    "id_prod": id_prod,
                    "producto": prod["producto"],
                    "descripcion": desc,
                    "cantidad": cant,
                    "pu": pu,
                    "total_linea": cant * pu,
                    "marca": prod.get("marca") or "",
                })

            cotizado_id = _estado_id(conn, "%COTIZ%")
            confirmado_id = _estado_id(conn, "%CONFIRM%")
            declinado_id = _estado_id(conn, "%DECLIN%")

            conn.execute(text("""
          UPDATE public.leads
          SET id_cotizacion_vigente=:id_cot, monto_cotizado=:m, updated_at=now()
          WHERE id_lead=:id_lead
            AND COALESCE(id_estado, -1) <> COALESCE(:conf, -2)
            AND COALESCE(id_estado, -1) <> COALESCE(:decl, -3)
        """), {"id_cot": int(id_cot), "id_lead": id_lead, "m": neto, "conf": confirmado_id, "decl": declinado_id})

            try:
                conn.execute(
                    text(
                        """
                        UPDATE public.leads
                        SET num_cotizacion = COALESCE(NULLIF(num_cotizacion,''), :num),
                            id_estado = COALESCE(:cot, id_estado),
                            updated_at = now()
                        WHERE id_lead=:id_lead
                          AND COALESCE(id_estado, -1) <> COALESCE(:conf, -2)
                          AND COALESCE(id_estado, -1) <> COALESCE(:decl, -3)
                        """
                    ),
                    {
                        "id_lead": id_lead,
                        "num": str(int(numero)),
                        "cot": cotizado_id,
                        "conf": confirmado_id,
                        "decl": declinado_id,
                    },
                )
            except Exception:
                pass

            conn.commit()
            return {"ok": True, "id_cotizacion": int(id_cot), "numero": int(numero), "neto": neto, "iva": iva, "total": total}
    except HTTPException:
        raise
    except Exception as e:
        rid = _log500("cotizar", payload or {}, e)
        raise HTTPException(500, f"Internal Server Error (cotizador). RID={rid}")


@router.put("/cotizaciones/{id_cotizacion}")
def actualizar_cotizacion(id_cotizacion: int, payload: dict = Body(...)):
    try:
        id_lead = int(payload.get("id_lead") or 0)
        items = payload.get("items") or []
        if not id_lead or not items:
            raise HTTPException(400, "id_lead e items son requeridos")

        traslado = float(payload.get("traslado") or 0)
        descuento_valor = float(payload.get("descuento_valor") or 0)
        descuento_tipo = (payload.get("descuento_tipo") or "").strip()

        subtotal = 0.0
        for it in items:
            cant = int(it.get("cantidad") or 0)
            pu = float(it.get("precio_unitario") or 0)
            if cant <= 0:
                raise HTTPException(400, "cantidad inválida")
            subtotal += cant * pu

        if descuento_tipo == "%":
            desc = subtotal * (descuento_valor / 100.0)
        else:
            desc = descuento_valor

        neto = max(0.0, subtotal - desc)
        tipo_cliente = str(payload.get("tipo_cliente") or "").strip().upper()
        is_empresa = ("EMP" in tipo_cliente)
        base_iva = neto + traslado
        iva = round(base_iva * 0.19, 2) if is_empresa else 0.0
        total = round(base_iva + iva, 2)

        with get_connection() as conn:
            row = conn.execute(
                text("SELECT * FROM public.cotizaciones WHERE id_cotizacion=:id"),
                {"id": id_cotizacion},
            ).mappings().first()
            if not row:
                raise HTTPException(404, "Cotización no existe")
            if int(row.get("id_lead") or 0) != id_lead:
                raise HTTPException(400, "Cotización no pertenece al lead")

            cot_cols = cols_for(conn, "cotizaciones")
            if "version" not in cot_cols:
                conn.execute(text("ALTER TABLE public.cotizaciones ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 0"))
                cot_cols = cols_for(conn, "cotizaciones")

            prev_version = int(row.get("version") or 0)
            new_version = prev_version + 1

            insert_data = {
                "id_lead": id_lead,
                "numero": row.get("numero") or 0,
                "fecha": row.get("fecha"),
                "traslado": traslado,
                "descuento_valor": descuento_valor,
                "descuento_tipo": descuento_tipo or None,
                "subtotal_productos": neto,
                "subtotal": neto,
                "iva": iva,
                "total": total,
                "created_at": row.get("created_at"),
                "updated_at": None,
                "estado": row.get("estado") or "EMITIDA",
                "nombre_cliente": payload.get("cliente") or payload.get("nombre_cliente") or row.get("nombre_cliente"),
                "marca": payload.get("marca") or row.get("marca"),
                "fecha_evento": payload.get("fecha_evento") or row.get("fecha_evento"),
                "tipo_cliente": payload.get("tipo_cliente") or row.get("tipo_cliente"),
                "version": new_version,
            }
            insert_data = filter_existing(cot_cols, insert_data)

            cols_sql = ", ".join(insert_data.keys())
            vals_sql = ", ".join([f":{k}" for k in insert_data.keys()])
            q = text(f"INSERT INTO public.cotizaciones ({cols_sql}) VALUES ({vals_sql}) RETURNING id_cotizacion")
            new_id = conn.execute(q, insert_data).scalar_one()

            conn.execute(text("DELETE FROM public.cotizacion_items WHERE id_cotizacion=:id"), {"id": new_id})
            for it in items:
                id_prod = int(it.get("id_producto") or 0)
                cant = int(it.get("cantidad") or 0)
                pu = float(it.get("precio_unitario") or 0)
                prod = _product_snapshot(conn, id_prod)
                if not prod or not (prod.get("producto") or "").strip():
                    raise HTTPException(400, f"Producto inválido: {id_prod}")
                desc = prod.get("descripcion") or ""
                conn.execute(
                    text(
                        """
                        INSERT INTO public.cotizacion_items(
                          id_cotizacion, id_producto, producto, descripcion, cantidad, precio_unitario, total_linea, marca
                        ) VALUES (
                          :id_cot, :id_prod, :producto, :descripcion, :cantidad, :pu, :total_linea, :marca
                        )
                        """
                    ),
                    {
                        "id_cot": int(new_id),
                        "id_prod": id_prod,
                        "producto": prod["producto"],
                        "descripcion": desc,
                        "cantidad": cant,
                        "pu": pu,
                        "total_linea": cant * pu,
                        "marca": prod.get("marca") or "",
                    },
                )

            cotizado_id = _estado_id(conn, "%COTIZ%")
            confirmado_id = _estado_id(conn, "%CONFIRM%")
            declinado_id = _estado_id(conn, "%DECLIN%")
            conn.execute(
                text(
                    """
                    UPDATE public.leads
                    SET id_cotizacion_vigente=:id_cot, monto_cotizado=:m, updated_at=now()
                    WHERE id_lead=:id_lead
                      AND COALESCE(id_estado, -1) <> COALESCE(:conf, -2)
                      AND COALESCE(id_estado, -1) <> COALESCE(:decl, -3)
                    """
                ),
                {"id_cot": int(new_id), "id_lead": id_lead, "m": neto, "conf": confirmado_id, "decl": declinado_id},
            )
            try:
                num2 = conn.execute(
                    text("SELECT numero FROM public.cotizaciones WHERE id_cotizacion=:id"),
                    {"id": int(new_id)},
                ).scalar()
                num_text = str(int(num2)) if num2 is not None else None
                if num_text:
                    conn.execute(
                        text(
                            """
                            UPDATE public.leads
                            SET num_cotizacion = COALESCE(NULLIF(num_cotizacion,''), :num),
                                id_estado = COALESCE(:cot, id_estado),
                                updated_at = now()
                            WHERE id_lead=:id_lead
                              AND COALESCE(id_estado, -1) <> COALESCE(:conf, -2)
                              AND COALESCE(id_estado, -1) <> COALESCE(:decl, -3)
                            """
                        ),
                        {
                            "id_lead": id_lead,
                            "num": num_text,
                            "cot": cotizado_id,
                            "conf": confirmado_id,
                            "decl": declinado_id,
                        },
                    )
            except Exception:
                pass

            conn.commit()
            return {"ok": True, "id_cotizacion": int(new_id), "neto": neto, "iva": iva, "total": total, "version": new_version}
    except HTTPException:
        raise
    except Exception as e:
        rid = _log500("actualizar_cotizacion", payload or {}, e)
        raise HTTPException(500, f"Internal Server Error (cotizador). RID={rid}")