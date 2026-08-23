from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from .rut import normalize_chilean_rut
from .schemas import ReceivedDocument


class SIIRepository:
    def legal_entity(self, conn, legal_entity_id: int) -> dict[str, Any] | None:
        row = conn.execute(text("SELECT id_legal_entity, legal_code, legal_name, rut FROM fin_legal_entities WHERE id_legal_entity=:id AND is_active IS TRUE"), {"id": legal_entity_id}).mappings().first()
        if not row:
            return None
        item = dict(row)
        item["name"] = item["legal_name"]
        item["rut_normalized"] = normalize_chilean_rut(item["rut"])
        return item

    def legal_entities(self, conn) -> list[dict[str, Any]]:
        rows = conn.execute(text("SELECT id_legal_entity,legal_code,legal_name,rut FROM fin_legal_entities WHERE is_active IS TRUE ORDER BY legal_name")).mappings()
        return [{**dict(row), "name": row["legal_name"], "rut_normalized": normalize_chilean_rut(row["rut"])} for row in rows]

    def upsert_document(self, conn, *, legal_entity_id: int, supplier_id: int, document: ReceivedDocument, storage_key: str | None) -> tuple[int, bool]:
        body, dv = document.issuer.rut.split("-", 1)
        row = conn.execute(text("""
            INSERT INTO fin_sii_received_documents(
              id_legal_entity,supplier_id,receiver_rut,issuer_rut,issuer_dv,issuer_name,
              document_type,folio,issue_date,reception_date,due_date,payment_method,
              net_amount,exempt_amount,vat_amount,vat_non_recoverable,other_tax_amount,total_amount,
              purchase_type,source,source_external_id,xml_sha256,xml_storage_key,first_seen_at,last_seen_at
            ) VALUES (
              :entity,:supplier,:receiver,:issuer,:dv,:name,:type,:folio,:issue,:reception,:due,:payment,
              :net,:exempt,:vat,:vat_nr,:other_tax,:total,:purchase_type,:source,:external,:sha,:storage,now(),now()
            )
            ON CONFLICT (id_legal_entity,issuer_rut,document_type,folio) DO UPDATE SET
              supplier_id=EXCLUDED.supplier_id, issuer_name=COALESCE(EXCLUDED.issuer_name,fin_sii_received_documents.issuer_name),
              reception_date=COALESCE(EXCLUDED.reception_date,fin_sii_received_documents.reception_date),
              due_date=COALESCE(EXCLUDED.due_date,fin_sii_received_documents.due_date),
              net_amount=EXCLUDED.net_amount, exempt_amount=EXCLUDED.exempt_amount,
              vat_amount=EXCLUDED.vat_amount, vat_non_recoverable=EXCLUDED.vat_non_recoverable,
              other_tax_amount=EXCLUDED.other_tax_amount, total_amount=EXCLUDED.total_amount,
              xml_sha256=COALESCE(EXCLUDED.xml_sha256,fin_sii_received_documents.xml_sha256),
              xml_storage_key=COALESCE(EXCLUDED.xml_storage_key,fin_sii_received_documents.xml_storage_key),
              last_seen_at=now(),updated_at=now()
            RETURNING id,(xmax=0) AS inserted
        """), {
            "entity": legal_entity_id, "supplier": supplier_id, "receiver": document.receiver_rut,
            "issuer": body, "dv": dv, "name": document.issuer.legal_name, "type": document.document_type,
            "folio": document.folio, "issue": document.issue_date, "reception": document.reception_date,
            "due": document.due_date, "payment": document.payment_method, "net": document.net_amount,
            "exempt": document.exempt_amount, "vat": document.vat_amount, "vat_nr": document.vat_non_recoverable,
            "other_tax": document.other_tax_amount, "total": document.total_amount,
            "purchase_type": document.purchase_type, "source": document.source,
            "external": document.source_external_id, "sha": document.xml_sha256, "storage": storage_key,
        }).mappings().one()
        document_id = int(row["id"])
        for line in document.lines:
            conn.execute(text("""
              INSERT INTO fin_sii_received_document_lines(document_id,line_number,item_code,description,quantity,unit,unit_price,discount_amount,surcharge_amount,line_net_amount)
              VALUES (:doc,:line,:code,:description,:quantity,:unit,:price,:discount,:surcharge,:total)
              ON CONFLICT(document_id,line_number) DO UPDATE SET item_code=EXCLUDED.item_code,description=EXCLUDED.description,quantity=EXCLUDED.quantity,unit=EXCLUDED.unit,unit_price=EXCLUDED.unit_price,discount_amount=EXCLUDED.discount_amount,surcharge_amount=EXCLUDED.surcharge_amount,line_net_amount=EXCLUDED.line_net_amount
            """), {"doc": document_id, "line": line.line_number, "code": line.item_code, "description": line.description,
                    "quantity": line.quantity, "unit": line.unit, "price": line.unit_price, "discount": line.discount_amount,
                    "surcharge": line.surcharge_amount, "total": line.line_net_amount})
        for ref in document.references:
            conn.execute(text("""
              INSERT INTO fin_sii_document_references(document_id,reference_line,reference_document_type,reference_folio,reference_date,reference_code,reason)
              VALUES (:doc,:line,:type,:folio,:date,:code,:reason)
              ON CONFLICT(document_id,reference_line) DO UPDATE SET reference_document_type=EXCLUDED.reference_document_type,reference_folio=EXCLUDED.reference_folio,reference_date=EXCLUDED.reference_date,reference_code=EXCLUDED.reference_code,reason=EXCLUDED.reason
            """), {"doc": document_id, "line": ref.reference_line, "type": ref.document_type, "folio": ref.folio,
                    "date": ref.reference_date, "code": ref.code, "reason": ref.reason})
        conn.execute(text("""
          INSERT INTO fin_sii_document_sources(document_id,source,external_id,sha256,metadata_json)
          VALUES (:doc,:source,COALESCE(:external,''),COALESCE(:sha,''),CAST(:metadata AS JSONB))
          ON CONFLICT(document_id,source,external_id,sha256) DO UPDATE SET last_seen_at=now(),metadata_json=EXCLUDED.metadata_json
        """), {"doc": document_id, "source": document.source, "external": document.source_external_id,
                "sha": document.xml_sha256, "metadata": json.dumps(document.metadata, ensure_ascii=False)})
        return document_id, bool(row["inserted"])

    def list_documents(self, conn, *, page: int, page_size: int, filters: dict[str, Any]) -> dict[str, Any]:
        where = ["1=1"]
        params: dict[str, Any] = {"limit": page_size, "offset": (page - 1) * page_size}
        for key, column in (("legal_entity_id","d.id_legal_entity"),("document_type","d.document_type"),("financial_status","d.financial_status"),("source","d.source")):
            if filters.get(key):
                where.append(f"{column}=:{key}"); params[key] = filters[key]
        if filters.get("date_from"): where.append("d.issue_date>=:date_from"); params["date_from"] = filters["date_from"]
        if filters.get("date_to"): where.append("d.issue_date<=:date_to"); params["date_to"] = filters["date_to"]
        if filters.get("q"):
            where.append("(COALESCE(d.issuer_name,'') ILIKE :q OR d.issuer_rut ILIKE :q OR d.folio ILIKE :q)"); params["q"] = f"%{filters['q']}%"
        clause = " AND ".join(where)
        total = conn.execute(text(f"SELECT count(*) FROM fin_sii_received_documents d WHERE {clause}"), params).scalar_one()
        rows = conn.execute(text(f"""
          SELECT d.*, p.razon_social, p.nombre AS supplier_name, p.rut_normalized, g.balance AS payable_balance, g.payable_status
          FROM fin_sii_received_documents d JOIN inv_proveedores p ON p.id_proveedor=d.supplier_id
          LEFT JOIN fin_gastos g ON g.id_gasto=d.accounts_payable_id
          WHERE {clause} ORDER BY d.issue_date DESC,d.id DESC LIMIT :limit OFFSET :offset
        """), params).mappings().all()
        return {"items": [dict(r) for r in rows], "total": int(total), "page": page, "page_size": page_size}

    def get_document(self, conn, document_id: int) -> dict[str, Any] | None:
        row = conn.execute(text("""
          SELECT d.*,p.razon_social,p.nombre AS supplier_name,p.rut_normalized,g.balance AS payable_balance,g.payable_status
          FROM fin_sii_received_documents d JOIN inv_proveedores p ON p.id_proveedor=d.supplier_id
          LEFT JOIN fin_gastos g ON g.id_gasto=d.accounts_payable_id WHERE d.id=:id
        """), {"id": document_id}).mappings().first()
        if not row: return None
        result = dict(row)
        result["lines"] = [dict(r) for r in conn.execute(text("SELECT * FROM fin_sii_received_document_lines WHERE document_id=:id ORDER BY line_number"), {"id": document_id}).mappings()]
        result["references"] = [dict(r) for r in conn.execute(text("SELECT * FROM fin_sii_document_references WHERE document_id=:id ORDER BY reference_line"), {"id": document_id}).mappings()]
        result["sources"] = [dict(r) for r in conn.execute(text("SELECT id,source,external_id,sha256,first_seen_at,last_seen_at,metadata_json FROM fin_sii_document_sources WHERE document_id=:id"), {"id": document_id}).mappings()]
        return result
