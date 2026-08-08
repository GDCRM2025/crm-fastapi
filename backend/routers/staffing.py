from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.core.system_notifs import push_system_notif
from backend.core.webpush import send_webpush_to_users

try:
    from backend.routers.auth import get_current_user  # type: ignore
except Exception:  # pragma: no cover
    def get_current_user():  # type: ignore
        return {"id": "dev", "role": "SUPERADMIN", "username": "dev", "name": "Dev"}


router = APIRouter(prefix="/staffing", tags=["staffing"])


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
            "10": "RRHH",
            "11": "FINANZAS",
            "12": "SUPERADMIN",
        }
        return mp.get(raw, raw).upper()
    return raw.upper()


def _uid(user: dict) -> int:
    raw = user.get("id") or user.get("id_usuario") or user.get("user_id")
    if str(raw or "").isdigit():
        return int(raw)
    raise HTTPException(status_code=401, detail="Usuario inválido")


def _is_superadmin(role: str) -> bool:
    r = str(role or "").upper()
    return (r == "SUPERADMIN") or ("SUPERADMIN" in r)


def _staff_role_for_user(role: str) -> str:
    r = str(role or "").upper()
    if any(k in r for k in ("CONDUCTOR", "CHOFER", "CHOP", "CHOPER", "DRIVER")):
        return "CONDUCTOR"
    return "OPERADOR"


def _can_signup(role: str) -> bool:
    r = str(role or "").upper()
    return any(k in r for k in ("OPERADOR", "CONDUCTOR", "CHOFER", "CHOP", "OPERACIONES"))


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


def _ensure_tables(db: Session) -> None:
    # Best-effort DDL; avoid ALTERs (deadlocks).
    try:
        db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS public.ops_event_signups (
                  id_signup BIGSERIAL PRIMARY KEY,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  id_lead BIGINT NOT NULL,
                  id_usuario BIGINT NOT NULL,
                  staff_role TEXT NOT NULL DEFAULT 'OPERADOR',
                  note TEXT NOT NULL DEFAULT ''
                )
                """
            )
        )
        db.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ops_event_signups_uq
                ON public.ops_event_signups(id_lead, id_usuario, staff_role)
                """
            )
        )
        db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS public.ops_event_assignments (
                  id_assignment BIGSERIAL PRIMARY KEY,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  id_lead BIGINT NOT NULL,
                  id_usuario BIGINT NOT NULL,
                  staff_role TEXT NOT NULL DEFAULT 'OPERADOR',
                  assigned_by BIGINT,
                  assigned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  status TEXT NOT NULL DEFAULT 'ASSIGNED', -- ASSIGNED/CONFIRMED/DECLINED/CANCELLED
                  responded_at TIMESTAMPTZ,
                  note TEXT NOT NULL DEFAULT ''
                )
                """
            )
        )
        db.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ops_event_assignments_uq
                ON public.ops_event_assignments(id_lead, id_usuario, staff_role)
                """
            )
        )
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass


def _estado_id_confirmado(db: Session) -> int | None:
    try:
        if not _table_exists(db, "estados_lead"):
            return None
        v = db.execute(
            text(
                """
                SELECT id_estado
                FROM public.estados_lead
                WHERE UPPER(nombre) LIKE '%CONFIRM%'
                ORDER BY id_estado ASC
                LIMIT 1
                """
            )
        ).scalar()
        if v is None:
            return None
        try:
            return int(v)
        except Exception:
            return None
    except Exception:
        return None


def _lead_expr(db: Session, col: str, fallback_sql: str) -> str:
    return f"l.{col}" if _col_exists(db, "leads", col) else fallback_sql


