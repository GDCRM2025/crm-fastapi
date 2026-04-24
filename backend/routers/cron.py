from __future__ import annotations

import os
import json
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Body, Header
from fastapi.responses import JSONResponse
from sqlalchemy import text

from backend.core.db import get_connection


router = APIRouter(prefix="/cron", tags=["cron"])


def _read_dotenv_value_simple(var_name: str) -> str:
    try:
        # Prefer local BASE_DIR/.env, but also support deployments where code is under /home/.../crm
        candidates = [
            (Path(__file__).resolve().parents[2] / ".env"),
            (Path(__file__).resolve().parents[3] / ".env"),
        ]
        for p in candidates:
            try:
                if not p.exists():
                    continue
                for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                    s = line.strip()
                    if not s or s.startswith("#"):
                        continue
                    if not s.startswith(var_name + "="):
                        continue
                    v = s.split("=", 1)[1].strip()
                    if (len(v) >= 2) and (v[0] == v[-1]) and v[0] in ("'", '"'):
                        v = v[1:-1].strip()
                    return v
            except Exception:
                continue
    except Exception:
        return ""
    return ""


def _internal_key() -> str:
    return (os.getenv("GREENI_INTERNAL_KEY") or _read_dotenv_value_simple("GREENI_INTERNAL_KEY") or "").strip()


def _require_internal_key(x_greeni_key: str | None) -> bool:
    key = _internal_key()
    if not key:
        # Safety default: if key isn't configured, deny cron endpoints (avoid exposing jobs publicly).
        return False
    return (x_greeni_key or "").strip() == key


def _col_exists_conn(conn, table: str, col: str) -> bool:
    try:
        return bool(
            conn.execute(
                text(
                    """
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema='public' AND table_name=:t AND column_name=:c
                    """
                ),
                {"t": table, "c": col},
            ).first()
        )
    except Exception:
        return False


def _estado_id_conn(conn, name_like: str) -> int | None:
    try:
        r = conn.execute(
            text("SELECT id_estado FROM estados_lead WHERE UPPER(nombre) LIKE :n LIMIT 1"),
            {"n": f"%{name_like.upper()}%"},
        ).fetchone()
        return int(r[0]) if r else None
    except Exception:
        return None


