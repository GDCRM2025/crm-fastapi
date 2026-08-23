from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from .schemas import SIIToken


class SIITokenCache:
    def __init__(self) -> None:
        self._tokens: dict[tuple[int, str], SIIToken] = {}
        self._locks: dict[tuple[int, str], threading.Lock] = {}
        self._guard = threading.Lock()

    def lock_for(self, legal_entity_id: int, environment: str) -> threading.Lock:
        key = (legal_entity_id, environment.upper())
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def get_valid(self, legal_entity_id: int, environment: str) -> SIIToken | None:
        token = self._tokens.get((legal_entity_id, environment.upper()))
        if token is None:
            return None
        cutoff = datetime.now(timezone.utc) + timedelta(minutes=2)
        return token if token.expires_at is None or token.expires_at > cutoff else None

    def put(self, legal_entity_id: int, environment: str, token: SIIToken) -> None:
        self._tokens[(legal_entity_id, environment.upper())] = token

    def invalidate(self, legal_entity_id: int, environment: str) -> None:
        self._tokens.pop((legal_entity_id, environment.upper()), None)
