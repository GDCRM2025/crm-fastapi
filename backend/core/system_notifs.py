from __future__ import annotations

from typing import Any

from sqlalchemy import text


def ensure_system_notifs(conn) -> None:
    """
    Notificaciones internas por rol (Operaciones/Compras/Bodega/MICE/Admin).
    Best-effort: si el entorno no permite DDL, no debe romper flujos.
    """
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
    except Exception:
        # No rompas el flujo por DDL; deja que el caller maneje transacciones.
        pass


def push_system_notif(
    conn,
    *,
    kind: str,
    role_target: str,
    id_lead: int | None,
    title: str,
    body: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """
    Upsert idempotente (por kind+role_target+id_lead). Si ya existía, lo "revive"
    y lo marca como no leído.
    """
    payload = payload or {}
    ensure_system_notifs(conn)
    try:
        conn.execute(
            text(
                """
                INSERT INTO public.system_notifs(kind, role_target, id_lead, title, body, payload)
                VALUES (:k,:r,:lid,:t,:b, CAST(:p AS jsonb))
                ON CONFLICT (kind, role_target, id_lead)
                DO UPDATE SET
                  created_at = now(),
                  title = EXCLUDED.title,
                  body = EXCLUDED.body,
                  payload = EXCLUDED.payload,
                  read_at = NULL,
                  read_by = NULL
                """
            ),
            {
                "k": (kind or "")[:80],
                "r": (role_target or "")[:80],
                "lid": int(id_lead) if id_lead else None,
                "t": (title or "")[:200],
                "b": (body or "")[:1200],
                "p": __import__("json").dumps(payload, ensure_ascii=False),
            },
        )
    except Exception:
        # Best-effort: si falla (ej: tabla no existe / permisos), no bloquea.
        pass
