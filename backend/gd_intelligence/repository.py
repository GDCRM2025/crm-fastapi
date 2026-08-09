from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from backend.gd_intelligence.utm import build_utm_url, campaign_identifier


INTEGRATION_PROVIDERS = (
    "GTM", "GA4", "SEARCH_CONSOLE", "CLARITY", "PAGESPEED", "CRUX", "TRACKING", "META"
)


def schema_ready(conn) -> bool:
    return bool(conn.execute(text("SELECT to_regclass('public.wi_sites') IS NOT NULL")).scalar())


def list_sites(conn, include_disabled: bool = False) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            """
            SELECT s.id, s.code, s.name, s.domain, s.timezone, s.currency,
                   s.enabled, s.config, s.created_at, s.updated_at,
                   COALESCE(
                     jsonb_object_agg(i.provider, i.status) FILTER (WHERE i.id IS NOT NULL),
                     '{}'::jsonb
                   ) AS integrations
            FROM public.wi_sites s
            LEFT JOIN public.wi_integrations i ON i.site_id=s.id
            WHERE (:include_disabled OR s.enabled)
            GROUP BY s.id
            ORDER BY s.code
            """
        ),
        {"include_disabled": include_disabled},
    ).mappings()
    return [dict(row) for row in rows]


def create_site(conn, payload: dict[str, Any], actor: str) -> dict[str, Any]:
    row = conn.execute(
        text(
            """
            INSERT INTO public.wi_sites
              (code,name,domain,timezone,currency,enabled,config,created_by,updated_by)
            VALUES
              (:code,:name,:domain,:timezone,:currency,:enabled,CAST(:config AS jsonb),:actor,:actor)
            RETURNING id,code,name,domain,timezone,currency,enabled,config,created_at,updated_at
            """
        ),
        {**payload, "config": json.dumps(payload.get("config") or {}), "actor": actor},
    ).mappings().one()
    site_id = int(row["id"])
    conn.execute(
        text(
            """
            INSERT INTO public.wi_integrations(site_id,provider,status,enabled)
            SELECT :site_id, provider, 'NOT_CONFIGURED', false
            FROM unnest(ARRAY['GTM','GA4','SEARCH_CONSOLE','PAGESPEED','CRUX','CLARITY','TRACKING','META']) provider
            ON CONFLICT(site_id,provider) DO NOTHING
            """
        ),
        {"site_id": site_id},
    )
    return dict(row)


def update_site(conn, site_id: int, changes: dict[str, Any], actor: str) -> dict[str, Any] | None:
    if not changes:
        return conn.execute(
            text("SELECT * FROM public.wi_sites WHERE id=:id"), {"id": site_id}
        ).mappings().one_or_none()
    allowed = {"name", "domain", "timezone", "currency", "enabled", "config"}
    values = {key: value for key, value in changes.items() if key in allowed}
    assignments = []
    params: dict[str, Any] = {"id": site_id, "actor": actor}
    for key, value in values.items():
        if key == "config":
            assignments.append("config=CAST(:config AS jsonb)")
            params[key] = json.dumps(value or {})
        else:
            assignments.append(f"{key}=:{key}")
            params[key] = value
    assignments.extend(("updated_by=:actor", "updated_at=now()"))
    return conn.execute(
        text(
            "UPDATE public.wi_sites SET "
            + ",".join(assignments)
            + " WHERE id=:id RETURNING id,code,name,domain,timezone,currency,enabled,config,created_at,updated_at"
        ),
        params,
    ).mappings().one_or_none()


