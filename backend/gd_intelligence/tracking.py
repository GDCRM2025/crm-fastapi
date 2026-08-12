from __future__ import annotations

import json
import re
import uuid
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from sqlalchemy import text

from backend.gd_intelligence.paid_media_intelligence import click_ids_from_url


UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content")
EVENT_TYPES = {"session_start", "page_view", "click_whatsapp", "form_start", "form_submit", "lead_created"}
PUBLIC_QUERY_KEYS = frozenset((*UTM_KEYS, "gclid", "fbclid"))


def valid_uuid(value: Any, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"{field} debe ser UUID") from exc


def parse_utm(url: str) -> dict[str, str | None]:
    query = parse_qs(urlsplit(str(url or "")).query, keep_blank_values=False)
    return {key: (query.get(key) or [None])[0] for key in UTM_KEYS}


def safe_public_url(value: Any, *, keep_marketing_query: bool = True) -> str:
    """Minimize public URLs while preserving only attribution parameters."""
    raw = str(value or "").strip()[:4096]
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL pública debe ser HTTP(S)")
    query: list[tuple[str, str]] = []
    if keep_marketing_query:
        for key, values in parse_qs(parsed.query, keep_blank_values=False).items():
            if key.lower() not in PUBLIC_QUERY_KEYS:
                continue
            query.extend((key.lower(), str(item)[:500]) for item in values[:1])
    host = parsed.hostname.lower()
    if parsed.port and not ((parsed.scheme == "https" and parsed.port == 443) or (parsed.scheme == "http" and parsed.port == 80)):
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path or "/", urlencode(query), ""))[:2048]


def device_from_user_agent(user_agent: str | None) -> str:
    value = str(user_agent or "")
    if re.search(r"ipad|tablet", value, re.I):
        return "TABLET"
    if re.search(r"mobile|iphone|android", value, re.I):
        return "MOBILE"
    return "DESKTOP"


def public_event_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    blocked = re.compile(r"pass|secret|token|authorization|cookie|email|phone|telefono", re.I)
    safe = {str(k)[:60]: v for k, v in value.items() if not blocked.search(str(k))}
    raw = json.dumps(safe, default=str)
    if len(raw) > 4000:
        return {"truncated": True}
    return safe


