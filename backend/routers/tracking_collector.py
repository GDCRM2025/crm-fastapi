from __future__ import annotations

import re
import threading
import time
from collections import defaultdict, deque
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text

from backend.core.database import engine
from backend.gd_intelligence.tracking import record_event, upsert_session


router = APIRouter(prefix="/collect/v1", tags=["public-tracking-collector"])

SESSION_KEYS = frozenset({"site_code", "visitor_id", "session_id", "landing_url", "referrer", "utm"})
EVENT_KEYS = frozenset({"site_code", "session_id", "event_id", "event_type", "page_url", "metadata", "timestamp"})
UTM_KEYS = frozenset({"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"})
METADATA_KEYS = frozenset({"click_id", "language", "viewport_width", "viewport_height", "timezone"})
SENSITIVE_KEY = re.compile(r"pass|secret|token|authorization|cookie|card|cvv|rut|email|phone|telefono|message|body", re.I)
MAX_BODY_BYTES = 16_384
RATE_WINDOW_SECONDS = 60
RATE_LIMIT = 180
_rate_lock = threading.Lock()
_rate_hits: dict[str, deque[float]] = defaultdict(deque)


def _origin_host(origin: str) -> str:
    parsed = urlsplit(str(origin or "").strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise HTTPException(403, "Origen HTTPS no autorizado")
    return parsed.hostname.lower().removeprefix("www.")


def _cors_headers(origin: str) -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Max-Age": "600",
        "Vary": "Origin",
        "Cache-Control": "no-store",
    }


def _client_key(request: Request, site_code: str) -> str:
    host = request.client.host if request.client else "unknown"
    return f"{host}:{site_code}"


def _rate_limit(request: Request, site_code: str) -> None:
    now = time.monotonic()
    key = _client_key(request, site_code)
    with _rate_lock:
        hits = _rate_hits[key]
        while hits and now - hits[0] >= RATE_WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= RATE_LIMIT:
            raise HTTPException(429, "Demasiadas solicitudes de tracking")
        hits.append(now)


def _reject_sensitive(value: Any, path: str = "payload") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if SENSITIVE_KEY.search(str(key)):
                raise HTTPException(422, f"Campo no permitido: {path}.{key}")
            _reject_sensitive(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value[:50]):
            _reject_sensitive(item, f"{path}[{index}]")


def _allowlist(payload: dict[str, Any], allowed: frozenset[str]) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise HTTPException(422, f"Campos no permitidos: {', '.join(unknown[:5])}")
    _reject_sensitive(payload)
    if isinstance(payload.get("utm"), dict):
        unknown_utm = sorted(set(payload["utm"]) - UTM_KEYS)
        if unknown_utm:
            raise HTTPException(422, "Parámetros UTM no permitidos")
    if isinstance(payload.get("metadata"), dict):
        unknown_meta = sorted(set(payload["metadata"]) - METADATA_KEYS)
        if unknown_meta:
            raise HTTPException(422, "Metadatos no permitidos")


def _site(conn, site_code: str, origin_host: str) -> dict[str, Any]:
    row = conn.execute(text("""
      SELECT id,code,lower(regexp_replace(domain,'^www\\.','','i')) AS domain
      FROM public.wi_sites WHERE code=:code AND enabled
    """), {"code": site_code}).mappings().one_or_none()
    if not row:
        raise HTTPException(404, "Sitio no autorizado")
    if str(row["domain"] or "").rstrip("/") != origin_host:
        raise HTTPException(403, "El origen no corresponde al sitio")
    return dict(row)


def _metric(conn, site_code: str, **increments: int) -> None:
    allowed = {"events_received", "events_accepted", "events_rejected", "sessions_created", "duplicate_events"}
    safe = {key: int(value) for key, value in increments.items() if key in allowed and value}
    if not safe:
        return
    columns = ",".join(safe)
    values = ",".join(f":{key}" for key in safe)
    updates = ",".join(f"{key}=wi_tracking_collector_metrics.{key}+EXCLUDED.{key}" for key in safe)
    conn.execute(text(f"""
      INSERT INTO public.wi_tracking_collector_metrics(metric_date,site_code,{columns},last_event_at)
      VALUES (CURRENT_DATE,:site_code,{values},now())
      ON CONFLICT(metric_date,site_code) DO UPDATE SET {updates},last_event_at=now(),updated_at=now()
    """), {"site_code": site_code[:16], **safe})


