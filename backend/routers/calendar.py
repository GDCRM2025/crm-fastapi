from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from datetime import datetime
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.db import get_db
try:
    from backend.routers.auth import get_current_user  # type: ignore
except Exception:  # pragma: no cover
    def get_current_user():  # type: ignore
        return {"rol": "Admin"}

router = APIRouter(prefix="/calendar", tags=["calendar"])

def _role_key(me) -> str:
    raw = str((me or {}).get("rol") or (me or {}).get("role") or "").strip().lower()
    if not raw:
        return ""
    try:
        import unicodedata
        raw = unicodedata.normalize("NFD", raw)
        raw = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
    except Exception:
        pass
    raw = "".join(ch for ch in raw if ch.isalnum())
    return raw

def _require_ops_calendar(me) -> None:
    rk = _role_key(me)
    if not rk:
        raise HTTPException(403, detail="Sin permiso")
    allowed = (
        ("admin" in rk)
        or ("superadmin" in rk)
        or ("operac" in rk)
        or ("compra" in rk)
        or ("bodeg" in rk)
        or ("mice" in rk)
        or ("operador" in rk)
        or ("conductor" in rk)
        or ("chofer" in rk)
        or ("chop" in rk)
    )
    if not allowed:
        raise HTTPException(403, detail="Sin permiso")

