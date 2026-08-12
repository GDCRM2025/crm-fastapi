from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text

from backend.core.database import engine
from backend.gd_intelligence.ad_platforms import (
    GoogleAdsReadOnly,
    MetaAdsReadOnly,
    PlatformConfigurationError,
    PlatformRequestError,
    bounded_history,
    google_ads_capabilities,
    meta_ads_capabilities,
    public_assets,
)
from backend.gd_intelligence.intelligence import actionable_alerts, executive_readiness, safe_ai_context, seo_opportunity_score
from backend.gd_intelligence.paid_media_intelligence import assess_change_risk, assess_creative_fatigue, attribute_paid_media, classify_search_term
from backend.gd_intelligence.permissions import has_permission, is_superadmin
from backend.routers.auth import get_current_user


def _superadmin_only(user: dict = Depends(get_current_user)) -> None:
    if not is_superadmin(user):
        raise HTTPException(403, "GD Intelligence está disponible temporalmente sólo para SUPERADMIN.")


router = APIRouter(
    prefix="/api/gd-intelligence/intelligence",
    tags=["intelligence-platform"],
    dependencies=[Depends(_superadmin_only)],
)


def _require(conn, user: dict, permission: str) -> None:
    if not has_permission(conn, user, permission):
        raise HTTPException(403, "Sin permiso para esta función.")


def _actor(user: dict) -> str:
    return str(user.get("username") or user.get("email") or user.get("id_usuario") or "unknown")[:200]


class AdAccountSelection(BaseModel):
    platform: Literal["GOOGLE_ADS", "META_ADS"]
    external_id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=240)
    currency: str | None = Field(default=None, max_length=3)
    timezone: str | None = Field(default=None, max_length=64)
    site_ids: list[int] = Field(min_length=1, max_length=20)
    manager_external_id: str | None = Field(default=None, max_length=120)
    business_external_id: str | None = Field(default=None, max_length=120)
    page_external_id: str | None = Field(default=None, max_length=120)
    instagram_external_id: str | None = Field(default=None, max_length=120)

    @field_validator("external_id", "manager_external_id", "business_external_id", "page_external_id", "instagram_external_id")
    @classmethod
    def public_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().removeprefix("act_")
        if not value or not value.replace("-", "").isdigit():
            raise ValueError("El identificador público no tiene un formato válido.")
        return value


class AIContextRequest(BaseModel):
    sources: list[str] = Field(default_factory=list, max_length=20)


