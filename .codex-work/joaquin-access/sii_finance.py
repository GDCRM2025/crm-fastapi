from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import text

from backend.core.activity_log import log_activity
from backend.core.db import get_connection
from backend.gd_sii.auth import authenticate_cached
from backend.gd_sii.certificate import load_pkcs12
from backend.gd_sii.exceptions import SIIError, SIINotSupportedError
from backend.gd_sii.rut import normalize_chilean_rut
from backend.gd_sii.reconciliation import BankInvoiceMatchingService
from backend.gd_sii.service import SIIReceivedDocumentsService
from backend.gd_sii.storage import PrivateXMLStorage
from backend.gd_sii.vault import SIICredentialVault
from backend.routers.auth import get_current_user

router = APIRouter(prefix="/api/finance/sii", tags=["finance-sii"])
service = SIIReceivedDocumentsService()
matching = BankInvoiceMatchingService()
VIEW_ROLES = {"SUPERADMIN", "ADMIN", "FINANZAS", "CONTROLDEGESTION"}
MANAGE_ROLES = {"SUPERADMIN", "ADMIN", "CONTROLDEGESTION"}
RECONCILE_ROLES = {"SUPERADMIN", "ADMIN", "FINANZAS", "CONTROLDEGESTION"}


class ReconciliationAllocationIn(BaseModel):
    payable_id: int = Field(gt=0)
    amount: Decimal = Field(gt=0)


class ReconciliationConfirmIn(BaseModel):
    allocations: list[ReconciliationAllocationIn] = Field(min_length=1, max_length=100)


class ReconciliationReverseIn(BaseModel):
    reason: str | None = Field(default=None, max_length=240)


