from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.core.activity_log import log_activity
from backend.core import tasks as core_tasks
from backend.core.tasks import (
    complete_task,
    ensure_tasks_table,
    list_tasks,
    skip_task,
    upsert_mvp_tasks_for_user,
)

try:
    from backend.routers.auth import get_current_user  # type: ignore
except Exception:  # pragma: no cover
    def get_current_user():  # type: ignore
        return {"id": "dev", "role": "ADMIN", "username": "dev", "name": "Dev"}


router = APIRouter(prefix="/tasks", tags=["tasks"])


def _role(user: dict) -> str:
    return str(user.get("role") or user.get("rol") or "").upper()


def _uid(user: dict) -> int:
    raw = user.get("id")
    if str(raw or "").isdigit():
        return int(raw)
    raise HTTPException(401, "Usuario inválido")


def _table_exists(db: Session, table: str) -> bool:
    try:
        return bool(db.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"public.{table}"}).scalar())
    except Exception:
        return False


def _fetch_marcas_for_uid(db: Session, uid: int) -> list[int]:
    """
    Fallback cuando el token no trae `marcas` (o viene vacío por cuentas legacy).
    """
    if not _table_exists(db, "usuarios_marcas"):
        return []
    try:
        rows = db.execute(
            text("SELECT id_marca FROM public.usuarios_marcas WHERE id_usuario=:u ORDER BY id_marca"),
            {"u": int(uid)},
        ).fetchall()
        out: list[int] = []
        for r in rows:
            try:
                out.append(int(r[0]))
            except Exception:
                pass
        return out
    except Exception:
        return []


def _resolve_uid(db: Session, user: dict) -> int:
    """
    Compat: algunos tokens vienen con sub/username string (ej: 'greengd').
    Intentamos mapear a public.usuarios.id_usuario para que tasks funcione.
    """
    try:
        return _uid(user)
    except Exception:
        pass
    if not _table_exists(db, "usuarios"):
        raise HTTPException(401, "Usuario inválido")
    cand = [
        str(user.get("username") or "").strip(),
        str(user.get("id") or "").strip(),
        str(user.get("name") or "").strip(),
    ]
    cand = [c for c in cand if c]
    if not cand:
        raise HTTPException(401, "Usuario inválido")
    try:
        for c in cand:
            v = db.execute(
                text(
                    """
                    SELECT id_usuario
                    FROM public.usuarios
                    WHERE email=:u OR username=:u
                    ORDER BY id_usuario
                    LIMIT 1
                    """
                ),
                {"u": c},
            ).scalar()
            if v is not None and str(v).isdigit():
                return int(v)
    except Exception:
        pass
    raise HTTPException(401, "Usuario inválido")


def _uname(user: dict) -> str:
    return str(user.get("username") or user.get("email") or user.get("name") or user.get("id") or "").strip()[:200]


