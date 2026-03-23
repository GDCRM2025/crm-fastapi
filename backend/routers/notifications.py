from __future__ import annotations

from datetime import datetime, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy import text

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
    rk = _role_key(role)
    out: list[str] = []
    if not rk:
        return []
    if "admin" in rk:
        out += ["ADMIN", "SUPERADMIN"]
    if "operac" in rk:
        out += ["OPERACIONES", "JEFE DE OPERACIONES"]
    if "compra" in rk:
        out += ["COMPRAS", "JEFE DE COMPRAS"]
    if "bodeg" in rk:
        out += ["BODEGUERO"]
    if "mice" in rk:
        out += ["MICE"]
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
    Si un lead tiene cotización (monto/num/pdf o cotizaciones) y está en NUEVO/CONTACTADO,
    lo movemos a COTIZADO.
    """
    if not cotizado_id:
        return 0
    cols = {r[0] for r in conn.execute(text("""
      SELECT column_name FROM information_schema.columns
      WHERE table_schema='public' AND table_name='leads'
    """)).fetchall()}

    parts = []
    if "monto_cotizado" in cols:
        parts.append("COALESCE(l.monto_cotizado,0) > 0")
    if "num_cotizacion" in cols:
        parts.append("l.num_cotizacion IS NOT NULL AND NULLIF(btrim(l.num_cotizacion::text),'') IS NOT NULL")
    if "cotizacion_pdf_url" in cols:
        parts.append("COALESCE(NULLIF(btrim(l.cotizacion_pdf_url),''), NULL) IS NOT NULL")
    # tabla cotizaciones
    parts.append("EXISTS (SELECT 1 FROM public.cotizaciones c WHERE c.id_lead=l.id_lead)")
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
def get_notifications(user=Depends(get_current_user), force_jobs: int = 0):
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

    with get_connection() as conn:
        _ensure_system_notifs(conn)
        confirmado_id = _estado_id("CONFIRM")
        declinado_id = _estado_id("DECLIN")
        nuevo_id = _estado_id("NUEVO")
        contactado_id = _estado_id("CONTACT")
        cotizado_id = _estado_id("COTIZ")

        # Auto-decline for "evento pasado" is cheap and should be automatic.
        # Throttle to run at most once per 30 minutes per server process.
        try:
            from pathlib import Path
            root = Path(__file__).resolve().parents[2]
            stamp = str(root / "data" / "debug" / "auto_decline_past_events.stamp")
            if int(force_jobs or 0) == 1 or _touch_once_per(stamp, every_seconds=1800):
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

        # Normalize: si hay cotización, el lead no debería quedar en NUEVO/CONTACTADO.
        # Throttle to once per 15 minutes.
        try:
            from pathlib import Path
            root = Path(__file__).resolve().parents[2]
            stamp = str(root / "data" / "debug" / "normalize_cotizado.stamp")
            if int(force_jobs or 0) == 1 or _touch_once_per(stamp, every_seconds=900):
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
        if int(force_jobs or 0) == 1 and _is_admin(role):
            try:
                res = auto_decline_stale_leads(dry_run=False, triggered_by="NOTIFICATIONS(force_jobs)", conn=conn)
                if isinstance(res, dict) and res.get("ok") and not res.get("dry_run"):
                    jobs["stale_declined"] = int(res.get("total") or 0)
            except Exception:
                job_errors["stale_decline"] = "error"

        # A) Eventos de hoy / mañana (confirmados, por marca)
        if confirmado_id and _col_exists("leads", "fecha_evento"):
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
            if _col_exists("leads", "created_at"):
                date_col = "created_at"
            elif _col_exists("leads", "fecha_ingreso"):
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
                # Usamos "created_at" como campo UI para mostrar "último movimiento".
                # (panel.js lo muestra como texto secundario)
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
    lock = (not _is_admin(role)) and (len(stale_by_status.get("NUEVO", [])) + len(stale_by_status.get("CONTACTADO", [])) > 0)
    return {
        "ok": True,
        "total": total,
        "items": items,
        "counts": counts,
        "system_notifs_preview": system_notifs_preview,
        "jobs": jobs,
        "job_errors": job_errors,
        "stale_leads": stale_by_status,
        "lock": lock
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
    role_targets = _role_targets(role)
    if not role_targets:
        role_targets = [role]
    limit = max(1, min(int(limit or 50), 200))
    with get_connection() as conn:
        _ensure_system_notifs(conn)
        where = ["role_target = ANY(:roles)", "created_at >= (now() - interval '30 days')"]
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
            {"roles": role_targets, "lim": limit},
        ).mappings().all()
        items = []
        for r in rows:
            items.append(
                {
                    "id": int(r.get("id") or 0),
                    "created_at": (r.get("created_at").isoformat() if r.get("created_at") else ""),
                    "kind": r.get("kind") or "",
                    "id_lead": int(r.get("id_lead")) if r.get("id_lead") else None,
                    "title": r.get("title") or "",
                    "body": r.get("body") or "",
                    "payload": r.get("payload") or {},
                    "read_at": (r.get("read_at").isoformat() if r.get("read_at") else None),
                    "read_by": r.get("read_by") or None,
                }
            )
        return {"ok": True, "items": items}


@router.post("/system/{id}/read")
def mark_system_notif_read(id: int, user=Depends(get_current_user)):
    role = (user.get("role") or user.get("rol") or "").upper()
    who = (user.get("email") or user.get("username") or user.get("name") or str(user.get("id") or "")).strip() or role or "user"
    with get_connection() as conn:
        _ensure_system_notifs(conn)
        conn.execute(
            text(
                """
                UPDATE public.system_notifs
                SET read_at = now(), read_by = :who
                WHERE id = :id AND role_target = :r
                """
            ),
            {"id": int(id), "r": role, "who": who},
        )
        conn.commit()
    return {"ok": True}


@router.post("/system/read_all")
def mark_system_notifs_read_all(user=Depends(get_current_user)):
    role = (user.get("role") or user.get("rol") or "").upper()
    who = (user.get("email") or user.get("username") or user.get("name") or str(user.get("id") or "")).strip() or role or "user"
    with get_connection() as conn:
        _ensure_system_notifs(conn)
        conn.execute(
            text(
                """
                UPDATE public.system_notifs
                SET read_at = now(), read_by = :who
                WHERE role_target = :r AND read_at IS NULL
                """
            ),
            {"r": role, "who": who},
        )
        conn.commit()
    return {"ok": True}