def _ensure_system_notifs(conn) -> None:
    try:
        conn.execute(
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
        conn.commit()
    except Exception:
        pass


def _job_past_event_decline(conn) -> dict:
    from backend.routers.notifications import _apply_past_event_auto_decline  # local import

    confirmado_id = _estado_id_conn(conn, "CONFIRM")
    declinado_id = _estado_id_conn(conn, "DECLIN")
    nuevo_id = _estado_id_conn(conn, "NUEVO")
    contactado_id = _estado_id_conn(conn, "CONTACT")
    cotizado_id = _estado_id_conn(conn, "COTIZ")
    changed = _apply_past_event_auto_decline(
        conn,
        nuevo_id=nuevo_id,
        contactado_id=contactado_id,
        cotizado_id=cotizado_id,
        declinado_id=declinado_id,
    )
    # Note: _apply_* does not commit.
    conn.commit()
    return {"ok": True, "changed": int(changed or 0), "confirmado_id": confirmado_id}


def _job_normalize_cotizado(conn) -> dict:
    from backend.routers.notifications import _apply_quote_state_normalize  # local import

    cotizado_id = _estado_id_conn(conn, "COTIZ")
    confirmado_id = _estado_id_conn(conn, "CONFIRM")
    declinado_id = _estado_id_conn(conn, "DECLIN")
    changed = _apply_quote_state_normalize(
        conn,
        cotizado_id=cotizado_id,
        confirmado_id=confirmado_id,
        declinado_id=declinado_id,
    )
    conn.commit()
    return {"ok": True, "changed": int(changed or 0)}


def _job_stale_decline(conn) -> dict:
    # This job can be heavy; keep it manual/cron only.
    from backend.core.stale_leads import auto_decline_stale_leads

    res = auto_decline_stale_leads(dry_run=False, triggered_by="CRON", conn=conn)
    try:
        conn.commit()
    except Exception:
        pass
    return {"ok": True, "result": res}


def _job_rrhh_reminders() -> dict:
    # Uses its own engine/transaction inside.
    from backend.core.rrhh_reminders import run_rrhh_mark_reminders

    res = run_rrhh_mark_reminders(dry_run=False, force_run=True)
    return {"ok": True, "result": res}


def _job_sold_events_superadmin(conn, *, lookback_hours: int = 24) -> dict:
    """
    Create a SUPERADMIN system_notif for newly sold/confirmed events.
    We rely on (id_estado=CONFIRMADO) + time window in (confirmado_at|updated_at).
    Dedup is enforced by system_notifs unique(kind, role_target, id_lead).
    """
    _ensure_system_notifs(conn)
    confirmado_id = _estado_id_conn(conn, "CONFIRM")
    if not confirmado_id:
        return {"ok": True, "created": 0, "reason": "no_confirmado_estado"}

    time_col = "updated_at"
    if _col_exists_conn(conn, "leads", "confirmado_at"):
        time_col = "confirmado_at"
    elif _col_exists_conn(conn, "leads", "estado_changed_at"):
        time_col = "estado_changed_at"

    # Basic optional fields (best-effort).
    cols = {r[0] for r in conn.execute(text("""
      SELECT column_name FROM information_schema.columns
      WHERE table_schema='public' AND table_name='leads'
    """)).fetchall()}
    has_fecha_evento = "fecha_evento" in cols
    has_monto = "monto_cotizado" in cols
    has_marca_id = "id_marca" in cols

    select_fields = ["l.id_lead", "l.cliente", f"l.{time_col} AS sold_at"]
    if has_fecha_evento:
        select_fields.append("l.fecha_evento")
    if has_monto:
        select_fields.append("l.monto_cotizado")
    if has_marca_id:
        select_fields.append("l.id_marca")

    rows = conn.execute(
        text(
            f"""
            SELECT {", ".join(select_fields)}
            FROM leads l
            WHERE l.id_estado = :conf
              AND l.{time_col} IS NOT NULL
              AND l.{time_col} >= (now() - (:hrs || ' hours')::interval)
            ORDER BY l.{time_col} DESC
            LIMIT 200
            """
        ),
        {"conf": int(confirmado_id), "hrs": int(max(1, min(int(lookback_hours), 168)))},
    ).fetchall()

    created = 0
    for r in rows:
        try:
            id_lead = int(r[0])
        except Exception:
            continue
        cliente = str(r[1] or "").strip() or f"Lead #{id_lead}"
        sold_at = r[2]
        fecha_evento = None
        monto = None
        id_marca = None
        idx = 3
        if has_fecha_evento:
            fecha_evento = r[idx]; idx += 1
        if has_monto:
            monto = r[idx]; idx += 1
        if has_marca_id:
            id_marca = r[idx]

        title = "Evento vendido (confirmado)"
        parts = [cliente]
        if fecha_evento:
            parts.append(f"Fecha: {fecha_evento}")
        if monto is not None:
            try:
                parts.append(f"Monto: {float(monto):,.0f}".replace(",", "."))
            except Exception:
                parts.append(f"Monto: {monto}")
        body = " | ".join(parts)

        payload = {
            "id_lead": id_lead,
            "cliente": cliente,
            "sold_at": (sold_at.isoformat() if hasattr(sold_at, "isoformat") else str(sold_at or "")),
        }
        if fecha_evento:
            payload["fecha_evento"] = str(fecha_evento)
        if id_marca is not None:
            payload["id_marca"] = int(id_marca)

        try:
            res = conn.execute(
                text(
                    """
                    INSERT INTO public.system_notifs(kind, role_target, id_lead, title, body, payload)
                    VALUES ('EVENT_SOLD', 'SUPERADMIN', :id_lead, :title, :body, CAST(:payload AS jsonb))
                    ON CONFLICT (kind, role_target, id_lead) DO NOTHING
                    RETURNING id
                    """
                ),
                {"id_lead": id_lead, "title": title, "body": body, "payload": json.dumps(payload)},
            ).fetchone()
            if res:
                created += 1
        except Exception:
            continue

    try:
        conn.commit()
    except Exception:
        pass

    return {"ok": True, "created": int(created), "lookback_hours": int(lookback_hours), "time_col": time_col}


@router.post("/run")
def cron_run(
    payload: dict = Body(default=None),
    x_greeni_key: str | None = Header(default=None, alias="X-Greeni-Key"),
):
    """
    Run maintenance jobs off-peak (cPanel cron).
    Security: requires `X-Greeni-Key: $GREENI_INTERNAL_KEY`.

    payload example:
      {"jobs":["past_event_decline","normalize_cotizado","rrhh_reminders","sold_events_superadmin"], "lookback_hours": 24}
    """
    if not _require_internal_key(x_greeni_key):
        return JSONResponse(status_code=403, content={"ok": False, "error": "forbidden"})

    jobs = []
    try:
        if isinstance(payload, dict):
            jobs = payload.get("jobs") or []
    except Exception:
        jobs = []
    if not isinstance(jobs, list) or not jobs:
        jobs = ["past_event_decline", "normalize_cotizado", "rrhh_reminders", "sold_events_superadmin"]

    lookback_hours = 24
    try:
        if isinstance(payload, dict) and payload.get("lookback_hours") is not None:
            lookback_hours = int(payload.get("lookback_hours"))
    except Exception:
        lookback_hours = 24

    t0 = time.time()
    out: dict[str, dict] = {}

    with get_connection() as conn:
        for j in jobs:
            name = str(j or "").strip().lower()
            if not name:
                continue
            jt0 = time.time()
            try:
                if name in ("past_event_decline", "past_events", "auto_decline_past_events"):
                    out[name] = _job_past_event_decline(conn)
                elif name in ("normalize_cotizado", "quote_normalize"):
                    out[name] = _job_normalize_cotizado(conn)
                elif name in ("stale_decline", "auto_decline_stale"):
                    out[name] = _job_stale_decline(conn)
                elif name in ("rrhh_reminders", "rrhh_tick"):
                    out[name] = _job_rrhh_reminders()
                elif name in ("sold_events_superadmin", "sold_events", "event_sold"):
                    out[name] = _job_sold_events_superadmin(conn, lookback_hours=lookback_hours)
                else:
                    out[name] = {"ok": False, "error": "unknown_job"}
            except Exception as e:
                out[name] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            out[name]["secs"] = round(time.time() - jt0, 3)

    return {"ok": True, "jobs": out, "ts": datetime.utcnow().isoformat(), "total_secs": round(time.time() - t0, 3)}
