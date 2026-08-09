from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.core.activity_log import log_activity
from backend.core.database import engine
from backend.gd_intelligence.permissions import (
    PERMISSIONS,
    has_permission,
    resolve_permissions,
    role_key,
    user_id,
)
from backend.gd_intelligence.rbac_admin import ROLE_KEYS, role_matrix, save_role_permissions
from backend.gd_intelligence.integration_inventory import scan_public_integrations
from backend.gd_intelligence.integration_center import actionable_integration, backend_capabilities, list_google_properties
from backend.gd_intelligence.tracking import record_event, upsert_session
from backend.gd_intelligence.repository import (
    INTEGRATION_PROVIDERS,
    create_site,
    create_utm_link,
    list_integrations,
    list_sites,
    list_utm_links,
    schema_ready,
    store_integration_discovery,
    update_public_integration,
    update_site,
)
from backend.gd_intelligence.schemas import (
    IntegrationPublicUpdate,
    RolePermissionsUpdate,
    SiteCreate,
    SiteUpdate,
    UTMBuildRequest,
)
from backend.gd_intelligence.site_health import history as site_health_history
from backend.gd_intelligence.site_health import latest_results, scan_site, store_result
from backend.routers.auth import get_current_user


router = APIRouter(prefix="/api/gd-intelligence", tags=["gd-intelligence"])


def _actor(user: dict) -> str:
    return str(
        user.get("username")
        or user.get("email")
        or user.get("name")
        or user.get("id_usuario")
        or "unknown"
    )[:200]


def _require_schema(conn) -> None:
    if not schema_ready(conn):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "GD_INTELLIGENCE_MIGRATION_REQUIRED"},
        )


def _require_site_health_schema(conn) -> None:
    ready = conn.execute(
        text("SELECT to_regclass('public.wi_site_health_runs') IS NOT NULL")
    ).scalar()
    if not ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "SITE_HEALTH_MIGRATION_REQUIRED"},
        )


def _require_integration_inventory_schema(conn) -> None:
    ready = conn.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='wi_integrations' "
            "AND column_name='last_verified_at')"
        )
    ).scalar()
    if not ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "INTEGRATION_INVENTORY_MIGRATION_REQUIRED"},
        )


def _require(conn, user: dict, permission: str) -> None:
    if not has_permission(conn, user, permission):
        raise HTTPException(status_code=403, detail="Sin permiso para esta función.")