def _enabled() -> None:
    enabled = os.getenv("GD_FINANCE_SII_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
    try:
        from backend.core.feature_flags import feature_enabled
        enabled = enabled and feature_enabled("finance_sii")
    except Exception:
        pass
    if not enabled:
        raise HTTPException(404, "Modulo SII deshabilitado")


def _role(me: dict, allowed: set[str]) -> None:
    _enabled()
    role = "".join(ch for ch in str(me.get("role") or me.get("rol") or "").upper() if ch.isalnum())
    if role not in allowed:
        raise HTTPException(403, "No autorizado")


def _safe(exc: Exception) -> HTTPException:
    if isinstance(exc, SIIError):
        return HTTPException(400, {"code": exc.code, "message": exc.safe_message})
    return HTTPException(500, {"code": "SII_INTERNAL_ERROR", "message": "Error interno de integracion SII"})


@router.get("/connections")
def connections(me=Depends(get_current_user)):
    _role(me, VIEW_ROLES)
    with get_connection() as conn:
        rows = conn.execute(text("""
          SELECT e.id_legal_entity,e.legal_code,e.legal_name,e.rut,
            COALESCE(s.environment,c.environment,'CERTIFICATION') AS environment,
            COALESCE(s.status,'DISCONNECTED') AS status,
            s.certificate_filename,s.certificate_subject,s.certificate_serial,
            s.certificate_valid_from,s.certificate_valid_to,s.last_auth_at,
            c.last_sync_at,c.last_sync_status,s.last_error_code,s.last_error_safe
          FROM fin_legal_entities e
          LEFT JOIN wi_sii_connections c USING(id_legal_entity)
          LEFT JOIN wi_sii_settings s ON s.id=1
          WHERE e.is_active IS TRUE ORDER BY e.legal_name
        """)).mappings().all()
        setting = conn.execute(text("SELECT * FROM wi_sii_settings WHERE id=1")).mappings().first()
    items = []
    for row in rows:
        item = dict(row)
        item["name"] = item["legal_name"]
        item["rut_normalized"] = normalize_chilean_rut(item["rut"])
        items.append(item)
    shared = dict(setting) if setting else {"status": "DISCONNECTED", "environment": "CERTIFICATION"}
    shared.pop("certificate_secret_key", None)
    return {"ok": True, "items": items, "shared_certificate": shared}


@router.post("/certificate")
async def upload_certificate(
    certificate: Annotated[UploadFile, File()],
    password: Annotated[str, Form(min_length=1, max_length=500)],
    environment: Annotated[str, Form()] = "CERTIFICATION",
    me=Depends(get_current_user),
):
    _role(me, MANAGE_ROLES)
    environment = environment.upper()
    if environment not in {"CERTIFICATION", "PRODUCTION"}:
        raise HTTPException(400, "Ambiente invalido")
    if environment == "PRODUCTION" and not (os.getenv("SII_MODE") == "PRODUCTION" and os.getenv("SII_ALLOW_PRODUCTION") == "1"):
        raise HTTPException(403, "Produccion SII requiere autorizacion explicita")
    filename = Path(certificate.filename or "").name
    if Path(filename).suffix.lower() not in {".pfx", ".p12"}:
        raise HTTPException(400, "Solo se permiten certificados .pfx o .p12")
    data = await certificate.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise HTTPException(413, "Certificado demasiado grande")
    try:
        loaded = load_pkcs12(data, password)
        meta = loaded.metadata
        vault = SIICredentialVault()
        with get_connection() as conn:
            existed = bool(conn.execute(text("SELECT certificate_secret_key FROM wi_sii_settings WHERE id=1")).scalar())
            secret_key = "corporate-sii.credential"
            vault.put_certificate(conn, secret_key=secret_key, certificate=data, password=password, actor_id=me.get("id"))
            conn.execute(text("""
              INSERT INTO wi_sii_settings(id,environment,status,certificate_secret_key,
                certificate_filename,certificate_subject,certificate_serial,
                certificate_valid_from,certificate_valid_to,created_by,updated_by)
              VALUES (1,:env,'CONFIGURED',:secret,:filename,:subject,:serial,
                :valid_from,:valid_to,:actor,:actor)
              ON CONFLICT(id) DO UPDATE SET environment=EXCLUDED.environment,
                status='CONFIGURED',certificate_secret_key=EXCLUDED.certificate_secret_key,
                certificate_filename=EXCLUDED.certificate_filename,
                certificate_subject=EXCLUDED.certificate_subject,
                certificate_serial=EXCLUDED.certificate_serial,
                certificate_valid_from=EXCLUDED.certificate_valid_from,
                certificate_valid_to=EXCLUDED.certificate_valid_to,
                last_auth_at=NULL,last_error_code=NULL,last_error_safe=NULL,
                updated_at=now(),updated_by=EXCLUDED.updated_by
            """), {"env": environment, "secret": secret_key, "filename": filename, **meta, "actor": me.get("id")})
            conn.execute(text("""
              UPDATE wi_sii_connections SET environment=:env,status='CONFIGURED',
                certificate_secret_key=:secret,certificate_filename=:filename,
                certificate_subject=:subject,certificate_serial=:serial,
                certificate_valid_from=:valid_from,certificate_valid_to=:valid_to,
                last_error_code=NULL,last_error_safe=NULL,updated_at=now(),updated_by=:actor
            """), {"env": environment, "secret": secret_key, "filename": filename, **meta, "actor": me.get("id")})
            log_activity(conn, username=me.get("username"), user_id=me.get("id"), role=me.get("role"),
                         action="SII_CERTIFICATE_REPLACED" if existed else "SII_CERTIFICATE_CREATED",
                         entity_type="wi_sii_settings", entity_id=1,
                         meta={"environment": environment, "filename": filename, "serial": meta["serial"]})
            conn.commit()
    except HTTPException:
        raise
    except Exception as exc:
        raise _safe(exc)
    return {"ok": True, "status": "CONFIGURED", "certificate": meta}


@router.post("/test")
def test_connection(me=Depends(get_current_user)):
    _role(me, MANAGE_ROLES)
    try:
        vault = SIICredentialVault()
        with get_connection() as conn:
            row = conn.execute(text("SELECT * FROM wi_sii_settings WHERE id=1")).mappings().first()
            if not row or not row.get("certificate_secret_key"):
                raise HTTPException(404, "Certificado SII no configurado")
            pfx, password = vault.load_certificate_internal(conn, row["certificate_secret_key"])
            loaded = load_pkcs12(pfx, password)
            authenticate_cached(0, row["environment"], loaded)
            conn.execute(text("UPDATE wi_sii_settings SET status='CONNECTED',last_auth_at=now(),last_error_code=NULL,last_error_safe=NULL,updated_at=now() WHERE id=1"))
            conn.execute(text("UPDATE wi_sii_connections SET status='CONNECTED',last_auth_at=now(),last_error_code=NULL,last_error_safe=NULL,updated_at=now()"))
            log_activity(conn, username=me.get("username"), user_id=me.get("id"), role=me.get("role"), action="SII_TOKEN_CONNECTION_SUCCESS",
                         entity_type="wi_sii_settings", entity_id=1, meta={"status": "CONNECTED", "environment": row["environment"]})
            conn.commit()
    except HTTPException:
        raise
    except Exception as exc:
        try:
            with get_connection() as conn:
                params = {"code": getattr(exc, "code", "SII_AUTH_FAILED"), "safe": getattr(exc, "safe_message", "Fallo de conexion SII")}
                conn.execute(text("UPDATE wi_sii_settings SET status='ERROR',last_error_code=:code,last_error_safe=:safe,updated_at=now() WHERE id=1"), params)
                conn.execute(text("UPDATE wi_sii_connections SET status='ERROR',last_error_code=:code,last_error_safe=:safe,updated_at=now()"), params)
                log_activity(conn, username=me.get("username"), user_id=me.get("id"), role=me.get("role"), action="SII_TOKEN_CONNECTION_ERROR",
                             entity_type="wi_sii_settings", entity_id=1, meta=params)
                conn.commit()
        except Exception:
            pass
        raise _safe(exc)
    return {"ok": True, "status": "CONNECTED"}


@router.post("/disconnect")
def disconnect(me=Depends(get_current_user)):
    _role(me, MANAGE_ROLES)
    try:
        vault = SIICredentialVault()
        with get_connection() as conn:
            key = conn.execute(text("SELECT certificate_secret_key FROM wi_sii_settings WHERE id=1")).scalar()
            if key: vault.delete(conn, key)
            conn.execute(text("UPDATE wi_sii_settings SET status='DISCONNECTED',certificate_secret_key=NULL,certificate_filename=NULL,certificate_subject=NULL,certificate_serial=NULL,certificate_valid_from=NULL,certificate_valid_to=NULL,last_auth_at=NULL,updated_at=now() WHERE id=1"))
            conn.execute(text("UPDATE wi_sii_connections SET status='DISCONNECTED',certificate_secret_key=NULL,certificate_filename=NULL,certificate_subject=NULL,certificate_serial=NULL,certificate_valid_from=NULL,certificate_valid_to=NULL,last_auth_at=NULL,updated_at=now()"))
            conn.commit()
    except Exception as exc:
        raise _safe(exc)
    return {"ok": True, "status": "DISCONNECTED"}


@router.post("/connections/{legal_entity_id}/certificate")
async def legacy_entity_certificate(legal_entity_id: int, me=Depends(get_current_user)):
    _role(me, MANAGE_ROLES)
    raise HTTPException(410, "El certificado SII ahora se administra una sola vez para todo el grupo")


@router.post("/connections/{legal_entity_id}/test")
def legacy_entity_test(legal_entity_id: int, me=Depends(get_current_user)):
    _role(me, MANAGE_ROLES)
    return test_connection(me)


@router.get("/received-documents")
def list_documents(page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                   legal_entity_id: int | None = None, date_from: date | None = None, date_to: date | None = None,
                   document_type: str | None = None, financial_status: str | None = None,
                   source: str | None = None, q: str | None = Query(None, max_length=120), me=Depends(get_current_user)):
    _role(me, VIEW_ROLES)
    return {"ok": True, **service.list_documents(page=page, page_size=page_size, filters=locals())}


@router.get("/received-documents/{document_id}")
def get_document(document_id: int, me=Depends(get_current_user)):
    _role(me, VIEW_ROLES)
    item = service.get_document(document_id)
    if not item: raise HTTPException(404, "Documento no encontrado")
    return {"ok": True, "item": item}


@router.get("/received-documents/{document_id}/xml")
def download_xml(document_id: int, download: bool = False, me=Depends(get_current_user)):
    _role(me, VIEW_ROLES)
    item = service.get_document(document_id)
    if not item or not item.get("xml_storage_key"): raise HTTPException(404, "XML no disponible")
    data = PrivateXMLStorage().get(item["xml_storage_key"])
    headers = {"Content-Disposition": f"{'attachment' if download else 'inline'}; filename=DTE-{item['document_type']}-{item['folio']}.xml"}
    return Response(data, media_type="application/xml", headers=headers)


@router.post("/import/xml")
async def import_xml(legal_entity_id: Annotated[int, Form()], file: Annotated[UploadFile, File()], me=Depends(get_current_user)):
    _role(me, MANAGE_ROLES)
    data = await file.read(10 * 1024 * 1024 + 1)
    try: return {"ok": True, **service.import_dte_xml(legal_entity_id, data, actor=me)}
    except Exception as exc: raise _safe(exc)


@router.post("/import/rcv")
async def import_rcv(legal_entity_id: Annotated[int, Form()], file: Annotated[UploadFile, File()], me=Depends(get_current_user)):
    _role(me, MANAGE_ROLES)
    data = await file.read(25 * 1024 * 1024 + 1)
    try: return {"ok": True, **service.import_rcv_csv(legal_entity_id, data, actor=me)}
    except Exception as exc: raise _safe(exc)


@router.post("/import/email")
def import_email(me=Depends(get_current_user)):
    _role(me, MANAGE_ROLES)
    try: return {"ok": True, **service.import_email_dte(actor=me)}
    except Exception as exc: raise _safe(exc)


@router.post("/sync")
def sync(me=Depends(get_current_user)):
    _role(me, MANAGE_ROLES)
    exc = SIINotSupportedError("El SII no expone un Web Service publico documentado para descarga masiva RCV; utilice CSV oficial o XML DTE.")
    raise HTTPException(501, {"code": exc.code, "message": exc.safe_message})


@router.get("/sync-runs")
def sync_runs(limit: int = Query(50, ge=1, le=200), me=Depends(get_current_user)):
    _role(me, VIEW_ROLES)
    with get_connection() as conn:
        rows = conn.execute(text("SELECT * FROM wi_sii_sync_runs ORDER BY started_at DESC LIMIT :limit"), {"limit": limit}).mappings().all()
    return {"ok": True, "items": [dict(r) for r in rows]}


@router.get("/suppliers")
def list_suppliers(q: str | None = Query(None, max_length=120), page: int = Query(1, ge=1),
                   page_size: int = Query(50, ge=1, le=200), me=Depends(get_current_user)):
    _role(me, VIEW_ROLES)
    params = {"q": f"%{q or ''}%", "limit": page_size, "offset": (page - 1) * page_size}
    where = "AND (COALESCE(p.razon_social,p.nombre,'') ILIKE :q OR COALESCE(p.rut_normalized,'') ILIKE :q)" if q else ""
    with get_connection() as conn:
        total = conn.execute(text(f"SELECT count(*) FROM inv_proveedores p WHERE p.rut_normalized IS NOT NULL {where}"), params).scalar_one()
        rows = conn.execute(text(f"""
          SELECT p.id_proveedor,p.razon_social,p.nombre,p.rut_normalized,p.giro,p.actividad_economica,
            p.codigo_actividad_economica,p.direccion_tributaria,p.comuna_tributaria,p.ciudad_tributaria,
            p.source,p.first_seen_at,p.last_seen_at,p.last_sii_update_at,p.is_active,
            min(d.issue_date) AS primera_factura,max(d.issue_date) AS ultima_factura,count(d.id) AS cantidad_documentos,
            COALESCE(sum(d.total_amount) FILTER(WHERE d.document_type IN ('33','34','56')),0)-COALESCE(sum(d.total_amount) FILTER(WHERE d.document_type='61'),0) AS total_comprado,
            COALESCE(sum(g.balance),0) AS saldo_pendiente
          FROM inv_proveedores p LEFT JOIN fin_sii_received_documents d ON d.supplier_id=p.id_proveedor
          LEFT JOIN fin_gastos g ON g.id_gasto=d.accounts_payable_id
          WHERE p.rut_normalized IS NOT NULL {where}
          GROUP BY p.id_proveedor ORDER BY COALESCE(p.razon_social,p.nombre) LIMIT :limit OFFSET :offset
        """), params).mappings().all()
    return {"ok": True, "items": [dict(r) for r in rows], "total": int(total), "page": page, "page_size": page_size}


@router.get("/suppliers/{supplier_id}")
def supplier_detail(supplier_id: int, me=Depends(get_current_user)):
    _role(me, VIEW_ROLES)
    with get_connection() as conn:
        supplier = conn.execute(text("SELECT * FROM inv_proveedores WHERE id_proveedor=:id"), {"id": supplier_id}).mappings().first()
        if not supplier: raise HTTPException(404, "Proveedor no encontrado")
        documents = conn.execute(text("SELECT id,document_type,folio,issue_date,total_amount,financial_status,accounts_payable_id FROM fin_sii_received_documents WHERE supplier_id=:id ORDER BY issue_date DESC,id DESC LIMIT 200"), {"id": supplier_id}).mappings().all()
        summary = conn.execute(text("""
          SELECT count(DISTINCT d.id) AS invoices,
            COALESCE(sum(d.total_amount) FILTER(WHERE d.document_type IN ('33','34','56')),0)
              - COALESCE(sum(d.total_amount) FILTER(WHERE d.document_type='61'),0) AS total_purchased,
            COALESCE((SELECT sum(a.amount_applied) FROM fin_payable_bank_allocations a
              JOIN fin_gastos g ON g.id_gasto=a.payable_id
              WHERE g.supplier_id=:id AND a.status='CONFIRMED'),0) AS paid,
            COALESCE((SELECT sum(GREATEST(0,g.balance)) FROM fin_gastos g
              WHERE g.supplier_id=:id AND g.parent_payable_id IS NULL AND g.sii_document_id IS NOT NULL),0) AS outstanding,
            max(d.issue_date) AS last_purchase
          FROM fin_sii_received_documents d WHERE d.supplier_id=:id
        """), {"id": supplier_id}).mappings().one()
        payments = conn.execute(text("""
          SELECT a.id,a.amount_applied,a.matched_at,a.status,bm.id_bank_movement,bm.tx_date,
            bm.description,bm.reference,ba.bank_name,ba.label,g.id_gasto AS payable_id
          FROM fin_payable_bank_allocations a
          JOIN fin_gastos g ON g.id_gasto=a.payable_id
          JOIN fin_bank_movements bm ON bm.id_bank_movement=a.bank_movement_id
          JOIN fin_bank_accounts ba ON ba.id_bank_account=bm.id_bank_account
          WHERE g.supplier_id=:id ORDER BY a.created_at DESC LIMIT 200
        """), {"id": supplier_id}).mappings().all()
        audit = conn.execute(text("SELECT id,created_at,action,user_id,meta FROM activity_log WHERE entity_type IN ('supplier','inv_proveedor','fin_sii_received_document') AND (entity_id=:id OR meta->>'supplier_id'=:sid) ORDER BY created_at DESC LIMIT 100"), {"id": supplier_id, "sid": str(supplier_id)}).mappings().all()
    return {"ok": True, "item": dict(supplier), "summary": dict(summary),
            "documents": [dict(r) for r in documents], "payments": [dict(r) for r in payments],
            "audit": [dict(r) for r in audit]}


@router.get("/dashboard")
def finance_dashboard(me=Depends(get_current_user)):
    _role(me, VIEW_ROLES)
    with get_connection() as conn:
        payables = conn.execute(text("""
          WITH roots AS (
            SELECT root.id_gasto,root.fecha_vencimiento,
              GREATEST(0,COALESCE(root.amount_original,root.monto,0)+COALESCE(sum(child.amount_original),0)) AS net_payable
            FROM fin_gastos root
            LEFT JOIN fin_gastos child ON child.parent_payable_id=root.id_gasto AND child.is_active IS TRUE
            WHERE root.parent_payable_id IS NULL AND root.sii_document_id IS NOT NULL AND root.is_active IS TRUE
            GROUP BY root.id_gasto
          ), paid AS (
            SELECT payable_id,COALESCE(sum(amount_applied),0) AS paid_amount
            FROM fin_payable_bank_allocations WHERE status='CONFIRMED' GROUP BY payable_id
          )
          SELECT COALESCE(sum(GREATEST(0,r.net_payable-COALESCE(p.paid_amount,0))),0) AS outstanding,
            COALESCE(sum(GREATEST(0,r.net_payable-COALESCE(p.paid_amount,0)))
              FILTER(WHERE r.fecha_vencimiento<CURRENT_DATE),0) AS overdue,
            count(*) FILTER(WHERE r.net_payable-COALESCE(p.paid_amount,0)>0) AS open_invoices,
            count(*) FILTER(WHERE COALESCE(p.paid_amount,0)>0 AND r.net_payable-COALESCE(p.paid_amount,0)>0) AS partial_invoices
          FROM roots r LEFT JOIN paid p ON p.payable_id=r.id_gasto
        """)).mappings().one()
        paid_month = conn.execute(text("""
          SELECT COALESCE(sum(amount_applied),0) FROM fin_payable_bank_allocations
          WHERE status='CONFIRMED' AND matched_at>=date_trunc('month',CURRENT_DATE)
        """)).scalar_one()
        unreconciled = matching.unreconciled_movements(conn, limit=5000)
        certificate = conn.execute(text("""
          SELECT status,certificate_valid_to,last_auth_at FROM wi_sii_settings WHERE id=1
        """)).mappings().first()
    return {"ok": True, "summary": {**dict(payables), "paid_this_month": paid_month,
            "unreconciled_movements": len(unreconciled),
            "unreconciled_amount": sum((Decimal(str(row["remaining_amount"])) for row in unreconciled), Decimal("0")),
            "sii": dict(certificate) if certificate else {"status": "DISCONNECTED"}}}


@router.get("/reconciliation/movements")
def reconciliation_movements(
    legal_entity_id: int | None = None,
    limit: int = Query(100, ge=1, le=500),
    me=Depends(get_current_user),
):
    _role(me, VIEW_ROLES)
    with get_connection() as conn:
        items = matching.unreconciled_movements(conn, legal_entity_id=legal_entity_id, limit=limit)
    return {"ok": True, "items": items}


@router.get("/reconciliation/movements/{movement_id}/candidates")
def reconciliation_candidates(movement_id: int, me=Depends(get_current_user)):
    _role(me, VIEW_ROLES)
    try:
        with get_connection() as conn:
            movement, items = matching.candidates(conn, movement_id)
        return {"ok": True, "movement": movement, "items": items}
    except Exception as exc:
        raise _safe(exc)


@router.post("/reconciliation/movements/{movement_id}/suggest")
def reconciliation_suggest(movement_id: int, me=Depends(get_current_user)):
    _role(me, RECONCILE_ROLES)
    try:
        with get_connection() as conn:
            with conn.begin():
                items = matching.persist_suggestions(conn, movement_id, actor=me)
        return {"ok": True, "items": items}
    except Exception as exc:
        raise _safe(exc)


@router.post("/reconciliation/movements/{movement_id}/confirm")
def reconciliation_confirm(movement_id: int, body: ReconciliationConfirmIn, me=Depends(get_current_user)):
    _role(me, RECONCILE_ROLES)
    try:
        allocations = [item.model_dump() for item in body.allocations]
        with get_connection() as conn:
            with conn.begin():
                result = matching.confirm(conn, movement_id, allocations, actor=me)
        return {"ok": True, **result}
    except Exception as exc:
        raise _safe(exc)


@router.post("/reconciliation/allocations/{allocation_id}/reverse")
def reconciliation_reverse(allocation_id: int, body: ReconciliationReverseIn, me=Depends(get_current_user)):
    _role(me, RECONCILE_ROLES)
    try:
        with get_connection() as conn:
            with conn.begin():
                result = matching.reverse(conn, allocation_id, actor=me, reason=body.reason)
        return {"ok": True, **result}
    except Exception as exc:
        raise _safe(exc)


@router.get("/reconciliation/payables/{payable_id}")
def reconciliation_payable_detail(payable_id: int, me=Depends(get_current_user)):
    _role(me, VIEW_ROLES)
    with get_connection() as conn:
        payable = conn.execute(text("""
          SELECT g.id_gasto AS payable_id,g.id_legal_entity,g.fecha AS issue_date,g.fecha_vencimiento,
            g.amount_original,g.balance,g.payable_status,g.sii_document_id,d.document_type,d.folio,
            d.total_amount,d.xml_storage_key,COALESCE(p.razon_social,p.nombre,d.issuer_name) AS supplier_name
          FROM fin_gastos g
          JOIN fin_sii_received_documents d ON d.id=g.sii_document_id
          JOIN inv_proveedores p ON p.id_proveedor=g.supplier_id
          WHERE g.id_gasto=:id
        """), {"id": payable_id}).mappings().first()
        if not payable:
            raise HTTPException(404, "Cuenta por pagar no encontrada")
        allocations = conn.execute(text("""
          SELECT a.id,a.amount_applied,a.match_method,a.match_confidence,a.status,a.matched_at,a.reversed_at,
            bm.id_bank_movement,bm.tx_date,bm.description,bm.reference,ba.bank_name,ba.label
          FROM fin_payable_bank_allocations a
          JOIN fin_bank_movements bm ON bm.id_bank_movement=a.bank_movement_id
          JOIN fin_bank_accounts ba ON ba.id_bank_account=bm.id_bank_account
          WHERE a.payable_id=:id ORDER BY a.created_at DESC,a.id DESC
        """), {"id": payable_id}).mappings().all()
    return {"ok": True, "item": dict(payable), "allocations": [dict(row) for row in allocations]}


@router.get("/health")
def health(me=Depends(get_current_user)):
    _role(me, VIEW_ROLES)
    with get_connection() as conn:
        row = conn.execute(text("""
          SELECT s.status AS certificate_status,
            count(e.id_legal_entity) FILTER(WHERE e.is_active) AS active_entities
          FROM wi_sii_settings s CROSS JOIN fin_legal_entities e
          WHERE s.id=1 GROUP BY s.status
        """)).mappings().first()
    return {"status": "ok", "certificate_status": row["certificate_status"] if row else "DISCONNECTED",
            "active_entities": int(row["active_entities"] if row else 0), "automated_rcv_api": False}


# === GD FINANCE R54 MOVEMENT RESOLUTION START ===

class ReconciliationResolveAllocationR54(BaseModel):
    payable_id: int = Field(gt=0)
    amount: Decimal = Field(gt=0)
    account_code: str = Field(
        min_length=1,
        max_length=32,
    )


class ReconciliationResolveR54(BaseModel):
    mode: str = Field(
        min_length=1,
        max_length=32,
    )
    allocations: list[
        ReconciliationResolveAllocationR54
    ] = Field(
        default_factory=list,
        max_length=100,
    )
    account_code: str | None = Field(
        default=None,
        max_length=32,
    )
    responsible_id: int | None = Field(
        default=None,
        gt=0,
    )
    description: str | None = Field(
        default=None,
        max_length=240,
    )


def _r54_actor_id(me: dict) -> int | None:
    for key in (
        "id_usuario",
        "user_id",
        "id",
        "sub",
    ):
        try:
            value = me.get(key)
            if value is not None:
                return int(value)
        except Exception:
            pass
    return None


def _r54_account(
    conn,
    code: str | None,
):
    account_code = str(
        code or ""
    ).strip()

    if not account_code:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "PUC_REQUIRED",
                "message":
                    "Selecciona una cuenta "
                    "del Plan de Cuentas.",
            },
        )

    row = conn.execute(
        text(
            """
            SELECT
              code,
              name,
              type,
              classification,
              parent_code
            FROM public.plan_cuentas
            WHERE code=:code
              AND COALESCE(
                    is_active,
                    TRUE
                  )=TRUE
            LIMIT 1
            """
        ),
        {
            "code": account_code,
        },
    ).mappings().first()

    if not row:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "PUC_INVALID",
                "message":
                    "La cuenta PUC seleccionada "
                    "no existe o está inactiva.",
            },
        )

    return dict(row)