def _cols(db: Session, table: str) -> set[str]:
    rows = db.execute(text("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name=:t
    """), {"t": table}).fetchall()
    return {r[0] for r in rows}

def _table_exists(db: Session, table: str) -> bool:
    return bool(db.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}).scalar())


@router.get("/pre-agenda")
def pre_agenda(estado: str | None = None, limit: int = 100, db: Session = Depends(get_db), me=Depends(get_current_user)):
    q = """
        SELECT ec.id_evento, ec.titulo, ec.fecha_inicio, ec.fecha_termino, ec.estado,
               l.nombre_cliente, l.telefono, l.email,
               m.nombre AS marca,
               co.nombre AS comuna
        FROM eventos_calendario ec
        JOIN leads l ON l.id_lead=ec.id_lead
        LEFT JOIN marcas m ON m.id_marca=l.id_marca
        LEFT JOIN comunas co ON co.id_comuna=l.id_comuna
        WHERE 1=1
    """
    params = {"limit": max(1, min(500, int(limit)))}
    if estado:
        q += " AND lower(ec.estado)=lower(:estado)"
        params["estado"] = estado

    q += " ORDER BY ec.fecha_inicio DESC NULLS LAST, ec.id_evento DESC LIMIT :limit"
    items = db.execute(text(q), params).mappings().all()
    return {"ok": True, "items": list(items)}

@router.get("/events")
def events(
    from_date: str | None = Query(default=None, description="YYYY-MM-DD"),
    to_date: str | None = Query(default=None, description="YYYY-MM-DD"),
    limit: int = Query(default=2000, ge=1, le=5000),
    db: Session = Depends(get_db),
    me=Depends(get_current_user),
):
    """
    Normaliza eventos desde eventos_calendario para el frontend.
    Soporta esquemas antiguos (titulo/fecha_inicio/fecha_termino/descripcion) y nuevos (title/start_at/end_at/description).
    """
    # Si no existe eventos_calendario, igual entregamos "lo que ya está agendado" desde leads
    # (calendar_start/calendar_end se setean al aprobar /tools/agenda/{id}/approve).
    if not _table_exists(db, "eventos_calendario"):
        if not _table_exists(db, "leads"):
            return {"ok": True, "items": []}

        cols_l = _cols(db, "leads")
        if "calendar_start" not in cols_l or "calendar_end" not in cols_l:
            return {"ok": True, "items": []}

        # columnas opcionales
        title_col = "pre_title" if "pre_title" in cols_l else ("cliente" if "cliente" in cols_l else None)
        loc_col = "pre_location" if "pre_location" in cols_l else ("direccion" if "direccion" in cols_l else None)
        ops_col = "pre_ops" if "pre_ops" in cols_l else None
        join_marca = _table_exists(db, "marcas") and ("id_marca" in cols_l)
        join_comuna = _table_exists(db, "comunas") and ("id_comuna" in cols_l)

        where = ["l.calendar_start IS NOT NULL", "l.calendar_end IS NOT NULL"]
        params: dict = {"limit": int(limit)}
        if from_date:
            where.append("l.calendar_start::date >= :fd")
            params["fd"] = from_date
        if to_date:
            where.append("l.calendar_start::date <= :td")
            params["td"] = to_date

        title_expr = f"COALESCE(l.{title_col}, l.cliente, '')" if title_col else "COALESCE(l.cliente, '')"
        loc_expr = "''"
        if loc_col:
            loc_expr = f"COALESCE(l.{loc_col}, '')"
        if join_comuna:
            loc_expr = f"NULLIF(COALESCE({loc_expr}, ''),'') || CASE WHEN co.nombre IS NOT NULL AND co.nombre<>'' THEN ' — '||co.nombre ELSE '' END"
        ops_expr = f"COALESCE(l.{ops_col}, 0)" if ops_col else "0"
        marca_expr = "''"
        if join_marca:
            marca_expr = "COALESCE(m.marca, m.nombre, '')"
        comuna_expr = "''"
        if join_comuna:
            comuna_expr = "COALESCE(co.nombre, '')"

        q = f"""
          SELECT
            l.id_lead AS id_evento,
            l.id_lead,
            {title_expr} AS title,
            l.calendar_start AS start_at,
            l.calendar_end AS end_at,
            {loc_expr} AS location,
            {ops_expr} AS ops,
            {marca_expr} AS marca,
            {comuna_expr} AS comuna
          FROM leads l
          {"LEFT JOIN marcas m ON m.id_marca=l.id_marca" if join_marca else ""}
          {"LEFT JOIN comunas co ON co.id_comuna=l.id_comuna" if join_comuna else ""}
          WHERE {" AND ".join(where)}
          ORDER BY l.calendar_start DESC NULLS LAST, l.id_lead DESC
          LIMIT :limit
        """
        rows = db.execute(text(q), params).mappings().all()
        items = []
        for r in rows:
            items.append({
                "id_evento": r.get("id_evento"),
                "id_lead": r.get("id_lead"),
                "title": r.get("title") or "",
                "start": str(r.get("start_at") or ""),
                "end": str(r.get("end_at") or ""),
                "location": r.get("location") or "",
                "description": "",
                "ops": int(r.get("ops") or 0),
                "estado": "aprobado",
                "marca": r.get("marca") or "",
                "comuna": r.get("comuna") or "",
            })
        return {"ok": True, "items": items}

    cols = _cols(db, "eventos_calendario")

    title_col = "title" if "title" in cols else ("titulo" if "titulo" in cols else None)
    start_col = "start_at" if "start_at" in cols else ("fecha_inicio" if "fecha_inicio" in cols else None)
    end_col = "end_at" if "end_at" in cols else ("fecha_termino" if "fecha_termino" in cols else None)
    desc_col = "description" if "description" in cols else ("descripcion" if "descripcion" in cols else None)
    loc_col = "location" if "location" in cols else ("lugar" if "lugar" in cols else None)

    if not title_col or not start_col or not end_col:
        raise HTTPException(500, detail="eventos_calendario no tiene columnas mínimas")

    where = ["1=1"]
    params = {"limit": int(limit)}
    if from_date:
        where.append(f"{start_col}::date >= :fd")
        params["fd"] = from_date
    if to_date:
        where.append(f"{start_col}::date <= :td")
        params["td"] = to_date

    # Si podemos, adjuntamos marca/comuna (para filtros en Operaciones).
    leads_cols = _cols(db, "leads") if _table_exists(db, "leads") else set()
    join_marca = _table_exists(db, "marcas") and ("id_marca" in leads_cols)
    join_comuna = _table_exists(db, "comunas") and ("id_comuna" in leads_cols)
    marca_expr = "''"
    comuna_expr = "''"
    join_sql = ""
    if join_marca or join_comuna:
        join_sql += " LEFT JOIN leads l ON l.id_lead=eventos_calendario.id_lead "
    if join_marca:
        join_sql += " LEFT JOIN marcas m ON m.id_marca=l.id_marca "
        marca_expr = "COALESCE(m.marca, m.nombre, '')"
    if join_comuna:
        join_sql += " LEFT JOIN comunas co ON co.id_comuna=l.id_comuna "
        comuna_expr = "COALESCE(co.nombre, '')"

    q = f"""
      SELECT
        id_evento,
        id_lead,
        {title_col} AS title,
        {start_col} AS start_at,
        {end_col} AS end_at,
        {desc_col} AS description,
        {loc_col} AS location,
        COALESCE(ops, 0) AS ops,
        COALESCE(estado, '') AS estado,
        {marca_expr} AS marca,
        {comuna_expr} AS comuna
      FROM eventos_calendario
      {join_sql}
      WHERE {" AND ".join(where)}
      ORDER BY {start_col} DESC NULLS LAST, id_evento DESC
      LIMIT :limit
    """
    rows = db.execute(text(q), params).mappings().all()
    items = []
    for r in rows:
        items.append({
            "id_evento": r.get("id_evento"),
            "id_lead": r.get("id_lead"),
            "title": r.get("title") or "",
            "start": str(r.get("start_at") or ""),
            "end": str(r.get("end_at") or ""),
            "location": r.get("location") or "",
            "description": r.get("description") or "",
            "ops": int(r.get("ops") or 0),
            "estado": r.get("estado") or "",
            "marca": r.get("marca") or "",
            "comuna": r.get("comuna") or "",
        })
    return {"ok": True, "items": items}

@router.get("/lead_info")
def lead_info(
    id_lead: int = Query(..., ge=1),
    db: Session = Depends(get_db),
    me=Depends(get_current_user),
):
    """
    Detalle de un evento/lead para Operaciones/Chofer/Operador.
    Importante: NO depende de permisos de Leads (ventas), porque esto es información operativa.
    """
    _require_ops_calendar(me)

    if not _table_exists(db, "leads"):
        raise HTTPException(404, detail="Leads no disponible")

    cols_l = _cols(db, "leads")
    join_m = _table_exists(db, "marcas") and ("id_marca" in cols_l)
    join_c = _table_exists(db, "comunas") and ("id_comuna" in cols_l)
    join_e = _table_exists(db, "estados_lead") and ("id_estado" in cols_l)

    row = db.execute(
        text(
            f"""
            SELECT
              l.id_lead,
              COALESCE(l.cliente,'') AS cliente,
              COALESCE(l.telefono,'') AS telefono,
              COALESCE(l.direccion,'') AS direccion,
              COALESCE(l.notas,'') AS notas,
              COALESCE(l.fecha_evento::text,'') AS fecha_evento,
              { "COALESCE(m.marca,m.nombre,'') AS marca," if join_m else "'' AS marca," }
              { "COALESCE(co.nombre,'') AS comuna," if join_c else "'' AS comuna," }
              { "COALESCE(e.nombre,'') AS estado" if join_e else "'' AS estado" }
            FROM leads l
            { "LEFT JOIN marcas m ON m.id_marca=l.id_marca" if join_m else "" }
            { "LEFT JOIN comunas co ON co.id_comuna=l.id_comuna" if join_c else "" }
            { "LEFT JOIN estados_lead e ON e.id_estado=l.id_estado" if join_e else "" }
            WHERE l.id_lead=:id
            LIMIT 1
            """
        ),
        {"id": int(id_lead)},
    ).mappings().first()
    if not row:
        raise HTTPException(404, detail="Lead no existe")

    start_at = None
    end_at = None
    # Preferimos eventos_calendario si existe.
    if _table_exists(db, "eventos_calendario"):
        cols_ev = _cols(db, "eventos_calendario")
        s_col = "start_at" if "start_at" in cols_ev else ("fecha_inicio" if "fecha_inicio" in cols_ev else None)
        e_col = "end_at" if "end_at" in cols_ev else ("fecha_termino" if "fecha_termino" in cols_ev else None)
        if s_col and e_col and ("id_lead" in cols_ev):
            ev = db.execute(
                text(
                    f"""
                    SELECT {s_col} AS s, {e_col} AS e
                    FROM eventos_calendario
                    WHERE id_lead=:id
                    ORDER BY {s_col} DESC NULLS LAST
                    LIMIT 1
                    """
                ),
                {"id": int(id_lead)},
            ).first()
            if ev:
                start_at, end_at = ev[0], ev[1]
    # Fallback: leads.calendar_start/calendar_end
    if (start_at is None or end_at is None) and ("calendar_start" in cols_l) and ("calendar_end" in cols_l):
        ev2 = db.execute(
            text("SELECT calendar_start, calendar_end FROM leads WHERE id_lead=:id"),
            {"id": int(id_lead)},
        ).first()
        if ev2:
            start_at = start_at or ev2[0]
            end_at = end_at or ev2[1]

    def _fmt_hm(x) -> str:
        if not x:
            return ""
        if isinstance(x, str):
            return x
        try:
            if isinstance(x, datetime):
                return x.strftime("%H:%M")
        except Exception:
            pass
        return str(x)

    horario = ""
    dur_h = ""
    try:
        if start_at and end_at and hasattr(start_at, "timestamp") and hasattr(end_at, "timestamp"):
            horario = f"{_fmt_hm(start_at)}-{_fmt_hm(end_at)}"
            sec = (end_at - start_at).total_seconds()
            if sec > 0:
                dur = sec / 3600.0
                dur_h = f"{dur:.1f}"
    except Exception:
        pass

    out = dict(row)
    out["horario"] = horario
    out["duracion_horas"] = dur_h
    return {"ok": True, "lead": out}


@router.post("/{id_evento}/approve")
def approve(id_evento: int, db: Session = Depends(get_db), me=Depends(get_current_user)):
    db.execute(text("""
        UPDATE eventos_calendario
        SET estado='aprobado', aprobado_por=:by, updated_at=now()
        WHERE id_evento=:id
    """), {"id": id_evento, "by": me.get("nombre") or "admin"})
    db.commit()
    return {"ok": True}


@router.post("/{id_evento}/reject")
def reject(id_evento: int, db: Session = Depends(get_db), me=Depends(get_current_user)):
    db.execute(text("""
        UPDATE eventos_calendario
        SET estado='rechazado', aprobado_por=:by, updated_at=now()
        WHERE id_evento=:id
    """), {"id": id_evento, "by": me.get("nombre") or "admin"})
    db.commit()
    return {"ok": True}
