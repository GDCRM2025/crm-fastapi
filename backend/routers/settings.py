# backend/routers/settings.py
from __future__ import annotations

from typing import List, Optional
import json

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text

from backend.core.db import get_connection, table_columns
from backend.core.email import send_email_group
from backend.routers.auth import get_current_user

# IMPORTANTE:
# Este router es legacy. Históricamente expuso endpoints sin prefijo (ej: `/productos`)
# y termina pisando rutas modernas (`backend.routers.productos`), rompiendo el cotizador
# y el filtrado por marcas para ejecutivos.
#
# Para mantener compatibilidad sin romper el sistema actual, lo dejamos aislado bajo
# `/legacy_settings/*`.
router = APIRouter(prefix="/legacy_settings", tags=["legacy_settings"])


def _role_key(role: str) -> str:
    import re, unicodedata
    raw = str(role or "").strip().lower()
    raw = unicodedata.normalize("NFD", raw)
    raw = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
    raw = re.sub(r"[^a-z0-9]+", "", raw)
    return raw


def _is_admin(role: str) -> bool:
    rk = _role_key(role)
    return ("admin" in rk) or ("superadmin" in rk)


def _ensure_system_notifs(conn) -> None:
    try:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS public.system_notifs (
                  id BIGSERIAL PRIMARY KEY,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  kind TEXT NOT NULL,
                  role_target TEXT NOT NULL,
                  id_lead BIGINT,
                  title TEXT NOT NULL,
                  body TEXT NOT NULL,
                  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                  read_at TIMESTAMPTZ,
                  read_by TEXT,
                  UNIQUE(kind, role_target, id_lead)
                )
                """
            )
        )
    except Exception:
        pass


def _emails_for_roles(conn, roles: list[str]) -> list[str]:
    """
    Emails de usuarios activos por rol objetivo.
    """
    roles_u = [str(r).upper().strip() for r in (roles or []) if str(r).strip()]
    if not roles_u:
        return []
    role_ids: list[int] = []
    role_names: list[str] = []
    for r in roles_u:
        if r.isdigit():
            try:
                role_ids.append(int(r))
            except Exception:
                pass
        else:
            role_names.append(r)
    try:
        rows = conn.execute(
            text(
                """
                SELECT DISTINCT u.email
                FROM public.usuarios u
                LEFT JOIN public.roles r ON r.id_rol=u.id_rol
                WHERE COALESCE(u.is_active, TRUE) = TRUE
                  AND u.email IS NOT NULL AND u.email <> ''
                  AND (
                    (:has_names AND (
                      UPPER(COALESCE(r.nombre,'')) = ANY(:role_names)
                      OR UPPER(COALESCE(u.rol,'')) = ANY(:role_names)
                    ))
                    OR
                    (:has_ids AND (
                      u.id_rol = ANY(:role_ids)
                      OR r.id_rol = ANY(:role_ids)
                    ))
                  )
                """
            ),
            {
                "has_names": bool(role_names),
                "has_ids": bool(role_ids),
                "role_names": role_names or ["__NONE__"],
                "role_ids": role_ids or [-1],
            },
        ).fetchall()
        out = []
        for r in rows:
            e = (r[0] or "").strip()
            if "@" in e and "." in e:
                out.append(e)
        return sorted(set(out))
    except Exception:
        return []


# -------------------
# Models
# -------------------
class MarcaIn(BaseModel):
    marca: str = Field(..., min_length=1)
    logo_path: Optional[str] = None
    is_active: bool = True


class MarcaOut(MarcaIn):
    id_marca: int


class ProductoIn(BaseModel):
    producto: str = Field(..., min_length=1)
    descripcion: Optional[str] = None
    marca: str = Field(..., min_length=1)
    costo: float = 0
    is_active: bool = True
    orden: int = 0


class ProductoOut(ProductoIn):
    id_producto: int


# -------------------
# Marcas
# -------------------
@router.get("/marcas", response_model=List[MarcaOut])
def list_marcas(solo_activas: bool = Query(False)):
    with get_connection() as conn:
        sql = """
        SELECT id_marca, marca, logo_path, is_active
        FROM public.marcas
        """
        if solo_activas:
            sql += " WHERE is_active = true"
        sql += " ORDER BY marca ASC"

        rows = conn.execute(text(sql)).mappings().all()
        return [dict(r) for r in rows]


@router.post("/marcas", response_model=MarcaOut)
def upsert_marca(payload: MarcaIn):
    with get_connection() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO public.marcas (marca, logo_path, is_active)
                VALUES (:marca, :logo_path, :is_active)
                ON CONFLICT (marca)
                DO UPDATE SET
                  logo_path = EXCLUDED.logo_path,
                  is_active = EXCLUDED.is_active
                RETURNING id_marca, marca, logo_path, is_active
                """
            ),
            payload.model_dump(),
        ).mappings().one()
        conn.commit()
        return dict(row)


