from __future__ import annotations

import os
import json
import time
import threading
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


def _try_acquire_file_lock(lock_name: str, ttl_seconds: int = 3600) -> tuple[bool, str]:
    """
    Best-effort cross-process lock (shared hosting): prevent multiple concurrent runs.
    """
    try:
        base = Path(__file__).resolve().parents[2] / "tmp"
        base.mkdir(parents=True, exist_ok=True)
        lock = base / f"{lock_name}.lock"

        # If lock is stale, remove it.
        try:
            if lock.exists() and (time.time() - lock.stat().st_mtime) > float(ttl_seconds):
                lock.unlink(missing_ok=True)  # py310
        except Exception:
            pass

        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            return False, str(lock)
        except Exception:
            return False, str(lock)

        try:
            os.write(fd, f"{int(time.time())}\n".encode("utf-8"))
        except Exception:
            pass
        try:
            os.close(fd)
        except Exception:
            pass
        return True, str(lock)
    except Exception:
        return False, ""


def _release_file_lock(lock_path: str) -> None:
    try:
        if lock_path:
            Path(lock_path).unlink(missing_ok=True)  # py310
    except Exception:
        pass


def _job_refresh_drive_assets(*, marca: str | None = None) -> dict:
    """
    Refresh quote assets from Drive folders into local cache (drive_cache).
    Runs OFF-PEAK via cron to avoid hitting Drive during normal usage.
    """
    try:
        from backend.core.quote_assets import DRIVE_ASSET_FOLDERS, normalize_marca
        from backend.core.drive_assets import resolve_brand_assets_from_folder
    except Exception as e:
        return {"ok": False, "error": f"drive_assets_unavailable: {type(e).__name__}"}

    wanted = normalize_marca(marca or "") if marca else ""
    pairs = []
    for k, fid in (DRIVE_ASSET_FOLDERS or {}).items():
        kk = normalize_marca(k or "")
        if wanted and kk != wanted:
            continue
        if fid:
            pairs.append((kk, str(fid)))

    # Also allow configuring folder_id per brand in DB (marcas.drive_assets_folder_id / drive_folder_id).
    try:
        with get_connection() as conn:
            has_marca_col = _col_exists_conn(conn, "marcas", "marca")
            has_nombre_col = _col_exists_conn(conn, "marcas", "nombre")
            if has_marca_col and has_nombre_col:
                name_expr = "COALESCE(marca, nombre)"
            elif has_marca_col:
                name_expr = "marca"
            elif has_nombre_col:
                name_expr = "nombre"
            else:
                name_expr = "''"

            fcols = []
            for c in ("drive_assets_folder_id", "drive_folder_id", "folder_id_drive", "drive_folder"):
                if _col_exists_conn(conn, "marcas", c):
                    fcols.append(c)
            if fcols:
                folder_expr = "COALESCE(" + ", ".join(fcols) + ")"
                rows = conn.execute(
                    text(
                        f"""
                        SELECT DISTINCT UPPER({name_expr}) AS k, {folder_expr} AS folder
                        FROM public.marcas
                        WHERE {folder_expr} IS NOT NULL AND TRIM(CAST({folder_expr} AS text)) <> ''
                        """
                    )
                ).fetchall()
                for r in rows:
                    kk = normalize_marca(str(r[0] or ""))
                    if wanted and kk != wanted:
                        continue
                    fid = str(r[1] or "").strip()
                    if kk and fid and all(kk != x[0] for x in pairs):
                        pairs.append((kk, fid))
    except Exception:
        pass

    if wanted and not pairs:
        return {"ok": False, "error": f"marca_no_configurada: {wanted}"}

    ok_ct = 0
    err_ct = 0
    errors: list[dict] = []
    refreshed: list[str] = []
    for kk, fid in pairs:
        try:
            resolve_brand_assets_from_folder(kk, fid, refresh=True, ttl_seconds=0)
            ok_ct += 1
            refreshed.append(kk)
        except Exception as e:
            err_ct += 1
            errors.append({"marca": kk, "err": f"{type(e).__name__}: {str(e)[:240]}"})
        # small delay to avoid burst load on shared hosting
        try:
            time.sleep(0.15)
        except Exception:
            pass

    return {"ok": True, "refreshed": refreshed, "ok_count": ok_ct, "err_count": err_ct, "errors": errors[:10]}


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

    rrhh_enabled = str(os.getenv("CRM_RRHH_REMINDERS_ENABLED") or "").strip().lower() in ("1", "true", "yes", "on")

    lookback_hours = 24
    try:
        if isinstance(payload, dict) and payload.get("lookback_hours") is not None:
            lookback_hours = int(payload.get("lookback_hours"))
    except Exception:
        lookback_hours = 24

    t0 = time.time()
    out: dict[str, dict] = {}
    queued: list[str] = []

    def _run_job_sync(conn, name: str) -> dict:
        if name in ("past_event_decline", "past_events", "auto_decline_past_events"):
            return _job_past_event_decline(conn)
        if name in ("normalize_cotizado", "quote_normalize"):
            return _job_normalize_cotizado(conn)
        if name in ("refresh_drive_assets", "drive_assets_refresh"):
            marca = ""
            try:
                if isinstance(payload, dict):
                    marca = str(payload.get("marca") or payload.get("brand") or payload.get("nombre") or "").strip()
            except Exception:
                marca = ""
            return _job_refresh_drive_assets(marca=marca or None)
        if name in ("sold_events_superadmin", "sold_events", "event_sold"):
            return _job_sold_events_superadmin(conn, lookback_hours=lookback_hours)
        if name in ("stale_decline", "auto_decline_stale"):
            return _job_stale_decline(conn)
        if name in ("rrhh_reminders", "rrhh_tick"):
            return _job_rrhh_reminders()
        return {"ok": False, "error": "unknown_job"}

    def _spawn(name: str) -> None:
        """
        Shared hosting safety: run heavy jobs in a background thread so this HTTP request
        returns quickly and doesn't block Passenger's request queue.
        """
        def _bg():
            try:
                if name in ("refresh_drive_assets", "drive_assets_refresh"):
                    got, lock_path = _try_acquire_file_lock("cron_refresh_drive_assets", ttl_seconds=3600)
                    if not got:
                        return
                    try:
                        _job_refresh_drive_assets(
                            marca=(str(payload.get("marca") or payload.get("brand") or "") if isinstance(payload, dict) else "").strip() or None
                        )
                    finally:
                        _release_file_lock(lock_path)
                    return
                # Jobs that require DB connection use a fresh connection.
                if name in ("rrhh_reminders", "rrhh_tick"):
                    _job_rrhh_reminders()
                    return
                with get_connection() as conn2:
                    _run_job_sync(conn2, name)
            except Exception:
                pass
        threading.Thread(target=_bg, daemon=True).start()

    # In shared hosting, these can take long and should not block the request queue.
    heavy = {"stale_decline", "auto_decline_stale", "refresh_drive_assets", "drive_assets_refresh"}

    with get_connection() as conn:
        for j in jobs:
            name = str(j or "").strip().lower()
            if not name:
                continue
            jt0 = time.time()
            try:
                # RRHH reminders explicitly disabled unless enabled via env.
                if name in ("rrhh_reminders", "rrhh_tick"):
                    if not rrhh_enabled:
                        out[name] = {"ok": False, "disabled": True}
                    else:
                        _spawn(name)
                        queued.append(name)
                        out[name] = {"ok": True, "queued": True}
                    out[name]["secs"] = round(time.time() - jt0, 3)
                    continue

                # Heavy jobs run in background to avoid blocking Passenger workers.
                if name in heavy:
                    _spawn(name)
                    queued.append(name)
                    out[name] = {"ok": True, "queued": True}
                    out[name]["secs"] = round(time.time() - jt0, 3)
                    continue

                # Light jobs run inline (fast).
                out[name] = _run_job_sync(conn, name)
            except Exception as e:
                out[name] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            out[name]["secs"] = round(time.time() - jt0, 3)

    return {"ok": True, "jobs": out, "queued": queued, "ts": datetime.utcnow().isoformat(), "total_secs": round(time.time() - t0, 3)}
