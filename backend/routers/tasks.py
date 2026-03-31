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
    raw = str(user.get("role") or user.get("rol") or "").strip()
    if raw.isdigit():
        mp = {
            "1": "ADMIN",
            "2": "EJECUTIVO DE VENTAS",
            "3": "JEFE DE OPERACIONES",
            "4": "BODEGUERO",
            "5": "COMPRAS",
            "6": "CONDUCTOR",
            "7": "OPERADOR",
            "8": "MICE",
            "9": "OPERADOR PATIO",
            "11": "FINANZAS",
        }
        return mp.get(raw, raw).upper()
    return raw.upper()


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


def _fetch_marcas_for_uid(db: Session, uid: int) -> list[int]:
    """
    Fallback cuando el token no trae `marcas` (o viene vacío por cuentas legacy).
    """
    # 1) Preferir tabla N:N `usuarios_marcas` (si existe). (fallback: `usuario_marcas`)
    join_table = None
    if _table_exists(db, "usuarios_marcas"):
        join_table = "usuarios_marcas"
    elif _table_exists(db, "usuario_marcas"):
        join_table = "usuario_marcas"

    if join_table:
        try:
            rows = db.execute(
                text(f"SELECT id_marca FROM public.{join_table} WHERE id_usuario=:u ORDER BY id_marca"),
                {"u": int(uid)},
            ).fetchall()
            out: list[int] = []
            for r in rows:
                try:
                    out.append(int(r[0]))
                except Exception:
                    pass
            if out:
                return out
        except Exception:
            pass

    # 2) Legacy: algunos esquemas guardan una sola marca en `usuarios.id_marca`.
    if _table_exists(db, "usuarios"):
        try:
            mid = db.execute(
                text(
                    """
                    SELECT id_marca
                    FROM public.usuarios
                    WHERE id_usuario=:u
                    LIMIT 1
                    """
                ),
                {"u": int(uid)},
            ).scalar()
            if mid is not None and str(mid).isdigit() and int(mid) > 0:
                return [int(mid)]
        except Exception:
            pass
    try:
        return []
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
        str(user.get("email") or "").strip(),
        str(user.get("sub") or "").strip(),
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


