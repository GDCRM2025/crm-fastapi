from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy import text

from backend.core.activity_log import log_activity
from backend.core.db import get_connection
from backend.gd_sii.auth import authenticate_cached
from backend.gd_sii.certificate import load_pkcs12
from backend.gd_sii.exceptions import SIIError, SIINotSupportedError
from backend.gd_sii.rut import normalize_chilean_rut
from backend.gd_sii.service import SIIReceivedDocumentsService
from backend.gd_sii.storage import PrivateXMLStorage
from backend.gd_sii.vault import SIICredentialVault
from backend.routers.auth import get_current_user

router = APIRouter(prefix="/api/finance/sii", tags=["finance-sii"])
service = SIIReceivedDocumentsService()
VIEW_ROLES = {"SUPERADMIN", "ADMIN", "FINANZAS"}
MANAGE_ROLES = {"SUPERADMIN", "ADMIN"}


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
          SELECT e.id_legal_entity,e.legal_code,e.legal_name,e.rut,c.environment,c.status,c.certificate_filename,
            c.certificate_subject,c.certificate_serial,c.certificate_valid_from,c.certificate_valid_to,
            c.last_auth_at,c.last_sync_at,c.last_sync_status,c.last_error_code,c.last_error_safe
          FROM fin_legal_entities e LEFT JOIN wi_sii_connections c USING(id_legal_entity)
          WHERE e.is_active IS TRUE ORDER BY e.legal_name
        """)).mappings().all()
    items = []
    for row in rows:
        item = dict(row)
        item["name"] = item["legal_name"]
        item["rut_normalized"] = normalize_chilean_rut(item["rut"])
        items.append(item)
    return {"ok": True, "items": items}


@router.post("/connections/{legal_entity_id}/certificate")
async def upload_certificate(
    legal_entity_id: int,
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
            entity = conn.execute(text("SELECT rut FROM fin_legal_entities WHERE id_legal_entity=:id AND is_active"), {"id": legal_entity_id}).scalar()
            if not entity:
                raise HTTPException(404, "Entidad legal no encontrada")
            body, dv = normalize_chilean_rut(entity).split("-", 1)
            secret_key = f"legal-entity-{legal_entity_id}.credential"
            vault.put_certificate(conn, secret_key=secret_key, certificate=data, password=password, actor_id=me.get("id"))
            conn.execute(text("""
              INSERT INTO wi_sii_connections(id_legal_entity,environment,rut_company,dv_company,status,certificate_secret_key,
                certificate_filename,certificate_subject,certificate_serial,certificate_valid_from,certificate_valid_to,created_by,updated_by)
              VALUES (:id,:env,:rut,:dv,'CONFIGURED',:secret,:filename,:subject,:serial,:valid_from,:valid_to,:actor,:actor)
              ON CONFLICT(id_legal_entity) DO UPDATE SET environment=EXCLUDED.environment,status='CONFIGURED',
                certificate_secret_key=EXCLUDED.certificate_secret_key,certificate_filename=EXCLUDED.certificate_filename,
                certificate_subject=EXCLUDED.certificate_subject,certificate_serial=EXCLUDED.certificate_serial,
                certificate_valid_from=EXCLUDED.certificate_valid_from,certificate_valid_to=EXCLUDED.certificate_valid_to,
                last_error_code=NULL,last_error_safe=NULL,updated_at=now(),updated_by=EXCLUDED.updated_by
            """), {"id": legal_entity_id, "env": environment, "rut": body, "dv": dv, "secret": secret_key,
                    "filename": filename, **meta, "actor": me.get("id")})
            log_activity(conn, username=me.get("username"), user_id=me.get("id"), role=me.get("role"),
                         action="SII_CERTIFICATE_UPDATED", entity_type="wi_sii_connection", entity_id=legal_entity_id,
                         meta={"environment": environment, "filename": filename, "serial": meta["serial"]})
            conn.commit()
    except HTTPException:
        raise
    except Exception as exc:
        raise _safe(exc)
    return {"ok": True, "status": "CONFIGURED", "certificate": meta}


@router.post("/connections/{legal_entity_id}/test")
def test_connection(legal_entity_id: int, me=Depends(get_current_user)):
    _role(me, MANAGE_ROLES)
    try:
        vault = SIICredentialVault()
        with get_connection() as conn:
            row = conn.execute(text("SELECT * FROM wi_sii_connections WHERE id_legal_entity=:id"), {"id": legal_entity_id}).mappings().first()
            if not row or not row.get("certificate_secret_key"):
                raise HTTPException(404, "Conexion SII no configurada")
            pfx, password = vault.load_certificate_internal(conn, row["certificate_secret_key"])
            loaded = load_pkcs12(pfx, password)
            authenticate_cached(legal_entity_id, row["environment"], loaded)
            conn.execute(text("UPDATE wi_sii_connections SET status='CONNECTED',last_auth_at=now(),last_error_code=NULL,last_error_safe=NULL,updated_at=now() WHERE id_legal_entity=:id"), {"id": legal_entity_id})
            log_activity(conn, username=me.get("username"), user_id=me.get("id"), role=me.get("role"), action="SII_CONNECTION_TESTED",
                         entity_type="wi_sii_connection", entity_id=legal_entity_id, meta={"status": "CONNECTED", "environment": row["environment"]})
            conn.commit()
    except HTTPException:
        raise
    except Exception as exc:
        try:
            with get_connection() as conn:
                conn.execute(text("UPDATE wi_sii_connections SET status='ERROR',last_error_code=:code,last_error_safe=:safe,updated_at=now() WHERE id_legal_entity=:id"), {"id": legal_entity_id, "code": getattr(exc, "code", "SII_AUTH_FAILED"), "safe": getattr(exc, "safe_message", "Fallo de conexion SII")})
                conn.commit()
        except Exception:
            pass
        raise _safe(exc)
    return {"ok": True, "status": "CONNECTED"}


@router.post("/connections/{legal_entity_id}/disconnect")
def disconnect(legal_entity_id: int, me=Depends(get_current_user)):
    _role(me, MANAGE_ROLES)
    try:
        vault = SIICredentialVault()
        with get_connection() as conn:
            key = conn.execute(text("SELECT certificate_secret_key FROM wi_sii_connections WHERE id_legal_entity=:id"), {"id": legal_entity_id}).scalar()
            if key: vault.delete(conn, key)
            conn.execute(text("UPDATE wi_sii_connections SET status='DISCONNECTED',certificate_secret_key=NULL,certificate_filename=NULL,certificate_subject=NULL,certificate_serial=NULL,certificate_valid_from=NULL,certificate_valid_to=NULL,updated_at=now() WHERE id_legal_entity=:id"), {"id": legal_entity_id})
            conn.commit()
    except Exception as exc:
        raise _safe(exc)
    return {"ok": True, "status": "DISCONNECTED"}


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
        audit = conn.execute(text("SELECT id,created_at,action,user_id,meta FROM activity_log WHERE entity_type IN ('supplier','inv_proveedor','fin_sii_received_document') AND (entity_id=:id OR meta->>'supplier_id'=:sid) ORDER BY created_at DESC LIMIT 100"), {"id": supplier_id, "sid": str(supplier_id)}).mappings().all()
    return {"ok": True, "item": dict(supplier), "documents": [dict(r) for r in documents], "audit": [dict(r) for r in audit]}


@router.get("/health")
def health(me=Depends(get_current_user)):
    _role(me, VIEW_ROLES)
    with get_connection() as conn:
        row = conn.execute(text("SELECT count(*) AS configured, count(*) FILTER(WHERE status='CONNECTED') AS connected FROM wi_sii_connections WHERE status<>'DISCONNECTED'" )).mappings().one()
    return {"status": "ok", "configured_entities": int(row["configured"]), "connected_entities": int(row["connected"]), "automated_rcv_api": False}