def _list_week_events(db: Session, *, week_offset: int = 0) -> dict[str, Any]:
    tz = ZoneInfo("America/Santiago")
    now = datetime.now(tz)
    today = now.date()
    week_start = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
    week_end = week_start + timedelta(days=6)
    confirmado_id = _estado_id_confirmado(db)

    pre_title_expr = _lead_expr(db, "pre_title", "NULL")
    pre_start_expr = _lead_expr(db, "pre_start", "NULL")
    pre_end_expr = _lead_expr(db, "pre_end", "NULL")
    pre_ops_expr = _lead_expr(db, "pre_ops", "NULL")

    where = ["l.fecha_evento BETWEEN :ws AND :we"]
    params: dict[str, Any] = {"ws": week_start, "we": week_end}
    if confirmado_id:
        where.append("l.id_estado=:conf")
        params["conf"] = confirmado_id
    else:
        where.append("EXISTS (SELECT 1 FROM public.estados_lead e WHERE e.id_estado=l.id_estado AND UPPER(e.nombre) LIKE '%CONFIRM%')")

    rows = db.execute(
        text(
            f"""
            SELECT
              l.id_lead::bigint AS id_lead,
              l.cliente AS cliente,
              l.fecha_evento::date AS fecha_evento,
              {pre_title_expr} AS pre_title,
              {pre_start_expr} AS pre_start,
              {pre_end_expr} AS pre_end,
              {pre_ops_expr} AS pre_ops,
              COALESCE(m.nombre,m.marca,'') AS marca,
              COALESCE(c.nombre,'') AS comuna
            FROM public.leads l
            LEFT JOIN public.marcas m ON m.id_marca=l.id_marca
            LEFT JOIN public.comunas c ON c.id_comuna=l.id_comuna
            WHERE {" AND ".join(where)}
            ORDER BY l.fecha_evento ASC, l.id_lead DESC
            """
        ),
        params,
    ).mappings().all()

    def _iso(v) -> str | None:
        if v is None:
            return None
        try:
            if isinstance(v, datetime):
                return v.isoformat()
            if isinstance(v, date):
                return v.isoformat()
            return str(v)
        except Exception:
            return None

    events: list[dict[str, Any]] = []
    for r in rows:
        title = str(r.get("pre_title") or "").strip()
        if not title:
            cliente = str(r.get("cliente") or "").strip() or f"Lead #{int(r.get('id_lead') or 0)}"
            marca = str(r.get("marca") or "").strip()
            title = f"{cliente} - {marca}" if marca else cliente
        ops = r.get("pre_ops")
        try:
            ops_i = int(ops) if ops is not None else 0
        except Exception:
            ops_i = 0
        if ops_i < 1:
            ops_i = 1
        events.append(
            {
                "id_lead": int(r.get("id_lead") or 0),
                "title": title,
                "cliente": str(r.get("cliente") or "").strip(),
                "marca": str(r.get("marca") or "").strip(),
                "comuna": str(r.get("comuna") or "").strip(),
                "fecha_evento": _iso(r.get("fecha_evento")),
                "start_at": _iso(r.get("pre_start")),
                "end_at": _iso(r.get("pre_end")),
                "ops_required": ops_i,
            }
        )

    return {"ok": True, "week": {"start": str(week_start), "end": str(week_end)}, "events": events}


class SignupIn(BaseModel):
    id_lead: int
    note: str | None = None
    staff_role: str | None = None  # optional override


class AssignIn(BaseModel):
    id_lead: int
    user_ids: list[int]
    staff_role: str | None = None
    note: str | None = None


class RespondIn(BaseModel):
    id_lead: int
    status: str  # CONFIRMED / DECLINED
    staff_role: str | None = None
    note: str | None = None


