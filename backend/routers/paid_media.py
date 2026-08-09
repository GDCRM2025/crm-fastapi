from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from backend.core.database import engine
from backend.gd_intelligence.paid_media import business_metrics
from backend.gd_intelligence.ad_platforms import google_ads_capabilities, meta_ads_capabilities
from backend.gd_intelligence.permissions import has_permission
from backend.routers.auth import get_current_user


router = APIRouter(prefix="/api/gd-intelligence/paid-media", tags=["paid-media"])


def _require(conn, user: dict, permission: str = "paid_media_view") -> None:
    if not has_permission(conn, user, permission):
        raise HTTPException(403, "Sin permiso para Paid Media")


@router.get("/status")
def status(user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        _require(conn, user)
        accounts = [dict(x) for x in conn.execute(text("""
          SELECT a.id,a.platform,a.external_id,a.name,a.currency,a.status,a.enabled,
                 a.last_verified_at,a.last_sync_at,
                 COALESCE(jsonb_agg(jsonb_build_object('site_id',s.id,'code',s.code,'name',s.name))
                   FILTER(WHERE s.id IS NOT NULL),'[]'::jsonb) AS sites
          FROM wi_ad_accounts a LEFT JOIN wi_ad_account_sites x ON x.account_id=a.id
          LEFT JOIN wi_sites s ON s.id=x.site_id GROUP BY a.id ORDER BY a.platform,a.name
        """)).mappings()]
    google = google_ads_capabilities()
    meta = meta_ads_capabilities()
    return {"ok": True, "mode": "READ_ANALYZE_RECOMMEND", "accounts": accounts,
            "backend": {"google_ads": bool(google["developer_token"] and (google["service_account"] or google["oauth_refresh"])),
                        "meta_ads": bool(meta["system_user_or_oauth_token"])},
            "authorization": {"google_ads": google, "meta_ads": meta}}


@router.get("/summary")
def summary(days: int = Query(30, ge=1, le=730), user: dict = Depends(get_current_user)):
    start = date.today() - timedelta(days=days - 1)
    with engine.connect() as conn:
        _require(conn, user)
        row = conn.execute(text("""
          SELECT COALESCE(SUM(m.spend),0) spend,COALESCE(SUM(m.impressions),0) impressions,
                 COALESCE(SUM(m.clicks),0) clicks,COALESCE(SUM(m.platform_conversions),0) platform_conversions,
                 COALESCE(SUM(m.platform_conversion_value),0) platform_conversion_value
          FROM wi_ad_metrics_daily m WHERE m.metric_date>=:start
        """), {"start": start}).mappings().one()
        crm = conn.execute(text("""
          SELECT COUNT(DISTINCT la.lead_id) leads,
                 COUNT(DISTINCT c.id_cotizacion) quotes,
                 COUNT(DISTINCT CASE WHEN upper(COALESCE(e.nombre,'')) LIKE '%CONFIRM%' THEN l.id_lead END) sales,
                 COALESCE(SUM(CASE WHEN upper(COALESCE(e.nombre,'')) LIKE '%CONFIRM%' THEN c.total ELSE 0 END),0) revenue
          FROM wi_lead_attribution la JOIN leads l ON l.id_lead=la.lead_id
          LEFT JOIN estados_lead e ON e.id_estado=l.id_estado
          LEFT JOIN cotizaciones c ON c.id_lead=l.id_lead
          WHERE la.created_at>=:start
        """), {"start": start}).mappings().one()
    raw = {**dict(row), **dict(crm)}
    return {"ok": True, "period": {"from": start, "to": date.today()}, "raw": raw,
            "calculated": business_metrics(spend=raw["spend"], impressions=raw["impressions"], clicks=raw["clicks"],
                                            leads=raw["leads"], quotes=raw["quotes"], sales=raw["sales"], revenue=raw["revenue"]),
            "sale_definition": "Lead cuyo estado real contiene CONFIRM y revenue de cotizaciones asociadas"}


@router.get("/search-terms")
def search_terms(limit: int = Query(100, ge=1, le=500), user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        _require(conn, user)
        rows = conn.execute(text("""
          SELECT st.metric_date,a.name account,st.campaign_external_id,st.ad_group_external_id,
                 st.search_term,st.match_type,st.spend,st.impressions,st.clicks,st.platform_conversions,
                 st.opportunity_status
          FROM wi_search_terms st JOIN wi_ad_accounts a ON a.id=st.account_id
          ORDER BY st.metric_date DESC,st.spend DESC LIMIT :limit
        """), {"limit": limit}).mappings()
        return {"ok": True, "items": [dict(x) for x in rows],
                "policy": "Las negativas son candidatas; nunca se publican automáticamente."}
