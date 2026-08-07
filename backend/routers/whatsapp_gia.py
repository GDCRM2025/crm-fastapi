from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
import unicodedata
import uuid
from datetime import date, datetime, timezone
from typing import Any

from dotenv import dotenv_values
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.core.database import DATABASE_URL, SessionLocal, get_db
from backend.core.greenie_schema import acquire_greenie_schema_lock
from backend.core.whatsapp_window import require_open_customer_window
from backend.routers.auth import get_current_user
from backend.routers.whatsapp_webhook import ensure_tables as ensure_webhook_tables

router = APIRouter(
    prefix="/gia/whatsapp",
    tags=["WhatsApp Greenie"],
    dependencies=[Depends(get_current_user)],
)

BASE_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BASE_DIR / ".env"


def _runtime_env(name: str, default: str = "") -> str:
    try:
        value = dotenv_values(ENV_FILE).get(name)
    except Exception:
        value = None
    if value is None or str(value).strip() == "":
        value = os.environ.get(name, default)
    return str(value or default).strip()


def _inline_content_disposition(filename: Any, fallback: str) -> str:
    raw_name = str(filename or fallback).replace("\\", "/")
    raw_name = raw_name.rsplit("/", 1)[-1].replace("\r", "").replace("\n", "")
    if not raw_name:
        raw_name = fallback

    ascii_name = unicodedata.normalize("NFKD", raw_name)
    ascii_name = ascii_name.encode("ascii", errors="ignore").decode("ascii")
    ascii_name = "".join(
        character
        if character.isalnum() or character in (".", "_", "-")
        else "_"
        for character in ascii_name
    ).strip("._")
    if not ascii_name:
        ascii_name = fallback

    encoded_name = urllib.parse.quote(raw_name, safe="")
    return (
        f'inline; filename="{ascii_name}"; '
        f"filename*=UTF-8''{encoded_name}"
    )



class SendMessageBody(BaseModel):
    text: str = Field(min_length=1, max_length=4096)


class AssignConversationBody(BaseModel):
    brand_code: str


class SendMediaBody(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=3, max_length=120)
    data_base64: str = Field(min_length=4)
    caption: str | None = Field(default=None, max_length=1024)




class SendTextV2Body(BaseModel):
    text: str = Field(min_length=1, max_length=4096)
    reply_to_message_id: int | None = None

class SendMediaV2Body(SendMediaBody):
    reply_to_message_id: int | None = None

class SendReactionBody(BaseModel):
    target_message_id: int
    emoji: str = Field(default="", max_length=16)

class SendStickerBody(BaseModel):
    media_id: str | None = Field(default=None, max_length=255)
    source_message_id: int | None = None
    library_id: int | None = None


class AddBrandStickerBody(BaseModel):
    filename: str = Field(min_length=1, max_length=180)
    mime_type: str = Field(default="image/webp", max_length=120)
    data_base64: str = Field(min_length=4)


class CreateLeadBody(BaseModel):
    nombre: str = Field(min_length=2, max_length=180)
    fecha_evento: date
    comuna: str | None = Field(default=None, max_length=120)


class CreateMessageTemplateBody(BaseModel):
    conversation_id: int | None = None
    name: str = Field(pattern=r"^[a-z0-9_]{3,128}$")
    category: str = Field(pattern=r"^(UTILITY|MARKETING)$")
    language: str = Field(default="es", pattern=r"^[a-z]{2}(?:_[A-Z]{2})?$")
    header: str | None = Field(default=None, max_length=60)
    body: str = Field(min_length=1, max_length=1024)
    footer: str | None = Field(default=None, max_length=60)


class SendMessageTemplateBody(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9_]{3,128}$")
    language: str = Field(default="es", pattern=r"^[a-z]{2}(?:_[A-Z]{2})?$")


class CreateQuickReplyBody(BaseModel):
    conversation_id: int
    category: str = Field(pattern=r"^(VENTA|SEGUIMIENTO|SOLICITUDES|SUGERENCIAS|POSTVENTA)$")
    title: str = Field(min_length=2, max_length=90)
    body: str = Field(min_length=2, max_length=1200)
    sort_order: int = Field(default=100, ge=0, le=9999)


class UpdateQuickReplyBody(CreateQuickReplyBody):
    active: bool = True


BRANDS: dict[str, tuple[str, str]] = {
    "CAMALEON": ("CAMALEÓN", "Andrés Landerer"),
    "DEL_SABOR": ("DEL SABOR", "Walter Canales"),
    "GOURMET": ("GOURMET", "Constanza Franco"),
    "EXPRESS": ("EXPRESS", "Daniel Toledo"),
}

QUICK_REPLY_SEEDS: tuple[tuple[str, str, str, str], ...] = (
    ("venta_saludo", "VENTA", "Saludo inicial", "Hola {{cliente}}, soy {{ejecutivo}} de {{marca}}. Gracias por escribirnos. Cuéntame brevemente qué necesitas y con gusto te orientaré."),
    ("venta_datos", "VENTA", "Datos para cotizar", "Para recomendarte la mejor alternativa, ¿me compartes la fecha, comuna, cantidad estimada de personas y tipo de servicio que necesitas?"),
    ("venta_disponibilidad", "VENTA", "Revisando disponibilidad", "Gracias por la información, {{cliente}}. Estoy revisando disponibilidad y alternativas de {{marca}} para entregarte una propuesta adecuada."),
    ("venta_propuesta", "VENTA", "Propuesta preparada", "Hola {{cliente}}. Ya tenemos una propuesta preparada según lo conversado. Te la compartiré para que puedas revisarla con calma."),
    ("venta_conversar", "VENTA", "Invitación a conversar", "Si te parece, podemos revisar juntos la propuesta por este medio o coordinar una llamada breve para resolver tus dudas."),
    ("venta_avanzar", "VENTA", "Avanzar o ajustar", "¿Te gustaría que avancemos con esta alternativa o prefieres que ajustemos algún aspecto de la propuesta?"),
    ("seguimiento_cotizacion", "SEGUIMIENTO", "Cotización enviada", "Hola {{cliente}}. Te enviamos la cotización solicitada de {{marca}}. Quedo atento a tus comentarios o cualquier ajuste que necesites."),
    ("seguimiento_amable", "SEGUIMIENTO", "Seguimiento amable", "Hola {{cliente}}, espero que estés muy bien. Quería saber si pudiste revisar la información que te enviamos y si puedo ayudarte con alguna duda."),
    ("seguimiento_dudas", "SEGUIMIENTO", "Resolver dudas", "Quedo disponible para aclarar precios, alcance, condiciones o alternativas. La idea es que puedas tomar una decisión con toda la información necesaria."),
    ("seguimiento_disponibilidad", "SEGUIMIENTO", "Disponibilidad pendiente", "La disponibilidad se confirma al formalizar la reserva. Si deseas avanzar, puedo indicarte los siguientes pasos."),
    ("seguimiento_retomar", "SEGUIMIENTO", "Retomar conversación", "Hola {{cliente}}. Retomo nuestra conversación para saber si tu solicitud sigue vigente o si cambiaron la fecha o los requerimientos."),
    ("seguimiento_cierre", "SEGUIMIENTO", "Cierre respetuoso", "Hola {{cliente}}. Para no interrumpirte, cerraré este seguimiento por ahora. Si deseas retomarlo, escríbenos y con gusto continuaremos ayudándote."),
    ("solicitud_fecha", "SOLICITUDES", "Fecha y ubicación", "Para continuar necesito confirmar la fecha del evento y la comuna o dirección donde se realizará."),
    ("solicitud_personas", "SOLICITUDES", "Cantidad de personas", "¿Cuál es la cantidad estimada de asistentes? Si tienes un rango aproximado también nos sirve para preparar la propuesta."),
    ("solicitud_presupuesto", "SOLICITUDES", "Presupuesto objetivo", "¿Tienes un presupuesto estimado o rango objetivo? Con esa referencia puedo priorizar las alternativas más convenientes."),
    ("solicitud_facturacion", "SOLICITUDES", "Facturación", "Para preparar la documentación, envíanos razón social, RUT, giro, dirección y correo de facturación."),
    ("solicitud_pago", "SOLICITUDES", "Comprobante de pago", "Cuando realices el pago, por favor envíanos el comprobante indicando el nombre o número de la cotización."),
    ("solicitud_restricciones", "SOLICITUDES", "Restricciones alimentarias", "¿Existen alergias, intolerancias o restricciones alimentarias que debamos considerar en la propuesta?"),
    ("solicitud_montaje", "SOLICITUDES", "Montaje y acceso", "¿Nos puedes indicar el horario disponible para montaje, condiciones de acceso y un contacto en el lugar?"),
    ("sugerencia_alternativa", "SUGERENCIAS", "Alternativa recomendada", "Según lo conversado, esta alternativa ofrece un buen equilibrio entre experiencia, cantidad y presupuesto. Podemos ajustarla si cambian tus prioridades."),
    ("sugerencia_optimizar", "SUGERENCIAS", "Optimizar presupuesto", "Podemos optimizar el presupuesto ajustando cantidades, variedad o formato de servicio sin perder los elementos principales de la experiencia."),
    ("sugerencia_comparar", "SUGERENCIAS", "Comparar opciones", "Si quieres, puedo prepararte dos alternativas comparables: una esencial y otra más completa, para facilitar la decisión."),
    ("sugerencia_complementos", "SUGERENCIAS", "Complementos", "También podemos evaluar complementos opcionales. Te indicaré claramente cuáles son recomendados y cuáles puedes omitir."),
    ("sugerencia_llamada", "SUGERENCIAS", "Llamada de asesoría", "Por la cantidad de variables, una llamada breve puede ayudarnos a recomendarte mejor. ¿Qué horario te acomoda?"),
    ("postventa_reserva", "POSTVENTA", "Reserva recibida", "Gracias, {{cliente}}. Recibimos la confirmación y continuaremos con la coordinación correspondiente."),
    ("postventa_pago", "POSTVENTA", "Pago recibido", "Confirmamos la recepción del comprobante. Validaremos el pago y actualizaremos el estado de tu solicitud."),
    ("postventa_coordinacion", "POSTVENTA", "Coordinación previa", "Estamos revisando los últimos detalles de coordinación. Si hubo algún cambio de horario, dirección o contacto, avísanos por este medio."),
    ("postventa_gracias", "POSTVENTA", "Agradecimiento", "Muchas gracias por confiar en {{marca}}. Fue un gusto acompañarte y esperamos que hayas disfrutado la experiencia."),
    ("postventa_opinion", "POSTVENTA", "Solicitar opinión", "Tu opinión es muy importante para nosotros. ¿Cómo evaluarías la atención y el servicio recibido? Cualquier sugerencia nos ayuda a mejorar."),
)

_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY = False


