from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import text

from backend.core.db import get_connection
from backend.routers.auth import get_current_user

router = APIRouter(tags=["permissions"])

EXCLUDED_ROLE_PARTS = ("OPERADOR", "CONDUCTOR", "CHOFER", "CHOP", "PATIO")

PERMISSION_CATALOG: list[dict] = [
    {"id": "dash_home", "group": "Dashboard", "label": "Inicio / dashboard"},
    {"id": "leads_ver", "group": "Ventas", "label": "Leads"},
    {"id": "leads_fil", "group": "Ventas", "label": "Filtros / embudos de leads"},
    {"id": "historial", "group": "Ventas", "label": "Historial"},
    {"id": "events_calendar", "group": "Agenda", "label": "Agenda / eventos"},
    {"id": "evt", "group": "Agenda", "label": "Registrar evento"},
    {"id": "chk_hoy", "group": "Agenda", "label": "Checklist eventos"},
    {"id": "rep_funnel", "group": "Reportes", "label": "Funnel"},
    {"id": "rep_cierre", "group": "Reportes", "label": "Cierre"},
    {"id": "rep_tipo", "group": "Reportes", "label": "Tipos de cliente"},
    {"id": "rep_total", "group": "Reportes", "label": "Venta total"},
    {"id": "rep_cxc", "group": "Reportes", "label": "Cuentas por cobrar"},
    {"id": "rep_cxp", "group": "Reportes", "label": "Cuentas por pagar"},
    {"id": "rep_com", "group": "Reportes", "label": "Comisiones"},
    {"id": "rep_prod", "group": "Reportes", "label": "Productos"},
    {"id": "rep_cli", "group": "Reportes", "label": "Clientes"},
    {"id": "rep_hoy", "group": "Reportes", "label": "Venta diaria"},
    {"id": "rep_dia", "group": "Reportes", "label": "Venta por dia"},
    {"id": "gast", "group": "Finanzas", "label": "Gastos"},
    {"id": "plan_cuentas", "group": "Finanzas", "label": "Plan de cuentas"},
    {"id": "inv_tomar", "group": "Inventario", "label": "Toma inventario"},
    {"id": "inv_stock", "group": "Inventario", "label": "Stock"},
    {"id": "inv_prod", "group": "Inventario", "label": "Productos inventario"},
    {"id": "inv_class", "group": "Inventario", "label": "Clasificacion"},
    {"id": "inv_cat", "group": "Inventario", "label": "Categorias"},
    {"id": "inv_uni", "group": "Inventario", "label": "Unidades"},
    {"id": "inv_prov", "group": "Inventario", "label": "Proveedores"},
    {"id": "inv_mov", "group": "Inventario", "label": "Movimientos"},
    {"id": "op_gps", "group": "Operaciones", "label": "GPS"},
    {"id": "op_vruta", "group": "Operaciones", "label": "Vista ruta"},
    {"id": "op_cal", "group": "Operaciones", "label": "Calendario operativo"},
    {"id": "op_rec", "group": "Operaciones", "label": "Recetas"},
    {"id": "op_mice", "group": "Operaciones", "label": "MICE & place"},
    {"id": "op_ruta", "group": "Operaciones", "label": "Rutas"},
    {"id": "op_ma_cat", "group": "Operaciones", "label": "Materiales categorias"},
    {"id": "op_ma_inv", "group": "Operaciones", "label": "Materiales inventario"},
    {"id": "op_ma_ficha", "group": "Operaciones", "label": "Ficha materiales"},
    {"id": "op_ca_ficha", "group": "Operaciones", "label": "Ficha carros"},
    {"id": "op_ca_ent", "group": "Operaciones", "label": "Entrega carros"},
    {"id": "op_ca_dev", "group": "Operaciones", "label": "Devolucion carros"},
    {"id": "rrhh_hub", "group": "RRHH", "label": "RRHH hub"},
    {"id": "rrhh_nomina", "group": "RRHH", "label": "Nomina"},
    {"id": "rrhh_staff", "group": "RRHH", "label": "Colaboradores"},
    {"id": "rrhh_sgjo", "group": "RRHH", "label": "Marcaciones SGJO"},
    {"id": "rrhh_solicitudes", "group": "RRHH", "label": "Solicitudes RRHH"},
    {"id": "tasks_my", "group": "Tareas", "label": "Mis tareas"},
    {"id": "system_notifs", "group": "Tareas", "label": "Notificaciones internas"},
    {"id": "tool_gmail", "group": "Tools", "label": "Correo GIA"},
    {"id": "tool_ig", "group": "Tools", "label": "Instagram GIA"},
    {"id": "tool_wapp", "group": "Tools", "label": "WhatsApp"},
    {"id": "tool_chat", "group": "Tools", "label": "Chat"},
    {"id": "tool_calc", "group": "Tools", "label": "Calculadora"},
    {"id": "tools_hub", "group": "Tools", "label": "Tools hub"},
    {"id": "set_users", "group": "Settings", "label": "Usuarios"},
    {"id": "set_permissions", "group": "Settings", "label": "Permisos"},
    {"id": "set_marcas", "group": "Settings", "label": "Marcas"},
    {"id": "set_prod", "group": "Settings", "label": "Productos venta"},
    {"id": "set_comi", "group": "Settings", "label": "Comisiones"},
    {"id": "set_com", "group": "Settings", "label": "Comunas"},
    {"id": "set_tc", "group": "Settings", "label": "Tipos cliente"},
    {"id": "set_el", "group": "Settings", "label": "Estados lead"},
    {"id": "set_roles", "group": "Settings", "label": "Roles"},
    {"id": "set_notify_email", "group": "Settings", "label": "Notificaciones correo"},
    {"id": "set_metas", "group": "Settings", "label": "Metas ventas"},
    {"id": "set_bak", "group": "Settings", "label": "Backups"},
]