def _r54_validate_responsible(
    conn,
    responsible_id: int | None,
):
    if responsible_id is None:
        return None

    row = conn.execute(
        text(
            """
            SELECT u.id_usuario
            FROM public.usuarios u
            JOIN public.fin_expense_responsibles fr
              ON fr.id_usuario=u.id_usuario
             AND fr.is_enabled=TRUE
            WHERE u.id_usuario=:id
              AND u.is_active=TRUE
            LIMIT 1
            """
        ),
        {
            "id": responsible_id,
        },
    ).first()

    if not row:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "RESPONSIBLE_INVALID",
                "message":
                    "El responsable seleccionado "
                    "no está habilitado en Finanzas.",
            },
        )

    return responsible_id


def _r54_movement(
    conn,
    movement_id: int,
    *,
    lock: bool = False,
):
    lock_sql = (
        " FOR UPDATE OF bm"
        if lock
        else ""
    )

    row = conn.execute(
        text(
            """
            SELECT
              bm.id_bank_movement,
              bm.id_bank_account,
              bm.tx_date,
              bm.description,
              bm.amount,
              bm.reference,
              bm.balance,
              bm.status,
              bm.cuenta_code,

              ba.bank_name,
              ba.label AS bank_account,
              ba.id_legal_entity,

              le.legal_name,
              le.rut,
              le.legal_code

            FROM public.fin_bank_movements bm

            JOIN public.fin_bank_accounts ba
              ON ba.id_bank_account=
                 bm.id_bank_account

            LEFT JOIN public.fin_legal_entities le
              ON le.id_legal_entity=
                 ba.id_legal_entity

            WHERE bm.id_bank_movement=:movement_id
            """
            + lock_sql
        ),
        {
            "movement_id": movement_id,
        },
    ).mappings().first()

    if not row:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "BANK_MOVEMENT_NOT_FOUND",
                "message":
                    "Movimiento bancario no encontrado.",
            },
        )

    return dict(row)