@router.put("/marcas/{id_marca}", response_model=MarcaOut)
def update_marca(id_marca: int, payload: MarcaIn):
    with get_connection() as conn:
        row = conn.execute(
            text(
                """
                UPDATE public.marcas
                SET marca=:marca, logo_path=:logo_path, is_active=:is_active
                WHERE id_marca=:id_marca
                RETURNING id_marca, marca, logo_path, is_active
                """
            ),
            {"id_marca": id_marca, **payload.model_dump()},
        ).mappings().first()

        if not row:
            raise HTTPException(status_code=404, detail="Marca no existe")
        conn.commit()
        return dict(row)


# -------------------
# Productos
# -------------------
@router.get("/productos", response_model=List[ProductoOut])
def list_productos(
    marca: Optional[str] = None,
    solo_activos: bool = Query(True),
):
    with get_connection() as conn:
        cols = table_columns(conn, "productos")
        where = []
        params = {}

        if marca:
            where.append(
                "translate(upper(COALESCE(marca,'')), 'ÁÉÍÓÚÜÑáéíóúüñ', 'AEIOUUNAEIOUUN') = "
                "translate(upper(:marca), 'ÁÉÍÓÚÜÑáéíóúüñ', 'AEIOUUNAEIOUUN')"
            )
            params["marca"] = str(marca).strip()

        if solo_activos and "is_active" in cols:
            where.append("COALESCE(is_active, true) = true")

        sql = """
        SELECT id_producto, producto, descripcion, marca, costo,
               COALESCE(is_active, true) AS is_active,
               COALESCE(orden, 0) AS orden
        FROM public.productos
        """
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY marca ASC, orden ASC, producto ASC"

        rows = conn.execute(text(sql), params).mappings().all()
        return [dict(r) for r in rows]


