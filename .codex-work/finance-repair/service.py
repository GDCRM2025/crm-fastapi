from __future__ import annotations

from collections.abc import Iterable
import json
from typing import Any

from sqlalchemy import text

from backend.core.activity_log import log_activity
from backend.core.db import get_connection

from .dte_parser import parse_dte_xml
from .email_provider import GIAEmailDTEProvider
from .exceptions import RCVEntityConflict, ReceiverRUTMismatch, SIIError
from .payable_sync import PayableSyncService
from .rcv_parser import parse_rcv_csv
from .repository import SIIRepository
from .schemas import ReceivedDocument
from .storage import PrivateXMLStorage
from .supplier_sync import SupplierSyncService


class SIIReceivedDocumentsService:
    def __init__(self, *, repository: SIIRepository | None = None, storage: PrivateXMLStorage | None = None) -> None:
        self.repository = repository or SIIRepository()
        self.storage = storage or PrivateXMLStorage()
        self.suppliers = SupplierSyncService()
        self.payables = PayableSyncService()

    def import_dte_xml(self, legal_entity_id: int, xml_bytes: bytes, *, actor: dict[str, Any]) -> dict[str, Any]:
        return self.import_documents(legal_entity_id, [parse_dte_xml(xml_bytes)], actor=actor, source="DTE_XML")

    def import_rcv_csv(self, legal_entity_id: int, csv_bytes: bytes, *, actor: dict[str, Any]) -> dict[str, Any]:
        with get_connection() as conn:
            entity = self.repository.legal_entity(conn, legal_entity_id)
            if not entity:
                raise ValueError("Entidad legal no encontrada")
            receiver_rut = entity["rut_normalized"]
        documents = list(parse_rcv_csv(csv_bytes, receiver_rut=receiver_rut))
        self._reject_cross_entity_rcv(legal_entity_id, documents)
        return self.import_documents(legal_entity_id, documents, actor=actor, source="SII_RCV_CSV")

    def _reject_cross_entity_rcv(self, legal_entity_id: int, documents: list[ReceivedDocument]) -> None:
        """Reject an RCV whose exact document fingerprints already belong elsewhere.

        Chilean RCV CSV exports do not include the receiver RUT, so assigning the
        selected entity as receiver is not enough validation. An exact match on
        issuer, DTE type, folio, issue date and total in another legal entity is
        a strong signal that the wrong company was selected during upload.
        """
        if not documents:
            return
        fingerprints = []
        for document in documents:
            issuer_body = str(document.issuer.rut or "").split("-", 1)[0]
            fingerprints.append(
                {
                    "issuer_rut": issuer_body,
                    "document_type": str(document.document_type),
                    "folio": str(document.folio),
                    "issue_date": document.issue_date.isoformat(),
                    "total_amount": str(document.total_amount),
                }
            )
        with get_connection() as conn:
            rows = conn.execute(
                text(
                    """
                    WITH incoming AS (
                      SELECT *
                      FROM jsonb_to_recordset(CAST(:items AS jsonb)) AS x(
                        issuer_rut text,
                        document_type text,
                        folio text,
                        issue_date date,
                        total_amount numeric
                      )
                    )
                    SELECT d.id_legal_entity,
                           e.legal_name,
                           e.rut,
                           count(*)::integer AS matches
                    FROM incoming i
                    JOIN fin_sii_received_documents d
                      ON d.issuer_rut=i.issuer_rut
                     AND d.document_type=i.document_type
                     AND d.folio=i.folio
                     AND d.issue_date=i.issue_date
                     AND d.total_amount=i.total_amount
                     AND d.id_legal_entity<>:entity
                    JOIN fin_legal_entities e
                      ON e.id_legal_entity=d.id_legal_entity
                    GROUP BY d.id_legal_entity,e.legal_name,e.rut
                    ORDER BY matches DESC,e.legal_name
                    """
                ),
                {"items": json.dumps(fingerprints), "entity": legal_entity_id},
            ).mappings().all()
        if rows:
            conflict = rows[0]
            raise RCVEntityConflict(
                "Carga detenida: "
                f"{conflict['matches']} documento(s) del RCV ya existen en "
                f"{conflict['legal_name']} ({conflict['rut']}). "
                "Revisa la empresa seleccionada antes de volver a cargar."
            )

    def import_email_dte(self, *, actor: dict[str, Any], provider: GIAEmailDTEProvider | None = None) -> dict[str, Any]:
        provider = provider or GIAEmailDTEProvider()
        attachments = provider.fetch_new_dte_attachments()
        parsed: list[ReceivedDocument] = []
        errors: list[dict[str, str]] = []
        for attachment in attachments:
            try:
                document = parse_dte_xml(attachment.xml_bytes)
                document.source = "EMAIL"
                document.source_external_id = attachment.external_id
                document.metadata = attachment.metadata
                parsed.append(document)
            except SIIError as exc:
                errors.append({"external_id": attachment.external_id, "code": exc.code, "message": exc.safe_message})
        with get_connection() as conn:
            entities = self.repository.legal_entities(conn)
        entity_by_rut = {item["rut_normalized"]: int(item["id_legal_entity"]) for item in entities}
        grouped: dict[int, list[ReceivedDocument]] = {}
        for document in parsed:
            entity_id = entity_by_rut.get(document.receiver_rut)
            if entity_id is None:
                errors.append({"external_id": document.source_external_id or "", "code": "RECEIVER_RUT_MISMATCH", "message": "No existe entidad legal para el RUT receptor"})
                continue
            grouped.setdefault(entity_id, []).append(document)
        results = [self.import_documents(entity_id, documents, actor=actor, source="EMAIL") for entity_id, documents in grouped.items()]
        return {"status": "SUCCESS" if not errors else "PARTIAL", "attachments_seen": len(attachments),
                "documents_parsed": len(parsed), "entities_processed": len(grouped), "results": results, "errors": errors}

    def import_documents(self, legal_entity_id: int, documents: Iterable[ReceivedDocument], *, actor: dict[str, Any], source: str) -> dict[str, Any]:
        docs = list(documents)
        metrics = {"records_seen": len(docs), "records_inserted": 0, "records_updated": 0,
                   "records_skipped": 0, "records_failed": 0, "payables_created": 0, "payables_updated": 0}
        errors: list[dict[str, str]] = []
        with get_connection() as conn:
            with conn.begin():
                entity = self.repository.legal_entity(conn, legal_entity_id)
                if not entity:
                    raise ValueError("Entidad legal no encontrada")
                expected_rut = entity["rut_normalized"]
                for document in docs:
                    if document.receiver_rut != expected_rut:
                        raise ReceiverRUTMismatch("El RUT receptor no corresponde a la entidad legal seleccionada")
                supplier_map, supplier_metrics, supplier_events = self.suppliers.sync_suppliers_batch(conn, (d.issuer for d in docs), source=source)
                for event in supplier_events:
                    log_activity(conn, username=actor.get("username") or actor.get("name"), user_id=actor.get("id"),
                                 role=actor.get("role"), action=event["action"], entity_type="inv_proveedor",
                                 entity_id=event["supplier_id"],
                                 meta={**event, "source": source, "legal_entity_id": legal_entity_id})
                run_id = conn.execute(text("""
                  INSERT INTO wi_sii_sync_runs(id_legal_entity,source,status,records_seen,triggered_by)
                  VALUES (:entity,:source,'RUNNING',:seen,:actor) RETURNING id
                """), {"entity": legal_entity_id, "source": source, "seen": len(docs), "actor": actor.get("id")}).scalar_one()
                for document in docs:
                    try:
                        with conn.begin_nested():
                            storage_key = None
                            if document.raw_xml and document.xml_sha256:
                                storage_key = self.storage.put(legal_entity_id, document.xml_sha256, document.raw_xml)
                            document_id, inserted = self.repository.upsert_document(
                                conn, legal_entity_id=legal_entity_id, supplier_id=supplier_map[document.issuer.rut],
                                document=document, storage_key=storage_key,
                            )
                            payable_id, payable_inserted = self.payables.sync(
                                conn, legal_entity_id=legal_entity_id, supplier_id=supplier_map[document.issuer.rut],
                                document_id=document_id, document=document,
                            )
                            metrics["records_inserted" if inserted else "records_updated"] += 1
                            metrics["payables_created" if payable_inserted else "payables_updated"] += 1
                            log_activity(conn, username=actor.get("username") or actor.get("name"), user_id=actor.get("id"),
                                         role=actor.get("role"), action="PAYABLE_CREATED_FROM_SII" if payable_inserted else "PAYABLE_UPDATED_FROM_SII",
                                         entity_type="fin_gasto", entity_id=payable_id,
                                         meta={"document_id": document_id, "supplier_id": supplier_map[document.issuer.rut],
                                               "legal_entity_id": legal_entity_id, "source": source})
                            log_activity(conn, username=actor.get("username") or actor.get("name"), user_id=actor.get("id"),
                                         role=actor.get("role"), action="SII_DOCUMENT_CREATED" if inserted else "SII_DOCUMENT_UPDATED",
                                         entity_type="fin_sii_received_document", entity_id=document_id,
                                         meta={"source": source, "document_type": document.document_type, "folio": document.folio,
                                               "supplier_id": supplier_map[document.issuer.rut], "legal_entity_id": legal_entity_id})
                    except Exception as exc:
                        metrics["records_failed"] += 1
                        errors.append({"folio": document.folio, "code": getattr(exc, "code", "SII_IMPORT_ERROR"),
                                       "message": getattr(exc, "safe_message", "No fue posible importar el documento")})
                status = "SUCCESS" if not errors else "PARTIAL" if metrics["records_inserted"] + metrics["records_updated"] else "ERROR"
                conn.execute(text("""
                  UPDATE wi_sii_sync_runs SET finished_at=now(),status=:status,records_inserted=:records_inserted,
                    records_updated=:records_updated,records_skipped=:records_skipped,records_failed=:records_failed,
                    suppliers_seen=:suppliers_seen,suppliers_created=:suppliers_created,suppliers_updated=:suppliers_updated,
                    suppliers_unchanged=:suppliers_unchanged,payables_created=:payables_created,payables_updated=:payables_updated,
                    error_code=:error_code,error_safe=:error_safe WHERE id=:id
                """), {**metrics, "id": run_id, "status": status,
                         "suppliers_seen": supplier_metrics["seen"], "suppliers_created": supplier_metrics["created"],
                         "suppliers_updated": supplier_metrics["updated"], "suppliers_unchanged": supplier_metrics["unchanged"],
                         "error_code": errors[0]["code"] if errors else None,
                         "error_safe": errors[0]["message"] if errors else None})
                log_activity(conn, username=actor.get("username") or actor.get("name"), user_id=actor.get("id"), role=actor.get("role"),
                             action="SII_XML_IMPORTED" if source == "DTE_XML" else "SII_RCV_IMPORTED",
                             entity_type="wi_sii_sync_run", entity_id=int(run_id),
                             meta={"legal_entity_id": legal_entity_id, "status": status, **metrics, **{f"suppliers_{k}": v for k, v in supplier_metrics.items()}})
        return {"status": status, "sync_run_id": int(run_id), **metrics, "suppliers": supplier_metrics, "errors": errors}

    def list_documents(self, **kwargs) -> dict[str, Any]:
        with get_connection() as conn:
            return self.repository.list_documents(conn, **kwargs)

    def get_document(self, document_id: int) -> dict[str, Any] | None:
        with get_connection() as conn:
            return self.repository.get_document(conn, document_id)
