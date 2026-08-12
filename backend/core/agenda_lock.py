from __future__ import annotations


AGENDA_LOCK_BASE = 26_073_000


def agenda_lock_key(id_lead: int) -> int:
    """Return the per-lead PostgreSQL advisory lock used by every agenda path."""
    return AGENDA_LOCK_BASE + (int(id_lead) % 999_999)