@router.post("/productos", response_model=ProductoOut)
def create_producto(payload: ProductoIn, user: dict = Depends(get_current_user)):
    with get_connection() as conn:
        role = str(user.get("role") or user.get("rol") or "").strip()
        marcas_ids = [int(x) for x in (user.get("marcas") or []) if str(x).isdigit()]
        if (not _is_admin(role)) and (not marcas_ids):
            # Fallback: tokens legacy pueden venir sin `marcas`.
            # Intentamos leer desde usuarios_marcas / usuario_marcas / usuarios.id_marca.
            try:
                uid = user.get("id") or user.get("sub") or user.get("username") or user.get("email")
                id_usuario = None
                if str(uid or "").isdigit():
                    id_usuario = int(str(uid))
                else:
                    u = str(uid or "").strip()
                    if u:
                        id_usuario = conn.execute(
                            text(
                                """
                                SELECT id_usuario
                                FROM public.usuarios
                                WHERE username=:u OR email=:u
                                LIMIT 1
                                """
                            ),
                            {"u": u},
                        ).scalar()
                        if id_usuario is not None:
                            id_usuario = int(id_usuario)
                if id_usuario:
                    join_table = None
                    try:
                        has_um = bool(conn.execute(text("SELECT to_regclass('public.usuarios_marcas') IS NOT NULL")).scalar())
                    except Exception:
                        has_um = False
                    try:
                        has_um_legacy = bool(conn.execute(text("SELECT to_regclass('public.usuario_marcas') IS NOT NULL")).scalar())
                    except Exception:
                        has_um_legacy = False
                    if has_um:
                        join_table = "usuarios_marcas"
                    elif has_um_legacy:
                        join_table = "usuario_marcas"
                    if join_table:
                        rows = conn.execute(
                            text(f"SELECT id_marca FROM public.{join_table} WHERE id_usuario=:id"),
                            {"id": int(id_usuario)},
                        ).fetchall()
                        for r in rows:
                            try:
                                marcas_ids.append(int(r[0]))
                            except Exception:
                                pass
                    if not marcas_ids:
                        try:
                            mid = conn.execute(
                                text("SELECT id_marca FROM public.usuarios WHERE id_usuario=:id LIMIT 1"),
                                {"id": int(id_usuario)},
                            ).scalar()
                            if mid is not None:
                                marcas_ids.append(int(mid))
                        except Exception:
                            pass
                    marcas_ids = sorted(set([int(x) for x in marcas_ids if str(x).isdigit()]))
            except Exception:
                marcas_ids = marcas_ids or []

        marca_in = str(payload.marca or "").strip()
        if not marca_in:
            raise HTTPException(status_code=400, detail="marca requerida")

        if not _is_admin(role):
            if not marcas_ids:
                raise HTTPException(status_code=403, detail="No tienes marcas asignadas para crear productos.")

            if len(marcas_ids) == 1:
                mid = marcas_ids[0]
                mname = conn.execute(
                    text("SELECT COALESCE(nombre,marca,'') AS nombre FROM public.marcas WHERE id_marca=:id LIMIT 1"),
                    {"id": int(mid)},
                ).scalar() or ""
                marca_in = str(mname or marca_in).strip()
            else:
                mid = conn.execute(
                    text(
                        """
                        SELECT id_marca
                        FROM public.marcas
                        WHERE translate(upper(COALESCE(nombre,marca,'')), 'ÁÉÍÓÚÜÑáéíóúüñ', 'AEIOUUNAEIOUUN')
                              = translate(upper(:m), 'ÁÉÍÓÚÜÑáéíóúüñ', 'AEIOUUNAEIOUUN')
                        LIMIT 1
                        """
                    ),
                    {"m": marca_in},
                ).scalar()
                if mid is None or int(mid) not in set(marcas_ids):
                    raise HTTPException(status_code=403, detail="No puedes crear productos para esa marca.")

        producto_txt = str(payload.producto or "").strip()
        if not producto_txt:
            raise HTTPException(status_code=400, detail="producto requerido")

        marca_canon = conn.execute(
            text(
                """
                SELECT UPPER(TRIM(COALESCE(nombre, marca, '')))
                FROM public.marcas
                WHERE translate(upper(COALESCE(nombre, marca, '')), 'ÁÉÍÓÚÜÑáéíóúüñ', 'AEIOUUNAEIOUUN')
                    = translate(upper(:m), 'ÁÉÍÓÚÜÑáéíóúüñ', 'AEIOUUNAEIOUUN')
                LIMIT 1
                """
            ),
            {"m": marca_in},
        ).scalar()

        marca_canon = str(marca_canon or marca_in).strip().upper()

        data = payload.model_dump()
        data["producto"] = producto_txt
        data["marca"] = marca_canon
        data["is_active"] = True if data.get("is_active") is None else bool(data["is_active"])

        row = conn.execute(
            text(
                """
                INSERT INTO public.productos (producto, descripcion, marca, costo, is_active, orden)
                VALUES (:producto, :descripcion, :marca, :costo, :is_active, :orden)
                RETURNING id_producto, producto, descripcion, marca, costo, is_active, orden
                """
            ),
            data,
        ).mappings().one()

        conn.commit()

        try:
            _ensure_system_notifs(conn)
            id_producto = int(row["id_producto"])
            who = (user.get("nombre") or user.get("name") or user.get("username") or str(user.get("id") or "")).strip() or "CRM"
            roles = ["OPERACIONES", "JEFE DE OPERACIONES", "MICE", "ADMIN", "SUPERADMIN"]
            title = f"Nuevo producto ({marca_canon})"
            body = f"{row.get('producto') or ''} · creado por {who}"
            url = f"/crm/web/views/operaciones_recetas.html?open_producto_id={id_producto}"
            payload_notif = {
                "id_producto": id_producto,
                "producto": row.get("producto") or "",
                "marca": marca_canon,
                "created_by": who,
                "url": url,
            }
            pjson = json.dumps(payload_notif, ensure_ascii=False)
            fake_lead_id = -id_producto

            for rt in roles:
                conn.execute(
                    text(
                        """
                        INSERT INTO public.system_notifs(kind, role_target, id_lead, title, body, payload)
                        VALUES ('PRODUCTO_NUEVO', :rt, :id, :t, :b, CAST(:p AS JSONB))
                        ON CONFLICT (kind, role_target, id_lead) DO NOTHING
                        """
                    ),
                    {"rt": rt, "id": fake_lead_id, "t": title, "b": body, "p": pjson},
                )
            conn.commit()

            to = _emails_for_roles(conn, roles)
            if to:
                subj = f"CRM · Nuevo producto ({marca_canon})"
                txt = f"Producto: {row.get('producto')}\nMarca: {marca_canon}\nCreado por: {who}\nLink: {url}\n"
                html = f"""
                <div style="font-family:Arial,Helvetica,sans-serif;line-height:1.4">
                  <h2 style="margin:0 0 8px">Nuevo producto para receta</h2>
                  <div style="margin:0 0 14px;color:#334155">
                    Se creó un producto de venta y necesita receta en Operaciones.
                  </div>
                  <table style="border-collapse:collapse">
                    <tr><td style="padding:4px 10px 4px 0;font-weight:700">Marca</td><td style="padding:4px 0">{marca_canon}</td></tr>
                    <tr><td style="padding:4px 10px 4px 0;font-weight:700">Producto</td><td style="padding:4px 0">{row.get('producto') or ''}</td></tr>
                    <tr><td style="padding:4px 10px 4px 0;font-weight:700">Creado por</td><td style="padding:4px 0">{who}</td></tr>
                  </table>
                  <div style="margin-top:14px">
                    <a href="{url}" style="display:inline-block;background:#16a34a;color:#fff;text-decoration:none;padding:10px 14px;border-radius:12px;font-weight:700">
                      Crear receta / sub-receta
                    </a>
                  </div>
                  <div style="margin-top:16px;color:#64748b;font-size:12px;border-top:1px solid #e2e8f0;padding-top:10px">
                    CRM Green Diamond · Notificación automática
                  </div>
                </div>
                """
                send_email_group(to, subj, txt, html=html)
        except Exception:
            pass

        return dict(row)


