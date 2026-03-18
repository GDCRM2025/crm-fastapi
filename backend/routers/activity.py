from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from html import escape as _html_escape
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from backend.core.db import get_connection
from backend.core.email import send_email, send_email_group
from backend.core.activity_log import ensure_activity_log, fmt_window_title
from backend.routers.auth import get_current_user

router = APIRouter(prefix="/activity", tags=["activity"])


def _role(user: dict) -> str:
    return str(user.get("role") or user.get("rol") or "").upper()


def _require_admin(user: dict) -> None:
    if _role(user) not in ("ADMIN", "SUPERADMIN"):
        raise HTTPException(403, "Solo Admin")


def _parse_recipients() -> List[str]:
    raw = (os.getenv("ADMIN_DIGEST_TO") or "").strip()
    if not raw:
        return []
    out: List[str] = []
    for x in raw.split(","):
        e = x.strip()
        if "@" in e and "." in e:
            out.append(e)
    return out


def _recipients_from_db() -> List[str]:
    """
    Si no hay ADMIN_DIGEST_TO, tomamos admins desde BD.
    Excluye operadores/choferes porque filtramos por rol.
    """
    try:
        with get_connection() as conn:
            # Intento 1: por tabla roles
            rows = conn.execute(
                text(
                    """
                    SELECT DISTINCT u.email
                    FROM public.usuarios u
                    LEFT JOIN public.roles r ON r.id_rol=u.id_rol
                    WHERE COALESCE(u.is_active, TRUE) = TRUE
                      AND u.email IS NOT NULL AND u.email <> ''
                      AND (
                        UPPER(COALESCE(r.nombre,'')) IN ('ADMIN','SUPERADMIN')
                        OR UPPER(COALESCE(u.rol,'')) IN ('ADMIN','SUPERADMIN')
                      )
                    """
                )
            ).fetchall()
        out: List[str] = []
        for r in rows:
            e = (r[0] or "").strip()
            if "@" in e and "." in e:
                out.append(e)
        return sorted(set(out))
    except Exception:
        return []

def _suppress_recipients(addrs: List[str]) -> List[str]:
    """
    Temporal: suprime envíos a ciertos correos (ej: SIMON) hasta que el digest quede validado.
    - Por defecto suprime simonurrutia.m@gmail.com
    - Solo se re-habilita si *dos* flags están activas:
      CRM_ALLOW_SIMON_DIGEST=1 y CRM_DIGEST_VALIDATED=1
    - Se puede agregar lista extra con SUPPRESS_DIGEST_TO="a@b.com,c@d.com"
    """
    if not addrs:
        return []
    allow_simon = (
        str(os.getenv("CRM_ALLOW_SIMON_DIGEST") or "").strip().lower() in ("1", "true", "yes")
        and str(os.getenv("CRM_DIGEST_VALIDATED") or "").strip().lower() in ("1", "true", "yes")
    )
    suppressed = set()
    if not allow_simon:
        suppressed.add("simonurrutia.m@gmail.com")
    extra = (os.getenv("SUPPRESS_DIGEST_TO") or "").strip()
    if extra:
        for x in extra.split(","):
            e = x.strip().lower()
            if "@" in e:
                suppressed.add(e)
    if not suppressed:
        return addrs
    out: List[str] = []
    for a in addrs:
        if (a or "").strip().lower() in suppressed:
            continue
        out.append(a)
    return out


