from __future__ import annotations

from datetime import datetime, timedelta
import os
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import text

from backend.core.activity_log import log_activity
from backend.core.db import get_connection
from backend.core.stale_leads import auto_decline_stale_leads
try:
    from backend.routers.auth import get_current_user  # type: ignore
except Exception:
    def get_current_user():  # type: ignore
        return {"role": "ADMIN", "marcas": []}
import re
import unicodedata


def _is_admin(role: str) -> bool:
    rk = _role_key(role)
    return ("admin" in rk) or ("operac" in rk)


def _role_key(role: str) -> str:
    raw = str(role or "").strip().lower()
    if not raw:
        return ""
    raw = unicodedata.normalize("NFD", raw)
    raw = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
    raw = re.sub(r"[^a-z0-9]+", "", raw)
    return raw


def _role_targets(role: str) -> list[str]:
    """
    Normaliza targets para system_notifs:
    evita que un usuario no vea notifs porque su rol exacto difiere (p.ej. OPERACIONES vs JEFE DE OPERACIONES).
    """
    raw = str(role or "").strip()
    rk = _role_key(raw)
    out: list[str] = []
    if not rk:
        return []

    # Soporte roles numéricos (según tu matriz):
    # 1 ADMIN
    # 2 EJECUTIVO DE VENTAS
    # 3 JEFE DE OPERACIONES
    # 4 BODEGUERO
    # 5 COMPRAS
    # 6 CONDUCTOR
    # 7 OPERADOR
    # 8 MICE
    # 9 OPERADOR PATIO
    # 11 FINANZAS
    if raw.isdigit():
        mp = {
            "1": ["ADMIN", "SUPERADMIN", "1"],
            "2": ["EJECUTIVO DE VENTAS", "2"],
            "3": ["OPERACIONES", "JEFE DE OPERACIONES", "3"],
            "4": ["BODEGUERO", "4"],
            "5": ["COMPRAS", "JEFE DE COMPRAS", "5"],
            "6": ["CONDUCTOR", "6"],
            "7": ["OPERADOR", "7"],
            "8": ["MICE", "8"],
            "9": ["OPERADOR PATIO", "9"],
            "11": ["FINANZAS", "11"],
        }
        out += mp.get(raw, [raw])
    if "admin" in rk:
        out += ["ADMIN", "SUPERADMIN", "1"]
    if "operac" in rk:
        out += ["OPERACIONES", "JEFE DE OPERACIONES", "3"]
    if "compra" in rk:
        out += ["COMPRAS", "JEFE DE COMPRAS", "5"]
    if "bodeg" in rk:
        out += ["BODEGUERO", "4"]
    if "mice" in rk:
        out += ["MICE", "8"]
    if "rrhh" in rk or "recursoshumanos" in rk or "humanos" in rk:
        out += ["RRHH"]
    if not out:
        out = [str(role or "").upper().strip()]
    # de-dup manteniendo orden
    seen = set()
    res: list[str] = []
    for x in out:
        if x and x not in seen:
            seen.add(x)
            res.append(x)
    return res

router = APIRouter(prefix="/notifications", tags=["notifications"])
_lead_lock_bypass_ddl_done = False


def _user_id(user: dict) -> int | None:
    for k in ("id", "id_usuario", "user_id"):
        v = user.get(k)
        if str(v or "").isdigit():
            return int(v)
    return None


def _username(user: dict) -> str:
    return str(user.get("username") or user.get("email") or user.get("name") or user.get("nombre") or user.get("id") or "").strip()