def _ensure_tables(db: Session) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return

    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return

        ensure_webhook_tables(db)
        acquire_greenie_schema_lock(db)
        db.execute(text("""
        CREATE TABLE IF NOT EXISTS whatsapp_contacts (
            id BIGSERIAL PRIMARY KEY,
            wa_id TEXT NOT NULL UNIQUE,
            profile_name TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """))
        db.execute(text("""
        CREATE TABLE IF NOT EXISTS whatsapp_conversations (
            id BIGSERIAL PRIMARY KEY,
            contact_id BIGINT NOT NULL REFERENCES whatsapp_contacts(id),
            phone_number_id TEXT NOT NULL,
            brand_code TEXT,
            executive_name TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            unread_count INTEGER NOT NULL DEFAULT 0,
            last_message_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(contact_id, phone_number_id)
        )
        """))
        db.execute(text("""
        CREATE TABLE IF NOT EXISTS whatsapp_messages (
            id BIGSERIAL PRIMARY KEY,
            conversation_id BIGINT NOT NULL REFERENCES whatsapp_conversations(id) ON DELETE CASCADE,
            whatsapp_message_id TEXT UNIQUE,
            direction TEXT NOT NULL,
            message_type TEXT NOT NULL DEFAULT 'text',
            body TEXT,
            status TEXT,
            sent_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            raw_payload JSONB
        )
        """))
        db.execute(text("""
        ALTER TABLE whatsapp_messages ADD COLUMN IF NOT EXISTS media_id TEXT;
        ALTER TABLE whatsapp_messages ADD COLUMN IF NOT EXISTS mime_type TEXT;
        ALTER TABLE whatsapp_messages ADD COLUMN IF NOT EXISTS filename TEXT;
        ALTER TABLE whatsapp_messages ADD COLUMN IF NOT EXISTS caption TEXT;
        ALTER TABLE whatsapp_messages ADD COLUMN IF NOT EXISTS media_size BIGINT;
        ALTER TABLE whatsapp_messages ADD COLUMN IF NOT EXISTS latitude DOUBLE PRECISION;
        ALTER TABLE whatsapp_messages ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION;
        ALTER TABLE whatsapp_messages ADD COLUMN IF NOT EXISTS location_name TEXT;
        ALTER TABLE whatsapp_messages ADD COLUMN IF NOT EXISTS location_address TEXT;
        """))
        db.execute(text("""
        UPDATE whatsapp_messages
        SET media_id = COALESCE(
                media_id,
                raw_payload -> message_type ->> 'id'
            ),
            mime_type = COALESCE(
                mime_type,
                raw_payload -> message_type ->> 'mime_type'
            ),
            filename = COALESCE(
                filename,
                raw_payload -> message_type ->> 'filename'
            ),
            caption = COALESCE(
                caption,
                raw_payload -> message_type ->> 'caption'
            ),
            latitude = COALESCE(
                latitude,
                NULLIF(raw_payload -> 'location' ->> 'latitude', '')::DOUBLE PRECISION
            ),
            longitude = COALESCE(
                longitude,
                NULLIF(raw_payload -> 'location' ->> 'longitude', '')::DOUBLE PRECISION
            ),
            location_name = COALESCE(
                location_name,
                raw_payload -> 'location' ->> 'name'
            ),
            location_address = COALESCE(
                location_address,
                raw_payload -> 'location' ->> 'address'
            )
        WHERE raw_payload IS NOT NULL
          AND message_type IN ('image','audio','video','document','sticker','location')
        """))
        db.execute(text("""
        CREATE TABLE IF NOT EXISTS whatsapp_conversation_leads (
            id BIGSERIAL PRIMARY KEY,
            conversation_id BIGINT NOT NULL REFERENCES whatsapp_conversations(id) ON DELETE CASCADE,
            lead_id BIGINT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(conversation_id, lead_id)
        )
        """))
        db.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_whatsapp_conversations_last_message
        ON whatsapp_conversations(last_message_at DESC)
        """))
        db.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_whatsapp_messages_conversation
        ON whatsapp_messages(conversation_id, sent_at)
        """))

        db.execute(text("""
        ALTER TABLE whatsapp_messages
        ADD COLUMN IF NOT EXISTS reply_to_whatsapp_message_id TEXT
        """))
        db.execute(text("""
        CREATE TABLE IF NOT EXISTS whatsapp_message_reactions (
            id BIGSERIAL PRIMARY KEY,
            conversation_id BIGINT NOT NULL REFERENCES whatsapp_conversations(id) ON DELETE CASCADE,
            target_whatsapp_message_id TEXT NOT NULL,
            reactor_key TEXT NOT NULL,
            direction TEXT NOT NULL,
            emoji TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(target_whatsapp_message_id, reactor_key)
        )
        """))
        db.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_whatsapp_reactions_target
        ON whatsapp_message_reactions(target_whatsapp_message_id)
        """))
        db.execute(text("""
        CREATE TABLE IF NOT EXISTS whatsapp_brand_stickers (
            id BIGSERIAL PRIMARY KEY,
            brand_code TEXT NOT NULL,
            phone_number_id TEXT NOT NULL,
            name TEXT NOT NULL,
            media_id TEXT NOT NULL,
            mime_type TEXT NOT NULL DEFAULT 'image/webp',
            media_data BYTEA NOT NULL,
            media_size INTEGER NOT NULL,
            created_by TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_used_at TIMESTAMPTZ
        )
        """))
        db.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_whatsapp_brand_stickers_brand
        ON whatsapp_brand_stickers(brand_code, created_at DESC)
        """))
        db.execute(text("""
        CREATE TABLE IF NOT EXISTS whatsapp_quick_replies (
            id BIGSERIAL PRIMARY KEY,
            brand_code TEXT NOT NULL,
            seed_key TEXT,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            sort_order INTEGER NOT NULL DEFAULT 100,
            created_by TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(brand_code, seed_key)
        )
        """))
        db.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_whatsapp_quick_replies_brand
        ON whatsapp_quick_replies(brand_code, active, category, sort_order, id)
        """))
        db.execute(text("""
        CREATE OR REPLACE FUNCTION public.notify_greenie_message_change()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
          row_id BIGINT;
          conversation BIGINT;
        BEGIN
          IF TG_OP = 'DELETE' THEN
            row_id := OLD.id;
            conversation := OLD.conversation_id;
          ELSE
            row_id := NEW.id;
            conversation := NEW.conversation_id;
          END IF;
          PERFORM pg_notify(
            'greenie_events',
            json_build_object(
              'source', TG_TABLE_NAME,
              'operation', TG_OP,
              'id', row_id,
              'conversation_id', conversation
            )::text
          );
          IF TG_OP = 'DELETE' THEN
            RETURN OLD;
          END IF;
          RETURN NEW;
        END;
        $$;

        DROP TRIGGER IF EXISTS trg_notify_greenie_message
          ON public.whatsapp_messages;
        CREATE TRIGGER trg_notify_greenie_message
          AFTER INSERT OR UPDATE ON public.whatsapp_messages
          FOR EACH ROW EXECUTE FUNCTION public.notify_greenie_message_change();

        DROP TRIGGER IF EXISTS trg_notify_greenie_reaction
          ON public.whatsapp_message_reactions;
        CREATE TRIGGER trg_notify_greenie_reaction
          AFTER INSERT OR UPDATE OR DELETE ON public.whatsapp_message_reactions
          FOR EACH ROW EXECUTE FUNCTION public.notify_greenie_message_change();
        """))

        # Mapeo definitivo de ejecutivos. Por ahora el número de prueba se considera CAMALEÓN.
        for code, (brand_name, executive_name) in BRANDS.items():
            db.execute(text("""
            INSERT INTO whatsapp_channels(brand_code, brand_name, executive_name, enabled)
            VALUES (:code, :brand_name, :executive_name, TRUE)
            ON CONFLICT (brand_code) DO UPDATE SET
                brand_name = EXCLUDED.brand_name,
                executive_name = EXCLUDED.executive_name,
                enabled = TRUE,
                updated_at = now()
            """), {
                "code": code,
                "brand_name": brand_name,
                "executive_name": executive_name,
            })

        test_phone_number_id = _runtime_env("WHATSAPP_PHONE_NUMBER_ID", "").strip()
        if test_phone_number_id:
            db.execute(text("""
            UPDATE whatsapp_channels
            SET phone_number_id = NULL, updated_at = now()
            WHERE phone_number_id = :phone_number_id AND brand_code <> 'CAMALEON'
            """), {"phone_number_id": test_phone_number_id})
            db.execute(text("""
            UPDATE whatsapp_channels
            SET phone_number_id = :phone_number_id,
                waba_id = COALESCE(NULLIF(:waba_id, ''), waba_id),
                display_phone_number = COALESCE(NULLIF(:display_phone_number, ''), display_phone_number),
                brand_name = 'CAMALEÓN',
                executive_name = 'Andrés Landerer',
                enabled = TRUE,
                updated_at = now()
            WHERE brand_code = 'CAMALEON'
            """), {
                "phone_number_id": test_phone_number_id,
                "waba_id": _runtime_env("WHATSAPP_WABA_ID", "").strip(),
                "display_phone_number": _runtime_env("WHATSAPP_TEST_PHONE_NUMBER", "").strip(),
            })

        # Repara conversaciones recibidas antes de configurar el canal.
        db.execute(text("""
        UPDATE whatsapp_conversations c
        SET brand_code = ch.brand_code,
            executive_name = ch.executive_name,
            updated_at = now()
        FROM whatsapp_channels ch
        WHERE ch.enabled = TRUE
          AND ch.phone_number_id = c.phone_number_id
          AND (
              c.brand_code IS DISTINCT FROM ch.brand_code
              OR c.executive_name IS DISTINCT FROM ch.executive_name
          )
        """))
        db.commit()
        _SCHEMA_READY = True


def _ts(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def _phone9(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[-9:] if len(digits) >= 9 else digits


def _phone_e164_cl(value: Any) -> str:
    digits = _phone9(value)
    return f"+56{digits}" if len(digits) == 9 else str(value or "").strip()


def _brand_for_phone(db: Session, phone_number_id: str) -> tuple[str | None, str | None]:
    row = db.execute(text("""
        SELECT brand_code, executive_name
        FROM whatsapp_channels
        WHERE phone_number_id = :phone_number_id AND enabled = TRUE
        LIMIT 1
    """), {"phone_number_id": phone_number_id}).mappings().first()
    if not row:
        return None, None
    return row["brand_code"], row["executive_name"]


def _upsert_contact(db: Session, wa_id: str, profile_name: str | None) -> int:
    return int(db.execute(text("""
        INSERT INTO whatsapp_contacts(wa_id, profile_name)
        VALUES (:wa_id, :profile_name)
        ON CONFLICT (wa_id) DO UPDATE SET
            profile_name = COALESCE(EXCLUDED.profile_name, whatsapp_contacts.profile_name),
            updated_at = now()
        RETURNING id
    """), {"wa_id": wa_id, "profile_name": profile_name}).scalar_one())


def _upsert_conversation(
    db: Session,
    contact_id: int,
    phone_number_id: str,
    sent_at: datetime,
    unread_delta: int,
) -> int:
    brand_code, executive_name = _brand_for_phone(db, phone_number_id)
    return int(db.execute(text("""
        INSERT INTO whatsapp_conversations(
            contact_id, phone_number_id, brand_code, executive_name,
            unread_count, last_message_at
        ) VALUES (
            :contact_id, :phone_number_id, :brand_code, :executive_name,
            :unread_delta, :sent_at
        )
        ON CONFLICT (contact_id, phone_number_id) DO UPDATE SET
            brand_code = COALESCE(EXCLUDED.brand_code, whatsapp_conversations.brand_code),
            executive_name = COALESCE(EXCLUDED.executive_name, whatsapp_conversations.executive_name),
            unread_count = whatsapp_conversations.unread_count + :unread_delta,
            last_message_at = GREATEST(whatsapp_conversations.last_message_at, EXCLUDED.last_message_at),
            updated_at = now()
        RETURNING id
    """), {
        "contact_id": contact_id,
        "phone_number_id": phone_number_id,
        "brand_code": brand_code,
        "executive_name": executive_name,
        "unread_delta": unread_delta,
        "sent_at": sent_at,
    }).scalar_one())


def _sync_webhook_events(db: Session) -> None:
    _ensure_tables(db)
    rows = db.execute(text("""
        SELECT id, event_type, whatsapp_message_id, payload
        FROM whatsapp_webhook_events
        WHERE processing_status IN ('received', 'unknown_channel')
        ORDER BY id
        LIMIT 500
    """)).mappings().all()

    for row in rows:
        payload = row["payload"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        try:
            for entry in payload.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value") or {}
                    metadata = value.get("metadata") or {}
                    phone_number_id = str(metadata.get("phone_number_id") or "")
                    contacts = value.get("contacts") or []
                    profile_by_wa = {
                        str(c.get("wa_id") or ""): ((c.get("profile") or {}).get("name"))
                        for c in contacts if isinstance(c, dict)
                    }
                    for message in value.get("messages") or []:
                        wa_id = str(message.get("from") or "")
                        if not wa_id or not phone_number_id:
                            continue
                        message_id = str(message.get("id") or "") or None
                        message_type = str(message.get("type") or "unknown")
                        media_id = None
                        mime_type = None
                        filename = None
                        caption = None
                        latitude = None
                        longitude = None
                        location_name = None
                        location_address = None

                        if message_type == "reaction":
                            reaction = message.get("reaction") or {}
                            emoji = str(reaction.get("emoji") or "").strip()
                            target_id = str(reaction.get("message_id") or "").strip()
                            body = emoji or "Reacción eliminada"
                            if target_id:
                                body = f"{body}"
                        elif message_type == "text":
                            body = ((message.get("text") or {}).get("body"))
                        elif message_type == "button":
                            body = ((message.get("button") or {}).get("text"))
                        elif message_type == "interactive":
                            body = json.dumps(message.get("interactive") or {}, ensure_ascii=False)
                        elif message_type in {"image", "audio", "video", "document", "sticker"}:
                            media = message.get(message_type) or {}
                            media_id = str(media.get("id") or "") or None
                            mime_type = str(media.get("mime_type") or "") or None
                            filename = str(media.get("filename") or "") or None
                            caption = str(media.get("caption") or "") or None
                            labels = {
                                "image": "[Imagen]",
                                "audio": "[Audio]",
                                "video": "[Video]",
                                "document": f"[Documento] {filename or ''}".strip(),
                                "sticker": "[Sticker]",
                            }
                            body = caption or labels.get(message_type, f"[{message_type}]")
                        elif message_type == "location":
                            location = message.get("location") or {}
                            latitude = location.get("latitude")
                            longitude = location.get("longitude")
                            location_name = str(location.get("name") or "") or None
                            location_address = str(location.get("address") or "") or None
                            body = location_name or location_address or "[Ubicacion]"
                        else:
                            body = f"[{message_type}]"
                        sent_at = _ts(message.get("timestamp"))
                        contact_id = _upsert_contact(db, wa_id, profile_by_wa.get(wa_id))
                        conversation_id = _upsert_conversation(db, contact_id, phone_number_id, sent_at, 0)
                        inserted_message_id = db.execute(text("""
                            INSERT INTO whatsapp_messages(
                                conversation_id, whatsapp_message_id, direction,
                                message_type, body, status, sent_at, raw_payload,
                                media_id, mime_type, filename, caption,
                                latitude, longitude, location_name, location_address
                            ) VALUES (
                                :conversation_id, :message_id, 'inbound',
                                :message_type, :body, 'received', :sent_at,
                                CAST(:raw_payload AS JSONB),
                                :media_id, :mime_type, :filename, :caption,
                                :latitude, :longitude, :location_name, :location_address
                            )
                            ON CONFLICT (whatsapp_message_id) DO NOTHING
                            RETURNING id
                        """), {
                            "conversation_id": conversation_id,
                            "message_id": message_id,
                            "message_type": message_type,
                            "body": body,
                            "sent_at": sent_at,
                            "raw_payload": json.dumps(message, ensure_ascii=False),
                            "media_id": media_id,
                            "mime_type": mime_type,
                            "filename": filename,
                            "caption": caption,
                            "latitude": latitude,
                            "longitude": longitude,
                            "location_name": location_name,
                            "location_address": location_address,
                        }).scalar()
                        if inserted_message_id:
                            db.execute(text("""
                                UPDATE whatsapp_conversations
                                SET unread_count = unread_count + 1,
                                    updated_at = now()
                                WHERE id = :conversation_id
                            """), {"conversation_id": conversation_id})
                    for status in value.get("statuses") or []:
                        message_id = str(status.get("id") or "")
                        if message_id:
                            db.execute(text("""
                                UPDATE whatsapp_messages
                                SET status = :status
                                WHERE whatsapp_message_id = :message_id
                            """), {
                                "status": str(status.get("status") or "unknown"),
                                "message_id": message_id,
                            })
            db.execute(text("""
                UPDATE whatsapp_webhook_events
                SET processing_status = 'processed', processed_at = now(), error_message = NULL
                WHERE id = :id
            """), {"id": row["id"]})
        except Exception as exc:
            db.execute(text("""
                UPDATE whatsapp_webhook_events
                SET processing_status = 'error', error_message = :error
                WHERE id = :id
            """), {"id": row["id"], "error": str(exc)[:1000]})
    db.commit()


def _multipart_body(fields: dict[str, str], file_field: str, filename: str, mime_type: str, content: bytes) -> tuple[bytes, str]:
    boundary = "----GIA" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(),
            str(value).encode("utf-8"),
            b"\r\n",
        ])
    chunks.extend([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode("utf-8"),
        f"Content-Type: {mime_type}\r\n\r\n".encode(),
        content,
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    return b"".join(chunks), boundary


def _meta_upload_media(phone_number_id: str, filename: str, mime_type: str, content: bytes) -> str:
    token = _runtime_env("WHATSAPP_ACCESS_TOKEN", "").strip()
    version = _runtime_env("WHATSAPP_GRAPH_API_VERSION", "v25.0").strip()
    if not token:
        raise HTTPException(status_code=503, detail="WHATSAPP_ACCESS_TOKEN no configurado")
    body, boundary = _multipart_body(
        {"messaging_product": "whatsapp", "type": mime_type},
        "file",
        filename,
        mime_type,
        content,
    )
    request = urllib.request.Request(
        f"https://graph.facebook.com/{version}/{phone_number_id}/media",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"Meta rechazo la carga: {detail}") from exc
    media_id = str(result.get("id") or "")
    if not media_id:
        raise HTTPException(status_code=502, detail="Meta no devolvio media_id")
    return media_id


def _find_ffmpeg() -> str | None:
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    try:
        import imageio_ffmpeg  # type: ignore
        executable = imageio_ffmpeg.get_ffmpeg_exe()
        if executable and Path(executable).exists():
            return executable
    except Exception:
        pass
    return None


def _normalise_voice_audio(
    filename: str,
    mime_type: str,
    content: bytes,
) -> tuple[str, str, bytes, bool]:
    """
    Los navegadores normalmente graban WebM/Opus o MP4.
    WhatsApp no admite audio/webm. Para notas grabadas en el CRM se convierte
    siempre a OGG con codec Opus y se envia con audio.voice=true.
    """
    clean_mime = mime_type.lower().split(";", 1)[0].strip()
    lower_name = filename.lower()
    is_recording = lower_name.startswith(("audio_", "voice_", "nota_voz_"))
    needs_conversion = is_recording or clean_mime in {
        "audio/webm",
        "video/webm",
        "application/webm",
    } or lower_name.endswith(".webm")

    if not needs_conversion:
        return filename, clean_mime, content, False

    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        raise HTTPException(
            status_code=503,
            detail=(
                "El servidor no tiene FFmpeg para convertir la nota de voz a "
                "OGG/Opus. Instala imageio-ffmpeg en el entorno virtual."
            ),
        )

    input_suffix = ".webm"
    if clean_mime == "audio/mp4" or lower_name.endswith((".m4a", ".mp4")):
        input_suffix = ".m4a"
    elif clean_mime == "audio/ogg" or lower_name.endswith(".ogg"):
        input_suffix = ".ogg"

    with tempfile.TemporaryDirectory(prefix="greenie_voice_") as tmp:
        source = Path(tmp) / f"input{input_suffix}"
        output = Path(tmp) / "voice.ogg"
        source.write_bytes(content)
        process = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel", "error",
                "-y",
                "-i", str(source),
                "-vn",
                "-ac", "1",
                "-ar", "48000",
                "-c:a", "libopus",
                "-b:a", "32k",
                "-application", "voip",
                str(output),
            ],
            capture_output=True,
            timeout=90,
            check=False,
        )
        if process.returncode != 0 or not output.exists() or output.stat().st_size == 0:
            error = process.stderr.decode("utf-8", errors="replace")[-1200:]
            raise HTTPException(
                status_code=422,
                detail=f"No se pudo convertir la nota de voz a OGG/Opus: {error}",
            )
        converted = output.read_bytes()

    stem = Path(filename).stem or "nota_voz"
    return f"{stem}.ogg", "audio/ogg", converted, True


def _greenie_download_media_bytes(media_id: str) -> tuple[bytes, str]:
    token = _runtime_env("WHATSAPP_ACCESS_TOKEN")
    version = _runtime_env("WHATSAPP_GRAPH_API_VERSION", "v25.0")
    if not token:
        raise HTTPException(status_code=503, detail="WHATSAPP_ACCESS_TOKEN no configurado")

    metadata_request = urllib.request.Request(
        f"https://graph.facebook.com/{version}/{media_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(metadata_request, timeout=45) as response:
            metadata = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"Meta rechazo el sticker: {detail}") from exc

    media_url = str(metadata.get("url") or "").strip()
    mime_type = str(metadata.get("mime_type") or "image/webp").split(";", 1)[0].strip()
    if not media_url:
        raise HTTPException(status_code=502, detail="Meta no devolvio la URL del sticker")

    file_request = urllib.request.Request(
        media_url,
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(file_request, timeout=60) as response:
            content = response.read()
            mime_type = str(response.headers.get("Content-Type") or mime_type).split(";", 1)[0].strip()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"No se pudo descargar el sticker: {detail}") from exc

    if not content:
        raise HTTPException(status_code=502, detail="El sticker descargado esta vacio")
    return content, mime_type or "image/webp"


def _media_type_for_mime(mime_type: str) -> str:
    mime = mime_type.lower().split(";", 1)[0].strip()
    if mime == "image/webp":
        return "sticker"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("video/"):
        return "video"
    return "document"


def _meta_send_uploaded_media(
    phone_number_id: str,
    to: str,
    media_type: str,
    media_id: str,
    filename: str,
    caption: str | None,
    voice: bool = False,
) -> dict[str, Any]:
    token = _runtime_env("WHATSAPP_ACCESS_TOKEN", "").strip()
    version = _runtime_env("WHATSAPP_GRAPH_API_VERSION", "v25.0").strip()
    media_object: dict[str, Any] = {"id": media_id}
    if media_type == "audio" and voice:
        media_object["voice"] = True
    if caption and media_type in {"image", "video", "document"}:
        media_object["caption"] = caption
    if filename and media_type == "document":
        media_object["filename"] = filename
    payload = json.dumps({
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": media_type,
        media_type: media_object,
    }).encode("utf-8")
    request = urllib.request.Request(
        f"https://graph.facebook.com/{version}/{phone_number_id}/messages",
        data=payload,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"Meta rechazo el envio: {detail}") from exc


def _meta_send_text(phone_number_id: str, to: str, body: str) -> dict[str, Any]:
    token = _runtime_env("WHATSAPP_ACCESS_TOKEN", "").strip()
    version = _runtime_env("WHATSAPP_GRAPH_API_VERSION", "v25.0").strip()
    if not token:
        raise HTTPException(status_code=503, detail="WHATSAPP_ACCESS_TOKEN no configurado")
    url = f"https://graph.facebook.com/{version}/{phone_number_id}/messages"
    payload = json.dumps({
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": body},
    }).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"Meta rechazó el mensaje: {detail}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"No se pudo conectar con Meta: {exc}") from exc


def _template_admin(user: dict[str, Any]) -> None:
    role = str(user.get("role") or user.get("rol") or "").strip().upper()
    if "ADMIN" not in role:
        raise HTTPException(
            status_code=403,
            detail="Solo administradores pueden crear o eliminar plantillas de Meta",
        )


def _template_assets(
    db: Session,
    conversation_id: int | None = None,
) -> tuple[str, str]:
    waba_id = ""
    phone_number_id = ""
    if conversation_id:
        row = db.execute(text("""
            SELECT c.phone_number_id, ch.waba_id
            FROM whatsapp_conversations c
            LEFT JOIN whatsapp_channels ch
              ON ch.phone_number_id=c.phone_number_id
             AND ch.enabled=TRUE
            WHERE c.id=:conversation_id
            LIMIT 1
        """), {"conversation_id": conversation_id}).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="Conversación no encontrada")
        phone_number_id = str(row.get("phone_number_id") or "").strip()
        waba_id = str(row.get("waba_id") or "").strip()
    waba_id = waba_id or _runtime_env("WHATSAPP_WABA_ID")
    phone_number_id = phone_number_id or _runtime_env("WHATSAPP_PHONE_NUMBER_ID")
    if not waba_id:
        raise HTTPException(status_code=503, detail="WHATSAPP_WABA_ID no configurado")
    return waba_id, phone_number_id


def _meta_graph_json(
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    query: dict[str, str] | None = None,
) -> dict[str, Any]:
    token = _runtime_env("WHATSAPP_ACCESS_TOKEN")
    version = _runtime_env("WHATSAPP_GRAPH_API_VERSION", "v25.0")
    if not token:
        raise HTTPException(status_code=503, detail="WHATSAPP_ACCESS_TOKEN no configurado")
    url = f"https://graph.facebook.com/{version}/{path.lstrip('/')}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = None
    headers = {"Authorization": f"Bearer {token}"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {"success": True}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            error = json.loads(raw).get("error") or {}
            message = str(error.get("message") or raw)
            code = error.get("code")
            if code == 190 or exc.code == 401:
                message = "El token de Meta venció o no es válido. Genera uno nuevo en WhatsApp > API Setup."
        except Exception:
            message = raw or str(exc)
        raise HTTPException(status_code=502, detail=f"Meta: {message}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"No se pudo conectar con Meta: {exc}") from exc


def _greenie_env(name: str, default: str = "") -> str:
    runtime = globals().get("_runtime_env")
    if callable(runtime):
        return str(runtime(name, default) or default).strip()
    return str(os.getenv(name, default) or default).strip()


def _greenie_meta_post_message(phone_number_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    token = _greenie_env("WHATSAPP_ACCESS_TOKEN")
    version = _greenie_env("WHATSAPP_GRAPH_API_VERSION", "v25.0")
    if not token:
        raise HTTPException(status_code=503, detail="WHATSAPP_ACCESS_TOKEN no configurado")
    request = urllib.request.Request(
        f"https://graph.facebook.com/{version}/{phone_number_id}/messages",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"Meta rechazo el mensaje: {detail}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"No se pudo conectar con Meta: {exc}") from exc


def _greenie_target_message(db: Session, conversation_id: int, local_message_id: int | None):
    if not local_message_id:
        return None
    row = db.execute(text("""
        SELECT id, whatsapp_message_id, body, message_type, direction, sent_at
        FROM whatsapp_messages
        WHERE id = :message_id
          AND conversation_id = :conversation_id
        LIMIT 1
    """), {
        "message_id": local_message_id,
        "conversation_id": conversation_id,
    }).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="El mensaje seleccionado ya no existe")
    if not str(row.get("whatsapp_message_id") or "").strip():
        raise HTTPException(status_code=400, detail="Este mensaje no tiene un identificador valido de WhatsApp")
    return row


def _normalize_reaction_messages(db: Session, conversation_id: int | None = None) -> None:
    params: dict[str, Any] = {}
    condition = ""
    if conversation_id is not None:
        condition = "AND m.conversation_id = :conversation_id"
        params["conversation_id"] = conversation_id

    rows = db.execute(text(f"""
        SELECT m.id, m.conversation_id, m.direction, m.raw_payload
        FROM whatsapp_messages m
        WHERE m.message_type = 'reaction'
          {condition}
        ORDER BY m.id
        LIMIT 500
    """), params).mappings().all()

    for row in rows:
        payload = row.get("raw_payload") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        reaction = payload.get("reaction") or {}
        target_id = str(reaction.get("message_id") or "").strip()
        emoji = str(reaction.get("emoji") or "").strip()
        reactor = str(payload.get("from") or "business").strip() or "business"
        direction = str(row.get("direction") or "inbound")
        reactor_key = f"{direction}:{reactor}"

        if target_id:
            if emoji:
                db.execute(text("""
                    INSERT INTO whatsapp_message_reactions(
                        conversation_id, target_whatsapp_message_id,
                        reactor_key, direction, emoji
                    ) VALUES (
                        :conversation_id, :target_id,
                        :reactor_key, :direction, :emoji
                    )
                    ON CONFLICT (target_whatsapp_message_id, reactor_key)
                    DO UPDATE SET
                        emoji = EXCLUDED.emoji,
                        direction = EXCLUDED.direction,
                        updated_at = now()
                """), {
                    "conversation_id": row["conversation_id"],
                    "target_id": target_id,
                    "reactor_key": reactor_key,
                    "direction": direction,
                    "emoji": emoji,
                })
            else:
                db.execute(text("""
                    DELETE FROM whatsapp_message_reactions
                    WHERE target_whatsapp_message_id = :target_id
                      AND reactor_key = :reactor_key
                """), {
                    "target_id": target_id,
                    "reactor_key": reactor_key,
                })

        db.execute(text("DELETE FROM whatsapp_messages WHERE id = :id"), {"id": row["id"]})
        if direction == "inbound":
            db.execute(text("""
                UPDATE whatsapp_conversations
                SET unread_count = GREATEST(unread_count - 1, 0)
                WHERE id = :conversation_id
            """), {"conversation_id": row["conversation_id"]})
    db.commit()


def _greenie_comunas(db: Session) -> list[dict[str, Any]]:
    columns = {
        str(row["column_name"])
        for row in db.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'comunas'
        """)).mappings().all()
    }
    if not columns:
        raise HTTPException(
            status_code=500,
            detail="No existe la tabla public.comunas",
        )

    id_column = next(
        (name for name in ("id_comuna", "id", "comuna_id") if name in columns),
        None,
    )
    name_column = next(
        (
            name
            for name in (
                "nombre",
                "comuna",
                "name",
                "descripcion",
                "nombre_comuna",
            )
            if name in columns
        ),
        None,
    )
    if not name_column:
        raise HTTPException(
            status_code=500,
            detail=(
                "La tabla public.comunas no tiene una columna reconocida para "
                "el nombre. Columnas detectadas: " + ", ".join(sorted(columns))
            ),
        )

    id_expression = id_column if id_column else "ROW_NUMBER() OVER (ORDER BY " + name_column + ")"
    rows = db.execute(text(f"""
        SELECT
            {id_expression} AS id_comuna,
            TRIM(CAST({name_column} AS TEXT)) AS nombre
        FROM public.comunas
        WHERE NULLIF(TRIM(CAST({name_column} AS TEXT)), '') IS NOT NULL
        ORDER BY TRIM(CAST({name_column} AS TEXT))
    """)).mappings().all()

    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for row in rows:
        nombre = str(row.get("nombre") or "").strip()
        key = nombre.casefold()
        if not nombre or key in seen:
            continue
        seen.add(key)
        items.append({
            "id_comuna": row.get("id_comuna"),
            "nombre": nombre,
        })
    return items