def _build_digest_window(
    dt_from: datetime | None,
    dt_to: datetime | None,
    subject_prefix: str = "CRM · Digest actividad",
    filter_username: str = "",
) -> tuple[str, str, str]:
    """
    Construye contenido del digest (texto + HTML). No envía correos.
    """
    now = datetime.now(timezone.utc)
    dt_from = dt_from or (now - timedelta(hours=6))
    dt_to = dt_to or now

    with get_connection() as conn:
        ensure_activity_log(conn)
        if filter_username:
            rows = conn.execute(
                text(
                    """
                    SELECT created_at, username, role, action, entity_type, entity_id
                    FROM public.activity_log
                    WHERE created_at >= :a AND created_at <= :b
                      AND (username = :u OR LOWER(username) = LOWER(:u))
                    ORDER BY created_at DESC
                    LIMIT 4000
                    """
                ),
                {"a": dt_from, "b": dt_to, "u": filter_username},
            ).fetchall()
        else:
            rows = conn.execute(
                text(
                    """
                    SELECT created_at, username, role, action, entity_type, entity_id
                    FROM public.activity_log
                    WHERE created_at >= :a AND created_at <= :b
                    ORDER BY created_at DESC
                    LIMIT 4000
                    """
                ),
                {"a": dt_from, "b": dt_to},
            ).fetchall()

    title = f"{subject_prefix} ({fmt_window_title(dt_from, dt_to)})"

    # Resumen por usuario (más usable que un listado plano).
    per_user: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        u = (r[1] or "?").strip() or "?"
        role = (r[2] or "").strip()
        key = u.lower()
        if key not in per_user:
            per_user[key] = {"username": u, "role": role, "n": 0, "actions": {}}
        it = per_user[key]
        it["n"] = int(it["n"]) + 1
        act = (r[3] or "").strip() or "?"
        it["actions"][act] = int(it["actions"].get(act, 0)) + 1

    users_sorted = sorted(per_user.values(), key=lambda x: (-int(x.get("n", 0)), str(x.get("username", ""))))

    lines: List[str] = [title, ""]
    # HTML (para que sea legible en celular).
    html_rows: List[str] = []
    if not rows:
        lines.append("Sin actividad en el periodo.")
        html_body = f"<p><b>{_html_escape(title)}</b></p><p>Sin actividad en el periodo.</p>"
    else:
        lines.append("RESUMEN POR USUARIO")
        lines.append("-------------------")
        html_rows.append(
            "<tr>"
            "<th style='text-align:left;padding:6px 8px;border-bottom:1px solid #ddd'>Usuario</th>"
            "<th style='text-align:left;padding:6px 8px;border-bottom:1px solid #ddd'>Rol</th>"
            "<th style='text-align:right;padding:6px 8px;border-bottom:1px solid #ddd'>Acciones</th>"
            "<th style='text-align:left;padding:6px 8px;border-bottom:1px solid #ddd'>Top</th>"
            "</tr>"
        )
        for u in users_sorted[:60]:
            acts = sorted((u.get("actions") or {}).items(), key=lambda t: (-int(t[1]), str(t[0])))[:3]
            acts_txt = ", ".join([f"{a}:{n}" for a, n in acts]) if acts else "-"
            role_txt = f" ({u.get('role')})" if u.get("role") else ""
            lines.append(f"- {u.get('username')}{role_txt}: {u.get('n')} acciones · {acts_txt}")
            html_rows.append(
                "<tr>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #eee'>{_html_escape(str(u.get('username') or ''))}</td>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #eee'>{_html_escape(str(u.get('role') or ''))}</td>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #eee;text-align:right'>{int(u.get('n') or 0)}</td>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #eee'>{_html_escape(acts_txt)}</td>"
                "</tr>"
            )

        lines.append("")
        lines.append("DETALLE (últimos movimientos)")
        lines.append("-----------------------------")
        detail_lines: List[str] = []
        for r in rows[:800]:
            ts = r[0].strftime("%Y-%m-%d %H:%M")
            u = r[1] or "?"
            role = r[2] or ""
            action = r[3] or ""
            et = r[4] or ""
            eid = r[5] or ""
            ln = f"- {ts} · {u} ({role}) · {action} {et}:{eid}".strip()
            lines.append(ln)
            detail_lines.append(ln)

        html_body = f"""
<div style="font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Arial; color:#0f172a">
  <div style="max-width:920px;margin:0 auto;padding:12px 10px">
    <div style="font-weight:900;font-size:16px;margin:0 0 8px 0">{_html_escape(title)}</div>
    <div style="opacity:.75;font-weight:700;font-size:12px;margin-bottom:10px">
      Resumen por usuario + detalle (últimos movimientos).
    </div>

    <div style="border:1px solid #e5e7eb;border-radius:14px;overflow:hidden;background:#ffffff">
      <div style="padding:10px 12px;background:#f8fafc;border-bottom:1px solid #e5e7eb;font-weight:900">
        Resumen por usuario
      </div>
      <div style="padding:0">
        <table style="border-collapse:collapse;width:100%;font-size:13px">
          <thead style="background:#f8fafc">
            {html_rows[0] if html_rows else ""}
          </thead>
          <tbody>
            {''.join(html_rows[1:]) if len(html_rows) > 1 else ''}
          </tbody>
        </table>
      </div>
    </div>

    <div style="height:12px"></div>

    <div style="border:1px solid #e5e7eb;border-radius:14px;overflow:hidden;background:#ffffff">
      <div style="padding:10px 12px;background:#f8fafc;border-bottom:1px solid #e5e7eb;font-weight:900">
        Detalle (hasta 250)
      </div>
      <pre style="margin:0;white-space:pre-wrap;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;
background:#fbfbfb;padding:10px 12px;line-height:1.35">{_html_escape('\n'.join(detail_lines[:250]))}</pre>
    </div>
  </div>
</div>
""".strip()

    body = "\n".join(lines).strip() + "\n"
    return title, body, html_body