def _infer_marcas_from_leads(db: Session, user_keys: list[str]) -> list[int]:
    """
    Si el usuario no tiene `marcas` en token/usuarios_marcas (o el UID no calza),
    inferimos marcas desde leads asignados al usuario para no dejar tareas en 0.
    """
    try:
        if not _table_exists(db, "leads"):
            return []
        if not core_tasks._col_exists(db, "leads", "id_marca"):  # type: ignore[attr-defined]
            return []
        if not core_tasks._col_exists(db, "leads", "id_usuario"):  # type: ignore[attr-defined]
            return []
        rows = db.execute(
            text(
                """
                SELECT DISTINCT l.id_marca
                FROM public.leads l
                WHERE lower(NULLIF(btrim(COALESCE(l.id_usuario::text,'')) ,'')) = ANY(CAST(:user_keys AS text[]))
                  AND l.id_marca IS NOT NULL
                ORDER BY l.id_marca
                """
            ),
            {"user_keys": [str(x).strip().lower() for x in (user_keys or []) if str(x).strip()]},
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


def _uname(user: dict) -> str:
    return str(user.get("username") or user.get("email") or user.get("name") or user.get("id") or "").strip()[:200]

def _is_admin(user: dict) -> bool:
    r = _role(user)
    return r in ("ADMIN", "SUPERADMIN", "1")


def _user_info(db: Session, uid: int) -> dict[str, Any]:
    if not _table_exists(db, "usuarios"):
        return {"id": int(uid), "username": str(uid), "role": ""}
    try:
        cols_display = []
        for c in ("nombre", "name", "username", "email"):
            if _col_exists(db, "usuarios", c):
                cols_display.append(f"NULLIF(btrim({c}), '')")
        display_expr = "COALESCE(" + ", ".join(cols_display + ["id_usuario::text"]) + ") AS display"
        role_expr = "'' AS role"
        if _col_exists(db, "usuarios", "role") and _col_exists(db, "usuarios", "rol"):
            role_expr = "COALESCE(NULLIF(btrim(role::text),''), NULLIF(btrim(rol::text),''), '') AS role"
        elif _col_exists(db, "usuarios", "role"):
            role_expr = "COALESCE(NULLIF(btrim(role::text),''), '') AS role"
        elif _col_exists(db, "usuarios", "rol"):
            role_expr = "COALESCE(NULLIF(btrim(rol::text),''), '') AS role"

        row = db.execute(
            text(f"SELECT id_usuario, {display_expr}, {role_expr} FROM public.usuarios WHERE id_usuario=:u LIMIT 1"),
            {"u": int(uid)},
        ).mappings().first()
        if not row:
            return {"id": int(uid), "username": str(uid), "role": ""}
        return {
            "id": int(row.get("id_usuario") or uid),
            "username": str(row.get("display") or uid),
            "role": str(row.get("role") or ""),
        }
    except Exception:
        return {"id": int(uid), "username": str(uid), "role": ""}


@router.get("/context")
def context(db: Session = Depends(get_db), user: dict = Depends(get_current_user)) -> dict[str, Any]:
    uid = _resolve_uid(db, user)
    return {"ok": True, "uid": int(uid), "role": _role(user), "is_admin": bool(_is_admin(user)), "username": _uname(user)}


@router.get("/admin/users")
def admin_users(db: Session = Depends(get_db), user: dict = Depends(get_current_user)) -> dict[str, Any]:
    if not _is_admin(user):
        raise HTTPException(403, "No autorizado")
    if not _table_exists(db, "usuarios"):
        return {"ok": True, "items": []}
    # NOTA: en producción hay cuentas legacy sin `rol/role` o sin `usuarios_marcas`.
    # Para que Admin pueda "ver como" sin quedar bloqueado, incluimos:
    # - usuarios con rol EJECUTIVO (o '2')
    # - usuarios que tienen marcas asignadas (usuarios_marcas/usuario_marcas)
    # - usuarios que ya tienen tareas abiertas
    # - usuarios que aparecen como id_usuario en leads (si es numérico)
    try:
        cols_display = []
        for c in ("nombre", "name", "username", "email"):
            if _col_exists(db, "usuarios", c):
                cols_display.append(f"NULLIF(btrim({c}), '')")
        display_expr = "COALESCE(" + ", ".join(cols_display + ["id_usuario::text"]) + ") AS display"
        role_col = "''"
        if _col_exists(db, "usuarios", "role"):
            role_col = "role::text"
        elif _col_exists(db, "usuarios", "rol"):
            role_col = "rol::text"
        role_expr = f"COALESCE(NULLIF(btrim({role_col}),''), '') AS role"
        rows = db.execute(
            text(
                f"""
                SELECT id_usuario, {display_expr}, {role_expr}
                FROM public.usuarios
                ORDER BY display
                """
            )
        ).mappings().all()
    except Exception:
        rows = []

    m: dict[int, int] = {}
    try:
        ensure_tasks_table(db)
        if core_tasks._tasks_table_exists(db):  # type: ignore[attr-defined]
            counts = db.execute(
                text(
                    """
                    SELECT assigned_user_id, COUNT(*)::int AS open_count
                    FROM public.tasks
                    WHERE status='open' AND assigned_user_id IS NOT NULL
                    GROUP BY assigned_user_id
                    """
                )
            ).fetchall()
            m = {int(r[0]): int(r[1]) for r in counts if r and r[0] is not None}
    except Exception:
        m = {}

    ids_with_tasks = set(m.keys())
    ids_with_marcas: set[int] = set()
    try:
        join_table = None
        if _table_exists(db, "usuarios_marcas"):
            join_table = "usuarios_marcas"
        elif _table_exists(db, "usuario_marcas"):
            join_table = "usuario_marcas"
        if join_table:
            rows2 = db.execute(text(f"SELECT DISTINCT id_usuario FROM public.{join_table}")).fetchall()
            for r in rows2:
                try:
                    ids_with_marcas.add(int(r[0]))
                except Exception:
                    pass
    except Exception:
        ids_with_marcas = set()

    ids_with_leads: set[int] = set()
    try:
        if _table_exists(db, "leads") and _col_exists(db, "leads", "id_usuario"):
            rows3 = db.execute(
                text(
                    """
                    SELECT DISTINCT NULLIF(btrim(COALESCE(id_usuario::text,'')),'') AS u
                    FROM public.leads
                    WHERE NULLIF(btrim(COALESCE(id_usuario::text,'')),'') IS NOT NULL
                    LIMIT 5000
                    """
                )
            ).fetchall()
            for r in rows3:
                u = str(r[0] or "").strip()
                if u.isdigit():
                    try:
                        ids_with_leads.add(int(u))
                    except Exception:
                        pass
    except Exception:
        ids_with_leads = set()

    items = []
    for r in rows:
        uid = int(r.get("id_usuario"))
        role_txt = str(r.get("role") or "")
        role_up = role_txt.strip().upper()
        is_exec = (role_up == "2") or ("EJECUTIV" in role_up)
        if not (is_exec or (uid in ids_with_marcas) or (uid in ids_with_tasks) or (uid in ids_with_leads)):
            continue
        items.append({"id": uid, "name": str(r.get("display") or uid), "role": role_txt, "open": int(m.get(uid, 0))})
    return {"ok": True, "items": items}


@router.post("/sync")
def sync_tasks(
    for_user_id: int | None = Query(default=None, description="(Admin) generar para otro usuario"),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    Genera tareas automáticas (idempotente) para el usuario actual.
    """
    try:
        uid = _resolve_uid(db, user)
        target_uid = int(for_user_id) if (for_user_id is not None and _is_admin(user)) else int(uid)
        if for_user_id is not None and not _is_admin(user):
            raise HTTPException(403, "No autorizado")

        info = _user_info(db, target_uid)

        marcas_token = list(user.get("marcas") or []) if target_uid == int(uid) else []
        marcas_db = _fetch_marcas_for_uid(db, target_uid)
        marcas = marcas_token or marcas_db
        if not marcas:
            marcas = _fetch_marcas_for_uid(db, target_uid)
        if not marcas:
            # Último fallback: inferir por leads asignados (evita 0 tareas cuando faltan bindings en usuarios_marcas)
            try:
                user_keys = core_tasks._user_match_keys(db, user_id=int(target_uid), username=str(info.get("username") or target_uid))  # type: ignore[attr-defined]
            except Exception:
                user_keys = [str(target_uid), str(info.get("username") or target_uid)]
            marcas = _infer_marcas_from_leads(db, user_keys)

        try:
            out = upsert_mvp_tasks_for_user(
                db,
                user_id=int(target_uid),
                username=str(info.get("username") or target_uid),
                role=str(info.get("role") or _role(user)),
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
    except HTTPException:
        raise
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        return {"ok": True, "created": 0, "skipped": 0, "disabled": True, "error": f"{type(e).__name__}: {str(e)[:200]}"}


@router.get("")
@router.get("/")
def get_tasks(
    status: str = Query("open"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    for_user_id: int | None = Query(default=None, description="(Admin) ver tareas de otro usuario"),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    uid = _resolve_uid(db, user)
    target_uid = int(for_user_id) if (for_user_id is not None and _is_admin(user)) else int(uid)
    if for_user_id is not None and not _is_admin(user):
        raise HTTPException(403, "No autorizado")
    return list_tasks(db, assigned_user_id=int(target_uid), status=status, limit=limit, offset=offset)


@router.post("/{id_task}/done")
def mark_done(
    id_task: int,
    payload: Dict[str, Any] = Body(default_factory=dict),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    uid = _resolve_uid(db, user)
    who = _uname(user)
    is_admin = _is_admin(user)
    ensure_tasks_table(db)

    # Cargar la tarea (necesario para validar consecuencias / evidencia).
    task = (
        db.execute(
            text(
                """
                SELECT id_task, kind, entity_type, entity_id, meta
                FROM public.tasks
                WHERE id_task=:id AND (assigned_user_id=:uid OR :is_admin)
                LIMIT 1
                """
            ),
            {"id": int(id_task), "uid": int(uid), "is_admin": bool(is_admin)},
        )
        .mappings()
        .first()
    )
    if not task:
        raise HTTPException(404, "Tarea no existe")

    kind = str(task.get("kind") or "").strip().upper()
    entity_type = str(task.get("entity_type") or "").strip().lower()
    entity_id = task.get("entity_id")

    # Consecuencia: tareas críticas requieren evidencia (seguimiento / dato efectivamente completado).
    require_followup = kind in {
        "LEAD_NUEVO_SEGUIMIENTO",
        "LEAD_CONTACTADO_SEGUIMIENTO",
        "LEAD_COTIZADO_SEGUIMIENTO",
        "CONTACTAR_LEAD",
        "RIESGO_AUTO_DECLINE_NUEVO",
        "RIESGO_AUTO_DECLINE_CONTACTADO_SIN_FECHA",
        "RIESGO_AUTO_DECLINE_CONTACTADO_CON_FECHA",
        "RIESGO_COTIZADO_EVENTO_CERCA",
        "DECLINADO_FECHA_FUTURA",
    }
    require_field_check = kind in {"COMPLETAR_TELEFONO", "COMPLETAR_DIRECCION", "COMPLETAR_HORARIO"}

    action = str(payload.get("action") or payload.get("method") or payload.get("tipo") or "").strip().upper()
    note_text = str(payload.get("text") or payload.get("nota") or payload.get("message") or "").strip()

    if require_followup:
        if entity_type != "lead" or not str(entity_id or "").isdigit():
            raise HTTPException(400, "Esta tarea requiere seguimiento sobre un lead válido.")
        if not action:
            raise HTTPException(
                400,
                "Para cerrar esta tarea debes registrar seguimiento (WhatsApp/Llamada/Email/Nota).",
            )
        if action not in ("WSP", "WHATSAPP", "CALL", "LLAMAR", "EMAIL", "MAIL", "NOTE", "NOTA"):
            raise HTTPException(400, "action inválida (WSP/CALL/EMAIL/NOTE).")
        if not note_text:
            raise HTTPException(400, "text requerido (detalle del seguimiento).")

        lead_id = int(entity_id)
        tag = "NOTE"
        if action in ("WSP", "WHATSAPP"):
            tag = "WSP"
        elif action in ("CALL", "LLAMAR"):
            tag = "CALL"
        elif action in ("EMAIL", "MAIL"):
            tag = "EMAIL"

        # Append note (misma semántica que /leads/{id}/append_note) + cierra la tarea.
        try:
            from datetime import datetime

            ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        except Exception:
            ts = ""
        header = f"[{tag}] {ts} · {who}".strip()
        block = f"{header}\n{note_text}".strip()

        try:
            cn = db.connection()
            # Verifica lead
            ok = bool(cn.execute(text("SELECT 1 FROM public.leads WHERE id_lead=:id LIMIT 1"), {"id": lead_id}).scalar())
            if not ok:
                raise HTTPException(404, "Lead no existe")
            cn.execute(
                text(
                    """
                    UPDATE public.leads
                    SET notas = CASE
                      WHEN COALESCE(notas,'') = '' THEN :b
                      ELSE notas || E'\n\n' || :b
                    END,
                    updated_at = now()
                    WHERE id_lead=:id
                    """
                ),
                {"id": lead_id, "b": block},
            )
            # Guardar evidencia en meta de tarea
            cn.execute(
                text(
                    """
                    UPDATE public.tasks
                    SET status='done',
                        completed_at=now(),
                        completed_by=:by,
                        meta = COALESCE(meta,'{}'::jsonb) || jsonb_build_object(
                          'followup_action', to_jsonb(CAST(:a AS text)),
                          'followup_block', to_jsonb(CAST(:b AS text))
                        ),
                        updated_at=now()
                    WHERE id_task=:tid
                    """
                ),
                {"tid": int(id_task), "a": tag, "b": block[:2000], "by": who},
            )
            # Cierra cualquier otra tarea abierta del mismo lead para este usuario (evita que vuelva a aparecer).
            cn.execute(
                text(
                    """
                    UPDATE public.tasks
                    SET status='done', completed_at=now(), completed_by=:by, updated_at=now()
                    WHERE status='open'
                      AND entity_type='lead'
                      AND entity_id=:lid
                      AND assigned_user_id=:uid
                    """
                ),
                {"by": who, "lid": int(lead_id), "uid": int(uid)},
            )
            # Log activity (best-effort)
            try:
                log_activity(
                    cn,
                    username=who,
                    user_id=uid,
                    role=_role(user),
                    action="LEAD_FOLLOWUP",
                    entity_type="lead",
                    entity_id=int(lead_id),
                    meta={"via": tag, "task_id": int(id_task)},
                )
            except Exception:
                pass
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"No pude registrar seguimiento: {type(e).__name__}: {str(e)[:160]}")

    if require_field_check and entity_type == "lead" and str(entity_id or "").isdigit():
        lead_id = int(entity_id)
        cn = db.connection()
        # Best-effort: algunos deploys no tienen pre_start/pre_end.
        try:
            cols = {r[0] for r in cn.execute(text("""
              SELECT column_name
              FROM information_schema.columns
              WHERE table_schema='public' AND table_name='leads'
                AND column_name IN ('telefono','direccion','pre_start','pre_end','hora_inicio','hora_fin')
            """)).fetchall()}
        except Exception:
            cols = set()
        sel = ["id_lead"]
        if "telefono" in cols:
            sel.append("telefono")
        if "direccion" in cols:
            sel.append("direccion")
        if "pre_start" in cols:
            sel.append("pre_start")
        if "pre_end" in cols:
            sel.append("pre_end")
        if "hora_inicio" in cols:
            sel.append("hora_inicio")
        if "hora_fin" in cols:
            sel.append("hora_fin")
        try:
            lead = cn.execute(text(f"SELECT {', '.join(sel)} FROM public.leads WHERE id_lead=:id"), {"id": lead_id}).mappings().first()
        except Exception:
            lead = None
        if not lead:
            raise HTTPException(404, "Lead no existe")
        if kind == "COMPLETAR_TELEFONO":
            if "telefono" in sel and not str(lead.get("telefono") or "").strip():
                raise HTTPException(400, "No puedes cerrar: el lead sigue sin teléfono.")
        if kind == "COMPLETAR_DIRECCION":
            if "direccion" in sel and not str(lead.get("direccion") or "").strip():
                raise HTTPException(400, "No puedes cerrar: el lead sigue sin dirección.")
        if kind == "COMPLETAR_HORARIO":
            # Acepta cualquier par (pre_start/pre_end) o (hora_inicio/hora_fin) si existen.
            has_pre = ("pre_start" in sel and "pre_end" in sel)
            has_hr = ("hora_inicio" in sel and "hora_fin" in sel)
            ok = False
            if has_pre and (lead.get("pre_start") is not None and lead.get("pre_end") is not None):
                ok = True
            if has_hr and str(lead.get("hora_inicio") or "").strip() and str(lead.get("hora_fin") or "").strip():
                ok = True
            if (has_pre or has_hr) and not ok:
                raise HTTPException(400, "No puedes cerrar: el lead sigue sin horario (inicio/fin).")

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
def summary(
    scope: str = Query("me", description="me|all (all solo admin)"),
    for_user_id: int | None = Query(default=None, description="(Admin) ver conteo como otro usuario"),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    Conteo rápido para UI/badges (no crea tareas).
    - Solo considera las 3 tareas de seguimiento por estado (NUEVO/CONTACTADO/COTIZADO).
    - Admin puede pedir `scope=all` para ver global (siempre excluye leads CONFIRMADO/DECLINADO).
    """
    uid = _resolve_uid(db, user)
    is_admin = _is_admin(user)
    target_uid = int(for_user_id) if (for_user_id is not None and is_admin) else int(uid)
    if for_user_id is not None and not is_admin:
        raise HTTPException(403, "No autorizado")

    scope = (scope or "me").strip().lower()
    if scope not in ("me", "all"):
        scope = "me"
    if scope == "all" and not is_admin:
        scope = "me"

    kinds = ["LEAD_NUEVO_SEGUIMIENTO", "LEAD_CONTACTADO_SEGUIMIENTO", "LEAD_COTIZADO_SEGUIMIENTO"]

    try:
        ensure_tasks_table(db)
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return {
            "ok": True,
            "scope": scope,
            "open_total": 0,
            "overdue_total": 0,
            "open_nuevos": 0,
            "open_contactados": 0,
            "open_cotizados": 0,
            "disabled": True,
        }

    # Excluir leads cerrados (CONFIRMADO / DECLINADO) para evitar ruido en conteos.
    closed = []
    try:
        if _table_exists(db, "leads") and _col_exists(db, "leads", "id_estado"):
            confirmado_id = core_tasks._estado_id_like(db, "CONFIRM%", 4)  # type: ignore[attr-defined]
            declinado_id = core_tasks._estado_id_like(db, "%DECLIN%", 5)  # type: ignore[attr-defined]
            closed = [int(confirmado_id), int(declinado_id)]
    except Exception:
        closed = []

    where = "t.kind = ANY(CAST(:kinds AS text[]))"
    params: dict[str, Any] = {"kinds": kinds}
    if scope == "me":
        where += " AND t.assigned_user_id=:uid"
        params["uid"] = int(target_uid)

    if closed:
        where += """
          AND NOT (
            t.entity_type='lead'
            AND EXISTS (
              SELECT 1
              FROM public.leads l
              WHERE l.id_lead=t.entity_id
                AND l.id_estado = ANY(CAST(:closed AS int[]))
            )
          )
        """
        params["closed"] = closed

    try:
        row = db.execute(
            text(
                f"""
                SELECT
                  COUNT(*) FILTER (WHERE t.status='open')::int AS open_total,
                  COUNT(*) FILTER (WHERE t.status='open' AND t.due_at IS NOT NULL AND t.due_at < now())::int AS overdue_total,
                  COUNT(*) FILTER (WHERE t.status='open' AND t.kind='LEAD_NUEVO_SEGUIMIENTO')::int AS open_nuevos,
                  COUNT(*) FILTER (WHERE t.status='open' AND t.kind='LEAD_CONTACTADO_SEGUIMIENTO')::int AS open_contactados,
                  COUNT(*) FILTER (WHERE t.status='open' AND t.kind='LEAD_COTIZADO_SEGUIMIENTO')::int AS open_cotizados
                FROM public.tasks t
                WHERE {where}
                """
            ),
            params,
        ).mappings().first()
        return {"ok": True, "scope": scope, **(dict(row) if row else {})}
    except Exception:
        return {
            "ok": True,
            "scope": scope,
            "open_total": 0,
            "overdue_total": 0,
            "open_nuevos": 0,
            "open_contactados": 0,
            "open_cotizados": 0,
        }


@router.get("/debug")
def debug_tasks(db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    """
    Diagnóstico rápido para entender por qué salen 0 tareas.
    No expone contraseñas ni datos sensibles.
    """
    uid = _resolve_uid(db, user)
    role = _role(user)
    uname = _uname(user)
    marcas_token = list(user.get("marcas") or [])
    marcas = list(marcas_token)

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
    marcas_ids_text: list[str] = []
    try:
        for x in marcas_ids:
            s = str(x).strip()
            if s and s not in marcas_ids_text:
                marcas_ids_text.append(s)
    except Exception:
        marcas_ids_text = []
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
        brand_sql = core_tasks._brand_filter_sql(db)  # type: ignore[attr-defined]
    except Exception:
        brand_sql = "FALSE"

    # Evita SQL inválido en esquemas legacy sin `leads.id_usuario`.
    if has_id_usuario is False:
        assigned_sql = "FALSE"
        unassigned_sql = "TRUE"
    else:
        try:
            assigned_sql = core_tasks._assigned_to_user_sql()  # type: ignore[attr-defined]
            unassigned_sql = core_tasks._unassigned_sql()  # type: ignore[attr-defined]
        except Exception:
            assigned_sql = "TRUE"
            unassigned_sql = "FALSE"

    scope_sql = assigned_sql
    if marcas_ids or marcas_upper:
        scope_sql = f"({assigned_sql} OR ({unassigned_sql} AND {brand_sql}))"

    params = {
        "uid": int(uid),
        "uname": (uname or "").strip()[:200],
        "user_keys": [str(x).strip().lower() for x in (user_keys or []) if str(x).strip()],
        "marcas_ids": marcas_ids or [0],
        "marcas_ids_text": marcas_ids_text or ["0"],
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
        "marcas_db": _fetch_marcas_for_uid(db, int(uid)),
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