@router.get("/week")
def staffing_week(
    week_offset: int = Query(0),
    db: Session = Depends(get_db),
    me=Depends(get_current_user),
):
    role = _role(me)
    uid = _uid(me)
    if not _can_signup(role) and not _is_superadmin(role):
        raise HTTPException(status_code=403, detail="No autorizado")

    _ensure_tables(db)
    base = _list_week_events(db, week_offset=int(week_offset or 0))
    events = list(base.get("events") or [])
    if not events:
        return {**base, "counts": {}}

    ids = [int(e.get("id_lead") or 0) for e in events if int(e.get("id_lead") or 0) > 0]
    if not ids:
        return {**base, "counts": {}}

    # counts
    sign_rows = db.execute(
        text(
            """
            SELECT id_lead::bigint AS id_lead, staff_role, COUNT(*)::int AS n
            FROM public.ops_event_signups
            WHERE id_lead = ANY(:ids)
            GROUP BY id_lead, staff_role
            """
        ),
        {"ids": ids},
    ).fetchall()
    signup_counts: dict[tuple[int, str], int] = {}
    for r in sign_rows:
        try:
            signup_counts[(int(r[0]), str(r[1] or "OPERADOR"))] = int(r[2] or 0)
        except Exception:
            continue

    asg_rows = db.execute(
        text(
            """
            SELECT id_lead::bigint AS id_lead, staff_role,
                   SUM(CASE WHEN status='ASSIGNED' THEN 1 ELSE 0 END)::int AS assigned,
                   SUM(CASE WHEN status='CONFIRMED' THEN 1 ELSE 0 END)::int AS confirmed,
                   SUM(CASE WHEN status='DECLINED' THEN 1 ELSE 0 END)::int AS declined
            FROM public.ops_event_assignments
            WHERE id_lead = ANY(:ids)
            GROUP BY id_lead, staff_role
            """
        ),
        {"ids": ids},
    ).fetchall()
    asg_counts: dict[tuple[int, str], dict[str, int]] = {}
    for r in asg_rows:
        try:
            asg_counts[(int(r[0]), str(r[1] or "OPERADOR"))] = {
                "assigned": int(r[2] or 0),
                "confirmed": int(r[3] or 0),
                "declined": int(r[4] or 0),
            }
        except Exception:
            continue

    # my state
    my_staff_role = _staff_role_for_user(role)
    my_signup = set(
        int(x)
        for x in (
            db.execute(
                text(
                    """
                    SELECT id_lead::bigint
                    FROM public.ops_event_signups
                    WHERE id_usuario=:u
                    """
                ),
                {"u": uid},
            )
            .scalars()
            .all()
        )
        if str(x or "").isdigit()
    )
    my_asg = {}
    for r in db.execute(
        text(
            """
            SELECT id_lead::bigint AS id_lead, staff_role, status
            FROM public.ops_event_assignments
            WHERE id_usuario=:u
            """
        ),
        {"u": uid},
    ).fetchall():
        try:
            my_asg[(int(r[0]), str(r[1] or "OPERADOR"))] = str(r[2] or "ASSIGNED")
        except Exception:
            continue

    out_events = []
    for e in events:
        lid = int(e.get("id_lead") or 0)
        sr = str(e.get("staff_role") or my_staff_role or "OPERADOR").upper()
        c1 = signup_counts.get((lid, sr), 0)
        c2 = asg_counts.get((lid, sr), {"assigned": 0, "confirmed": 0, "declined": 0})
        out_events.append(
            {
                **e,
                "staff_role": sr,
                "signup_count": c1,
                "assigned_count": int(c2.get("assigned") or 0),
                "confirmed_count": int(c2.get("confirmed") or 0),
                "declined_count": int(c2.get("declined") or 0),
                "i_signed_up": lid in my_signup,
                "my_assignment_status": my_asg.get((lid, sr)),
            }
        )

    return {**base, "events": out_events, "me": {"id": uid, "role": role, "staff_role": my_staff_role}}


