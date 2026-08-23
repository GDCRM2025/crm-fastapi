from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class SIIToken:
    value: str
    obtained_at: datetime
    expires_at: datetime | None = None


@dataclass
class IssuerData:
    rut: str
    legal_name: str | None = None
    business_activity: str | None = None
    activity_code: str | None = None
    address: str | None = None
    commune: str | None = None
    city: str | None = None


@dataclass
class DocumentLine:
    line_number: int
    item_code: str | None = None
    description: str | None = None
    quantity: Decimal | None = None
    unit: str | None = None
    unit_price: Decimal | None = None
    discount_amount: Decimal = Decimal("0")
    surcharge_amount: Decimal = Decimal("0")
    line_net_amount: Decimal = Decimal("0")


@dataclass
class DocumentReference:
    reference_line: int
    document_type: str | None = None
    folio: str | None = None
    reference_date: date | None = None
    code: str | None = None
    reason: str | None = None


@dataclass
class ReceivedDocument:
    receiver_rut: str
    issuer: IssuerData
    document_type: str
    folio: str
    issue_date: date
    reception_date: date | None = None
    due_date: date | None = None
    payment_method: str | None = None
    net_amount: Decimal = Decimal("0")
    exempt_amount: Decimal = Decimal("0")
    vat_amount: Decimal = Decimal("0")
    vat_non_recoverable: Decimal = Decimal("0")
    other_tax_amount: Decimal = Decimal("0")
    total_amount: Decimal = Decimal("0")
    purchase_type: str | None = None
    source: str = "DTE_XML"
    source_external_id: str | None = None
    xml_sha256: str | None = None
    raw_xml: bytes | None = field(default=None, repr=False)
    lines: list[DocumentLine] = field(default_factory=list)
    references: list[DocumentReference] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