@router.get("/reconciliation/accounts")
def reconciliation_accounts_r54(
    me=Depends(get_current_user),
):
    _role(me, VIEW_ROLES)

    with get_connection() as conn:
        rows = conn.execute(
            text(
                """
                WITH confirmed AS (
                  SELECT
                    bank_movement_id,
                    COALESCE(
                      SUM(amount_applied),
                      0
                    ) AS applied
                  FROM public.fin_payable_bank_allocations
                  WHERE status='CONFIRMED'
                  GROUP BY bank_movement_id
                ),

                pending AS (
                  SELECT
                    bm.id_bank_account,

                    COUNT(*) FILTER (
                      WHERE bm.status='PENDING'
                        AND bm.amount < 0
                        AND (
                          ABS(bm.amount)
                          -
                          COALESCE(c.applied,0)
                        ) > 0.50
                    ) AS pending_movements,

                    COALESCE(
                      SUM(
                        GREATEST(
                          ABS(bm.amount)
                          -
                          COALESCE(c.applied,0),
                          0
                        )
                      ) FILTER (
                        WHERE bm.status='PENDING'
                          AND bm.amount < 0
                          AND (
                            ABS(bm.amount)
                            -
                            COALESCE(c.applied,0)
                          ) > 0.50
                      ),
                      0
                    ) AS pending_amount

                  FROM public.fin_bank_movements bm

                  LEFT JOIN confirmed c
                    ON c.bank_movement_id=
                       bm.id_bank_movement

                  GROUP BY bm.id_bank_account
                )

                SELECT
                  ba.id_bank_account,
                  ba.bank_name,
                  ba.label,
                  ba.currency,
                  ba.id_legal_entity,

                  le.legal_name,
                  le.rut,
                  le.legal_code,

                  COALESCE(
                    p.pending_movements,
                    0
                  ) AS pending_movements,

                  COALESCE(
                    p.pending_amount,
                    0
                  ) AS pending_amount

                FROM public.fin_bank_accounts ba

                JOIN public.fin_legal_entities le
                  ON le.id_legal_entity=
                     ba.id_legal_entity

                LEFT JOIN pending p
                  ON p.id_bank_account=
                     ba.id_bank_account

                WHERE ba.is_active=TRUE
                  AND le.is_active=TRUE

                ORDER BY
                  le.legal_name,
                  ba.bank_name,
                  ba.label
                """
            )
        ).mappings().all()

    return {
        "ok": True,
        "items": [
            dict(row)
            for row in rows
        ],
    }


@router.get(
    "/reconciliation/accounts/"
    "{bank_account_id}/movements"
)
def reconciliation_account_movements_r54(
    bank_account_id: int,
    limit: int = Query(
        500,
        ge=1,
        le=1000,
    ),
    me=Depends(get_current_user),
):
    _role(me, VIEW_ROLES)

    with get_connection() as conn:
        account = conn.execute(
            text(
                """
                SELECT
                  ba.id_bank_account,
                  ba.bank_name,
                  ba.label,
                  ba.id_legal_entity,
                  le.legal_name,
                  le.rut,
                  le.legal_code

                FROM public.fin_bank_accounts ba

                JOIN public.fin_legal_entities le
                  ON le.id_legal_entity=
                     ba.id_legal_entity

                WHERE ba.id_bank_account=:id
                  AND ba.is_active=TRUE
                  AND le.is_active=TRUE
                """
            ),
            {
                "id": bank_account_id,
            },
        ).mappings().first()

        if not account:
            raise HTTPException(
                status_code=404,
                detail={
                    "code":
                        "BANK_ACCOUNT_NOT_FOUND",
                    "message":
                        "Cuenta bancaria no encontrada.",
                },
            )

        rows = conn.execute(
            text(
                """
                WITH confirmed AS (
                  SELECT
                    bank_movement_id,
                    COALESCE(
                      SUM(amount_applied),
                      0
                    ) AS applied
                  FROM public.fin_payable_bank_allocations
                  WHERE status='CONFIRMED'
                  GROUP BY bank_movement_id
                )

                SELECT
                  bm.id_bank_movement,
                  bm.id_bank_account,
                  bm.tx_date,
                  bm.description,
                  bm.reference,
                  bm.amount,
                  bm.balance,
                  bm.status,

                  ba.bank_name,
                  ba.label,
                  ba.id_legal_entity,

                  le.legal_name,
                  le.rut,
                  le.legal_code,

                  GREATEST(
                    ABS(bm.amount)
                    -
                    COALESCE(c.applied,0),
                    0
                  ) AS remaining_amount

                FROM public.fin_bank_movements bm

                JOIN public.fin_bank_accounts ba
                  ON ba.id_bank_account=
                     bm.id_bank_account

                JOIN public.fin_legal_entities le
                  ON le.id_legal_entity=
                     ba.id_legal_entity

                LEFT JOIN confirmed c
                  ON c.bank_movement_id=
                     bm.id_bank_movement

                WHERE bm.id_bank_account=:account
                  AND bm.status='PENDING'
                  AND bm.amount < 0

                  AND GREATEST(
                    ABS(bm.amount)
                    -
                    COALESCE(c.applied,0),
                    0
                  ) > 0.50

                ORDER BY
                  bm.tx_date DESC,
                  bm.id_bank_movement DESC

                LIMIT :limit
                """
            ),
            {
                "account": bank_account_id,
                "limit": limit,
            },
        ).mappings().all()

    return {
        "ok": True,
        "account": dict(account),
        "items": [
            dict(row)
            for row in rows
        ],
    }


@router.get(
    "/reconciliation/accounts/"
    "{bank_account_id}/movements/"
    "{movement_id}/candidates"
)
def reconciliation_account_candidates_r54(
    bank_account_id: int,
    movement_id: int,
    me=Depends(get_current_user),
):
    _role(me, VIEW_ROLES)

    try:
        with get_connection() as conn:
            movement = _r54_movement(
                conn,
                movement_id,
            )

            if (
                int(
                    movement["id_bank_account"]
                )
                != int(bank_account_id)
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code":
                            "BANK_ACCOUNT_MISMATCH",
                        "message":
                            "El movimiento no pertenece "
                            "a la cuenta bancaria seleccionada.",
                    },
                )

            movement_match, items = (
                matching.candidates(
                    conn,
                    movement_id,
                )
            )

            valid_ids = set(
                int(value)
                for value in conn.execute(
                    text(
                        """
                        SELECT id_gasto
                        FROM public.fin_gastos
                        WHERE id_legal_entity=:entity
                          AND COALESCE(
                                is_active,
                                TRUE
                              )=TRUE
                        """
                    ),
                    {
                        "entity":
                            movement[
                                "id_legal_entity"
                            ],
                    },
                ).scalars().all()
            )

            filtered = [
                item
                for item in items
                if int(
                    item.get(
                        "payable_id"
                    )
                    or 0
                )
                in valid_ids
            ]

        return {
            "ok": True,
            "movement": movement_match,
            "items": filtered,
            "legal_entity_id":
                movement["id_legal_entity"],
            "bank_account_id":
                bank_account_id,
        }

    except HTTPException:
        raise

    except Exception as exc:
        raise _safe(exc)


