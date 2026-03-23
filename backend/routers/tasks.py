from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.core.activity_log import log_activity
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


def _uname(user: dict) -> str:
    return str(user.get("username") or user.get("email") or user.get("name") or user.get("id") or "").strip()[:200]


@router.post("/sync")
def sync_tasks(db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    """
    Genera tareas automáticas (idempotente) para el usuario actual.
    """
    uid = _uid(user)
    out = upsert_mvp_tasks_for_user(db, user_id=uid, username=_uname(user), role=_role(user))
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
    uid = _uid(user)
    return list_tasks(db, assigned_user_id=uid, status=status, limit=limit, offset=offset)


@router.post("/{id_task}/done")
def mark_done(id_task: int, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    uid = _uid(user)
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


@router.post("/{id_task}/skip")
def mark_skipped(
    id_task: int,
    payload: Dict[str, Any] = Body(default_factory=dict),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    uid = _uid(user)
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
