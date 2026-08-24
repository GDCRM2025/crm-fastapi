from __future__ import annotations

import os

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
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
from backend.gd_intelligence.integration_center import actionable_integration, backend_capabilities
from backend.gd_intelligence.google_bi import (
    GoogleBIConnection,
    GoogleBIError,
    persist_ga4_rows,
    persist_search_console_rows,
    seven_day_window,
)
from backend.gd_intelligence.integration_state import overview_summary, site_capability_state
from backend.gd_intelligence.credential_vault import CredentialValidationError, CredentialVault, VaultUnavailable, vault_bootstrap_status
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
    CredentialWrite,
    RolePermissionsUpdate,
    SiteCreate,
    SiteUpdate,
    UTMBuildRequest,
)
from backend.gd_intelligence.site_health import history as site_health_history
from backend.gd_intelligence.site_health import latest_results, scan_site, store_result
from backend.gd_intelligence.pagespeed import PageSpeedRequestError, fetch_pagespeed, performance_rows
from backend.gd_intelligence.crux import CrUXNoData, CrUXRequestError, fetch_crux
from backend.routers.auth import get_current_user


router = APIRouter(
    prefix="/api/gd-intelligence",
    tags=["gd-intelligence"],
)

# Google redirects cannot attach the CRM bearer header. This router is public
# only for the one-time callback; high-entropy state binds it to the SUPERADMIN
# who initiated the protected /start request.
google_oauth_callback_router = APIRouter(prefix="/api/gd-intelligence", tags=["gd-intelligence"])


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


def _require_credential_vault_schema(conn) -> None:
    if not conn.execute(text("SELECT to_regclass('public.wi_integration_credentials') IS NOT NULL")).scalar():
        raise HTTPException(status_code=503, detail="La administración segura de integraciones aún no está disponible.")


def _require_google_bi_schema(conn) -> None:
    ready = conn.execute(
        text(
            "SELECT to_regclass('public.wi_google_bi_connections') IS NOT NULL "
            "AND to_regclass('public.wi_google_bi_oauth_states') IS NOT NULL"
        )
    ).scalar()
    if not ready:
        raise HTTPException(status_code=503, detail={"code": "GOOGLE_BI_MIGRATION_REQUIRED"})


def _require(conn, user: dict, permission: str) -> None:
    if not has_permission(conn, user, permission):
        raise HTTPException(status_code=403, detail="Sin permiso para esta función.")


def _tracking_events_by_site(conn) -> dict[int, int]:
    if not conn.execute(text("SELECT to_regclass('public.wi_events') IS NOT NULL")).scalar():
        return {}
    return {int(row.site_id): int(row.total) for row in conn.execute(text("SELECT site_id,count(*) AS total FROM wi_events GROUP BY site_id"))}


def _tracking_metrics(conn) -> dict[str, int | str]:
    def count(table: str, where: str = "") -> int:
        if not conn.execute(text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f"public.{table}"}).scalar():
            return 0
        return int(conn.execute(text(f"SELECT count(*) FROM {table} {where}")).scalar())
    sessions = count("wi_sessions")
    events = count("wi_events")
    return {
        "status": "REAL" if sessions or events else "NO_DATA",
        "sessions": sessions,
        "events": events,
        "click_whatsapp": count("wi_events", "WHERE event_type='click_whatsapp'"),
        "attribution_records": count("wi_lead_attribution"),
    }


def _pagespeed_key(conn, integration_id: int) -> str | None:
    global_key = str(os.getenv("PAGESPEED_API_KEY") or "").strip()
    if global_key:
        return global_key
    try:
        return CredentialVault().use_internal(conn, integration_id=integration_id, credential_type="API_KEY")
    except (KeyError, VaultUnavailable):
        return None


def _crux_key(conn, integration_id: int | None) -> str | None:
    global_key = str(os.getenv("CRUX_API_KEY") or "").strip()
    if global_key:
        return global_key
    if not integration_id:
        return None
    try:
        return CredentialVault().use_internal(conn, integration_id=int(integration_id), credential_type="API_KEY")
    except (KeyError, VaultUnavailable):
        return None


def _ad_platform_connected(conn, platform: str) -> bool:
    if not conn.execute(text("SELECT to_regclass('public.wi_ad_accounts') IS NOT NULL")).scalar():
        return False
    return bool(conn.execute(text("SELECT 1 FROM wi_ad_accounts WHERE platform=:platform AND enabled LIMIT 1"), {"platform": platform}).scalar())


def _integration_state_rows(conn, site_id: int | None = None) -> tuple[list[dict], dict]:
    capabilities = backend_capabilities(conn)
    tracking = _tracking_events_by_site(conn)
    items = [actionable_integration(item, capabilities, tracking_events=tracking.get(int(item["site_id"]), 0)) for item in list_integrations(conn, site_id)]
    return items, capabilities


