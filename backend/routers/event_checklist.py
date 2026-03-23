from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.core.activity_log import log_activity
from backend.core.event_checklist import list_event_checklists, upsert_event_checklist

try:
    from backend.routers.auth import get_current_user  # type: ignore
except Exception:  # pragma: no cover
    def get_current_user():  # type: ignore
        return {"id": "dev", "role": "ADMIN", "username": "dev", "name": "Dev"}


router = APIRouter(prefix="/checklist", tags=["checklist"])


def _uid(user: dict) -> Optional[int]:
    raw = user.get("id")
    if str(raw or "").isdigit():
        return int(raw)
    return None


def _uname(user: dict) -> str:
    return str(user.get("username") or user.get("email") or user.get("name") or user.get("id") or "").strip()[:200]


def _role(user: dict) -> str:
    return str(user.get("role") or user.get("rol") or "").upper()


def _is_admin(role: str) -> bool:
    return role in ("ADMIN", "SUPERADMIN")


def _col_exists(db: Session, table: str, col: str) -> bool:
    try:
        return bool(
            db.execute(
                text(
                    """
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema='public' AND table_name=:t AND column_name=:c
                    LIMIT 1
                    """
                ),
                {"t": table, "c": col},
            ).scalar()
        )
    except Exception:
        return False


def _table_exists(db: Session, table: str) -> bool:
    try:
        return bool(db.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"public.{table}"}).scalar())
    except Exception:
        return False


def _lead_name_expr(db: Session) -> str:
    # Compatibilidad: algunos deploys usan nombre_cliente, otros cliente.
    if _col_exists(db, "leads", "nombre_cliente"):
        return "COALESCE(NULLIF(btrim(l.nombre_cliente),''), '')"
    if _col_exists(db, "leads", "cliente"):
        return "COALESCE(NULLIF(btrim(l.cliente),''), '')"
    return "''"


def _estado_id(db: Session, like: str) -> Optional[int]:
    try:
        v = db.execute(
            text(
                "SELECT id_estado FROM public.estados_lead WHERE UPPER(nombre) LIKE :n ORDER BY id_estado LIMIT 1"
            ),
            {"n": f"%{(like or '').upper()}%"},
        ).scalar()
        return int(v) if v is not None else None
    except Exception:
        return None