def _role(me: dict) -> str:
    return str(me.get("role") or me.get("rol") or "").strip().upper()


def _is_admin(me: dict) -> bool:
    r = _role(me)
    return "SUPER" in r or r == "ADMIN" or "ADMIN" in r


def _require_admin(me: dict) -> None:
    if not _is_admin(me):
        raise HTTPException(status_code=403, detail="Solo Admin/SuperAdmin.")


def _ensure_tables(conn) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.user_menu_permissions (
              id_usuario INTEGER NOT NULL,
              menu_id TEXT NOT NULL,
              access TEXT NOT NULL DEFAULT 'none',
              updated_by INTEGER,
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY (id_usuario, menu_id)
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.role_menu_permissions (
              rol TEXT NOT NULL,
              menu_id TEXT NOT NULL,
              access TEXT NOT NULL DEFAULT 'none',
              updated_by INTEGER,
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY (rol, menu_id)
            )
            """
        )
    )
    try:
        conn.commit()
    except Exception:
        pass


def _norm_access(value) -> str:
    v = str(value or "none").strip().lower()
    if v not in ("none", "read", "full"):
        return "none"
    return v


def _uid(me: dict) -> int | None:
    raw = me.get("id_usuario") or me.get("id") or me.get("user_id")
    try:
        return int(raw) if str(raw or "").isdigit() else None
    except Exception:
        return None


def _is_excluded_role(role: str) -> bool:
    r = str(role or "").upper()
    return any(part in r for part in EXCLUDED_ROLE_PARTS)


def _all_full() -> dict[str, str]:
    return {str(item["id"]): "full" for item in PERMISSION_CATALOG}


def _ensure_usuarios_marcas(conn) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.usuarios_marcas(
              id_usuario INTEGER NOT NULL REFERENCES usuarios(id_usuario) ON DELETE CASCADE,
              id_marca INTEGER NOT NULL REFERENCES marcas(id_marca) ON DELETE CASCADE,
              PRIMARY KEY (id_usuario, id_marca)
            )
            """
        )
    )
    try:
        conn.commit()
    except Exception:
        pass


