from __future__ import annotations

import csv
import io
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

from .exceptions import SIIRCVInvalid
from .rut import normalize_chilean_rut
from .schemas import IssuerData, ReceivedDocument

DEFAULT_MAX_RCV_BYTES = 25 * 1024 * 1024


def _header(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", normalized.lower()).strip("_")


ALIASES = {
    "document_type": ("tipo_doc", "tipo_documento", "tipo_dte"),
    "folio": ("folio", "numero_documento", "nro_documento"),
    "rut": ("rut_proveedor", "rut_emisor", "rut_prov"),
    "name": ("razon_social", "razon_social_proveedor", "nombre_proveedor"),
    "issue_date": ("fecha_documento", "fecha_emision", "fecha_emision_documento"),
    "reception_date": ("fecha_recepcion", "fecha_recepcion_sii"),
    "exempt": ("monto_exento", "exento"),
    "net": ("monto_neto", "neto"),
    "vat": ("iva_recuperable", "iva"),
    "vat_nr": ("iva_no_recuperable", "iva_no_rec"),
    "other_tax": ("otros_impuestos", "impuestos_adicionales"),
    "total": ("monto_total", "total"),
    "purchase_type": ("tipo_compra",),
}


def _find(row: dict[str, str], key: str) -> str | None:
    for alias in ALIASES[key]:
        value = row.get(alias)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _money(value: str | None) -> Decimal:
    if not value:
        return Decimal("0")
    cleaned = re.sub(r"[^0-9,.-]", "", value)
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+", cleaned):
        cleaned = cleaned.replace(".", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return Decimal(cleaned or "0")
    except InvalidOperation as exc:
        raise SIIRCVInvalid("Monto invalido en RCV") from exc


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            from datetime import datetime
            return datetime.strptime(value[:10], fmt).date()
        except ValueError:
            continue
    raise SIIRCVInvalid("Fecha invalida en RCV")


def parse_rcv_csv(data: bytes, *, receiver_rut: str, max_bytes: int = DEFAULT_MAX_RCV_BYTES) -> list[ReceivedDocument]:
    if not data or len(data) > max_bytes:
        raise SIIRCVInvalid("RCV vacio o excede el maximo permitido")
    text = None
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise SIIRCVInvalid("Encoding RCV no soportado")
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    if not reader.fieldnames:
        raise SIIRCVInvalid("RCV sin encabezados")
    documents: list[ReceivedDocument] = []
    for raw in reader:
        row = {_header(k): (v or "").strip() for k, v in raw.items() if k is not None}
        doc_type, folio, rut = _find(row, "document_type"), _find(row, "folio"), _find(row, "rut")
        issue_date = _parse_date(_find(row, "issue_date"))
        if not (doc_type and folio and rut and issue_date):
            raise SIIRCVInvalid("Fila RCV sin identificadores obligatorios")
        documents.append(ReceivedDocument(
            receiver_rut=normalize_chilean_rut(receiver_rut),
            issuer=IssuerData(rut=normalize_chilean_rut(rut), legal_name=_find(row, "name")),
            document_type=doc_type.strip(), folio=folio.strip(), issue_date=issue_date,
            reception_date=_parse_date(_find(row, "reception_date")),
            exempt_amount=_money(_find(row, "exempt")), net_amount=_money(_find(row, "net")),
            vat_amount=_money(_find(row, "vat")), vat_non_recoverable=_money(_find(row, "vat_nr")),
            other_tax_amount=_money(_find(row, "other_tax")), total_amount=_money(_find(row, "total")),
            purchase_type=_find(row, "purchase_type"), source="SII_RCV_CSV",
        ))
    return documents
