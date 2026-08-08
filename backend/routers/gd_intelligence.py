from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError

from backend.core.database import engine
from backend.gd_intelligence.permissions import PERMISSIONS, has_permission, resolve_permissions
from backend.gd_intelligence.repository import create_site, list_sites, schema_ready, update_site
from backend.gd_intelligence.schemas import SiteCreate, SiteUpdate
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