@router.get("/week_admin")
def staffing_week_admin(
    week_offset: int = Query(0),
    db: Session = Depends(get_db),
    me=Depends(get_current_user),
):
    role = _role(me)
    if not _is_superadmin(role):
        raise HTTPException(status_code=403, detail="Solo SuperAdmin")

    _ensure_tables(db)
    base = _list_week_events(db, week_offset=int(week_offset or 0))
    events = list(base.get("events") or [])
    if not events:
        return {**base, "events": []}
    ids = [int(e.get("id_lead") or 0) for e in events if int(e.get("id_lead") or 0) > 0]
    if not ids:
        return {**base, "events": []}

    sign = db.execute(
        text(
            """
            SELECT id_lead::bigint AS id_lead, id_usuario::bigint AS id_usuario, staff_role, created_at, note
            FROM public.ops_event_signups
            WHERE id_lead = ANY(:ids)
            ORDER BY created_at ASC
            """
        ),
        {"ids": ids},
    ).fetchall()
    asg = db.execute(
        text(
            """
            SELECT id_lead::bigint AS id_lead, id_usuario::bigint AS id_usuario, staff_role,
                   status, assigned_at, responded_at, note
            FROM public.ops_event_assignments
            WHERE id_lead = ANY(:ids)
            ORDER BY assigned_at ASC
            """
        ),
        {"ids": ids},
    ).fetchall()

    user_ids = sorted({int(r[1]) for r in sign} | {int(r[1]) for r in asg})
    users_by_id: dict[int, dict[str, str]] = {}
    if user_ids and _table_exists(db, "usuarios"):
        # OJO: `usuarios` no tiene columna `name` en prod. Usamos nombre/username/email.
        rows_u = db.execute(
            text(
                """
                SELECT id_usuario::bigint AS id_usuario,
                       COALESCE(NULLIF(btrim(nombre),''), NULLIF(btrim(username),''), NULLIF(btrim(email),''), id_usuario::text) AS display,
                       COALESCE(NULLIF(btrim(role::text),''), NULLIF(btrim(rol::text),''), '') AS role
                FROM public.usuarios
                WHERE id_usuario = ANY(:ids)
                """
            ),
            {"ids": user_ids},
        ).fetchall()
        for r in rows_u:
            try:
                users_by_id[int(r[0])] = {"display": str(r[1] or ""), "role": str(r[2] or "")}
            except Exception:
                continue

    sign_by_lead: dict[int, list[dict[str, Any]]] = {}
    for r in sign:
        lid = int(r[0])
        uid = int(r[1])
        sign_by_lead.setdefault(lid, []).append(
            {
                "id_usuario": uid,
                "display": users_by_id.get(uid, {}).get("display") or str(uid),
                "role": users_by_id.get(uid, {}).get("role") or "",
                "staff_role": str(r[2] or "OPERADOR"),
                "created_at": (r[3].isoformat() if isinstance(r[3], datetime) else str(r[3] or "")),
                "note": str(r[4] or ""),
            }
        )

    asg_by_lead: dict[int, list[dict[str, Any]]] = {}
    for r in asg:
        lid = int(r[0])
        uid = int(r[1])
        asg_by_lead.setdefault(lid, []).append(
            {
                "id_usuario": uid,
                "display": users_by_id.get(uid, {}).get("display") or str(uid),
                "role": users_by_id.get(uid, {}).get("role") or "",
                "staff_role": str(r[2] or "OPERADOR"),
                "status": str(r[3] or "ASSIGNED"),
                "assigned_at": (r[4].isoformat() if isinstance(r[4], datetime) else str(r[4] or "")),
                "responded_at": (r[5].isoformat() if isinstance(r[5], datetime) else str(r[5] or "")),
                "note": str(r[6] or ""),
            }
        )

    out = []
    for e in events:
        lid = int(e.get("id_lead") or 0)
        out.append({**e, "signups": sign_by_lead.get(lid, []), "assignments": asg_by_lead.get(lid, [])})
    return {**base, "events": out}


