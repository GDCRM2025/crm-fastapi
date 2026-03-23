from __future__ import annotations

from datetime import datetime, date
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session


def ensure_event_checklist_table(db: Session) -> None:
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.event_checklists (
              id_check BIGSERIAL PRIMARY KEY,
              id_lead BIGINT NOT NULL,
              event_day DATE NOT NULL,
              user_id INT,
              username TEXT,
              items JSONB NOT NULL DEFAULT '{}'::jsonb,
              notes TEXT,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              UNIQUE(id_lead, event_day, user_id)
            )
            """
        )
    )
    db.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_event_checklists_day_user
            ON public.event_checklists(event_day, user_id)
            """
        )
    )
    db.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_event_checklists_lead_day
            ON public.event_checklists(id_lead, event_day)
            """
        )
    )


def upsert_event_checklist(
    db: Session,
    *,
    id_lead: int,
    event_day: date,
    user_id: Optional[int],
    username: str,
    items: Dict[str, Any],
    notes: str = "",
) -> Dict[str, Any]:
    ensure_event_checklist_table(db)
    row = db.execute(
        text(
            """
            INSERT INTO public.event_checklists(id_lead, event_day, user_id, username, items, notes)
            VALUES (:lid, :d, :uid, :u, CAST(:items AS jsonb), NULLIF(:notes,''))
            ON CONFLICT (id_lead, event_day, user_id)
            DO UPDATE SET
              username = EXCLUDED.username,
              items = EXCLUDED.items,
              notes = EXCLUDED.notes,
              updated_at = now()
            RETURNING id_check, created_at, updated_at
            """
        ),
        {
            "lid": int(id_lead),
            "d": event_day,
            "uid": int(user_id) if user_id is not None else None,
            "u": (username or "")[:200],
            "items": json_dumps(items or {}),
            "notes": (notes or "")[:2000],
        },
    ).mappings().first()

    return {
        "ok": True,
        "id_check": int(row["id_check"]) if row else None,
        "created_at": str(row["created_at"]) if row else None,
        "updated_at": str(row["updated_at"]) if row else None,
    }


def list_event_checklists(
    db: Session,
    *,
    event_day: date,
    user_id: Optional[int],
) -> Dict[int, Dict[str, Any]]:
    ensure_event_checklist_table(db)
    rows = db.execute(
        text(
            """
            SELECT id_lead, items, notes, updated_at
            FROM public.event_checklists
            WHERE event_day=:d
              AND (:uid IS NULL OR user_id=:uid)
            """
        ),
        {"d": event_day, "uid": int(user_id) if user_id is not None else None},
    ).mappings().all()
    out: Dict[int, Dict[str, Any]] = {}
    for r in rows:
        try:
            out[int(r["id_lead"])] = {
                "items": r["items"] or {},
                "notes": r["notes"] or "",
                "updated_at": str(r["updated_at"]) if r.get("updated_at") else "",
            }
        except Exception:
            continue
    return out


def json_dumps(obj: Any) -> str:
    import json

    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return "{}"

