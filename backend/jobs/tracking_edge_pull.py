from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import HTTPException
from sqlalchemy import text

from backend.core.database import engine
from backend.gd_intelligence.tracking import record_event, upsert_session
from backend.routers import tracking_collector as validation


LOCK_KEY = 746_843_112


def _secret() -> str:
    candidates = [
        os.getenv("GD_TRACKING_EDGE_SECRET_FILE"),
        str(Path(os.getenv("CREDENTIALS_DIRECTORY", "")) / "GD_TRACKING_EDGE_SECRET")
        if os.getenv("CREDENTIALS_DIRECTORY")
        else None,
        "/opt/greendiamond/shared/secrets/bootstrap/tracking_edge_secret",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            value = Path(candidate).read_text(encoding="utf-8").strip()
            if len(value) >= 32:
                return value
    value = os.getenv("GD_TRACKING_EDGE_SECRET", "").strip()
    if len(value) >= 32:
        return value
    raise RuntimeError("GD_TRACKING_EDGE_SECRET no configurado")


def canonical_signature(secret: str, timestamp: str, nonce: str, method: str, path: str, body: bytes) -> str:
    body_hash = hashlib.sha256(body).hexdigest()
    canonical = "\n".join((timestamp, nonce, method.upper(), path, body_hash)).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()


class EdgeClient:
    def __init__(self, base_url: str, secret: str, timeout: float = 20.0):
        self.base_url = base_url.rstrip("/")
        if not self.base_url.startswith("https://"):
            raise RuntimeError("GD_TRACKING_EDGE_URL requiere HTTPS")
        self.secret = secret
        self.timeout = timeout

    def request(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        signature = canonical_signature(self.secret, timestamp, nonce, "POST", path, body)
        request = Request(
            self.base_url + path,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-GD-Timestamp": timestamp,
                "X-GD-Nonce": nonce,
                "X-GD-Signature": signature,
                "User-Agent": "GreenDiamond-Tracking-Pull/1",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read(1_000_001)
                if len(raw) > 1_000_000:
                    raise RuntimeError("Respuesta edge demasiado grande")
        except (HTTPError, URLError, TimeoutError) as exc:
            raise RuntimeError(f"Edge pull HTTP failure: {getattr(exc, 'code', 'network')}") from exc
        try:
            value = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise RuntimeError("Respuesta edge inválida") from exc
        if not isinstance(value, dict) or value.get("ok") is not True:
            raise RuntimeError("Respuesta edge rechazada")
        return value


def _site(conn, site_code: str, origin_host: str) -> dict[str, Any]:
    row = conn.execute(
        text(
            """
            SELECT id,code,lower(regexp_replace(domain,'^www\\.','','i')) AS domain
            FROM public.wi_sites WHERE code=:code AND enabled
            """
        ),
        {"code": site_code},
    ).mappings().one_or_none()
    if not row or str(row["domain"] or "").rstrip("/") != origin_host:
        raise ValueError("SITE_ORIGIN_MISMATCH")
    return dict(row)


def _ingest_item(conn, item: dict[str, Any]) -> tuple[bool, bool]:
    allowed_envelope = {"edge_id", "event_id", "site_code", "event_type", "payload_sanitized", "payload", "origin", "user_agent", "received_at"}
    if set(item) - allowed_envelope:
        raise ValueError("INVALID_ENVELOPE")
    edge_id = int(item.get("edge_id") or 0)
    payload = item.get("payload")
    item_type = str(item.get("event_type") or "").lower()
    site_code = str(item.get("site_code") or "").strip().upper()
    origin = str(item.get("origin") or "")
    if edge_id < 1 or not isinstance(payload, dict) or item_type not in {"session", "event"}:
        raise ValueError("INVALID_ENVELOPE")
    if site_code != str(payload.get("site_code") or "").strip().upper():
        raise ValueError("SITE_CODE_MISMATCH")
    try:
        validation._allowlist(payload, validation.SESSION_KEYS if item_type == "session" else validation.EVENT_KEYS)
        origin_host = validation._origin_host(origin)
        validation._require_page_host(payload, origin_host, "landing_url" if item_type == "session" else "page_url")
    except HTTPException as exc:
        raise ValueError("PAYLOAD_VALIDATION_FAILED") from exc
    site = _site(conn, site_code, origin_host)
    if item_type == "session":
        upsert_session(conn, site_id=int(site["id"]), payload=payload, user_agent=str(item.get("user_agent") or "")[:300])
        return True, False
    session_site = conn.execute(
        text("SELECT site_id FROM public.wi_sessions WHERE session_id=:session_id"),
        {"session_id": payload.get("session_id")},
    ).scalar()
    if not session_site or int(session_site) != int(site["id"]):
        raise ValueError("SESSION_SITE_MISMATCH")
    result = record_event(conn, site_id=int(site["id"]), payload=payload)
    return False, bool(result.get("duplicate"))


def _quarantine(conn, item: dict[str, Any], reason: str) -> None:
    conn.execute(
        text(
            """
            INSERT INTO public.wi_tracking_edge_rejections(edge_id,event_id,site_code,reason_code)
            VALUES(:edge_id,:event_id,:site_code,:reason)
            ON CONFLICT(edge_id) DO UPDATE SET reason_code=excluded.reason_code,last_seen_at=now()
            """
        ),
        {
            "edge_id": int(item.get("edge_id") or 0),
            "event_id": str(item.get("event_id") or "")[:80] or None,
            "site_code": str(item.get("site_code") or "")[:16] or "UNKNOWN",
            "reason": reason[:80],
        },
    )


def _record_run(conn, *, status: str, pulled: int, sessions: int, events: int, duplicates: int, rejected: int, metrics: dict[str, Any], error_code: str | None = None) -> None:
    conn.execute(
        text(
            """
            INSERT INTO public.wi_tracking_edge_pull_runs(status,pulled,sessions_ingested,events_ingested,duplicates,rejected,queue_depth,oldest_unacked_age,last_event_at,error_code)
            VALUES(:status,:pulled,:sessions,:events,:duplicates,:rejected,:depth,:oldest,:last_event,:error)
            """
        ),
        {
            "status": status,
            "pulled": pulled,
            "sessions": sessions,
            "events": events,
            "duplicates": duplicates,
            "rejected": rejected,
            "depth": int(metrics.get("queue_depth") or 0),
            "oldest": int(metrics.get("oldest_unacked_age") or 0),
            "last_event": metrics.get("last_event_at"),
            "error": error_code,
        },
    )


def _sync_queue_alert(conn, metrics: dict[str, Any]) -> None:
    exists = conn.execute(text("SELECT to_regclass('public.wi_actionable_alerts') IS NOT NULL")).scalar()
    if not exists:
        return
    depth = int(metrics.get("queue_depth") or 0)
    oldest = int(metrics.get("oldest_unacked_age") or 0)
    threshold_depth = int(os.getenv("GD_TRACKING_EDGE_ALERT_DEPTH", "5000"))
    threshold_age = int(os.getenv("GD_TRACKING_EDGE_ALERT_AGE_SECONDS", "900"))
    key = "tracking:edge:queue_backlog"
    if depth < threshold_depth and oldest < threshold_age:
        conn.execute(text("UPDATE public.wi_actionable_alerts SET status='RESOLVED',resolved_at=now() WHERE alert_key=:key AND status!='RESOLVED'"), {"key": key})
        return
    reason = "La cola de tracking requiere atención; el CRM sigue operativo."
    evidence = json.dumps({"queue_depth": depth, "oldest_unacked_age": oldest})
    conn.execute(
        text(
            """
            INSERT INTO public.wi_actionable_alerts(alert_key,category,severity,title,reason,action_label,action_target,entity_type,entity_id,evidence,next_review_at)
            VALUES(:key,'INTEGRATIONS','IMPORTANT','Tracking pendiente de entrega',:reason,'VER INTEGRACIÓN','#integrations','tracking_edge','public-relay',CAST(:evidence AS jsonb),now()+interval '15 minutes')
            ON CONFLICT(alert_key) DO UPDATE SET severity=excluded.severity,title=excluded.title,reason=excluded.reason,
              evidence=excluded.evidence,last_seen_at=now(),next_review_at=excluded.next_review_at,
              status=CASE WHEN wi_actionable_alerts.status='RESOLVED' THEN 'OPEN' ELSE wi_actionable_alerts.status END,resolved_at=NULL
            """
        ),
        {"key": key, "reason": reason, "evidence": evidence},
    )


def run_once(*, client: EdgeClient, batch_size: int) -> dict[str, Any]:
    with engine.begin() as conn:
        locked = bool(conn.execute(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": LOCK_KEY}).scalar())
        if not locked:
            return {"status": "LOCKED", "pulled": 0, "acked": 0}
        response = client.request("/internal/v1/pull", {"limit": batch_size})
        items = response.get("items") if isinstance(response.get("items"), list) else []
        if len(items) > batch_size:
            raise RuntimeError("Edge excedió batch solicitado")
        processed: list[int] = []
        sessions = events = duplicates = rejected = 0
        for item in items:
            if not isinstance(item, dict):
                raise RuntimeError("Envelope edge inválido")
            try:
                is_session, duplicate = _ingest_item(conn, item)
                sessions += int(is_session)
                events += int(not is_session and not duplicate)
                duplicates += int(duplicate)
            except (ValueError, TypeError, KeyError):
                _quarantine(conn, item, "PAYLOAD_VALIDATION_FAILED")
                rejected += 1
            processed.append(int(item.get("edge_id") or 0))
        metrics = response.get("metrics") if isinstance(response.get("metrics"), dict) else {}
        _record_run(conn, status="PASS", pulled=len(items), sessions=sessions, events=events, duplicates=duplicates, rejected=rejected, metrics=metrics)
        _sync_queue_alert(conn, metrics)
        lease_id = response.get("lease_id")
    acked = 0
    if processed:
        if not isinstance(lease_id, str) or not lease_id:
            raise RuntimeError("Lease faltante")
        ack = client.request("/internal/v1/ack", {"lease_id": lease_id, "edge_ids": processed})
        acked = int(ack.get("acked") or 0)
        if acked != len(processed):
            raise RuntimeError("ACK parcial; se reintentará idempotentemente")
    return {"status": "PASS", "pulled": len(items), "acked": acked, "sessions": sessions, "events": events, "duplicates": duplicates, "rejected": rejected}


def main() -> int:
    parser = argparse.ArgumentParser(description="Pull firmado del borde first-party hacia PostgreSQL")
    parser.add_argument("--batch-size", type=int, default=int(os.getenv("EDGE_PULL_BATCH_SIZE", "500")))
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 500:
        raise SystemExit("batch-size fuera de rango")
    url = os.getenv("GD_TRACKING_EDGE_URL", "https://collect.greendiamond.cl")
    try:
        result = run_once(client=EdgeClient(url, _secret()), batch_size=args.batch_size)
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "error": type(exc).__name__}, separators=(",", ":")), file=sys.stderr)
        return 1
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