def _send_digest_window(
    dt_from: datetime | None,
    dt_to: datetime | None,
    subject_prefix: str = "CRM · Digest actividad",
    filter_username: str = "",
) -> Dict[str, Any]:
    """
    Envío best-effort de digest de activity_log, sin depender de auth.
    Usado por:
    - cron-safe endpoint (/activity/digest_cron)
    - logout PM (requisito del cliente)
    """
    title, body, html_body = _build_digest_window(
        dt_from, dt_to, subject_prefix=subject_prefix, filter_username=filter_username
    )

    to = _suppress_recipients(_parse_recipients() or _recipients_from_db())
    if not to:
        return {"ok": False, "sent": 0, "reason": "no_recipients"}

    # Enviamos en UN solo correo (To + Cc) para que quede un hilo común y reducir conexiones SMTP.
    try:
        send_email_group(to, title, body, html=html_body)
        return {"ok": True, "sent": len(to), "failed": 0, "errors": []}
    except Exception as e:
        # Fallback: intentamos individual (best-effort)
        sent = 0
        errors: List[str] = [f"group: {type(e).__name__}: {e}"]
        for addr in to:
            try:
                send_email(addr, title, body, html=html_body)
                sent += 1
            except Exception as ee:
                errors.append(f"{addr}: {type(ee).__name__}: {ee}")
        return {"ok": sent > 0, "sent": sent, "failed": (len(to) - sent), "errors": errors[:6]}


@router.get("/digest")
def activity_digest(
    send: int = Query(0, ge=0, le=1),
    hours: int = Query(6, ge=1, le=48),
    user: dict = Depends(get_current_user),
):
    """
    Digest de actividad (BD) para admins.
    Se usa desde cron: 14:30 y 18:00.
    """
    _require_admin(user)

    now = datetime.now(timezone.utc)
    dt_from = now - timedelta(hours=int(hours))

    if int(send or 0) == 1:
        # usa el mismo formateo que cron/logout
        r = _send_digest_window(dt_from, now, subject_prefix="CRM · Digest actividad", filter_username="")
        if not r.get("ok"):
            raise HTTPException(400, f"No se pudo enviar digest: {r.get('reason') or r.get('error') or 'unknown'}")
        return {"ok": True, "sent": int(r.get("sent") or 0), "hours": int(hours)}

    title, body, _html = _build_digest_window(dt_from, now, subject_prefix="CRM · Digest actividad", filter_username="")
    return {"ok": True, "hours": int(hours), "preview": body[:8000]}


@router.get("/digest_cron", include_in_schema=False)
def activity_digest_cron(
    secret: str = Query(""),
    hours: int = Query(6, ge=1, le=48),
):
    """
    Endpoint para cron (SIN token), protegido por secreto.
    Cron recomendado: 14:30 y 18:00.
    """
    expected = (os.getenv("ADMIN_DIGEST_SECRET") or "").strip()
    if not expected or secret != expected:
        raise HTTPException(403, "Forbidden")
    now = datetime.now(timezone.utc)
    dt_from = now - timedelta(hours=int(hours))
    try:
        return _send_digest_window(dt_from, now, subject_prefix="CRM · Digest actividad", filter_username="")
    except Exception as e:
        # Cron no debería “tumbar” el CRM por un error de correo.
        return {"ok": False, "sent": 0, "error": f"{type(e).__name__}: {e}"}