@router.post("/sync")
def sync_tasks(db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    """
    Genera tareas automáticas (idempotente) para el usuario actual.
    """
    uid = _resolve_uid(db, user)
    marcas_token = list(user.get("marcas") or [])
    marcas_db = _fetch_marcas_for_uid(db, uid)
    marcas = marcas_token or marcas_db
    if not marcas:
        marcas = _fetch_marcas_for_uid(db, uid)
    try:
        out = upsert_mvp_tasks_for_user(
            db,
            user_id=uid,
            username=_uname(user),
            role=_role(user),
            marcas=marcas,
        )
    except Exception as e:
        # Nunca 500: si no hay permisos DDL o falta alguna tabla, degradar silenciosamente.
        return {"ok": True, "created": 0, "skipped": 0, "disabled": True, "error": str(e)[:200]}
    try:
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
    return out


@router.get("")
@router.get("/")
def get_tasks(
    status: str = Query("open"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    uid = _resolve_uid(db, user)
    return list_tasks(db, assigned_user_id=uid, status=status, limit=limit, offset=offset)


@router.post("/{id_task}/done")
def mark_done(id_task: int, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    uid = _resolve_uid(db, user)
    who = _uname(user)
    ensure_tasks_table(db)
    out = complete_task(db, id_task=id_task, completed_by=who)
    try:
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
    try:
        cn = db.connection()
        log_activity(
            cn,
            username=who,
            user_id=uid,
            role=_role(user),
            action="TASK_DONE",
            entity_type="task",
            entity_id=int(id_task),
            meta={},
        )
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
    return out


@router.get("/summary")
def summary(db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    """
    Conteo rápido para UI/badges (no crea tareas).
    """
    uid = _resolve_uid(db, user)
    try:
        ensure_tasks_table(db)
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return {"ok": True, "open_total": 0, "overdue_total": 0, "open_contactar": 0, "overdue_contactar": 0, "disabled": True}
    try:
        row = db.execute(
            text(
                """
                SELECT
                  COUNT(*) FILTER (WHERE status='open')::int AS open_total,
                  COUNT(*) FILTER (WHERE status='open' AND due_at IS NOT NULL AND due_at < now())::int AS overdue_total,
                  COUNT(*) FILTER (WHERE status='open' AND kind='CONTACTAR_LEAD')::int AS open_contactar,
                  COUNT(*) FILTER (WHERE status='open' AND kind='CONTACTAR_LEAD' AND due_at IS NOT NULL AND due_at < now())::int AS overdue_contactar
                FROM public.tasks
                WHERE assigned_user_id=:uid
                """
            ),
            {"uid": int(uid)},
        ).mappings().first()
        return {"ok": True, **(dict(row) if row else {})}
    except Exception:
        return {"ok": True, "open_total": 0, "overdue_total": 0, "open_contactar": 0, "overdue_contactar": 0}


@router.get("/debug")
def debug_tasks(db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    """
    Diagnóstico rápido para entender por qué salen 0 tareas.
    No expone contraseñas ni datos sensibles.
    """
    uid = _resolve_uid(db, user)
    role = _role(user)
    uname = _uname(user)
    marcas = list(user.get("marcas") or [])

    # Intenta usar helpers del core (best-effort).
    try:
        user_keys = core_tasks._user_match_keys(db, user_id=int(uid), username=uname)  # type: ignore[attr-defined]
    except Exception:
        user_keys = [str(uid), uname]
    try:
        has_id_usuario = bool(core_tasks._col_exists(db, "leads", "id_usuario"))  # type: ignore[attr-defined]
    except Exception:
        has_id_usuario = None
    try:
        has_id_marca = bool(core_tasks._col_exists(db, "leads", "id_marca"))  # type: ignore[attr-defined]
    except Exception:
        has_id_marca = None
    try:
        has_marca_txt = bool(core_tasks._col_exists(db, "leads", "marca"))  # type: ignore[attr-defined]
    except Exception:
        has_marca_txt = None

    nuevo_id = core_tasks._estado_id_like(db, "%NUEV%", 1)  # type: ignore[attr-defined]
    contactado_id = core_tasks._estado_id_like(db, "%CONTACT%", 2)  # type: ignore[attr-defined]
    confirmado_id = core_tasks._estado_id_like(db, "CONFIRM%", 4)  # type: ignore[attr-defined]

    marcas_ids: list[int] = []
    for m in marcas:
        try:
            marcas_ids.append(int(m))
        except Exception:
            pass
    marcas_upper: list[str] = []
    try:
        if marcas_ids:
            rows = db.execute(
                text("SELECT nombre FROM public.marcas WHERE id_marca = ANY(CAST(:mids AS int[]))"),
                {"mids": marcas_ids},
            ).fetchall()
            marcas_upper = [str(r[0] or "").strip().upper() for r in rows if r and str(r[0] or "").strip()]
    except Exception:
        marcas_upper = []

    try:
        assigned_sql = core_tasks._assigned_to_user_sql()  # type: ignore[attr-defined]
        unassigned_sql = core_tasks._unassigned_sql()  # type: ignore[attr-defined]
        brand_sql = core_tasks._brand_filter_sql(db)  # type: ignore[attr-defined]
    except Exception:
        assigned_sql = "TRUE"
        unassigned_sql = "FALSE"
        brand_sql = "FALSE"

    scope_sql = assigned_sql
    if marcas_ids or marcas_upper:
        scope_sql = f"({assigned_sql} OR ({unassigned_sql} AND {brand_sql}))"

    params = {
        "uid": int(uid),
        "uname": (uname or "").strip()[:200],
        "user_keys": [str(x).strip().lower() for x in (user_keys or []) if str(x).strip()],
        "marcas_ids": marcas_ids or [0],
        "marcas_upper": marcas_upper or ["__NONE__"],
    }

    def _count(where_sql: str, more: dict | None = None):
        try:
            p = dict(params)
            if more:
                p.update(more)
            return int(db.execute(text(f"SELECT COUNT(*) FROM public.leads l WHERE {where_sql}"), p).scalar() or 0)
        except Exception as e:
            return {"error": f"{type(e).__name__}: {str(e)[:160]}"}

    return {
        "ok": True,
        "uid": int(uid),
        "username": uname,
        "role": role,
        "marcas_token": marcas_token,
        "marcas_db": marcas_db,
        "marcas_ids": marcas_ids,
        "marcas_names": marcas_upper,
        "schema": {"id_usuario": has_id_usuario, "id_marca": has_id_marca, "marca": has_marca_txt},
        "user_keys_sample": (params["user_keys"] or [])[:6],
        "estado_ids": {"nuevo": int(nuevo_id), "contactado": int(contactado_id), "confirmado": int(confirmado_id)},
        "scope_sql": scope_sql[:220],
        "counts": {
            "leads_total": _count("TRUE"),
            "nuevo_total": _count("l.id_estado=:st", {"st": int(nuevo_id)}),
            "nuevo_en_scope": _count(f"l.id_estado=:st AND {scope_sql}", {"st": int(nuevo_id)}),
            "contactado_en_scope": _count(f"l.id_estado=:st AND {scope_sql}", {"st": int(contactado_id)}),
            "confirmado_en_scope": _count(f"l.id_estado=:st AND {scope_sql}", {"st": int(confirmado_id)}),
        },
    }


@router.post("/{id_task}/skip")
def mark_skipped(
    id_task: int,
    payload: Dict[str, Any] = Body(default_factory=dict),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    uid = _resolve_uid(db, user)
    who = _uname(user)
    reason = str(payload.get("reason") or payload.get("motivo") or "").strip()
    if not reason:
        raise HTTPException(400, "reason requerido")
    ensure_tasks_table(db)
    out = skip_task(db, id_task=id_task, skipped_by=who, reason=reason)
    try:
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
    try:
        log_activity(
            db.connection(),  # type: ignore[arg-type]
            username=who,
            user_id=uid,
            role=_role(user),
            action="TASK_SKIPPED",
            entity_type="task",
            entity_id=int(id_task),
            meta={"reason": reason},
        )
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
    return out