def _brands_for_users(conn, user_ids: list[int]) -> dict[int, list[dict]]:
    ids = []
    for raw in user_ids or []:
        try:
            val = int(raw)
            if val > 0:
                ids.append(val)
        except Exception:
            continue
    ids = sorted(set(ids))
    if not ids:
        return {}
    _ensure_usuarios_marcas(conn)
    rows = conn.execute(
        text(
            """
            SELECT um.id_usuario,
                   m.id_marca,
                   COALESCE(NULLIF(btrim(m.marca),''), NULLIF(btrim(m.nombre),''), 'Marca ' || m.id_marca::text) AS marca
            FROM public.usuarios_marcas um
            JOIN public.marcas m ON m.id_marca=um.id_marca
            WHERE um.id_usuario = ANY(:ids)
            ORDER BY um.id_usuario, lower(COALESCE(NULLIF(btrim(m.marca),''), NULLIF(btrim(m.nombre),''), ''))
            """
        ),
        {"ids": ids},
    ).mappings().all()
    out: dict[int, list[dict]] = {}
    for r in rows or []:
        uid = int(r["id_usuario"])
        out.setdefault(uid, []).append({"id_marca": int(r["id_marca"]), "marca": str(r["marca"] or "")})
    return out


def _attach_brands(conn, users: list[dict]) -> list[dict]:
    by_user = _brands_for_users(conn, [int(u.get("id_usuario") or 0) for u in users or []])
    out = []
    for u in users or []:
        item = dict(u)
        marcas = by_user.get(int(item.get("id_usuario") or 0), [])
        item["marcas"] = marcas
        item["marcas_ids"] = [int(m["id_marca"]) for m in marcas]
        item["marcas_label"] = ", ".join([str(m["marca"]) for m in marcas]) if marcas else "Todas / sin restriccion"
        out.append(item)
    return out


@router.get("/admin/permissions/catalog")
def permissions_catalog(me=Depends(get_current_user)):
    _require_admin(me)
    return {"ok": True, "items": PERMISSION_CATALOG}


@router.get("/admin/permissions/users")
def permissions_users(include_ops: bool = Query(False), me=Depends(get_current_user)):
    _require_admin(me)
    with get_connection() as conn:
        where_ops = "" if include_ops else """
                  AND upper(COALESCE(rol,'')) NOT LIKE '%OPERADOR%'
                  AND upper(COALESCE(rol,'')) NOT LIKE '%CONDUCTOR%'
                  AND upper(COALESCE(rol,'')) NOT LIKE '%CHOFER%'
                  AND upper(COALESCE(rol,'')) NOT LIKE '%CHOP%'
                  AND upper(COALESCE(rol,'')) NOT LIKE '%PATIO%'
        """
        rows = conn.execute(
            text(
                f"""
                SELECT id_usuario,
                       COALESCE(NULLIF(btrim(nombre),''), NULLIF(btrim(username),''), 'Usuario') AS nombre,
                       COALESCE(NULLIF(btrim(username),''), '') AS username,
                       COALESCE(NULLIF(btrim(email),''), '') AS email,
                       COALESCE(NULLIF(btrim(rol),''), '') AS rol,
                       CASE
                         WHEN upper(COALESCE(rol,'')) LIKE '%OPERADOR%'
                           OR upper(COALESCE(rol,'')) LIKE '%CONDUCTOR%'
                           OR upper(COALESCE(rol,'')) LIKE '%CHOFER%'
                           OR upper(COALESCE(rol,'')) LIKE '%CHOP%'
                           OR upper(COALESCE(rol,'')) LIKE '%PATIO%'
                         THEN TRUE ELSE FALSE
                       END AS is_operational_role
                FROM public.usuarios
                WHERE COALESCE(is_active, TRUE) IS TRUE
                {where_ops}
                ORDER BY upper(COALESCE(rol,'')), lower(COALESCE(nombre, username, email, '')), id_usuario
                LIMIT 2000
                """
            )
        ).mappings().all()
        items = _attach_brands(conn, [dict(r) for r in rows])
    return {"ok": True, "items": items}