@router.get("/overview")
def overview(user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        _require(conn, user, "web_intelligence_view")
        ready = schema_ready(conn)
        sites = list_sites(conn) if ready else []
        return {
            "ok": True,
            "module": "GD Intelligence",
            "schema_ready": ready,
            "sites": sites,
            "site_count": len(sites),
        }


@router.get("/permissions/me")
def my_permissions(user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        granted = resolve_permissions(conn, user)
    return {
        "ok": True,
        "items": {permission: permission in granted for permission in PERMISSIONS},
    }


@router.get("/permissions/roles")
def permissions_roles(user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        _require(conn, user, "system_users_manage")
        _require_schema(conn)
        return {"ok": True, "permissions": list(PERMISSIONS), "items": role_matrix(conn)}


@router.put("/permissions/roles/{target_role}")
def permissions_role_update(
    target_role: str,
    payload: RolePermissionsUpdate,
    request: Request,
    user: dict = Depends(get_current_user),
):
    normalized = str(target_role or "").strip().upper().replace(" ", "_")
    if normalized not in ROLE_KEYS:
        raise HTTPException(status_code=404, detail="Rol no encontrado.")
    with engine.begin() as conn:
        _require(conn, user, "system_users_manage")
        if normalized in {"ADMIN", "SUPER_ADMIN"}:
            _require(conn, user, "system_super_admin")
        try:
            result = save_role_permissions(conn, normalized, payload.permissions, _actor(user))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        log_activity(
            conn,
            username=_actor(user),
            user_id=user_id(user),
            role=role_key(user),
            action="gd_intelligence.role_permissions.update",
            entity_type="gd_role",
            meta={"target_role": normalized, **result},
            request=request,
            status_code=200,
        )
    return {"ok": True, "role": normalized, **result}


@router.get("/web/sites")
def sites_list(
    include_disabled: bool = Query(False),
    user: dict = Depends(get_current_user),
):
    with engine.connect() as conn:
        _require(conn, user, "web_intelligence_view")
        _require_schema(conn)
        return {"ok": True, "items": list_sites(conn, include_disabled)}


@router.post("/web/sites", status_code=201)
def sites_create(payload: SiteCreate, user: dict = Depends(get_current_user)):
    try:
        with engine.begin() as conn:
            _require(conn, user, "web_intelligence_configure")
            _require_schema(conn)
            item = create_site(conn, payload.model_dump(), _actor(user))
        return {"ok": True, "item": item}
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Código o dominio ya registrado.") from exc


@router.patch("/web/sites/{site_id}")
def sites_update(site_id: int, payload: SiteUpdate, user: dict = Depends(get_current_user)):
    with engine.begin() as conn:
        _require(conn, user, "web_intelligence_configure")
        _require_schema(conn)
        item = update_site(
            conn,
            site_id,
            payload.model_dump(exclude_unset=True),
            _actor(user),
        )
        if item is None:
            raise HTTPException(status_code=404, detail="Sitio no encontrado.")
    return {"ok": True, "item": dict(item)}


@router.get("/web/integrations")
def integrations_list(
    site_id: int | None = Query(default=None, gt=0),
    user: dict = Depends(get_current_user),
):
    with engine.connect() as conn:
        _require(conn, user, "web_intelligence_view")
        _require_schema(conn)
        _require_integration_inventory_schema(conn)
        capabilities = backend_capabilities()
        items = [actionable_integration(item, capabilities) for item in list_integrations(conn, site_id)]
        return {"ok": True, "items": items, "backend": capabilities}


@router.get("/web/integrations/google/properties")
def google_properties(user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        _require(conn, user, "web_intelligence_configure")
    try:
        return {"ok": True, **list_google_properties()}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail={"code": str(exc)}) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail={"code": "GOOGLE_CONNECTION_FAILED"}) from exc


@router.put("/web/integrations/google/selection")
def google_property_selection(payload: dict = Body(...), request: Request = None, user: dict = Depends(get_current_user)):
    import re
    site_id = int(payload.get("site_id") or 0)
    ga4_id = str(payload.get("ga4_property_id") or "").strip()
    sc_property = str(payload.get("search_console_property") or "").strip()
    if ga4_id and not re.fullmatch(r"[0-9]{5,}", ga4_id):
        raise HTTPException(422, "GA4 Property ID debe ser numérico")
    if sc_property and not re.fullmatch(r"sc-domain:[A-Za-z0-9.-]+|https://[^\s]+", sc_property):
        raise HTTPException(422, "Propiedad Search Console inválida")
    if not ga4_id and not sc_property:
        raise HTTPException(422, "Selecciona al menos una propiedad")
    with engine.begin() as conn:
        _require(conn, user, "web_intelligence_configure")
        if not conn.execute(text("SELECT 1 FROM public.wi_sites WHERE id=:id"), {"id": site_id}).scalar():
            raise HTTPException(404, "Sitio no encontrado")
        if ga4_id:
            conn.execute(text("""
              UPDATE public.wi_integrations SET
                config=jsonb_set(COALESCE(config,'{}'::jsonb),'{property_id}',to_jsonb(CAST(:value AS text)),true),
                enabled=true,status='WARNING',updated_at=now()
              WHERE site_id=:site_id AND provider='GA4'
            """), {"site_id": site_id, "value": ga4_id})
        if sc_property:
            conn.execute(text("""
              UPDATE public.wi_integrations SET external_id=:value,enabled=true,status='WARNING',updated_at=now()
              WHERE site_id=:site_id AND provider='SEARCH_CONSOLE'
            """), {"site_id": site_id, "value": sc_property})
        log_activity(conn, username=_actor(user), user_id=user_id(user), role=role_key(user),
                     action="gd_intelligence.google_properties.select", entity_type="wi_integration",
                     meta={"site_id": site_id, "ga4_selected": bool(ga4_id), "search_console_selected": bool(sc_property)},
                     request=request, status_code=200)
    return {"ok": True}


@router.post("/tracking/session", status_code=201)
def tracking_session(payload: dict = Body(...), request: Request = None):
    site_code = str(payload.get("site_code") or "").strip().upper()
    with engine.begin() as conn:
        site_id = conn.execute(text("SELECT id FROM public.wi_sites WHERE code=:code AND enabled"), {"code": site_code}).scalar()
        if not site_id:
            raise HTTPException(status_code=404, detail="Sitio no encontrado")
        try:
            item = upsert_session(conn, site_id=int(site_id), payload=payload, user_agent=request.headers.get("user-agent") if request else None)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "item": item}


@router.post("/tracking/event", status_code=201)
def tracking_event(payload: dict = Body(...)):
    site_code = str(payload.get("site_code") or "").strip().upper()
    with engine.begin() as conn:
        site_id = conn.execute(text("SELECT id FROM public.wi_sites WHERE code=:code AND enabled"), {"code": site_code}).scalar()
        if not site_id:
            raise HTTPException(status_code=404, detail="Sitio no encontrado")
        try:
            item = record_event(conn, site_id=int(site_id), payload=payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "item": item}


@router.put("/web/sites/{site_id}/integrations/{provider}")
def integration_public_update(
    site_id: int,
    provider: str,
    payload: IntegrationPublicUpdate,
    request: Request,
    user: dict = Depends(get_current_user),
):
    normalized = str(provider or "").strip().upper()
    if normalized not in INTEGRATION_PROVIDERS:
        raise HTTPException(status_code=404, detail="Integración no soportada.")
    public_id = payload.external_id
    patterns = {
        "GTM": r"^GTM-[A-Z0-9]+$",
        "GA4": r"^(G-[A-Z0-9]+|properties/[0-9]+|[0-9]{5,})$",
        "SEARCH_CONSOLE": r"^(sc-domain:[A-Za-z0-9.-]+|https://[^\s]+)$",
        "CLARITY": r"^[A-Za-z0-9]+$",
        "PAGESPEED": r"^https://[^\s]+$",
        "CRUX": r"^https://[^\s]+$",
        "TRACKING": r"^(gd-tracker\.js|https://[^\s]+)$",
        "META": r"^[0-9]{8,20}$",
    }
    if public_id:
        import re
        if not re.fullmatch(patterns[normalized], public_id, flags=re.IGNORECASE):
            raise HTTPException(status_code=422, detail="Formato de ID público inválido para el proveedor.")
    with engine.begin() as conn:
        _require(conn, user, "web_intelligence_configure")
        _require_integration_inventory_schema(conn)
        item = update_public_integration(conn, site_id, normalized, public_id, payload.enabled)
        if item is None:
            raise HTTPException(status_code=404, detail="Sitio no encontrado.")
        log_activity(
            conn,
            username=_actor(user),
            user_id=user_id(user),
            role=role_key(user),
            action="gd_intelligence.integration_public.update",
            entity_type="wi_integration",
            meta={"site_id": site_id, "provider": normalized, "enabled": payload.enabled},
            request=request,
            status_code=200,
        )
    return {"ok": True, "item": item}


@router.post("/web/integrations/discover")
def integrations_discover(
    request: Request,
    site_id: int | None = Query(default=None, gt=0),
    user: dict = Depends(get_current_user),
):
    with engine.connect() as conn:
        _require(conn, user, "web_intelligence_configure")
        _require_integration_inventory_schema(conn)
        sites = conn.execute(
            text(
                "SELECT id,code,name,domain FROM public.wi_sites "
                "WHERE enabled AND (CAST(:site_id AS bigint) IS NULL OR id=CAST(:site_id AS bigint)) "
                "ORDER BY code"
            ),
            {"site_id": site_id},
        ).mappings().all()
    if not sites:
        raise HTTPException(status_code=404, detail="Sitio habilitado no encontrado.")
    items = []
    for site in sites:
        discovered = scan_public_integrations(str(site["domain"]))
        with engine.begin() as conn:
            for integration in discovered:
                stored = store_integration_discovery(conn, int(site["id"]), integration)
                items.append({**stored, "site_code": site["code"], "domain": site["domain"]})
    with engine.begin() as conn:
        log_activity(
            conn,
            username=_actor(user),
            user_id=user_id(user),
            role=role_key(user),
            action="gd_intelligence.integrations.discover",
            entity_type="wi_integration",
            meta={"site_id": site_id, "sites": len(sites), "items": len(items)},
            request=request,
            status_code=200,
        )
    return {"ok": True, "items": items}


@router.get("/campaigns/utm")
def utm_list(
    site_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=100, ge=1, le=500),
    user: dict = Depends(get_current_user),
):
    with engine.connect() as conn:
        _require(conn, user, "web_intelligence_campaigns")
        _require_schema(conn)
        return {"ok": True, "items": list_utm_links(conn, site_id, limit)}