@router.get("/events")
def events_for_day(
    day: str = Query(default_factory=lambda: date.today().isoformat()),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    Checklist de eventos del día (para ejecutivos y ops).
    - Fuente: leads CONFIRMADOS con fecha_evento = day (o calendar_start si no hay fecha_evento).
    - Retorna campos + flags de completitud + checklist guardado del usuario.
    """
    try:
        d = date.fromisoformat(str(day))
    except Exception:
        raise HTTPException(400, "day inválido (YYYY-MM-DD)")

    # Never 500: este endpoint no puede dejar el frontend pegado en "Cargando".
    try:
        if not _table_exists(db, "leads"):
            return {"ok": True, "day": d.isoformat(), "items": [], "error": "Tabla leads no existe"}

        confirmado_id = _estado_id(db, "CONFIRM")
        if not confirmado_id:
            return {"ok": True, "day": d.isoformat(), "items": []}

        role = _role(user)
        uid = _uid(user)

        # Scope: Admin ve todo; no-admin respeta marcas si el token trae marcas[].
        marcas = [int(x) for x in (user.get("marcas") or []) if str(x).isdigit()]
        only_own = not _is_admin(role)

        # columnas opcionales
        has_fecha = _col_exists(db, "leads", "fecha_evento")
        has_pre = _col_exists(db, "leads", "pre_start") and _col_exists(db, "leads", "pre_end")
        has_cal = _col_exists(db, "leads", "calendar_start") and _col_exists(db, "leads", "calendar_end")
        has_ops = _col_exists(db, "leads", "pre_ops")
        has_pre_mont = _col_exists(db, "leads", "pre_montaje_text")
        has_pre_prod = _col_exists(db, "leads", "pre_products_text")
        has_tel = _col_exists(db, "leads", "telefono")
        has_dir = _col_exists(db, "leads", "direccion")

        if has_fecha:
            date_expr = "DATE(l.fecha_evento)"
        elif has_cal:
            date_expr = "DATE(l.calendar_start)"
        else:
            return {"ok": True, "day": d.isoformat(), "items": []}

        start_expr = "l.calendar_start" if has_cal else ("l.pre_start" if has_pre else "NULL")
        end_expr = "l.calendar_end" if has_cal else ("l.pre_end" if has_pre else "NULL")
        ops_expr = "COALESCE(l.pre_ops,0)" if has_ops else "0"
        montaje_expr = "COALESCE(l.pre_montaje_text,'')" if has_pre_mont else "''"
        prod_expr = "COALESCE(l.pre_products_text,'')" if has_pre_prod else "''"
        tel_expr = "COALESCE(l.telefono,'')" if has_tel else "''"
        dir_expr = "COALESCE(l.direccion,'')" if has_dir else "''"

        has_id_marca = _col_exists(db, "leads", "id_marca")
        has_id_comuna = _col_exists(db, "leads", "id_comuna")
        join_marcas = has_id_marca and _table_exists(db, "marcas")
        join_comunas = has_id_comuna and _table_exists(db, "comunas")

        marca_sel = "COALESCE(m.nombre, m.marca,'')" if join_marcas else "''"
        comuna_sel = "COALESCE(c.nombre,'')" if join_comunas else "''"
        join_sql = ""
        if join_marcas:
            join_sql += " LEFT JOIN public.marcas m ON m.id_marca=l.id_marca "
        if join_comunas:
            join_sql += " LEFT JOIN public.comunas c ON c.id_comuna=l.id_comuna "

        where = [f"l.id_estado = :conf", f"{date_expr} = :d"]
        params: Dict[str, Any] = {"conf": int(confirmado_id), "d": str(d)}
        if only_own and marcas and has_id_marca:
            where.append("l.id_marca = ANY(:marcas)")
            params["marcas"] = marcas

        where_sql = " AND ".join(where)
        name_expr = _lead_name_expr(db)

        sql = f"""
          SELECT
            l.id_lead::bigint AS id_lead,
            {name_expr} AS cliente,
            {marca_sel} AS marca,
            {comuna_sel} AS comuna,
            {start_expr} AS start_at,
            {end_expr} AS end_at,
            {ops_expr}::int AS ops,
            {montaje_expr} AS montaje_text,
            {prod_expr} AS productos_text,
            {tel_expr} AS telefono,
            {dir_expr} AS direccion
          FROM public.leads l
          {join_sql}
          WHERE {where_sql}
          ORDER BY COALESCE({start_expr}, now()) ASC, l.id_lead ASC
        """

        rows = db.execute(text(sql), params).mappings().all()
        saved = list_event_checklists(db, event_day=d, user_id=uid)
    except Exception as e:
        return {"ok": True, "day": d.isoformat(), "items": [], "error": f"{type(e).__name__}: {str(e)[:240]}"}

    items: List[Dict[str, Any]] = []
    for r in rows:
        lid = int(r["id_lead"])
        start_at = r.get("start_at")
        end_at = r.get("end_at")
        items.append(
            {
                "id_lead": lid,
                "cliente": r.get("cliente") or "",
                "marca": r.get("marca") or "",
                "comuna": r.get("comuna") or "",
                "start_at": str(start_at) if start_at else "",
                "end_at": str(end_at) if end_at else "",
                "ops": int(r.get("ops") or 0),
                "montaje_text": r.get("montaje_text") or "",
                "productos_text": r.get("productos_text") or "",
                "telefono": r.get("telefono") or "",
                "direccion": r.get("direccion") or "",
                "missing": {
                    "cliente": not bool((r.get("cliente") or "").strip()),
                    "comuna": not bool((r.get("comuna") or "").strip()),
                    "marca": not bool((r.get("marca") or "").strip()),
                    "equipos": not bool((r.get("montaje_text") or "").strip()) and not bool((r.get("productos_text") or "").strip()),
                    "inicio": not bool(start_at),
                    "fin": not bool(end_at),
                    "ops": int(r.get("ops") or 0) <= 0,
                },
                "checklist": saved.get(lid) or {"items": {}, "notes": "", "updated_at": ""},
            }
        )

    return {"ok": True, "day": d.isoformat(), "items": items}


@router.post("/events/{id_lead}/confirm")
def confirm_event(
    id_lead: int,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    try:
        d = date.fromisoformat(str(payload.get("day") or date.today().isoformat()))
    except Exception:
        raise HTTPException(400, "day inválido (YYYY-MM-DD)")

    items = payload.get("items") or {}
    if not isinstance(items, dict):
        raise HTTPException(400, "items inválido")
    notes = str(payload.get("notes") or "").strip()

    uid = _uid(user)
    who = _uname(user)

    out = upsert_event_checklist(
        db,
        id_lead=int(id_lead),
        event_day=d,
        user_id=uid,
        username=who,
        items=items,
        notes=notes,
    )

    # Registrar en notas del lead (best-effort, no bloqueante).
    try:
        checked = sum(1 for k, v in (items or {}).items() if bool(v))
        total = len(items.keys()) if items else 0
        pct = int(round((checked / total) * 100)) if total else 0
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        msg = f"[CHECKLIST {ts}] {who}: {pct}% ({checked}/{total}) · Día {d.isoformat()}"
        if notes:
            msg += f" · {notes[:160]}"
        try:
            db.execute(
                text(
                    """
                    UPDATE public.leads
                    SET notas = CASE
                      WHEN notas IS NULL OR notas='' THEN :n
                      ELSE notas || E'\n' || :n
                    END,
                    updated_at=now()
                    WHERE id_lead=:id
                    """
                ),
                {"id": int(id_lead), "n": msg},
            )
        except Exception:
            pass
        try:
            log_activity(
                db.connection(),
                username=who,
                user_id=uid,
                role=_role(user),
                action="EVENT_CHECKLIST_CONFIRMED",
                entity_type="lead",
                entity_id=int(id_lead),
                meta={"day": d.isoformat(), "pct": pct, "checked": checked, "total": total},
            )
        except Exception:
            pass
    except Exception:
        pass

    try:
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
    return out
