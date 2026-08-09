from __future__ import annotations

import json
import mimetypes
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.greenie_schema import acquire_greenie_schema_lock
from backend.core.whatsapp_window import require_open_customer_window
from backend.routers.auth import get_current_user
from backend.gd_intelligence.tracking import attach_lead_attribution


router = APIRouter(
    prefix="/gia/whatsapp",
    tags=["WhatsApp Greenie Comercial"],
    dependencies=[Depends(get_current_user)],
)
ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env"
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY = False


class SelectLeadBody(BaseModel):
    lead_id: int


class RoutingBody(BaseModel):
    assignment_mode: str = Field(pattern="^(direct|manual)$")
    executive_name: str | None = Field(default=None, max_length=180)
    is_dispatch: bool = False


class AssignExecutiveBody(BaseModel):
    executive_id: str | None = None
    executive_name: str = Field(min_length=2, max_length=180)
    lead_id: int | None = None


class FollowupBody(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    kind: str = Field(default="NOTE", max_length=24)
    title: str | None = Field(default=None, max_length=120)


def _env(name: str, default: str = "") -> str:
    try:
        value = dotenv_values(ENV_FILE).get(name)
    except Exception:
        value = None
    if value is None or not str(value).strip():
        value = os.getenv(name, default)
    return str(value or default).strip()


def _cols(db: Session, table: str) -> set[str]:
    rows = db.execute(text("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name=:table
    """), {"table": table}).mappings().all()
    return {str(row["column_name"]) for row in rows}


def _table_exists(db: Session, table: str) -> bool:
    return bool(db.execute(text("SELECT to_regclass(:name)"), {"name": f"public.{table}"}).scalar())


def _ensure_schema(db: Session) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return

    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        try:
            acquire_greenie_schema_lock(db)
            if not _table_exists(db, "whatsapp_channels"):
                raise HTTPException(
                    status_code=503,
                    detail="whatsapp_channels no existe",
                )
            if not _table_exists(db, "whatsapp_conversations"):
                raise HTTPException(
                    status_code=503,
                    detail="whatsapp_conversations no existe",
                )

            db.execute(text("""
                ALTER TABLE whatsapp_channels
                ADD COLUMN IF NOT EXISTS assignment_mode TEXT NOT NULL DEFAULT 'direct'
            """))
            db.execute(text("""
                ALTER TABLE whatsapp_channels
                ADD COLUMN IF NOT EXISTS is_dispatch BOOLEAN NOT NULL DEFAULT FALSE
            """))
            db.execute(text("""
                ALTER TABLE whatsapp_channels
                ADD COLUMN IF NOT EXISTS default_user_id TEXT
            """))
            db.execute(text("""
                ALTER TABLE whatsapp_conversations
                ADD COLUMN IF NOT EXISTS selected_lead_id BIGINT
            """))
            db.execute(text("""
                ALTER TABLE whatsapp_conversations
                ADD COLUMN IF NOT EXISTS assigned_user_id TEXT
            """))
            db.execute(text("""
                ALTER TABLE whatsapp_conversations
                ADD COLUMN IF NOT EXISTS assigned_user_name TEXT
            """))

            if _table_exists(db, "whatsapp_messages"):
                for ddl in (
                    "ALTER TABLE whatsapp_messages ADD COLUMN IF NOT EXISTS media_id TEXT",
                    "ALTER TABLE whatsapp_messages ADD COLUMN IF NOT EXISTS mime_type TEXT",
                    "ALTER TABLE whatsapp_messages ADD COLUMN IF NOT EXISTS filename TEXT",
                    "ALTER TABLE whatsapp_messages ADD COLUMN IF NOT EXISTS caption TEXT",
                    "ALTER TABLE whatsapp_messages ADD COLUMN IF NOT EXISTS media_size BIGINT",
                ):
                    db.execute(text(ddl))
            db.commit()
        except Exception:
            db.rollback()
            raise
        _SCHEMA_READY = True


def _phone9(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[-9:] if len(digits) >= 9 else digits


def _sql_col(alias: str, cols: set[str], name: str, fallback: str = "NULL") -> str:
    return f"{alias}.{name}" if name in cols else fallback


def _conversation(db: Session, conversation_id: int) -> dict[str, Any]:
    row = db.execute(text("""
        SELECT
            c.*,
            ct.wa_id,
            ct.profile_name,
            ch.brand_name AS channel_brand_name,
            ch.executive_name AS channel_executive_name,
            COALESCE(ch.assignment_mode, 'direct') AS assignment_mode,
            COALESCE(ch.is_dispatch, FALSE) AS is_dispatch,
            ch.display_phone_number AS channel_phone
        FROM whatsapp_conversations c
        JOIN whatsapp_contacts ct ON ct.id=c.contact_id
        LEFT JOIN whatsapp_channels ch
          ON ch.phone_number_id=c.phone_number_id
         AND ch.enabled=TRUE
        WHERE c.id=:id
        LIMIT 1
    """), {"id": conversation_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Conversación no encontrada")
    return dict(row)


def _lead_rows(db: Session, wa_id: str) -> list[dict[str, Any]]:
    if not _table_exists(db, "leads"):
        return []

    lead_cols = _cols(db, "leads")
    marca_exists = _table_exists(db, "marcas")
    estado_exists = _table_exists(db, "estados_lead")
    comuna_exists = _table_exists(db, "comunas")

    marca_cols = _cols(db, "marcas") if marca_exists else set()
    estado_cols = _cols(db, "estados_lead") if estado_exists else set()
    comuna_cols = _cols(db, "comunas") if comuna_exists else set()

    marca_expr = (
        "m.marca" if "marca" in marca_cols
        else "m.nombre" if "nombre" in marca_cols
        else "m.nombre_marca" if "nombre_marca" in marca_cols
        else "NULL::text"
    )
    estado_expr = (
        "es.nombre" if "nombre" in estado_cols
        else "es.estado" if "estado" in estado_cols
        else "es.nombre_estado" if "nombre_estado" in estado_cols
        else "NULL::text"
    )
    estado_color_expr = "es.color" if "color" in estado_cols else "'#64748b'::text"
    comuna_expr = (
        "co.nombre" if "nombre" in comuna_cols
        else "co.comuna" if "comuna" in comuna_cols
        else "co.nombre_comuna" if "nombre_comuna" in comuna_cols
        else "NULL::text"
    )

    marca_join = "LEFT JOIN public.marcas m ON m.id_marca=l.id_marca" if marca_exists and "id_marca" in lead_cols and "id_marca" in marca_cols else ""
    estado_join = "LEFT JOIN public.estados_lead es ON es.id_estado=l.id_estado" if estado_exists and "id_estado" in lead_cols and "id_estado" in estado_cols else ""
    comuna_join = "LEFT JOIN public.comunas co ON co.id_comuna=l.id_comuna" if comuna_exists and "id_comuna" in lead_cols and "id_comuna" in comuna_cols else ""

    if not marca_join:
        marca_expr = "NULL::text"
    if not estado_join:
        estado_expr = "NULL::text"
        estado_color_expr = "'#64748b'::text"
    if not comuna_join:
        comuna_expr = "NULL::text"

    is_deleted = "AND COALESCE(l.is_deleted,FALSE)=FALSE" if "is_deleted" in lead_cols else ""
    created = _sql_col("l", lead_cols, "created_at", "NULL::timestamptz")
    updated = _sql_col("l", lead_cols, "updated_at", created)
    phone9 = _phone9(wa_id)
    if not phone9:
        return []

    phone_col = next((name for name in ("telefono", "phone", "celular") if name in lead_cols), None)
    if not phone_col:
        return []

    select = f"""
        l.id_lead,
        {_sql_col('l', lead_cols, 'cliente', "''::text")} AS cliente,
        {_sql_col('l', lead_cols, 'email', "NULL::text")} AS email,
        {_sql_col('l', lead_cols, phone_col, "NULL::text")} AS telefono,
        {_sql_col('l', lead_cols, 'direccion', "NULL::text")} AS direccion,
        {_sql_col('l', lead_cols, 'id_marca', 'NULL::bigint')} AS id_marca,
        {_sql_col('l', lead_cols, 'id_estado', 'NULL::bigint')} AS id_estado,
        {_sql_col('l', lead_cols, 'id_comuna', 'NULL::bigint')} AS id_comuna,
        {_sql_col('l', lead_cols, 'fecha_evento', 'NULL::date')} AS fecha_evento,
        {_sql_col('l', lead_cols, 'monto_cotizado', '0::numeric')} AS monto_cotizado,
        {_sql_col('l', lead_cols, 'plataforma', "NULL::text")} AS plataforma,
        {_sql_col('l', lead_cols, 'notas', "NULL::text")} AS notas,
        {_sql_col('l', lead_cols, 'num_cotizacion', "NULL::text")} AS num_cotizacion,
        {_sql_col('l', lead_cols, 'cotizacion_pdf_url', "NULL::text")} AS cotizacion_pdf_url,
        {_sql_col('l', lead_cols, 'seguimiento_at', 'NULL::timestamptz')} AS seguimiento_at,
        {_sql_col('l', lead_cols, 'calendar_start', 'NULL::timestamptz')} AS calendar_start,
        {_sql_col('l', lead_cols, 'calendar_end', 'NULL::timestamptz')} AS calendar_end,
        {_sql_col('l', lead_cols, 'calendar_html_link', "NULL::text")} AS calendar_html_link,
        {created} AS created_at,
        {updated} AS updated_at,
        COALESCE({marca_expr}, '') AS marca,
        COALESCE({estado_expr}, '') AS estado,
        COALESCE({estado_color_expr}, '#64748b') AS estado_color,
        COALESCE({comuna_expr}, '') AS comuna
    """

    order_status = f"UPPER(COALESCE({estado_expr},''))" if estado_join else "''"
    rows = db.execute(text(f"""
        SELECT {select}
        FROM public.leads l
        {marca_join}
        {estado_join}
        {comuna_join}
        WHERE RIGHT(regexp_replace(COALESCE(l.{phone_col}::text,''), '[^0-9]', '', 'g'), 9)=:phone9
          {is_deleted}
        ORDER BY
          CASE WHEN {order_status} LIKE '%CONFIRM%' THEN 1
               WHEN {order_status} LIKE '%DECLIN%' THEN 2
               ELSE 0 END,
          {_sql_col('l', lead_cols, 'fecha_evento', 'NULL::date')} DESC NULLS LAST,
          {created} DESC NULLS LAST,
          l.id_lead DESC
        LIMIT 100
    """), {"phone9": phone9}).mappings().all()
    return [dict(row) for row in rows]


def _quote_rows(db: Session, lead_ids: list[int]) -> list[dict[str, Any]]:
    if not lead_ids or not _table_exists(db, "cotizaciones"):
        return []
    cols = _cols(db, "cotizaciones")
    if "id_lead" not in cols:
        return []

    def pick(candidates: tuple[str, ...], fallback: str) -> str:
        for name in candidates:
            if name in cols:
                return f"q.{name}"
        return fallback

    id_expr = pick(("id_cotizacion", "id"), "NULL::bigint")
    number_expr = pick(("numero", "num_cotizacion", "numero_cotizacion"), "NULL::text")
    amount_expr = pick(("total", "total_final", "monto_total", "subtotal_productos", "monto"), "0::numeric")
    pdf_expr = pick(("pdf_path", "pdf_url", "cotizacion_pdf_url", "url_pdf"), "NULL::text")
    date_expr = pick(("fecha", "fecha_cotizacion", "created_at"), "NULL::timestamptz")
    brand_expr = pick(("marca", "brand_name"), "NULL::text")
    status_expr = pick(("estado", "status"), "NULL::text")

    rows = db.execute(text(f"""
        SELECT
            {id_expr} AS id_cotizacion,
            q.id_lead,
            {number_expr} AS numero,
            {amount_expr} AS total,
            {pdf_expr} AS pdf_url,
            {date_expr} AS fecha,
            {brand_expr} AS marca,
            {status_expr} AS estado
        FROM public.cotizaciones q
        WHERE q.id_lead = ANY(:ids)
        ORDER BY {date_expr} DESC NULLS LAST, {id_expr} DESC NULLS LAST
        LIMIT 200
    """), {"ids": lead_ids}).mappings().all()
    return [dict(row) for row in rows]


def _statuses(db: Session) -> list[dict[str, Any]]:
    if not _table_exists(db, "estados_lead"):
        return []
    cols = _cols(db, "estados_lead")
    id_col = next((name for name in ("id_estado", "id") if name in cols), None)
    name_col = next((name for name in ("nombre", "estado", "nombre_estado") if name in cols), None)
    if not id_col or not name_col:
        return []
    color_expr = "color" if "color" in cols else "'#64748b'::text"
    rows = db.execute(text(f"""
        SELECT {id_col} AS id_estado,
               {name_col} AS nombre,
               COALESCE({color_expr}, '#64748b') AS color
        FROM public.estados_lead
        ORDER BY {id_col}
    """)).mappings().all()
    return [dict(row) for row in rows]


def _executives(db: Session) -> list[dict[str, Any]]:
    if not _table_exists(db, "usuarios"):
        return []
    cols = _cols(db, "usuarios")
    id_col = next((c for c in ("id_usuario", "id") if c in cols), None)
    name_col = next((c for c in ("nombre", "name", "username", "email") if c in cols), None)
    if not id_col or not name_col:
        return []
    role_col = next((c for c in ("rol", "role", "cargo") if c in cols), None)
    where = ""
    if role_col:
        where = f"WHERE UPPER(COALESCE({role_col}::text,'')) LIKE ANY(ARRAY['%EJECUTIV%','%VENTAS%','%ADMIN%'])"
    rows = db.execute(text(f"""
        SELECT {id_col}::text AS id, {name_col}::text AS nombre
        FROM public.usuarios
        {where}
        ORDER BY {name_col}
        LIMIT 200
    """)).mappings().all()
    return [dict(row) for row in rows if str(row.get("nombre") or "").strip()]


def _lead_belongs_to_phone(db: Session, lead_id: int, wa_id: str) -> dict[str, Any]:
    phone9 = _phone9(wa_id)
    row = db.execute(text("""
        SELECT *
        FROM public.leads
        WHERE id_lead=:lead_id
          AND RIGHT(regexp_replace(COALESCE(telefono::text,''), '[^0-9]', '', 'g'), 9)=:phone9
        LIMIT 1
    """), {"lead_id": lead_id, "phone9": phone9}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="El lead no pertenece a este teléfono")
    return dict(row)


def _try_attach_web_attribution(db: Session, conversation_id: int, lead_id: int) -> dict[str, Any] | None:
    """Use an explicit Ref GD UUID only; never infer attribution from phone alone."""
    ready = db.execute(text("""
      SELECT to_regclass('public.wi_events') IS NOT NULL
         AND to_regclass('public.wi_lead_attribution') IS NOT NULL
    """)).scalar()
    if not ready:
        return None
    rows = db.execute(text("""
      SELECT body FROM public.whatsapp_messages
      WHERE conversation_id=:conversation_id AND direction='inbound' AND body ILIKE '%Ref GD:%'
      ORDER BY sent_at DESC LIMIT 20
    """), {"conversation_id": conversation_id}).scalars().all()
    import re
    for body in rows:
        match = re.search(r"Ref GD:([0-9a-fA-F-]{36})", str(body or ""))
        if not match:
            continue
        session_id = db.execute(text("""
          SELECT session_id FROM public.wi_events
          WHERE event_id=CAST(:event_id AS uuid) AND event_type='click_whatsapp'
          LIMIT 1
        """), {"event_id": match.group(1)}).scalar()
        if session_id:
            return attach_lead_attribution(db, lead_id=lead_id, session_id=session_id, method="WABA_CLICK_ID", confidence=1.0)
    return None


def _find_quote_source(db: Session, lead: dict[str, Any]) -> tuple[str, str]:
    lead_cols = _cols(db, "leads")
    candidates: list[tuple[str, str]] = []
    for col in ("cotizacion_pdf_url", "pdf_path", "pdf_url"):
        if col in lead_cols and str(lead.get(col) or "").strip():
            candidates.append((str(lead.get(col)).strip(), f"cotizacion_lead_{lead['id_lead']}.pdf"))

    if _table_exists(db, "cotizaciones"):
        cols = _cols(db, "cotizaciones")
        pdf_col = next((c for c in ("pdf_path", "pdf_url", "cotizacion_pdf_url", "url_pdf") if c in cols), None)
        if pdf_col and "id_lead" in cols:
            order_col = next((c for c in ("id_cotizacion", "created_at", "fecha") if c in cols), "id_lead")
            num_col = next((c for c in ("numero", "num_cotizacion", "numero_cotizacion") if c in cols), None)
            num_expr = f", {num_col} AS numero" if num_col else ", NULL::text AS numero"
            row = db.execute(text(f"""
                SELECT {pdf_col} AS pdf_source {num_expr}
                FROM public.cotizaciones
                WHERE id_lead=:lead_id
                  AND NULLIF(TRIM(COALESCE({pdf_col}::text,'')), '') IS NOT NULL
                ORDER BY {order_col} DESC NULLS LAST
                LIMIT 1
            """), {"lead_id": int(lead["id_lead"])}).mappings().first()
            if row and str(row.get("pdf_source") or "").strip():
                number = str(row.get("numero") or lead.get("num_cotizacion") or lead["id_lead"]).strip()
                candidates.insert(0, (str(row["pdf_source"]).strip(), f"Cotizacion_{number}.pdf"))

    if not candidates:
        raise HTTPException(status_code=404, detail="El lead no tiene una cotización PDF asociada")
    return candidates[0]


def _read_source(source: str) -> bytes:
    value = str(source or "").strip()
    if not value:
        raise HTTPException(status_code=404, detail="Ruta de cotización vacía")

    if value.startswith("http://") or value.startswith("https://"):
        try:
            request = urllib.request.Request(value, headers={"User-Agent": "Greenie/1.0"})
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read(30 * 1024 * 1024)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"No se pudo descargar la cotización: {exc}") from exc
    else:
        raw_path = Path(value)
        candidates = [raw_path]
        if not raw_path.is_absolute():
            clean = value.lstrip("/")
            if clean.startswith("crm/"):
                clean = clean[4:]
            candidates.extend([ROOT / clean, ROOT / "web" / clean.removeprefix("web/")])
        path = next((p.resolve() for p in candidates if p.exists() and p.is_file()), None)
        if not path:
            raise HTTPException(status_code=404, detail="No se encontró el archivo PDF en el servidor")
        data = path.read_bytes()

    if not data or not data.startswith(b"%PDF"):
        raise HTTPException(status_code=422, detail="El archivo asociado no es un PDF válido")
    if len(data) > 30 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="El PDF supera 30 MB")
    return data


def _meta_upload(phone_number_id: str, filename: str, data: bytes) -> str:
    token = _env("WHATSAPP_ACCESS_TOKEN")
    version = _env("WHATSAPP_GRAPH_API_VERSION", "v25.0")
    if not token:
        raise HTTPException(status_code=503, detail="WHATSAPP_ACCESS_TOKEN no configurado")

    boundary = "----GreenieBoundary7MA4YWxkTrZu0gW"
    chunks: list[bytes] = []
    chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"messaging_product\"\r\n\r\nwhatsapp\r\n".encode())
    chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"type\"\r\n\r\napplication/pdf\r\n".encode())
    chunks.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\nContent-Type: application/pdf\r\n\r\n".encode()
        + data
        + b"\r\n"
    )
    chunks.append(f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        f"https://graph.facebook.com/{version}/{phone_number_id}/media",
        data=b"".join(chunks),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"Meta rechazó la carga: {detail}") from exc
    media_id = str(payload.get("id") or "").strip()
    if not media_id:
        raise HTTPException(status_code=502, detail="Meta no devolvió media_id")
    return media_id


def _meta_send_document(phone_number_id: str, to: str, media_id: str, filename: str, caption: str) -> dict[str, Any]:
    token = _env("WHATSAPP_ACCESS_TOKEN")
    version = _env("WHATSAPP_GRAPH_API_VERSION", "v25.0")
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "document",
        "document": {"id": media_id, "filename": filename, "caption": caption[:1024]},
    }
    request = urllib.request.Request(
        f"https://graph.facebook.com/{version}/{phone_number_id}/messages",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"Meta rechazó el documento: {detail}") from exc


def _safe_statuses(db: Session) -> list[dict[str, Any]]:
    try:
        return _statuses(db)
    except Exception:
        db.rollback()
        return []


def _safe_executives(db: Session, conversation: dict[str, Any]) -> list[dict[str, Any]]:
    if not (conversation.get("is_dispatch") or conversation.get("assignment_mode") == "manual"):
        return []
    try:
        return _executives(db)
    except Exception:
        db.rollback()
        return []


@router.get("/conversations/{conversation_id}/commercial-360")
def commercial_360(
    conversation_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    _ensure_schema(db)
    conversation = _conversation(db, conversation_id)
    try:
        leads = _lead_rows(db, str(conversation["wa_id"]))
    except Exception:
        db.rollback()
        raise
    lead_ids = [int(row["id_lead"]) for row in leads]
    try:
        quotes = _quote_rows(db, lead_ids)
    except Exception:
        db.rollback()
        quotes = []
    selected = conversation.get("selected_lead_id")
    if selected not in lead_ids:
        selected = lead_ids[0] if lead_ids else None
        if selected:
            db.execute(text("""
                UPDATE whatsapp_conversations
                SET selected_lead_id=:lead_id, updated_at=now()
                WHERE id=:id
            """), {"lead_id": selected, "id": conversation_id})
            db.commit()

    quote_by_lead: dict[int, list[dict[str, Any]]] = {}
    for quote in quotes:
        try:
            key = int(quote.get("id_lead") or 0)
        except Exception:
            continue
        quote_by_lead.setdefault(key, []).append(quote)
    for lead in leads:
        lead["cotizaciones"] = quote_by_lead.get(int(lead["id_lead"]), [])

    future_events = [
        {
            "lead_id": lead["id_lead"],
            "cliente": lead.get("cliente"),
            "fecha_evento": lead.get("fecha_evento"),
            "calendar_start": lead.get("calendar_start"),
            "calendar_end": lead.get("calendar_end"),
            "calendar_html_link": lead.get("calendar_html_link"),
            "estado": lead.get("estado"),
            "marca": lead.get("marca"),
            "comuna": lead.get("comuna"),
        }
        for lead in leads
        if lead.get("fecha_evento") or lead.get("calendar_start")
    ]

    total_cotizado = sum(float(lead.get("monto_cotizado") or 0) for lead in leads)
    return {
        "ok": True,
        "conversation": conversation,
        "routing": {
            "mode": "manual" if conversation.get("is_dispatch") else str(conversation.get("assignment_mode") or "direct"),
            "is_dispatch": bool(conversation.get("is_dispatch")),
            "brand_name": conversation.get("channel_brand_name") or conversation.get("brand_code"),
            "executive_name": conversation.get("assigned_user_name") or conversation.get("channel_executive_name") or conversation.get("executive_name"),
        },
        "selected_lead_id": selected,
        "has_leads": bool(leads),
        "leads": leads,
        "cotizaciones": quotes,
        "eventos": future_events,
        "estados": _safe_statuses(db),
        "executives": _safe_executives(db, conversation),
        "summary": {
            "lead_count": len(leads),
            "quote_count": len(quotes),
            "total_cotizado": total_cotizado,
        },
    }


@router.post("/conversations/{conversation_id}/select-lead")
def select_lead(
    conversation_id: int,
    body: SelectLeadBody,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    _ensure_schema(db)
    conversation = _conversation(db, conversation_id)
    _lead_belongs_to_phone(db, body.lead_id, str(conversation["wa_id"]))
    db.execute(text("""
        UPDATE whatsapp_conversations
        SET selected_lead_id=:lead_id, updated_at=now()
        WHERE id=:id
    """), {"lead_id": body.lead_id, "id": conversation_id})
    _try_attach_web_attribution(db, conversation_id, body.lead_id)
    db.commit()
    return {"ok": True, "selected_lead_id": body.lead_id}


@router.patch("/channels/{phone_number_id}/routing")
def update_channel_routing(
    phone_number_id: str,
    body: RoutingBody,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    _ensure_schema(db)
    row = db.execute(text("""
        UPDATE whatsapp_channels
        SET assignment_mode=:mode,
            is_dispatch=:is_dispatch,
            executive_name=COALESCE(NULLIF(:executive_name,''), executive_name),
            updated_at=now()
        WHERE phone_number_id=:phone_number_id
        RETURNING brand_code, brand_name, executive_name, assignment_mode, is_dispatch
    """), {
        "phone_number_id": phone_number_id,
        "mode": body.assignment_mode,
        "is_dispatch": body.is_dispatch,
        "executive_name": body.executive_name or "",
    }).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Canal WhatsApp no encontrado")
    db.commit()
    return {"ok": True, "channel": dict(row)}


@router.post("/conversations/{conversation_id}/assign-executive")
def assign_executive(
    conversation_id: int,
    body: AssignExecutiveBody,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    _ensure_schema(db)
    conversation = _conversation(db, conversation_id)
    manual = bool(conversation.get("is_dispatch")) or str(conversation.get("assignment_mode") or "") == "manual"
    if not manual:
        raise HTTPException(status_code=400, detail="Este canal tiene asignación directa por marca")
    if body.lead_id:
        _lead_belongs_to_phone(db, body.lead_id, str(conversation["wa_id"]))
    db.execute(text("""
        UPDATE whatsapp_conversations
        SET assigned_user_id=:user_id,
            assigned_user_name=:user_name,
            selected_lead_id=COALESCE(:lead_id, selected_lead_id),
            executive_name=:user_name,
            updated_at=now()
        WHERE id=:id
    """), {
        "id": conversation_id,
        "user_id": body.executive_id,
        "user_name": body.executive_name.strip(),
        "lead_id": body.lead_id,
    })
    if body.lead_id and "id_usuario" in _cols(db, "leads"):
        db.execute(text("""
            UPDATE public.leads
            SET id_usuario=COALESCE(NULLIF(:user_id,''), :user_name), updated_at=now()
            WHERE id_lead=:lead_id
        """), {
            "user_id": body.executive_id or "",
            "user_name": body.executive_name.strip(),
            "lead_id": body.lead_id,
        })
    db.commit()
    return {"ok": True, "executive_name": body.executive_name.strip()}


@router.post("/conversations/{conversation_id}/leads/{lead_id}/followup")
def add_followup(
    conversation_id: int,
    lead_id: int,
    body: FollowupBody,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    _ensure_schema(db)
    conversation = _conversation(db, conversation_id)
    _lead_belongs_to_phone(db, lead_id, str(conversation["wa_id"]))
    cols = _cols(db, "leads")
    if "notas" not in cols:
        raise HTTPException(status_code=500, detail="leads.notas no existe")
    actor = str(user.get("name") or user.get("username") or user.get("email") or "Usuario CRM")
    now_text = datetime.now().strftime("%d/%m/%Y %H:%M")
    kind = body.kind.strip().upper()[:24]
    title = (body.title or "Seguimiento desde Greenie").strip()
    block = f"[{kind}] {now_text} · {actor} · {title}\n{body.text.strip()}"
    followup_set = ", seguimiento_at=now()" if "seguimiento_at" in cols else ""
    db.execute(text(f"""
        UPDATE public.leads
        SET notas=CASE WHEN COALESCE(notas,'')='' THEN :block ELSE notas || E'\n\n' || :block END,
            updated_at=now()
            {followup_set}
        WHERE id_lead=:lead_id
    """), {"block": block, "lead_id": lead_id})
    db.commit()
    return {"ok": True, "block": block}


@router.post("/conversations/{conversation_id}/leads/{lead_id}/send-quote")
def send_quote(
    conversation_id: int,
    lead_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    _ensure_schema(db)
    conversation = _conversation(db, conversation_id)
    require_open_customer_window(db, conversation_id)
    lead = _lead_belongs_to_phone(db, lead_id, str(conversation["wa_id"]))
    source, filename = _find_quote_source(db, lead)
    pdf = _read_source(source)
    media_id = _meta_upload(str(conversation["phone_number_id"]), filename, pdf)
    client = str(lead.get("cliente") or conversation.get("profile_name") or "cliente").strip()
    result = _meta_send_document(
        str(conversation["phone_number_id"]),
        str(conversation["wa_id"]),
        media_id,
        filename,
        f"Hola {client}, adjuntamos tu cotización.",
    )
    messages = result.get("messages") or []
    message_id = messages[0].get("id") if messages and isinstance(messages[0], dict) else None
    db.execute(text("""
        INSERT INTO whatsapp_messages(
            conversation_id, whatsapp_message_id, direction,
            message_type, body, status, sent_at, raw_payload,
            media_id, mime_type, filename, media_size
        ) VALUES (
            :conversation_id, :message_id, 'outbound',
            'document', :body, 'accepted', now(), CAST(:raw_payload AS JSONB),
            :media_id, 'application/pdf', :filename, :media_size
        )
        ON CONFLICT (whatsapp_message_id) DO NOTHING
    """), {
        "conversation_id": conversation_id,
        "message_id": message_id,
        "body": f"[Cotización enviada] {filename}",
        "raw_payload": json.dumps(result, ensure_ascii=False),
        "media_id": media_id,
        "filename": filename,
        "media_size": len(pdf),
    })
    if "notas" in _cols(db, "leads"):
        actor = str(user.get("name") or user.get("username") or "Usuario CRM")
        note = f"[WSP] {datetime.now().strftime('%d/%m/%Y %H:%M')} · {actor}\nCotización enviada por Greenie: {filename}"
        db.execute(text("""
            UPDATE public.leads
            SET notas=CASE WHEN COALESCE(notas,'')='' THEN :note ELSE notas || E'\n\n' || :note END,
                updated_at=now()
            WHERE id_lead=:lead_id
        """), {"note": note, "lead_id": lead_id})
    db.execute(text("""
        UPDATE whatsapp_conversations
        SET last_message_at=now(), selected_lead_id=:lead_id, updated_at=now()
        WHERE id=:id
    """), {"lead_id": lead_id, "id": conversation_id})
    db.commit()
    return {"ok": True, "message_id": message_id, "media_id": media_id, "filename": filename}
