from __future__ import annotations

import html
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.core.database import get_db


router = APIRouter(
    prefix="/meta/whatsapp/embedded-signup",
    tags=["WhatsApp Embedded Signup"],
)

log = logging.getLogger("crm")
ROOT = Path(__file__).resolve().parents[2]
AUDIT_DIR = ROOT / "data" / "whatsapp_onboarding"

BRANDS: dict[str, tuple[str, str]] = {
    "CAMALEON": ("CAMALEÓN", "Andrés Landerer"),
    "DEL_SABOR": ("DEL SABOR", "Walter Canales"),
    "GOURMET": ("GOURMET", "Constanza Franco"),
    "EXPRESS": ("EXPRESS", "Daniel Toledo"),
}


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or default).strip()


def _graph_version() -> str:
    value = _env("META_GRAPH_API_VERSION", "v23.0").strip("/")
    return value or "v23.0"


def _graph_url(path: str) -> str:
    path = str(path or "").lstrip("/")
    return f"https://graph.facebook.com/{_graph_version()}/{path}"


def _http_json(
    method: str,
    url: str,
    *,
    token: str | None = None,
    form: dict[str, Any] | None = None,
    timeout: int = 35,
) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    data: bytes | None = None
    if form is not None:
        data = urllib.parse.urlencode(
            {key: value for key, value in form.items() if value is not None}
        ).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method=method.upper(),
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
            return json.loads(raw or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            payload = json.loads(raw or "{}")
        except Exception:
            payload = {"error": {"message": raw or str(exc)}}
        message = (
            payload.get("error", {}).get("message")
            if isinstance(payload.get("error"), dict)
            else None
        ) or raw or str(exc)
        raise RuntimeError(f"Meta Graph API {exc.code}: {message}") from exc
    except Exception as exc:
        raise RuntimeError(f"No se pudo consultar Meta Graph API: {exc}") from exc


def _exchange_code(code: str, redirect_uri: str) -> dict[str, Any]:
    app_id = _env("META_APP_ID")
    app_secret = _env("META_APP_SECRET")
    if not app_id:
        raise RuntimeError("Falta META_APP_ID en .env")
    if not app_secret:
        raise RuntimeError("Falta META_APP_SECRET en .env")

    return _http_json(
        "GET",
        _graph_url(
            "oauth/access_token?"
            + urllib.parse.urlencode(
                {
                    "client_id": app_id,
                    "client_secret": app_secret,
                    "redirect_uri": redirect_uri,
                    "code": code,
                }
            )
        ),
    )


def _paged_get(path: str, token: str) -> list[dict[str, Any]]:
    url = _graph_url(path)
    rows: list[dict[str, Any]] = []
    for _ in range(10):
        payload = _http_json("GET", url, token=token)
        data = payload.get("data")
        if isinstance(data, list):
            rows.extend(item for item in data if isinstance(item, dict))
        next_url = (
            payload.get("paging", {}).get("next")
            if isinstance(payload.get("paging"), dict)
            else None
        )
        if not next_url:
            break
        url = str(next_url)
    return rows


def _discover_assets(token: str) -> dict[str, Any]:
    businesses = _paged_get("me/businesses?fields=id,name&limit=100", token)
    discovered_wabas: list[dict[str, Any]] = []
    discovered_phones: list[dict[str, Any]] = []

    for business in businesses:
        business_id = str(business.get("id") or "").strip()
        if not business_id:
            continue

        waba_rows: list[dict[str, Any]] = []
        for edge in (
            "owned_whatsapp_business_accounts",
            "client_whatsapp_business_accounts",
        ):
            try:
                waba_rows.extend(
                    _paged_get(
                        f"{business_id}/{edge}?fields=id,name,currency,timezone_id&limit=100",
                        token,
                    )
                )
            except Exception as exc:
                log.info(
                    "[WA EMBEDDED] edge %s no disponible para business %s: %s",
                    edge,
                    business_id,
                    exc,
                )

        seen_waba: set[str] = set()
        for waba in waba_rows:
            waba_id = str(waba.get("id") or "").strip()
            if not waba_id or waba_id in seen_waba:
                continue
            seen_waba.add(waba_id)

            enriched_waba = {
                **waba,
                "business_id": business_id,
                "business_name": business.get("name"),
            }
            discovered_wabas.append(enriched_waba)

            try:
                phones = _paged_get(
                    f"{waba_id}/phone_numbers?fields=id,display_phone_number,verified_name,quality_rating,code_verification_status,platform_type&limit=100",
                    token,
                )
            except Exception as exc:
                log.warning(
                    "[WA EMBEDDED] no se pudieron leer teléfonos WABA %s: %s",
                    waba_id,
                    exc,
                )
                phones = []

            for phone in phones:
                discovered_phones.append(
                    {
                        **phone,
                        "waba_id": waba_id,
                        "waba_name": waba.get("name"),
                        "business_id": business_id,
                        "business_name": business.get("name"),
                    }
                )

    return {
        "businesses": businesses,
        "wabas": discovered_wabas,
        "phone_numbers": discovered_phones,
    }


def _subscribe_waba(waba_id: str, token: str) -> tuple[bool, str | None]:
    try:
        payload = _http_json(
            "POST",
            _graph_url(f"{waba_id}/subscribed_apps"),
            token=token,
            form={},
        )
        return bool(payload.get("success", True)), None
    except Exception as exc:
        return False, str(exc)


def _table_exists(db: Session, table: str) -> bool:
    return bool(
        db.execute(
            text("SELECT to_regclass(:name)"),
            {"name": f"public.{table}"},
        ).scalar()
    )


def _bind_single_phone(
    db: Session,
    *,
    brand_code: str,
    phone: dict[str, Any],
) -> dict[str, Any]:
    if not _table_exists(db, "whatsapp_channels"):
        return {"bound": False, "reason": "whatsapp_channels no existe"}

    brand_code = str(brand_code or "CAMALEON").strip().upper()
    if brand_code not in BRANDS:
        brand_code = "CAMALEON"
    brand_name, executive_name = BRANDS[brand_code]

    phone_number_id = str(phone.get("id") or "").strip()
    waba_id = str(phone.get("waba_id") or "").strip()
    display_phone_number = str(phone.get("display_phone_number") or "").strip()
    if not phone_number_id or not waba_id:
        return {"bound": False, "reason": "faltan phone_number_id o waba_id"}

    db.execute(
        text(
            """
            INSERT INTO public.whatsapp_channels (
                brand_code,
                brand_name,
                executive_name,
                phone_number_id,
                waba_id,
                display_phone_number,
                enabled,
                updated_at
            )
            VALUES (
                :brand_code,
                :brand_name,
                :executive_name,
                :phone_number_id,
                :waba_id,
                :display_phone_number,
                TRUE,
                now()
            )
            ON CONFLICT (brand_code) DO UPDATE SET
                brand_name = EXCLUDED.brand_name,
                executive_name = EXCLUDED.executive_name,
                phone_number_id = EXCLUDED.phone_number_id,
                waba_id = EXCLUDED.waba_id,
                display_phone_number = EXCLUDED.display_phone_number,
                enabled = TRUE,
                updated_at = now()
            """
        ),
        {
            "brand_code": brand_code,
            "brand_name": brand_name,
            "executive_name": executive_name,
            "phone_number_id": phone_number_id,
            "waba_id": waba_id,
            "display_phone_number": display_phone_number,
        },
    )
    db.commit()
    return {
        "bound": True,
        "brand_code": brand_code,
        "brand_name": brand_name,
        "executive_name": executive_name,
        "phone_number_id": phone_number_id,
        "waba_id": waba_id,
        "display_phone_number": display_phone_number,
    }


def _audit(payload: dict[str, Any]) -> None:
    try:
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = AUDIT_DIR / f"embedded_signup_{stamp}.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    except Exception:
        log.exception("[WA EMBEDDED] no se pudo guardar auditoría")


def _page(
    *,
    title: str,
    message: str,
    ok: bool,
    details: dict[str, Any] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    color = "#166534" if ok else "#991b1b"
    background = "#dcfce7" if ok else "#fee2e2"
    icon = "✓" if ok else "!"
    safe_title = html.escape(title)
    safe_message = html.escape(message)
    details_html = ""
    if details:
        safe_details = html.escape(
            json.dumps(details, ensure_ascii=False, indent=2, default=str)
        )
        details_html = f"<details><summary>Detalles técnicos</summary><pre>{safe_details}</pre></details>"

    return HTMLResponse(
        status_code=status_code,
        content=f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{safe_title}</title>
<style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#eef3f8;color:#0f172a;font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif;padding:24px;box-sizing:border-box}}
.card{{width:min(720px,100%);background:#fff;border:1px solid #d8e1eb;border-radius:18px;padding:26px;box-shadow:0 24px 70px rgba(15,23,42,.16)}}
.badge{{width:54px;height:54px;border-radius:50%;display:grid;place-items:center;background:{background};color:{color};font-size:30px;font-weight:950}}
h1{{font-size:23px;margin:16px 0 8px}}p{{line-height:1.5;color:#475569}}.next{{margin-top:18px;padding:12px 14px;border-radius:12px;background:{background};color:{color};font-weight:900}}
details{{margin-top:18px}}summary{{cursor:pointer;font-weight:850}}pre{{white-space:pre-wrap;word-break:break-word;background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;padding:12px;font-size:12px}}
</style>
</head>
<body>
<section class="card">
<div class="badge">{icon}</div>
<h1>{safe_title}</h1>
<p>{safe_message}</p>
<div class="next">Puedes cerrar esta ventana y volver a Greenie.</div>
{details_html}
</section>
</body>
</html>""",
    )


@router.get("/callback", include_in_schema=False)
def embedded_signup_callback(
    request: Request,
    code: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_reason: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    ping: int = Query(default=0),
    brand: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    redirect_uri = _env(
        "META_EMBEDDED_SIGNUP_REDIRECT_URI",
        "https://greendiamond.cl/crm/meta/whatsapp/embedded-signup/callback",
    )

    if ping == 1:
        return _page(
            title="Callback de WhatsApp listo",
            message="Greenie ya puede recibir el retorno de Embedded Signup.",
            ok=True,
            details={
                "redirect_uri": redirect_uri,
                "graph_version": _graph_version(),
            },
        )

    if error or error_reason or error_description:
        details = {
            "error": error,
            "error_reason": error_reason,
            "error_description": error_description,
        }
        _audit({"ok": False, "stage": "meta_redirect", **details})
        return _page(
            title="Registro cancelado o rechazado",
            message=error_description or error_reason or error or "Meta no completó el registro.",
            ok=False,
            details=details,
            status_code=400,
        )

    if not code:
        return _page(
            title="Falta el código de autorización",
            message="La ruta funciona, pero Meta no envió el parámetro code.",
            ok=False,
            details={"query_keys": sorted(request.query_params.keys())},
            status_code=400,
        )

    try:
        token_payload = _exchange_code(code, redirect_uri)
        access_token = str(token_payload.get("access_token") or "").strip()
        if not access_token:
            raise RuntimeError("Meta no devolvió access_token")

        assets = _discover_assets(access_token)
        subscriptions: list[dict[str, Any]] = []
        for waba in assets.get("wabas") or []:
            waba_id = str(waba.get("id") or "").strip()
            if not waba_id:
                continue
            subscribed, subscription_error = _subscribe_waba(waba_id, access_token)
            subscriptions.append(
                {
                    "waba_id": waba_id,
                    "subscribed": subscribed,
                    "error": subscription_error,
                }
            )

        phone_numbers = assets.get("phone_numbers") or []
        default_brand = (
            brand
            or _env("GREENIE_ONBOARDING_DEFAULT_BRAND", "CAMALEON")
            or "CAMALEON"
        ).strip().upper()

        binding: dict[str, Any]
        if len(phone_numbers) == 1:
            binding = _bind_single_phone(
                db,
                brand_code=default_brand,
                phone=phone_numbers[0],
            )
        elif len(phone_numbers) == 0:
            binding = {
                "bound": False,
                "reason": "No se encontró ningún número accesible con este código",
            }
        else:
            binding = {
                "bound": False,
                "reason": "Se encontraron varios números; no se asignó ninguno automáticamente",
                "phone_number_ids": [row.get("id") for row in phone_numbers],
            }

        audit_payload = {
            "ok": True,
            "received_at": datetime.now(timezone.utc).isoformat(),
            "brand_requested": default_brand,
            "token_type": token_payload.get("token_type"),
            "expires_in": token_payload.get("expires_in"),
            "assets": assets,
            "subscriptions": subscriptions,
            "binding": binding,
            "query_keys": sorted(request.query_params.keys()),
        }
        _audit(audit_payload)

        if binding.get("bound"):
            return _page(
                title="WhatsApp conectado a Greenie",
                message=(
                    f"El número {binding.get('display_phone_number') or binding.get('phone_number_id')} "
                    f"quedó asociado a {binding.get('brand_name')}."
                ),
                ok=True,
                details={
                    "brand": binding.get("brand_name"),
                    "executive": binding.get("executive_name"),
                    "waba_id": binding.get("waba_id"),
                    "phone_number_id": binding.get("phone_number_id"),
                    "subscriptions": subscriptions,
                },
            )

        return _page(
            title="Autorización recibida",
            message=(
                "Meta autorizó la integración, pero Greenie no pudo asignar automáticamente "
                "un único número. No se modificó ningún canal."
            ),
            ok=True,
            details={
                "binding": binding,
                "wabas": assets.get("wabas") or [],
                "phone_numbers": phone_numbers,
                "subscriptions": subscriptions,
            },
        )
    except Exception as exc:
        db.rollback()
        log.exception("[WA EMBEDDED] error procesando callback")
        _audit(
            {
                "ok": False,
                "stage": "callback_processing",
                "error": str(exc),
                "query_keys": sorted(request.query_params.keys()),
            }
        )
        return _page(
            title="No se pudo completar la conexión",
            message=str(exc),
            ok=False,
            status_code=502,
        )