def list_integrations(conn, site_id: int | None = None) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            """
            SELECT i.id,i.site_id,s.code AS site_code,s.name AS site_name,s.domain,
                   i.provider,i.status,i.enabled,i.external_id,i.last_verified_at,
                   i.last_sync_at,i.last_success_at,i.last_failure_at,i.last_error_code,i.last_error_safe,
                   i.updated_at,
                   CASE WHEN i.provider='GA4' THEN i.config->>'property_id' END AS property_id,
                   (c.id IS NOT NULL AND c.status='CONFIGURED') AS credential_configured,
                   c.credential_type,c.masked_suffix AS credential_suffix,c.updated_at AS credential_updated_at,
                   c.last_verified_at AS credential_last_verified_at
            FROM public.wi_integrations i
            JOIN public.wi_sites s ON s.id=i.site_id
            LEFT JOIN LATERAL (
              SELECT id,status,credential_type,masked_suffix,updated_at,last_verified_at
              FROM public.wi_integration_credentials
              WHERE integration_id=i.id
              ORDER BY (status='CONFIGURED') DESC,updated_at DESC LIMIT 1
            ) c ON true
            WHERE (CAST(:site_id AS bigint) IS NULL OR i.site_id=CAST(:site_id AS bigint))
              AND i.provider=ANY(:providers)
            ORDER BY s.code,i.provider
            """
        ),
        {"site_id": site_id, "providers": list(INTEGRATION_PROVIDERS)},
    ).mappings()
    return [dict(row) for row in rows]


def update_public_integration(
    conn,
    site_id: int,
    provider: str,
    external_id: str | None,
    enabled: bool,
) -> dict[str, Any] | None:
    status = "WARNING" if enabled and external_id else "NOT_CONFIGURED" if enabled else "DISABLED"
    row = conn.execute(
        text(
            """
            INSERT INTO public.wi_integrations(site_id,provider,status,enabled,external_id,updated_at)
            SELECT id,:provider,:status,:enabled,:external_id,now()
            FROM public.wi_sites WHERE id=:site_id
            ON CONFLICT(site_id,provider) DO UPDATE SET
              status=excluded.status,enabled=excluded.enabled,external_id=excluded.external_id,
              last_error_code=NULL,last_error_safe=NULL,updated_at=now()
            RETURNING id,site_id,provider,status,enabled,external_id,last_verified_at,updated_at
            """
        ),
        {
            "site_id": site_id,
            "provider": provider,
            "status": status,
            "enabled": enabled,
            "external_id": external_id,
        },
    ).mappings().one_or_none()
    return dict(row) if row else None


def store_integration_discovery(conn, site_id: int, item: dict[str, Any]) -> dict[str, Any]:
    row = conn.execute(
        text(
            """
            INSERT INTO public.wi_integrations(
              site_id,provider,status,enabled,external_id,last_verified_at,last_error_safe,updated_at
            ) VALUES (
              :site_id,:provider,:status,:enabled,:external_id,:last_verified_at,:last_error_safe,now()
            )
            ON CONFLICT(site_id,provider) DO UPDATE SET
              status=excluded.status,enabled=excluded.enabled,external_id=excluded.external_id,
              last_verified_at=excluded.last_verified_at,last_error_safe=excluded.last_error_safe,
              last_error_code=NULL,updated_at=now()
            RETURNING id,site_id,provider,status,enabled,external_id,last_verified_at,last_error_safe,updated_at
            """
        ),
        {"site_id": site_id, **item},
    ).mappings().one()
    return dict(row)


