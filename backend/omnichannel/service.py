from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import text


CHANNELS = ("WHATSAPP", "INSTAGRAM", "MESSENGER", "EMAIL")


@dataclass(frozen=True)
class ChannelCapability:
    channel: str
    source: str
    receive: bool
    reply: bool
    create_lead: bool
    link_lead: bool
    assign: bool
    follow_up: bool
    quote: bool
    state: str
    explanation: str
    action_route: str | None = None


CAPABILITIES: dict[str, ChannelCapability] = {
    "WHATSAPP": ChannelCapability(
        "WHATSAPP", "whatsapp_conversations", True, True, True, True, True, True, True,
        "AVAILABLE", "Recepción, respuesta y gestión comercial operativas.",
        "/web/views/tools.html?v=20260807-greenie-live-v1#whatsapp",
    ),
    "EMAIL": ChannelCapability(
        "EMAIL", "gia_email_messages", True, True, True, True, True, True, True,
        "AVAILABLE", "Recepción, respuesta y creación de lead operativas cuando la cuenta está configurada.",
        "/web/views/tools.html?v=20260807-greenie-live-v1#correo",
    ),
    "INSTAGRAM": ChannelCapability(
        "INSTAGRAM", "gia_ig_events", True, False, False, True, True, True, False,
        "RECEIVE_ONLY", "El webhook conserva eventos reales; el envío Graph API aún no está habilitado.",
        "/web/views/tools.html?v=20260807-greenie-live-v1#instagram",
    ),
    "MESSENGER": ChannelCapability(
        "MESSENGER", "gia_ig_events", True, False, False, True, True, True, False,
        "RECEIVE_ONLY", "Los eventos Page se identifican sin inventar una capacidad de respuesta.", None,
    ),
}


def capability_payload() -> list[dict[str, Any]]:
    return [asdict(CAPABILITIES[channel]) for channel in CHANNELS]


def _table_exists(db, table: str) -> bool:
    return bool(db.execute(text("SELECT to_regclass(:name)"), {"name": f"public.{table}"}).scalar())


def source_brand_ref(db, channel: str, source_ref: str) -> str | None:
    """Confirms a work item points to a real source row, without copying it."""
    normalized = str(channel).upper()
    table, key = {
        "WHATSAPP": ("whatsapp_conversations", "id"),
        "EMAIL": ("gia_email_messages", "id_msg"),
        "INSTAGRAM": ("gia_ig_events", "id"),
        "MESSENGER": ("gia_ig_events", "id"),
    }.get(normalized, ("", ""))
    if not table or not _table_exists(db, table):
        return None
    if table == "gia_ig_events":
        statement = text(f"SELECT object_name, brand_id::text FROM public.{table} WHERE {key}::text=:ref")
    elif table == "whatsapp_conversations":
        statement = text(f"SELECT NULL, brand_code::text FROM public.{table} WHERE {key}::text=:ref")
    else:
        statement = text(f"SELECT NULL, id_marca::text FROM public.{table} WHERE {key}::text=:ref")
    row = db.execute(statement, {"ref": source_ref}).first()
    if not row:
        return None
    if table != "gia_ig_events":
        return str(row[1] or "")
    object_name = str(row[0] or "").strip().lower()
    matches_channel = object_name == ("instagram" if normalized == "INSTAGRAM" else "page")
    return str(row[1] or "") if matches_channel else None


def source_exists(db, channel: str, source_ref: str) -> bool:
    return source_brand_ref(db, channel, source_ref) is not None


def can_access_brand(user: dict[str, Any], brand_ref: Any) -> bool:
    role = str(user.get("role") or user.get("rol") or "").upper()
    if "ADMIN" in role or "SUPER" in role:
        return True
    allowed: set[str] = set()
    for raw in user.get("marcas") or user.get("brands") or []:
        if isinstance(raw, dict):
            for key in ("id_marca", "id", "brand_code", "codigo", "marca", "nombre"):
                value = raw.get(key)
                if value is not None and str(value).strip():
                    allowed.add(str(value).strip().upper())
        elif raw is not None:
            allowed.add(str(raw).strip().upper())
    return bool(allowed) and str(brand_ref or "").strip().upper() in allowed


