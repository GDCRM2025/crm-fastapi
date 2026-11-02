from __future__ import annotations

import hmac
import os
from datetime import date
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import text

from backend.core.db import get_connection

router = APIRouter(prefix="/api/atlas/finance", tags=["atlas-finance-readonly"])

def require_atlas_service(authorization: str | None = Header(default=None)) -> None:
    expected = str(os.getenv("ATLAS_FINANCE_SERVICE_TOKEN") or "").strip()
    if len(expected) < 32:
        raise HTTPException(503, "ATLAS service token is not configured")
    supplied = str(authorization or "")
    if not supplied.lower().startswith("bearer "):
        raise HTTPException(401, "Bearer token required")
    token = supplied.split(" ", 1)[1].strip()
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(401, "Invalid service token")

def _table_exists(conn, name: str) -> bool:
    return bool(conn.execute(text("""
      SELECT EXISTS(
        SELECT 1 FROM information_schema.tables
        WHERE table_schema='public' AND table_name=:name
      )
    """), {"name": name}).scalar())

def _rows(conn, sql: str, params: dict | None = None) -> list[dict]:
    return [dict(row) for row in conn.execute(text(sql), params or {}).mappings().all()]

@router.get("/sourceHealth", dependencies=[Depends(require_atlas_service)])
def source_health():
    required = ["fin_eventos", "fin_pagos", "fin_gastos", "fin_saldos", "fin_legal_entities", "wi_sii_connections", "fin_sii_received_documents", "fin_bank_movements", "bank_movements", "fin_movimientos_bancarios", "cartola_movimientos"]
    with get_connection() as conn:
        tables = {name: _table_exists(conn, name) for name in required}
        entities = _rows(conn, """
          SELECT id_legal_entity, legal_code, legal_name, rut, is_active
          FROM fin_legal_entities WHERE is_active IS TRUE ORDER BY legal_name
        """) if tables["fin_legal_entities"] else []
    return {"data": [
      {"source":"CRM_GREEN_DIAMOND","table":name,"status":"OK" if exists else "MISSING"}
      for name, exists in tables.items()
    ] + [{"source":"CRM_GREEN_DIAMOND","kind":"legal_entity","id":str(x["id_legal_entity"]),"legalCode":x["legal_code"],"legalName":x["legal_name"],"rut":x["rut"],"status":"ACTIVE"} for x in entities]}

@router.get("/sales", dependencies=[Depends(require_atlas_service)])
def sales(from_date: date = Query(default=date(2000,1,1), alias="from"), to_date: date | None = Query(default=None, alias="to")):
    to_date = to_date or date.today()
    if to_date < from_date:
        raise HTTPException(400, "Invalid date range")
    with get_connection() as conn:
        if not _table_exists(conn, "fin_eventos"):
            raise HTTPException(503, "CRM finance events table unavailable")
        rows = _rows(conn, """
          SELECT id_evento, id_lead, num_cotizacion, cliente, comuna, marca, tipo_cliente,
                 fecha_evento, monto_bruto, monto_neto, iva, traslado, abono, saldo,
                 factura_tipo_doc, factura_num, factura_fecha, factura_rut,
                 factura_razon_social, ejecutivo, created_at, updated_at
          FROM fin_eventos
          WHERE fecha_evento >= :from_date AND fecha_evento <= :to_date
          ORDER BY fecha_evento, id_evento
        """, {"from_date":from_date,"to_date":to_date})
    return {"data":rows}

@router.get("/receivables", dependencies=[Depends(require_atlas_service)])
def receivables(as_of: date | None = Query(default=None)):
    with get_connection() as conn:
        if not _table_exists(conn, "fin_eventos"):
            raise HTTPException(503, "CRM finance events table unavailable")
        rows = _rows(conn, """
          SELECT id_evento, id_lead, cliente, marca, tipo_cliente, fecha_evento,
                 monto_bruto, monto_neto, iva, traslado, abono, saldo,
                 factura_num, factura_fecha, factura_rut
          FROM fin_eventos
          WHERE COALESCE(saldo,0) > 0 AND (:as_of IS NULL OR fecha_evento <= :as_of)
          ORDER BY fecha_evento, cliente
        """, {"as_of":as_of})
    return {"data":rows}

@router.get("/payables", dependencies=[Depends(require_atlas_service)])
def payables(from_date: date | None = Query(default=None), to_date: date | None = Query(default=None)):
    with get_connection() as conn:
        if not _table_exists(conn, "fin_sii_received_documents"):
            raise HTTPException(503, "CRM received DTE table unavailable")
        rows = _rows(conn, """
          SELECT d.id, d.id_legal_entity, d.supplier_id, d.issuer_rut, d.issuer_name,
                 d.receiver_rut, d.document_type, d.folio, d.issue_date, d.due_date,
                 d.net_amount, d.exempt_amount, d.vat_amount, d.total_amount,
                 d.financial_status, d.accounts_payable_id, d.source, d.xml_sha256,
                 p.razon_social AS supplier_legal_name, p.nombre AS supplier_name,
                 g.balance AS payable_balance, g.payable_status,
                 g.pagado AS marked_paid, g.fecha_pago AS recorded_payment_date
          FROM fin_sii_received_documents d
          LEFT JOIN inv_proveedores p ON p.id_proveedor=d.supplier_id
          LEFT JOIN fin_gastos g ON g.id_gasto=d.accounts_payable_id
          WHERE (:from_date IS NULL OR d.issue_date >= :from_date)
            AND (:to_date IS NULL OR d.issue_date <= :to_date)
          ORDER BY d.issue_date, d.issuer_name, d.folio
        """, {"from_date":from_date,"to_date":to_date})
    return {"data":rows}

@router.get("/issuedDocuments", dependencies=[Depends(require_atlas_service)])
def issued_documents(from_date: date | None = Query(default=None), to_date: date | None = Query(default=None)):
    with get_connection() as conn:
        if not _table_exists(conn, "fin_eventos"):
            raise HTTPException(503, "CRM finance events table unavailable")
        rows = _rows(conn, """
          SELECT id_evento, id_lead, cliente, marca, fecha_evento, monto_bruto,
                 monto_neto, iva, factura_tipo_doc, factura_num, factura_fecha,
                 factura_rut, factura_razon_social, factura_glosa, requiere_documento
          FROM fin_eventos
          WHERE factura_num IS NOT NULL AND btrim(factura_num) <> ''
            AND (:from_date IS NULL OR COALESCE(factura_fecha,fecha_evento) >= :from_date)
            AND (:to_date IS NULL OR COALESCE(factura_fecha,fecha_evento) <= :to_date)
          ORDER BY COALESCE(factura_fecha,fecha_evento), factura_num
        """, {"from_date":from_date,"to_date":to_date})
    return {"data":rows}

@router.get("/bankTransactions", dependencies=[Depends(require_atlas_service)])
def bank_transactions():
    with get_connection() as conn:
        candidates = ["fin_bank_movements","bank_movements","fin_movimientos_bancarios","cartola_movimientos"]
        found = next((name for name in candidates if _table_exists(conn,name)), None)
        if not found:
            raise HTTPException(503, "CRM bank movement source table not identified; no empty result returned")
        rows = _rows(conn, f"SELECT * FROM {found} ORDER BY 1 DESC LIMIT 10000")
    return {"data":rows}
