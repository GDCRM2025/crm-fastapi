from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text

from .schemas import ReceivedDocument


class PayableSyncService:
    def sync(self, conn, *, legal_entity_id: int, supplier_id: int, document_id: int, document: ReceivedDocument) -> tuple[int, bool]:
        existing = conn.execute(text("SELECT id_gasto FROM fin_gastos WHERE sii_document_id=:doc"), {"doc": document_id}).scalar()
        sign = Decimal("-1") if document.document_type == "61" else Decimal("1")
        amount = document.total_amount * sign
        parent_id = None
        if document.document_type in {"56", "61"} and document.references:
            ref = next((r for r in document.references if r.folio), None)
            if ref:
                parent_id = conn.execute(text("""
                  SELECT g.id_gasto FROM fin_sii_received_documents d JOIN fin_gastos g ON g.sii_document_id=d.id
                  WHERE d.id_legal_entity=:entity AND d.issuer_rut=:issuer AND d.document_type=:type AND d.folio=:folio LIMIT 1
                """), {"entity": legal_entity_id, "issuer": document.issuer.rut.split('-')[0], "type": ref.document_type, "folio": ref.folio}).scalar()
        params = {"entity": legal_entity_id, "supplier": supplier_id, "doc": document_id, "parent": parent_id,
                  "date": document.issue_date, "due": document.due_date, "amount": amount,
                  "provider": document.issuer.legal_name, "type": document.document_type, "folio": document.folio}
        if existing:
            conn.execute(text("UPDATE fin_gastos SET monto=:amount,amount_original=:amount,balance=CASE WHEN pagado THEN 0 ELSE :amount END,fecha=:date,fecha_vencimiento=:due,updated_at=now() WHERE id_gasto=:id"), {**params, "id": existing})
            payable_id, inserted = int(existing), False
        else:
            payable_id = int(conn.execute(text("""
              INSERT INTO fin_gastos(fecha,monto,descripcion,proveedor,tipo_doc,doc_num,pagado,fecha_vencimiento,is_active,id_legal_entity,supplier_id,sii_document_id,parent_payable_id,amount_original,balance,payable_status)
              VALUES (:date,:amount,'Documento recibido SII',:provider,:type,:folio,FALSE,:due,TRUE,:entity,:supplier,:doc,:parent,:amount,:amount,'PENDING') RETURNING id_gasto
            """), params).scalar_one())
            inserted = True
        if parent_id:
            adjustment = -document.total_amount if document.document_type == "61" else document.total_amount
            conn.execute(text("UPDATE fin_gastos SET balance=GREATEST(0,COALESCE(balance,monto)+:adjustment),payable_status=CASE WHEN GREATEST(0,COALESCE(balance,monto)+:adjustment)=0 THEN 'CREDITED' ELSE payable_status END WHERE id_gasto=:id"), {"adjustment": adjustment, "id": parent_id})
        conn.execute(text("UPDATE fin_sii_received_documents SET accounts_payable_id=:payable,financial_status=CASE WHEN document_type='61' THEN 'CREDITED' ELSE 'PENDING_REVIEW' END WHERE id=:doc"), {"payable": payable_id, "doc": document_id})
        return payable_id, inserted