@router.get(
    "/reconciliation/classification-meta"
)
def reconciliation_classification_meta_r54(
    me=Depends(get_current_user),
):
    _role(me, VIEW_ROLES)

    with get_connection() as conn:
        accounts = conn.execute(
            text(
                """
                SELECT
                  pc.code,
                  pc.name,
                  pc.type,
                  pc.classification,
                  pc.parent_code

                FROM public.plan_cuentas pc

                WHERE COALESCE(
                        pc.is_active,
                        TRUE
                      )=TRUE

                  AND NOT EXISTS (
                    SELECT 1
                    FROM public.plan_cuentas child
                    WHERE child.parent_code=pc.code
                      AND COALESCE(
                            child.is_active,
                            TRUE
                          )=TRUE
                  )

                ORDER BY pc.code
                """
            )
        ).mappings().all()

        responsibles = conn.execute(
            text(
                """
                SELECT
                  u.id_usuario,
                  u.nombre,
                  u.username

                FROM public.usuarios u

                JOIN public.fin_expense_responsibles fr
                  ON fr.id_usuario=
                     u.id_usuario
                 AND fr.is_enabled=TRUE

                WHERE u.is_active=TRUE

                ORDER BY
                  u.nombre,
                  u.username
                """
            )
        ).mappings().all()

    return {
        "ok": True,
        "accounts": [
            dict(row)
            for row in accounts
        ],
        "responsibles": [
            dict(row)
            for row in responsibles
        ],
    }


@router.post(
    "/reconciliation/movements/"
    "{movement_id}/resolve"
)
def reconciliation_resolve_r54(
    movement_id: int,
    body: ReconciliationResolveR54,
    me=Depends(get_current_user),
):
    _role(me, RECONCILE_ROLES)

    mode = str(
        body.mode or ""
    ).strip().upper()

    allowed_modes = {
        "INVOICE",
        "DIRECT_EXPENSE",
        "COGS",
        "OPERATING_EXPENSE",
        "ASSET_PURCHASE",
        "OBLIGATION",
        "TRANSFER",
    }

    if mode not in allowed_modes:
        raise HTTPException(
            status_code=400,
            detail={
                "code":
                    "MOVEMENT_RESOLUTION_MODE_INVALID",
                "message":
                    "Tipo de resolución contable inválido.",
            },
        )

    actor = _r54_actor_id(me)
    description = str(
        body.description or ""
    ).strip()

    try:
        if mode == "INVOICE":
            if not body.allocations:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code":
                            "INVOICE_REQUIRED",
                        "message":
                            "Selecciona al menos "
                            "una factura.",
                    },
                )

            with get_connection() as conn:
                with conn.begin():
                    movement = _r54_movement(
                        conn,
                        movement_id,
                        lock=True,
                    )

                    if (
                        str(
                            movement["status"]
                            or ""
                        ).upper()
                        != "PENDING"
                    ):
                        raise HTTPException(
                            status_code=409,
                            detail={
                                "code":
                                    "MOVEMENT_ALREADY_PROCESSED",
                                "message":
                                    "El movimiento ya fue "
                                    "procesado.",
                            },
                        )

                    _r54_validate_responsible(
                        conn,
                        body.responsible_id,
                    )

                    seen = set()
                    confirm_allocations = []

                    for item in body.allocations:
                        payable_id = int(
                            item.payable_id
                        )

                        if payable_id in seen:
                            raise HTTPException(
                                status_code=400,
                                detail={
                                    "code":
                                        "DUPLICATE_PAYABLE",
                                    "message":
                                        "Hay una factura "
                                        "seleccionada más "
                                        "de una vez.",
                                },
                            )

                        seen.add(payable_id)

                        payable = conn.execute(
                            text(
                                """
                                SELECT
                                  id_gasto,
                                  id_legal_entity,
                                  balance,
                                  payable_status
                                FROM public.fin_gastos
                                WHERE id_gasto=:id
                                  AND COALESCE(
                                        is_active,
                                        TRUE
                                      )=TRUE
                                """
                            ),
                            {
                                "id": payable_id,
                            },
                        ).mappings().first()

                        if not payable:
                            raise HTTPException(
                                status_code=404,
                                detail={
                                    "code":
                                        "PAYABLE_NOT_FOUND",
                                    "message":
                                        "Cuenta por pagar "
                                        "no encontrada.",
                                },
                            )

                        if (
                            int(
                                payable[
                                    "id_legal_entity"
                                ]
                                or 0
                            )
                            != int(
                                movement[
                                    "id_legal_entity"
                                ]
                                or 0
                            )
                        ):
                            raise HTTPException(
                                status_code=409,
                                detail={
                                    "code":
                                        "LEGAL_ENTITY_MISMATCH",
                                    "message":
                                        "La factura y la "
                                        "cartola pertenecen "
                                        "a empresas/RUT "
                                        "distintos.",
                                },
                            )

                        account = _r54_account(
                            conn,
                            item.account_code,
                        )

                        account_type = str(
                            account.get("type")
                            or ""
                        ).lower()

                        if account_type in {
                            "income",
                            "revenue",
                        }:
                            raise HTTPException(
                                status_code=400,
                                detail={
                                    "code":
                                        "PURCHASE_ACCOUNT_INVALID",
                                    "message":
                                        "Una factura de compra "
                                        "no puede clasificarse "
                                        "en una cuenta de ingreso.",
                                },
                            )

                        confirm_allocations.append(
                            {
                                "payable_id":
                                    payable_id,
                                "amount":
                                    item.amount,
                            }
                        )

                    result = matching.confirm(
                        conn,
                        movement_id,
                        confirm_allocations,
                        actor=me,
                    )

                    for item in body.allocations:
                        conn.execute(
                            text(
                                """
                                UPDATE public.fin_gastos

                                SET
                                  cuenta_code=:account,

                                  responsable_id=
                                    COALESCE(
                                      :responsible,
                                      responsable_id
                                    ),

                                  descripcion_interna=
                                    COALESCE(
                                      NULLIF(
                                        :description,
                                        ''
                                      ),
                                      descripcion_interna
                                    )

                                WHERE id_gasto=:payable
                                  AND id_legal_entity=:entity
                                """
                            ),
                            {
                                "account":
                                    item.account_code,
                                "responsible":
                                    body.responsible_id,
                                "description":
                                    description,
                                "payable":
                                    item.payable_id,
                                "entity":
                                    movement[
                                        "id_legal_entity"
                                    ],
                            },
                        )

            return {
                "ok": True,
                "mode": mode,
                "movement_id":
                    movement_id,
                "reconciliation":
                    result,
                "message":
                    "Factura conciliada y "
                    "clasificada en el PUC.",
            }

        with get_connection() as conn:
            with conn.begin():
                movement = _r54_movement(
                    conn,
                    movement_id,
                    lock=True,
                )

                if (
                    str(
                        movement["status"]
                        or ""
                    ).upper()
                    != "PENDING"
                ):
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code":
                                "MOVEMENT_ALREADY_PROCESSED",
                            "message":
                                "El movimiento ya fue "
                                "procesado.",
                        },
                    )

                amount = Decimal(
                    str(
                        movement["amount"]
                    )
                )

                if amount >= 0:
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "code":
                                "BANK_MOVEMENT_NOT_EXPENSE",
                            "message":
                                "Esta operación sólo "
                                "resuelve egresos.",
                        },
                    )

                confirmed = Decimal(
                    str(
                        conn.execute(
                            text(
                                """
                                SELECT COALESCE(
                                  SUM(amount_applied),
                                  0
                                )
                                FROM public.fin_payable_bank_allocations
                                WHERE bank_movement_id=:movement
                                  AND status='CONFIRMED'
                                """
                            ),
                            {
                                "movement":
                                    movement_id,
                            },
                        ).scalar()
                        or 0
                    )
                )

                if confirmed > Decimal("0"):
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code":
                                "BANK_MOVEMENT_ALREADY_RECONCILED",
                            "message":
                                "Este movimiento ya tiene "
                                "una factura conciliada. "
                                "No se puede volver a "
                                "registrar como gasto.",
                        },
                    )

                account = _r54_account(
                    conn,
                    body.account_code,
                )

                account_type = str(
                    account.get("type")
                    or ""
                ).lower()

                _r54_validate_responsible(
                    conn,
                    body.responsible_id,
                )

                expense_modes = {
                    "DIRECT_EXPENSE",
                    "COGS",
                    "OPERATING_EXPENSE",
                }

                if mode in expense_modes:
                    if account_type != "expense":
                        raise HTTPException(
                            status_code=400,
                            detail={
                                "code":
                                    "EXPENSE_ACCOUNT_REQUIRED",
                                "message":
                                    "Para costo o gasto "
                                    "debes seleccionar una "
                                    "cuenta PUC de gasto.",
                            },
                        )

                    classification = str(
                        account.get("classification")
                        or ""
                    ).strip().upper()

                    if mode == "COGS" and classification != "COGS":
                        raise HTTPException(
                            status_code=400,
                            detail={
                                "code": "COGS_ACCOUNT_REQUIRED",
                                "message": "Selecciona una cuenta de Costo directo / COGS.",
                            },
                        )

                    if mode == "OPERATING_EXPENSE" and classification == "COGS":
                        raise HTTPException(
                            status_code=400,
                            detail={
                                "code": "OPERATING_ACCOUNT_REQUIRED",
                                "message": "Selecciona una cuenta de gasto operacional, administrativo o fijo.",
                            },
                        )

                    if body.responsible_id is None:
                        raise HTTPException(
                            status_code=400,
                            detail={
                                "code":
                                    "RESPONSIBLE_REQUIRED",
                                "message":
                                    "Selecciona el responsable "
                                    "del gasto.",
                            },
                        )

                    if not description:
                        raise HTTPException(
                            status_code=400,
                            detail={
                                "code":
                                    "DESCRIPTION_REQUIRED",
                                "message":
                                    "Indica brevemente a qué "
                                    "corresponde el gasto.",
                            },
                        )

                    expense_amount = abs(
                        amount
                    )

                    conn.execute(
                        text(
                            """
                            INSERT INTO public.fin_gastos(
                              fecha,
                              cuenta_code,
                              monto,
                              descripcion,
                              proveedor,
                              marca,
                              centro_costo,
                              created_at,
                              doc_num,
                              pagado,
                              fecha_pago,
                              tipo_doc,
                              is_active,
                              bank_movement_id,
                              responsable_id,
                              descripcion_interna,
                              id_legal_entity
                            )

                            VALUES(
                              :fecha,
                              :cuenta,
                              :monto,
                              :descripcion,
                              :proveedor,
                              'GENERAL',
                              NULL,
                              now(),
                              :doc_num,
                              TRUE,
                              :fecha_pago,
                              'CARTOLA',
                              TRUE,
                              :movement,
                              :responsable,
                              :descripcion_interna,
                              :entity
                            )

                            ON CONFLICT (
                              bank_movement_id
                            )

                            DO UPDATE SET
                              cuenta_code=
                                EXCLUDED.cuenta_code,
                              monto=
                                EXCLUDED.monto,
                              descripcion=
                                EXCLUDED.descripcion,
                              proveedor=
                                EXCLUDED.proveedor,
                              pagado=TRUE,
                              fecha_pago=
                                EXCLUDED.fecha_pago,
                              responsable_id=
                                EXCLUDED.responsable_id,
                              descripcion_interna=
                                EXCLUDED.descripcion_interna,
                              id_legal_entity=
                                EXCLUDED.id_legal_entity,
                              is_active=TRUE
                            """
                        ),
                        {
                            "fecha":
                                movement["tx_date"],
                            "cuenta":
                                account["code"],
                            "monto":
                                expense_amount,
                            "descripcion":
                                description,
                            "proveedor":
                                (
                                    movement[
                                        "description"
                                    ]
                                    or movement[
                                        "reference"
                                    ]
                                    or "Movimiento bancario"
                                ),
                            "doc_num":
                                (
                                    movement[
                                        "reference"
                                    ]
                                    or str(
                                        movement_id
                                    )
                                ),
                            "fecha_pago":
                                movement["tx_date"],
                            "movement":
                                movement_id,
                            "responsable":
                                body.responsible_id,
                            "descripcion_interna":
                                description,
                            "entity":
                                movement[
                                    "id_legal_entity"
                                ],
                        },
                    )

                else:
                    if account_type == "expense":
                        raise HTTPException(
                            status_code=400,
                            detail={
                                "code":
                                    "BALANCE_ACCOUNT_REQUIRED",
                                "message":
                                    "Para una obligación o "
                                    "transferencia selecciona "
                                    "una cuenta patrimonial "
                                    "del PUC, no una cuenta "
                                    "de gasto.",
                            },
                        )

                    if mode == "ASSET_PURCHASE" and account_type != "asset":
                        raise HTTPException(
                            status_code=400,
                            detail={
                                "code": "ASSET_ACCOUNT_REQUIRED",
                                "message": "Para compra de maquinaria o equipos selecciona una cuenta de Activo.",
                            },
                        )

                    if mode == "OBLIGATION" and account_type not in {
                        "liability",
                        "equity",
                    }:
                        raise HTTPException(
                            status_code=400,
                            detail={
                                "code": "LIABILITY_ACCOUNT_REQUIRED",
                                "message": "Para pagar una obligación selecciona una cuenta de Pasivo o Patrimonio.",
                            },
                        )

                    existing_expense = conn.execute(
                        text(
                            """
                            SELECT id_gasto
                            FROM public.fin_gastos
                            WHERE bank_movement_id=:movement
                              AND COALESCE(
                                    is_active,
                                    TRUE
                                  )=TRUE
                            LIMIT 1
                            """
                        ),
                        {
                            "movement":
                                movement_id,
                        },
                    ).scalar()

                    if existing_expense:
                        raise HTTPException(
                            status_code=409,
                            detail={
                                "code":
                                    "DIRECT_EXPENSE_EXISTS",
                                "message":
                                    "El movimiento ya tiene "
                                    "un gasto asociado.",
                            },
                        )

                conn.execute(
                    text(
                        """
                        UPDATE public.fin_payable_bank_allocations

                        SET
                          status='REVERSED',
                          reversed_at=now(),
                          reversed_by=:actor,
                          reversal_reason=
                            'Movimiento resuelto sin factura',
                          updated_at=now()

                        WHERE bank_movement_id=:movement
                          AND status='SUGGESTED'
                        """
                    ),
                    {
                        "actor": actor,
                        "movement":
                            movement_id,
                    },
                )

                conn.execute(
                    text(
                        """
                        UPDATE public.fin_bank_movements

                        SET
                          status='CLASSIFIED',
                          cuenta_code=:cuenta,
                          responsable_id=
                            COALESCE(
                              :responsable,
                              responsable_id
                            ),
                          descripcion_interna=
                            COALESCE(
                              NULLIF(
                                :descripcion,
                                ''
                              ),
                              descripcion_interna
                            ),
                          classified_by=:actor,
                          classified_at=now()

                        WHERE id_bank_movement=:movement
                        """
                    ),
                    {
                        "cuenta":
                            account["code"],
                        "responsable":
                            body.responsible_id,
                        "descripcion":
                            description,
                        "actor":
                            actor,
                        "movement":
                            movement_id,
                    },
                )

        if mode == "COGS":
            message = (
                "Movimiento clasificado como costo directo / COGS."
            )

        elif mode in {
            "DIRECT_EXPENSE",
            "OPERATING_EXPENSE",
        }:
            message = (
                "Movimiento clasificado como gasto operacional."
            )

        elif mode == "ASSET_PURCHASE":
            message = (
                "Compra registrada como activo. No se llevó completa al P&L."
            )

        elif mode == "OBLIGATION":
            message = (
                "Movimiento aplicado a "
                "una obligación patrimonial. "
                "No se duplicó el P&L."
            )

        else:
            message = (
                "Transferencia clasificada. "
                "No se registró como gasto."
            )

        return {
            "ok": True,
            "mode": mode,
            "movement_id":
                movement_id,
            "account_code":
                account["code"],
            "account_name":
                account["name"],
            "message":
                message,
        }

    except HTTPException:
        raise

    except Exception as exc:
        raise _safe(exc)