@router.put("/productos/{id_producto}", response_model=ProductoOut)
def update_producto(id_producto: int, payload: ProductoIn):
    with get_connection() as conn:
        row = conn.execute(
            text(
                """
                UPDATE public.productos
                SET producto=:producto,
                    descripcion=:descripcion,
                    marca=:marca,
                    costo=:costo,
                    is_active=:is_active,
                    orden=:orden
                WHERE id_producto=:id_producto
                RETURNING id_producto, producto, descripcion, marca, costo, is_active, orden
                """
            ),
            {"id_producto": id_producto, **payload.model_dump()},
        ).mappings().first()

        if not row:
            raise HTTPException(status_code=404, detail="Producto no existe")
        conn.commit()
        return dict(row)


@router.patch("/productos/{id_producto}/estado", response_model=ProductoOut)
def toggle_producto(id_producto: int, is_active: bool = Query(...)):
    with get_connection() as conn:
        row = conn.execute(
            text(
                """
                UPDATE public.productos
                SET is_active=:is_active
                WHERE id_producto=:id_producto
                RETURNING id_producto, producto, descripcion, marca, costo, is_active, COALESCE(orden,0) AS orden
                """
            ),
            {"id_producto": id_producto, "is_active": is_active},
        ).mappings().first()

        if not row:
            raise HTTPException(status_code=404, detail="Producto no existe")
        conn.commit()
        return dict(row)