@router.get("/ad-accounts/discover")
def discover_ad_accounts(platform: Literal["GOOGLE_ADS", "META_ADS"], user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        _require(conn, user, "paid_media_manage")
    try:
        if platform == "GOOGLE_ADS":
            items = GoogleAdsReadOnly().list_accounts()
            return {"ok": True, "platform": platform, "mode": "READ_ONLY", "accounts": public_assets(items), "capabilities": google_ads_capabilities()}
        assets = MetaAdsReadOnly().list_assets()
        return {"ok": True, "platform": platform, "mode": "READ_ONLY", "assets": {key: public_assets(value) for key, value in assets.items()}, "capabilities": meta_ads_capabilities()}
    except PlatformConfigurationError as exc:
        raise HTTPException(409, {"code": str(exc), "action": "CONFIGURAR AUTORIZACIÓN"}) from exc
    except PlatformRequestError as exc:
        raise HTTPException(502, {"code": str(exc), "action": "RECONECTAR"}) from exc


@router.post("/ad-accounts/select")
def select_ad_account(payload: AdAccountSelection, user: dict = Depends(get_current_user)):
    with engine.begin() as conn:
        _require(conn, user, "paid_media_manage")
        valid_sites = conn.execute(text("SELECT id FROM wi_sites WHERE id=ANY(:ids) AND enabled=true"), {"ids": payload.site_ids}).scalars().all()
        if set(valid_sites) != set(payload.site_ids):
            raise HTTPException(422, "Uno o más sitios no están disponibles.")
        account_id = conn.execute(text("""
          INSERT INTO wi_ad_accounts(platform,external_id,name,currency,timezone,status,enabled,last_verified_at,
            manager_external_id,business_external_id,page_external_id,instagram_external_id)
          VALUES(:platform,:external_id,:name,:currency,:timezone,'CONNECTED',true,now(),:manager,:business,:page,:instagram)
          ON CONFLICT(platform,external_id) DO UPDATE SET name=excluded.name,currency=excluded.currency,
            timezone=excluded.timezone,status='CONNECTED',enabled=true,last_verified_at=now(),
            manager_external_id=excluded.manager_external_id,business_external_id=excluded.business_external_id,
            page_external_id=excluded.page_external_id,instagram_external_id=excluded.instagram_external_id,updated_at=now()
          RETURNING id
        """), {"platform": payload.platform, "external_id": payload.external_id, "name": payload.name,
               "currency": payload.currency, "timezone": payload.timezone, "manager": payload.manager_external_id,
               "business": payload.business_external_id, "page": payload.page_external_id, "instagram": payload.instagram_external_id}).scalar_one()
        conn.execute(text("DELETE FROM wi_ad_account_sites WHERE account_id=:id"), {"id": account_id})
        for site_id in payload.site_ids:
            conn.execute(text("INSERT INTO wi_ad_account_sites(account_id,site_id) VALUES(:account,:site)"), {"account": account_id, "site": site_id})
    return {"ok": True, "account_id": account_id, "platform": payload.platform, "mode": "READ_ONLY"}


def _persist_entities(conn, account_id: int, platform: str, rows: list[dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        if platform == "GOOGLE_ADS":
            campaign, group, wrapper = row.get("campaign") or {}, row.get("adGroup") or {}, row.get("adGroupAd") or {}
            ad = wrapper.get("ad") or {}
            candidates = (
                ("CAMPAIGN", campaign.get("id"), None, campaign.get("name"), campaign.get("status"), campaign.get("id"), None, {}),
                ("AD_SET", group.get("id"), campaign.get("id"), group.get("name"), group.get("status"), campaign.get("id"), group.get("id"), {}),
                ("AD", ad.get("id"), group.get("id"), ad.get("name") or f"Ad {ad.get('id')}", wrapper.get("status"), campaign.get("id"), group.get("id"), {}),
            )
        else:
            entity_type = str(row.get("entity_type") or "")
            parent = row.get("campaign_id") if entity_type == "AD_SET" else row.get("adset_id") if entity_type == "AD" else None
            candidates = ((entity_type, row.get("id"), parent, row.get("name"), row.get("effective_status") or row.get("status"), row.get("campaign_id") or (row.get("id") if entity_type == "CAMPAIGN" else None), row.get("adset_id"), {"creative": row.get("creative") or {}}),)
        for entity_type, external_id, parent_id, name, status, campaign_id, group_id, metadata in candidates:
            if not external_id:
                continue
            conn.execute(text("""
              INSERT INTO wi_ad_entities(account_id,platform,entity_type,external_id,parent_external_id,name,status,campaign_external_id,ad_group_external_id,metadata)
              VALUES(:account,:platform,:type,:external,:parent,:name,:status,:campaign,:group,CAST(:metadata AS jsonb))
              ON CONFLICT(account_id,entity_type,external_id) DO UPDATE SET parent_external_id=excluded.parent_external_id,
                name=excluded.name,status=excluded.status,campaign_external_id=excluded.campaign_external_id,
                ad_group_external_id=excluded.ad_group_external_id,metadata=excluded.metadata,last_seen_at=now()
            """), {"account": account_id, "platform": platform, "type": entity_type, "external": str(external_id),
                   "parent": str(parent_id) if parent_id else None, "name": str(name or f"{entity_type} {external_id}"),
                   "status": str(status or "UNKNOWN"), "campaign": str(campaign_id or ""), "group": str(group_id or ""),
                   "metadata": __import__("json").dumps(metadata)})
            count += 1
    return count


def _persist_metrics(conn, account_id: int, rows: list[dict[str, Any]]) -> int:
    for row in rows:
        conn.execute(text("""
          INSERT INTO wi_ad_metrics_daily(account_id,metric_date,campaign_external_id,ad_group_external_id,ad_external_id,
            creative_external_id,device,spend,impressions,reach,frequency,clicks,platform_conversions,platform_conversion_value)
          VALUES(:account,:date,:campaign,:group,:ad,:creative,:device,:spend,:impressions,:reach,:frequency,:clicks,:conversions,:value)
          ON CONFLICT(account_id,metric_date,campaign_external_id,ad_group_external_id,ad_external_id,creative_external_id,keyword_external_id,device,network)
          DO UPDATE SET spend=excluded.spend,impressions=excluded.impressions,reach=excluded.reach,frequency=excluded.frequency,
            clicks=excluded.clicks,platform_conversions=excluded.platform_conversions,platform_conversion_value=excluded.platform_conversion_value,ingested_at=now()
        """), {"account": account_id, "date": row.get("date"), "campaign": row.get("campaign_id") or "",
               "group": row.get("ad_group_id") or "", "ad": row.get("ad_id") or "", "creative": row.get("creative_id") or "",
               "device": row.get("device") or "", "spend": row.get("spend") or 0, "impressions": row.get("impressions") or 0,
               "reach": row.get("reach"), "frequency": row.get("frequency"), "clicks": row.get("clicks") or 0,
               "conversions": row.get("conversions") or 0, "value": row.get("conversion_value") or 0})
    return len(rows)


def _persist_search_terms(conn, account_id: int, rows: list[dict[str, Any]]) -> int:
    for row in rows:
        metrics = row.get("metrics") or {}
        segments = row.get("segments") or {}
        conn.execute(text("""
          INSERT INTO wi_search_terms(account_id,metric_date,campaign_external_id,ad_group_external_id,search_term,match_type,spend,impressions,clicks,platform_conversions)
          VALUES(:account,:date,:campaign,:group,:term,:match,:spend,:impressions,:clicks,:conversions)
          ON CONFLICT(account_id,metric_date,campaign_external_id,ad_group_external_id,search_term)
          DO UPDATE SET match_type=excluded.match_type,spend=excluded.spend,impressions=excluded.impressions,
            clicks=excluded.clicks,platform_conversions=excluded.platform_conversions
        """), {"account": account_id, "date": segments.get("date"), "campaign": str((row.get("campaign") or {}).get("id") or ""),
               "group": str((row.get("adGroup") or {}).get("id") or ""), "term": str((row.get("searchTermView") or {}).get("searchTerm") or ""),
               "match": segments.get("searchTermMatchType"), "spend": float(metrics.get("costMicros") or 0) / 1_000_000,
               "impressions": metrics.get("impressions") or 0, "clicks": metrics.get("clicks") or 0,
               "conversions": metrics.get("conversions") or 0})
    return len(rows)


def _persist_click_refs(conn, account_id: int, platform: str, rows: list[dict[str, Any]]) -> int:
    for row in rows:
        if not row.get("click_id_hash"):
            continue
        conn.execute(text("""
          INSERT INTO wi_ad_click_refs(account_id,platform,click_id_hash,clicked_at,campaign_external_id,ad_group_external_id,ad_external_id)
          VALUES(:account,:platform,:hash,:clicked,:campaign,:group,:ad)
          ON CONFLICT(platform,click_id_hash) DO UPDATE SET account_id=excluded.account_id,clicked_at=excluded.clicked_at,
            campaign_external_id=excluded.campaign_external_id,ad_group_external_id=excluded.ad_group_external_id,
            ad_external_id=excluded.ad_external_id,ingested_at=now()
        """), {"account": account_id, "platform": platform, "hash": row["click_id_hash"], "clicked": row.get("clicked_at"),
               "campaign": row.get("campaign_external_id"), "group": row.get("ad_group_external_id"), "ad": row.get("ad_external_id")})
    return len([row for row in rows if row.get("click_id_hash")])


@router.post("/ad-accounts/{account_id}/sync")
def sync_ad_account(account_id: int, days: int = Query(90, ge=1, le=730), user: dict = Depends(get_current_user)):
    start, end = bounded_history(days)
    with engine.begin() as conn:
        _require(conn, user, "paid_media_manage")
        account = conn.execute(text("SELECT id,platform,external_id FROM wi_ad_accounts WHERE id=:id AND enabled=true"), {"id": account_id}).mappings().first()
        if not account:
            raise HTTPException(404, "Cuenta publicitaria no encontrada.")
        run_id = conn.execute(text("INSERT INTO wi_ad_sync_runs(account_id,source,period_start,period_end,started_by) VALUES(:id,:source,:start,:end,:actor) RETURNING id"),
                              {"id": account_id, "source": account["platform"], "start": start, "end": end, "actor": _actor(user)}).scalar_one()
    try:
        adapter = GoogleAdsReadOnly() if account["platform"] == "GOOGLE_ADS" else MetaAdsReadOnly()
        payload = adapter.sync(str(account["external_id"]), start=start, end=end)
        with engine.begin() as conn:
            entities = _persist_entities(conn, account_id, str(account["platform"]), payload["entities"])
            metrics = _persist_metrics(conn, account_id, payload["metrics"])
            terms = _persist_search_terms(conn, account_id, payload.get("search_terms") or [])
            click_refs = _persist_click_refs(conn, account_id, str(account["platform"]), payload.get("click_refs") or [])
            conn.execute(text("UPDATE wi_ad_sync_runs SET status='COMPLETED',entities_count=:entities,metrics_count=:metrics,search_terms_count=:terms,finished_at=now() WHERE id=:id"),
                         {"id": run_id, "entities": entities, "metrics": metrics, "terms": terms})
            conn.execute(text("UPDATE wi_ad_accounts SET status='CONNECTED',last_sync_at=now(),last_verified_at=now(),last_error_code=NULL,last_error_safe=NULL,updated_at=now() WHERE id=:id"), {"id": account_id})
        return {"ok": True, "mode": "READ_ONLY", "run_id": run_id, "period": {"from": start, "to": end}, "counts": {"entities": entities, "metrics": metrics, "search_terms": terms, "click_refs": click_refs}}
    except (PlatformConfigurationError, PlatformRequestError) as exc:
        with engine.begin() as conn:
            conn.execute(text("UPDATE wi_ad_sync_runs SET status='FAILED',error_code=:code,finished_at=now() WHERE id=:id"), {"id": run_id, "code": str(exc)[:80]})
            conn.execute(text("UPDATE wi_ad_accounts SET status='ERROR',last_error_code=:code,last_error_safe='No pudimos sincronizar la cuenta. Revisa la autorización.',updated_at=now() WHERE id=:id"), {"id": account_id, "code": str(exc)[:80]})
        raise HTTPException(409 if isinstance(exc, PlatformConfigurationError) else 502, {"code": str(exc), "action": "RECONECTAR"}) from exc


@router.post("/paid-media/analyze")
def analyze_paid_media(user: dict = Depends(get_current_user)):
    """Build local intelligence snapshots. This never calls an Ads mutate endpoint."""
    with engine.begin() as conn:
        _require(conn, user, "paid_media_manage")
        attributed = 0
        lead_rows = conn.execute(text("""
          SELECT la.id lead_attribution_id,la.lead_id,la.session_id,s.site_id,s.landing_url,s.utm_source,s.utm_campaign,s.utm_content,s.gclid_hash,s.fbclid_hash
          FROM wi_lead_attribution la JOIN wi_sessions s ON s.session_id=la.session_id
        """)).mappings().all()
        for lead in lead_rows:
            candidates = conn.execute(text("""
              SELECT a.platform,a.id account_id,r.campaign_external_id,NULL::text campaign_name,
                r.ad_group_external_id,r.ad_external_id,r.click_id_hash
              FROM wi_ad_accounts a JOIN wi_ad_account_sites x ON x.account_id=a.id
              JOIN wi_ad_click_refs r ON r.account_id=a.id AND (r.click_id_hash=:gclid OR r.click_id_hash=:fbclid)
              WHERE x.site_id=:site AND a.enabled=true
              UNION ALL
              SELECT a.platform,a.id account_id,e.campaign_external_id,e.name campaign_name,e.ad_group_external_id,
                CASE WHEN e.entity_type='AD' THEN e.external_id END ad_external_id,NULL::char(64) click_id_hash
              FROM wi_ad_accounts a JOIN wi_ad_account_sites x ON x.account_id=a.id
              JOIN wi_ad_entities e ON e.account_id=a.id AND e.entity_type IN ('CAMPAIGN','AD')
              WHERE x.site_id=:site AND a.enabled=true
            """), {"site": lead["site_id"], "gclid": lead["gclid_hash"], "fbclid": lead["fbclid_hash"]}).mappings().all()
            result = attribute_paid_media(dict(lead), [dict(row) for row in candidates])
            crm = conn.execute(text("""
              SELECT COUNT(c.id_cotizacion) quotes,
                COUNT(c.id_cotizacion) FILTER(WHERE upper(COALESCE(e.nombre,'')) LIKE '%CONFIRM%') sales,
                COALESCE(SUM(c.total) FILTER(WHERE upper(COALESCE(e.nombre,'')) LIKE '%CONFIRM%'),0) revenue
              FROM leads l LEFT JOIN estados_lead e ON e.id_estado=l.id_estado
              LEFT JOIN cotizaciones c ON c.id_lead=l.id_lead WHERE l.id_lead=:lead
            """), {"lead": lead["lead_id"]}).mappings().one()
            conn.execute(text("""
              INSERT INTO wi_paid_media_attribution(lead_id,lead_attribution_id,session_id,account_id,platform,
                campaign_external_id,ad_external_id,click_id_hash,attribution_method,confidence,reason,evidence,quote_count,sale_count,revenue)
              VALUES(:lead,:la,:session,:account,:platform,:campaign,:ad,:click_hash,:method,:confidence,:reason,CAST(:evidence AS jsonb),:quotes,:sales,:revenue)
              ON CONFLICT(lead_id) DO UPDATE SET lead_attribution_id=excluded.lead_attribution_id,session_id=excluded.session_id,
                account_id=excluded.account_id,platform=excluded.platform,campaign_external_id=excluded.campaign_external_id,
                ad_external_id=excluded.ad_external_id,click_id_hash=excluded.click_id_hash,attribution_method=excluded.attribution_method,
                confidence=excluded.confidence,reason=excluded.reason,evidence=excluded.evidence,quote_count=excluded.quote_count,
                sale_count=excluded.sale_count,revenue=excluded.revenue,calculated_at=now()
            """), {"lead": lead["lead_id"], "la": lead["lead_attribution_id"], "session": lead["session_id"],
                   "account": result["account_id"], "platform": result["platform"], "campaign": result["campaign_external_id"],
                   "ad": result["ad_external_id"], "click_hash": lead["gclid_hash"] or lead["fbclid_hash"], "method": result["method"],
                   "confidence": result["confidence"], "reason": result["reason"], "evidence": __import__("json").dumps(result["evidence"]),
                   "quotes": crm["quotes"], "sales": crm["sales"], "revenue": crm["revenue"]})
            attributed += 1
        terms = conn.execute(text("SELECT id,spend,impressions,clicks,platform_conversions,crm_leads,crm_sales,crm_revenue FROM wi_search_terms")).mappings().all()
        for row in terms:
            result = classify_search_term(spend=row["spend"],impressions=row["impressions"],clicks=row["clicks"],platform_conversions=row["platform_conversions"],crm_leads=row["crm_leads"],crm_sales=row["crm_sales"],crm_revenue=row["crm_revenue"])
            conn.execute(text("UPDATE wi_search_terms SET opportunity_status=:state,classification_reason=:reason,classification_confidence=:confidence,evaluated_at=now() WHERE id=:id"), {"id": row["id"], **result})
        creative_rows = conn.execute(text("""
          WITH periods AS (
            SELECT account_id,creative_external_id,
              MIN(metric_date) first_date,MAX(metric_date) last_date,
              SUM(impressions) FILTER(WHERE metric_date>=current_date-13) impressions,
              AVG(frequency) FILTER(WHERE metric_date>=current_date-13) frequency,
              CASE WHEN SUM(impressions) FILTER(WHERE metric_date>=current_date-13)>0 THEN
                SUM(clicks) FILTER(WHERE metric_date>=current_date-13)::numeric/SUM(impressions) FILTER(WHERE metric_date>=current_date-13) END ctr_current,
              CASE WHEN SUM(impressions) FILTER(WHERE metric_date BETWEEN current_date-27 AND current_date-14)>0 THEN
                SUM(clicks) FILTER(WHERE metric_date BETWEEN current_date-27 AND current_date-14)::numeric/SUM(impressions) FILTER(WHERE metric_date BETWEEN current_date-27 AND current_date-14) END ctr_previous
            FROM wi_ad_metrics_daily WHERE creative_external_id<>'' GROUP BY account_id,creative_external_id
          ) SELECT * FROM periods
        """)).mappings().all()
        for row in creative_rows:
            result = assess_creative_fatigue(age_days=max(0,(date.today()-row["first_date"]).days),impressions=row["impressions"],frequency=row["frequency"],ctr_current=row["ctr_current"],ctr_previous=row["ctr_previous"])
            conn.execute(text("""
              INSERT INTO wi_creative_intelligence(account_id,creative_external_id,period_start,period_end,state,confidence,reason,signals,metrics)
              VALUES(:account,:creative,:start,:end,:state,:confidence,:reason,CAST(:signals AS jsonb),CAST(:metrics AS jsonb))
              ON CONFLICT(account_id,creative_external_id,period_start,period_end) DO UPDATE SET state=excluded.state,
                confidence=excluded.confidence,reason=excluded.reason,signals=excluded.signals,metrics=excluded.metrics,evaluated_at=now()
            """), {"account": row["account_id"], "creative": row["creative_external_id"], "start": date.today()-timedelta(days=27), "end": date.today(),
                   "state": result["state"], "confidence": result["confidence"], "reason": result["reason"],
                   "signals": __import__("json").dumps(result["signals"]), "metrics": __import__("json").dumps(result["metrics"])})
        changes = conn.execute(text("""
          SELECT c.account_id,c.entity_type,c.entity_external_id,c.created_at,c.risk,
            COALESCE(SUM(m.platform_conversions) FILTER(WHERE m.metric_date>=current_date-29),0) conversions_30d
          FROM wi_ads_change_log c LEFT JOIN wi_ad_metrics_daily m ON m.account_id=c.account_id
            AND (m.campaign_external_id=c.entity_external_id OR m.ad_group_external_id=c.entity_external_id OR m.ad_external_id=c.entity_external_id)
          GROUP BY c.id ORDER BY c.created_at DESC
        """)).mappings().all()
        seen: set[tuple[Any, Any, Any]] = set()
        risk_count = 0
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        for row in changes:
            key = (row["account_id"],row["entity_type"],row["entity_external_id"])
            if key in seen:
                continue
            seen.add(key)
            created = row["created_at"]
            hours = max(0,(now-created).total_seconds()/3600) if created else None
            result = assess_change_risk(hours_since_change=hours,conversions_30d=row["conversions_30d"],learning=False,evidence_complete=True)
            conn.execute(text("""
              INSERT INTO wi_ads_change_risk(account_id,entity_type,entity_external_id,state,risk,confidence,reason,recommendation,next_review_date,metrics_to_watch,metrics)
              VALUES(:account,:type,:external,:state,:risk,:confidence,:reason,:recommendation,:next_review,CAST(:watch AS jsonb),CAST(:metrics AS jsonb))
            """), {"account": row["account_id"], "type": row["entity_type"], "external": row["entity_external_id"],
                   "state": result["state"], "risk": result["risk"], "confidence": result["confidence"], "reason": result["reason"],
                   "recommendation": result["recommendation"], "next_review": result["next_review_date"],
                   "watch": __import__("json").dumps(result["metrics_to_watch"]), "metrics": __import__("json").dumps(result["metrics"])})
            risk_count += 1
    return {"ok": True, "mode": "READ_ANALYZE_RECOMMEND", "attributions": attributed, "search_terms": len(terms), "creatives": len(creative_rows), "change_risks": risk_count, "platform_writes": 0}


@router.get("/paid-media/attribution")
def paid_media_attribution(limit: int = Query(200, ge=1, le=1000), user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        _require(conn, user, "paid_media_view")
        rows = conn.execute(text("""
          SELECT p.id,p.lead_id,p.platform,p.account_id,a.name account,p.campaign_external_id,p.ad_external_id,
            p.attribution_method,p.confidence,p.reason,p.evidence,p.quote_count,p.sale_count,p.revenue,p.calculated_at
          FROM wi_paid_media_attribution p LEFT JOIN wi_ad_accounts a ON a.id=p.account_id
          ORDER BY p.calculated_at DESC LIMIT :limit
        """), {"limit": limit}).mappings().all()
    return {"ok": True, "items": [dict(row) for row in rows], "methods": ["EXACT","STRONG","INFERRED","UNKNOWN"]}


@router.get("/paid-media/search-terms")
def intelligent_search_terms(limit: int = Query(250, ge=1, le=1000), user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        _require(conn, user, "paid_media_view")
        rows = conn.execute(text("""
          SELECT s.id,s.metric_date,a.name account,s.search_term,s.match_type,s.spend,s.impressions,s.clicks,
            s.platform_conversions,s.crm_leads,s.crm_sales,s.crm_revenue,s.opportunity_status state,
            s.classification_confidence confidence,s.classification_reason reason,s.evaluated_at
          FROM wi_search_terms s JOIN wi_ad_accounts a ON a.id=s.account_id
          ORDER BY s.metric_date DESC,s.spend DESC LIMIT :limit
        """), {"limit": limit}).mappings().all()
    return {"ok": True, "items": [dict(row) for row in rows], "policy": "NEGATIVE_CANDIDATE requiere revisión humana; nunca se publica automáticamente."}


@router.get("/paid-media/creatives")
def creative_intelligence(limit: int = Query(200, ge=1, le=1000), user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        _require(conn, user, "paid_media_view")
        rows = conn.execute(text("""
          SELECT DISTINCT ON(c.account_id,c.creative_external_id) c.id,a.name account,c.creative_external_id,
            c.period_start,c.period_end,c.state,c.confidence,c.reason,c.signals,c.metrics,c.evaluated_at
          FROM wi_creative_intelligence c JOIN wi_ad_accounts a ON a.id=c.account_id
          ORDER BY c.account_id,c.creative_external_id,c.evaluated_at DESC LIMIT :limit
        """), {"limit": limit}).mappings().all()
    return {"ok": True, "items": [dict(row) for row in rows], "policy": "Las señales de fatiga son hipótesis de revisión, no cambios automáticos."}


@router.get("/paid-media/change-risk")
def paid_media_change_risk(limit: int = Query(200, ge=1, le=1000), user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        _require(conn, user, "paid_media_view")
        rows = conn.execute(text("SELECT id,account_id,entity_type,entity_external_id,state,risk,confidence,reason,recommendation,next_review_date,metrics_to_watch,metrics,evaluated_at FROM wi_ads_change_risk ORDER BY evaluated_at DESC LIMIT :limit"), {"limit": limit}).mappings().all()
    return {"ok": True, "items": [dict(row) for row in rows], "platform_writes": 0}


@router.get("/seo/opportunities")
def seo_opportunities(days: int = Query(90, ge=7, le=730), user: dict = Depends(get_current_user)):
    since = date.today() - timedelta(days=days - 1)
    with engine.begin() as conn:
        _require(conn, user, "web_intelligence_seo")
        sites = conn.execute(text("SELECT id,code,name FROM wi_sites WHERE enabled=true ORDER BY code")).mappings().all()
        output: list[dict[str, Any]] = []
        for site in sites:
            technical = conn.execute(text("""
              SELECT (CASE WHEN status='ONLINE' THEN 25 ELSE 0 END + CASE WHEN https_ok THEN 15 ELSE 0 END +
                CASE WHEN canonical_ok THEN 15 ELSE 0 END + CASE WHEN sitemap_ok THEN 10 ELSE 0 END +
                CASE WHEN title_ok THEN 10 ELSE 0 END + CASE WHEN meta_description_ok THEN 10 ELSE 0 END +
                CASE WHEN h1_ok THEN 10 ELSE 0 END + CASE WHEN missing_alt_count=0 THEN 5 ELSE 0 END)::numeric score
              FROM wi_site_health_runs WHERE site_id=:site ORDER BY started_at DESC LIMIT 1
            """), {"site": site["id"]}).scalar()
            queries = conn.execute(text("""
              SELECT query,page,SUM(clicks)::int clicks,SUM(impressions)::int impressions,
                CASE WHEN SUM(impressions)>0 THEN SUM(clicks)::numeric/SUM(impressions) END ctr,
                AVG(average_position) position
              FROM wi_seo_query_daily WHERE site_id=:site AND metric_date>=:since
              GROUP BY query,page ORDER BY SUM(impressions) DESC LIMIT 100
            """), {"site": site["id"], "since": since}).mappings().all()
            if not queries and technical is not None:
                score = seo_opportunity_score(technical_score=float(technical))
                output.append({"site_id": site["id"], "site_code": site["code"], "opportunity_key": "technical-site", "query": None, "page": None, **score})
            for row in queries:
                traffic = conn.execute(text("SELECT COALESCE(SUM(sessions),0) sessions,COALESCE(SUM(conversions),0) conversions,COALESCE(SUM(revenue),0) revenue FROM wi_daily_metrics WHERE site_id=:site AND metric_date>=:since AND landing_page=:page"),
                                       {"site": site["id"], "since": since, "page": row["page"]}).mappings().one()
                score = seo_opportunity_score(impressions=row["impressions"],clicks=row["clicks"],ctr=float(row["ctr"] or 0),position=float(row["position"] or 0),sessions=float(traffic["sessions"]),conversions=float(traffic["conversions"]),revenue=float(traffic["revenue"]),technical_score=float(technical) if technical is not None else None)
                output.append({"site_id": site["id"], "site_code": site["code"], "opportunity_key": f"{row['query']}|{row['page']}", "query": row["query"], "page": row["page"], **score})
        output.sort(key=lambda item: item["score"], reverse=True)
        for item in output:
            conn.execute(text("""
              INSERT INTO wi_seo_opportunities(site_id,opportunity_key,query,page_url,score,state,reason,evidence,next_action)
              VALUES(:site_id,:opportunity_key,:query,:page,:score,:state,:reason,CAST(:evidence AS jsonb),'REVISAR OPORTUNIDAD')
              ON CONFLICT(site_id,opportunity_key) DO UPDATE SET query=excluded.query,page_url=excluded.page_url,
                score=excluded.score,state=excluded.state,reason=excluded.reason,evidence=excluded.evidence,observed_at=now()
            """), {**item, "evidence": __import__("json").dumps(item.get("components") or {})})
        return {"ok": True, "items": output, "policy": "Sólo evidencia real disponible; las fuentes ausentes producen estados parciales."}


@router.post("/alerts/refresh")
def refresh_alerts(user: dict = Depends(get_current_user)):
    with engine.begin() as conn:
        _require(conn, user, "web_intelligence_view")
        integrations = conn.execute(text("SELECT id,provider,status,last_verified_at,last_error_safe FROM wi_integrations WHERE enabled OR status IN ('ERROR','WARNING')")).mappings().all()
        health = conn.execute(text("SELECT DISTINCT ON(site_id) id,site_id,status,http_status,error_safe FROM wi_site_health_runs ORDER BY site_id,started_at DESC")).mappings().all()
        ads = conn.execute(text("SELECT DISTINCT ON(account_id,entity_type,entity_external_id) id entity_id,state,risk,confidence,reason,metrics FROM wi_ads_change_risk ORDER BY account_id,entity_type,entity_external_id,evaluated_at DESC")).mappings().all()
        seo = conn.execute(text("SELECT id,opportunity_key,score,state,reason FROM wi_seo_opportunities WHERE score>=65")).mappings().all()
        conversations: list[dict[str, Any]] = []
        try:
            from backend.omnichannel.service import list_inbox
            from datetime import datetime, timezone

            current = datetime.now(timezone.utc)
            for item in list_inbox(conn, limit=200):
                if not item.get("unread_count") or not (item.get("lead_id") or item.get("channel") == "WHATSAPP"):
                    continue
                occurred = datetime.fromisoformat(str(item.get("occurred_at") or "").replace("Z", "+00:00"))
                minutes = max(0, int((current - occurred).total_seconds() / 60))
                if minutes <= 1440:
                    conversations.append({"id": f"{item['channel']}:{item['source_ref']}", "channel": item["channel"], "unattended_minutes": minutes, "threshold_minutes": 30, "action_target": "/web/views/omnichannel_inbox.html"})
        except (ValueError, TypeError):
            conversations = []
        generated = actionable_alerts(integrations=integrations, site_health=health, conversations=conversations, ads=ads, seo=seo)
        for alert in generated:
            conn.execute(text("""
              INSERT INTO wi_actionable_alerts(alert_key,category,severity,title,reason,action_label,action_target,entity_type,entity_id,evidence,next_review_at)
              VALUES(:alert_key,:category,:severity,:title,:reason,:action_label,:action_target,:entity_type,:entity_id,CAST(:evidence AS jsonb),:next_review_at)
              ON CONFLICT(alert_key) DO UPDATE SET severity=excluded.severity,title=excluded.title,reason=excluded.reason,
                action_label=excluded.action_label,action_target=excluded.action_target,evidence=excluded.evidence,last_seen_at=now(),
                next_review_at=excluded.next_review_at,status=CASE WHEN wi_actionable_alerts.status='RESOLVED' THEN 'OPEN' ELSE wi_actionable_alerts.status END,resolved_at=NULL
            """), {**alert, "evidence": __import__("json").dumps(alert["evidence"], default=str)})
        rows = conn.execute(text("SELECT id,category,severity,status,title,reason,action_label,action_target,evidence,last_seen_at,next_review_at FROM wi_actionable_alerts WHERE status!='RESOLVED' ORDER BY CASE severity WHEN 'CRITICAL' THEN 1 WHEN 'IMPORTANT' THEN 2 ELSE 3 END,last_seen_at DESC")).mappings().all()
        return {"ok": True, "items": [dict(row) for row in rows], "generated": len(generated), "noise_control": "deduplication+next_review_at"}


@router.get("/executive/readiness")
def executive_dashboard_readiness(user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        _require(conn, user, "web_intelligence_view")
        sources = {
            "crm_sales": bool(conn.execute(text("SELECT EXISTS(SELECT 1 FROM leads)")).scalar()),
            "tracking": bool(conn.execute(text("SELECT EXISTS(SELECT 1 FROM wi_sessions)")).scalar()),
            "paid_media": bool(conn.execute(text("SELECT EXISTS(SELECT 1 FROM wi_ad_metrics_daily)")).scalar()),
            "seo": bool(conn.execute(text("SELECT EXISTS(SELECT 1 FROM wi_seo_query_daily)")).scalar()),
            "site_health": bool(conn.execute(text("SELECT EXISTS(SELECT 1 FROM wi_site_health_runs)")).scalar()),
        }
        readiness = executive_readiness(source_freshness=sources)
        metrics = None
        if readiness["ready"]:
            crm = conn.execute(text("""
              WITH lead_scope AS (
                SELECT l.*,upper(COALESCE(e.nombre,'')) state_name
                FROM leads l LEFT JOIN estados_lead e ON e.id_estado=l.id_estado
                WHERE l.created_at>=now()-interval '30 days' AND COALESCE(l.is_deleted,false)=false
              ), quote_scope AS (
                SELECT c.* FROM cotizaciones c WHERE c.created_at>=now()-interval '30 days'
              ), sales AS (
                SELECT DISTINCT ON(l.id_lead) l.id_lead,q.total
                FROM lead_scope l LEFT JOIN LATERAL (
                  SELECT c.total,c.accepted_at FROM cotizaciones c WHERE c.id_lead=l.id_lead
                  ORDER BY (c.id_cotizacion=l.id_cotizacion_vigente) DESC,c.id_cotizacion DESC LIMIT 1
                ) q ON true WHERE l.state_name LIKE '%CONFIRM%' OR q.accepted_at IS NOT NULL
              ) SELECT (SELECT COUNT(*) FROM lead_scope) leads,(SELECT COUNT(*) FROM quote_scope) quotes,
                (SELECT COUNT(*) FROM sales) sales,(SELECT COALESCE(SUM(total),0) FROM sales) revenue
            """)).mappings().one()
            ads = conn.execute(text("SELECT COALESCE(SUM(spend),0) spend FROM wi_ad_metrics_daily WHERE metric_date>=current_date-29")).mappings().one()
            attributed = conn.execute(text("SELECT COALESCE(SUM(sale_count),0) sales,COALESCE(SUM(revenue),0) revenue FROM wi_paid_media_attribution WHERE calculated_at>=now()-interval '30 days' AND attribution_method!='UNKNOWN'")).mappings().one()
            opportunities = int(conn.execute(text("SELECT COUNT(*) FROM wi_seo_opportunities WHERE score>=65")).scalar() or 0)
            risks = int(conn.execute(text("SELECT COUNT(*) FROM wi_actionable_alerts WHERE status='OPEN' AND severity IN ('IMPORTANT','CRITICAL')")).scalar() or 0)
            spend = float(ads["spend"] or 0) if sources["paid_media"] else None
            paid_sales = int(attributed["sales"] or 0) if sources["paid_media"] else None
            paid_revenue = float(attributed["revenue"] or 0) if sources["paid_media"] else None
            metrics = {**dict(crm), "paid_spend": spend, "paid_sales": paid_sales,
                       "cpa_crm": spend / paid_sales if spend is not None and paid_sales else None,
                       "roas_crm": paid_revenue / spend if spend and paid_revenue is not None else None,
                       "seo_opportunities": opportunities, "open_risks": risks, "period_days": 30}
    return {"ok": True, **readiness, "metrics": metrics, "policy": "El dashboard ejecutivo sólo se habilita con fuentes reales suficientes."}


@router.post("/ai/context")
def ai_context(payload: AIContextRequest, user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        _require(conn, user, "ai_use")
        services = {
            "sales": {"available": bool(conn.execute(text("SELECT EXISTS(SELECT 1 FROM leads)")).scalar())},
            "leads": {"count": int(conn.execute(text("SELECT COUNT(*) FROM leads")).scalar() or 0)},
            "quotes": {"count": int(conn.execute(text("SELECT COUNT(*) FROM cotizaciones")).scalar() or 0)},
            "paid_media": {"available": bool(conn.execute(text("SELECT EXISTS(SELECT 1 FROM wi_ad_metrics_daily)")).scalar())},
            "seo": {"available": bool(conn.execute(text("SELECT EXISTS(SELECT 1 FROM wi_seo_query_daily)")).scalar())},
            "site_health": {"available": bool(conn.execute(text("SELECT EXISTS(SELECT 1 FROM wi_site_health_runs)")).scalar())},
            "alerts": {"open": int(conn.execute(text("SELECT COUNT(*) FROM wi_actionable_alerts WHERE status='OPEN'")).scalar() or 0)},
            "integrations": {"attention": int(conn.execute(text("SELECT COUNT(*) FROM wi_integrations WHERE status IN ('WARNING','ERROR')")).scalar() or 0)},
        }
    return {"ok": True, **safe_ai_context(str(user.get("role") or user.get("rol") or ""), payload.sources, services)}
