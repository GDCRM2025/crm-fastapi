from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import Request
from sqlalchemy import text


def ensure_activity_log(conn) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.activity_log (
              id BIGSERIAL PRIMARY KEY,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              username TEXT,
              user_id BIGINT,
              role TEXT,
              action TEXT NOT NULL,
              entity_type TEXT,
              entity_id BIGINT,
              ip TEXT,
              user_agent TEXT,
              method TEXT,
              path TEXT,
              status_code INTEGER,
              meta JSONB NOT NULL DEFAULT '{}'::jsonb
            )
            """
        )
    )
    for ddl in (
        "ALTER TABLE public.activity_log ADD COLUMN IF NOT EXISTS ip TEXT",
        "ALTER TABLE public.activity_log ADD COLUMN IF NOT EXISTS user_agent TEXT",
        "ALTER TABLE public.activity_log ADD COLUMN IF NOT EXISTS method TEXT",
        "ALTER TABLE public.activity_log ADD COLUMN IF NOT EXISTS path TEXT",
        "ALTER TABLE public.activity_log ADD COLUMN IF NOT EXISTS status_code INTEGER",
    ):
        conn.execute(text(ddl))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_activity_log_created_at ON public.activity_log(created_at DESC)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_activity_log_user_id ON public.activity_log(user_id)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_activity_log_action ON public.activity_log(action)"))


def request_meta(request: Request | None) -> Dict[str, Any]:
    if request is None:
        return {}
    try:
        forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        ip = forwarded or (request.client.host if request.client else "")
    except Exception:
        ip = ""
    return {
        "ip": ip or None,
        "user_agent": (request.headers.get("user-agent") or "")[:500] or None,
        "method": request.method,
        "path": str(request.url.path),
    }


def log_activity(
    conn,
    *,
    username: str | None,
    user_id: int | None,
    role: str | None,
    action: str,
    entity_type: str | None = None,
    entity_id: int | None = None,
    meta: Optional[Dict[str, Any]] = None,
    ip: str | None = None,
    user_agent: str | None = None,
    method: str | None = None,
    path: str | None = None,
    status_code: int | None = None,
    request: Request | None = None,
) -> None:
    """
    Log en BD (no en disco).
    """
    ensure_activity_log(conn)
    if request is not None:
        req = request_meta(request)
        ip = ip or req.get("ip")
        user_agent = user_agent or req.get("user_agent")
        method = method or req.get("method")
        path = path or req.get("path")
    conn.execute(
        text(
            """
            INSERT INTO public.activity_log(
              username,user_id,role,action,entity_type,entity_id,
              ip,user_agent,method,path,status_code,meta,created_at
            )
            VALUES (
              :u,:uid,:r,:a,:et,:eid,
              :ip,:ua,:method,:path,:status_code,CAST(:m AS JSONB),now()
            )
            """
        ),
        {
            "u": (username or "").strip() or None,
            "uid": int(user_id) if user_id is not None else None,
            "r": (role or "").strip() or None,
            "a": str(action),
            "et": (entity_type or "").strip() or None,
            "eid": int(entity_id) if entity_id is not None else None,
            "ip": (ip or "").strip() or None,
            "ua": (user_agent or "").strip()[:500] or None,
            "method": (method or "").strip().upper() or None,
            "path": (path or "").strip()[:500] or None,
            "status_code": int(status_code) if status_code is not None else None,
            "m": json.dumps(meta or {}, ensure_ascii=False),
        },
    )


def fmt_window_title(dt_from: datetime, dt_to: datetime) -> str:
    return f"{dt_from.strftime('%Y-%m-%d %H:%M')} -> {dt_to.strftime('%Y-%m-%d %H:%M')}"