@router.post("/signup")
def signup_event(data: SignupIn, db: Session = Depends(get_db), me=Depends(get_current_user)):
    role = _role(me)
    uid = _uid(me)
    if not _can_signup(role):
        raise HTTPException(status_code=403, detail="No autorizado")

    _ensure_tables(db)
    lid = int(data.id_lead)
    staff_role = str(data.staff_role or _staff_role_for_user(role) or "OPERADOR").upper()
    if staff_role not in ("OPERADOR", "CONDUCTOR"):
        staff_role = _staff_role_for_user(role)
    note = (data.note or "").strip()

    try:
        db.execute(
            text(
                """
                INSERT INTO public.ops_event_signups(id_lead, id_usuario, staff_role, note)
                VALUES (:l,:u,:r,:n)
                ON CONFLICT (id_lead, id_usuario, staff_role)
                DO UPDATE SET note=EXCLUDED.note
                """
            ),
            {"l": lid, "u": uid, "r": staff_role, "n": note},
        )
        db.commit()
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))

    return {"ok": True}


@router.post("/unsignup")
def unsignup_event(data: SignupIn, db: Session = Depends(get_db), me=Depends(get_current_user)):
    role = _role(me)
    uid = _uid(me)
    if not _can_signup(role):
        raise HTTPException(status_code=403, detail="No autorizado")

    _ensure_tables(db)
    lid = int(data.id_lead)
    staff_role = str(data.staff_role or _staff_role_for_user(role) or "OPERADOR").upper()
    if staff_role not in ("OPERADOR", "CONDUCTOR"):
        staff_role = _staff_role_for_user(role)

    try:
        db.execute(
            text("DELETE FROM public.ops_event_signups WHERE id_lead=:l AND id_usuario=:u AND staff_role=:r"),
            {"l": lid, "u": uid, "r": staff_role},
        )
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
    return {"ok": True}


@router.post("/assign")
def assign_staff(data: AssignIn, db: Session = Depends(get_db), me=Depends(get_current_user)):
    role = _role(me)
    by = _uid(me)
    if not _is_superadmin(role):
        raise HTTPException(status_code=403, detail="Solo SuperAdmin")

    _ensure_tables(db)
    lid = int(data.id_lead)
    staff_role = str(data.staff_role or "OPERADOR").upper()
    if staff_role not in ("OPERADOR", "CONDUCTOR"):
        staff_role = "OPERADOR"
    note = (data.note or "").strip()

    user_ids = [int(x) for x in (data.user_ids or []) if int(x) > 0]
    if not user_ids:
        raise HTTPException(status_code=400, detail="Faltan user_ids")

    # Guardar asignaciones
    try:
        for uid in user_ids:
            db.execute(
                text(
                    """
                    INSERT INTO public.ops_event_assignments(id_lead, id_usuario, staff_role, assigned_by, status, note)
                    VALUES (:l,:u,:r,:by,'ASSIGNED',:n)
                    ON CONFLICT (id_lead, id_usuario, staff_role)
                    DO UPDATE SET
                      assigned_by=EXCLUDED.assigned_by,
                      assigned_at=now(),
                      status='ASSIGNED',
                      responded_at=NULL,
                      note=EXCLUDED.note
                    """
                ),
                {"l": lid, "u": uid, "r": staff_role, "by": by, "n": note},
            )
        db.commit()
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))

    # Push + system_notifs (best-effort)
    try:
        ev = db.execute(
            text(
                """
                SELECT l.cliente, l.fecha_evento::date AS fecha,
                       COALESCE(m.nombre,m.marca,'') AS marca,
                       COALESCE(c.nombre,'') AS comuna
                FROM public.leads l
                LEFT JOIN public.marcas m ON m.id_marca=l.id_marca
                LEFT JOIN public.comunas c ON c.id_comuna=l.id_comuna
                WHERE l.id_lead=:id
                LIMIT 1
                """
            ),
            {"id": lid},
        ).fetchone()
        cliente = str(ev[0] or "").strip() if ev else ""
        fecha = str(ev[1] or "") if ev else ""
        marca = str(ev[2] or "").strip() if ev else ""
        comuna = str(ev[3] or "").strip() if ev else ""
        title = "Asignación de evento"
        body = "Te asignaron: %s · %s · %s" % (marca or "Marca", fecha or "Fecha", comuna or "Comuna")
        url = f"/crm/web/views/staff.html?tab=op&lead={lid}"
        send_webpush_to_users(user_ids=user_ids, title=title, body=body, url=url, tag=f"asg-{lid}")
    except Exception:
        pass

    try:
        with db.connection() as cn:  # type: ignore[attr-defined]
            push_system_notif(
                cn,
                kind="OPS_ASIGNADO",
                role_target="SUPERADMIN",
                id_lead=lid,
                title="Asignación realizada",
                body=f"Asignaste {len(user_ids)} persona(s) al Lead #{lid}.",
                payload={"id_lead": lid, "count": len(user_ids), "staff_role": staff_role},
            )
            db.commit()
    except Exception:
        pass

    return {"ok": True, "assigned": len(user_ids)}