def create_utm_link(conn, payload: dict[str, Any], actor: str) -> dict[str, Any] | None:
    site = conn.execute(
        text("SELECT id,code FROM public.wi_sites WHERE id=:id AND enabled"),
        {"id": payload["site_id"]},
    ).mappings().one_or_none()
    if site is None:
        return None
    sequence = int(conn.execute(text("SELECT nextval('public.wi_campaign_identifier_seq')")).scalar_one())
    identifier = campaign_identifier(str(site["code"]), sequence)
    generated_url = build_utm_url(
        payload["url"],
        utm_source=payload["utm_source"],
        utm_medium=payload["utm_medium"],
        utm_campaign=payload["utm_campaign"],
        utm_term=payload.get("utm_term"),
        utm_content=payload.get("utm_content"),
    )
    campaign_id = payload.get("campaign_id")
    if campaign_id:
        campaign_id = conn.execute(
            text("SELECT id FROM public.wi_marketing_campaigns WHERE id=:id AND site_id=:site_id"),
            {"id": campaign_id, "site_id": payload["site_id"]},
        ).scalar()
        if not campaign_id:
            raise ValueError("La campaña no pertenece al sitio seleccionado")
    else:
        campaign_id = conn.execute(text("""
          INSERT INTO public.wi_marketing_campaigns(site_id,name,created_by)
          VALUES (:site_id,:name,:actor)
          ON CONFLICT(site_id,name) DO UPDATE SET updated_at=now()
          RETURNING id
        """), {"site_id": payload["site_id"], "name": payload["utm_campaign"], "actor": actor}).scalar_one()
    row = conn.execute(
        text(
            """
            INSERT INTO public.wi_utm_links(
              identifier,site_id,campaign_id,base_url,generated_url,utm_source,utm_medium,
              utm_campaign,utm_term,utm_content,created_by
            ) VALUES (
              :identifier,:site_id,:campaign_id,:base_url,:generated_url,:utm_source,:utm_medium,
              :utm_campaign,:utm_term,:utm_content,:actor
            )
            RETURNING id,identifier,site_id,campaign_id,base_url,generated_url,utm_source,utm_medium,
                      utm_campaign,utm_term,utm_content,created_by,created_at
            """
        ),
        {
            "identifier": identifier,
            "site_id": payload["site_id"],
            "campaign_id": campaign_id,
            "base_url": payload["url"],
            "generated_url": generated_url,
            "utm_source": payload["utm_source"],
            "utm_medium": payload["utm_medium"],
            "utm_campaign": payload["utm_campaign"],
            "utm_term": payload.get("utm_term"),
            "utm_content": payload.get("utm_content"),
            "actor": actor,
        },
    ).mappings().one()
    return dict(row)


def list_utm_links(conn, site_id: int | None, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            """
            SELECT u.id,u.identifier,u.site_id,u.campaign_id,s.code AS site_code,s.name AS site_name,
                   u.base_url,u.generated_url,u.utm_source,u.utm_medium,u.utm_campaign,
                   u.utm_term,u.utm_content,u.created_by,u.created_at,
                   COUNT(DISTINCT ws.session_id) AS visits,
                   COUNT(DISTINCT la.lead_id) AS leads,
                   COUNT(DISTINCT c.id_cotizacion) AS quotes,
                   CASE WHEN COUNT(DISTINCT ws.session_id)=0 THEN 0
                        ELSE ROUND(COUNT(DISTINCT la.lead_id)::numeric / COUNT(DISTINCT ws.session_id), 4) END AS conversion,
                   COALESCE(SUM(c.total),0) AS revenue
            FROM public.wi_utm_links u
            JOIN public.wi_sites s ON s.id=u.site_id
            LEFT JOIN public.wi_sessions ws ON ws.site_id=u.site_id AND ws.utm_campaign=u.utm_campaign
              AND COALESCE(ws.utm_source,'')=COALESCE(u.utm_source,'') AND COALESCE(ws.utm_medium,'')=COALESCE(u.utm_medium,'')
            LEFT JOIN public.wi_lead_attribution la ON la.session_id=ws.session_id
            LEFT JOIN public.cotizaciones c ON c.id_lead=la.lead_id
            WHERE (CAST(:site_id AS bigint) IS NULL OR u.site_id=CAST(:site_id AS bigint))
            GROUP BY u.id,s.code,s.name
            ORDER BY u.created_at DESC
            LIMIT :limit
            """
        ),
        {"site_id": site_id, "limit": min(max(limit, 1), 500)},
    ).mappings()
    return [dict(row) for row in rows]