@router.post("/campaigns/utm", status_code=201)
def utm_create(payload: UTMBuildRequest, user: dict = Depends(get_current_user)):
    try:
        with engine.begin() as conn:
            _require(conn, user, "web_intelligence_campaigns")
            _require_schema(conn)
            item = create_utm_link(conn, payload.model_dump(), _actor(user))
            if item is None:
                raise HTTPException(status_code=404, detail="Sitio no encontrado o deshabilitado.")
        return {"ok": True, "item": item}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/web/site-health/latest")
def site_health_latest(user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        _require(conn, user, "web_intelligence_view")
        _require_schema(conn)
        _require_site_health_schema(conn)
        return {"ok": True, "items": latest_results(conn)}


@router.get("/web/site-health/history")
def site_health_history_list(
    site_id: int = Query(gt=0),
    limit: int = Query(default=100, ge=1, le=500),
    user: dict = Depends(get_current_user),
):
    with engine.connect() as conn:
        _require(conn, user, "web_intelligence_view")
        _require_site_health_schema(conn)
        return {"ok": True, "items": site_health_history(conn, site_id, limit)}


@router.post("/web/site-health/run")
def site_health_run(
    request: Request,
    site_id: int | None = Query(default=None, gt=0),
    user: dict = Depends(get_current_user),
):
    with engine.connect() as conn:
        _require(conn, user, "web_intelligence_performance")
        _require_schema(conn)
        _require_site_health_schema(conn)
        sites = conn.execute(
            text(
                """
                SELECT id,code,name,domain
                FROM public.wi_sites
                WHERE enabled AND (CAST(:site_id AS bigint) IS NULL OR id=CAST(:site_id AS bigint))
                ORDER BY code
                """
            ),
            {"site_id": site_id},
        ).mappings().all()
    if not sites:
        raise HTTPException(status_code=404, detail="Sitio habilitado no encontrado.")

    items = []
    for site in sites:
        scanned = scan_site(str(site["domain"]))
        with engine.begin() as conn:
            item = store_result(conn, int(site["id"]), scanned, _actor(user))
        items.append({**item, "code": site["code"], "name": site["name"], "domain": site["domain"]})

    with engine.begin() as conn:
        log_activity(
            conn,
            username=_actor(user),
            user_id=user_id(user),
            role=role_key(user),
            action="gd_intelligence.site_health.run",
            entity_type="wi_site_health",
            meta={
                "site_id": site_id,
                "count": len(items),
                "statuses": {str(item["code"]): str(item["status"]) for item in items},
            },
            request=request,
            status_code=200,
        )
    return {"ok": True, "items": items}
