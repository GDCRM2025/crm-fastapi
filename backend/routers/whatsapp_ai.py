from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.routers.auth import get_current_user


router = APIRouter(prefix="/gia/whatsapp", tags=["WhatsApp Greenie AI"])


def _output_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    parts: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            value = content.get("text")
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
    return "\n".join(parts).strip()


def _conversation_context(db: Session, conversation_id: int) -> dict[str, Any]:
    conversation = db.execute(
        text(
            """
            SELECT
                c.id,
                c.phone_number_id,
                c.selected_lead_id,
                c.status,
                c.executive_name,
                ct.wa_id,
                ct.profile_name,
                ch.brand_code,
                ch.brand_name,
                ch.executive_name AS channel_executive_name
            FROM whatsapp_conversations c
            JOIN whatsapp_contacts ct ON ct.id=c.contact_id
            LEFT JOIN whatsapp_channels ch
              ON ch.phone_number_id=c.phone_number_id
             AND COALESCE(ch.enabled, TRUE)=TRUE
            WHERE c.id=:id
            LIMIT 1
            """
        ),
        {"id": conversation_id},
    ).mappings().first()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversación no encontrada")

    messages = db.execute(
        text(
            """
            SELECT direction, message_type, body, caption, sent_at, status
            FROM whatsapp_messages
            WHERE conversation_id=:id
              AND COALESCE(message_type,'') <> 'reaction'
            ORDER BY sent_at DESC NULLS LAST, id DESC
            LIMIT 50
            """
        ),
        {"id": conversation_id},
    ).mappings().all()
    messages = list(reversed([dict(row) for row in messages]))

    lead = None
    quotes: list[dict[str, Any]] = []
    lead_id = conversation.get("selected_lead_id")
    if lead_id:
        try:
            lead_row = db.execute(
                text(
                    """
                    SELECT
                        l.id_lead,
                        l.cliente,
                        l.email,
                        l.telefono,
                        l.fecha_evento,
                        l.direccion,
                        l.monto_cotizado,
                        l.notas,
                        l.num_cotizacion,
                        COALESCE(m.marca, m.nombre, '') AS marca,
                        COALESCE(e.nombre, '') AS estado,
                        COALESCE(co.nombre, co.comuna, '') AS comuna
                    FROM leads l
                    LEFT JOIN marcas m ON m.id_marca=l.id_marca
                    LEFT JOIN estados_lead e ON e.id_estado=l.id_estado
                    LEFT JOIN comunas co ON co.id_comuna=l.id_comuna
                    WHERE l.id_lead=:lead_id
                    LIMIT 1
                    """
                ),
                {"lead_id": lead_id},
            ).mappings().first()
            lead = dict(lead_row) if lead_row else None
        except Exception:
            db.rollback()
            lead_row = db.execute(
                text("SELECT * FROM leads WHERE id_lead=:lead_id LIMIT 1"),
                {"lead_id": lead_id},
            ).mappings().first()
            lead = dict(lead_row) if lead_row else None

        try:
            quote_rows = db.execute(
                text(
                    """
                    SELECT id_cotizacion, numero, total, estado, fecha, fecha_evento
                    FROM cotizaciones
                    WHERE id_lead=:lead_id
                    ORDER BY id_cotizacion DESC
                    LIMIT 10
                    """
                ),
                {"lead_id": lead_id},
            ).mappings().all()
            quotes = [dict(row) for row in quote_rows]
        except Exception:
            db.rollback()
            quotes = []

    return {
        "conversation": dict(conversation),
        "messages": messages,
        "selected_lead": lead,
        "quotes": quotes,
    }


def _call_openai(context: dict[str, Any]) -> dict[str, Any]:
    api_key = str(os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="Falta OPENAI_API_KEY en el .env del CRM",
        )

    model = str(os.getenv("GREENIE_AI_MODEL") or "gpt-5-mini").strip()
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "urgency": {
                "type": "string",
                "enum": ["low", "medium", "high"],
            },
            "intent": {"type": "string"},
            "summary": {"type": "string"},
            "missing_information": {
                "type": "array",
                "items": {"type": "string"},
            },
            "recommended_action": {"type": "string"},
            "suggested_reply": {"type": "string"},
            "commercial_risks": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": [
            "urgency",
            "intent",
            "summary",
            "missing_information",
            "recommended_action",
            "suggested_reply",
            "commercial_risks",
        ],
    }

    instructions = (
        "Eres Greenie, copiloto comercial interno de GreenDiamond. "
        "Analiza una conversación real de WhatsApp junto con su lead y cotizaciones. "
        "Ayuda al ejecutivo a decidir, pero nunca inventes precios, disponibilidad, "
        "fechas, productos ni compromisos. Si falta información, decláralo. "
        "La respuesta sugerida debe ser breve, profesional, natural, en español de Chile, "
        "y no debe afirmar que una acción fue realizada. No envíes nada automáticamente."
    )
    request_payload = {
        "model": model,
        "store": False,
        "instructions": instructions,
        "input": json.dumps(context, ensure_ascii=False, default=str),
        "max_output_tokens": 1400,
        "text": {
            "verbosity": "low",
            "format": {
                "type": "json_schema",
                "name": "greenie_commercial_assist",
                "strict": True,
                "schema": schema,
            },
        },
    }

    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(request_payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise HTTPException(
            status_code=502,
            detail=f"OpenAI rechazó la solicitud: {detail[:500]}",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"No se pudo consultar Greenie IA: {exc}",
        ) from exc

    payload = json.loads(raw or "{}")
    output = _output_text(payload)
    if not output:
        raise HTTPException(status_code=502, detail="Greenie IA no devolvió contenido")
    try:
        result = json.loads(output)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="Greenie IA devolvió JSON inválido") from exc

    return {
        "ok": True,
        "model": model,
        "analysis": result,
        "usage": payload.get("usage") or {},
    }


@router.post("/conversations/{conversation_id}/ai-assist")
def ai_assist(
    conversation_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    context = _conversation_context(db, conversation_id)
    context["operator"] = {
        "name": user.get("name") or user.get("username") or user.get("email"),
        "role": user.get("role") or user.get("rol"),
    }
    return _call_openai(context)