def _ensure_lead_lock_bypass(conn) -> None:
    global _lead_lock_bypass_ddl_done
    if _lead_lock_bypass_ddl_done:
        return
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.lead_lock_bypass (
              id BIGSERIAL PRIMARY KEY,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              user_id BIGINT,
              username TEXT,
              role TEXT,
              reason TEXT NOT NULL,
              minutes INTEGER NOT NULL DEFAULT 60,
              expires_at TIMESTAMPTZ NOT NULL,
              stale_count INTEGER NOT NULL DEFAULT 0,
              stale_ids TEXT,
              ip TEXT,
              user_agent TEXT
            )
            """
        )
    )
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_lead_lock_bypass_user_expires ON public.lead_lock_bypass(user_id, expires_at DESC)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_lead_lock_bypass_created_at ON public.lead_lock_bypass(created_at DESC)"))
    _lead_lock_bypass_ddl_done = True


def _lead_lock_policy() -> tuple[int, int]:
    try:
        minutes = int(os.getenv("CRM_LEAD_LOCK_BYPASS_MINUTES") or "60")
    except Exception:
        minutes = 60
    try:
        max_daily = int(os.getenv("CRM_LEAD_LOCK_BYPASS_MAX_DAILY") or "3")
    except Exception:
        max_daily = 3
    return max(15, min(minutes, 180)), max(0, min(max_daily, 10))


def _active_bypass(conn, user: dict) -> dict:
    _ensure_lead_lock_bypass(conn)
    uid = _user_id(user)
    uname = _username(user)
    params = {"uid": uid or -1, "uname": uname}
    row = conn.execute(
        text(
            """
            SELECT id, reason, expires_at,
                   GREATEST(0, CEIL(EXTRACT(EPOCH FROM (expires_at - now())) / 60.0))::int AS minutes_left
            FROM public.lead_lock_bypass
            WHERE expires_at > now()
              AND (
                (:uid > 0 AND user_id=:uid)
                OR (:uname <> '' AND lower(COALESCE(username,'')) = lower(:uname))
              )
            ORDER BY expires_at DESC
            LIMIT 1
            """
        ),
        params,
    ).mappings().first()
    if not row:
        return {"active": False}
    return {
        "active": True,
        "id": int(row.get("id") or 0),
        "reason": row.get("reason") or "",
        "expires_at": row.get("expires_at").isoformat() if hasattr(row.get("expires_at"), "isoformat") else str(row.get("expires_at") or ""),
        "minutes_left": int(row.get("minutes_left") or 0),
    }


def _daily_bypass_count(conn, user: dict) -> int:
    _ensure_lead_lock_bypass(conn)
    uid = _user_id(user)
    uname = _username(user)
    return int(
        conn.execute(
            text(
                """
                SELECT COUNT(*)::int
                FROM public.lead_lock_bypass
                WHERE (created_at AT TIME ZONE 'America/Santiago')::date = (now() AT TIME ZONE 'America/Santiago')::date
                  AND (
                    (:uid > 0 AND user_id=:uid)
                    OR (:uname <> '' AND lower(COALESCE(username,'')) = lower(:uname))
                  )
                """
            ),
            {"uid": uid or -1, "uname": uname},
        ).scalar()
        or 0
    )


def _build_lead_lock(conn, user: dict, *, limit_per_status: int = 12) -> dict:
    role = (user.get("role") or user.get("rol") or "").upper()
    marcas = [int(x) for x in (user.get("marcas") or []) if str(x).isdigit()]
    only_own = not _is_admin(role)
    stale_ids: list[int] = []
    stale_by_status: dict[str, list] = {"NUEVO": [], "CONTACTADO": [], "COTIZADO": []}

    try:
        dry = auto_decline_stale_leads(dry_run=True, triggered_by="LEAD_LOCK", conn=conn)
        raw_items = dry.get("items") or []
    except Exception:
        raw_items = []

    ids = [int(x.get("id_lead")) for x in raw_items if str(x.get("id_lead", "")).isdigit()]
    lead_meta = {}
    if ids:
        try:
            rows = conn.execute(
                text(
                    """
                    SELECT l.id_lead, l.id_marca, l.cliente, l.created_at, l.updated_at,
                           l.fecha_evento, COALESCE(e.nombre,'') AS estado
                    FROM leads l
                    LEFT JOIN estados_lead e ON e.id_estado = l.id_estado
                    WHERE l.id_lead = ANY(:ids)
                      AND l.fecha_evento IS NOT NULL
                      AND l.fecha_evento >= (now() AT TIME ZONE 'America/Santiago')::date
                      AND l.fecha_evento >= date_trunc('month', (now() AT TIME ZONE 'America/Santiago')::date)::date
                      AND l.fecha_evento < (date_trunc('month', (now() AT TIME ZONE 'America/Santiago')::date) + INTERVAL '1 month')::date
                    """
                ),
                {"ids": ids[:500]},
            ).fetchall()
            lead_meta = {
                int(r[0]): {
                    "id_marca": int(r[1] or 0),
                    "cliente": r[2],
                    "created_at": r[3],
                    "updated_at": r[4],
                    "fecha_evento": r[5],
                    "estado": str(r[6] or "").upper(),
                }
                for r in rows
            }
        except Exception:
            lead_meta = {}

    for it in raw_items:
        try:
            lid = int(it.get("id_lead"))
        except Exception:
            continue
        if only_own and marcas:
            mid = (lead_meta.get(lid) or {}).get("id_marca")
            if not mid or mid not in marcas:
                continue
        if lid not in lead_meta:
            continue
        motivo = str(it.get("motivo") or "")
        stale_ids.append(lid)
        meta = lead_meta.get(lid) or {}
        last_dt = meta.get("updated_at") or meta.get("created_at")
        last_txt = last_dt.isoformat() if hasattr(last_dt, "isoformat") else (str(last_dt) if last_dt else "")
        payload = {"id_lead": lid, "motivo": motivo, "cliente": meta.get("cliente") or "", "created_at": last_txt}
        estado = str(meta.get("estado") or "").upper()
        if "NUEVO" in estado:
            stale_by_status["NUEVO"].append(payload)
        elif "CONTACT" in estado:
            stale_by_status["CONTACTADO"].append(payload)
        elif "COTIZ" in estado:
            stale_by_status["COTIZADO"].append(payload)

    for k in list(stale_by_status.keys()):
        stale_by_status[k] = stale_by_status[k][: max(1, int(limit_per_status))]

    bypass = _active_bypass(conn, user)
    minutes, max_daily = _lead_lock_policy()
    used_today = _daily_bypass_count(conn, user)
    hard_count = len(stale_by_status.get("NUEVO", [])) + len(stale_by_status.get("CONTACTADO", []))
    lock = (not _is_admin(role)) and hard_count > 0 and not bool(bypass.get("active"))
    return {
        "ok": True,
        "total": int(len(stale_ids)),
        "counts": {
            "NUEVO": len(stale_by_status.get("NUEVO", [])),
            "CONTACTADO": len(stale_by_status.get("CONTACTADO", [])),
            "COTIZADO": len(stale_by_status.get("COTIZADO", [])),
        },
        "stale_leads": stale_by_status,
        "lock": bool(lock),
        "bypass": {
            **bypass,
            "used_today": int(used_today),
            "max_daily": int(max_daily),
            "minutes": int(minutes),
            "remaining_today": max(0, int(max_daily) - int(used_today)),
        },
    }

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

def _touch_once_per(path: str, every_seconds: int) -> bool:
    """
    Best-effort throttle using a local stamp file.
    Returns True if caller should run the job now.
    """
    try:
        import os, time
        from pathlib import Path
        p = Path(path)
        now = time.time()
        if p.exists():
            try:
                if (now - p.stat().st_mtime) < float(every_seconds):
                    return False
            except Exception:
                pass
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(int(now)))
        return True
    except Exception:
        # If we can't write/read, prefer to not spam heavy jobs.
        return False

def _apply_past_event_auto_decline(conn, *, nuevo_id: int | None, contactado_id: int | None, cotizado_id: int | None, declinado_id: int | None) -> int:
    """
    Auto-decline leads whose fecha_evento already passed.
    Business rule:
    - NUEVO -> "sin seguimiento ejecutivo"
    - CONTACTADO/COTIZADO -> "cliente no contesto"
    """
    if not declinado_id:
        return 0
    if not (nuevo_id or contactado_id or cotizado_id):
        return 0
    # Keep motivos ASCII (some deployments had SQL_ASCII client encoding in tooling).
    motivo_nuevo = "AUTO: Evento pasado (sin seguimiento ejecutivo)"
    motivo_cliente = "AUTO: Evento pasado (cliente no contesto)"

    # Only update non-declined leads with fecha_evento < today.
    # Also append to notas if it exists.
    cols = {r[0] for r in conn.execute(text("""
      SELECT column_name FROM information_schema.columns
      WHERE table_schema='public' AND table_name='leads'
    """)).fetchall()}
    set_parts = ["id_estado=:decl", "updated_at=now()"]
    if "declinado_motivo" in cols:
        set_parts.append("declinado_motivo = c.motivo")
    if "declinado_at" in cols:
        set_parts.append("declinado_at = now()")
    if "notas" in cols:
        set_parts.append(
            "notas = CASE WHEN l.notas IS NULL OR btrim(l.notas)='' THEN (:nota_prefix || c.motivo) "
            "ELSE l.notas || E'\\n' || (:nota_prefix || c.motivo) END"
        )

    # Build state list
    states = [x for x in (nuevo_id, contactado_id, cotizado_id) if isinstance(x, int)]
    if not states:
        return 0

    q = text(f"""
      WITH cand AS (
        SELECT
          l.id_lead,
          CASE
            WHEN l.id_estado = :nuevo THEN :mot_nuevo
            ELSE :mot_cliente
          END AS motivo
        FROM leads l
        WHERE l.id_estado = ANY(:states)
          AND l.fecha_evento IS NOT NULL
          AND l.fecha_evento < CURRENT_DATE
          AND l.id_estado <> :decl
      )
      UPDATE leads l
      SET {", ".join(set_parts)}
      FROM cand c
      WHERE l.id_lead = c.id_lead
      RETURNING l.id_lead
    """)
    params = {
        "decl": int(declinado_id),
        "states": states,
        "nuevo": int(nuevo_id or -1),
        "mot_nuevo": motivo_nuevo,
        "mot_cliente": motivo_cliente,
        "nota_prefix": f"[AUTO-DECLINADO {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}] ",
    }
    rows = conn.execute(q, params).fetchall()
    return len(rows)


def _apply_quote_state_normalize(conn, *, cotizado_id: int | None, confirmado_id: int | None, declinado_id: int | None) -> int:
    """
    Si un lead tiene cotización (sistema o manual completo) y está en NUEVO/CONTACTADO,
    lo movemos a COTIZADO.
    """
    if not cotizado_id:
        return 0
    cols = {r[0] for r in conn.execute(text("""
      SELECT column_name FROM information_schema.columns
      WHERE table_schema='public' AND table_name='leads'
    """)).fetchall()}

    # Regla negocio:
    # - Sistema: existe registro en `cotizaciones` (o id_cotizacion_vigente).
    # - Manual: requiere monto_cotizado > 0 Y num_cotizacion no vacío (PDF es opcional).
    parts = []
    # manual completo
    if ("monto_cotizado" in cols) and ("num_cotizacion" in cols):
        parts.append("(COALESCE(l.monto_cotizado,0) > 0 AND NULLIF(btrim(COALESCE(l.num_cotizacion::text,'')),'') IS NOT NULL)")
    # sistema
    parts.append("EXISTS (SELECT 1 FROM public.cotizaciones c WHERE c.id_lead=l.id_lead)")
    if "id_cotizacion_vigente" in cols:
        parts.append("(l.id_cotizacion_vigente IS NOT NULL AND NULLIF(btrim(l.id_cotizacion_vigente::text),'' ) IS NOT NULL AND btrim(l.id_cotizacion_vigente::text) <> '0')")
    has_quote_sql = "(" + " OR ".join(parts) + ")"

    # estados base (tolerante si IDs cambian): NUEVO/CONTACTADO por nombre
    nuevo = conn.execute(text("SELECT id_estado FROM estados_lead WHERE UPPER(nombre) LIKE '%NUEVO%' LIMIT 1")).scalar()
    contact = conn.execute(text("SELECT id_estado FROM estados_lead WHERE UPPER(nombre) LIKE '%CONTACT%' LIMIT 1")).scalar()
    states = [int(x) for x in (nuevo, contact) if x is not None]
    if not states:
        return 0

    q = text(f"""
      UPDATE leads l
      SET id_estado=:cot, updated_at=now()
      WHERE l.id_estado = ANY(:states)
        AND ({has_quote_sql})
        AND (:conf IS NULL OR l.id_estado <> :conf)
        AND (:decl IS NULL OR l.id_estado <> :decl)
      RETURNING l.id_lead
    """)
    rows = conn.execute(q, {"cot": int(cotizado_id), "states": states, "conf": int(confirmado_id) if confirmado_id else None, "decl": int(declinado_id) if declinado_id else None}).fetchall()
    return len(rows)


def _col_exists(table: str, col: str) -> bool:
    with get_connection() as conn:
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


def _estado_id(name_like: str) -> int | None:
    with get_connection() as conn:
        r = conn.execute(
            text("SELECT id_estado FROM estados_lead WHERE UPPER(nombre) LIKE :n LIMIT 1"),
            {"n": f"%{name_like.upper()}%"},
        ).fetchone()
        return int(r[0]) if r else None


@router.get("")
def get_notifications(user=Depends(get_current_user), force_jobs: int = 0, light: int = 0):
    # Shared hosting: allow disabling the notifications module completely.
    # Keep the endpoint fast (return empty) to avoid Passenger queue full from legacy polling.
    try:
        notifs_enabled = str(os.getenv("CRM_NOTIFS_ENABLED") or "").strip().lower() in ("1", "true", "yes", "on")
    except Exception:
        notifs_enabled = True
    if not notifs_enabled:
        return {
            "ok": True,
            "total": 0,
            "items": [],
            "counts": {},
            "system_notifs_preview": [],
            "jobs": {"past_event_declined": 0, "quote_normalized": 0, "stale_declined": 0},
            "job_errors": {},
            "stale_leads": {"NUEVO": [], "CONTACTADO": [], "COTIZADO": []},
            "lock": False,
        }

    items = []
    counts = {}
    system_notifs_preview = []
    jobs = {
        "past_event_declined": 0,
        "quote_normalized": 0,
        "stale_declined": 0,
    }
    job_errors: dict[str, str] = {}
    role = (user.get("role") or user.get("rol") or "").upper()
    marcas = [int(x) for x in (user.get("marcas") or []) if str(x).isdigit()]
    only_own = not _is_admin(role)

    # Shared hosting: optionally disable the whole notifications module for non-RRHH.
    # This protects the server if some clients are still polling /notifications.
    try:
        rrhh_only = str(os.getenv("CRM_NOTIFS_RRHH_ONLY") or "").strip().lower() in ("1", "true", "yes", "on")
        if rrhh_only and ("RRHH" not in role):
            return {
                "ok": True,
                "total": 0,
                "items": [],
                "counts": {},
                "system_notifs_preview": [],
                "jobs": {"past_event_declined": 0, "quote_normalized": 0, "stale_declined": 0},
                "job_errors": {},
                "stale_leads": {"NUEVO": [], "CONTACTADO": [], "COTIZADO": []},
                "lock": False,
            }
    except Exception:
        pass

    run_jobs = str(os.getenv("CRM_NOTIFS_RUN_JOBS") or "").strip().lower() in ("1", "true", "yes", "on")
    include_stale = str(os.getenv("CRM_NOTIFS_INCLUDE_STALE") or "").strip().lower() in ("1", "true", "yes", "on")
    is_light = int(light or 0) == 1

    with get_connection() as conn:
        _ensure_system_notifs(conn)
        confirmado_id = _estado_id_conn(conn, "CONFIRM")
        declinado_id = _estado_id_conn(conn, "DECLIN")
        nuevo_id = _estado_id_conn(conn, "NUEVO")
        contactado_id = _estado_id_conn(conn, "CONTACT")
        cotizado_id = _estado_id_conn(conn, "COTIZ")

        # Auto-decline for "evento pasado" is cheap and should be automatic.
        # Throttle to run at most once per 30 minutes per server process.
        try:
            from pathlib import Path
            root = Path(__file__).resolve().parents[2]
            stamp = str(root / "data" / "debug" / "auto_decline_past_events.stamp")
            if run_jobs and (int(force_jobs or 0) == 1 or _touch_once_per(stamp, every_seconds=1800)):
                try:
                    changed = _apply_past_event_auto_decline(
                        conn,
                        nuevo_id=nuevo_id,
                        contactado_id=contactado_id,
                        cotizado_id=cotizado_id,
                        declinado_id=declinado_id,
                    )
                    if changed:
                        jobs["past_event_declined"] = int(changed)
                        conn.commit()
                except Exception:
                    job_errors["past_event_decline"] = "error"
                    # Best-effort log (no romper notificaciones)
                    try:
                        (root / "data" / "debug" / "auto_decline_past_events.log").open("a", encoding="utf-8").write(
                            f"[{datetime.utcnow().isoformat()}] error auto_decline_past_events\n"
                        )
                    except Exception:
                        pass
        except Exception:
            pass


@router.get("/lead_lock")
def lead_lock_status(user=Depends(get_current_user)):
    try:
        with get_connection() as conn:
            return _build_lead_lock(conn, user)
    except Exception:
        return {
            "ok": True,
            "total": 0,
            "counts": {"NUEVO": 0, "CONTACTADO": 0, "COTIZADO": 0},
            "stale_leads": {"NUEVO": [], "CONTACTADO": [], "COTIZADO": []},
            "lock": False,
            "bypass": {"active": False, "used_today": 0, "max_daily": 3, "minutes": 60, "remaining_today": 3},
        }


@router.post("/lead_lock/bypass")
def lead_lock_bypass(payload: dict = Body(...), user=Depends(get_current_user)):
    reason = str((payload or {}).get("reason") or (payload or {}).get("motivo") or "").strip()
    if len(reason) < 12:
        raise HTTPException(400, "Motivo obligatorio: explica la urgencia o por qué necesitas aplazar.")

    role = (user.get("role") or user.get("rol") or "").upper()
    minutes, max_daily = _lead_lock_policy()
    if _is_admin(role):
        raise HTTPException(400, "Admin no necesita bypass de bloqueo comercial.")

    with get_connection() as conn:
        state = _build_lead_lock(conn, user)
        if not state.get("lock") and not (state.get("total") or 0):
            return {"ok": True, "bypass": state.get("bypass") or {}, "message": "No tienes bloqueo activo."}
        used = _daily_bypass_count(conn, user)
        if used >= max_daily:
            raise HTTPException(429, f"Límite diario de aplazamientos alcanzado ({max_daily}). Debes trabajar los leads pendientes.")

        uid = _user_id(user)
        uname = _username(user)
        stale = state.get("stale_leads") or {}
        stale_ids = []
        for arr in (stale.get("NUEVO") or [], stale.get("CONTACTADO") or [], stale.get("COTIZADO") or []):
            try:
                stale_ids.append(str(arr.get("id_lead")))
            except Exception:
                pass
        conn.execute(
            text(
                """
                INSERT INTO public.lead_lock_bypass(user_id, username, role, reason, minutes, expires_at, stale_count, stale_ids)
                VALUES (:uid, :uname, :role, :reason, :minutes, now() + (:minutes || ' minutes')::interval, :stale_count, :stale_ids)
                """
            ),
            {
                "uid": uid,
                "uname": uname,
                "role": role,
                "reason": reason[:1000],
                "minutes": int(minutes),
                "stale_count": int(state.get("total") or 0),
                "stale_ids": ",".join([x for x in stale_ids if x and x != "None"])[:2000],
            },
        )
        try:
            log_activity(
                conn,
                username=uname,
                user_id=uid,
                role=role,
                action="lead_lock.bypass",
                entity_type="usuario",
                entity_id=uid,
                meta={
                    "reason": reason[:500],
                    "minutes": int(minutes),
                    "used_today": int(used) + 1,
                    "max_daily": int(max_daily),
                    "stale_count": int(state.get("total") or 0),
                    "stale_ids": [x for x in stale_ids if x and x != "None"][:80],
                },
            )
        except Exception:
            pass
        conn.commit()
        bypass = _active_bypass(conn, user)
        return {
            "ok": True,
            "bypass": {
                **bypass,
                "used_today": int(used) + 1,
                "max_daily": int(max_daily),
                "minutes": int(minutes),
                "remaining_today": max(0, int(max_daily) - int(used) - 1),
            },
        }

        # Normalize: si hay cotización, el lead no debería quedar en NUEVO/CONTACTADO.
        # Throttle to once per 15 minutes.
        try:
            from pathlib import Path
            root = Path(__file__).resolve().parents[2]
            stamp = str(root / "data" / "debug" / "normalize_cotizado.stamp")
            if run_jobs and (int(force_jobs or 0) == 1 or _touch_once_per(stamp, every_seconds=900)):
                changed = _apply_quote_state_normalize(
                    conn,
                    cotizado_id=cotizado_id,
                    confirmado_id=confirmado_id,
                    declinado_id=declinado_id,
                )
                if changed:
                    jobs["quote_normalized"] = int(changed)
                    conn.commit()
        except Exception:
            pass

        # If caller explicitly requests jobs, allow applying stale rules (declinar) as well.
        # This is heavier than the other jobs; we only run it for admin.
        if run_jobs and int(force_jobs or 0) == 1 and _is_admin(role):
            try:
                res = auto_decline_stale_leads(dry_run=False, triggered_by="NOTIFICATIONS(force_jobs)", conn=conn)
                if isinstance(res, dict) and res.get("ok") and not res.get("dry_run"):
                    jobs["stale_declined"] = int(res.get("total") or 0)
            except Exception:
                job_errors["stale_decline"] = "error"

        # A) Eventos de hoy / mañana (confirmados, por marca)
        if (not is_light) and confirmado_id and _col_exists_conn(conn, "leads", "fecha_evento"):
            params = {"e": confirmado_id}
            marca_sql = ""
            if only_own and marcas:
                marca_sql = " AND id_marca = ANY(:m)"
                params["m"] = marcas
            today_rows = conn.execute(
                text(f"""
                    SELECT id_lead, cliente, fecha_evento, id_marca
                    FROM leads
                    WHERE fecha_evento = current_date
                      AND id_estado = :e
                    {marca_sql}
                    ORDER BY id_lead DESC
                """),
                params,
            ).fetchall()
            tom_rows = conn.execute(
                text(f"""
                    SELECT id_lead, cliente, fecha_evento, id_marca
                    FROM leads
                    WHERE fecha_evento = (current_date + interval '1 day')::date
                      AND id_estado = :e
                    {marca_sql}
                    ORDER BY id_lead DESC
                """),
                params,
            ).fetchall()
            counts["events_today"] = int(len(today_rows))
            counts["events_tomorrow"] = int(len(tom_rows))
            items.append({
                "key": "events_today",
                "title": "Eventos hoy (confirmados)",
                "count": int(len(today_rows)),
                "url": "/web/views/leads.html"
            })
            items.append({
                "key": "events_tomorrow",
                "title": "Eventos mañana (confirmados)",
                "count": int(len(tom_rows)),
                "url": "/web/views/leads.html"
            })

        # A2) Leads nuevos de hoy (para sonido + badge en frontend).
        # Nota: el panel usa el delta de este contador para reproducir "Nuevo lead".
        try:
            if _col_exists_conn(conn, "leads", "created_at"):
                date_col = "created_at"
            elif _col_exists_conn(conn, "leads", "fecha_ingreso"):
                date_col = "fecha_ingreso"
            else:
                date_col = "updated_at"

            params = {}
            marca_sql = ""
            if only_own and marcas:
                marca_sql = " AND id_marca = ANY(:m) "
                params["m"] = marcas

            new_today = conn.execute(
                text(
                    f"""
                    SELECT COUNT(*)::int
                    FROM leads
                    WHERE DATE({date_col}) = current_date
                    {marca_sql}
                    """
                ),
                params,
            ).scalar() or 0

            counts["leads_nuevos"] = int(new_today)
            items.append({
                "key": "leads_nuevos",
                "title": "Leads nuevos (hoy)",
                "count": int(new_today),
                "url": "/web/views/leads.html"
            })
        except Exception:
            pass

        # B) Leads sin movimiento (solo lectura): usamos el motor oficial stale_leads (dry_run)
        stale_ids: list[int] = []
        stale_by_status: dict[str, list] = {"NUEVO": [], "CONTACTADO": [], "COTIZADO": []}
        if (not is_light) and (include_stale or (int(force_jobs or 0) == 1 and _is_admin(role))):
            try:
                dry = auto_decline_stale_leads(dry_run=True, triggered_by="NOTIFICATIONS", conn=conn)
                raw_items = dry.get("items") or []
                # Enriquecemos con datos del lead (cliente + timestamps) y filtramos por marca si corresponde.
                ids = [int(x.get("id_lead")) for x in raw_items if str(x.get("id_lead", "")).isdigit()]
                if ids:
                    rows = conn.execute(
                        text(
                            """
                            SELECT id_lead, id_marca, cliente, created_at, updated_at
                            FROM leads
                            WHERE id_lead = ANY(:ids)
                            """
                        ),
                        {"ids": ids},
                    ).fetchall()
                    lead_meta = {
                        int(r[0]): {
                            "id_marca": int(r[1] or 0),
                            "cliente": r[2],
                            "created_at": r[3],
                            "updated_at": r[4],
                        }
                        for r in rows
                    }
                else:
                    lead_meta = {}

                for it in raw_items:
                    try:
                        lid = int(it.get("id_lead"))
                    except Exception:
                        continue
                    if only_own and marcas:
                        mid = (lead_meta.get(lid) or {}).get("id_marca")
                        if not mid or mid not in marcas:
                            continue
                    motivo = str(it.get("motivo") or "")
                    stale_ids.append(lid)
                    meta = lead_meta.get(lid) or {}
                    cliente = meta.get("cliente") or ""
                    last_dt = meta.get("updated_at") or meta.get("created_at")
                    last_txt = ""
                    try:
                        if isinstance(last_dt, (datetime,)):
                            last_txt = last_dt.isoformat()
                        elif last_dt:
                            last_txt = str(last_dt)
                    except Exception:
                        last_txt = ""
                    payload = {"id_lead": lid, "motivo": motivo, "cliente": cliente, "created_at": last_txt}
                    if "NUEVO" in motivo:
                        stale_by_status["NUEVO"].append(payload)
                    elif "CONTACTADO" in motivo:
                        stale_by_status["CONTACTADO"].append(payload)
                    elif "COTIZADO" in motivo:
                        stale_by_status["COTIZADO"].append(payload)

                counts["leads_sin_mov"] = int(len(stale_ids))
                if counts["leads_sin_mov"] > 0:
                    qparam = ",".join(str(x) for x in stale_ids[:200])
                    items.append({
                        "key": "leads_sin_mov",
                        "title": "Leads sin movimiento (requieren acción)",
                        "count": counts["leads_sin_mov"],
                        "url": f"/web/views/leads.html?stale_ids={qparam}"
                    })
            except Exception:
                pass

        # C) Notificaciones internas por rol (eventos agendados, etc.)
        try:
            role_targets = _role_targets(role)
            if not role_targets:
                role_targets = [role]
            rows = conn.execute(
                text(
                    """
                    SELECT id, created_at, kind, id_lead, title, body, payload, read_at
                    FROM public.system_notifs
                    WHERE role_target = ANY(:roles)
                      AND created_at >= (now() - interval '7 days')
                    ORDER BY created_at DESC
                    LIMIT 80
                    """
                ),
                {"roles": role_targets},
            ).fetchall()
            unread = []
            preview = []
            for r in rows:
                nid = int(r[0])
                created_at = r[1]
                kind = str(r[2] or "")
                lid = r[3]
                title = str(r[4] or "Notificación")
                body = str(r[5] or "")
                payload = r[6] or {}
                read_at = r[7]
                item = {
                    "id": nid,
                    "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at or ""),
                    "kind": kind,
                    "id_lead": int(lid) if lid else None,
                    "title": title,
                    "body": body,
                    "payload": payload,
                    "read_at": read_at.isoformat() if hasattr(read_at, "isoformat") else (str(read_at) if read_at else None),
                }
                preview.append(item)
                if not read_at:
                    unread.append(item)

            if unread:
                items.append(
                    {
                        "key": "system_notifs",
                        "title": "Eventos agendados / Operaciones",
                        "count": int(len(unread)),
                        "url": "/web/views/system_notifs.html",
                    }
                )
                counts["system_notifs"] = int(len(unread))
            # Adjuntamos preview (no cuenta como "items" para badge)
            system_notifs_preview = preview[:12]
        except Exception:
            pass

    total = sum([it["count"] for it in items])
    try:
        bypass = _active_bypass(conn, user)
        minutes, max_daily = _lead_lock_policy()
        used_today = _daily_bypass_count(conn, user)
    except Exception:
        minutes, max_daily, used_today = _lead_lock_policy()[0], _lead_lock_policy()[1], 0
        bypass = {"active": False}
    lock = (
        (not _is_admin(role))
        and (len(stale_by_status.get("NUEVO", [])) + len(stale_by_status.get("CONTACTADO", [])) > 0)
        and not bool(bypass.get("active"))
    )
    return {
        "ok": True,
        "total": total,
        "items": items,
        "counts": counts,
        "system_notifs_preview": system_notifs_preview,
        "jobs": jobs,
        "job_errors": job_errors,
        "stale_leads": stale_by_status,
        "lock": lock,
        "bypass": {
            **bypass,
            "used_today": int(used_today),
            "max_daily": int(max_daily),
            "minutes": int(minutes),
            "remaining_today": max(0, int(max_daily) - int(used_today)),
        },
    }


@router.get("/system")
def list_system_notifs(
    limit: int = 50,
    unread_only: int = 0,
    user=Depends(get_current_user),
):
    """
    Lista notificaciones internas por rol (ej: EVENT_AGENDADO).
    Se usa para Operaciones/Compras/Bodega/Admin desde UI.
    """
    role = (user.get("role") or user.get("rol") or "").upper()
    uname = (user.get("username") or user.get("email") or user.get("name") or "").strip()
    if uname and "@" in uname:
        # por si viene email
        uname = uname.split("@", 1)[0]
    role_targets = _role_targets(role)
    if not role_targets:
        role_targets = [role]
    limit = max(1, min(int(limit or 50), 200))
    with get_connection() as conn:
        _ensure_system_notifs(conn)
        where = ["role_target = ANY(:roles)", "created_at >= (now() - interval '30 days')"]
        # Soporte notifs dirigidas a un usuario específico (ej: solo Oscar).
        # Si payload.username_target existe, solo lo ve ese usuario; si no existe, lo ven todos por rol.
        if uname:
            where.append("(payload->>'username_target' IS NULL OR payload->>'username_target' = :uname)")
        if int(unread_only or 0) == 1:
            where.append("read_at IS NULL")
        rows = conn.execute(
            text(
                f"""
                SELECT id, created_at, kind, id_lead, title, body, payload, read_at, read_by
                FROM public.system_notifs
                WHERE {' AND '.join(where)}
                ORDER BY created_at DESC
                LIMIT :lim
                """
            ),
            {"roles": role_targets, "lim": limit, "uname": uname},
        ).mappings().all()
        # Dedup: un mismo evento suele insertarse por varios role_target (ADMIN/OPERACIONES/MICE...).
        # La UI no muestra role_target, así que lo colapsamos por (kind + lead + id_evento + fecha_evento + title).
        dedup: dict[str, dict] = {}
        for r in rows:
            payload = r.get("payload") or {}
            try:
                if not isinstance(payload, dict):
                    payload = {}
            except Exception:
                payload = {}
            lid = int(r.get("id_lead")) if r.get("id_lead") else None
            k = str(r.get("kind") or "")
            title = str(r.get("title") or "")
            id_evento = payload.get("id_evento") or payload.get("evento_id") or ""
            fecha = payload.get("fecha_evento") or payload.get("fecha") or ""
            key = f"{k}|{lid or ''}|{id_evento}|{fecha}|{title}"
            cur = {
                "id": int(r.get("id") or 0),
                "created_at": (r.get("created_at").isoformat() if r.get("created_at") else ""),
                "kind": k,
                "id_lead": lid,
                "title": title,
                "body": str(r.get("body") or ""),
                "payload": payload,
                "read_at": (r.get("read_at").isoformat() if r.get("read_at") else None),
                "read_by": r.get("read_by") or None,
            }
            prev = dedup.get(key)
            if not prev:
                dedup[key] = cur
            else:
                # prefer unread; else prefer newest
                if (prev.get("read_at") and not cur.get("read_at")):
                    dedup[key] = cur
                elif (cur.get("created_at") or "") > (prev.get("created_at") or ""):
                    dedup[key] = cur
        items = list(dedup.values())
        items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
        items = items[:limit]
        return {"ok": True, "items": items}


@router.post("/system/{id}/read")
def mark_system_notif_read(id: int, user=Depends(get_current_user)):
    role = (user.get("role") or user.get("rol") or "").upper()
    who = (user.get("email") or user.get("username") or user.get("name") or str(user.get("id") or "")).strip() or role or "user"
    role_targets = _role_targets(role) or [role]
    with get_connection() as conn:
        _ensure_system_notifs(conn)
        conn.execute(
            text(
                """
                UPDATE public.system_notifs
                SET read_at = now(), read_by = :who
                WHERE id = :id AND role_target = ANY(:roles)
                """
            ),
            {"id": int(id), "roles": role_targets, "who": who},
        )
        conn.commit()
    return {"ok": True}


@router.post("/system/read_all")
def mark_system_notifs_read_all(user=Depends(get_current_user)):
    role = (user.get("role") or user.get("rol") or "").upper()
    who = (user.get("email") or user.get("username") or user.get("name") or str(user.get("id") or "")).strip() or role or "user"
    role_targets = _role_targets(role) or [role]
    with get_connection() as conn:
        _ensure_system_notifs(conn)
        conn.execute(
            text(
                """
                UPDATE public.system_notifs
                SET read_at = now(), read_by = :who
                WHERE role_target = ANY(:roles) AND read_at IS NULL
                """
            ),
            {"roles": role_targets, "who": who},
        )
    conn.commit()
    return {"ok": True}


@router.get("/sold_latest")
def sold_latest(user=Depends(get_current_user)):
    """
    Lightweight helper for SUPERADMIN: returns latest unread EVENT_SOLD notif (if any).
    Intended to be checked infrequently (e.g., on dashboard load / every few minutes).
    """
    role = (user.get("role") or user.get("rol") or "").upper()
    # Safety: only SUPERADMIN should see these alerts.
    if "SUPER" not in role:
        return {"ok": True, "item": None}
    uname = (user.get("username") or user.get("email") or user.get("name") or "").strip()
    if uname and "@" in uname:
        uname = uname.split("@", 1)[0]
    role_targets = _role_targets(role) or [role]

    with get_connection() as conn:
        _ensure_system_notifs(conn)
        where = ["kind='EVENT_SOLD'", "read_at IS NULL", "role_target = ANY(:roles)"]
        if uname:
            where.append("(payload->>'username_target' IS NULL OR payload->>'username_target' = :uname)")
        r = conn.execute(
            text(
                f"""
                SELECT id, created_at, id_lead, title, body, payload
                FROM public.system_notifs
                WHERE {' AND '.join(where)}
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"roles": role_targets, "uname": uname},
        ).fetchone()
        if not r:
            return {"ok": True, "item": None}
        created_at = r[1]
        return {
            "ok": True,
            "item": {
                "id": int(r[0]),
                "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at or ""),
                "id_lead": int(r[2]) if r[2] else None,
                "title": str(r[3] or ""),
                "body": str(r[4] or ""),
                "payload": r[5] or {},
            },
        }