@router.get("/overview")
def overview(user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        _require(conn, user, "web_intelligence_view")
        ready = schema_ready(conn)
        sites = list_sites(conn) if ready else []
        items, capabilities = _integration_state_rows(conn) if ready else ([], backend_capabilities())
        health_items = latest_results(conn) if ready else []
        health_by_site = {int(row["site_id"]): row for row in health_items}
        google_ads_connected = _ad_platform_connected(conn, "GOOGLE_ADS") if ready else False
        meta_ads_connected = _ad_platform_connected(conn, "META_ADS") if ready else False
        site_states = [site_capability_state(site, items, health_by_site.get(int(site["id"])), google_ads_connected=google_ads_connected, meta_ads_connected=meta_ads_connected) for site in sites]
        return {
            "ok": True,
            "module": "GD Intelligence",
            "schema_ready": ready,
            "sites": sites,
            "site_count": len(sites),
            "summary": overview_summary(items, sites, site_states),
            "site_capabilities": site_states,
            "backend": capabilities,
            "tracking_data": _tracking_metrics(conn) if ready else {"status": "NO_DATA", "sessions": 0, "events": 0, "click_whatsapp": 0, "attribution_records": 0},
        }


@router.get("/bootstrap/status")
def bootstrap_status(user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        _require(conn, user, "system_super_admin")
        try:
            vault = vault_bootstrap_status(conn)
        except VaultUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    from backend.core.bootstrap_credentials import bootstrap_source
    return {
        "ok": True,
        "database_source": bootstrap_source("database_url"),
        "vault": vault,
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
        _require_credential_vault_schema(conn)
        items, capabilities = _integration_state_rows(conn, site_id)
        return {"ok": True, "items": items, "backend": capabilities}


def _credential_context(conn, integration_id: int) -> dict:
    item = conn.execute(text("""
      SELECT i.id,i.provider,s.domain FROM wi_integrations i
      JOIN wi_sites s ON s.id=i.site_id WHERE i.id=:id
    """), {"id": integration_id}).mappings().one_or_none()
    if not item:
        raise HTTPException(404, "Integración no encontrada.")
    return dict(item)


@router.put("/web/integrations/{integration_id}/credential")
async def integration_credential_write(integration_id: int, request: Request, user: dict = Depends(get_current_user)):
    try:
        raw = await request.json()
        payload = CredentialWrite.model_validate(raw if isinstance(raw, dict) else {})
    except Exception:
        raise HTTPException(422, "Revisa la credencial y su confirmación.")
    actor = _actor(user)
    credential_type = payload.credential_type
    try:
        with engine.begin() as conn:
            _require(conn, user, "web_intelligence_configure")
            _require_credential_vault_schema(conn)
            context = _credential_context(conn, integration_id)
            if context["provider"] not in {"PAGESPEED", "CRUX"}:
                raise HTTPException(422, "Esta integración debe conectarse mediante autorización del proveedor.")
            item = CredentialVault().store(conn, integration_id=integration_id, credential_type=credential_type,
                                           secret=payload.secret, actor=actor, domain=context["domain"])
            log_activity(conn, username=actor, user_id=user_id(user), role=role_key(user),
                         action="CREDENTIAL_CONFIGURED_OR_REPLACED", entity_type="wi_integration", entity_id=integration_id,
                         meta={"provider": context["provider"], "credential_type": credential_type, "result": "SUCCESS"}, request=request, status_code=200)
        return {"ok": True, "credential": item, "message": "Credencial verificada y guardada de forma segura."}
    except CredentialValidationError as exc:
        with engine.begin() as conn:
            CredentialVault().record_failure(conn, integration_id=integration_id, credential_type=credential_type, actor=actor, error_code=exc.code, message=str(exc))
            log_activity(conn, username=actor, user_id=user_id(user), role=role_key(user),
                         action="CONNECTION_FAILED", entity_type="wi_integration", entity_id=integration_id,
                         meta={"credential_type": credential_type, "error_code": exc.code}, request=request, status_code=422)
        raise HTTPException(422, str(exc))
    except VaultUnavailable as exc:
        raise HTTPException(503, str(exc))


@router.post("/web/integrations/{integration_id}/verify")
def integration_credential_verify(integration_id: int, request: Request, credential_type: str = Body("API_KEY", embed=True), user: dict = Depends(get_current_user)):
    actor = _actor(user)
    try:
        with engine.begin() as conn:
            _require(conn, user, "web_intelligence_configure")
            _require_credential_vault_schema(conn)
            context = _credential_context(conn, integration_id)
            item = CredentialVault().verify(conn, integration_id=integration_id, credential_type=credential_type, actor=actor, domain=context["domain"])
            log_activity(conn, username=actor, user_id=user_id(user), role=role_key(user), action="CONNECTION_VERIFIED",
                         entity_type="wi_integration", entity_id=integration_id, meta={"credential_type": credential_type, "result": "SUCCESS"}, request=request, status_code=200)
        return {"ok": True, "item": item, "message": "Conexión correcta."}
    except KeyError:
        raise HTTPException(409, "Configura una credencial antes de verificar.")
    except CredentialValidationError as exc:
        with engine.begin() as conn:
            CredentialVault().record_failure(conn, integration_id=integration_id, credential_type=credential_type, actor=actor, error_code=exc.code, message=str(exc))
            log_activity(conn, username=actor, user_id=user_id(user), role=role_key(user), action="CONNECTION_FAILED",
                         entity_type="wi_integration", entity_id=integration_id,
                         meta={"credential_type": credential_type, "error_code": exc.code}, request=request, status_code=422)
        raise HTTPException(422, str(exc))
    except VaultUnavailable as exc:
        raise HTTPException(503, str(exc))


@router.delete("/web/integrations/{integration_id}/credential")
def integration_credential_revoke(integration_id: int, request: Request, credential_type: str = Query("API_KEY"), user: dict = Depends(get_current_user)):
    actor = _actor(user)
    with engine.begin() as conn:
        _require(conn, user, "system_integrations_manage")
        _require_credential_vault_schema(conn)
        context = _credential_context(conn, integration_id)
        try:
            item = CredentialVault().revoke(conn, integration_id=integration_id, credential_type=credential_type, actor=actor)
        except KeyError:
            raise HTTPException(409, "Esta integración no tiene una credencial activa.")
        log_activity(conn, username=actor, user_id=user_id(user), role=role_key(user), action="INTEGRATION_DISCONNECTED",
                     entity_type="wi_integration", entity_id=integration_id,
                     meta={"provider": context["provider"], "credential_type": credential_type, "result": "SUCCESS"}, request=request, status_code=200)
    return {"ok": True, "item": item, "message": "Integración desconectada. Los datos históricos se conservan."}


def _google_http_error(exc: GoogleBIError) -> HTTPException:
    code = 400 if exc.code in {"OAUTH_STATE_INVALID", "OAUTH_CALLBACK_INVALID"} else 409
    if exc.code.endswith("FAILED") or exc.code.endswith("UNAVAILABLE") or exc.code.endswith("REJECTED"):
        code = 502
    return HTTPException(code, {"code": exc.code, "message": str(exc)})


def _oauth_result_page(ok: bool) -> HTMLResponse:
    title = "Google BI conectado" if ok else "No fue posible conectar Google BI"
    message = "Ya puedes volver a GD Intelligence." if ok else "Vuelve a GD Intelligence e intenta reconectar."
    event = "google-bi-connected" if ok else "google-bi-error"
    html = (
        "<!doctype html><html lang='es'><meta charset='utf-8'><title>Google BI</title>"
        "<body style='font:16px system-ui;background:#071827;color:#edf6fc;padding:32px'>"
        f"<h1>{title}</h1><p>{message}</p>"
        f"<script>if(window.opener){{window.opener.postMessage({{type:'{event}'}},window.location.origin);window.close();}}</script>"
        "</body></html>"
    )
    return HTMLResponse(html, status_code=200 if ok else 400, headers={"Cache-Control": "no-store"})


@router.get("/web/integrations/google/oauth/status")
def google_oauth_status(user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        _require(conn, user, "web_intelligence_view")
        _require_google_bi_schema(conn)
        return {"ok": True, **GoogleBIConnection().public_status(conn)}


@router.get("/web/integrations/google/oauth/start")
def google_oauth_start(user: dict = Depends(get_current_user)):
    actor_id = user_id(user)
    if not str(actor_id or "").isdigit():
        raise HTTPException(401, "La sesión no identifica al usuario.")
    try:
        with engine.begin() as conn:
            _require(conn, user, "web_intelligence_configure")
            _require_google_bi_schema(conn)
            result = GoogleBIConnection().start(conn, actor_user_id=int(actor_id))
        return {"ok": True, **result}
    except GoogleBIError as exc:
        raise _google_http_error(exc) from exc


@google_oauth_callback_router.get("/web/integrations/google/oauth/callback", response_class=HTMLResponse)
def google_oauth_callback(
    request: Request,
    state: str = Query(default="", max_length=300),
    code: str = Query(default="", max_length=4096),
    error: str | None = Query(default=None, max_length=120),
):
    connection = GoogleBIConnection()
    try:
        with engine.begin() as conn:
            _require_google_bi_schema(conn)
            verifier, actor_id = connection.consume_callback_state(conn, state=state)
            actor_row = conn.execute(text("""
              SELECT COALESCE(NULLIF(username,''),NULLIF(email,''),'user-'||id_usuario::text) AS actor,
                     COALESCE(NULLIF(rol,''),'SUPERADMIN') AS role
              FROM usuarios WHERE id_usuario=:id
            """), {"id": actor_id}).mappings().one()
            actor = str(actor_row["actor"])[:200]
            actor_role = str(actor_row["role"])[:80]
        if error:
            return _oauth_result_page(False)
        token_result = connection.exchange_code(code=code, code_verifier=verifier)
        with engine.begin() as conn:
            connection.save_authorization(conn, token_result=token_result, actor=actor)
        with engine.connect() as conn:
            connection.list_properties(conn)
        with engine.begin() as conn:
            connection.mark_verified(conn, actor=actor)
            log_activity(
                conn, username=actor, user_id=int(actor_id), role=actor_role,
                action="gd_intelligence.google_bi.authorized", entity_type="wi_google_bi_connection",
                entity_id="google_bi", meta={"result": "SUCCESS", "scopes_verified": True},
                request=request, status_code=200,
            )
        return _oauth_result_page(True)
    except GoogleBIError as exc:
        if exc.code != "OAUTH_STATE_INVALID":
            with engine.begin() as conn:
                _require_google_bi_schema(conn)
                connection.record_error(conn, error=exc, actor=locals().get("actor", "google-oauth-callback"))
        return _oauth_result_page(False)


@router.post("/web/integrations/google/oauth/disconnect")
def google_oauth_disconnect(request: Request = None, user: dict = Depends(get_current_user)):
    with engine.begin() as conn:
        _require(conn, user, "system_integrations_manage")
        _require_google_bi_schema(conn)
        GoogleBIConnection().disconnect(conn, actor=_actor(user))
        log_activity(
            conn, username=_actor(user), user_id=user_id(user), role=role_key(user),
            action="gd_intelligence.google_bi.disconnected", entity_type="wi_google_bi_connection",
            entity_id="google_bi", meta={"historical_data_preserved": True}, request=request, status_code=200,
        )
    return {"ok": True, "message": "Google BI fue desconectado. Los datos históricos se conservan."}


@router.get("/web/integrations/google/properties")
def google_properties(user: dict = Depends(get_current_user)):
    connection = GoogleBIConnection()
    try:
        with engine.connect() as conn:
            _require(conn, user, "web_intelligence_configure")
            _require_google_bi_schema(conn)
            result = connection.list_properties(conn)
        with engine.begin() as conn:
            connection.mark_verified(conn, actor=_actor(user))
        return {"ok": True, **result}
    except GoogleBIError as exc:
        with engine.begin() as conn:
            _require_google_bi_schema(conn)
            connection.record_error(conn, error=exc, actor=_actor(user))
        raise _google_http_error(exc) from exc


def _sync_google_site(site_id: int, actor: str) -> list[dict]:
    with engine.connect() as conn:
        rows = list(conn.execute(text("""
          SELECT i.id,i.provider,i.config->>'property_id' AS ga4_property_id,i.external_id AS sc_property
          FROM public.wi_integrations i
          WHERE i.site_id=:site AND i.provider IN ('GA4','SEARCH_CONSOLE')
          ORDER BY i.provider
        """), {"site": site_id}).mappings().all())
    connection = GoogleBIConnection()
    start_date, end_date = seven_day_window()
    results: list[dict] = []
    for row in rows:
        provider = str(row["provider"])
        mapping = row.get("ga4_property_id") if provider == "GA4" else row.get("sc_property")
        if not mapping:
            continue
        with engine.begin() as conn:
            run_id = conn.execute(text("""
              INSERT INTO public.wi_sync_runs(integration_id,provider,status,cursor)
              VALUES(:integration,:provider,'RUNNING',CAST(:cursor AS jsonb)) RETURNING id
            """), {"integration": int(row["id"]), "provider": provider,
                    "cursor": __import__("json").dumps({"start": start_date.isoformat(), "end": end_date.isoformat()})}).scalar_one()
        try:
            with engine.connect() as conn:
                provider_rows = connection.fetch_ga4(conn, property_id=str(mapping), start=start_date, end=end_date) if provider == "GA4" else connection.fetch_search_console(conn, property_url=str(mapping), start=start_date, end=end_date)
            with engine.begin() as conn:
                saved = persist_ga4_rows(conn, site_id=site_id, rows=provider_rows) if provider == "GA4" else persist_search_console_rows(conn, site_id=site_id, rows=provider_rows)
                conn.execute(text("""
                  UPDATE public.wi_sync_runs SET status='SUCCESS',finished_at=now(),rows_received=:rows,
                    duration_ms=GREATEST(0,(EXTRACT(EPOCH FROM (now()-started_at))*1000)::integer) WHERE id=:run
                """), {"rows": saved, "run": run_id})
                conn.execute(text("""
                  UPDATE public.wi_integrations SET status=:status,enabled=true,last_sync_at=now(),
                    last_success_at=now(),last_verified_at=now(),last_rows=:rows,last_error_code=NULL,
                    last_error_safe=NULL,retry_count=0,updated_at=now() WHERE id=:integration
                """), {"status": "CONNECTED" if saved else "NO_DATA", "rows": saved, "integration": int(row["id"])})
            results.append({"provider": provider, "state": "CONNECTED" if saved else "NO_DATA", "rows": saved})
        except Exception as raw_exc:
            exc = raw_exc if isinstance(raw_exc, GoogleBIError) else GoogleBIError(
                "GOOGLE_SYNC_PERSISTENCE_FAILED", "No pudimos guardar la sincronización de Google de forma segura."
            )
            with engine.begin() as conn:
                conn.execute(text("""
                  UPDATE public.wi_sync_runs SET status='ERROR',finished_at=now(),error_type=:code,error_safe=:safe,
                    duration_ms=GREATEST(0,(EXTRACT(EPOCH FROM (now()-started_at))*1000)::integer) WHERE id=:run
                """), {"code": exc.code, "safe": str(exc)[:300], "run": run_id})
                conn.execute(text("""
                  UPDATE public.wi_integrations SET status='ERROR',last_sync_at=now(),last_failure_at=now(),
                    last_error_code=:code,last_error_safe=:safe,retry_count=retry_count+1,updated_at=now()
                  WHERE id=:integration
                """), {"code": exc.code, "safe": str(exc)[:300], "integration": int(row["id"])})
            results.append({"provider": provider, "state": "ERROR", "rows": 0, "safe_error": str(exc)})
    successes = [item for item in results if item["state"] != "ERROR"]
    total = sum(int(item["rows"]) for item in successes)
    with engine.begin() as conn:
        conn.execute(text("""
          UPDATE public.wi_google_bi_connections SET
            status=CASE WHEN :successes>0 THEN 'API_VERIFIED' ELSE status END,
            last_sync_at=now(),last_data_at=CASE WHEN :rows>0 THEN now() ELSE last_data_at END,
            last_rows=:rows,updated_by=:actor,updated_at=now()
          WHERE connection_key='google_bi'
        """), {"successes": len(successes), "rows": total, "actor": actor[:200]})
    return results


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
    connection = GoogleBIConnection()
    try:
        with engine.connect() as conn:
            _require(conn, user, "web_intelligence_configure")
            _require_google_bi_schema(conn)
            available = connection.list_properties(conn)
    except GoogleBIError as exc:
        raise _google_http_error(exc) from exc
    if ga4_id and ga4_id not in {item["id"] for item in available["ga4"]}:
        raise HTTPException(422, "La propiedad GA4 no está disponible para la cuenta autorizada.")
    if sc_property and sc_property not in {item["id"] for item in available["search_console"]}:
        raise HTTPException(422, "La propiedad Search Console no está disponible para la cuenta autorizada.")
    with engine.begin() as conn:
        _require(conn, user, "web_intelligence_configure")
        if not conn.execute(text("SELECT 1 FROM public.wi_sites WHERE id=:id"), {"id": site_id}).scalar():
            raise HTTPException(404, "Sitio no encontrado")
        if ga4_id:
            conn.execute(text("""
              UPDATE public.wi_integrations SET
                config=jsonb_set(COALESCE(config,'{}'::jsonb),'{property_id}',to_jsonb(CAST(:value AS text)),true),
                enabled=true,status='REQUIRES_ATTENTION',updated_at=now()
              WHERE site_id=:site_id AND provider='GA4'
            """), {"site_id": site_id, "value": ga4_id})
        if sc_property:
            conn.execute(text("""
            UPDATE public.wi_integrations SET external_id=:value,enabled=true,status='REQUIRES_ATTENTION',updated_at=now()
              WHERE site_id=:site_id AND provider='SEARCH_CONSOLE'
            """), {"site_id": site_id, "value": sc_property})
        log_activity(conn, username=_actor(user), user_id=user_id(user), role=role_key(user),
                     action="gd_intelligence.google_properties.select", entity_type="wi_integration",
                     meta={"site_id": site_id, "ga4_selected": bool(ga4_id), "search_console_selected": bool(sc_property)},
                     request=request, status_code=200)
    return {"ok": True, "sync_window_days": 7, "sync": _sync_google_site(site_id, _actor(user))}


@router.post("/web/integrations/google/sync")
def google_sync(site_id: int = Query(..., ge=1), user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        _require(conn, user, "web_intelligence_configure")
        _require_google_bi_schema(conn)
        if not conn.execute(text("SELECT 1 FROM wi_sites WHERE id=:id AND enabled"), {"id": site_id}).scalar():
            raise HTTPException(404, "Sitio no encontrado")
    return {"ok": True, "site_id": site_id, "sync_window_days": 7, "items": _sync_google_site(site_id, _actor(user))}


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


@router.get("/tracking/metrics")
def tracking_metrics(user: dict = Depends(get_current_user)):
    with engine.begin() as conn:
        _require(conn, user, "web_intelligence_view")
        ready = conn.execute(text("SELECT to_regclass('public.wi_tracking_collector_metrics') IS NOT NULL")).scalar()
        if not ready:
            return {"ok": True, "status": "MIGRATION_REQUIRED", "totals": {}, "events_by_site": []}
        rows = conn.execute(text("""
          SELECT site_code,
                 sum(events_received)::bigint AS events_received,
                 sum(events_accepted)::bigint AS events_accepted,
                 sum(events_rejected)::bigint AS events_rejected,
                 sum(sessions_created)::bigint AS sessions_created,
                 sum(duplicate_events)::bigint AS duplicate_events,
                 max(last_event_at) AS last_event_at
          FROM public.wi_tracking_collector_metrics
          WHERE metric_date >= CURRENT_DATE - 30
          GROUP BY site_code ORDER BY site_code
        """)).mappings().all()
        items = [dict(row) for row in rows]
        totals = {
            key: sum(int(item.get(key) or 0) for item in items)
            for key in ("events_received", "events_accepted", "events_rejected", "sessions_created", "duplicate_events")
        }
        totals["last_event_at"] = max((item.get("last_event_at") for item in items if item.get("last_event_at")), default=None)
    return {"ok": True, "status": "REAL" if totals["events_accepted"] else "NO_DATA", "totals": totals, "events_by_site": items}


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


@router.get("/campaigns/attribution-report")
def attribution_report(
    days: int = Query(default=90, ge=1, le=730),
    site_id: int | None = Query(default=None, gt=0),
    user: dict = Depends(get_current_user),
):
    """Observed source/campaign/landing funnel. No UTM values are inferred or simulated."""
    with engine.connect() as conn:
        _require(conn, user, "web_intelligence_campaigns")
        _require_schema(conn)
        rows = conn.execute(text("""
          WITH per_lead AS (
            SELECT la.session_id,la.lead_id,
              (SELECT COUNT(*) FROM public.cotizaciones cq WHERE cq.id_lead=la.lead_id)::bigint quotes,
              CASE WHEN UPPER(COALESCE(el.nombre,'')) LIKE '%CONFIRM%' OR current_quote.accepted_at IS NOT NULL THEN 1 ELSE 0 END sales,
              CASE WHEN UPPER(COALESCE(el.nombre,'')) LIKE '%CONFIRM%' OR current_quote.accepted_at IS NOT NULL
                   THEN COALESCE(current_quote.total,0) ELSE 0 END revenue
            FROM public.wi_lead_attribution la
            JOIN public.leads l ON l.id_lead=la.lead_id AND COALESCE(l.is_deleted,false)=false
            LEFT JOIN public.estados_lead el ON el.id_estado=l.id_estado
            LEFT JOIN LATERAL (
              SELECT c.total,c.accepted_at FROM public.cotizaciones c
              WHERE c.id_lead=l.id_lead
              ORDER BY (c.id_cotizacion=l.id_cotizacion_vigente) DESC,c.id_cotizacion DESC LIMIT 1
            ) current_quote ON true
          ), outcomes AS (
            SELECT session_id,COUNT(DISTINCT lead_id)::bigint leads,COALESCE(SUM(quotes),0)::bigint quotes,
              COALESCE(SUM(sales),0)::bigint sales,COALESCE(SUM(revenue),0)::numeric revenue
            FROM per_lead GROUP BY session_id
          ), base AS (
            SELECT ws.session_id,ws.site_id,s.code site_code,ws.utm_source,ws.utm_medium,ws.utm_campaign,
              ws.landing_url,ws.started_at,ws.last_seen_at,COALESCE(o.leads,0) leads,
              COALESCE(o.quotes,0) quotes,COALESCE(o.sales,0) sales,COALESCE(o.revenue,0) revenue
            FROM public.wi_sessions ws JOIN public.wi_sites s ON s.id=ws.site_id
            LEFT JOIN outcomes o ON o.session_id=ws.session_id
            WHERE ws.started_at>=now()-(interval '1 day' * CAST(:days AS integer))
              AND (CAST(:site_id AS bigint) IS NULL OR ws.site_id=CAST(:site_id AS bigint))
          ), grouped AS (
            SELECT 'SOURCE' dimension,site_code,
              COALESCE(NULLIF(utm_source,''),'DIRECTO / SIN UTM') primary_value,
              COALESCE(NULLIF(utm_medium,''),'Sin medio') secondary_value,
              COUNT(DISTINCT session_id)::bigint visits,SUM(leads)::bigint leads,SUM(quotes)::bigint quotes,
              SUM(sales)::bigint sales,SUM(revenue)::numeric revenue,MAX(last_seen_at) last_data_at
            FROM base GROUP BY site_code,COALESCE(NULLIF(utm_source,''),'DIRECTO / SIN UTM'),COALESCE(NULLIF(utm_medium,''),'Sin medio')
            UNION ALL
            SELECT 'CAMPAIGN',site_code,COALESCE(NULLIF(utm_campaign,''),'SIN CAMPAÑA'),
              CONCAT_WS(' / ',NULLIF(utm_source,''),NULLIF(utm_medium,'')),
              COUNT(DISTINCT session_id)::bigint,SUM(leads)::bigint,SUM(quotes)::bigint,SUM(sales)::bigint,
              SUM(revenue)::numeric,MAX(last_seen_at)
            FROM base GROUP BY site_code,COALESCE(NULLIF(utm_campaign,''),'SIN CAMPAÑA'),CONCAT_WS(' / ',NULLIF(utm_source,''),NULLIF(utm_medium,''))
            UNION ALL
            SELECT 'LANDING',site_code,landing_url,COALESCE(NULLIF(utm_campaign,''),'Sin campaña'),
              COUNT(DISTINCT session_id)::bigint,SUM(leads)::bigint,SUM(quotes)::bigint,SUM(sales)::bigint,
              SUM(revenue)::numeric,MAX(last_seen_at)
            FROM base GROUP BY site_code,landing_url,COALESCE(NULLIF(utm_campaign,''),'Sin campaña')
          )
          SELECT dimension,site_code,primary_value,secondary_value,visits,leads,quotes,sales,revenue,
            CASE WHEN visits>0 THEN ROUND(leads::numeric/visits,4) END conversion,last_data_at
          FROM grouped ORDER BY dimension,visits DESC,site_code,primary_value LIMIT 500
        """), {"days": days, "site_id": site_id}).mappings().all()
    return {"ok": True, "items": [dict(row) for row in rows], "days": days,
            "null_policy": "Sólo sesiones y resultados observados; sin inferir UTMs ausentes."}


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


@router.get("/web/performance/latest")
def performance_latest(user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        _require(conn, user, "web_intelligence_performance")
        rows = conn.execute(text("""
          SELECT DISTINCT ON(p.site_id,p.strategy,p.source)
            p.id,p.site_id,s.code,s.name,s.domain,p.url,p.strategy,p.source,p.measured_at,
            p.performance,p.accessibility,p.best_practices,p.seo,p.lcp_ms,p.inp_ms,p.cls,
            p.fcp_ms,p.tbt_ms,p.speed_index_ms,p.ttfb_ms
          FROM wi_performance_runs p JOIN wi_sites s ON s.id=p.site_id
          ORDER BY p.site_id,p.strategy,p.source,p.measured_at DESC
        """)).mappings().all()
    return {"ok": True, "items": [dict(row) for row in rows], "model": {"lab": "PageSpeed/Lighthouse", "field": "CrUX"}}


@router.post("/web/performance/run")
def performance_run(
    request: Request,
    site_id: int | None = Query(default=None, gt=0),
    strategy: str = Query(default="mobile", pattern="^(mobile|desktop)$"),
    user: dict = Depends(get_current_user),
):
    """Run LAB and FIELD providers independently; one missing source never fabricates the other."""
    with engine.connect() as conn:
        _require(conn, user, "web_intelligence_performance")
        sites = conn.execute(text("""
          SELECT s.id,s.code,s.name,s.domain,
                 psi.id AS pagespeed_integration_id,
                 crux.id AS crux_integration_id
          FROM wi_sites s
          LEFT JOIN wi_integrations psi ON psi.site_id=s.id AND psi.provider='PAGESPEED'
          LEFT JOIN wi_integrations crux ON crux.site_id=s.id AND crux.provider='CRUX'
          WHERE s.enabled AND (CAST(:site_id AS bigint) IS NULL OR s.id=CAST(:site_id AS bigint))
          ORDER BY s.code
        """), {"site_id": site_id}).mappings().all()
    if not sites:
        raise HTTPException(404, "Sitio habilitado no encontrado.")

    results: list[dict] = []
    for site in sites:
        site_result = {
            "site_id": site["id"],
            "site_code": site["code"],
            "lab_state": "READY_FOR_CREDENTIAL",
            "field_state": "READY_FOR_CREDENTIAL",
            "lab": False,
            "field": False,
        }

        pagespeed_id = site.get("pagespeed_integration_id")
        with engine.connect() as conn:
            page_key = _pagespeed_key(conn, int(pagespeed_id)) if pagespeed_id else None
        if not pagespeed_id:
            site_result["lab_state"] = "NOT_CONFIGURED"
            site_result["lab_reason"] = "No existe la integración PageSpeed para este sitio."
        elif not page_key:
            site_result["lab_reason"] = "Falta configurar la API Key de PageSpeed en Integraciones."
        else:
            try:
                parsed = fetch_pagespeed(f"https://{site['domain']}/", api_key=page_key, strategy=strategy)
                row = performance_rows(parsed)[0]
                with engine.begin() as conn:
                    conn.execute(text("""
                      INSERT INTO wi_performance_runs(site_id,url,strategy,source,measured_at,performance,accessibility,
                        best_practices,seo,lcp_ms,inp_ms,cls,fcp_ms,tbt_ms,speed_index_ms,ttfb_ms,payload)
                      VALUES(:site_id,:url,:strategy,'LAB',now(),:performance,:accessibility,:best_practices,:seo,
                        :lcp_ms,NULL,:cls,:fcp_ms,:tbt_ms,:speed_index_ms,:ttfb_ms,'{}'::jsonb)
                    """), {
                        "site_id": site["id"], "url": row.get("url") or f"https://{site['domain']}/",
                        "strategy": str(row.get("strategy") or strategy).upper(),
                        **{key: row.get(key) for key in ("performance","accessibility","best_practices","seo","lcp_ms","cls","fcp_ms","tbt_ms","speed_index_ms","ttfb_ms")},
                    })
                    conn.execute(text("""
                      UPDATE wi_integrations SET status='CONNECTED',enabled=true,last_sync_at=now(),last_success_at=now(),
                        last_rows=1,last_error_code=NULL,last_error_safe=NULL,updated_at=now()
                      WHERE id=:id
                    """), {"id": pagespeed_id})
                site_result.update({"lab": True, "lab_state": "CONNECTED"})
            except PageSpeedRequestError as exc:
                with engine.begin() as conn:
                    conn.execute(text("""
                      UPDATE wi_integrations SET status='ERROR',last_sync_at=now(),last_failure_at=now(),
                        last_error_code='PAGESPEED_REQUEST_FAILED',last_error_safe=:message,updated_at=now()
                      WHERE id=:id
                    """), {"id": pagespeed_id, "message": str(exc)})
                site_result.update({"lab_state": "ERROR", "lab_reason": str(exc)})

        crux_id = site.get("crux_integration_id")
        with engine.connect() as conn:
            crux_key = _crux_key(conn, int(crux_id)) if crux_id else None
        if not crux_id:
            site_result["field_state"] = "NOT_CONFIGURED"
            site_result["field_reason"] = "No existe la integración CrUX para este sitio."
        elif not crux_key:
            site_result["field_reason"] = "Falta configurar la API Key de Chrome UX Report en Integraciones."
        else:
            try:
                field = fetch_crux(f"https://{site['domain']}", api_key=crux_key, strategy=strategy)
                with engine.begin() as conn:
                    conn.execute(text("""
                      INSERT INTO wi_performance_runs(site_id,url,strategy,source,measured_at,performance,accessibility,
                        best_practices,seo,lcp_ms,inp_ms,cls,fcp_ms,tbt_ms,speed_index_ms,ttfb_ms,payload)
                      VALUES(:site_id,:url,:strategy,'FIELD',now(),NULL,NULL,NULL,NULL,
                        :lcp_ms,:inp_ms,:cls,:fcp_ms,NULL,NULL,:ttfb_ms,'{}'::jsonb)
                    """), {
                        "site_id": site["id"], "url": field.get("url") or f"https://{site['domain']}/",
                        "strategy": str(field.get("strategy") or strategy).upper(),
                        **{key: field.get(key) for key in ("lcp_ms","inp_ms","cls","fcp_ms","ttfb_ms")},
                    })
                    conn.execute(text("""
                      UPDATE wi_integrations SET status='CONNECTED',enabled=true,last_sync_at=now(),last_success_at=now(),
                        last_rows=1,last_error_code=NULL,last_error_safe=NULL,updated_at=now()
                      WHERE id=:id
                    """), {"id": crux_id})
                site_result.update({"field": True, "field_state": "CONNECTED", "field_reason": None})
            except CrUXNoData as exc:
                with engine.begin() as conn:
                    conn.execute(text("""
                      UPDATE wi_integrations SET status='NO_DATA',enabled=true,last_sync_at=now(),last_success_at=now(),
                        last_rows=0,last_error_code=NULL,last_error_safe=NULL,updated_at=now()
                      WHERE id=:id
                    """), {"id": crux_id})
                site_result.update({"field": False, "field_state": "NO_DATA", "field_reason": str(exc)})
            except CrUXRequestError as exc:
                with engine.begin() as conn:
                    conn.execute(text("""
                      UPDATE wi_integrations SET status='ERROR',last_sync_at=now(),last_failure_at=now(),
                        last_error_code=:code,last_error_safe=:message,updated_at=now()
                      WHERE id=:id
                    """), {"id": crux_id, "code": exc.code, "message": str(exc)})
                site_result.update({"field_state": "ERROR", "field_reason": str(exc)})

        site_result["state"] = "CONNECTED" if site_result["lab"] or site_result["field"] else (
            "ERROR" if "ERROR" in {site_result["lab_state"], site_result["field_state"]} else
            "NO_DATA" if site_result["field_state"] == "NO_DATA" and site_result["lab_state"] != "CONNECTED" else
            "READY_FOR_CREDENTIAL"
        )
        results.append(site_result)

    with engine.begin() as conn:
        log_activity(conn, username=_actor(user), user_id=user_id(user), role=role_key(user),
                     action="gd_intelligence.performance.run", entity_type="wi_performance_runs",
                     meta={"site_id": site_id, "strategy": strategy, "sites": len(results),
                           "lab_connected": sum(1 for item in results if item["lab"]),
                           "field_connected": sum(1 for item in results if item["field"])},
                     request=request, status_code=200)
    return {"ok": True, "items": results, "strategy": strategy.upper(),
            "model": {"lab": "PageSpeed/Lighthouse", "field": "Chrome UX Report API"}}
