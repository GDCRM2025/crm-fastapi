from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text


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
