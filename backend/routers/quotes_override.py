# backend/routers/quotes_override.py
from typing import Optional, Dict, Any, List
import json
import time
import os

from fastapi import APIRouter, Query, HTTPException, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, FileResponse
from sqlalchemy import text

from backend.core.db import get_connection
from backend.routers.auth import get_current_user
from backend.core.quote_assets import PDF_ASSETS, DRIVE_ASSET_FOLDERS, logo_for, normalize_marca
import base64
import requests

from pathlib import Path
from datetime import datetime
import shutil
import subprocess
import tempfile
from urllib.parse import unquote

router = APIRouter(prefix="/quotes", tags=["quotes"])


def _table_exists(table: str) -> bool:
    with get_connection() as cn:
        return bool(cn.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}).scalar())


def _cols_for(table: str) -> List[str]:
    with get_connection() as cn:
        rows = cn.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name=:t
                """
            ),
            {"t": table},
        ).fetchall()
    return [r[0] for r in rows]


def _has(cols: List[str], col: str) -> bool:
    return col in cols


@router.get("/history")
def history(
    id_lead: Optional[int] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    q: str = Query(default=""),
    user: dict = Depends(get_current_user),
):
    if not _table_exists("cotizaciones"):
        return {"items": []}

    cols = _cols_for("cotizaciones")

    join_sql = ""
    has_id_lead = _has(cols, "id_lead")
    has_leads = _table_exists("leads") and has_id_lead
    has_marcas = _table_exists("marcas") and has_leads
    leads_cols = _cols_for("leads") if has_leads else []
    marcas_cols = _cols_for("marcas") if has_marcas else []

    fecha_expr = "NULL"
    if _has(cols, "fecha") and _has(cols, "created_at"):
        fecha_expr = "COALESCE(c.fecha, c.created_at)"
    elif _has(cols, "fecha"):
        fecha_expr = "c.fecha"
    elif _has(cols, "created_at"):
        fecha_expr = "c.created_at"

    fecha_evento_expr = "NULL"
    if _has(cols, "fecha_evento") and has_leads and ("fecha_evento" in leads_cols):
        fecha_evento_expr = "COALESCE(c.fecha_evento, l.fecha_evento)"
    elif _has(cols, "fecha_evento"):
        fecha_evento_expr = "c.fecha_evento"
    elif has_leads and ("fecha_evento" in leads_cols):
        fecha_evento_expr = "l.fecha_evento"
    if has_leads:
        join_sql += " LEFT JOIN leads l ON l.id_lead = c.id_lead "
    if has_marcas and ("id_marca" in leads_cols):
        join_sql += " LEFT JOIN marcas m ON m.id_marca = l.id_marca "

    nombre_expr = "''"
    if _has(cols, "nombre_cliente"):
        nombre_expr = "COALESCE(c.nombre_cliente, l.cliente)" if has_leads and ("cliente" in leads_cols) else "c.nombre_cliente"

    marca_expr = "''"
    if _has(cols, "marca"):
        if has_marcas:
            marca_col = "nombre" if "nombre" in marcas_cols else ("marca" if "marca" in marcas_cols else None)
            if marca_col:
                # preferir marca actual del lead (marcas) sobre la guardada en la cotización
                marca_expr = f"COALESCE(m.{marca_col}, c.marca)"
            else:
                marca_expr = "c.marca"
        else:
            marca_expr = "c.marca"

    subtotal_expr = "0"
    if _has(cols, "subtotal_productos"):
        subtotal_expr = "c.subtotal_productos"
    elif _has(cols, "subtotal"):
        subtotal_expr = "c.subtotal"

    iva_expr = "0"
    if _has(cols, "iva"):
        # Regla negocio: IVA solo aplica si tipo_cliente es EMPRESA.
        if _has(cols, "tipo_cliente"):
            iva_expr = "CASE WHEN UPPER(COALESCE(c.tipo_cliente,'')) LIKE '%EMP%' THEN COALESCE(c.iva,0) ELSE 0 END"
        else:
            iva_expr = "COALESCE(c.iva,0)"

    traslado_expr = "0"
    if _has(cols, "traslado"):
        traslado_expr = "c.traslado"

    # Total: usar columna si existe (incluye descuento/IVA/traslado). Para compatibilidad legacy,
    # si no existe, calculamos una aproximación.
    total_expr = f"(COALESCE({subtotal_expr},0) + COALESCE({traslado_expr},0) + COALESCE({iva_expr},0))"
    if _has(cols, "total"):
        total_expr = "COALESCE(c.total,0)"

    descuento_valor_expr = "0"
    if _has(cols, "descuento_valor"):
        descuento_valor_expr = "COALESCE(c.descuento_valor,0)"

    descuento_tipo_expr = "''"
    if _has(cols, "descuento_tipo"):
        descuento_tipo_expr = "COALESCE(c.descuento_tipo,'')"

    # Para mostrar neto consistente en historial, preferimos derivarlo del total:
    # neto = total - traslado - iva (iva real, no solo la regla visual).
    iva_raw_expr = "0"
    if _has(cols, "iva"):
        iva_raw_expr = "COALESCE(c.iva,0)"
    neto_calc_expr = f"GREATEST(0, COALESCE({total_expr},0) - COALESCE({traslado_expr},0) - COALESCE({iva_raw_expr},0))"

    # Descuento absoluto: si es %, lo derivamos desde neto (evita depender de subtotal legacy).
    descuento_abs_expr = f"""
      CASE
        WHEN {descuento_tipo_expr}='%' AND COALESCE({descuento_valor_expr},0) > 0 AND COALESCE({descuento_valor_expr},0) < 100
          THEN ROUND(({neto_calc_expr} * COALESCE({descuento_valor_expr},0) / (100 - COALESCE({descuento_valor_expr},0))), 2)
        ELSE ROUND(COALESCE({descuento_valor_expr},0), 2)
      END
    """
    subtotal_bruto_calc_expr = f"GREATEST(0, ({neto_calc_expr} + COALESCE({descuento_abs_expr},0)))"

    version_expr = "0"
    if _has(cols, "version"):
        version_expr = "c.version"

    # revision index (0 = original, 1+ = modificaciones)
    revision_expr = "0"
    if _has(cols, "id_cotizacion"):
        partition_cols = ["COALESCE(c.numero, c.id_cotizacion)"]
        if has_id_lead:
            partition_cols.append("c.id_lead")
        if _has(cols, "marca"):
            partition_cols.append("c.marca")
        partition_sql = ", ".join(partition_cols)
        revision_expr = f"(ROW_NUMBER() OVER (PARTITION BY {partition_sql} ORDER BY c.id_cotizacion) - 1)"

    role = (user.get("role") or "").upper()
    marcas_ids = [int(x) for x in (user.get("marcas") or []) if str(x).isdigit()]
    only_own = role in ("EJECUTIVO DE VENTAS", "VENDEDOR")

    where = []
    params: Dict[str, Any] = {"limit": limit, "offset": offset}
    if id_lead and has_id_lead:
        where.append("c.id_lead = :id_lead")
        params["id_lead"] = id_lead
    elif id_lead and not has_id_lead:
        return jsonable_encoder({"items": []})
    if q:
        q_parts = []
        if _has(cols, "nombre_cliente"):
            q_parts.append("COALESCE(c.nombre_cliente,'') ILIKE :q")
        if _has(cols, "marca"):
            q_parts.append("COALESCE(c.marca,'') ILIKE :q")
        if has_leads and ("cliente" in leads_cols):
            q_parts.append("COALESCE(l.cliente,'') ILIKE :q")
        if q_parts:
            where.append("(" + " OR ".join(q_parts) + ")")
            params["q"] = f"%{q}%"
    if only_own:
        if not marcas_ids:
            return jsonable_encoder({"items": []})
        if has_leads:
            where.append("l.id_marca = ANY(:marcas)")
            params["marcas"] = marcas_ids

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    sql = f"""
        SELECT
          c.id_cotizacion,
          c.id_lead,
          COALESCE(c.numero, 0) AS numero,
          CAST({fecha_expr} AS text) AS fecha_emision,
          CAST({fecha_evento_expr} AS text) AS fecha_evento,
          {nombre_expr} AS nombre_cliente,
          {marca_expr} AS marca,
          {subtotal_expr} AS subtotal,
          {subtotal_bruto_calc_expr} AS subtotal_bruto,
          {descuento_valor_expr} AS descuento_valor,
          {descuento_tipo_expr} AS descuento_tipo,
          {descuento_abs_expr} AS descuento_abs,
          {neto_calc_expr} AS neto,
          {iva_expr} AS iva,
          {traslado_expr} AS traslado,
          {total_expr} AS total,
          {version_expr} AS version,
          {revision_expr} AS revision
        FROM cotizaciones c
        {join_sql}
        {where_sql}
        ORDER BY c.id_cotizacion DESC
        LIMIT :limit OFFSET :offset
    """
    with get_connection() as cn:
        rows = cn.execute(text(sql), params).mappings().all()

    return jsonable_encoder({"items": list(rows)})


@router.get("/{id_cotizacion}")
def get_cotizacion(id_cotizacion: int):
    if not _table_exists("cotizaciones"):
        raise HTTPException(404, "cotizaciones no existe")
    cols = _cols_for("cotizaciones")
    if "id_cotizacion" not in cols:
        raise HTTPException(500, "cotizaciones sin PK id_cotizacion")

    with get_connection() as cn:
        row = cn.execute(
            text("SELECT * FROM cotizaciones WHERE id_cotizacion=:id"),
            {"id": id_cotizacion},
        ).mappings().first()
    if not row:
        raise HTTPException(404, "Cotización no existe")
    data = dict(row)

    # calcula revision (0 = original)
    try:
        num = data.get("numero")
        if num is not None:
            cols = _cols_for("cotizaciones")
            where = ["numero=:n", "id_cotizacion < :id"]
            params: Dict[str, Any] = {"n": num, "id": id_cotizacion}
            if "id_lead" in cols and data.get("id_lead") is not None:
                where.append("id_lead=:lid")
                params["lid"] = data.get("id_lead")
            if "marca" in cols and data.get("marca"):
                where.append("marca=:m")
                params["m"] = data.get("marca")
            q = text(f"SELECT COUNT(*) FROM cotizaciones WHERE {' AND '.join(where)}")
            rev = cn.execute(q, params).scalar() or 0
            data["revision"] = int(rev)
    except Exception:
        pass

    return {"ok": True, "cotizacion": data}


@router.get("/{id_cotizacion}/items")
def get_items(id_cotizacion: int):
    if _table_exists("cotizacion_items"):
        with get_connection() as cn:
            rows = cn.execute(
                text(
                    """
                    SELECT id_producto, producto, descripcion, cantidad, precio_unitario, total_linea, marca
                    FROM cotizacion_items
                    WHERE id_cotizacion=:id
                    """
                ),
                {"id": id_cotizacion},
            ).mappings().all()
        return {"items": list(rows)}

    # fallback legacy: cotizaciones_detalle
    if _table_exists("cotizaciones_detalle"):
        cols = _cols_for("cotizaciones_detalle")
        prod_col = next((c for c in ("producto", "nombre_producto", "detalle", "descripcion", "nombre") if c in cols), None)
        qty_col = next((c for c in ("cantidad", "qty", "unidades", "cant") if c in cols), None)
        price_col = next((c for c in ("precio_unitario", "precio", "valor") if c in cols), None)
        if not prod_col or not qty_col:
            return {"items": []}
        price_sql = f", {price_col} AS precio_unitario" if price_col else ""
        with get_connection() as cn:
            rows = cn.execute(
                text(
                    f"""
                    SELECT {prod_col} AS producto, {qty_col} AS cantidad {price_sql}
                    FROM cotizaciones_detalle
                    WHERE id_cotizacion=:id
                    """
                ),
                {"id": id_cotizacion},
            ).mappings().all()
        return {"items": list(rows)}

    return {"items": []}


@router.get("/{id_cotizacion}/pdf")
def pdf_placeholder(
    id_cotizacion: int,
    debug: int = Query(default=0, ge=0, le=1),
    strict_assets: int = Query(default=0, ge=0, le=1),
    download: int = Query(default=0, ge=0, le=1),
    refresh: int = Query(default=0, ge=0, le=1),
):
    try:
        from weasyprint import HTML  # type: ignore
    except Exception:
        html = f"""
        <html><head><title>Cotización #{id_cotizacion}</title></head>
        <body style="font-family:Arial,sans-serif">
          <h2>Cotización #{id_cotizacion}</h2>
          <p>WeasyPrint no está instalado. Instala dependencias para generar PDF.</p>
        </body></html>
        """
        return HTMLResponse(html)

    def _disp(filename: str) -> str:
        # inline para "Ver", attachment para "Descargar"
        kind = "attachment" if int(download or 0) == 1 else "inline"
        safe = (filename or "cotizacion.pdf").replace('"', "")
        return f'{kind}; filename="{safe}"'

    refresh_assets = int(refresh or 0) == 1

    # cargar cotización + items + lead
    with get_connection() as cn:
        cot = cn.execute(
            text("SELECT * FROM cotizaciones WHERE id_cotizacion=:id"),
            {"id": id_cotizacion},
        ).mappings().first()
        if not cot:
            raise HTTPException(404, "Cotización no existe")
        cot = dict(cot)

        # Si ya hay un PDF generado y existe en disco, lo servimos directo
        # (evita regenerar y evita caer por assets rotos).
        try:
            p0 = (cot.get("pdf_path") or "").strip()
            if p0:
                root = Path(__file__).resolve().parents[2]
                p = Path(p0)
                if not p.is_absolute():
                    p = root / p0
                if p.exists() and p.is_file() and (not refresh_assets) and (not debug):
                    return FileResponse(
                        str(p),
                        media_type="application/pdf",
                        headers={"Content-Disposition": _disp(p.name)},
                    )
        except Exception:
            pass

        items = []
        if _table_exists("cotizacion_items"):
            items = cn.execute(
                text(
                    """
                    SELECT producto, descripcion, cantidad, precio_unitario, total_linea
                    FROM cotizacion_items
                    WHERE id_cotizacion=:id
                    """
                ),
                {"id": id_cotizacion},
            ).mappings().all()

        lead = None
        if _table_exists("leads") and "id_lead" in cot:
            lead = cn.execute(
                text("SELECT * FROM leads WHERE id_lead=:id"),
                {"id": cot["id_lead"]},
            ).mappings().first()
            if lead:
                lead = dict(lead)

        comuna = ""
        if lead and _table_exists("comunas") and lead.get("id_comuna"):
            try:
                comuna = cn.execute(
                    text("SELECT nombre FROM comunas WHERE id_comuna=:id"),
                    {"id": lead.get("id_comuna")},
                ).scalar() or ""
            except Exception:
                comuna = ""

    def _get_marca(obj) -> str:
        if not obj or not isinstance(obj, dict):
            return ""
        for k in ("marca", "marca_evento", "marca_nombre", "nombre_marca", "marca_lead", "marca_cliente"):
            v = obj.get(k)
            if v:
                return str(v).strip()
        return ""

    def _infer_marca_from_assets() -> str:
        root = Path(__file__).resolve().parents[2]
        bases = [
            root / "web" / "images" / "quote_assets",
            root / "data" / "quote_assets",
            root / "quote_assets",
        ]
        for base in bases:
            if not base.exists():
                continue
            dirs = []
            for d in base.iterdir():
                if not d.is_dir():
                    continue
                if d.name.lower() in ("cache",):
                    continue
                # exigir que tenga al menos portada/cotizacion
                if not (d / "portada.png").exists() and not (d / "cotizacion.png").exists():
                    continue
                dirs.append(d.name)
            if not dirs:
                continue
            # si hay una sola marca, usarla
            if len(dirs) == 1:
                return normalize_marca(dirs[0])
        return ""

    # Preferir marca actual del lead sobre la guardada en la cotización
    marca_raw = (_get_marca(lead) or _get_marca(cot) or "").strip()
    marca_key = normalize_marca(marca_raw)
    if not marca_key:
        marca_key = _infer_marca_from_assets()
    def _infer_marca_from_items() -> str:
        if not _table_exists("cotizacion_items"):
            return ""
        try:
            cols_it = _cols_for("cotizacion_items")
            if "marca" not in cols_it:
                return ""
            with get_connection() as cn2:
                rows = cn2.execute(
                    text("SELECT DISTINCT marca FROM cotizacion_items WHERE id_cotizacion=:id"),
                    {"id": id_cotizacion},
                ).fetchall()
            vals = [normalize_marca(r[0]) for r in rows if r and r[0]]
            vals = [v for v in vals if v]
            uniq = list(dict.fromkeys(vals))
            return uniq[0] if len(uniq) == 1 else ""
        except Exception:
            return ""

    if not marca_key:
        marca_key = _infer_marca_from_items()

    assets = PDF_ASSETS.get(marca_key, {})
    # para PDF preferimos logo remoto (mejor compatibilidad que webp local)
    logo = logo_for(marca_key, prefer_local=False)

    # Numero visible (correlativo):
    # - Preferimos `cotizaciones.numero` (secuencial por marca).
    # - Si está vacío (legacy), usamos `leads.num_cotizacion`.
    # - Último fallback: id_cotizacion.
    numero = cot.get("numero")
    try:
        if numero is not None and str(numero).strip() != "":
            numero = int(numero)
    except Exception:
        numero = None
    if not numero:
        try:
            nlead = (lead.get("num_cotizacion") if lead else "") or ""
            nlead = str(nlead).strip()
            if nlead.isdigit():
                numero = int(nlead)
        except Exception:
            numero = None
    if not numero:
        numero = cot.get("id_cotizacion")
    # revision (0=original)
    revision = 0
    try:
        if cot.get("numero") is not None:
            cols = _cols_for("cotizaciones")
            where = ["numero=:n", "id_cotizacion < :id"]
            params: Dict[str, Any] = {"n": cot.get("numero"), "id": id_cotizacion}
            if "id_lead" in cols and cot.get("id_lead") is not None:
                where.append("id_lead=:lid")
                params["lid"] = cot.get("id_lead")
            if "marca" in cols and cot.get("marca"):
                where.append("marca=:m")
                params["m"] = cot.get("marca")
            q = text(f"SELECT COUNT(*) FROM cotizaciones WHERE {' AND '.join(where)}")
            revision = int(cn.execute(q, params).scalar() or 0)
    except Exception:
        revision = int(cot.get("version") or 0) if "version" in cot else 0

    nro_text = f"{numero}" + (f" ({revision})" if revision > 0 else "")

    fecha_evento = cot.get("fecha_evento")
    if fecha_evento:
        try:
            fecha_evento = str(fecha_evento)[:10]
        except Exception:
            fecha_evento = ""
    else:
        fecha_evento = ""

    nombre_cliente = cot.get("nombre_cliente") or (lead.get("cliente") if lead else "")
    direccion = (lead.get("direccion") if lead else "") or ""
    telefono = (lead.get("telefono") if lead else "") or ""

    def _safe_filename_part(s: str) -> str:
        s = (s or "").strip()
        if not s:
            return ""
        # keep simple: letters, numbers, spaces, dash, underscore
        out = []
        for ch in s:
            if ch.isalnum() or ch in (" ", "-", "_"):
                out.append(ch)
        s2 = "".join(out).strip()
        s2 = " ".join(s2.split())
        return s2[:60]

    # path por marca y mes
    base_dir = Path(__file__).resolve().parents[2] / "data" / "quotes"
    yymm = (fecha_evento[:7] if fecha_evento else datetime.now().strftime("%Y-%m"))
    out_dir = base_dir / (marca_key or "GENERICA") / yymm
    out_dir.mkdir(parents=True, exist_ok=True)
    cliente_part = _safe_filename_part(str(nombre_cliente or ""))
    vpart = (f" ({revision})" if revision > 0 else "")
    # Nombre final para descarga/archivo:
    # "Cotizacion 12947 (1) - Oscar Mendoza.pdf"
    filename = f"Cotizacion {numero}{vpart}" + (f" - {cliente_part}" if cliente_part else "") + ".pdf"
    pdf_path = out_dir / filename

    def _drive_assets_ttl_seconds() -> int:
        """
        TTL para consultar Drive assets.
        Importante: Drive permite reemplazar el archivo manteniendo ID/nombre,
        por lo que un TTL muy alto hace que el cliente "no vea" cambios.
        """
        for k in ("GD_DRIVE_ASSETS_TTL_SECONDS", "DRIVE_ASSETS_TTL_SECONDS"):
            v = (os.getenv(k) or "").strip()
            if v:
                try:
                    n = int(float(v))
                    if n < 0:
                        n = 0
                    return n
                except Exception:
                    pass
        # Default: 60s (balance entre frescura y no pegarle tanto a Drive).
        return 60

    def _latest_drive_cache_mtime(mkey: str) -> float:
        """
        Devuelve el mtime más reciente de los assets cacheados para la marca.
        Si cambia cualquiera de los fondos/logo, se fuerza regeneración del PDF.
        """
        if not mkey:
            return 0.0
        try:
            base = Path(__file__).resolve().parents[2] / "data" / "quote_assets" / "drive_cache"
            if not base.exists():
                return 0.0
            pats = [
                f"{mkey}_portada_*",
                f"{mkey}_cotizacion_*",
                f"{mkey}_terminos_*",
                f"{mkey}_banco_*",
                f"{mkey}_logo_*",
            ]
            mt = 0.0
            for pat in pats:
                for p in base.glob(pat):
                    try:
                        if p.is_file():
                            mt = max(mt, float(p.stat().st_mtime))
                    except Exception:
                        continue
            return mt
        except Exception:
            return 0.0

    # En modo refresh, eliminamos el PDF existente para evitar servir un archivo viejo
    # si el render falla (p. ej. por Pango/WeasyPrint) y caemos en fallbacks.
    if refresh_assets:
        try:
            if pdf_path.exists() and pdf_path.is_file():
                pdf_path.unlink()
        except Exception:
            pass

    # Si ya existe el PDF cacheado, servirlo directo.
    try:
        if pdf_path.exists() and pdf_path.is_file() and (not debug) and (not refresh_assets):
            must_regen = False
            # Si la marca usa Drive assets, aseguramos que el cache esté actualizado
            # (sin forzar refresh) y regeneramos PDF si alguno de los assets cambió.
            try:
                folder_id = DRIVE_ASSET_FOLDERS.get(marca_key or "", "")
                if folder_id:
                    from backend.core.drive_assets import resolve_brand_assets_from_folder
                    resolve_brand_assets_from_folder(
                        marca_key,
                        folder_id,
                        refresh=False,
                        ttl_seconds=_drive_assets_ttl_seconds(),
                    )
                    pdf_ts = float(pdf_path.stat().st_mtime)
                    assets_ts = _latest_drive_cache_mtime(marca_key or "")
                    if assets_ts > (pdf_ts + 1.0):
                        must_regen = True
            except Exception:
                # Si falla la verificación, NO rompemos la descarga.
                must_regen = False

            if not must_regen:
                return FileResponse(
                    str(pdf_path),
                    media_type="application/pdf",
                    headers={"Content-Disposition": _disp(pdf_path.name)},
                )
    except Exception:
        pass

    def _img_data(url: str) -> str:
        if not url:
            return ""
        # local static asset (e.g. /web/images/...)
        try:
            if url.startswith("/web/"):
                root = Path(__file__).resolve().parents[2]
                p = root / url.lstrip("/")
                if p.exists():
                    # usa ruta absoluta del archivo (WeasyPrint la resuelve con base_url)
                    return p.as_uri()
        except Exception:
            pass
        # cache por url (drive id)
        cache_path = None
        try:
            if "drive.google.com" in url or "googleusercontent.com" in url:
                from backend.core.quote_assets import _drive_id
                fid = _drive_id(url)
                cache_dir = Path(__file__).resolve().parents[2] / "data" / "quote_assets" / "cache"
                cache_dir.mkdir(parents=True, exist_ok=True)
                cache_path = cache_dir / f"{fid}"
                # Cache con TTL: si el asset cambió en Drive (mismo id), refrescamos automático.
                # En modo refresh, ignoramos TTL y forzamos refetch.
                TTL_SECONDS = int(os.getenv("QUOTE_ASSET_TTL_SECONDS", "600"))  # 10 min default
                def _fresh(fp: Path) -> bool:
                    try:
                        if refresh_assets:
                            return False
                        return (time.time() - fp.stat().st_mtime) < TTL_SECONDS
                    except Exception:
                        return False
                # si hay archivo cacheado con extensión, úsalo directo
                for ext in (".png", ".jpg", ".jpeg"):
                    fp = cache_path.with_suffix(ext)
                    if fp.exists() and _fresh(fp):
                        return fp.as_uri()
                # si solo hay webp, intenta convertir (si hay PIL)
                fp_webp = cache_path.with_suffix(".webp")
                if fp_webp.exists() and _fresh(fp_webp):
                    try:
                        from PIL import Image  # type: ignore
                        import io
                        img = Image.open(fp_webp)
                        buf = io.BytesIO()
                        img.save(buf, format="PNG")
                        png_path = cache_path.with_suffix(".png")
                        png_path.write_bytes(buf.getvalue())
                        return png_path.as_uri()
                    except Exception:
                        return ""
                if cache_path.exists() and (not refresh_assets) and _fresh(cache_path):
                    data = cache_path.read_bytes()
                    # guess mime (sin imghdr; fue removido en Python 3.13)
                    def _guess_kind(buf: bytes) -> str:
                        b = buf or b""
                        # PNG
                        if b.startswith(b"\x89PNG\r\n\x1a\n"):
                            return "png"
                        # JPEG
                        if b.startswith(b"\xff\xd8"):
                            return "jpeg"
                        # GIF
                        if b.startswith(b"GIF87a") or b.startswith(b"GIF89a"):
                            return "gif"
                        # WEBP
                        if len(b) >= 12 and b[:4] == b"RIFF" and b[8:12] == b"WEBP":
                            return "webp"
                        return ""
                    kind = _guess_kind(data)
                    # detectar webp por magia (redundante, pero deja compat con assets raros)
                    is_webp = data[:4] == b"RIFF" and data[8:12] == b"WEBP"
                    if kind == "jpeg":
                        mime = "image/jpeg"
                    elif kind == "webp" or is_webp:
                        # intenta convertir a PNG para WeasyPrint
                        try:
                            from PIL import Image  # type: ignore
                            import io
                            img = Image.open(io.BytesIO(data))
                            buf = io.BytesIO()
                            img.save(buf, format="PNG")
                            data = buf.getvalue()
                            mime = "image/png"
                        except Exception:
                            return ""
                    else:
                        mime = "image/png"
                    b64 = base64.b64encode(data).decode("ascii")
                    return f"data:{mime};base64,{b64}"
        except Exception:
            cache_path = None
        def _save_cache(content: bytes):
            try:
                if cache_path:
                    cache_path.write_bytes(content)
            except Exception:
                pass

        def _img_from_resp(resp):
            if resp.status_code != 200:
                return ""
            mime = (resp.headers.get("Content-Type", "") or "image/png").split(";")[0]
            content = resp.content
            # WebP -> PNG (WeasyPrint suele fallar con webp)
            if mime == "image/webp" or content[:4] == b"RIFF":
                try:
                    from PIL import Image  # type: ignore
                    import io
                    img = Image.open(io.BytesIO(content))
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    content = buf.getvalue()
                    mime = "image/png"
                except Exception:
                    pass
            if mime.startswith("image/"):
                # guarda como archivo local y retorna file:// (más estable en WeasyPrint)
                try:
                    if cache_path:
                        ext = ".png"
                        if mime == "image/jpeg":
                            ext = ".jpg"
                        elif mime == "image/png":
                            ext = ".png"
                        elif mime == "image/webp":
                            ext = ".webp"
                        file_path = cache_path.with_suffix(ext)
                        # Evitar tocar mtime si el contenido no cambió (así el PDF cache no se invalida siempre).
                        try:
                            if file_path.exists() and file_path.read_bytes() == content:
                                return file_path.as_uri()
                        except Exception:
                            pass
                        file_path.write_bytes(content)
                        return file_path.as_uri()
                except Exception:
                    pass
                _save_cache(content)
                b64 = base64.b64encode(content).decode("ascii")
                return f"data:{mime};base64,{b64}"
            return ""

        try:
            def _bust(u: str) -> str:
                if not refresh_assets:
                    return u
                sep = "&" if "?" in u else "?"
                return u + f"{sep}v={int(time.time())}"

            r = requests.get(_bust(url), timeout=12, headers={"User-Agent": "Mozilla/5.0"})
            img = _img_from_resp(r)
            if img:
                return img

            # si devuelve HTML, intenta con link directo de Drive
            try:
                from backend.core.quote_assets import drive_direct
                direct = drive_direct(url)
                r2 = requests.get(_bust(direct), timeout=12, headers={"User-Agent": "Mozilla/5.0"})
                img2 = _img_from_resp(r2)
                if img2:
                    return img2

                # si sigue siendo HTML, intentar extraer link de confirmación
                if "text/html" in (r2.headers.get("Content-Type","") or ""):
                    import re, html as ihtml
                    html_txt = r2.text or ""
                    m = re.search(r'href="(/uc\\?export=download[^"]+)"', html_txt)
                    if m:
                        href = ihtml.unescape(m.group(1))
                        confirm_url = "https://drive.google.com" + href
                        r3 = requests.get(_bust(confirm_url), timeout=12, headers={"User-Agent": "Mozilla/5.0"})
                        img3 = _img_from_resp(r3)
                        if img3:
                            return img3
                    m = re.search(r"confirm=([0-9A-Za-z_\\-]+)", html_txt)
                    if m:
                        token = m.group(1)
                        confirm_url = direct + f"&confirm={token}"
                        r3 = requests.get(confirm_url, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
                        img3 = _img_from_resp(r3)
                        if img3:
                            return img3
            except Exception:
                pass
        except Exception:
            pass
        return url

    # assets desde marcas si existen
    try:
        mrow = None
        with get_connection() as cn2:
            if lead and isinstance(lead, dict) and lead.get("id_marca"):
                mrow = cn2.execute(
                    text("SELECT * FROM marcas WHERE id_marca=:id"),
                    {"id": lead.get("id_marca")},
                ).mappings().first()
            if not mrow and marca_raw:
                mrow = cn2.execute(
                    text("SELECT * FROM marcas WHERE UPPER(marca)=UPPER(:m)"),
                    {"m": marca_raw},
                ).mappings().first()
    except Exception:
        mrow = None
    if mrow:
        mrow = dict(mrow)
        # forzar marca por tabla marcas si existe (prioridad alta)
        m_marca = (mrow.get("nombre") or mrow.get("marca") or "").strip()
        if m_marca:
            marca_raw = m_marca
            marca_key = normalize_marca(marca_raw)
            assets = PDF_ASSETS.get(marca_key, {})
            logo = logo_for(marca_key, prefer_local=False)

    def _pick(*keys):
        for k in keys:
            v = (mrow or {}).get(k)
            if v:
                return v
        return ""

    portada_url = _pick("pdf_portada_url", "portada_url", "portada")
    cot_url = _pick("pdf_cotizacion_url", "cotizacion_url", "cotizacion")
    term_url = _pick("pdf_terminos_url", "terminos_url", "terminos")
    banco_url = _pick("pdf_banco_url", "banco_url", "banco")
    logo_url = (mrow or {}).get("logo_url") or (mrow or {}).get("logo_path") or ""

    # Drive folders (si está configurado): permite reemplazar archivos manteniendo nombre, sin tocar FILE_ID.
    # Requiere Service Account + compartir carpetas con el email del SA.
    try:
        folder_id = DRIVE_ASSET_FOLDERS.get(marca_key or "", "")
        if folder_id:
            from backend.core.drive_assets import resolve_brand_assets_from_folder
            da = resolve_brand_assets_from_folder(
                marca_key,
                folder_id,
                refresh=refresh_assets,
                ttl_seconds=_drive_assets_ttl_seconds(),
            )
            portada_url = da.get("portada") or portada_url
            cot_url = da.get("cotizacion") or cot_url
            term_url = da.get("terminos") or term_url
            banco_url = da.get("banco") or banco_url
            logo_url = da.get("logo") or logo_url
    except Exception:
        pass

    def _local_asset(name: str) -> str:
        if not marca_key:
            return ""
        root = Path(__file__).resolve().parents[2]
        bases = [
            root / "web" / "images" / "quote_assets" / marca_key,
            root / "data" / "quote_assets" / marca_key,
            root / "quote_assets" / marca_key,
        ]
        def _looks_supported_image(p: Path) -> bool:
            try:
                head = p.read_bytes()[:16]
                if head.startswith(b"\x89PNG\r\n\x1a\n"):
                    return True
                if head[:2] == b"\xff\xd8":
                    return True
                return False
            except Exception:
                return False
        for base in bases:
            for ext in (".png", ".jpg", ".jpeg"):
                p = base / f"{name}{ext}"
                if p.exists() and _looks_supported_image(p):
                    return p.as_uri()
        return ""

    def _direct_src(u: str) -> str:
        s = (u or "").strip()
        if not s:
            return ""
        # si ya es local (file://) o data URI, úsalo directo
        if s.startswith("file://") or s.startswith("data:"):
            return s
        return _img_data(s)

    # Drive (o URLs configuradas en DB) es la fuente de verdad: permite actualizar assets sin tocar el server.
    portada = _direct_src(portada_url or assets.get("portada","")) or _local_asset("portada")
    cot_bg = _direct_src(cot_url or assets.get("cotizacion","")) or _local_asset("cotizacion")
    terminos = _direct_src(term_url or assets.get("terminos","")) or _local_asset("terminos")
    banco = _direct_src(banco_url or assets.get("banco","")) or _local_asset("banco")
    if logo_url:
        logo = logo_url
    logo_img = _direct_src(logo) or _local_asset("logo")

    assets_used = {
        "portada": portada,
        "cotizacion": cot_bg,
        "terminos": terminos,
        "banco": banco,
        "logo": logo_img,
    }
    missing_assets = [k for k, v in assets_used.items() if not v]
    if missing_assets:
        print(f"[quotes/pdf] Missing assets for {id_cotizacion} ({marca_key}): {missing_assets}")
        if strict_assets:
            raise HTTPException(status_code=422, detail={"missing_assets": missing_assets, "marca": marca_key})

    # En modo refresh, forzamos regeneración (para que tome los assets nuevos de Drive).

        # HTML
    subtotal_val = float(cot.get("subtotal_productos") or cot.get("subtotal") or 0)
    traslado_val = float(cot.get("traslado") or 0)
    descuento_valor = float(cot.get("descuento_valor") or 0)
    descuento_tipo = str(cot.get("descuento_tipo") or "").strip()

    items_bruto = 0.0
    for it in (items or []):
        q = float(it.get("cantidad") or 0)
        pu = float(it.get("precio_unitario") or 0)
        tl = float(it.get("total_linea") or 0)
        items_bruto += (tl if tl > 0 else (q * pu))

    subtotal_bruto = items_bruto if items_bruto > 0 else subtotal_val
    if descuento_tipo == "%" and descuento_valor > 0:
        descuento_abs = round((subtotal_bruto * descuento_valor) / 100.0, 2)
    elif descuento_valor > 0:
        descuento_abs = round(descuento_valor, 2)
    else:
        descuento_abs = 0.0

    neto_val = max(0.0, round(subtotal_bruto - descuento_abs, 2))

    tipo_cli = str(cot.get("tipo_cliente") or "").strip().upper()
    is_empresa = ("EMP" in tipo_cli)
    iva_val = float(cot.get("iva") or 0) if is_empresa else 0.0

    total_val = float(cot.get("total") or (neto_val + traslado_val + (iva_val if is_empresa else 0.0)))

    show_desc = (descuento_valor > 0.0) or (descuento_abs > 0.005)
    desc_label = f"Descuento ({descuento_valor:.0f}%)" if (descuento_tipo == "%" and descuento_valor > 0) else "Descuento"

    totals_rows = [f"<tr><td>Subtotal productos</td><td style='text-align:right'>${int(subtotal_bruto):,}</td></tr>"]
    if show_desc:
        totals_rows.append(f"<tr><td>{desc_label}</td><td style='text-align:right'>-$ {int(descuento_abs):,}</td></tr>")
        totals_rows.append(f"<tr><td>Neto</td><td style='text-align:right'>${int(neto_val):,}</td></tr>")
    if traslado_val > 0:
        totals_rows.append(f"<tr><td>Traslado</td><td style='text-align:right'>${int(traslado_val):,}</td></tr>")
    if is_empresa and iva_val > 0:
        totals_rows.append(f"<tr><td>IVA</td><td style='text-align:right'>${int(iva_val):,}</td></tr>")
    totals_rows.append(f"<tr><td><b>Total</b></td><td style='text-align:right'><b>${int(total_val):,}</b></td></tr>")

    rows_html = ""
    for it in (items or []):
        desc = it.get("descripcion") or ""
        rows_html += f"""
          <tr>
            <td>{it.get('producto','')}</td>
            <td class="desc">{desc}</td>
            <td style="text-align:right">{int(it.get('cantidad') or 0)}</td>
            <td style="text-align:right">${int(it.get('precio_unitario') or 0):,}</td>
            <td style="text-align:right">${int(it.get('total_linea') or 0):,}</td>
          </tr>
        """

    # Overrides de layout por marca (SOLO PDF)
    # - PETRAS: tabla ítems: Descripción/Precio/Cantidad/Monto + totales alineados a la derecha con padding
    # - MAS FLOW: totales arriba en tabla más formal
    def _norm_marca_pdf(s: str) -> str:
        try:
            import unicodedata

            x = unicodedata.normalize("NFD", str(s or ""))
            x = "".join(ch for ch in x if unicodedata.category(ch) != "Mn")
        except Exception:
            x = str(s or "")
        x = x.upper()
        x = "".join(ch for ch in x if ch.isalnum())
        return x

    marca_norm = _norm_marca_pdf(marca_key or marca_raw or cot.get("marca") or (mrow or {}).get("nombre") or (mrow or {}).get("marca"))
    is_petras = marca_norm.startswith("PETRAS")
    is_masflow = ("MASFLOW" in marca_norm) or (marca_norm == "MAS")

    # colores por marca (fondos claros necesitan texto oscuro)
    text_color = "#fff"
    border_color = "rgba(255,255,255,0.25)"

    def _simple_key(k: str) -> str:
        return (k or "").replace(" ", "").replace("_", "").replace("-", "")

    key_simple = _simple_key(marca_key).upper()
    raw_upper = (marca_raw or "").upper()
    cot_upper = (str(cot.get("marca") or "")).upper()
    mrow_upper = (str((mrow or {}).get("nombre") or (mrow or {}).get("marca") or "")).upper()
    # DEL SABOR / CARRITOS DEL SABOR y variantes
    if "SABOR" in key_simple or "SABOR" in raw_upper or "SABOR" in cot_upper or "SABOR" in mrow_upper:
        # negro/oliva (DEL SABOR usa paleta oscura sobre fondo claro)
        text_color = "#2f3a16"
        border_color = "rgba(47,58,22,0.25)"

    def _money(n: float) -> str:
        return f"${int(round(float(n or 0))):,}"

    def _build_totals_rows_override() -> list[str]:
        rows: list[str] = []
        rows.append(f"<tr><td>Subtotal productos</td><td style='text-align:right'>{_money(subtotal_bruto)}</td></tr>")
        if show_desc and descuento_abs > 0:
            rows.append(f"<tr><td>{desc_label}</td><td style='text-align:right'>- {_money(descuento_abs)}</td></tr>")
        rows.append(f"<tr><td>Neto</td><td style='text-align:right'>{_money(neto_val)}</td></tr>")
        rows.append(f"<tr><td>IVA</td><td style='text-align:right'>{_money(iva_val if is_empresa else 0)}</td></tr>")
        rows.append(f"<tr><td>Traslado</td><td style='text-align:right'>{_money(traslado_val)}</td></tr>")
        rows.append(f"<tr><td><b>Total</b></td><td style='text-align:right'><b>{_money(total_val)}</b></td></tr>")
        return rows

    items_table_html = ""
    totals_table_html = ""
    extra_css = ""
    if is_petras:
        rows_html_petras = ""
        for it in (items or []):
            prod = (it.get("producto") or "").strip()
            desc = (it.get("descripcion") or "").strip()
            if desc:
                desc_html = f"{prod}<div class=\"desc\" style=\"opacity:.8\">{desc}</div>"
            else:
                desc_html = prod
            rows_html_petras += f"""
              <tr>
                <td class="desc">{desc_html}</td>
                <td style="text-align:right">{_money(float(it.get('precio_unitario') or 0))}</td>
                <td style="text-align:right">{int(it.get('cantidad') or 0)}</td>
                <td style="text-align:right">{_money(float(it.get('total_linea') or 0))}</td>
              </tr>
            """
        items_table_html = f"""
          <table style="margin-top:6mm;">
            <colgroup>
              <col style="width:52%"/>
              <col style="width:16%"/>
              <col style="width:12%"/>
              <col style="width:20%"/>
            </colgroup>
            <thead>
              <tr>
                <th>Descripción</th>
                <th style="text-align:right">Precio</th>
                <th style="text-align:right">Cantidad</th>
                <th style="text-align:right">Monto</th>
              </tr>
            </thead>
            <tbody>{rows_html_petras}</tbody>
          </table>
        """
        totals_table_html = f"""
          <table class="totals totals-right">
            {''.join(_build_totals_rows_override())}
          </table>
        """
        extra_css = f"""
        .totals-right {{ width:50%; margin-left:auto; margin-top:6mm; padding-right:6mm; }}
        .totals-right td {{ border:none; padding:2px 0; }}
        """
    elif is_masflow:
        # Totales arriba (más formal) + tabla ítems normal
        totals_table_html = f"""
          <table class="totals totals-box">
            {''.join(_build_totals_rows_override())}
          </table>
        """
        items_table_html = f"""
          <table style="margin-top:6mm;">
            <colgroup>
              <col style="width:26%"/>
              <col style="width:38%"/>
              <col style="width:8%"/>
              <col style="width:12%"/>
              <col style="width:16%"/>
            </colgroup>
            <thead>
              <tr>
                <th>Producto</th>
                <th>Descripción</th>
                <th style="text-align:right">Cant</th>
                <th style="text-align:right">Precio</th>
                <th style="text-align:right">Total</th>
              </tr>
            </thead>
            <tbody>{rows_html}</tbody>
          </table>
        """
        extra_css = f"""
        .totals-box {{
          width:56%;
          margin-left:auto;
          margin-top:6mm;
          padding:4mm 5mm;
          border:1px solid {border_color};
          border-radius:10px;
        }}
        .totals-box td {{ border:none; padding:2px 0; }}
        """
    else:
        # Default: mantener salida actual (incluye filas extra de IVA/Traslado/Total)
        items_table_html = f"""
          <table style="margin-top:6mm;">
            <colgroup>
              <col style="width:26%"/>
              <col style="width:38%"/>
              <col style="width:8%"/>
              <col style="width:12%"/>
              <col style="width:16%"/>
            </colgroup>
            <thead>
              <tr>
                <th>Producto</th>
                <th>Descripción</th>
                <th style="text-align:right">Cant</th>
                <th style="text-align:right">Precio</th>
                <th style="text-align:right">Total</th>
              </tr>
            </thead>
            <tbody>{rows_html}</tbody>
          </table>
        """
        totals_table_html = f"""
          <table class="totals">
            {''.join(totals_rows)}
            <tr><td>IVA</td><td style="text-align:right">${int(iva_val):,}</td></tr>
            <tr><td>Traslado</td><td style="text-align:right">${int(traslado_val):,}</td></tr>
            <tr><td><b>Total</b></td><td style="text-align:right"><b>${int(total_val):,}</b></td></tr>
          </table>
        """

    html = f"""
    <html>
    <head>
      <meta charset="utf-8"/>
      <style>
        @page {{ size:A4; margin:0; }}
        body {{ font-family:Arial,sans-serif; margin:0; }}
        .page {{ page-break-after:always; position:relative; width:210mm; height:297mm; }}
        .bg, .content {{ position:absolute; top:0; right:0; bottom:0; left:0; }}
        .bg-img {{ display:block; width:100%; height:100%; }}
        .content {{ padding:18mm 16mm; z-index:2; color:{text_color}; }}
        .bg {{ z-index:1; }}
        .logo {{ height:29mm; transform: translateX(50mm); }}
        table {{ width:100%; border-collapse:collapse; table-layout:fixed; font-size:11px; }}
        th,td {{ border-bottom:1px solid {border_color}; padding:4px 6px; overflow:hidden; }}
        thead th {{
          text-transform: uppercase;
          letter-spacing: .06em;
          font-size: 10px;
        }}
        tbody td {{ vertical-align: top; }}
        td.desc {{
          font-size: 10px;
          line-height: 1.25;
          white-space: normal;
          word-break: break-word;
        }}
        .totals {{ margin-top:8mm; width:100%; font-size:12px; }}
        .totals td {{ border:none; padding:2px 0; }}
        {extra_css}
      </style>
    </head>
    <body>
      <div class="page">
        <div class="bg"><img class="bg-img" src="{portada}"/></div>
      </div>
      <div class="page">
        <div class="bg"><img class="bg-img" src="{cot_bg}"/></div>
        <div class="content">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <div><img class="logo" src="{logo_img}"/></div>
            <div style="text-align:right;font-size:12px;">
              <div><b>N° {nro_text}</b></div>
              <div>Fecha evento: {fecha_evento}</div>
            </div>
          </div>
          <div style="margin-top:6mm;font-size:12px;">
            <div><b>Cliente:</b> {nombre_cliente}</div>
            <div><b>Comuna:</b> {comuna}</div>
            <div><b>Dirección:</b> {direccion}</div>
            <div><b>Teléfono:</b> {telefono}</div>
          </div>
          {totals_table_html if is_masflow else ""}
          {items_table_html}
          {totals_table_html if not is_masflow else ""}
        </div>
      </div>
      <div class="page">
        <div class="bg"><img class="bg-img" src="{terminos}"/></div>
      </div>
      <div class="page">
        <div class="bg"><img class="bg-img" src="{banco}"/></div>
      </div>
    </body>
    </html>
    """

    base_url = str(Path(__file__).resolve().parents[2])
    # Debug info file
    debug_dir = Path(__file__).resolve().parents[2] / "data" / "debug"
    try:
        debug_dir.mkdir(parents=True, exist_ok=True)
        debug_info = {
            "id_cotizacion": id_cotizacion,
            "marca": marca_raw,
            "marca_key": marca_key,
            "assets": assets_used,
            "missing_assets": missing_assets,
            "pdf_path": str(pdf_path),
        }
        (debug_dir / f"quote_{id_cotizacion}.json").write_text(
            json.dumps(debug_info, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # throttled missing-assets log
        if missing_assets:
            interval = int(os.getenv("ASSET_MISSING_LOG_SECONDS", "600"))
            last_file = debug_dir / "assets_missing_last.txt"
            last_ts = 0.0
            try:
                last_ts = float(last_file.read_text(encoding="utf-8").strip() or "0")
            except Exception:
                last_ts = 0.0
            now = time.time()
            if now - last_ts >= interval:
                last_file.write_text(str(now), encoding="utf-8")
                with (debug_dir / "assets_missing.log").open("a", encoding="utf-8") as f:
                    f.write(json.dumps(debug_info, ensure_ascii=False) + "\n")
    except Exception:
        pass

    if debug:
        banner = f"""
        <div style="position:fixed;top:0;left:0;right:0;background:#111;color:#fff;
                    padding:8px 12px;font-size:12px;z-index:9999">
          <b>DEBUG PDF</b> · Marca: {marca_key or "—"} · Missing: {", ".join(missing_assets) if missing_assets else "—"}
        </div>
        <div style="height:36px"></div>
        """
        html_dbg = html.replace("<body>", "<body>" + banner, 1)
        return HTMLResponse(html_dbg)

    def _wkhtmltopdf_generate(html_str: str, out_pdf: Path, base_url: str) -> None:
        """
        Fallback para hosting con Pango roto (WeasyPrint falla).
        Requiere `wkhtmltopdf` instalado en el sistema.
        """
        root = Path(__file__).resolve().parents[2]
        bundled = [
            root / "bin" / "wkhtmltopdf",
            root / "bin" / "wkhtmltox" / "bin" / "wkhtmltopdf",
        ]
        exe = os.getenv("WKHTMLTOPDF_BIN") or ""
        if not exe:
            for p in bundled:
                if p.exists() and p.is_file():
                    exe = str(p)
                    break
        if not exe:
            exe = shutil.which("wkhtmltopdf") or ""
        if not exe:
            raise RuntimeError("wkhtmltopdf no está instalado (no encontré el binario).")
        out_pdf.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
            f.write(html_str)
            tmp_html = f.name
        try:
            cmd = [
                exe,
                "--quiet",
                "--enable-local-file-access",
                "--page-size",
                "A4",
                "--margin-top",
                "0",
                "--margin-right",
                "0",
                "--margin-bottom",
                "0",
                "--margin-left",
                "0",
                tmp_html,
                str(out_pdf),
            ]
            p = subprocess.run(cmd, capture_output=True, text=True)
            if p.returncode != 0:
                err = (p.stderr or p.stdout or "").strip()
                raise RuntimeError(f"wkhtmltopdf falló (code={p.returncode}): {err[:300]}")
        finally:
            try:
                os.unlink(tmp_html)
            except Exception:
                pass

    # PDF optimizado (reduce peso)
    try:
        # Preferir wkhtmltopdf si está disponible (mejor rendering en hosting con Pango roto).
        try:
            _wkhtmltopdf_generate(html, pdf_path, base_url)
        except Exception:
            try:
                HTML(string=html, base_url=base_url).write_pdf(
                    str(pdf_path),
                    optimize_size=("images",),
                    jpeg_quality=80,
                )
            except TypeError:
                HTML(string=html, base_url=base_url).write_pdf(str(pdf_path))
    except Exception as e:
        # Hosting cPanel/CentOS: WeasyPrint puede fallar por librerías Pango incompatibles.
        # Fallback 1: generar PDF con Pillow (sin depender de Pango).
        try:
            from PIL import Image, ImageDraw, ImageFont, ImageFile  # type: ignore
            import io
            import base64 as _b64
            import traceback as _tb

            # PDFs "tipo plantilla" suelen venir en PNG gigantes.
            # Evitamos errores típicos de PIL en hosting (imágenes truncadas o "decompression bomb").
            ImageFile.LOAD_TRUNCATED_IMAGES = True
            try:
                Image.MAX_IMAGE_PIXELS = None  # type: ignore[attr-defined]
            except Exception:
                pass

            def _open_img(src: str) -> Image.Image:
                s = (src or "").strip()
                if not s:
                    raise RuntimeError("img vacía")
                if s.startswith("data:"):
                    # data:image/png;base64,....
                    try:
                        b64 = s.split("base64,", 1)[1]
                    except Exception:
                        raise RuntimeError("data uri inválida")
                    raw = _b64.b64decode(b64)
                    im = Image.open(io.BytesIO(raw))
                    im.load()
                    return im
                if s.startswith("file://"):
                    # as_uri() encodea espacios como %20
                    p = unquote(s[7:])
                    im = Image.open(p)
                    im.load()
                    return im
                # path directo
                im = Image.open(s)
                im.load()
                return im

            def _font_reg(sz: int):
                for p in (
                    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                ):
                    try:
                        return ImageFont.truetype(p, sz)
                    except Exception:
                        pass
                return ImageFont.load_default()

            def _font_bold(sz: int):
                for p in (
                    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                ):
                    try:
                        return ImageFont.truetype(p, sz)
                    except Exception:
                        pass
                return ImageFont.load_default()

            W, H = 1240, 1754  # A4 aprox @150dpi
            mm = 150 / 25.4
            mx = int(16 * mm)
            my = int(18 * mm)

            f12 = _font_reg(18)
            f12s = _font_reg(15)   # desc pequeña / modo compacto
            f10 = _font_reg(13)    # desc extra-compacta
            f9 = _font_reg(11)     # desc multi-línea (para que se lea completa)
            f8 = _font_reg(10)     # desc ultra-compacta (cuando la fila es baja)
            f12b = _font_bold(18)
            f12bs = _font_bold(15)
            f14b = _font_bold(22)
            f16b = _font_bold(26)

            def _fit(img: Image.Image) -> Image.Image:
                # Fast path: si ya viene en RGB y tamaño A4, no reprocesar.
                try:
                    if img.mode == "RGB" and img.size == (W, H):
                        return img
                except Exception:
                    pass
                im = img.convert("RGBA")
                if im.size != (W, H):
                    im = im.resize((W, H))
                bg = Image.new("RGBA", (W, H), (255, 255, 255, 255))
                bg.alpha_composite(im)
                return bg.convert("RGB")

            def _safe_page(src: str, label: str) -> Image.Image:
                try:
                    return _fit(_open_img(src))
                except Exception:
                    im = Image.new("RGB", (W, H), (255, 255, 255))
                    d = ImageDraw.Draw(im)
                    d.text((mx, my), f"Falta asset: {label}", font=f16b, fill=(180, 0, 0))
                    return im

            portada_im = _safe_page(portada, "portada")
            cot_im = _safe_page(cot_bg, "cotizacion")
            term_im = _safe_page(terminos, "terminos")
            banco_im = _safe_page(banco, "banco")

            # Cotización page overlay
            draw = ImageDraw.Draw(cot_im)
            # Logo: deshabilitado por decisión de diseño (todas las marcas).

            # ===== Brand look (Pillow) =====
            # Objetivo: 4 looks DIFERENTES (como las imágenes) y legibles en todos los fondos.
            key_simple_p = (str(marca_key or "")).replace(" ", "").replace("_", "").replace("-", "").upper()
            is_sabor = "SABOR" in key_simple_p
            is_cam = "CAMALEON" in key_simple_p
            is_expr = "EXPRESS" in key_simple_p
            is_gour = "GOURMET" in key_simple_p

            def _money(v: int) -> str:
                return f"${int(v):,}".replace(",", ".")

            def _money_signed(v: int) -> str:
                try:
                    iv = int(v)
                except Exception:
                    iv = 0
                if iv < 0:
                    return f"-{_money(abs(iv))}"
                return _money(iv)

            subtotal_print = int(subtotal_bruto)
            descuento_print = int(descuento_abs) if show_desc else 0
            neto_print = int(neto_val)
            traslado_print = int(traslado_val)
            iva_print = int(iva_val)
            total_print = int(total_val)

            def _pct_str(v: float) -> str:
                try:
                    if v is None:
                        return ""
                    fv = float(v)
                    if abs(fv - round(fv)) < 1e-9:
                        return str(int(round(fv)))
                    return (f"{fv:.1f}").rstrip("0").rstrip(".")
                except Exception:
                    return ""

            pct_txt = _pct_str(descuento_valor) if (descuento_tipo == "%" and (descuento_valor or 0) > 0) else ""
            desc_line = "DESCUENTO" + (f" ({pct_txt}%)" if pct_txt else "")
            desc_grid_label = ("DESC " + (f"{pct_txt}%" if pct_txt else "")) if pct_txt else "DESCUENTO"

            def _clip(text: str, max_w: int, font) -> str:
                t = (text or "").strip()
                if not t:
                    return ""
                while t and draw.textlength(t, font=font) > max_w:
                    t = t[:-1]
                return t

            def _line_h(font) -> int:
                try:
                    bb = font.getbbox("Ag")  # type: ignore[attr-defined]
                    return int((bb[3] - bb[1]) + 2)
                except Exception:
                    return 14

            def _wrap_lines(text: str, max_w: int, font, max_lines: int = 2) -> list[str]:
                """
                Envuelve texto por palabras en `max_lines` líneas; si se trunca, agrega ellipsis.
                """
                raw = (text or "").strip()
                if not raw:
                    return []
                try:
                    words = [w for w in raw.split() if w]
                except Exception:
                    words = [raw]
                lines: list[str] = []
                cur = ""
                used_words = 0

                for w in words:
                    cand = (cur + " " + w).strip() if cur else w
                    if draw.textlength(cand, font=font) <= max_w:
                        cur = cand
                        used_words += 1
                        continue
                    if cur:
                        lines.append(cur)
                        cur = w
                        used_words += 1
                    else:
                        # palabra muy larga: clip duro
                        lines.append(_clip(w, max_w, font))
                        used_words += 1
                        cur = ""
                    if len(lines) >= max_lines:
                        cur = ""
                        break

                if cur and len(lines) < max_lines:
                    lines.append(cur)

                truncated = used_words < len(words)
                if truncated and lines:
                    ell = "…"
                    last = (lines[-1] or "").rstrip()
                    # asegurar espacio para ellipsis
                    target_w = max(20, max_w - int(draw.textlength(ell, font=font)))
                    last2 = _clip(last, target_w, font).rstrip()
                    lines[-1] = (last2 + ell) if last2 else ell
                return lines

            # Brand palettes (from your "Colores corporativos")
            PAL = {
                "CAM": {
                    "primary": (43, 181, 21),
                    "primary2": (20, 145, 8),
                    "ink": (12, 18, 24),
                    "muted": (78, 96, 110),
                    "line": (44, 60, 70),
                    "paper": (255, 255, 255),
                    "zebra": (236, 248, 236),
                },
                "SABOR": {
                    "primary": (52, 163, 10),
                    "primary2": (114, 64, 10),
                    "ink": (28, 34, 18),
                    "muted": (82, 96, 74),
                    "line": (44, 52, 40),
                    "paper": (255, 255, 255),
                },
                "EXP": {
                    "primary": (218, 72, 25),
                    "primary2": (246, 167, 19),
                    "ink": (18, 18, 18),
                    "muted": (90, 98, 104),
                    "line": (60, 60, 60),
                    "paper": (255, 255, 255),
                },
                "GOUR": {
                    "primary": (239, 193, 45),  # gold
                    "primary2": (50, 50, 50),   # dark gray
                    "ink": (18, 18, 18),
                    "muted": (96, 96, 96),
                    "line": (60, 60, 60),
                    "paper": (255, 255, 255),
                },
            }

            bkey = "EXP"
            if is_cam:
                bkey = "CAM"
            elif is_sabor:
                bkey = "SABOR"
            elif is_gour:
                bkey = "GOUR"
            pal = PAL[bkey]

            txt = pal["ink"]
            muted = pal["muted"]
            line = pal["line"]
            primary = pal["primary"]
            primary2 = pal["primary2"]

            # Content card (white paper on top of brand background)
            cx0 = mx
            cy0 = my + int(18 * mm)
            cx1 = W - mx
            cy1 = H - my - int(10 * mm)
            # pseudo shadow
            draw.rounded_rectangle((cx0 + 6, cy0 + 8, cx1 + 6, cy1 + 8), radius=18, fill=(220, 228, 236), outline=None)
            draw.rounded_rectangle((cx0, cy0, cx1, cy1), radius=18, fill=pal["paper"], outline=line, width=2)

            def _draw_table(
                x0: int,
                y0: int,
                w: int,
                cols,
                head_bg,
                head_fg,
                zebra=None,
                row_lines=True,
                max_rows=12,
                head_h=40,
                row_h=54,
                compact: bool = False,
                table_bottom: int | None = None,
            ):
                # X positions
                col_x = [x0]
                acc = x0
                for _, _, wp, _ in cols:
                    acc += int(w * float(wp))
                    col_x.append(acc)
                col_x[-1] = x0 + w

                y_head_top = y0
                y_head_bot = y0 + head_h
                if table_bottom is None:
                    table_h = head_h + (max_rows * row_h)
                    table_bottom = y0 + table_h
                else:
                    table_bottom = int(table_bottom)
                    table_h = max(0, table_bottom - y0)

                draw.rectangle((x0, y_head_top, x0 + w, y_head_bot), fill=head_bg)
                draw.rectangle((x0, y_head_bot, x0 + w, table_bottom), fill=(255, 255, 255))
                draw.rectangle((x0, y_head_top, x0 + w, table_bottom), outline=line, width=2)

                # column separators
                for i in range(1, len(col_x) - 1):
                    draw.line((col_x[i], y_head_top, col_x[i], y0 + table_h), fill=(200, 206, 212), width=1)

                # header labels
                head_font = f12bs if compact else f12b
                cell_font = f12s if compact else f12
                cell_bold = f12bs if compact else f12b
                # La descripción suele ser larga. Ajustamos tamaño según altura de fila
                # para priorizar legibilidad sin desbordar a la fila siguiente.
                desc_font = f8 if row_h <= 46 else f9

                for i, (_, label, _, align) in enumerate(cols):
                    xL, xR = col_x[i], col_x[i + 1]
                    pad = 12
                    x = xR - pad if align == "r" else xL + pad
                    draw.text(
                        (x, y_head_top + 10),
                        str(label).upper(),
                        font=head_font,
                        fill=head_fg,
                        anchor="ra" if align == "r" else None,
                    )

                # rows (grid up to max_rows; if hay pocos items, evitamos "pintar" el resto)
                grid_rows = max_rows
                show_empty_grid = len(items) > 6
                for i in range(grid_rows):
                    it = items[i] if i < len(items) else {}
                    y_top = y_head_bot + i * row_h
                    y_bot = y_top + row_h
                    if y_bot > table_bottom:
                        break
                    if zebra and (i < len(items)) and (i % 2 == 0):
                        draw.rectangle((x0 + 1, y_top, x0 + w - 1, y_bot), fill=zebra)
                    if row_lines and (i < len(items) or show_empty_grid):
                        draw.line((x0, y_bot, x0 + w, y_bot), fill=(220, 226, 232), width=1)

                    if i >= len(items):
                        continue

                    prod = str(it.get("producto", "") or "").strip()
                    desc = str(it.get("descripcion", "") or "").strip()
                    cant = int(it.get("cantidad") or 0)
                    pu = int(it.get("precio_unitario") or 0)
                    tl = int(it.get("total_linea") or 0)

                    for ci, (ckey, _, _, align) in enumerate(cols):
                        xL, xR = col_x[ci], col_x[ci + 1]
                        pad = 12
                        y_base = y_top + (13 if compact else 16)
                        if ckey == "cantidad":
                            draw.text((xR - pad, y_base), str(cant), font=cell_font, fill=txt, anchor="ra")
                        elif ckey == "pu":
                            draw.text((xR - pad, y_base), _money(pu), font=cell_font, fill=txt, anchor="ra")
                        elif ckey == "total":
                            draw.text((xR - pad, y_base), _money(tl), font=cell_bold, fill=txt, anchor="ra")
                        elif ckey == "codigo":
                            code = str(it.get("id_producto") or "")
                            draw.text((xL + pad, y_base), _clip(code, (xR - xL - 2 * pad), cell_font), font=cell_font, fill=txt)
                        elif ckey == "desc":
                            x = xL + pad
                            max_w = max(60, (xR - xL - 2 * pad))
                            y_prod = y_top + (2 if compact else 4)
                            draw.text((x, y_prod), _clip(prod, max_w, cell_bold), font=cell_bold, fill=txt)
                            if desc:
                                # Envuelve descripción (2–3 líneas según espacio) para que se lea completa.
                                y_desc0 = y_top + (18 if compact else 22)
                                lh = _line_h(desc_font)
                                # espacio disponible hasta el final de la fila
                                avail = max(0, (y_bot - 4) - y_desc0)
                                # En compacto hasta 3 líneas; en normal hasta 4 si cabe.
                                cap = 3 if compact else 4
                                min_lines = 2 if row_h >= 42 else 1
                                max_lines = max(min_lines, min(cap, int(avail // max(1, lh))))
                                for li, line_txt in enumerate(_wrap_lines(desc, max_w, desc_font, max_lines=max_lines)):
                                    draw.text((x, y_desc0 + li * lh), line_txt, font=desc_font, fill=muted)

                # Hint if truncated
                if len(items) > max_rows:
                    y_hint = min(table_bottom - 18, y_head_bot + (max_rows * row_h) + 10)
                    draw.text((x0 + 12, y_hint), f"... +{len(items) - max_rows} items", font=f12s, fill=muted)
                return table_bottom

            # ===== Brand-specific headers + tables =====
            pad = 26
            x = cx0 + pad
            y = cy0 + pad
            content_w = (cx1 - cx0) - (2 * pad)

            # Requisito: 1 página, sin crear más hojas.
            # Si hay muchos items, hacemos la tabla más compacta pero SIEMPRE mantenemos los totales dentro del margen.
            # No entrar en compacto antes de 12 ítems (UX: al menos 12 legibles).
            compact = len(items) > 12
            want_rows = 20  # objetivo: mostrar hasta 20 filas sin desbordar

            if is_cam:
                # CAM: green header band + green zebra table
                bar_h = 56 if compact else 64
                draw.rounded_rectangle((x, y, x + content_w, y + bar_h), radius=14, fill=primary, outline=None)
                draw.text((x + 18, y + 18), "COTIZACION", font=f16b, fill=(255, 255, 255))
                draw.text((x + content_w - 18, y + 20), f"N° {nro_text}", font=f14b, fill=(255, 255, 255), anchor="ra")
                y += bar_h + (10 if compact else 18)
                draw.text((x, y), f"Cliente: {nombre_cliente}", font=f12b, fill=txt)
                draw.text((x, y + 26), f"Comuna: {comuna}", font=f12, fill=txt)
                if fecha_evento:
                    draw.text((x + content_w - 2, y), f"Fecha evento: {fecha_evento}", font=f12b, fill=txt, anchor="ra")
                y += (52 if compact else 64)

                cols = [
                    ("cantidad", "Cant.", 0.10, "r"),
                    ("desc", "Descripcion", 0.60, "l"),
                    ("pu", "P. Unitario", 0.15, "r"),
                    ("total", "Total", 0.15, "r"),
                ]
                # Table geometry: totales dentro del mismo "marco" de tabla.
                table_top = y
                table_bottom = cy1 - pad - 8
                totals_h = 140
                totals_w = 380
                totals_y = table_bottom - totals_h - 10
                rows_area_bottom = totals_y - 10

                head_h = (34 if compact else 40)
                row_h = (42 if compact else 56)
                rows_fit = int(max(1, (rows_area_bottom - (table_top + head_h)) // row_h))
                max_rows = min(want_rows, rows_fit)

                y_end = _draw_table(
                    x,
                    table_top,
                    content_w,
                    cols,
                    head_bg=primary2,
                    head_fg=(255, 255, 255),
                    zebra=pal.get("zebra"),
                    max_rows=max_rows,
                    head_h=head_h,
                    row_h=row_h,
                    compact=compact,
                    table_bottom=table_bottom,
                )

                # Totales: centrados abajo (legible en todas las marcas)
                bx = x + max(0, int((content_w - totals_w) / 2))
                by = totals_y
                draw.rounded_rectangle((bx, by, bx + totals_w, by + totals_h), radius=12, outline=primary2, width=2, fill=(248, 252, 248))
                lx = bx + 18
                rx = bx + totals_w - 18
                y0 = by + 12
                step = 20

                def row(ypos: int, lab: str, val: int, bold: bool = False, vfill=txt):
                    draw.text((lx, ypos), lab, font=(f12b if bold else f12), fill=txt)
                    draw.text((rx, ypos), _money_signed(val), font=(f12b if bold else f12), fill=vfill, anchor="ra")

                row(y0 + 0 * step, "SUBTOTAL", subtotal_print)
                if show_desc:
                    row(y0 + 1 * step, desc_line, -abs(descuento_print))
                    row(y0 + 2 * step, "NETO", neto_print)
                    row(y0 + 3 * step, "TRASLADO", traslado_print)
                    row(y0 + 4 * step, "IVA", iva_print)
                    row(y0 + 5 * step, "TOTAL", total_print, bold=True, vfill=primary2)
                else:
                    row(y0 + 1 * step, "IVA", iva_print)
                    row(y0 + 2 * step, "TRASLADO", traslado_print)
                    row(y0 + 3 * step, "TOTAL", total_print, bold=True, vfill=primary2)

            elif is_sabor:
                # DEL SABOR: clean paper + olive/green accents; NO unit price
                draw.text((x, y), "COTIZACION", font=f16b, fill=primary2)
                draw.text((x + content_w, y), f"N° {nro_text}", font=f14b, fill=primary2, anchor="ra")
                y += 34
                draw.line((x, y, x + content_w, y), fill=primary, width=4)
                y += 18
                draw.text((x, y), f"Cliente: {nombre_cliente}", font=f12b, fill=txt)
                draw.text((x, y + 26), f"Comuna: {comuna}", font=f12, fill=txt)
                if fecha_evento:
                    draw.text((x + content_w, y), f"Fecha evento: {fecha_evento}", font=f12b, fill=txt, anchor="ra")
                y += 64

                cols = [
                    ("cantidad", "Cant.", 0.14, "r"),
                    ("desc", "Descripcion", 0.66, "l"),
                    ("total", "Total", 0.20, "r"),
                ]
                table_top = y
                table_bottom = cy1 - pad - 8
                totals_h = 120
                totals_y = table_bottom - totals_h - 10
                rows_area_bottom = totals_y - 10
                head_h = (34 if compact else 40)
                row_h = (42 if compact else 56)
                rows_fit = int(max(1, (rows_area_bottom - (table_top + head_h)) // row_h))
                max_rows = min(want_rows, rows_fit)

                y_end = _draw_table(
                    x,
                    table_top,
                    content_w,
                    cols,
                    head_bg=primary,
                    head_fg=(255, 255, 255),
                    zebra=None,
                    max_rows=max_rows,
                    head_h=head_h,
                    row_h=row_h,
                    compact=compact,
                    table_bottom=table_bottom,
                )

                # Totales centrados (mismo layout que otras marcas)
                totals_w = 420
                bx = x + max(0, int((content_w - totals_w) / 2))
                by = totals_y
                draw.rounded_rectangle((bx, by, bx + totals_w, by + totals_h), radius=12, outline=primary2, width=2, fill=(252, 252, 248))
                lx = bx + 18
                rx = bx + totals_w - 18
                y0 = by + 10
                step = 18

                def row(ypos: int, lab: str, val: int, bold: bool = False, vfill=txt):
                    draw.text((lx, ypos), lab, font=(f12b if bold else f12), fill=txt)
                    draw.text((rx, ypos), _money_signed(val), font=(f12b if bold else f12), fill=vfill, anchor="ra")

                row(y0 + 0 * step, "SUBTOTAL", subtotal_print)
                if show_desc:
                    row(y0 + 1 * step, desc_line, -abs(descuento_print))
                    row(y0 + 2 * step, "NETO", neto_print)
                    row(y0 + 3 * step, "TRASLADO", traslado_print)
                    row(y0 + 4 * step, "IVA", iva_print)
                    row(y0 + 5 * step, "TOTAL", total_print, bold=True, vfill=primary2)
                else:
                    row(y0 + 1 * step, "IVA", iva_print)
                    row(y0 + 2 * step, "TRASLADO", traslado_print)
                    row(y0 + 3 * step, "TOTAL", total_print, bold=True, vfill=primary2)

            elif is_gour:
                # GOURMET: dark header + gold accents; remove discount/%imp (not shown)
                bar_h = 66
                draw.rounded_rectangle((x, y, x + content_w, y + bar_h), radius=14, fill=primary2, outline=None)
                draw.text((x + 18, y + 18), "COTIZACION", font=f16b, fill=(255, 255, 255))
                draw.text((x + content_w - 18, y + 20), f"N° {nro_text}", font=f14b, fill=primary, anchor="ra")
                y += bar_h + 18
                draw.text((x, y), f"Cliente: {nombre_cliente}", font=f12b, fill=txt)
                draw.text((x, y + 26), f"Comuna: {comuna}", font=f12, fill=txt)
                if fecha_evento:
                    draw.text((x + content_w, y), f"Fecha evento: {fecha_evento}", font=f12b, fill=txt, anchor="ra")
                y += 64

                cols = [
                    ("codigo", "Codigo", 0.12, "l"),
                    ("desc", "Descripcion", 0.56, "l"),
                    ("cantidad", "Cant.", 0.10, "r"),
                    ("pu", "Precio", 0.11, "r"),
                    ("total", "Total", 0.11, "r"),
                ]
                table_top = y
                table_bottom = cy1 - pad - 8
                totals_h = 150
                totals_w = 380
                totals_y = table_bottom - totals_h - 10
                rows_area_bottom = totals_y - 10
                head_h = (34 if compact else 40)
                row_h = (42 if compact else 56)
                rows_fit = int(max(1, (rows_area_bottom - (table_top + head_h)) // row_h))
                max_rows = min(want_rows, rows_fit)

                y_end = _draw_table(
                    x,
                    table_top,
                    content_w,
                    cols,
                    head_bg=primary,
                    head_fg=(18, 18, 18),
                    zebra=None,
                    max_rows=max_rows,
                    head_h=head_h,
                    row_h=row_h,
                    compact=compact,
                    table_bottom=table_bottom,
                )

                bx = x + max(0, int((content_w - totals_w) / 2))
                by = totals_y
                draw.rounded_rectangle((bx, by, bx + totals_w, by + totals_h), radius=12, outline=primary, width=2, fill=primary2)
                lx = bx + 18
                rx = bx + totals_w - 18
                y0 = by + 12
                step = 20
                white = (255, 255, 255)

                def row(ypos: int, lab: str, val: int, bold: bool = False, vfill=white):
                    draw.text((lx, ypos), lab, font=(f12b if bold else f12), fill=white)
                    draw.text((rx, ypos), _money_signed(val), font=(f12b if bold else f12), fill=vfill, anchor="ra")

                row(y0 + 0 * step, "SUBTOTAL", subtotal_print)
                if show_desc:
                    row(y0 + 1 * step, desc_line, -abs(descuento_print))
                    row(y0 + 2 * step, "NETO", neto_print)
                    row(y0 + 3 * step, "TRASLADO", traslado_print)
                    row(y0 + 4 * step, "IVA", iva_print)
                    row(y0 + 5 * step, "TOTAL", total_print, bold=True, vfill=primary)
                else:
                    row(y0 + 1 * step, "IVA", iva_print)
                    row(y0 + 2 * step, "TRASLADO", traslado_print)
                    row(y0 + 3 * step, "TOTAL", total_print, bold=True, vfill=primary)

            else:
                # EXPRESS: classic grid quote; orange accents; totals in small grid
                draw.text((x, y), "COTIZACION", font=f16b, fill=primary)
                draw.text((x + content_w, y), f"N° {nro_text}", font=f14b, fill=txt, anchor="ra")
                y += 34
                draw.line((x, y, x + content_w, y), fill=(200, 200, 200), width=2)
                y += 18

                # meta 2 columns
                draw.text((x, y), "Cotizacion para:", font=f12b, fill=txt)
                draw.text((x, y + 24), nombre_cliente, font=f12, fill=txt)
                draw.text((x, y + 48), f"Comuna: {comuna}", font=f12, fill=txt)
                if fecha_evento:
                    draw.text((x + content_w, y), f"Fecha evento: {fecha_evento}", font=f12b, fill=txt, anchor="ra")
                y += 78

                cols = [
                    ("cantidad", "Cantidad", 0.13, "r"),
                    ("desc", "Descripcion", 0.58, "l"),
                    ("pu", "Precio", 0.145, "r"),
                    ("total", "Monto", 0.145, "r"),
                ]
                table_top = y
                table_bottom = cy1 - pad - 8
                totals_h = 138
                totals_w = 360
                totals_y = table_bottom - totals_h - 10
                rows_area_bottom = totals_y - 10
                head_h = (34 if compact else 40)
                row_h = (42 if compact else 56)
                rows_fit = int(max(1, (rows_area_bottom - (table_top + head_h)) // row_h))
                max_rows = min(want_rows, rows_fit)

                y_end = _draw_table(
                    x,
                    table_top,
                    content_w,
                    cols,
                    head_bg=primary,
                    head_fg=(255, 255, 255),
                    zebra=None,
                    max_rows=max_rows,
                    head_h=head_h,
                    row_h=row_h,
                    compact=compact,
                    table_bottom=table_bottom,
                )

                # totals small grid (like template)
                grid_w, grid_h = totals_w, totals_h
                gx = x + max(0, int((content_w - grid_w) / 2))
                gy = totals_y
                draw.rectangle((gx, gy, gx + grid_w, gy + grid_h), outline=line, width=2, fill=(250, 250, 250))
                labels = [("SUBTOTAL", subtotal_print)]
                if show_desc:
                    labels.append((desc_grid_label, -descuento_print))
                    labels.append(("NETO", neto_print))
                labels.extend([("IVA", iva_print), ("TRASLADO", traslado_print), ("TOTAL", total_print)])
                ry = gy
                for lab, val in labels:
                    is_total = (lab == "TOTAL")
                    h = 30 if not is_total else 34
                    if is_total:
                        draw.rectangle((gx, ry, gx + grid_w, ry + h), fill=(245, 245, 245))
                    draw.line((gx, ry + h, gx + grid_w, ry + h), fill=(210, 210, 210), width=1)
                    draw.text((gx + 14, ry + 7), lab, font=f12b if is_total else f12, fill=txt)
                    draw.text((gx + grid_w - 14, ry + 7), _money_signed(val), font=f12b if is_total else f12, fill=txt, anchor="ra")
                    ry += h

            # Guardar multipage PDF
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            portada_im.save(
                str(pdf_path),
                "PDF",
                resolution=150.0,
                save_all=True,
                append_images=[cot_im, term_im, banco_im],
            )
        except Exception:
            try:
                # Log explícito: si este fallback falla, antes solo veíamos el error de Pango y no sabíamos por qué.
                (debug_dir / "quote_pdf_pillow.log").open("a", encoding="utf-8").write(
                    f"[{datetime.now().isoformat()}] id_cotizacion={id_cotizacion} marca={marca_key} err={_tb.format_exc()}\n"
                )
            except Exception:
                pass
            pass

        # Fallback 2: wkhtmltopdf (si existe)
        try:
            _wkhtmltopdf_generate(html, pdf_path, base_url)
        except Exception:
            pass
        try:
            if pdf_path.exists() and pdf_path.is_file():
                return FileResponse(
                    str(pdf_path),
                    media_type="application/pdf",
                    headers={"Content-Disposition": _disp(pdf_path.name)},
                )
        except Exception:
            pass

        # Fallback: si existe un pdf_path previo, úsalo (solo si NO estamos forzando refresh).
        if not refresh_assets:
            try:
                p0 = (cot.get("pdf_path") or "").strip()
                if p0:
                    root = Path(__file__).resolve().parents[2]
                    p = Path(p0)
                    if not p.is_absolute():
                        p = root / p0
                    if p.exists() and p.is_file():
                        return FileResponse(
                            str(p),
                            media_type="application/pdf",
                            headers={"Content-Disposition": _disp(p.name)},
                        )
            except Exception:
                pass
        # Último recurso: mostrar HTML con error claro (evita "Internal Server Error" opaco)
        err_txt = str(e)
        hint = ""
        if "pango_context_set_round_glyph_positions" in err_txt or "libpango" in err_txt:
            hint = (
                "Este servidor tiene una incompatibilidad con Pango (WeasyPrint no puede renderizar). "
                "Solución recomendada: usar wkhtmltopdf (instalar binario) o actualizar librerías del sistema."
            )
        msg = f"No pude generar el PDF. Error: {err_txt}" + (f"\\n\\n{hint}" if hint else "")
        dbg = {
            "id_cotizacion": id_cotizacion,
            "marca": marca_raw,
            "marca_key": marca_key,
            "missing_assets": missing_assets,
            "assets": assets_used,
            "pdf_path": str(pdf_path),
        }
        return HTMLResponse(
            f"<html><body style='font-family:Arial,sans-serif'>"
            f"<h2>{msg}</h2>"
            f"<pre>{json.dumps(dbg, ensure_ascii=False, indent=2)}</pre>"
            f"</body></html>"
        )
    # debug HTML para inspección si el PDF sale en blanco
    try:
        (debug_dir / f"quote_{id_cotizacion}.html").write_text(html, encoding="utf-8")
    except Exception:
        pass

    # guardar ruta si existe columna
    if "pdf_path" in cot:
        with get_connection() as cn:
            cn.execute(text("UPDATE cotizaciones SET pdf_path=:p WHERE id_cotizacion=:id"),
                       {"p": str(pdf_path), "id": id_cotizacion})
            cn.commit()

    return FileResponse(
        str(pdf_path),
        media_type="application/pdf",
        headers={"Content-Disposition": _disp(pdf_path.name)},
    )