def visible_items(items: Iterable[dict[str, Any]], user: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in items if can_access_brand(user, item.get("brand_ref"))]


def _rows(db, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in db.execute(text(sql), params).mappings().all()]


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _whatsapp_items(db, limit: int) -> list[dict[str, Any]]:
    if not _table_exists(db, "whatsapp_conversations"):
        return []
    rows = _rows(db, """
        SELECT c.id::text AS source_ref, c.brand_code AS brand_ref,
               COALESCE(ct.profile_name, ct.wa_id, 'Contacto WhatsApp') AS contact_name,
               ct.wa_id AS contact_ref, c.last_message_at AS occurred_at,
               COALESCE(c.unread_count, 0) AS unread_count,
               COALESCE(m.body, '') AS preview, COALESCE(m.direction, '') AS direction,
               COALESCE(w.lead_id, c.selected_lead_id, lnk.lead_id) AS lead_id,
               w.quote_id, COALESCE(w.assigned_user_name, c.assigned_user_name, c.executive_name) AS assigned_to,
               w.follow_up_at, COALESCE(w.status, upper(c.status), 'OPEN') AS work_status
        FROM public.whatsapp_conversations c
        JOIN public.whatsapp_contacts ct ON ct.id=c.contact_id
        LEFT JOIN LATERAL (
          SELECT body, direction FROM public.whatsapp_messages
          WHERE conversation_id=c.id ORDER BY sent_at DESC, id DESC LIMIT 1
        ) m ON TRUE
        LEFT JOIN LATERAL (
          SELECT lead_id FROM public.whatsapp_conversation_leads
          WHERE conversation_id=c.id ORDER BY created_at DESC LIMIT 1
        ) lnk ON TRUE
        LEFT JOIN public.wi_omnichannel_work_items w
          ON w.channel='WHATSAPP' AND w.source_ref=c.id::text
        ORDER BY c.last_message_at DESC NULLS LAST, c.id DESC LIMIT :limit
    """, {"limit": limit})
    return [_normalize("WHATSAPP", row) for row in rows]


def _email_items(db, limit: int) -> list[dict[str, Any]]:
    if not _table_exists(db, "gia_email_messages"):
        return []
    rows = _rows(db, """
        SELECT e.id_msg::text AS source_ref, e.id_marca::text AS brand_ref,
               COALESCE(NULLIF(e.from_name,''), NULLIF(e.from_email,''), 'Contacto Email') AS contact_name,
               e.from_email AS contact_ref, COALESCE(e.received_at,e.created_at) AS occurred_at,
               CASE WHEN COALESCE(e.reply_sent,false) THEN 0 ELSE 1 END AS unread_count,
               COALESCE(e.subject,'') AS preview, 'inbound' AS direction,
               COALESCE(w.lead_id,e.lead_id) AS lead_id, w.quote_id,
               w.assigned_user_name AS assigned_to, w.follow_up_at,
               COALESCE(w.status, CASE WHEN COALESCE(e.reply_sent,false) THEN 'RESOLVED' ELSE 'OPEN' END) AS work_status
        FROM public.gia_email_messages e
        LEFT JOIN public.wi_omnichannel_work_items w
          ON w.channel='EMAIL' AND w.source_ref=e.id_msg::text
        ORDER BY COALESCE(e.received_at,e.created_at) DESC, e.id_msg DESC LIMIT :limit
    """, {"limit": limit})
    return [_normalize("EMAIL", row) for row in rows]