def _greenie_normalize_brand(value: Any) -> str:
    raw = unicodedata.normalize("NFKD", str(value or ""))
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    return "".join(ch for ch in raw.upper() if ch.isalnum())


def _greenie_brand_from_db(
    db: Session,
    brand_code: str,
    fallback_name: str | None = None,
) -> dict[str, Any]:
    columns = {
        str(row["column_name"])
        for row in db.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'marcas'
        """)).mappings().all()
    }
    if not columns:
        raise HTTPException(
            status_code=500,
            detail="No existe la tabla public.marcas en el CRM",
        )

    id_column = next(
        (name for name in ("id_marca", "id", "marca_id") if name in columns),
        None,
    )
    if not id_column:
        raise HTTPException(
            status_code=500,
            detail=(
                "La tabla public.marcas no tiene una columna ID reconocida. "
                "Columnas detectadas: " + ", ".join(sorted(columns))
            ),
        )

    candidate_columns = [
        name
        for name in (
            "nombre",
            "marca",
            "nombre_marca",
            "codigo",
            "code",
            "slug",
            "abreviatura",
            "descripcion",
        )
        if name in columns
    ]
    if not candidate_columns:
        raise HTTPException(
            status_code=500,
            detail=(
                "La tabla public.marcas no tiene columnas reconocidas para "
                "identificar la marca. Columnas detectadas: "
                + ", ".join(sorted(columns))
            ),
        )

    select_fields = ", ".join(
        [f'{id_column} AS id_marca']
        + [f'{column} AS "{column}"' for column in candidate_columns]
    )
    rows = db.execute(text(f"""
        SELECT {select_fields}
        FROM public.marcas
        ORDER BY {id_column}
    """)).mappings().all()

    requested = {
        _greenie_normalize_brand(brand_code),
        _greenie_normalize_brand(fallback_name),
        _greenie_normalize_brand(brand_code.replace("_", " ")),
    }
    requested.discard("")

    matches: list[tuple[int, dict[str, Any], str]] = []
    available: list[str] = []
    for row in rows:
        row_dict = dict(row)
        labels = [
            str(row_dict.get(column) or "").strip()
            for column in candidate_columns
            if str(row_dict.get(column) or "").strip()
        ]
        if labels:
            available.append(labels[0])
        normalized_labels = {
            _greenie_normalize_brand(label)
            for label in labels
            if _greenie_normalize_brand(label)
        }
        exact = requested.intersection(normalized_labels)
        if exact:
            display_name = next(
                (
                    str(row_dict.get(column) or "").strip()
                    for column in ("nombre", "marca", "nombre_marca", "descripcion")
                    if column in row_dict and str(row_dict.get(column) or "").strip()
                ),
                labels[0] if labels else brand_code,
            )
            matches.append((0, row_dict, display_name))
            continue

        # Respaldo controlado para códigos como DEL_SABOR frente a "DEL SABOR".
        for requested_value in requested:
            if any(
                requested_value and label
                and (requested_value in label or label in requested_value)
                for label in normalized_labels
            ):
                display_name = next(
                    (
                        str(row_dict.get(column) or "").strip()
                        for column in ("nombre", "marca", "nombre_marca", "descripcion")
                        if column in row_dict and str(row_dict.get(column) or "").strip()
                    ),
                    labels[0] if labels else brand_code,
                )
                matches.append((1, row_dict, display_name))
                break

    if not matches:
        listed = ", ".join(available[:20]) or "sin registros"
        raise HTTPException(
            status_code=400,
            detail=(
                f"No se pudo asociar el canal {brand_code} con una marca de la BD. "
                f"Marcas disponibles: {listed}"
            ),
        )

    matches.sort(key=lambda item: (item[0], int(item[1]["id_marca"])))
    _, selected, display_name = matches[0]
    return {
        "id_marca": int(selected["id_marca"]),
        "brand_name": display_name,
    }


def _conversation_or_404(db: Session, conversation_id: int):
    row = db.execute(text("""
        SELECT c.*, ct.wa_id, ct.profile_name
        FROM whatsapp_conversations c
        JOIN whatsapp_contacts ct ON ct.id = c.contact_id
        WHERE c.id = :id
    """), {"id": conversation_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Conversación no encontrada")
    return row


def _quick_reply_admin(user: dict[str, Any]) -> bool:
    role = str(user.get("role") or user.get("rol") or "").strip().upper()
    return "ADMIN" in role


def _seed_quick_replies(db: Session, brand_code: str) -> None:
    for position, (seed_key, category, title, body) in enumerate(QUICK_REPLY_SEEDS, start=1):
        db.execute(text("""
            INSERT INTO whatsapp_quick_replies(
                brand_code, seed_key, category, title, body, sort_order, created_by
            ) VALUES (
                :brand_code, :seed_key, :category, :title, :body, :sort_order, 'SISTEMA'
            )
            ON CONFLICT (brand_code, seed_key) DO NOTHING
        """), {
            "brand_code": brand_code,
            "seed_key": seed_key,
            "category": category,
            "title": title,
            "body": body,
            "sort_order": position * 10,
        })
    db.commit()


def _existing_leads(db: Session, wa_id: str) -> list[dict[str, Any]]:
    phone9 = _phone9(wa_id)
    if not phone9:
        return []
    marca_cols = {
        str(row[0])
        for row in db.execute(
            text(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema='public' AND table_name='marcas'
                """
            )
        ).fetchall()
    }
    lead_cols = {
        str(row[0])
        for row in db.execute(
            text(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema='public' AND table_name='leads'
                """
            )
        ).fetchall()
    }
    marca_expr = "COALESCE(m.nombre,m.marca,'')" if {"nombre", "marca"} <= marca_cols else (
        "COALESCE(m.nombre,'')" if "nombre" in marca_cols else "COALESCE(m.marca,'')"
    )
    deleted_filter = "COALESCE(l.is_deleted,FALSE)=FALSE" if "is_deleted" in lead_cols else "TRUE"
    rows = db.execute(text(f"""
        SELECT l.id_lead, l.cliente, l.fecha_evento, l.created_at,
               {marca_expr} AS marca,
               COALESCE(e.nombre, '') AS estado
        FROM public.leads l
        LEFT JOIN public.marcas m ON m.id_marca = l.id_marca
        LEFT JOIN public.estados_lead e ON e.id_estado = l.id_estado
        WHERE RIGHT(regexp_replace(COALESCE(l.telefono, ''), '[^0-9]', '', 'g'), 9) = :phone9
          AND {deleted_filter}
        ORDER BY l.created_at DESC, l.id_lead DESC
        LIMIT 10
    """), {"phone9": phone9}).mappings().all()
    return [dict(r) for r in rows]


def _greenie_listen_dsn() -> str:
    dsn = str(DATABASE_URL or "").strip()
    for prefix in ("postgresql+psycopg://", "postgresql+psycopg2://"):
        if dsn.startswith(prefix):
            return "postgresql://" + dsn[len(prefix):]
    return dsn


async def _greenie_event_stream():
    connection = None
    try:
        import psycopg

        connection = await asyncio.to_thread(
            psycopg.connect,
            _greenie_listen_dsn(),
            autocommit=True,
            connect_timeout=8,
        )
        await asyncio.to_thread(connection.execute, "LISTEN greenie_events")
        yield "retry: 3000\nevent: ready\ndata: {}\n\n"
        while True:
            notifications = await asyncio.to_thread(
                lambda: list(connection.notifies(timeout=20, stop_after=1))
            )
            if notifications:
                payload = str(notifications[0].payload or "{}").replace("\r", "").replace("\n", "")
                yield f"event: change\ndata: {payload}\n\n"
            else:
                yield ": keepalive\n\n"
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        payload = json.dumps({"error": type(exc).__name__}, ensure_ascii=False)
        yield f"event: unavailable\ndata: {payload}\n\n"
    finally:
        if connection is not None:
            try:
                await asyncio.to_thread(connection.close)
            except Exception:
                pass


@router.get("/quick-replies")
def quick_replies(
    conversation_id: int = Query(...),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    _ensure_tables(db)
    conversation = _conversation_or_404(db, conversation_id)
    brand_code = str(conversation.get("brand_code") or "").strip().upper()
    if not brand_code:
        raise HTTPException(status_code=409, detail="La conversación todavía no tiene una marca asignada")
    _seed_quick_replies(db, brand_code)
    can_manage = _quick_reply_admin(user)
    rows = db.execute(text("""
        SELECT id, category, title, body, active, sort_order, seed_key
        FROM whatsapp_quick_replies
        WHERE brand_code = :brand_code
          AND (:can_manage OR active = TRUE)
        ORDER BY category, sort_order, id
    """), {"brand_code": brand_code, "can_manage": can_manage}).mappings().all()
    return {
        "ok": True,
        "brand_code": brand_code,
        "can_manage": can_manage,
        "items": [dict(row) for row in rows],
    }


@router.post("/quick-replies")
def create_quick_reply(
    body: CreateQuickReplyBody,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    _ensure_tables(db)
    _template_admin(user)
    conversation = _conversation_or_404(db, body.conversation_id)
    brand_code = str(conversation.get("brand_code") or "").strip().upper()
    if not brand_code:
        raise HTTPException(status_code=409, detail="La conversación todavía no tiene una marca asignada")
    created_by = str(user.get("username") or user.get("email") or user.get("nombre") or "ADMIN")[:180]
    row = db.execute(text("""
        INSERT INTO whatsapp_quick_replies(
            brand_code, category, title, body, active, sort_order, created_by
        ) VALUES (
            :brand_code, :category, :title, :body, TRUE, :sort_order, :created_by
        )
        RETURNING id, category, title, body, active, sort_order, seed_key
    """), {
        "brand_code": brand_code,
        "category": body.category,
        "title": body.title.strip(),
        "body": body.body.strip(),
        "sort_order": body.sort_order,
        "created_by": created_by,
    }).mappings().one()
    db.commit()
    return {"ok": True, "item": dict(row)}


@router.put("/quick-replies/{reply_id}")
def update_quick_reply(
    reply_id: int,
    body: UpdateQuickReplyBody,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    _ensure_tables(db)
    _template_admin(user)
    conversation = _conversation_or_404(db, body.conversation_id)
    brand_code = str(conversation.get("brand_code") or "").strip().upper()
    if not brand_code:
        raise HTTPException(status_code=409, detail="La conversación todavía no tiene una marca asignada")
    row = db.execute(text("""
        UPDATE whatsapp_quick_replies
        SET category = :category,
            title = :title,
            body = :body,
            active = :active,
            sort_order = :sort_order,
            updated_at = now()
        WHERE id = :reply_id AND brand_code = :brand_code
        RETURNING id, category, title, body, active, sort_order, seed_key
    """), {
        "reply_id": reply_id,
        "brand_code": brand_code,
        "category": body.category,
        "title": body.title.strip(),
        "body": body.body.strip(),
        "active": body.active,
        "sort_order": body.sort_order,
    }).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Respuesta rápida no encontrada para esta marca")
    db.commit()
    return {"ok": True, "item": dict(row)}


@router.get("/templates")
def message_templates(
    conversation_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    _ensure_tables(db)
    waba_id, _ = _template_assets(db, conversation_id)
    result = _meta_graph_json(
        f"{waba_id}/message_templates",
        query={
            "fields": "id,name,status,category,language,components,quality_score,rejected_reason",
            "limit": "100",
        },
    )
    rows = result.get("data") or []
    return {
        "ok": True,
        "waba_configured": True,
        "items": sorted(
            rows,
            key=lambda item: (
                0 if str(item.get("status") or "").upper() == "APPROVED" else 1,
                str(item.get("name") or ""),
            ),
        ),
    }


@router.post("/templates")
def create_message_template(
    body: CreateMessageTemplateBody,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    _template_admin(user)
    _ensure_tables(db)
    if "{{" in body.body or "}}" in body.body:
        raise HTTPException(
            status_code=422,
            detail="Esta versión admite plantillas estáticas. Quita las variables {{n}} para enviarla a revisión.",
        )
    waba_id, _ = _template_assets(db, body.conversation_id)
    components: list[dict[str, str]] = []
    if body.header and body.header.strip():
        components.append({
            "type": "HEADER",
            "format": "TEXT",
            "text": body.header.strip(),
        })
    components.append({"type": "BODY", "text": body.body.strip()})
    if body.footer and body.footer.strip():
        components.append({"type": "FOOTER", "text": body.footer.strip()})
    result = _meta_graph_json(
        f"{waba_id}/message_templates",
        method="POST",
        payload={
            "name": body.name,
            "language": body.language,
            "category": body.category,
            "allow_category_change": True,
            "components": components,
        },
    )
    return {
        "ok": True,
        "id": result.get("id"),
        "status": result.get("status") or "PENDING",
        "category": result.get("category") or body.category,
        "name": body.name,
        "language": body.language,
    }


@router.delete("/templates/{template_name}")
def delete_message_template(
    template_name: str,
    conversation_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    _template_admin(user)
    _ensure_tables(db)
    clean_name = template_name.strip().lower()
    if not clean_name or len(clean_name) > 128 or any(
        char not in "abcdefghijklmnopqrstuvwxyz0123456789_" for char in clean_name
    ):
        raise HTTPException(status_code=422, detail="Nombre de plantilla inválido")
    waba_id, _ = _template_assets(db, conversation_id)
    result = _meta_graph_json(
        f"{waba_id}/message_templates",
        method="DELETE",
        query={"name": clean_name},
    )
    return {"ok": bool(result.get("success", True)), "name": clean_name}


@router.post("/conversations/{conversation_id}/send-template")
def send_message_template(
    conversation_id: int,
    body: SendMessageTemplateBody,
    db: Session = Depends(get_db),
):
    _ensure_tables(db)
    conversation = _conversation_or_404(db, conversation_id)
    _, fallback_phone_number_id = _template_assets(db, conversation_id)
    phone_number_id = str(conversation.get("phone_number_id") or fallback_phone_number_id)
    result = _meta_graph_json(
        f"{phone_number_id}/messages",
        method="POST",
        payload={
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": str(conversation["wa_id"]),
            "type": "template",
            "template": {
                "name": body.name,
                "language": {"code": body.language},
            },
        },
    )
    messages_result = result.get("messages") or []
    message_id = (
        messages_result[0].get("id")
        if messages_result and isinstance(messages_result[0], dict)
        else None
    )
    db.execute(text("""
        INSERT INTO whatsapp_messages(
            conversation_id, whatsapp_message_id, direction,
            message_type, body, status, sent_at, raw_payload
        ) VALUES (
            :conversation_id, :message_id, 'outbound',
            'template', :body, 'accepted', now(), CAST(:raw_payload AS JSONB)
        )
        ON CONFLICT (whatsapp_message_id) DO NOTHING
    """), {
        "conversation_id": conversation_id,
        "message_id": message_id,
        "body": f"[Plantilla enviada] {body.name}",
        "raw_payload": json.dumps(result, ensure_ascii=False),
    })
    db.execute(text("""
        UPDATE whatsapp_conversations
        SET last_message_at=now(), updated_at=now()
        WHERE id=:conversation_id
    """), {
        "conversation_id": conversation_id,
    })
    db.commit()
    return {"ok": True, "message_id": message_id, "template": body.name}


@router.get("/events", include_in_schema=False)
def greenie_events():
    enabled = _runtime_env("GREENIE_REALTIME_ENABLED", "0").lower() in ("1", "true", "yes", "on")
    if not enabled:
        return Response(
            status_code=503,
            headers={"Retry-After": "60", "Cache-Control": "no-store"},
        )
    # Prepara triggers y tablas antes de abrir la conexión LISTEN. La sesión corta
    # evita retener una conexión del pool durante toda la vida del stream.
    with SessionLocal() as db:
        _ensure_tables(db)
    return StreamingResponse(
        _greenie_event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/conversations")
def list_conversations(
    q: str | None = Query(default=None),
    status: str | None = Query(default="open"),
    db: Session = Depends(get_db),
):
    _sync_webhook_events(db)
    where = ["1=1"]
    params: dict[str, Any] = {}
    if status and status != "all":
        where.append("c.status = :status")
        params["status"] = status
    if q:
        where.append("(ct.profile_name ILIKE :q OR ct.wa_id ILIKE :q OR COALESCE(m.body, '') ILIKE :q)")
        params["q"] = f"%{q}%"
    rows = db.execute(text(f"""
        SELECT c.id, ct.wa_id, ct.profile_name, c.phone_number_id,
               c.brand_code, c.executive_name, c.status, c.unread_count,
               c.last_message_at, m.body AS last_message,
               m.direction AS last_direction, m.status AS last_message_status
        FROM whatsapp_conversations c
        JOIN whatsapp_contacts ct ON ct.id = c.contact_id
        LEFT JOIN LATERAL (
            SELECT body, direction, status
            FROM whatsapp_messages
            WHERE conversation_id = c.id
            ORDER BY sent_at DESC, id DESC
            LIMIT 1
        ) m ON TRUE
        WHERE {' AND '.join(where)}
        ORDER BY c.last_message_at DESC NULLS LAST, c.id DESC
        LIMIT 200
    """), params).mappings().all()
    return {"ok": True, "items": [dict(r) for r in rows]}


@router.get("/conversations/{conversation_id}/messages")
def conversation_messages(conversation_id: int, db: Session = Depends(get_db)):
    _sync_webhook_events(db)
    conversation = _conversation_or_404(db, conversation_id)
    db.execute(text("""
        UPDATE whatsapp_conversations
        SET unread_count = 0, updated_at = now()
        WHERE id = :id
    """), {"id": conversation_id})
    rows = db.execute(text("""
        SELECT id, whatsapp_message_id, direction, message_type, body, status, sent_at,
               media_id, mime_type, filename, caption, media_size,
               latitude, longitude, location_name, location_address
        FROM whatsapp_messages
        WHERE conversation_id = :id
        ORDER BY sent_at, id
        LIMIT 1000
    """), {"id": conversation_id}).mappings().all()
    existing = _existing_leads(db, str(conversation["wa_id"]))
    linked = db.execute(text("""
        SELECT lead_id, created_at
        FROM whatsapp_conversation_leads
        WHERE conversation_id = :id
        ORDER BY created_at DESC
    """), {"id": conversation_id}).mappings().all()
    db.commit()
    return {
        "ok": True,
        "conversation": dict(conversation),
        "items": [dict(r) for r in rows],
        "existing_leads": existing,
        "linked_leads": [dict(r) for r in linked],
    }


@router.get("/messages/{message_id}/media")
def get_message_media(
    message_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    _ensure_tables(db)
    row = db.execute(text("""
        SELECT media_id, mime_type, filename
        FROM whatsapp_messages
        WHERE id = :id
    """), {"id": message_id}).mappings().first()
    if not row or not row["media_id"]:
        raise HTTPException(status_code=404, detail="Archivo multimedia no encontrado")

    token = _runtime_env("WHATSAPP_ACCESS_TOKEN", "").strip()
    version = _runtime_env("WHATSAPP_GRAPH_API_VERSION", "v25.0").strip()
    if not token:
        raise HTTPException(status_code=503, detail="WHATSAPP_ACCESS_TOKEN no configurado")

    try:
        metadata_request = urllib.request.Request(
            f"https://graph.facebook.com/{version}/{row['media_id']}",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(metadata_request, timeout=30) as metadata_response:
            metadata = json.loads(metadata_response.read().decode("utf-8"))

        media_url = str(metadata.get("url") or "")
        if not media_url:
            raise HTTPException(status_code=502, detail="Meta no entrego URL del archivo")

        file_request = urllib.request.Request(
            media_url,
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(file_request, timeout=60) as file_response:
            content = file_response.read()
            content_type = (
                str(row.get("mime_type") or "").strip()
                or file_response.headers.get_content_type()
                or "application/octet-stream"
            )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"Meta rechazo el archivo: {detail}") from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"No se pudo descargar el archivo: {exc}") from exc

    db.execute(text("""
        UPDATE whatsapp_messages
        SET media_size = :media_size,
            mime_type = COALESCE(mime_type, :mime_type)
        WHERE id = :id
    """), {"id": message_id, "media_size": len(content), "mime_type": content_type})
    db.commit()

    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Content-Disposition": _inline_content_disposition(
                row.get("filename"),
                f"whatsapp_{message_id}",
            ),
            "Cache-Control": "private, max-age=300",
        },
    )


@router.post("/conversations/{conversation_id}/send")
def send_message(conversation_id: int, body: SendMessageBody, db: Session = Depends(get_db)):
    _ensure_tables(db)
    conversation = _conversation_or_404(db, conversation_id)
    require_open_customer_window(db, conversation_id)
    result = _meta_send_text(
        str(conversation["phone_number_id"]),
        str(conversation["wa_id"]),
        body.text.strip(),
    )
    messages = result.get("messages") or []
    message_id = messages[0].get("id") if messages and isinstance(messages[0], dict) else None
    row = db.execute(text("""
        INSERT INTO whatsapp_messages(
            conversation_id, whatsapp_message_id, direction,
            message_type, body, status, sent_at, raw_payload
        ) VALUES (
            :conversation_id, :message_id, 'outbound',
            'text', :body, 'accepted', now(), CAST(:raw_payload AS JSONB)
        )
        RETURNING id, sent_at
    """), {
        "conversation_id": conversation_id,
        "message_id": message_id,
        "body": body.text.strip(),
        "raw_payload": json.dumps(result, ensure_ascii=False),
    }).mappings().one()
    db.execute(text("""
        UPDATE whatsapp_conversations
        SET last_message_at = :sent_at, updated_at = now()
        WHERE id = :id
    """), {"id": conversation_id, "sent_at": row["sent_at"]})
    db.commit()
    return {"ok": True, "message_id": message_id, "id": row["id"], "status": "accepted"}


@router.post("/conversations/{conversation_id}/send-media")
def send_media(
    conversation_id: int,
    body: SendMediaBody,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    _ensure_tables(db)
    conversation = _conversation_or_404(db, conversation_id)
    require_open_customer_window(db, conversation_id)
    try:
        content = base64.b64decode(body.data_base64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Archivo base64 invalido") from exc
    if not content:
        raise HTTPException(status_code=400, detail="Archivo vacio")
    if len(content) > 16 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Archivo supera 16 MB para esta etapa de prueba")

    requested_mime = body.mime_type.lower().split(";", 1)[0].strip()
    if requested_mime == "image/webp" or body.filename.lower().endswith(".webp"):
        if len(content) > 100 * 1024:
            raise HTTPException(
                status_code=413,
                detail="El sticker supera 100 KB. Reduce el archivo WebP.",
            )
        filename = body.filename if body.filename.lower().endswith(".webp") else body.filename + ".webp"
        mime_type = "image/webp"
        voice = False
    else:
        filename, mime_type, content, voice = _normalise_voice_audio(
            body.filename,
            body.mime_type,
            content,
        )
    media_type = _media_type_for_mime(mime_type)
    media_id = _meta_upload_media(
        str(conversation["phone_number_id"]),
        filename,
        mime_type,
        content,
    )
    result = _meta_send_uploaded_media(
        str(conversation["phone_number_id"]),
        str(conversation["wa_id"]),
        media_type,
        media_id,
        filename,
        body.caption,
        voice=voice,
    )
    messages = result.get("messages") or []
    whatsapp_message_id = messages[0].get("id") if messages and isinstance(messages[0], dict) else None
    label = body.caption or {
        "image": "[Imagen enviada]",
        "audio": "[Audio enviado]",
        "sticker": "[Sticker enviado]",
        "video": "[Video enviado]",
        "document": f"[Documento enviado] {filename}",
    }.get(media_type, "[Archivo enviado]")
    row = db.execute(text("""
        INSERT INTO whatsapp_messages(
            conversation_id, whatsapp_message_id, direction,
            message_type, body, status, sent_at, raw_payload,
            media_id, mime_type, filename, caption, media_size
        ) VALUES (
            :conversation_id, :whatsapp_message_id, 'outbound',
            :message_type, :body, 'accepted', now(), CAST(:raw_payload AS JSONB),
            :media_id, :mime_type, :filename, :caption, :media_size
        )
        RETURNING id, sent_at
    """), {
        "conversation_id": conversation_id,
        "whatsapp_message_id": whatsapp_message_id,
        "message_type": media_type,
        "body": label,
        "raw_payload": json.dumps(result, ensure_ascii=False),
        "media_id": media_id,
        "mime_type": mime_type,
        "filename": filename,
        "caption": body.caption,
        "media_size": len(content),
    }).mappings().one()
    db.execute(text("""
        UPDATE whatsapp_conversations
        SET last_message_at = :sent_at, updated_at = now()
        WHERE id = :id
    """), {"id": conversation_id, "sent_at": row["sent_at"]})
    db.commit()
    return {
        "ok": True,
        "id": row["id"],
        "message_id": whatsapp_message_id,
        "media_id": media_id,
        "message_type": media_type,
        "status": "accepted",
    }


@router.get("/conversations/{conversation_id}/stickers")
def conversation_stickers(
    conversation_id: int,
    db: Session = Depends(get_db),
):
    _ensure_tables(db)
    conversation = _conversation_or_404(db, conversation_id)
    brand_code = str(conversation.get("brand_code") or "").strip().upper()
    library_rows = db.execute(text("""
        SELECT id AS library_id, name, media_id, mime_type, media_size,
               created_at, last_used_at
        FROM whatsapp_brand_stickers
        WHERE brand_code = :brand_code
          AND phone_number_id = :phone_number_id
        ORDER BY last_used_at DESC NULLS LAST, created_at DESC, id DESC
        LIMIT 80
    """), {
        "brand_code": brand_code,
        "phone_number_id": str(conversation["phone_number_id"]),
    }).mappings().all()
    recent_rows = db.execute(text("""
        SELECT *
        FROM (
            SELECT DISTINCT ON (m.media_id)
                m.id AS source_message_id,
                m.media_id,
                COALESCE(NULLIF(m.mime_type, ''), 'image/webp') AS mime_type,
                m.direction,
                m.sent_at
            FROM whatsapp_messages m
            JOIN whatsapp_conversations c
              ON c.id = m.conversation_id
            WHERE c.phone_number_id = :phone_number_id
              AND m.message_type = 'sticker'
              AND NULLIF(m.media_id, '') IS NOT NULL
            ORDER BY m.media_id, m.sent_at DESC, m.id DESC
        ) recent
        ORDER BY sent_at DESC
        LIMIT 60
    """), {
        "phone_number_id": str(conversation["phone_number_id"]),
    }).mappings().all()
    items = [
        {**dict(row), "kind": "library", "source_message_id": None}
        for row in library_rows
    ]
    items.extend(
        {**dict(row), "kind": "recent", "library_id": None, "name": "Sticker reciente"}
        for row in recent_rows
        if not any(str(saved["media_id"]) == str(row["media_id"]) for saved in library_rows)
    )
    return {"ok": True, "brand_code": brand_code, "items": items}


@router.post("/conversations/{conversation_id}/stickers/library")
def add_brand_sticker(
    conversation_id: int,
    body: AddBrandStickerBody,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    _ensure_tables(db)
    conversation = _conversation_or_404(db, conversation_id)
    brand_code = str(conversation.get("brand_code") or "").strip().upper()
    if brand_code not in BRANDS:
        raise HTTPException(status_code=400, detail="La conversación no tiene una marca configurada")
    try:
        content = base64.b64decode(body.data_base64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Sticker base64 invalido") from exc
    if not content:
        raise HTTPException(status_code=400, detail="El sticker esta vacio")
    if len(content) > 100 * 1024:
        raise HTTPException(status_code=413, detail="El sticker supera 100 KB")
    if len(content) < 12 or content[:4] != b"RIFF" or content[8:12] != b"WEBP":
        raise HTTPException(status_code=400, detail="El archivo debe ser un WebP valido")

    clean_name = Path(body.filename).name.strip() or "sticker.webp"
    if not clean_name.lower().endswith(".webp"):
        clean_name += ".webp"
    media_id = _meta_upload_media(
        str(conversation["phone_number_id"]),
        clean_name,
        "image/webp",
        content,
    )
    actor = str(user.get("name") or user.get("email") or user.get("username") or "Usuario CRM")
    row = db.execute(text("""
        INSERT INTO whatsapp_brand_stickers(
            brand_code, phone_number_id, name, media_id, mime_type,
            media_data, media_size, created_by
        ) VALUES (
            :brand_code, :phone_number_id, :name, :media_id, 'image/webp',
            :media_data, :media_size, :created_by
        )
        RETURNING id AS library_id, name, media_id, mime_type, media_size, created_at
    """), {
        "brand_code": brand_code,
        "phone_number_id": str(conversation["phone_number_id"]),
        "name": clean_name,
        "media_id": media_id,
        "media_data": content,
        "media_size": len(content),
        "created_by": actor,
    }).mappings().one()
    db.commit()
    return {"ok": True, "brand_code": brand_code, "item": {**dict(row), "kind": "library"}}


@router.get("/conversations/{conversation_id}/stickers/library/{sticker_id}/content")
def brand_sticker_content(
    conversation_id: int,
    sticker_id: int,
    db: Session = Depends(get_db),
):
    _ensure_tables(db)
    conversation = _conversation_or_404(db, conversation_id)
    row = db.execute(text("""
        SELECT name, mime_type, media_data
        FROM whatsapp_brand_stickers
        WHERE id = :sticker_id
          AND brand_code = :brand_code
          AND phone_number_id = :phone_number_id
        LIMIT 1
    """), {
        "sticker_id": sticker_id,
        "brand_code": str(conversation.get("brand_code") or "").strip().upper(),
        "phone_number_id": str(conversation["phone_number_id"]),
    }).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Sticker no encontrado en la biblioteca de la marca")
    return Response(
        content=bytes(row["media_data"]),
        media_type=str(row["mime_type"] or "image/webp"),
        headers={"Content-Disposition": _inline_content_disposition(row["name"], "sticker.webp")},
    )


@router.delete("/conversations/{conversation_id}/stickers/library/{sticker_id}")
def delete_brand_sticker(
    conversation_id: int,
    sticker_id: int,
    db: Session = Depends(get_db),
):
    _ensure_tables(db)
    conversation = _conversation_or_404(db, conversation_id)
    deleted = db.execute(text("""
        DELETE FROM whatsapp_brand_stickers
        WHERE id = :sticker_id
          AND brand_code = :brand_code
          AND phone_number_id = :phone_number_id
        RETURNING id
    """), {
        "sticker_id": sticker_id,
        "brand_code": str(conversation.get("brand_code") or "").strip().upper(),
        "phone_number_id": str(conversation["phone_number_id"]),
    }).scalar()
    if not deleted:
        raise HTTPException(status_code=404, detail="Sticker no encontrado en la biblioteca de la marca")
    db.commit()
    return {"ok": True, "deleted_id": int(deleted)}


@router.post("/conversations/{conversation_id}/send-sticker")
def send_existing_sticker(
    conversation_id: int,
    body: SendStickerBody,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    _ensure_tables(db)
    conversation = _conversation_or_404(db, conversation_id)
    require_open_customer_window(db, conversation_id)
    library = None
    if body.library_id:
        library = db.execute(text("""
            SELECT id, name, media_id, media_data
            FROM whatsapp_brand_stickers
            WHERE id = :library_id
              AND brand_code = :brand_code
              AND phone_number_id = :phone_number_id
            LIMIT 1
        """), {
            "library_id": body.library_id,
            "brand_code": str(conversation.get("brand_code") or "").strip().upper(),
            "phone_number_id": str(conversation["phone_number_id"]),
        }).mappings().first()
        if not library:
            raise HTTPException(status_code=404, detail="Sticker no encontrado en la biblioteca de la marca")
    media_id = str((library or {}).get("media_id") or body.media_id or "").strip()
    if not media_id:
        raise HTTPException(status_code=400, detail="Falta seleccionar un sticker")

    try:
        result = _meta_send_uploaded_media(
            str(conversation["phone_number_id"]),
            str(conversation["wa_id"]),
            "sticker",
            media_id,
            "sticker.webp",
            None,
        )
    except HTTPException:
        # Los media_id pueden vencer. Se descarga el sticker y se vuelve a subir.
        if library:
            content = bytes(library["media_data"])
            media_id = _meta_upload_media(
                str(conversation["phone_number_id"]),
                str(library["name"] or "sticker.webp"),
                "image/webp",
                content,
            )
            db.execute(text("""
                UPDATE whatsapp_brand_stickers
                SET media_id = :media_id, updated_at = now()
                WHERE id = :library_id
            """), {"media_id": media_id, "library_id": int(library["id"])})
            result = _meta_send_uploaded_media(
                str(conversation["phone_number_id"]),
                str(conversation["wa_id"]),
                "sticker",
                media_id,
                str(library["name"] or "sticker.webp"),
                None,
            )
        elif not body.source_message_id:
            raise
        else:
            source = db.execute(text("""
                SELECT m.media_id
                FROM whatsapp_messages m
                JOIN whatsapp_conversations c
                  ON c.id = m.conversation_id
                WHERE m.id = :message_id
                  AND c.phone_number_id = :phone_number_id
                  AND m.message_type = 'sticker'
                LIMIT 1
            """), {
                "message_id": body.source_message_id,
                "phone_number_id": str(conversation["phone_number_id"]),
            }).mappings().first()
            if not source:
                raise HTTPException(status_code=404, detail="Sticker no encontrado")
            content, mime_type = _greenie_download_media_bytes(str(source["media_id"]))
            if len(content) > 100 * 1024:
                raise HTTPException(status_code=413, detail="El sticker supera 100 KB")
            media_id = _meta_upload_media(
                str(conversation["phone_number_id"]),
                "sticker.webp",
                "image/webp",
                content,
            )
            result = _meta_send_uploaded_media(
                str(conversation["phone_number_id"]),
                str(conversation["wa_id"]),
                "sticker",
                media_id,
                "sticker.webp",
                None,
            )

    messages = result.get("messages") or []
    whatsapp_message_id = (
        messages[0].get("id")
        if messages and isinstance(messages[0], dict)
        else None
    )
    row = db.execute(text("""
        INSERT INTO whatsapp_messages(
            conversation_id, whatsapp_message_id, direction,
            message_type, body, status, sent_at, raw_payload,
            media_id, mime_type, filename
        ) VALUES (
            :conversation_id, :whatsapp_message_id, 'outbound',
            'sticker', '[Sticker enviado]', 'accepted', now(),
            CAST(:raw_payload AS JSONB), :media_id, 'image/webp', 'sticker.webp'
        )
        RETURNING id, sent_at
    """), {
        "conversation_id": conversation_id,
        "whatsapp_message_id": whatsapp_message_id,
        "raw_payload": json.dumps(result, ensure_ascii=False),
        "media_id": media_id,
    }).mappings().one()
    db.execute(text("""
        UPDATE whatsapp_conversations
        SET last_message_at = :sent_at, updated_at = now()
        WHERE id = :id
    """), {"id": conversation_id, "sent_at": row["sent_at"]})
    if library:
        db.execute(text("""
            UPDATE whatsapp_brand_stickers
            SET last_used_at = now(), updated_at = now()
            WHERE id = :library_id
        """), {"library_id": int(library["id"])})
    db.commit()
    return {
        "ok": True,
        "id": row["id"],
        "message_id": whatsapp_message_id,
        "media_id": media_id,
        "status": "accepted",
    }


@router.get("/metadata/comunas")
def whatsapp_comunas(db: Session = Depends(get_db)):
    _ensure_tables(db)
    return {"ok": True, "items": _greenie_comunas(db)}


@router.get("/conversations/{conversation_id}/messages-v2")
def conversation_messages_v2(conversation_id: int, db: Session = Depends(get_db)):
    _sync_webhook_events(db)
    _normalize_reaction_messages(db, conversation_id)
    conversation = _conversation_or_404(db, conversation_id)
    db.execute(text("""
        UPDATE whatsapp_conversations
        SET unread_count = 0, updated_at = now()
        WHERE id = :id
    """), {"id": conversation_id})

    rows = db.execute(text("""
        SELECT
            m.id,
            m.whatsapp_message_id,
            m.direction,
            m.message_type,
            m.body,
            m.status,
            m.sent_at,
            m.media_id,
            m.mime_type,
            m.filename,
            m.caption,
            m.media_size,
            COALESCE(
                NULLIF(m.reply_to_whatsapp_message_id, ''),
                NULLIF(m.raw_payload -> 'context' ->> 'id', '')
            ) AS reply_to_whatsapp_message_id,
            parent.id AS reply_to_id,
            parent.direction AS reply_to_direction,
            parent.message_type AS reply_to_type,
            parent.body AS reply_to_body,
            COALESCE((
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'emoji', r.emoji,
                        'direction', r.direction,
                        'reactor_key', r.reactor_key
                    )
                    ORDER BY r.updated_at, r.id
                )
                FROM whatsapp_message_reactions r
                WHERE r.target_whatsapp_message_id = m.whatsapp_message_id
            ), '[]'::jsonb) AS reactions
        FROM whatsapp_messages m
        LEFT JOIN whatsapp_messages parent
          ON parent.whatsapp_message_id = COALESCE(
              NULLIF(m.reply_to_whatsapp_message_id, ''),
              NULLIF(m.raw_payload -> 'context' ->> 'id', '')
          )
        WHERE m.conversation_id = :id
          AND m.message_type <> 'reaction'
        ORDER BY m.sent_at, m.id
        LIMIT 1000
    """), {"id": conversation_id}).mappings().all()

    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        reply_id = item.pop("reply_to_id", None)
        reply_direction = item.pop("reply_to_direction", None)
        reply_type = item.pop("reply_to_type", None)
        reply_body = item.pop("reply_to_body", None)
        if item.get("reply_to_whatsapp_message_id"):
            item["reply_to"] = {
                "id": reply_id,
                "direction": reply_direction,
                "message_type": reply_type,
                "body": reply_body or "Mensaje no disponible",
            }
        else:
            item["reply_to"] = None
        if isinstance(item.get("reactions"), str):
            try:
                item["reactions"] = json.loads(item["reactions"])
            except Exception:
                item["reactions"] = []
        items.append(item)

    db.commit()
    return {
        "ok": True,
        "conversation": dict(conversation),
        "items": items,
    }


@router.post("/conversations/{conversation_id}/send-v2")
def send_message_v2(
    conversation_id: int,
    body: SendTextV2Body,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    _ensure_tables(db)
    conversation = _conversation_or_404(db, conversation_id)
    require_open_customer_window(db, conversation_id)
    target = _greenie_target_message(db, conversation_id, body.reply_to_message_id)
    payload: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": str(conversation["wa_id"]),
        "type": "text",
        "text": {"preview_url": False, "body": body.text.strip()},
    }
    if target:
        payload["context"] = {"message_id": str(target["whatsapp_message_id"])}
    result = _greenie_meta_post_message(str(conversation["phone_number_id"]), payload)
    messages = result.get("messages") or []
    message_id = messages[0].get("id") if messages and isinstance(messages[0], dict) else None
    row = db.execute(text("""
        INSERT INTO whatsapp_messages(
            conversation_id, whatsapp_message_id, direction,
            message_type, body, status, sent_at, raw_payload,
            reply_to_whatsapp_message_id
        ) VALUES (
            :conversation_id, :message_id, 'outbound',
            'text', :body, 'accepted', now(), CAST(:raw_payload AS JSONB),
            :reply_to_whatsapp_message_id
        )
        RETURNING id, sent_at
    """), {
        "conversation_id": conversation_id,
        "message_id": message_id,
        "body": body.text.strip(),
        "raw_payload": json.dumps(result, ensure_ascii=False),
        "reply_to_whatsapp_message_id": str(target["whatsapp_message_id"]) if target else None,
    }).mappings().one()
    db.execute(text("""
        UPDATE whatsapp_conversations
        SET last_message_at = :sent_at, updated_at = now()
        WHERE id = :id
    """), {"id": conversation_id, "sent_at": row["sent_at"]})
    db.commit()
    return {"ok": True, "message_id": message_id, "id": row["id"], "status": "accepted"}


@router.post("/conversations/{conversation_id}/send-reaction")
def send_reaction(
    conversation_id: int,
    body: SendReactionBody,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    _ensure_tables(db)
    conversation = _conversation_or_404(db, conversation_id)
    target = _greenie_target_message(db, conversation_id, body.target_message_id)
    emoji = body.emoji.strip()
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": str(conversation["wa_id"]),
        "type": "reaction",
        "reaction": {
            "message_id": str(target["whatsapp_message_id"]),
            "emoji": emoji,
        },
    }
    result = _greenie_meta_post_message(str(conversation["phone_number_id"]), payload)
    if emoji:
        db.execute(text("""
            INSERT INTO whatsapp_message_reactions(
                conversation_id, target_whatsapp_message_id,
                reactor_key, direction, emoji
            ) VALUES (
                :conversation_id, :target_id,
                'outbound:business', 'outbound', :emoji
            )
            ON CONFLICT (target_whatsapp_message_id, reactor_key)
            DO UPDATE SET emoji = EXCLUDED.emoji, updated_at = now()
        """), {
            "conversation_id": conversation_id,
            "target_id": str(target["whatsapp_message_id"]),
            "emoji": emoji,
        })
    else:
        db.execute(text("""
            DELETE FROM whatsapp_message_reactions
            WHERE target_whatsapp_message_id = :target_id
              AND reactor_key = 'outbound:business'
        """), {"target_id": str(target["whatsapp_message_id"])})
    db.commit()
    return {"ok": True, "emoji": emoji, "meta": result}


@router.post("/conversations/{conversation_id}/send-media-v2")
def send_media_v2(
    conversation_id: int,
    body: SendMediaV2Body,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    _ensure_tables(db)
    conversation = _conversation_or_404(db, conversation_id)
    require_open_customer_window(db, conversation_id)
    try:
        content = base64.b64decode(body.data_base64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Archivo base64 invalido") from exc
    if not content:
        raise HTTPException(status_code=400, detail="Archivo vacio")
    if len(content) > 16 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Archivo supera 16 MB")

    requested_mime = body.mime_type.lower().split(";", 1)[0].strip()
    if requested_mime == "image/webp" or body.filename.lower().endswith(".webp"):
        if len(content) > 100 * 1024:
            raise HTTPException(status_code=413, detail="El sticker supera 100 KB")
        filename = body.filename if body.filename.lower().endswith(".webp") else body.filename + ".webp"
        mime_type = "image/webp"
        voice = False
    else:
        filename, mime_type, content, voice = _normalise_voice_audio(
            body.filename,
            body.mime_type,
            content,
        )
    media_type = _media_type_for_mime(mime_type)
    media_id = _meta_upload_media(
        str(conversation["phone_number_id"]),
        filename,
        mime_type,
        content,
    )
    target = _greenie_target_message(db, conversation_id, body.reply_to_message_id)
    media_object: dict[str, Any] = {"id": media_id}
    if media_type == "audio" and voice:
        media_object["voice"] = True
    if body.caption and media_type in {"image", "video", "document"}:
        media_object["caption"] = body.caption
    if filename and media_type == "document":
        media_object["filename"] = filename
    payload: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": str(conversation["wa_id"]),
        "type": media_type,
        media_type: media_object,
    }
    if target:
        payload["context"] = {"message_id": str(target["whatsapp_message_id"])}
    result = _greenie_meta_post_message(str(conversation["phone_number_id"]), payload)
    messages = result.get("messages") or []
    whatsapp_message_id = messages[0].get("id") if messages and isinstance(messages[0], dict) else None
    label = body.caption or {
        "image": "[Imagen enviada]",
        "audio": "[Audio enviado]",
        "video": "[Video enviado]",
        "document": f"[Documento enviado] {filename}",
        "sticker": "[Sticker enviado]",
    }.get(media_type, "[Archivo enviado]")
    row = db.execute(text("""
        INSERT INTO whatsapp_messages(
            conversation_id, whatsapp_message_id, direction,
            message_type, body, status, sent_at, raw_payload,
            media_id, mime_type, filename, caption, media_size,
            reply_to_whatsapp_message_id
        ) VALUES (
            :conversation_id, :whatsapp_message_id, 'outbound',
            :message_type, :body, 'accepted', now(), CAST(:raw_payload AS JSONB),
            :media_id, :mime_type, :filename, :caption, :media_size,
            :reply_to_whatsapp_message_id
        )
        RETURNING id, sent_at
    """), {
        "conversation_id": conversation_id,
        "whatsapp_message_id": whatsapp_message_id,
        "message_type": media_type,
        "body": label,
        "raw_payload": json.dumps(result, ensure_ascii=False),
        "media_id": media_id,
        "mime_type": mime_type,
        "filename": filename,
        "caption": body.caption,
        "media_size": len(content),
        "reply_to_whatsapp_message_id": str(target["whatsapp_message_id"]) if target else None,
    }).mappings().one()
    db.execute(text("""
        UPDATE whatsapp_conversations
        SET last_message_at = :sent_at, updated_at = now()
        WHERE id = :id
    """), {"id": conversation_id, "sent_at": row["sent_at"]})
    db.commit()
    return {
        "ok": True,
        "id": row["id"],
        "message_id": whatsapp_message_id,
        "media_id": media_id,
        "message_type": media_type,
        "status": "accepted",
    }


@router.post("/conversations/{conversation_id}/assign")
def assign_conversation(
    conversation_id: int,
    body: AssignConversationBody,
    db: Session = Depends(get_db),
):
    brand_code = body.brand_code.strip().upper()
    if brand_code not in BRANDS:
        raise HTTPException(status_code=400, detail="Marca inválida")
    brand_name, executive_name = BRANDS[brand_code]
    updated = db.execute(text("""
        UPDATE whatsapp_conversations
        SET brand_code = :brand_code, executive_name = :executive_name, updated_at = now()
        WHERE id = :id
        RETURNING id
    """), {
        "id": conversation_id,
        "brand_code": brand_code,
        "executive_name": executive_name,
    }).scalar()
    if not updated:
        raise HTTPException(status_code=404, detail="Conversación no encontrada")
    db.commit()
    return {
        "ok": True,
        "brand_code": brand_code,
        "brand_name": brand_name,
        "executive_name": executive_name,
    }


@router.post("/conversations/{conversation_id}/create-lead")
def create_lead_from_whatsapp(
    conversation_id: int,
    body: CreateLeadBody,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    _ensure_tables(db)
    if body.fecha_evento < date.today():
        raise HTTPException(
            status_code=400,
            detail="La fecha del evento no puede ser anterior a hoy",
        )
    conversation = _conversation_or_404(db, conversation_id)
    # La marca comercial nace del canal/número que recibió el mensaje. La copia
    # guardada en la conversación se usa solo como compatibilidad para registros
    # históricos creados antes de configurar whatsapp_channels.
    channel = db.execute(text("""
        SELECT brand_code, brand_name, executive_name
        FROM whatsapp_channels
        WHERE phone_number_id = :phone_number_id
          AND enabled = TRUE
        ORDER BY updated_at DESC NULLS LAST, id DESC
        LIMIT 1
    """), {"phone_number_id": conversation["phone_number_id"]}).mappings().first()
    brand_code = str(
        (channel or {}).get("brand_code") or conversation.get("brand_code") or ""
    ).strip().upper()
    if brand_code not in BRANDS:
        raise HTTPException(
            status_code=400,
            detail="El número de WhatsApp receptor no tiene una marca configurada",
        )

    fallback_brand_name, default_executive_name = BRANDS[brand_code]
    executive_name = str(
        (channel or {}).get("executive_name")
        or conversation.get("executive_name")
        or default_executive_name
    ).strip()
    brand_db = _greenie_brand_from_db(
        db,
        brand_code=brand_code,
        fallback_name=fallback_brand_name,
    )
    marca = int(brand_db["id_marca"])
    brand_name = str(brand_db["brand_name"])

    estado = db.execute(text("""
        SELECT id_estado
        FROM public.estados_lead
        WHERE UPPER(nombre) LIKE '%NUEVO%'
           OR UPPER(nombre) LIKE '%ATENDIDO%'
        ORDER BY CASE WHEN UPPER(nombre) LIKE '%NUEVO%' THEN 0 ELSE 1 END, id_estado
        LIMIT 1
    """)).scalar() or 1

    id_comuna = None
    comuna = (body.comuna or "").strip()
    if comuna:
        id_comuna = db.execute(text("""
            SELECT id_comuna
            FROM public.comunas
            WHERE UPPER(COALESCE(nombre, comuna)) = UPPER(:comuna)
               OR UPPER(COALESCE(nombre, comuna)) LIKE UPPER(:prefix)
            ORDER BY id_comuna
            LIMIT 1
        """), {"comuna": comuna, "prefix": comuna + "%"}).scalar()
        if not id_comuna:
            raise HTTPException(status_code=400, detail="Selecciona una comuna valida de la lista")

    actor = str(user.get("name") or user.get("username") or user.get("email") or "Usuario CRM")
    notas = (
        "[WHATSAPP GIA]\n"
        f"Conversación: {conversation_id}\n"
        f"Marca: {brand_name}\n"
        f"Ejecutivo: {executive_name}\n"
        f"Creado por: {actor}\n"
        f"Teléfono: {_phone_e164_cl(conversation['wa_id'])}"
    )
    lead_id = db.execute(text("""
        INSERT INTO public.leads(
            cliente, telefono, id_marca, id_estado, id_comuna,
            fecha_evento, monto_cotizado, plataforma, notas,
            created_at, updated_at
        ) VALUES (
            :cliente, :telefono, :id_marca, :id_estado, :id_comuna,
            :fecha_evento, 0, 'WHATSAPP', :notas,
            now(), now()
        )
        RETURNING id_lead
    """), {
        "cliente": body.nombre.strip(),
        "telefono": _phone_e164_cl(conversation["wa_id"]),
        "id_marca": marca,
        "id_estado": int(estado),
        "id_comuna": int(id_comuna) if id_comuna else None,
        "fecha_evento": body.fecha_evento,
        "notas": notas,
    }).scalar_one()
    db.execute(text("""
        INSERT INTO whatsapp_conversation_leads(conversation_id, lead_id)
        VALUES (:conversation_id, :lead_id)
        ON CONFLICT (conversation_id, lead_id) DO NOTHING
    """), {"conversation_id": conversation_id, "lead_id": int(lead_id)})
    db.execute(text("""
        UPDATE whatsapp_conversations
        SET brand_code = :brand_code,
            executive_name = :executive_name,
            updated_at = now()
        WHERE id = :conversation_id
    """), {
        "conversation_id": conversation_id,
        "brand_code": brand_code,
        "executive_name": executive_name,
    })
    db.commit()
    return {
        "ok": True,
        "id_lead": int(lead_id),
        "cliente_existia": bool(_existing_leads(db, str(conversation["wa_id"]))[:-1]),
        "brand_code": brand_code,
        "brand_name": brand_name,
        "executive_name": executive_name,
    }


@router.post("/conversations/{conversation_id}/close")
def close_conversation(conversation_id: int, db: Session = Depends(get_db)):
    updated = db.execute(text("""
        UPDATE whatsapp_conversations
        SET status = 'closed', unread_count = 0, updated_at = now()
        WHERE id = :id
        RETURNING id
    """), {"id": conversation_id}).scalar()
    if not updated:
        raise HTTPException(status_code=404, detail="Conversación no encontrada")
    db.commit()
    return {"ok": True}


@router.post("/conversations/{conversation_id}/reopen")
def reopen_conversation(conversation_id: int, db: Session = Depends(get_db)):
    updated = db.execute(text("""
        UPDATE whatsapp_conversations
        SET status = 'open', updated_at = now()
        WHERE id = :id
        RETURNING id
    """), {"id": conversation_id}).scalar()
    if not updated:
        raise HTTPException(status_code=404, detail="Conversación no encontrada")
    db.commit()
    return {"ok": True}
