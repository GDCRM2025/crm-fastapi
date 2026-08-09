from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


PROVIDER_GUIDANCE = {
    "GTM": ("Contenedor publicado", "No detectamos un contenedor de Tag Manager.", "VERIFICAR"),
    "GA4": ("Etiqueta de Analytics detectada", "Conecta Google y selecciona la propiedad de este sitio.", "CONECTAR GOOGLE"),
    "SEARCH_CONSOLE": ("Propiedad registrada", "Conecta Google y selecciona la propiedad de búsqueda.", "CONECTAR GOOGLE"),
    "CLARITY": ("Project ID registrado", "Registra el proyecto de Microsoft Clarity.", "CONFIGURAR"),
    "PAGESPEED": ("Servicio de rendimiento", "Configura la clave de PageSpeed para comenzar las mediciones.", "CONFIGURAR"),
    "CRUX": ("Experiencia real de usuarios", "Configura PageSpeed para consultar disponibilidad de datos CrUX.", "CONFIGURAR"),
    "TRACKING": ("GD Tracker detectado", "Instala GD Tracker mediante Tag Manager.", "VER INSTRUCCIONES"),
    "META": ("Meta Pixel detectado", "Conecta Meta y selecciona los activos autorizados.", "CONECTAR META"),
}

PROVIDER_NAMES = {"GTM":"Google Tag Manager","GA4":"Google Analytics 4","SEARCH_CONSOLE":"Google Search Console","CLARITY":"Microsoft Clarity","PAGESPEED":"Google PageSpeed","CRUX":"Chrome UX Report","TRACKING":"GD Tracker","META":"Meta"}
PROVIDER_GROUPS = {"GTM":"Google","GA4":"Google","SEARCH_CONSOLE":"Google","PAGESPEED":"Google","CRUX":"Google","META":"Meta","CLARITY":"Microsoft","TRACKING":"Green Diamond"}
STATUS_LABELS = {"CONNECTED":"Conectado","NOT_CONFIGURED":"Falta configurar","WARNING":"Requiere atención","ERROR":"Error de conexión","DISABLED":"Deshabilitado"}


def backend_capabilities() -> dict[str, bool]:
    credential_path = str(os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE") or "").strip()
    return {
        "google_service_credentials": bool(credential_path and Path(credential_path).is_file()),
        "google_oauth_configured": bool(os.getenv("GOOGLE_OAUTH_CLIENT_ID") and os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")),
        "pagespeed_api": bool(os.getenv("PAGESPEED_API_KEY")),
    }


def actionable_integration(item: dict[str, Any], capabilities: dict[str, bool] | None = None) -> dict[str, Any]:
    capabilities = capabilities or backend_capabilities()
    provider = str(item.get("provider") or "").upper()
    status = str(item.get("status") or "NOT_CONFIGURED").upper()
    detected, missing, fallback_action = PROVIDER_GUIDANCE.get(provider, ("Configuración detectada", "Falta configurar", "CONFIGURAR"))
    if item.get("external_id"):
        detected = f"{detected}: {item['external_id']}"
    if provider == "GA4" and item.get("property_id"):
        detected += f" · Property {item['property_id']}"
    if status == "CONNECTED":
        missing = "La integración está operativa."
        action = "VERIFICAR CONEXIÓN"
    elif status == "ERROR":
        missing = str(item.get("last_error_safe") or "La última verificación falló")
        action = "RECONECTAR"
    elif status == "DISABLED":
        missing = "Integración deshabilitada para este sitio"
        action = "CONFIGURAR"
    else:
        action = fallback_action
    if provider in {"GA4", "SEARCH_CONSOLE"} and not item.get("credential_configured") and status != "CONNECTED":
        action = "CONECTAR GOOGLE"
    if provider in {"PAGESPEED", "CRUX"}:
        if item.get("credential_configured") or capabilities["pagespeed_api"]:
            detected = "Credencial configurada de forma segura"
            missing = "La conexión está lista para verificarse."
            action = "VERIFICAR CONEXIÓN"
        else:
            action = "CONFIGURAR"
    if item.get("credential_configured"):
        detected += f" · Credencial ••••••••{item.get('credential_suffix') or ''}"
    return {**item, "status": status, "status_label": STATUS_LABELS.get(status,status.title()),
            "provider_name": PROVIDER_NAMES.get(provider,provider.title()),"provider_group": PROVIDER_GROUPS.get(provider,"Otros"),
            "detected": detected, "missing": missing, "action": action,
            "can_write_secret": provider in {"PAGESPEED","CRUX"}}


def list_google_properties() -> dict[str, list[dict[str, str]]]:
    """List public property identifiers through a backend-only service credential."""
    credential_path = str(os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE") or "").strip()
    if not credential_path or not Path(credential_path).is_file():
        raise RuntimeError("GOOGLE_CREDENTIALS_NOT_CONFIGURED")
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account

    credentials = service_account.Credentials.from_service_account_file(
        credential_path,
        scopes=(
            "https://www.googleapis.com/auth/analytics.readonly",
            "https://www.googleapis.com/auth/webmasters.readonly",
        ),
    )
    session = AuthorizedSession(credentials)
    ga4: list[dict[str, str]] = []
    account_response = session.get("https://analyticsadmin.googleapis.com/v1beta/accountSummaries", timeout=20)
    account_response.raise_for_status()
    for account in account_response.json().get("accountSummaries", []):
        for prop in account.get("propertySummaries", []):
            resource = str(prop.get("property") or "")
            ga4.append({"id": resource.removeprefix("properties/"), "name": str(prop.get("displayName") or resource)})
    sc_response = session.get("https://www.googleapis.com/webmasters/v3/sites", timeout=20)
    sc_response.raise_for_status()
    search_console = [
        {"id": str(site.get("siteUrl") or ""), "name": str(site.get("siteUrl") or "")}
        for site in sc_response.json().get("siteEntry", []) if site.get("siteUrl")
    ]
    return {"ga4": ga4, "search_console": search_console}
