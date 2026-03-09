from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Optional

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
              meta JSONB NOT NULL DEFAULT '{}'::jsonb
            )
            """
        )
    )


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
) -> None:
    """
    Log en BD (no en disco).
    """
    ensure_activity_log(conn)
    conn.execute(
        text(
            """
            INSERT INTO public.activity_log(username,user_id,role,action,entity_type,entity_id,meta,created_at)
            VALUES (:u,:uid,:r,:a,:et,:eid, CAST(:m AS JSONB), now())
            """
        ),
        {
            "u": (username or "").strip() or None,
            "uid": int(user_id) if user_id is not None else None,
            "r": (role or "").strip() or None,
            "a": str(action),
            "et": (entity_type or "").strip() or None,
            "eid": int(entity_id) if entity_id is not None else None,
            "m": json.dumps(meta or {}, ensure_ascii=False),
        },
    )


def fmt_window_title(dt_from: datetime, dt_to: datetime) -> str:
    return f"{dt_from.strftime('%Y-%m-%d %H:%M')} -> {dt_to.strftime('%Y-%m-%d %H:%M')}"

