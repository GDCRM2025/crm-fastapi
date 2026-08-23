from __future__ import annotations

from datetime import date
from typing import Protocol

from .exceptions import SIINotSupportedError
from .schemas import ReceivedDocument


class ReceivedDocumentProvider(Protocol):
    def fetch_documents(self, legal_entity_id: int, start_date: date, end_date: date) -> list[ReceivedDocument]: ...


class OfficialRCVProvider:
    def fetch_documents(self, legal_entity_id: int, start_date: date, end_date: date) -> list[ReceivedDocument]:
        raise SIINotSupportedError("El SII no expone un Web Service publico documentado para esta operacion.")


class DTEMailProvider(Protocol):
    def fetch_new_dte_attachments(self) -> list[bytes]: ...
