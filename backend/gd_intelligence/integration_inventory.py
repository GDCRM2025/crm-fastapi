from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import requests

from backend.gd_intelligence.site_health import _get, _small_body


PROVIDERS = (
    "GTM",
    "GA4",
    "SEARCH_CONSOLE",
    "CLARITY",
    "PAGESPEED",
    "CRUX",
    "TRACKING",
    "META",
)
GTM_RE = re.compile(r"GTM-[A-Z0-9]+", re.I)
GA4_RE = re.compile(r"(?<![A-Z0-9])G-[A-Z0-9]{10}(?![A-Z0-9])", re.I)
META_PIXEL_RE = re.compile(r"fbq\s*\(\s*['\"]init['\"]\s*,\s*['\"]([0-9]{8,20})", re.I)
CLARITY_RE = re.compile(r"clarity\.ms/(?:tag/)?([a-z0-9]+)", re.I)


def _ids(pattern: re.Pattern[str], content: str) -> list[str]:
    values: set[str] = set()
    for match in pattern.finditer(content or ""):
        value = match.group(1) if match.lastindex else match.group(0)
        values.add(str(value).upper())
    return sorted(values)


def scan_public_integrations(domain: str) -> list[dict[str, Any]]:
    host = str(domain or "").strip().lower().rstrip(".")
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; GreenDiamond-IntegrationInventory/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        }
    )
    verified_at = datetime.now(timezone.utc)
    page = ""
    error_safe: str | None = None
    try:
        response = _get(session, f"https://{host}/", timeout=12)
        page = _small_body(response)
    except (requests.RequestException, OSError, ValueError) as exc:
        error_safe = f"{type(exc).__name__}: {str(exc)[:200]}"

    gtm_ids = _ids(GTM_RE, page)
    container = ""
    if gtm_ids:
        try:
            response = _get(
                session,
                f"https://www.googletagmanager.com/gtm.js?id={gtm_ids[0]}",
                timeout=12,
            )
            container = _small_body(response)
        except (requests.RequestException, OSError, ValueError):
            container = ""
    combined = f"{page}\n{container}"
    ga4_container = _ids(GA4_RE, container)
    ga4_page = _ids(GA4_RE, page)
    clarity_ids = _ids(CLARITY_RE, combined)
    meta_ids = _ids(META_PIXEL_RE, combined)
    tracker_seen = bool(re.search(r"gd[-_]tracker|first[ -]?party", container, re.I))
    gsc_seen = bool(re.search(r"google-site-verification", page, re.I))
    duplicate_ga4 = bool(gtm_ids and ga4_page and set(ga4_page) - set(ga4_container))

    def item(provider: str, status: str, external_id: str | None, message: str | None = None):
        return {
            "provider": provider,
            "status": status,
            "enabled": status in {"DETECTED", "CONNECTED", "READY_FOR_CREDENTIAL", "REQUIRES_ATTENTION"},
            "external_id": external_id,
            "last_verified_at": verified_at,
            "last_error_safe": message,
        }

    if error_safe:
        session.close()
        return [item(provider, "ERROR", None, error_safe) for provider in PROVIDERS]

    ga4_ids = ga4_container or ga4_page
    ga4_message = "Posible GA4 duplicado fuera de GTM; revisar antes de publicar." if duplicate_ga4 else None
    results = [
        item("GTM", "DETECTED" if gtm_ids else "NOT_CONFIGURED", gtm_ids[0] if gtm_ids else None),
        item("GA4", "REQUIRES_ATTENTION" if duplicate_ga4 else "DETECTED" if ga4_ids else "READY_FOR_CREDENTIAL", ga4_ids[0] if ga4_ids else None, ga4_message),
        item("SEARCH_CONSOLE", "DETECTED" if gsc_seen else "READY_FOR_CREDENTIAL", f"sc-domain:{host}" if gsc_seen else None, "Verificación pública detectada; acceso API aún no validado." if gsc_seen else None),
        item("CLARITY", "DETECTED" if clarity_ids else "NOT_CONFIGURED", clarity_ids[0].lower() if clarity_ids else None),
        item("PAGESPEED", "READY_FOR_CREDENTIAL", None, "Implementación lista; API key aún no configurada."),
        item("CRUX", "READY_FOR_CREDENTIAL", None, "Implementación lista; credencial y disponibilidad de datos aún no validadas."),
        item("TRACKING", "DETECTED" if tracker_seen else "NOT_CONFIGURED", "gd-tracker.js" if tracker_seen else None),
        item("META", "DETECTED" if meta_ids else "READY_FOR_CREDENTIAL", meta_ids[0] if meta_ids else None),
    ]
    session.close()
    return results