@router.get("/admin/permissions/users/{id_usuario}")
def permissions_get_user(id_usuario: int, me=Depends(get_current_user)):
    _require_admin(me)
    with get_connection() as conn:
        _ensure_tables(conn)
        user = conn.execute(
            text(
                """
                SELECT id_usuario, nombre, username, email, rol
                FROM public.usuarios
                WHERE id_usuario=:id
                LIMIT 1
                """
            ),
            {"id": int(id_usuario)},
        ).mappings().first()
        if not user:
            raise HTTPException(status_code=404, detail="Usuario no encontrado.")
        user_d = dict(user)
        if _is_excluded_role(str(user_d.get("rol") or "")):
            raise HTTPException(status_code=403, detail="Operadores/conductores no usan esta matriz.")
        user_d = _attach_brands(conn, [user_d])[0]
        rows = conn.execute(
            text(
                """
                SELECT menu_id, access
                FROM public.user_menu_permissions
                WHERE id_usuario=:id
                """
            ),
            {"id": int(id_usuario)},
        ).mappings().all()
    return {"ok": True, "user": user_d, "permissions": {str(r["menu_id"]): str(r["access"]) for r in rows}}


@router.put("/admin/permissions/users/{id_usuario}")
def permissions_set_user(id_usuario: int, body: dict = Body(default_factory=dict), me=Depends(get_current_user)):
    _require_admin(me)
    perms = body.get("permissions") or {}
    if not isinstance(perms, dict):
        raise HTTPException(status_code=400, detail="permissions debe ser objeto.")
    catalog_ids = {str(item["id"]) for item in PERMISSION_CATALOG}
    by = _uid(me)
    with get_connection() as conn:
        _ensure_tables(conn)
        user = conn.execute(
            text("SELECT id_usuario, rol FROM public.usuarios WHERE id_usuario=:id LIMIT 1"),
            {"id": int(id_usuario)},
        ).mappings().first()
        if not user:
            raise HTTPException(status_code=404, detail="Usuario no encontrado.")
        if _is_excluded_role(str(user.get("rol") or "")):
            raise HTTPException(status_code=403, detail="Operadores/conductores no usan esta matriz.")
        conn.execute(text("DELETE FROM public.user_menu_permissions WHERE id_usuario=:id"), {"id": int(id_usuario)})
        for menu_id, access in perms.items():
            mid = str(menu_id or "").strip()
            acc = _norm_access(access)
            if mid not in catalog_ids or acc == "none":
                continue
            conn.execute(
                text(
                    """
                    INSERT INTO public.user_menu_permissions(id_usuario, menu_id, access, updated_by, updated_at)
                    VALUES(:id, :menu, :access, :by, now())
                    """
                ),
                {"id": int(id_usuario), "menu": mid, "access": acc, "by": by},
            )
        conn.commit()
    return permissions_get_user(id_usuario, me)


@router.get("/me/permissions")
def me_permissions(me=Depends(get_current_user)):
    role = _role(me)
    uid = _uid(me)
    if _is_admin(me):
        return {"ok": True, "source": "admin", "permissions": _all_full(), "marcas": [], "marcas_ids": [], "brand_scope": "all"}
    if _is_excluded_role(role):
        return {"ok": True, "source": "excluded", "permissions": {}, "marcas": [], "marcas_ids": [], "brand_scope": "excluded"}
    if not uid:
        return {"ok": True, "source": "none", "permissions": {}, "marcas": [], "marcas_ids": [], "brand_scope": "none"}
    with get_connection() as conn:
        _ensure_tables(conn)
        marcas = _brands_for_users(conn, [int(uid)]).get(int(uid), [])
        rows = conn.execute(
            text(
                """
                SELECT menu_id, access
                FROM public.user_menu_permissions
                WHERE id_usuario=:id
                """
            ),
            {"id": int(uid)},
        ).mappings().all()
    return {
        "ok": True,
        "source": "user",
        "permissions": {str(r["menu_id"]): str(r["access"]) for r in rows},
        "marcas": marcas,
        "marcas_ids": [int(m["id_marca"]) for m in marcas],
        "brand_scope": "assigned" if marcas else "all_or_unassigned",
    }