def _meta_items(db, limit: int) -> list[dict[str, Any]]:
    if not _table_exists(db, "gia_ig_events"):
        return []
    rows = _rows(db, """
        SELECT e.id::text AS source_ref, e.brand_id AS brand_ref,
               COALESCE(e.payload #>> '{entry,0,messaging,0,sender,id}', 'Contacto Meta') AS contact_name,
               e.payload #>> '{entry,0,messaging,0,sender,id}' AS contact_ref,
               e.received_at AS occurred_at, 1 AS unread_count,
               COALESCE(e.payload #>> '{entry,0,messaging,0,message,text}', e.event_source, '') AS preview,
               'inbound' AS direction, w.lead_id, w.quote_id, w.assigned_user_name AS assigned_to,
               w.follow_up_at, COALESCE(w.status,'OPEN') AS work_status,
               CASE WHEN lower(e.object_name)='instagram' THEN 'INSTAGRAM' ELSE 'MESSENGER' END AS channel
        FROM public.gia_ig_events e
        LEFT JOIN public.wi_omnichannel_work_items w
          ON w.channel=(CASE WHEN lower(e.object_name)='instagram' THEN 'INSTAGRAM' ELSE 'MESSENGER' END)
         AND w.source_ref=e.id::text
        WHERE e.event_source='messaging' AND lower(COALESCE(e.object_name,'')) IN ('instagram','page')
        ORDER BY e.received_at DESC, e.id DESC LIMIT :limit
    """, {"limit": limit})
    return [_normalize(str(row.pop("channel")), row) for row in rows]


def _normalize(channel: str, row: dict[str, Any]) -> dict[str, Any]:
    capability = CAPABILITIES[channel]
    return {
        "channel": channel,
        "source_ref": str(row.get("source_ref") or ""),
        "brand_ref": row.get("brand_ref"),
        "contact_name": row.get("contact_name"),
        "contact_ref": row.get("contact_ref"),
        "occurred_at": _iso(row.get("occurred_at")),
        "unread_count": int(row.get("unread_count") or 0),
        "preview": str(row.get("preview") or "")[:240],
        "direction": row.get("direction"),
        "lead_id": row.get("lead_id"),
        "quote_id": row.get("quote_id"),
        "assigned_to": row.get("assigned_to"),
        "follow_up_at": _iso(row.get("follow_up_at")),
        "status": str(row.get("work_status") or "OPEN").upper(),
        "capabilities": {
            "reply": capability.reply,
            "create_lead": capability.create_lead,
            "link_lead": capability.link_lead,
            "assign": capability.assign,
            "follow_up": capability.follow_up,
            "quote": capability.quote,
        },
        "action_route": capability.action_route,
    }


def list_inbox(db, *, channels: Iterable[str] | None = None, limit: int = 100) -> list[dict[str, Any]]:
    requested = {str(value).upper() for value in (channels or CHANNELS)} & set(CHANNELS)
    per_source_limit = max(1, min(int(limit), 200))
    items: list[dict[str, Any]] = []
    if "WHATSAPP" in requested:
        items.extend(_whatsapp_items(db, per_source_limit))
    if "EMAIL" in requested:
        items.extend(_email_items(db, per_source_limit))
    if requested & {"INSTAGRAM", "MESSENGER"}:
        items.extend(item for item in _meta_items(db, per_source_limit) if item["channel"] in requested)
    items.sort(key=lambda item: item.get("occurred_at") or "", reverse=True)
    return items[:per_source_limit]


def funnel_metrics(items: Iterable[dict[str, Any]], quote_rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    quotes_by_lead: dict[int, list[dict[str, Any]]] = {}
    for quote in quote_rows:
        lead_id = quote.get("lead_id")
        if lead_id is not None:
            quotes_by_lead.setdefault(int(lead_id), []).append(quote)
    result = []
    for channel in CHANNELS:
        channel_items = [item for item in items if item.get("channel") == channel]
        lead_ids = {int(item["lead_id"]) for item in channel_items if item.get("lead_id") is not None}
        quotes = [q for lead_id in lead_ids for q in quotes_by_lead.get(lead_id, [])]
        sales = [q for q in quotes if bool(q.get("is_sale"))]
        result.append({
            "channel": channel,
            "conversations": len({item.get("source_ref") for item in channel_items}),
            "leads": len(lead_ids),
            "quotes": len(quotes),
            "sales": len(sales),
            "revenue": round(sum(float(q.get("revenue") or 0) for q in sales), 2),
            "data_state": "AVAILABLE" if channel_items else "NO_DATA",
        })
    return result
