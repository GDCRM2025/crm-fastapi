from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from backend.gd_intelligence.utm import build_utm_url, campaign_identifier


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
            FROM unnest(ARRAY['GA4','SEARCH_CONSOLE','PAGESPEED','CRUX','CLARITY','TRACKING']) provider
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
    row = conn.execute(
        text(
            """
            INSERT INTO public.wi_utm_links(
              identifier,site_id,base_url,generated_url,utm_source,utm_medium,
              utm_campaign,utm_term,utm_content,created_by
            ) VALUES (
              :identifier,:site_id,:base_url,:generated_url,:utm_source,:utm_medium,
              :utm_campaign,:utm_term,:utm_content,:actor
            )
            RETURNING id,identifier,site_id,base_url,generated_url,utm_source,utm_medium,
                      utm_campaign,utm_term,utm_content,created_by,created_at
            """
        ),
        {
            "identifier": identifier,
            "site_id": payload["site_id"],
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
            SELECT u.id,u.identifier,u.site_id,s.code AS site_code,s.name AS site_name,
                   u.base_url,u.generated_url,u.utm_source,u.utm_medium,u.utm_campaign,
                   u.utm_term,u.utm_content,u.created_by,u.created_at
            FROM public.wi_utm_links u
            JOIN public.wi_sites s ON s.id=u.site_id
            WHERE (CAST(:site_id AS bigint) IS NULL OR u.site_id=CAST(:site_id AS bigint))
            ORDER BY u.created_at DESC
            LIMIT :limit
            """
        ),
        {"site_id": site_id, "limit": min(max(limit, 1), 500)},
    ).mappings()
    return [dict(row) for row in rows]
