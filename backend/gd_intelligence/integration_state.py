from __future__ import annotations

from typing import Any


PUBLIC_PROVIDERS = {"GTM", "GA4", "SEARCH_CONSOLE", "CLARITY", "TRACKING", "META"}
API_PROVIDERS = {"GA4", "SEARCH_CONSOLE", "PAGESPEED", "CRUX", "META"}
CANONICAL_STATES = {
    "DETECTED", "CONNECTED", "READY_FOR_CREDENTIAL", "NO_DATA",
    "REQUIRES_ATTENTION", "ERROR", "DISABLED", "NOT_CONFIGURED",
}


def canonical_integration_state(
    item: dict[str, Any],
    capabilities: dict[str, bool] | None = None,
    *,
    tracking_events: int = 0,
) -> dict[str, Any]:
    capabilities = capabilities or {}
    provider = str(item.get("provider") or "").upper()
    stored = str(item.get("status") or "NOT_CONFIGURED").upper()
    public_detected = bool(item.get("external_id")) and provider in PUBLIC_PROVIDERS
    credential_required = provider in API_PROVIDERS
    credential_available = bool(item.get("credential_configured"))
    if provider in {"GA4", "SEARCH_CONSOLE"}:
        credential_available = credential_available or bool(
            capabilities.get("google_service_credentials") or capabilities.get("google_oauth_configured")
        )
    elif provider in {"PAGESPEED", "CRUX"}:
        credential_available = credential_available or bool(capabilities.get("pagespeed_api"))
    elif provider == "META":
        credential_available = credential_available or bool(capabilities.get("meta_oauth_configured"))

    api_verified = bool(credential_available and stored == "CONNECTED")
    data_available = bool(provider == "TRACKING" and tracking_events > 0)
    if item.get("data_available") is not None:
        data_available = bool(item.get("data_available"))

    if stored == "DISABLED":
        status = "DISABLED"
    elif stored in {"ERROR", "REQUIRES_ATTENTION", "WARNING"} and item.get("last_failure_at"):
        status = "REQUIRES_ATTENTION" if item.get("last_success_at") or public_detected else "ERROR"
    elif api_verified:
        status = "CONNECTED" if data_available or provider not in {"GA4", "SEARCH_CONSOLE", "CRUX", "META"} else "NO_DATA"
    elif provider == "TRACKING" and public_detected:
        status = "CONNECTED" if data_available else "NO_DATA"
    elif public_detected:
        status = "DETECTED"
    elif credential_required and not credential_available:
        status = "READY_FOR_CREDENTIAL"
    elif stored == "CONNECTED":
        status = "CONNECTED"
    else:
        status = "NOT_CONFIGURED"

    installation_status = "DETECTED" if public_detected else "NOT_CONFIGURED"
    credential_status = "NOT_REQUIRED" if not credential_required else "AVAILABLE" if credential_available else "MISSING"
    api_status = "NOT_REQUIRED" if provider not in API_PROVIDERS else "CONNECTED" if api_verified else "READY_FOR_CREDENTIAL"
    data_status = "AVAILABLE" if data_available else "NO_DATA"
    health_status = "REQUIRES_ATTENTION" if status == "REQUIRES_ATTENTION" else "ERROR" if status == "ERROR" else "OPERATIONAL" if status in {"DETECTED", "CONNECTED", "NO_DATA"} else "UNKNOWN"
    return {
        **item,
        "status": status,
        "public_detected": public_detected,
        "credential_required": credential_required,
        "credential_available": credential_available,
        "api_verified": api_verified,
        "data_available": data_available,
        "installation_status": installation_status,
        "credential_status": credential_status,
        "api_status": api_status,
        "data_status": data_status,
        "health_status": health_status,
    }


def site_capability_state(
    site: dict[str, Any],
    integrations: list[dict[str, Any]],
    health: dict[str, Any] | None,
    *,
    google_ads_connected: bool = False,
    meta_ads_connected: bool = False,
) -> dict[str, Any]:
    by_provider = {str(row.get("provider")): row for row in integrations if row.get("site_id") == site.get("id")}
    ga4 = by_provider.get("GA4", {})
    search = by_provider.get("SEARCH_CONSOLE", {})
    tracker = by_provider.get("TRACKING", {})
    health_operational = bool(health and health.get("http_status") == 200 and health.get("https_ok"))
    findings = int(health.get("missing_alt_count") or 0) if health else 0
    if health and not health.get("schema_org_ok", True):
        findings += 1

    analytics = {
        "status": "CONNECTED" if ga4.get("api_verified") else "DETECTED" if ga4.get("public_detected") else "READY_FOR_CREDENTIAL",
        "detail": "Tag detectado · API conectada" if ga4.get("api_verified") else "Tag detectado · API pendiente" if ga4.get("public_detected") else "Falta autorizar Google",
    }
    seo = {
        "status": "CONNECTED" if health_operational and search.get("api_verified") else "REQUIRES_ATTENTION" if health_operational else "ERROR" if health else "NOT_CONFIGURED",
        "detail": "SEO técnico operativo · Search Console conectada" if search.get("api_verified") else "SEO técnico operativo · Search Console pendiente" if health_operational else "Sin medición técnica",
    }
    ads_connected = google_ads_connected or meta_ads_connected
    ads = {"status": "CONNECTED" if ads_connected else "READY_FOR_CREDENTIAL", "detail": "API publicitaria conectada" if ads_connected else "Falta autorizar Google Ads / Meta Ads"}
    tracking = {"status": tracker.get("status", "NOT_CONFIGURED"), "detail": "Operativo · datos reales" if tracker.get("data_available") else "Instalado · todavía sin eventos" if tracker.get("public_detected") else "No detectado"}
    health_state = {"status": "REQUIRES_ATTENTION" if health_operational and findings else "CONNECTED" if health_operational else "ERROR" if health else "NOT_CONFIGURED", "detail": f"Operativo · {findings} mejoras" if health_operational and findings else "Operativo" if health_operational else "Sin medición"}
    available = sum((bool(ga4.get("public_detected")), health_operational, bool(tracker.get("public_detected")), ads_connected, bool(search.get("api_verified"))))
    return {"site_id": site.get("id"), "site_code": site.get("code"), "analytics": analytics, "seo": seo, "ads": ads, "tracking": tracking, "health": health_state, "available_capabilities": available, "total_capabilities": 5}


def overview_summary(items: list[dict[str, Any]], sites: list[dict[str, Any]], site_states: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = [str(row.get("status")) for row in items]
    total_available = sum(int(row.get("available_capabilities") or 0) for row in site_states)
    total_capabilities = sum(int(row.get("total_capabilities") or 0) for row in site_states)
    return {
        "active_sites": sum(bool(site.get("enabled")) for site in sites),
        "installations_detected": sum(bool(row.get("public_detected")) for row in items),
        "apis_connected": sum(bool(row.get("api_verified")) for row in items),
        "ready_for_credential": sum(
            bool(row.get("credential_required")) and not bool(row.get("credential_available"))
            for row in items
        ),
        "requires_attention": statuses.count("REQUIRES_ATTENTION") + sum(row.get("health", {}).get("status") == "REQUIRES_ATTENTION" for row in site_states),
        "not_configured": statuses.count("NOT_CONFIGURED"),
        "errors": statuses.count("ERROR"),
        "configuration_progress": round(100 * total_available / total_capabilities) if total_capabilities else None,
    }
