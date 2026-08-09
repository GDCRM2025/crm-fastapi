from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


PROVIDER_GUIDANCE = {
    "GTM": ("Contenedor público detectado", "Falta detectar un contenedor GTM publicado", "VERIFICAR"),
    "GA4": ("Tag GA4 público detectado", "Falta seleccionar la propiedad GA4 numérica y verificar el tag", "CONFIGURAR"),
    "SEARCH_CONSOLE": ("Propiedad Search Console registrada", "Falta seleccionar una propiedad con acceso backend", "CONFIGURAR"),
    "CLARITY": ("Project ID público registrado", "Falta crear/registrar el Project ID de Clarity", "CONFIGURAR"),
    "PAGESPEED": ("API backend disponible", "Falta configurar PAGESPEED_API_KEY en el secret store", "VER INSTRUCCIONES"),
    "CRUX": ("CrUX backend disponible", "Falta configurar PAGESPEED_API_KEY y validar datos del origen", "VERIFICAR"),
    "TRACKING": ("gd-tracker.js detectado", "Falta instalar gd-tracker.js mediante GTM", "VER INSTRUCCIONES"),
    "META": ("Meta Pixel público detectado", "Falta registrar o verificar el Pixel ID", "CONFIGURAR"),
}


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
        missing = "Nada pendiente; puedes volver a verificar la conexión"
        action = "VERIFICAR"
    elif status == "ERROR":
        missing = str(item.get("last_error_safe") or "La última verificación falló")
        action = "RECONECTAR"
    elif status == "DISABLED":
        missing = "Integración deshabilitada para este sitio"
        action = "CONFIGURAR"
    else:
        action = fallback_action
    if provider in {"GA4", "SEARCH_CONSOLE"} and not (
        capabilities["google_service_credentials"] or capabilities["google_oauth_configured"]
    ):
        missing = "Falta habilitar credenciales Google sólo en el backend"
        action = "VER INSTRUCCIONES"
        if status == "CONNECTED":
            status = "WARNING"
    if provider in {"PAGESPEED", "CRUX"} and not capabilities["pagespeed_api"]:
        missing = "Falta PAGESPEED_API_KEY en el secret store del backend"
        action = "VER INSTRUCCIONES"
    return {**item, "status": status, "detected": detected, "missing": missing, "action": action}


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
