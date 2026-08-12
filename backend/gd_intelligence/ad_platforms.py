from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

import requests
from google.auth.transport.requests import AuthorizedSession

from backend.gd_intelligence.paid_media import parse_google_ads_row, parse_meta_ads_row
from backend.gd_intelligence.paid_media_intelligence import click_id_hash


GOOGLE_ADS_SCOPE = "https://www.googleapis.com/auth/adwords"
GOOGLE_ADS_API_VERSION = os.getenv("GOOGLE_ADS_API_VERSION", "v25")
META_GRAPH_API_VERSION = os.getenv("META_GRAPH_API_VERSION", "v25.0")


class PlatformConfigurationError(RuntimeError):
    pass


class PlatformRequestError(RuntimeError):
    pass


def _clean_id(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _safe_error(response: requests.Response) -> str:
    try:
        payload = response.json()
        error = payload.get("error") or {}
        code = error.get("status") or error.get("code") or response.status_code
        return f"PROVIDER_{str(code)[:40]}"
    except Exception:
        return f"PROVIDER_HTTP_{response.status_code}"


def google_ads_capabilities() -> dict[str, bool]:
    service_file = str(os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE") or "").strip()
    return {
        "developer_token": bool(os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN")),
        "service_account": bool(service_file and Path(service_file).is_file()),
        "oauth_refresh": bool(
            os.getenv("GOOGLE_OAUTH_CLIENT_ID")
            and os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
            and os.getenv("GOOGLE_ADS_REFRESH_TOKEN")
        ),
    }


def meta_ads_capabilities() -> dict[str, bool]:
    return {"system_user_or_oauth_token": bool(os.getenv("META_ACCESS_TOKEN"))}


class GoogleAdsReadOnly:
    """Google Ads REST adapter. It deliberately exposes no mutate operation."""

    def __init__(self):
        developer_token = str(os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN") or "").strip()
        if not developer_token:
            raise PlatformConfigurationError("GOOGLE_ADS_DEVELOPER_TOKEN_MISSING")
        self.developer_token = developer_token
        self.login_customer_id = _clean_id(os.getenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID"))
        self.session = AuthorizedSession(self._credentials())
        self.base = f"https://googleads.googleapis.com/{GOOGLE_ADS_API_VERSION}"

    @staticmethod
    def _credentials():
        service_file = str(os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE") or "").strip()
        if service_file and Path(service_file).is_file():
            from google.oauth2 import service_account

            return service_account.Credentials.from_service_account_file(service_file, scopes=(GOOGLE_ADS_SCOPE,))
        refresh_token = str(os.getenv("GOOGLE_ADS_REFRESH_TOKEN") or "").strip()
        client_id = str(os.getenv("GOOGLE_OAUTH_CLIENT_ID") or "").strip()
        client_secret = str(os.getenv("GOOGLE_OAUTH_CLIENT_SECRET") or "").strip()
        if refresh_token and client_id and client_secret:
            from google.oauth2.credentials import Credentials

            return Credentials(
                token=None,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=client_id,
                client_secret=client_secret,
                scopes=(GOOGLE_ADS_SCOPE,),
            )
        raise PlatformConfigurationError("GOOGLE_ADS_AUTH_MISSING")

    def _headers(self, *, manager: bool = True) -> dict[str, str]:
        headers = {"developer-token": self.developer_token, "Content-Type": "application/json"}
        if manager and self.login_customer_id:
            headers["login-customer-id"] = self.login_customer_id
        return headers

    def _get(self, path: str, *, manager: bool = True) -> dict[str, Any]:
        response = self.session.get(f"{self.base}/{path.lstrip('/')}", headers=self._headers(manager=manager), timeout=30)
        if not response.ok:
            raise PlatformRequestError(_safe_error(response))
        return response.json()

    def _search(self, customer_id: str, query: str) -> list[dict[str, Any]]:
        customer_id = _clean_id(customer_id)
        if not customer_id:
            raise ValueError("CUSTOMER_ID_INVALID")
        response = self.session.post(
            f"{self.base}/customers/{customer_id}/googleAds:searchStream",
            headers=self._headers(), json={"query": query}, timeout=90,
        )
        if not response.ok:
            raise PlatformRequestError(_safe_error(response))
        chunks = response.json() or []
        return [row for chunk in chunks for row in (chunk.get("results") or [])]

    def list_accounts(self) -> list[dict[str, Any]]:
        payload = self._get("customers:listAccessibleCustomers", manager=False)
        accounts: list[dict[str, Any]] = []
        for resource in payload.get("resourceNames", []) or []:
            customer_id = _clean_id(str(resource).split("/")[-1])
            rows = self._search(customer_id, "SELECT customer.id, customer.descriptive_name, customer.currency_code, customer.time_zone, customer.status FROM customer LIMIT 1")
            customer = (rows[0].get("customer") if rows else {}) or {}
            accounts.append({
                "external_id": customer_id,
                "name": customer.get("descriptiveName") or f"Google Ads {customer_id}",
                "currency": customer.get("currencyCode"),
                "timezone": customer.get("timeZone"),
                "status": customer.get("status"),
            })
        return accounts

    def sync(self, customer_id: str, *, start: date, end: date) -> dict[str, list[dict[str, Any]]]:
        period = f"segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'"
        campaign_rows = self._search(customer_id, "SELECT campaign.id,campaign.name,campaign.status,campaign.advertising_channel_type,segments.date,segments.device,metrics.cost_micros,metrics.impressions,metrics.clicks,metrics.conversions,metrics.conversions_value FROM campaign WHERE " + period)
        entity_rows = self._search(customer_id, "SELECT campaign.id,campaign.name,campaign.status,ad_group.id,ad_group.name,ad_group.status,ad_group_ad.ad.id,ad_group_ad.ad.name,ad_group_ad.status FROM ad_group_ad WHERE ad_group_ad.status != 'REMOVED'")
        search_rows = self._search(customer_id, "SELECT segments.date,campaign.id,ad_group.id,search_term_view.search_term,segments.search_term_match_type,metrics.cost_micros,metrics.impressions,metrics.clicks,metrics.conversions FROM search_term_view WHERE " + period)
        click_start = max(start, end - timedelta(days=89))
        click_rows = self._search(customer_id, "SELECT click_view.gclid,segments.date,segments.hour,campaign.id,ad_group.id,ad_group_ad.ad.id FROM click_view WHERE segments.date BETWEEN '" + click_start.isoformat() + "' AND '" + end.isoformat() + "'")
        return {
            "metrics": [parse_google_ads_row(row) for row in campaign_rows],
            "entities": entity_rows,
            "search_terms": search_rows,
            "click_refs": [{
                "click_id_hash": click_id_hash((row.get("clickView") or {}).get("gclid")),
                "clicked_at": f"{(row.get('segments') or {}).get('date')}T{int((row.get('segments') or {}).get('hour') or 0):02d}:00:00Z",
                "campaign_external_id": str((row.get("campaign") or {}).get("id") or ""),
                "ad_group_external_id": str((row.get("adGroup") or {}).get("id") or ""),
                "ad_external_id": str((((row.get("adGroupAd") or {}).get("ad") or {}).get("id")) or ""),
            } for row in click_rows if (row.get("clickView") or {}).get("gclid")],
        }


class MetaAdsReadOnly:
    """Meta Marketing API adapter restricted to GET requests."""

    def __init__(self):
        self.token = str(os.getenv("META_ACCESS_TOKEN") or "").strip()
        if not self.token:
            raise PlatformConfigurationError("META_ACCESS_TOKEN_MISSING")
        self.base = f"https://graph.facebook.com/{META_GRAPH_API_VERSION}"

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = {**(params or {}), "access_token": self.token}
        response = requests.get(f"{self.base}/{path.lstrip('/')}", params=query, timeout=45)
        if not response.ok:
            raise PlatformRequestError(_safe_error(response))
        return response.json()

    def _all(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        query = dict(params)
        seen_cursors: set[str] = set()
        while True:
            payload = self._get(path, query)
            items.extend(payload.get("data") or [])
            cursor = str((((payload.get("paging") or {}).get("cursors") or {}).get("after")) or "").strip()
            if not cursor or cursor in seen_cursors:
                break
            # Keep every page on the configured Graph host and keep the token in
            # the adapter; never follow an absolute provider URL containing it.
            seen_cursors.add(cursor)
            query = {**params, "after": cursor}
        return items

    def list_assets(self) -> dict[str, list[dict[str, Any]]]:
        businesses = self._all("me/businesses", {"fields": "id,name", "limit": 100})
        accounts = self._all("me/adaccounts", {"fields": "id,account_id,name,currency,timezone_name,account_status,business", "limit": 200})
        pages = self._all("me/accounts", {"fields": "id,name,instagram_business_account{id,username}", "limit": 200})
        instagram = []
        for page in pages:
            asset = page.get("instagram_business_account") or {}
            if asset.get("id"):
                instagram.append({"id": str(asset["id"]), "name": asset.get("username") or str(asset["id"]), "page_id": str(page.get("id") or "")})
        return {"businesses": businesses, "accounts": accounts, "pages": pages, "instagram_accounts": instagram}

    def sync(self, account_id: str, *, start: date, end: date) -> dict[str, list[dict[str, Any]]]:
        account = str(account_id or "").removeprefix("act_")
        act = f"act_{account}"
        entities: list[dict[str, Any]] = []
        for edge, entity_type, fields in (
            ("campaigns", "CAMPAIGN", "id,name,status,effective_status,objective,created_time,updated_time"),
            ("adsets", "AD_SET", "id,name,status,effective_status,campaign_id,created_time,updated_time"),
            ("ads", "AD", "id,name,status,effective_status,campaign_id,adset_id,creative{id,name,thumbnail_url},created_time,updated_time"),
        ):
            for row in self._all(f"{act}/{edge}", {"fields": fields, "limit": 500}):
                entities.append({**row, "entity_type": entity_type})
        insights = self._all(
            f"{act}/insights",
            {"fields": "date_start,campaign_id,campaign_name,adset_id,adset_name,ad_id,ad_name,spend,impressions,reach,frequency,clicks,actions,action_values", "level": "ad", "time_increment": 1, "time_range": str({"since": start.isoformat(), "until": end.isoformat()}).replace("'", '"'), "limit": 500},
        )
        return {"entities": entities, "metrics": [parse_meta_ads_row(row) for row in insights], "search_terms": [], "click_refs": []}


def bounded_history(days: int, *, maximum: int = 730) -> tuple[date, date]:
    days = max(1, min(int(days), maximum))
    end = date.today()
    return end - timedelta(days=days - 1), end


def public_assets(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Allowlist public identifiers returned to Integration Center."""
    allowed = {"id", "external_id", "account_id", "name", "currency", "timezone", "timezone_name", "status", "account_status", "business", "page_id", "username"}
    safe: list[dict[str, Any]] = []
    for item in items:
        public: dict[str, Any] = {}
        for key, value in item.items():
            if key not in allowed:
                continue
            if key == "business" and isinstance(value, dict):
                public[key] = {nested: value[nested] for nested in ("id", "name") if nested in value}
            elif isinstance(value, (str, int, float, bool)) or value is None:
                public[key] = value
        safe.append(public)
    return safe
