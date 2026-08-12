from __future__ import annotations

from decimal import Decimal
from typing import Any


def safe_ratio(numerator: Any, denominator: Any) -> float | None:
    n, d = Decimal(str(numerator or 0)), Decimal(str(denominator or 0))
    return float(n / d) if d else None


def business_metrics(*, spend: Any, impressions: Any, clicks: Any, leads: Any, quotes: Any, sales: Any, revenue: Any) -> dict[str, float | None]:
    return {
        "ctr": safe_ratio(clicks, impressions), "cpc": safe_ratio(spend, clicks),
        "cpl_crm": safe_ratio(spend, leads), "cpq": safe_ratio(spend, quotes),
        "cpa_crm": safe_ratio(spend, sales), "roas_crm": safe_ratio(revenue, spend),
        "click_to_lead": safe_ratio(leads, clicks), "lead_to_quote": safe_ratio(quotes, leads),
        "quote_to_sale": safe_ratio(sales, quotes),
    }


def parse_google_ads_row(row: dict[str, Any]) -> dict[str, Any]:
    metrics = row.get("metrics") or {}
    segments = row.get("segments") or {}
    campaign, ad_group, ad = row.get("campaign") or {}, row.get("adGroup") or {}, row.get("adGroupAd") or {}
    return {
        "date": segments.get("date"), "campaign_id": str(campaign.get("id") or ""),
        "campaign_name": campaign.get("name"), "ad_group_id": str(ad_group.get("id") or ""),
        "ad_id": str((ad.get("ad") or {}).get("id") or ""), "device": segments.get("device"),
        "spend": float(metrics.get("costMicros") or 0) / 1_000_000,
        "impressions": int(metrics.get("impressions") or 0), "clicks": int(metrics.get("clicks") or 0),
        "conversions": float(metrics.get("conversions") or 0),
        "conversion_value": float(metrics.get("conversionsValue") or 0),
    }


def parse_meta_ads_row(row: dict[str, Any]) -> dict[str, Any]:
    actions = {str(x.get("action_type")): float(x.get("value") or 0) for x in (row.get("actions") or [])}
    action_values = {str(x.get("action_type")): float(x.get("value") or 0) for x in (row.get("action_values") or [])}
    conversion_value = next((action_values[key] for key in (
        "omni_purchase", "purchase", "offsite_conversion.fb_pixel_purchase", "lead", "offsite_conversion.fb_pixel_lead"
    ) if key in action_values), 0.0)
    return {
        "date": row.get("date_start"), "campaign_id": str(row.get("campaign_id") or ""),
        "campaign_name": row.get("campaign_name"), "ad_group_id": str(row.get("adset_id") or ""),
        "ad_id": str(row.get("ad_id") or ""), "spend": float(row.get("spend") or 0),
        "impressions": int(row.get("impressions") or 0), "reach": int(row.get("reach") or 0),
        "frequency": float(row.get("frequency") or 0), "clicks": int(row.get("clicks") or 0),
        "conversions": actions.get("lead", actions.get("offsite_conversion.fb_pixel_lead", 0.0)),
        "conversion_value": conversion_value,
    }


def change_risk(*, hours_since_change: float | None, conversions_30d: float, learning: bool, budget_change_pct: float = 0) -> dict[str, str]:
    if hours_since_change is not None and hours_since_change < 72:
        return {"risk": "HIGH", "state": "RECENT_CHANGE", "recommendation": "NO_ACTION_RECOMMENDED"}
    if learning:
        return {"risk": "HIGH", "state": "LEARNING", "recommendation": "NO_ACTION_RECOMMENDED"}
    if conversions_30d < 15:
        return {"risk": "MEDIUM", "state": "LOW_DATA", "recommendation": "INSUFFICIENT_EVIDENCE"}
    if abs(float(budget_change_pct)) > 30:
        return {"risk": "HIGH", "state": "STABLE", "recommendation": "REVIEW_REQUIRED"}
    return {"risk": "LOW", "state": "STABLE", "recommendation": "ANALYZE"}