def attribution_touches(previous_first: dict[str, Any] | None, current: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """First touch is immutable; last touch always reflects the newest evidenced session."""
    first = dict(previous_first or current)
    return first, dict(current)


def upsert_session(conn, *, site_id: int, payload: dict[str, Any], user_agent: str | None = None) -> dict[str, Any]:
    visitor_id = valid_uuid(payload.get("visitor_id"), "visitor_id")
    session_id = valid_uuid(payload.get("session_id"), "session_id")
    landing = safe_public_url(payload.get("landing_url"))
    parsed = parse_utm(landing)
    click_ids = click_ids_from_url(landing)
    supplied = payload.get("utm") if isinstance(payload.get("utm"), dict) else {}
    utm = {key: str(supplied.get(key) or parsed.get(key) or "").strip()[:200] or None for key in UTM_KEYS}
    params = {
        "site_id": int(site_id), "visitor_id": visitor_id, "session_id": session_id,
        "landing_url": landing, "referrer": safe_public_url(payload.get("referrer"), keep_marketing_query=False) if payload.get("referrer") else None,
        "device": device_from_user_agent(user_agent), **utm,
        "gclid_hash": click_ids["gclid"], "fbclid_hash": click_ids["fbclid"],
    }
    row = conn.execute(text("""
      INSERT INTO public.wi_sessions(
        site_id,visitor_id,session_id,landing_url,referrer,device,
        utm_source,utm_medium,utm_campaign,utm_term,utm_content,gclid_hash,fbclid_hash
      ) VALUES (
        :site_id,:visitor_id,:session_id,:landing_url,:referrer,:device,
        :utm_source,:utm_medium,:utm_campaign,:utm_term,:utm_content,:gclid_hash,:fbclid_hash
      ) ON CONFLICT(session_id) DO UPDATE SET last_seen_at=now()
      RETURNING id,site_id,visitor_id,session_id,started_at,last_seen_at
    """), params).mappings().one()
    return dict(row)


def record_event(conn, *, site_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    event_type = str(payload.get("event_type") or "").strip().lower()
    if event_type not in EVENT_TYPES:
        raise ValueError("event_type no soportado")
    params = {
        "site_id": int(site_id), "session_id": valid_uuid(payload.get("session_id"), "session_id"),
        "event_id": valid_uuid(payload.get("event_id"), "event_id"), "event_type": event_type,
        "page_url": safe_public_url(payload.get("page_url")) if payload.get("page_url") else None,
        "metadata": json.dumps(public_event_metadata(payload.get("metadata"))),
    }
    row = conn.execute(text("""
      INSERT INTO public.wi_events(site_id,session_id,event_id,event_type,page_url,metadata)
      VALUES (:site_id,:session_id,:event_id,:event_type,:page_url,CAST(:metadata AS jsonb))
      ON CONFLICT(event_id) DO NOTHING
      RETURNING id,event_id,event_type,occurred_at
    """), params).mappings().one_or_none()
    if row:
        return {**dict(row), "duplicate": False}
    existing = conn.execute(text("""
      SELECT id,event_id,event_type,occurred_at FROM public.wi_events WHERE event_id=:event_id
    """), {"event_id": params["event_id"]}).mappings().one()
    return {**dict(existing), "duplicate": True}


def attach_lead_attribution(conn, *, lead_id: int, session_id: Any, method: str = "SESSION_ID", confidence: float = 1.0) -> dict[str, Any] | None:
    sid = valid_uuid(session_id, "session_id")
    session = conn.execute(text("SELECT * FROM public.wi_sessions WHERE session_id=:sid"), {"sid": sid}).mappings().one_or_none()
    if not session:
        return None
    touch = {key: session.get(key) for key in ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "landing_url", "referrer")}
    campaign_id = conn.execute(text("""
      SELECT id FROM public.wi_marketing_campaigns
      WHERE site_id=:site_id AND lower(name)=lower(:name) LIMIT 1
    """), {"site_id": session["site_id"], "name": session.get("utm_campaign") or ""}).scalar()
    row = conn.execute(text("""
      INSERT INTO public.wi_lead_attribution(
        lead_id,session_id,campaign_id,first_touch,last_touch,source,medium,campaign,
        landing_url,device,attribution_method,confidence
      ) VALUES (
        :lead_id,:session_id,:campaign_id,CAST(:touch AS jsonb),CAST(:touch AS jsonb),:source,:medium,:campaign,
        :landing,:device,:method,:confidence
      ) ON CONFLICT(lead_id) DO UPDATE SET
        session_id=excluded.session_id,campaign_id=excluded.campaign_id,last_touch=excluded.last_touch,
        source=excluded.source,medium=excluded.medium,campaign=excluded.campaign,landing_url=excluded.landing_url,
        device=excluded.device,attribution_method=excluded.attribution_method,confidence=excluded.confidence,updated_at=now()
      RETURNING *
    """), {
        "lead_id": int(lead_id), "session_id": sid, "campaign_id": campaign_id,
        "touch": json.dumps(touch), "source": session.get("utm_source"), "medium": session.get("utm_medium"),
        "campaign": session.get("utm_campaign"), "landing": session.get("landing_url"),
        "device": session.get("device"), "method": str(method)[:60], "confidence": max(0, min(float(confidence), 1)),
    }).mappings().one()
    return dict(row)


def waba_confidence(*, session_id_match: bool, click_id_match: bool, phone_match: bool) -> tuple[str, float] | None:
    if session_id_match and click_id_match:
        return ("WABA_CLICK_ID", 1.0)
    if session_id_match:
        return ("WABA_SESSION_ID", 0.95)
    if click_id_match:
        return ("WABA_CLICK_ID", 0.9)
    if phone_match:
        return ("WABA_PHONE_TIME_WINDOW", 0.65)
    return None
