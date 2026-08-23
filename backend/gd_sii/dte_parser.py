from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal, InvalidOperation

from defusedxml.lxml import fromstring

from .exceptions import SIIDTEUnsupported, SIIXMLInvalid
from .rut import normalize_chilean_rut
from .schemas import DocumentLine, DocumentReference, IssuerData, ReceivedDocument

SUPPORTED_DTE = {"33", "34", "56", "61"}
DEFAULT_MAX_XML_BYTES = 10 * 1024 * 1024


def _text(node, name: str) -> str | None:
    found = node.xpath(f".//*[local-name()='{name}'][1]")
    if not found or found[0].text is None:
        return None
    return found[0].text.strip() or None


def _decimal(value: str | None) -> Decimal:
    if not value:
        return Decimal("0")
    try:
        return Decimal(value.replace(",", "."))
    except InvalidOperation as exc:
        raise SIIXMLInvalid("Monto invalido en DTE") from exc


def _date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise SIIXMLInvalid("Fecha invalida en DTE") from exc


def parse_dte_xml(xml_bytes: bytes, *, max_bytes: int = DEFAULT_MAX_XML_BYTES) -> ReceivedDocument:
    if not xml_bytes or len(xml_bytes) > max_bytes:
        raise SIIXMLInvalid("XML vacio o excede el maximo permitido")
    upper_head = xml_bytes[:4096].upper()
    if b"<!DOCTYPE" in upper_head or b"<!ENTITY" in upper_head:
        raise SIIXMLInvalid("DTD y entidades externas no estan permitidas")
    try:
        root = fromstring(xml_bytes, forbid_dtd=True, forbid_entities=True)
    except Exception as exc:
        raise SIIXMLInvalid("XML DTE invalido") from exc

    document_nodes = root.xpath(".//*[local-name()='Documento'][1] | self::*[local-name()='Documento']")
    document = document_nodes[0] if document_nodes else root
    doc_type = _text(document, "TipoDTE")
    folio = _text(document, "Folio")
    issue_date = _date(_text(document, "FchEmis"))
    issuer_rut = _text(document, "RUTEmisor")
    receiver_rut = _text(document, "RUTRecep")
    if doc_type not in SUPPORTED_DTE:
        raise SIIDTEUnsupported("Tipo DTE no soportado")
    if not (folio and issue_date and issuer_rut and receiver_rut):
        raise SIIXMLInvalid("DTE sin identificadores obligatorios")

    issuer = IssuerData(
        rut=normalize_chilean_rut(issuer_rut),
        legal_name=_text(document, "RznSoc"),
        business_activity=_text(document, "GiroEmis"),
        activity_code=_text(document, "Acteco"),
        address=_text(document, "DirOrigen"),
        commune=_text(document, "CmnaOrigen"),
        city=_text(document, "CiudadOrigen"),
    )
    lines: list[DocumentLine] = []
    for i, item in enumerate(document.xpath(".//*[local-name()='Detalle']"), 1):
        codes = item.xpath(".//*[local-name()='CdgItem']/*[local-name()='VlrCodigo'][1]")
        name = _text(item, "NmbItem")
        description = _text(item, "DscItem")
        lines.append(DocumentLine(
            line_number=int(_text(item, "NroLinDet") or i),
            item_code=(codes[0].text.strip() if codes and codes[0].text else None),
            description=" - ".join(x for x in (name, description) if x) or None,
            quantity=_decimal(_text(item, "QtyItem")) if _text(item, "QtyItem") else None,
            unit=_text(item, "UnmdItem"),
            unit_price=_decimal(_text(item, "PrcItem")) if _text(item, "PrcItem") else None,
            discount_amount=_decimal(_text(item, "DescuentoMonto")),
            surcharge_amount=_decimal(_text(item, "RecargoMonto")),
            line_net_amount=_decimal(_text(item, "MontoItem")),
        ))
    references: list[DocumentReference] = []
    for i, ref in enumerate(document.xpath(".//*[local-name()='Referencia']"), 1):
        references.append(DocumentReference(
            reference_line=int(_text(ref, "NroLinRef") or i),
            document_type=_text(ref, "TpoDocRef"), folio=_text(ref, "FolioRef"),
            reference_date=_date(_text(ref, "FchRef")), code=_text(ref, "CodRef"),
            reason=_text(ref, "RazonRef"),
        ))
    return ReceivedDocument(
        receiver_rut=normalize_chilean_rut(receiver_rut), issuer=issuer,
        document_type=doc_type, folio=folio, issue_date=issue_date,
        due_date=_date(_text(document, "FchVenc")), payment_method=_text(document, "FmaPago"),
        net_amount=_decimal(_text(document, "MntNeto")),
        exempt_amount=_decimal(_text(document, "MntExe")),
        vat_amount=_decimal(_text(document, "IVA")), total_amount=_decimal(_text(document, "MntTotal")),
        xml_sha256=hashlib.sha256(xml_bytes).hexdigest(), raw_xml=xml_bytes,
        lines=lines, references=references,
    )