# === GD FINANCE R54 MOVEMENT RESOLUTION END ===


# === GD FINANCE R54 PREMIUM INVOICE FIRST START ===

import re as _r54_re
import unicodedata as _r54_unicode


class ChartAccountCreateR54(BaseModel):
    code: str | None = Field(
        default=None,
        max_length=32,
    )
    name: str = Field(
        min_length=1,
        max_length=180,
    )
    type: str = Field(
        min_length=1,
        max_length=40,
    )
    classification: str | None = Field(
        default=None,
        max_length=120,
    )
    parent_code: str | None = Field(
        default=None,
        max_length=32,
    )
    description: str | None = Field(
        default=None,
        max_length=500,
    )


def _next_chart_account_code(
    conn,
    parent_code: str | None,
    account_type: str,
) -> str:
    """Return a collision-free hierarchical PUC code.

    Legacy root accounts keep their four-digit convention. New descendants use
    dotted two-digit segments so nesting remains explicit at every depth.
    """
    conn.execute(
        text(
            "SELECT pg_advisory_xact_lock(hashtext('gd_puc_code_generation'))"
        )
    )

    codes = {
        str(value)
        for value in conn.execute(
            text("SELECT code FROM public.plan_cuentas")
        ).scalars().all()
    }

    if parent_code:
        prefix = f"{parent_code}."
        used = []
        for value in codes:
            if not value.startswith(prefix):
                continue
            tail = value[len(prefix):]
            if "." not in tail and tail.isdigit():
                used.append(int(tail))
        sequence = max(used, default=0) + 1
        while f"{prefix}{sequence:02d}" in codes:
            sequence += 1
        return f"{prefix}{sequence:02d}"

    branch_base = {
        "asset": 1000,
        "liability": 2000,
        "equity": 3000,
        "revenue": 4000,
        "expense": 6000,
    }.get(account_type, 9000)

    candidate = branch_base
    branch_limit = branch_base + 1000
    while str(candidate) in codes and candidate < branch_limit:
        candidate += 100
    if candidate >= branch_limit:
        candidate = 9000
        while str(candidate) in codes:
            candidate += 100
    return str(candidate)