@router.post("/respond")
def respond_assignment(data: RespondIn, db: Session = Depends(get_db), me=Depends(get_current_user)):
    role = _role(me)
    uid = _uid(me)
    if not _can_signup(role):
        raise HTTPException(status_code=403, detail="No autorizado")

    _ensure_tables(db)
    lid = int(data.id_lead)
    staff_role = str(data.staff_role or _staff_role_for_user(role) or "OPERADOR").upper()
    if staff_role not in ("OPERADOR", "CONDUCTOR"):
        staff_role = _staff_role_for_user(role)

    st = str(data.status or "").upper().strip()
    if st in ("OK", "YES", "CONFIRM", "CONFIRMADO"):
        st = "CONFIRMED"
    if st in ("NO", "DECLINE", "RECHAZO", "RECHAZADO"):
        st = "DECLINED"
    if st not in ("CONFIRMED", "DECLINED"):
        raise HTTPException(status_code=400, detail="status inválido (CONFIRMED/DECLINED)")

    note = (data.note or "").strip()

    try:
        r = db.execute(
            text(
                """
                UPDATE public.ops_event_assignments
                SET status=:st, responded_at=now(), note=COALESCE(NULLIF(:n,''), note)
                WHERE id_lead=:l AND id_usuario=:u AND staff_role=:r
                RETURNING id_assignment
                """
            ),
            {"st": st, "n": note, "l": lid, "u": uid, "r": staff_role},
        ).fetchone()
        db.commit()
        if not r:
            raise HTTPException(status_code=404, detail="No hay asignación para responder")
    except HTTPException:
        raise
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))

    # Aviso a SuperAdmin (system_notifs + push)
    try:
        ev = db.execute(
            text(
                """
                SELECT l.cliente, l.fecha_evento::date AS fecha,
                       COALESCE(m.nombre,m.marca,'') AS marca,
                       COALESCE(c.nombre,'') AS comuna
                FROM public.leads l
                LEFT JOIN public.marcas m ON m.id_marca=l.id_marca
                LEFT JOIN public.comunas c ON c.id_comuna=l.id_comuna
                WHERE l.id_lead=:id
                LIMIT 1
                """
            ),
            {"id": lid},
        ).fetchone()
        cliente = str(ev[0] or "").strip() if ev else ""
        fecha = str(ev[1] or "") if ev else ""
        marca = str(ev[2] or "").strip() if ev else ""
        comuna = str(ev[3] or "").strip() if ev else ""
        who = str(me.get("name") or me.get("nombre") or me.get("username") or uid)
        title = "Confirmación de asignación"
        body = f"{who}: {st} · {marca} · {fecha} · {comuna}"
        # system_notifs
        with db.connection() as cn:  # type: ignore[attr-defined]
            push_system_notif(
                cn,
                kind="OPS_ASIGNACION_RESP",
                role_target="SUPERADMIN",
                id_lead=lid,
                title=title,
                body=body,
                payload={"id_lead": lid, "status": st, "user": who, "staff_role": staff_role, "cliente": cliente},
            )
            db.commit()
    except Exception:
        pass

    return {"ok": True, "status": st}