def _preflight(request: Request) -> Response:
    origin = request.headers.get("origin") or ""
    host = _origin_host(origin)
    with engine.begin() as conn:
        allowed = conn.execute(text("""
          SELECT 1 FROM public.wi_sites
          WHERE enabled AND lower(regexp_replace(domain,'^www\\.','','i'))=:domain
          LIMIT 1
        """), {"domain": host}).scalar()
        if not allowed:
            raise HTTPException(403, "Origen no autorizado")
    return Response(status_code=204, headers=_cors_headers(origin))


@router.options("/session")
def session_options(request: Request):
    return _preflight(request)


@router.options("/event")
def event_options(request: Request):
    return _preflight(request)


def _validate_request(request: Request, payload: dict[str, Any], allowed: frozenset[str]) -> tuple[str, str]:
    if int(request.headers.get("content-length") or 0) > MAX_BODY_BYTES:
        raise HTTPException(413, "Payload demasiado grande")
    _allowlist(payload, allowed)
    site_code = str(payload.get("site_code") or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9_]{2,16}", site_code):
        raise HTTPException(422, "site_code inválido")
    origin = request.headers.get("origin") or ""
    return site_code, _origin_host(origin)


def _require_page_host(payload: dict[str, Any], origin_host: str, field: str) -> None:
    value = str(payload.get(field) or "").strip()
    if not value:
        return
    host = (urlsplit(value).hostname or "").lower().removeprefix("www.")
    if host != origin_host:
        raise HTTPException(422, f"{field} no corresponde al origen")


@router.post("/session", status_code=201)
def collect_session(request: Request, payload: dict[str, Any] = Body(...)):
    site_code, origin_host = _validate_request(request, payload, SESSION_KEYS)
    _require_page_host(payload, origin_host, "landing_url")
    _rate_limit(request, site_code)
    origin = request.headers.get("origin") or ""
    try:
        with engine.begin() as conn:
            site = _site(conn, site_code, origin_host)
            item = upsert_session(conn, site_id=int(site["id"]), payload=payload, user_agent=request.headers.get("user-agent"))
            _metric(conn, site_code, sessions_created=1)
    except ValueError as exc:
        with engine.begin() as conn:
            _metric(conn, site_code, events_rejected=1)
        raise HTTPException(422, str(exc)) from exc
    return JSONResponse({"ok": True, "session_id": str(item["session_id"])}, status_code=201, headers=_cors_headers(origin))


@router.post("/event", status_code=201)
def collect_event(request: Request, payload: dict[str, Any] = Body(...)):
    site_code, origin_host = _validate_request(request, payload, EVENT_KEYS)
    _require_page_host(payload, origin_host, "page_url")
    _rate_limit(request, site_code)
    origin = request.headers.get("origin") or ""
    try:
        session_error = False
        with engine.begin() as conn:
            site = _site(conn, site_code, origin_host)
            _metric(conn, site_code, events_received=1)
            session_site = conn.execute(text("SELECT site_id FROM public.wi_sessions WHERE session_id=:session_id"), {"session_id": payload.get("session_id")}).scalar()
            if not session_site or int(session_site) != int(site["id"]):
                _metric(conn, site_code, events_rejected=1)
                session_error = True
                item = {"duplicate": False}
            else:
                item = record_event(conn, site_id=int(site["id"]), payload=payload)
                _metric(conn, site_code, duplicate_events=1 if item.get("duplicate") else 0, events_accepted=0 if item.get("duplicate") else 1)
        if session_error:
            raise HTTPException(422, "La sesión no pertenece al sitio")
    except ValueError as exc:
        with engine.begin() as conn:
            _metric(conn, site_code, events_rejected=1)
        raise HTTPException(422, str(exc)) from exc
    return JSONResponse({"ok": True, "duplicate": bool(item.get("duplicate"))}, status_code=201, headers=_cors_headers(origin))