def _r54_normalize_text(value) -> str:
    value = str(
        value or ""
    ).lower()

    value = _r54_unicode.normalize(
        "NFD",
        value,
    )

    value = "".join(
        char
        for char in value
        if _r54_unicode.category(char)
        != "Mn"
    )

    return _r54_re.sub(
        r"[^a-z0-9]+",
        " ",
        value,
    ).strip()


def _r54_name_tokens(value) -> set[str]:
    stop = {
        "spa",
        "ltda",
        "limitada",
        "sa",
        "s",
        "a",
        "sociedad",
        "comercial",
        "industrial",
        "agricola",
        "empresa",
        "servicios",
        "inversiones",
        "de",
        "del",
        "la",
        "las",
        "los",
        "el",
        "y",
        "cia",
        "compania",
        "chile",
    }

    return {
        token
        for token
        in _r54_normalize_text(
            value
        ).split()
        if len(token) >= 3
        and token not in stop
    }


def _r54_invoice_movement_score(
    payable: dict,
    movement: dict,
) -> dict:
    payable_balance = Decimal(
        str(
            payable.get("balance")
            or 0
        )
    )

    movement_balance = Decimal(
        str(
            movement.get(
                "remaining_amount"
            )
            or 0
        )
    )

    diff = abs(
        payable_balance
        -
        movement_balance
    )

    score = 0
    reasons = []

    # Monto
    if diff <= Decimal("0.50"):
        score += 55
        reasons.append(
            "Monto exacto"
        )

    elif diff <= Decimal("100"):
        score += 50
        reasons.append(
            "Diferencia <= $100"
        )

    elif diff <= Decimal("1000"):
        score += 40
        reasons.append(
            "Diferencia <= $1.000"
        )

    elif (
        payable_balance > 0
        and diff
        / payable_balance
        <= Decimal("0.005")
    ):
        score += 34
        reasons.append(
            "Diferencia <= 0,5%"
        )

    elif (
        payable_balance > 0
        and diff
        / payable_balance
        <= Decimal("0.02")
    ):
        score += 20
        reasons.append(
            "Monto cercano"
        )

    movement_text = (
        str(
            movement.get(
                "description"
            )
            or ""
        )
        + " "
        + str(
            movement.get(
                "reference"
            )
            or ""
        )
    )

    supplier_name = str(
        payable.get(
            "supplier_name"
        )
        or ""
    )

    supplier_tokens = (
        _r54_name_tokens(
            supplier_name
        )
    )

    movement_tokens = (
        _r54_name_tokens(
            movement_text
        )
    )

    overlap = (
        supplier_tokens
        &
        movement_tokens
    )

    if overlap:
        important = sorted(
            overlap,
            key=len,
            reverse=True,
        )

        if any(
            len(token) >= 5
            for token in important
        ):
            score += 25
        else:
            score += 18

        reasons.append(
            "Proveedor: "
            + ", ".join(
                important[:3]
            )
        )

    supplier_norm = (
        _r54_normalize_text(
            supplier_name
        )
    )

    movement_norm = (
        _r54_normalize_text(
            movement_text
        )
    )

    if (
        supplier_norm
        and (
            supplier_norm
            in movement_norm
            or movement_norm
            in supplier_norm
        )
    ):
        score += 5
        reasons.append(
            "Nombre proveedor"
        )

    # RUT
    supplier_rut = _r54_re.sub(
        r"\D",
        "",
        str(
            payable.get(
                "supplier_rut"
            )
            or ""
        ),
    )

    movement_digits = (
        _r54_re.sub(
            r"\D",
            "",
            movement_text,
        )
    )

    if (
        len(supplier_rut) >= 7
        and supplier_rut
        in movement_digits
    ):
        score += 15
        reasons.append(
            "RUT proveedor"
        )

    # Folio
    folio = str(
        payable.get(
            "folio"
        )
        or ""
    ).strip()

    if (
        folio
        and folio
        in movement_text
    ):
        score += 15
        reasons.append(
            "Folio"
        )

    # Fecha
    issue_date = payable.get(
        "issue_date"
    )

    tx_date = movement.get(
        "tx_date"
    )

    if (
        issue_date
        and tx_date
    ):
        days = abs(
            (
                tx_date
                -
                issue_date
            ).days
        )

        if days == 0:
            score += 15
            reasons.append(
                "Misma fecha"
            )

        elif days <= 3:
            score += 12
            reasons.append(
                f"Fecha {days} día(s)"
            )

        elif days <= 7:
            score += 8
            reasons.append(
                f"Fecha {days} días"
            )

        elif days <= 30:
            score += 3
            reasons.append(
                "Fecha cercana"
            )

    score = min(
        100,
        int(score),
    )

    if score >= 95:
        confidence = "HIGH"

    elif score >= 75:
        confidence = "PROBABLE"

    else:
        confidence = "REVIEW"

    return {
        "score": score,
        "confidence": confidence,
        "reasons": reasons,
        "suggested_amount": min(
            payable_balance,
            movement_balance,
        ),
        "amount_difference": diff,
    }


