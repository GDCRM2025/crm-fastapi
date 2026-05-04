from __future__ import annotations

import os
import json
import time
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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


def _dow_mask_allows(mask: int | None, dow: int) -> bool:
    if mask is None:
        return True
    try:
        m = int(mask)
    except Exception:
        return True
    if dow < 0 or dow > 6:
        return True
    return bool(m & (1 << dow))


def _job_rrhh_auto_out(conn, *, force: bool = False) -> dict:
    """
    Auto-marca salida (OUT) a las 18:00 si:
      - Hora Chile >= 18:30 (o force)
      - Existe IN OK hoy
      - No existe OUT OK hoy
    Además crea un desvío tipo AUTO_OUT (pendiente) y envía correo al colaborador
    con links de aprobar/rechazar.
    """
    try:
        tz = ZoneInfo("America/Santiago")
    except Exception:
        tz = None
    now = datetime.now(tz) if tz else datetime.now()
    if not force:
        try:
            if (now.hour, now.minute) < (18, 30):
                return {"ok": True, "skipped": True, "reason": "before_18_30"}
        except Exception:
            pass

    try:
        d = conn.execute(text("SELECT (now() AT TIME ZONE 'America/Santiago')::date")).scalar()
        day = str(d)[:10]
    except Exception:
        day = now.strftime("%Y-%m-%d")

    # Email base URL
    base = (os.getenv("APP_URL") or "").strip().rstrip("/")
    if not base:
        base = "https://greendiamond.cl"

    created = 0
    emailed = 0
    skipped_no_shift = 0

    try:
        staff_rows = conn.execute(
            text(
                """
                SELECT id_staff, id_usuario, colaborador, rut,
                       COALESCE(NULLIF(btrim(email),''), NULL) AS email,
                       jefe_id_staff
                FROM public.rrhh_staff
                WHERE is_active IS TRUE
                  AND id_usuario IS NOT NULL
                  AND id_usuario > 0
                ORDER BY id_staff ASC
                """
            )
        ).mappings().all()
    except Exception:
        staff_rows = []

    # helper: resolve email via usuarios if missing
    def _user_email(uid: int) -> str | None:
        try:
            e = conn.execute(
                text("SELECT COALESCE(NULLIF(btrim(email),''), NULL) FROM public.usuarios WHERE id_usuario=:u LIMIT 1"),
                {"u": int(uid)},
            ).scalar()
            e = str(e or "").strip()
            return e or None
        except Exception:
            return None

    # send email
    def _send(to_email: str, subject: str, body: str) -> None:
        nonlocal emailed
        try:
            from backend.core.email import send_email_group
            send_email_group([to_email], subject, body)
            emailed += 1
        except Exception:
            return

    from backend.core import public_tokens  # local import (uses JWT secret)

    for st in staff_rows:
        try:
            id_staff = int(st.get("id_staff") or 0)
            id_usuario = int(st.get("id_usuario") or 0)
            if id_staff <= 0 or id_usuario <= 0:
                continue

            # shift hoy (si no existe, no auto-marcamos)
            try:
                rows = conn.execute(
                    text(
                        """
                        SELECT h.desde, h.hasta, h.dow_mask,
                               t.hora_entrada, t.hora_salida
                        FROM public.rrhh_horarios h
                        JOIN public.rrhh_turnos t ON t.id_turno=h.id_turno
                        WHERE h.is_active IS TRUE
                          AND t.is_active IS TRUE
                          AND h.id_staff=:s
                          AND h.desde <= :d
                          AND (h.hasta IS NULL OR h.hasta >= :d)
                        ORDER BY h.desde DESC, h.id_horario DESC
                        """
                    ),
                    {"s": id_staff, "d": day},
                ).mappings().all()
                dow = int(datetime.fromisoformat(day).weekday())
                shift = None
                for r in rows:
                    if _dow_mask_allows(r.get("dow_mask"), dow):
                        shift = dict(r)
                        break
                if not shift:
                    skipped_no_shift += 1
                    continue
                exp_in = str(shift.get("hora_entrada") or "").strip() or "09:00"
                exp_out = str(shift.get("hora_salida") or "").strip() or "18:00"
            except Exception:
                skipped_no_shift += 1
                continue

            # marks hoy
            m = conn.execute(
                text(
                    """
                    SELECT
                      max(CASE WHEN upper(tipo)='IN' AND ok IS TRUE THEN created_at END) AS in_at,
                      max(CASE WHEN upper(tipo)='OUT' AND ok IS TRUE THEN created_at END) AS out_at
                    FROM public.sgjo_marcaciones
                    WHERE id_usuario=:u
                      AND ((created_at AT TIME ZONE 'America/Santiago')::date = :d::date)
                    """
                ),
                {"u": id_usuario, "d": day},
            ).mappings().first() or {}
            if not m.get("in_at"):
                continue
            if m.get("out_at"):
                continue

            # 1) Inserta OUT automático 18:00 (idempotente)
            conn.execute(
                text(
                    """
                    INSERT INTO public.sgjo_marcaciones(
                      created_at, id_usuario, rut, tipo, method,
                      id_sede, id_punto,
                      lat, lng, accuracy_m, distance_m,
                      within_radius, used_fallback,
                      ok, error, meta
                    )
                    VALUES (
                      ((:d || ' 18:00:00')::timestamp AT TIME ZONE 'America/Santiago'),
                      :u, :rut, 'OUT', 'AUTO',
                      NULL, NULL,
                      NULL, NULL, NULL, NULL,
                      TRUE, FALSE,
                      TRUE, NULL,
                      jsonb_build_object('auto_out', true, 'auto_out_at', '18:00', 'threshold', '18:30')
                    )
                    """
                ),
                {"d": day, "u": id_usuario, "rut": str(st.get("rut") or "")},
            )

            # 2) Crea/actualiza desvío AUTO_OUT y obtiene id_desvio
            desvio = conn.execute(
                text(
                    """
                    INSERT INTO public.rrhh_desvios(
                      fecha, id_staff, id_usuario, rut, colaborador, rol, centro_costo,
                      tipo, status,
                      expected_in, expected_out, actual_in, actual_out,
                      diff_in_min, diff_out_min,
                      impact_kind, impact_min,
                      meta
                    ) VALUES (
                      :f,:s,:u,:rut,:c,:r,:cc,
                      'AUTO_OUT','pendiente',
                      :ein,:eout,:ain, ((:f || ' 18:00:00')::timestamp AT TIME ZONE 'America/Santiago'),
                      NULL,NULL,
                      'JUSTIFICADO', 0,
                      jsonb_build_object('auto_out', true, 'auto_out_at', '18:00', 'threshold', '18:30')
                    )
                    ON CONFLICT (fecha, id_staff, tipo) DO UPDATE SET
                      id_usuario=EXCLUDED.id_usuario,
                      rut=EXCLUDED.rut,
                      colaborador=EXCLUDED.colaborador,
                      expected_in=EXCLUDED.expected_in,
                      expected_out=EXCLUDED.expected_out,
                      actual_in=EXCLUDED.actual_in,
                      actual_out=EXCLUDED.actual_out,
                      impact_kind=EXCLUDED.impact_kind,
                      impact_min=EXCLUDED.impact_min,
                      meta=EXCLUDED.meta,
                      status=CASE WHEN rrhh_desvios.status IN ('aprobado','rechazado') THEN rrhh_desvios.status ELSE 'pendiente' END
                    RETURNING id_desvio
                    """
                ),
                {
                    "f": day,
                    "s": id_staff,
                    "u": id_usuario,
                    "rut": str(st.get("rut") or ""),
                    "c": str(st.get("colaborador") or ""),
                    "r": str(st.get("rol") or ""),
                    "cc": str(st.get("centro_costo") or ""),
                    "ein": exp_in,
                    "eout": exp_out,
                    "ain": m.get("in_at"),
                },
            ).scalar()

            created += 1

            # 3) Email al colaborador para aprobar/rechazar
            did = int(desvio) if desvio is not None else 0
            to_email = str(st.get("email") or "").strip() or (_user_email(id_usuario) or "")
            if did and to_email:
                tok_ok = public_tokens.sign({"k": "desvio", "id": did, "st": "aprobado"}, ttl_seconds=7 * 24 * 3600)
                tok_no = public_tokens.sign({"k": "desvio", "id": did, "st": "rechazado"}, ttl_seconds=7 * 24 * 3600)
                link_ok = f"{base}/crm/rrhh/desvios/action?t={tok_ok}"
                link_no = f"{base}/crm/rrhh/desvios/action?t={tok_no}"
                subj = f"RRHH · Salida automática · {day} · {st.get('colaborador') or ''}".strip()
                body = (
                    f"Hola {st.get('colaborador') or 'colaborador'},\n\n"
                    f"Hoy ({day}) no registraste tu SALIDA.\n"
                    f"Para cerrar la jornada, el sistema registró una salida automática a las 18:00.\n\n"
                    f"Turno esperado: {exp_in} → {exp_out}\n"
                    f"Salida registrada: 18:00 (AUTO)\n\n"
                    f"Por favor confirma:\n"
                    f"- Aprobar (correcto): {link_ok}\n"
                    f"- Rechazar (no corresponde): {link_no}\n\n"
                    f"Si rechazas, RRHH revisará tu caso.\n"
                )
                _send(to_email, subj, body)

        except Exception:
            continue

    try:
        conn.commit()
    except Exception:
        pass

    return {
        "ok": True,
        "date": day,
        "created": int(created),
        "emailed": int(emailed),
        "skipped_no_shift": int(skipped_no_shift),
        "threshold": "18:30",
        "out_at": "18:00",
    }


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
        if name in ("rrhh_auto_out", "rrhh_auto_mark_out", "auto_out"):
            force = False
            try:
                if isinstance(payload, dict) and payload.get("force") is not None:
                    force = bool(int(payload.get("force")))
            except Exception:
                force = False
            return _job_rrhh_auto_out(conn, force=force)
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
    heavy = {"stale_decline", "auto_decline_stale", "refresh_drive_assets", "drive_assets_refresh", "rrhh_auto_out", "rrhh_auto_mark_out", "auto_out"}

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