@router.get(
    "/reconciliation/open-payables"
)
def reconciliation_open_payables_r54(
    legal_entity_id: int | None = None,
    q: str | None = Query(
        None,
        max_length=120,
    ),
    limit: int = Query(
        500,
        ge=1,
        le=1000,
    ),
    me=Depends(get_current_user),
):
    _role(
        me,
        VIEW_ROLES,
    )

    search = (
        str(
            q or ""
        ).strip()
    )

    params = {
        "entity":
            legal_entity_id,
        "q":
            f"%{search}%",
        "limit":
            limit,
    }

    with get_connection() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                  g.id_gasto
                    AS payable_id,

                  g.id_legal_entity,

                  le.legal_name,
                  le.rut,

                  g.balance,
                  g.amount_original,
                  g.payable_status,
                  g.cuenta_code,

                  g.fecha_vencimiento
                    AS due_date,

                  d.id
                    AS document_id,

                  d.document_type,
                  d.folio,
                  d.issue_date,
                  d.total_amount,

                  p.id_proveedor
                    AS supplier_id,

                  COALESCE(
                    NULLIF(
                      p.razon_social,
                      ''
                    ),
                    NULLIF(
                      p.nombre,
                      ''
                    ),
                    g.proveedor,
                    'Proveedor'
                  )
                    AS supplier_name,

                  p.rut_normalized
                    AS supplier_rut

                FROM public.fin_gastos g

                JOIN public.fin_sii_received_documents d
                  ON d.accounts_payable_id =
                     g.id_gasto

                LEFT JOIN public.inv_proveedores p
                  ON p.id_proveedor =
                     d.supplier_id

                JOIN public.fin_legal_entities le
                  ON le.id_legal_entity =
                     g.id_legal_entity

                WHERE COALESCE(
                        g.is_active,
                        TRUE
                      ) = TRUE

                  AND g.parent_payable_id
                      IS NULL

                  AND COALESCE(
                        g.balance,
                        0
                      ) > 0.50

                  AND g.payable_status
                      NOT IN (
                        'PAID',
                        'CANCELLED'
                      )

                  AND (
                    CAST(:entity AS BIGINT) IS NULL
                    OR
                    g.id_legal_entity =
                    CAST(:entity AS BIGINT)
                  )

                  AND (
                    :q = '%%'
                    OR
                    COALESCE(
                      p.razon_social,
                      p.nombre,
                      g.proveedor,
                      ''
                    ) ILIKE :q

                    OR
                    COALESCE(
                      p.rut_normalized,
                      ''
                    ) ILIKE :q

                    OR
                    COALESCE(
                      d.folio,
                      ''
                    ) ILIKE :q
                  )

                ORDER BY
                  CASE
                    WHEN g.fecha_vencimiento
                         IS NULL
                    THEN 1
                    ELSE 0
                  END,

                  g.fecha_vencimiento,
                  d.issue_date DESC,
                  g.id_gasto DESC

                LIMIT :limit
                """
            ),
            params,
        ).mappings().all()

    return {
        "ok": True,
        "items": [
            dict(row)
            for row in rows
        ],
        "total": len(rows),
    }


@router.get(
    "/reconciliation/payables/"
    "{payable_id}/movement-candidates"
)
def payable_movement_candidates_r54(
    payable_id: int,
    bank_account_id: int | None = None,
    limit: int = Query(
        80,
        ge=1,
        le=300,
    ),
    me=Depends(get_current_user),
):
    _role(
        me,
        VIEW_ROLES,
    )

    with get_connection() as conn:
        payable_row = conn.execute(
            text(
                """
                SELECT
                  g.id_gasto
                    AS payable_id,

                  g.id_legal_entity,

                  le.legal_name,
                  le.rut,

                  g.balance,
                  g.amount_original,
                  g.payable_status,
                  g.cuenta_code,

                  g.fecha_vencimiento
                    AS due_date,

                  d.id
                    AS document_id,

                  d.document_type,
                  d.folio,
                  d.issue_date,
                  d.total_amount,

                  p.id_proveedor
                    AS supplier_id,

                  COALESCE(
                    NULLIF(
                      p.razon_social,
                      ''
                    ),
                    NULLIF(
                      p.nombre,
                      ''
                    ),
                    g.proveedor,
                    'Proveedor'
                  )
                    AS supplier_name,

                  p.rut_normalized
                    AS supplier_rut

                FROM public.fin_gastos g

                JOIN public.fin_sii_received_documents d
                  ON d.accounts_payable_id =
                     g.id_gasto

                LEFT JOIN public.inv_proveedores p
                  ON p.id_proveedor =
                     d.supplier_id

                JOIN public.fin_legal_entities le
                  ON le.id_legal_entity =
                     g.id_legal_entity

                WHERE g.id_gasto =
                      :payable

                  AND COALESCE(
                        g.is_active,
                        TRUE
                      ) = TRUE

                LIMIT 1
                """
            ),
            {
                "payable":
                    payable_id,
            },
        ).mappings().first()

        if not payable_row:
            raise HTTPException(
                status_code=404,
                detail={
                    "code":
                        "PAYABLE_NOT_FOUND",
                    "message":
                        "Factura/CxP no encontrada.",
                },
            )

        payable = dict(
            payable_row
        )

        movements = conn.execute(
            text(
                """
                WITH confirmed AS (
                  SELECT
                    bank_movement_id,
                    COALESCE(
                      SUM(
                        amount_applied
                      ),
                      0
                    ) AS applied

                  FROM public.fin_payable_bank_allocations

                  WHERE status =
                        'CONFIRMED'

                  GROUP BY
                    bank_movement_id
                )

                SELECT
                  bm.id_bank_movement,
                  bm.id_bank_account,
                  bm.tx_date,
                  bm.description,
                  bm.reference,
                  bm.amount,
                  bm.balance,

                  ba.bank_name,
                  ba.label
                    AS bank_account,

                  ba.id_legal_entity,

                  le.legal_name,
                  le.rut,

                  GREATEST(
                    ABS(
                      bm.amount
                    )
                    -
                    COALESCE(
                      confirmed.applied,
                      0
                    ),
                    0
                  )
                    AS remaining_amount

                FROM public.fin_bank_movements bm

                JOIN public.fin_bank_accounts ba
                  ON ba.id_bank_account =
                     bm.id_bank_account

                JOIN public.fin_legal_entities le
                  ON le.id_legal_entity =
                     ba.id_legal_entity

                LEFT JOIN confirmed
                  ON confirmed.bank_movement_id =
                     bm.id_bank_movement

                WHERE ba.id_legal_entity =
                      :entity

                  AND ba.is_active =
                      TRUE

                  AND bm.status =
                      'PENDING'

                  AND bm.amount < 0

                  AND bm.cuenta_code
                      IS NULL

                  AND (
                    CAST(:bank_account AS BIGINT) IS NULL
                    OR
                    bm.id_bank_account =
                    CAST(:bank_account AS BIGINT)
                  )

                  AND GREATEST(
                    ABS(
                      bm.amount
                    )
                    -
                    COALESCE(
                      confirmed.applied,
                      0
                    ),
                    0
                  ) > 0.50

                  AND NOT EXISTS (
                    SELECT 1

                    FROM public.fin_gastos direct_gasto

                    WHERE direct_gasto.bank_movement_id =
                          bm.id_bank_movement

                      AND COALESCE(
                            direct_gasto.is_active,
                            TRUE
                          ) = TRUE
                  )

                ORDER BY
                  bm.tx_date DESC,
                  bm.id_bank_movement DESC

                LIMIT 500
                """
            ),
            {
                "entity":
                    payable[
                        "id_legal_entity"
                    ],

                "bank_account":
                    bank_account_id,
            },
        ).mappings().all()

    items = []

    for row in movements:
        movement = dict(row)

        scoring = (
            _r54_invoice_movement_score(
                payable,
                movement,
            )
        )

        movement.update(
            scoring
        )

        items.append(
            movement
        )

    items.sort(
        key=lambda item: (
            -int(
                item.get(
                    "score"
                )
                or 0
            ),
            Decimal(
                str(
                    item.get(
                        "amount_difference"
                    )
                    or 0
                )
            ),
            str(
                item.get(
                    "tx_date"
                )
                or ""
            ),
        )
    )

    return {
        "ok": True,
        "payable": payable,
        "items": items[:limit],
        "bank_account_id":
            bank_account_id,
    }


@router.get(
    "/chart-of-accounts"
)
def chart_of_accounts_r54(
    q: str | None = Query(
        None,
        max_length=120,
    ),
    me=Depends(get_current_user),
):
    _role(
        me,
        VIEW_ROLES,
    )

    search = str(
        q or ""
    ).strip()

    params = {
        "q":
            f"%{search}%",
    }

    with get_connection() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                  code,
                  name,
                  type,
                  classification,
                  description,
                  parent_code,
                  COALESCE(
                    is_active,
                    TRUE
                  ) AS is_active

                FROM public.plan_cuentas

                WHERE COALESCE(
                        is_active,
                        TRUE
                      ) = TRUE

                  AND (
                    :q = '%%'
                    OR code ILIKE :q
                    OR name ILIKE :q
                    OR COALESCE(
                         classification,
                         ''
                       ) ILIKE :q
                  )

                ORDER BY code
                """
            ),
            params,
        ).mappings().all()

    return {
        "ok": True,
        "items": [
            dict(row)
            for row in rows
        ],
        "total": len(rows),
    }


@router.get(
    "/chart-of-accounts/next-code"
)
def next_chart_account_code_r54(
    parent_code: str | None = Query(None, max_length=32),
    account_type: str = Query("expense", max_length=40),
    me=Depends(get_current_user),
):
    _role(me, RECONCILE_ROLES)
    parent = str(parent_code or "").strip() or None
    normalized_type = str(account_type or "expense").strip().lower()

    with get_connection() as conn:
        with conn.begin():
            if parent:
                parent_row = conn.execute(
                    text(
                        """
                        SELECT type
                        FROM public.plan_cuentas
                        WHERE code=:code
                          AND COALESCE(is_active, TRUE)=TRUE
                        """
                    ),
                    {"code": parent},
                ).first()
                if not parent_row:
                    raise HTTPException(
                        status_code=404,
                        detail="Cuenta padre no encontrada.",
                    )
                normalized_type = str(parent_row[0])

            code = _next_chart_account_code(
                conn,
                parent,
                normalized_type,
            )

    return {"ok": True, "code": code}


@router.post(
    "/chart-of-accounts"
)
def create_chart_account_r54(
    body: ChartAccountCreateR54,
    me=Depends(get_current_user),
):
    _role(
        me,
        RECONCILE_ROLES,
    )

    requested_code = str(
        body.code
        or ""
    ).strip()
    name = body.name.strip()
    account_type = (
        body.type.strip()
    )

    classification = (
        str(
            body.classification
            or ""
        ).strip()
        or None
    )

    parent = (
        str(
            body.parent_code
            or ""
        ).strip()
        or None
    )

    description = (
        str(
            body.description
            or ""
        ).strip()
        or None
    )

    with get_connection() as conn:
        with conn.begin():
            code = requested_code

            if parent:
                parent_row = conn.execute(
                    text(
                        """
                        SELECT code, type, classification
                        FROM public.plan_cuentas
                        WHERE code=:code
                          AND COALESCE(is_active, TRUE)=TRUE
                        """
                    ),
                    {"code": parent},
                ).mappings().first()

                if not parent_row:
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "code": "PUC_PARENT_INVALID",
                            "message": "La cuenta padre no existe o está inactiva.",
                        },
                    )

                account_type = str(parent_row["type"])
                classification = (
                    str(parent_row["classification"] or "").strip()
                    or classification
                )

            if not code:
                code = _next_chart_account_code(
                    conn,
                    parent,
                    account_type,
                )

            exists = conn.execute(
                text(
                    """
                    SELECT 1
                    FROM public.plan_cuentas
                    WHERE code=:code
                    LIMIT 1
                    """
                ),
                {
                    "code": code,
                },
            ).first()

            if exists:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code":
                            "PUC_DUPLICATE_CODE",
                        "message":
                            "Ya existe una cuenta "
                            "con ese código.",
                    },
                )

            valid_types = {
                str(value)
                for value
                in conn.execute(
                    text(
                        """
                        SELECT DISTINCT type
                        FROM public.plan_cuentas
                        WHERE COALESCE(
                                is_active,
                                TRUE
                              )=TRUE
                          AND type IS NOT NULL
                        """
                    )
                ).scalars().all()
            }

            valid_types.update(
                {
                    "expense",
                    "revenue",
                    "asset",
                    "liability",
                    "equity",
                }
            )

            if (
                account_type
                not in valid_types
            ):
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code":
                            "PUC_INVALID_TYPE",
                        "message":
                            "Tipo de cuenta inválido.",
                    },
                )

            conn.execute(
                text(
                    """
                    INSERT INTO public.plan_cuentas(
                      code,
                      name,
                      type,
                      classification,
                      description,
                      parent_code,
                      is_active
                    )
                    VALUES(
                      :code,
                      :name,
                      :type,
                      :classification,
                      :description,
                      :parent,
                      TRUE
                    )
                    """
                ),
                {
                    "code":
                        code,
                    "name":
                        name,
                    "type":
                        account_type,
                    "classification":
                        classification,
                    "description":
                        description,
                    "parent":
                        parent,
                },
            )

    return {
        "ok": True,
        "item": {
            "code":
                code,
            "name":
                name,
            "type":
                account_type,
            "classification":
                classification,
            "parent_code":
                parent,
        },
    }


# === GD FINANCE R54 PREMIUM INVOICE FIRST END ===
