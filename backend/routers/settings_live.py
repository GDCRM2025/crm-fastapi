from __future__ import annotations

from typing import Any, Dict, List
import os
import secrets
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.core.database import engine
from backend.core.password import hash_password
from backend.core.email import send_email, EmailConfigError

# Intento importar auth (si existe). Si no existe, NO rompe el server.
try:
    from backend.routers.auth import get_current_user  # type: ignore
except Exception:  # pragma: no cover
    def get_current_user():  # type: ignore
        return {"rol": "Admin"}  # fallback dev


router = APIRouter(prefix="/settings", tags=["settings"])

def _as_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, (bytes, bytearray, memoryview)):
        try:
            return bytes(v).decode("utf-8", "ignore")
        except Exception:
            return str(v)
    return str(v)


# -----------------------------
# CONFIG
# -----------------------------
ENTITY_MAP: Dict[str, str] = {
    # Settings (UI)
    "usuarios": "usuarios",
    "marcas": "marcas",
    "productos": "productos",
    "comunas": "comunas",
    "roles": "roles",
    "estados": "estados_lead",
    "estados_lead": "estados_lead",
    "comisiones": "comisiones",

    # Alias frecuentes (tu tabla real es tipocliente)
    "tipo_cliente": "tipocliente",
    "tipocliente": "tipocliente",
    "tipos_cliente": "tipocliente",
    "tiposcliente": "tipocliente",
}

# Columnas que NO se muestran (ni se editan) en Settings
HIDDEN_COLS: Dict[str, List[str]] = {
    "usuarios": ["hashed_password", "id_rol"],
    # "marca" NO se oculta: en varios esquemas legacy, `marca` es la columna principal.
    # Ocultarla deja Settings inutilizable (no se puede crear/editar marcas).
    "marcas": [],
    "productos": ["orden", "ingredientes"],
    "comunas": ["costo_traslado", "comuna"],
    "default": ["created_at", "updated_at"],
}

# Columnas que NO se pueden setear desde el CRUD (aunque existan)
READONLY_COLS: Dict[str, List[str]] = {
    "usuarios": ["hashed_password", "created_at", "updated_at"],
    "default": ["created_at", "updated_at"],
}

# Labels mejores (UI)
LABELS: Dict[str, Dict[str, str]] = {
    "usuarios": {
        "id_usuario": "ID",
        "nombre": "Nombre",
        "email": "Email",
        "username": "Usuario",
        "telefono": "Teléfono",
        "cargo": "Cargo",
        "rol": "Rol",
        "avatar_url": "Avatar",
        "is_active": "Activo",
    },
    "estados_lead": {
        "id_estado": "ID",
        "nombre": "Estado",
        "color": "Color",
        "orden": "Orden",
        "is_active": "Activo",
    },
    "tipocliente": {
        "id_tipo_cliente": "ID",
        "nombre": "Tipo Cliente",
        "is_active": "Activo",
    },
    "marcas": {
        "id_marca": "ID",
        "nombre": "Marca",
        "marca": "Marca (alias)",
        "logo_path": "Logo (URL o ruta)",
        "logo_url": "Logo URL",
        "pdf_portada_url": "PDF Portada URL",
        "pdf_cotizacion_url": "PDF Cotización URL",
        "pdf_terminos_url": "PDF Términos URL",
        "pdf_banco_url": "PDF Banco URL",
        "form_token": "Form Token",
        "is_active": "Activo",
    },
    "productos": {
        "id_producto": "ID",
        "producto": "Producto",
        "marca": "Marca",
        "costo": "Costo",
        "descripcion": "Descripción",
        "orden": "Orden",
        "is_active": "Activo",
    },
    "comisiones": {
        "id_comision": "ID",
        "marca": "Marca",
        "rol": "Rol",
        "porcentaje": "Porcentaje",
        "is_active": "Activo",
    },
}


def _ensure_comisiones() -> None:
    with engine.connect() as cn:
        cn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS comisiones (
                    id_comision SERIAL PRIMARY KEY,
                    marca TEXT,
                    rol TEXT,
                    porcentaje NUMERIC(6,2) DEFAULT 0,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT now(),
                    updated_at TIMESTAMP DEFAULT now()
                )
                """
            )
        )
        cn.commit()


def _ensure_users_extra_cols() -> None:
    try:
        with engine.connect() as cn:
            cn.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS avatar_url TEXT"))
            cn.commit()
    except Exception:
        pass


def _ensure_marcas_assets_cols() -> None:
    """
    Asegura columnas usadas para assets de cotización por marca.
    (cPanel / ambientes antiguos pueden no tenerlas.)
    """
    try:
        with engine.begin() as cn:
            cn.execute(text("ALTER TABLE public.marcas ADD COLUMN IF NOT EXISTS logo_url TEXT"))
            cn.execute(text("ALTER TABLE public.marcas ADD COLUMN IF NOT EXISTS pdf_portada_url TEXT"))
            cn.execute(text("ALTER TABLE public.marcas ADD COLUMN IF NOT EXISTS pdf_cotizacion_url TEXT"))
            cn.execute(text("ALTER TABLE public.marcas ADD COLUMN IF NOT EXISTS pdf_terminos_url TEXT"))
            cn.execute(text("ALTER TABLE public.marcas ADD COLUMN IF NOT EXISTS pdf_banco_url TEXT"))
    except Exception:
        # No bloquear Settings si el hosting no permite ALTER.
        pass


@router.post("/marcas/import_quote_assets")
def import_marca_quote_assets(payload: Dict[str, Any] = Body(...), user: dict = Depends(get_current_user)):
    """
    Upsert de marca + URLs de assets (portada/cotización/términos/banco/logo).
    Pensado para que Admin no tenga que entrar manualmente a la BD.
    """
    if not (_is_privileged(_role(user)) or _is_privileged_user(user)):
        raise HTTPException(status_code=403, detail="Solo Admin/Operaciones.")

    name = (payload.get("marca") or payload.get("nombre") or payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="marca requerida")
    # Normaliza para evitar duplicados por mayúsculas/tildes.
    try:
        from backend.core.quote_assets import normalize_marca
        name = normalize_marca(name) or name.strip().upper()
    except Exception:
        name = name.strip().upper()

    # Acepta varias keys por conveniencia
    portada = (payload.get("pdf_portada_url") or payload.get("portada") or payload.get("portada_url") or "").strip() or None
    cotiz = (payload.get("pdf_cotizacion_url") or payload.get("cotizacion") or payload.get("fondo") or payload.get("cotizacion_url") or "").strip() or None
    term = (payload.get("pdf_terminos_url") or payload.get("terminos") or payload.get("terminos_url") or "").strip() or None
    banco = (payload.get("pdf_banco_url") or payload.get("banco") or payload.get("banco_url") or "").strip() or None
    logo = (payload.get("logo_url") or payload.get("logo") or payload.get("logo_path") or "").strip() or None

    def _drive_direct_if_needed(u: str | None) -> str | None:
        if not u:
            return None
        try:
            if "drive.google.com" in u:
                from backend.core.quote_assets import drive_direct
                return drive_direct(u)
        except Exception:
            pass
        return u

    portada = _drive_direct_if_needed(portada)
    cotiz = _drive_direct_if_needed(cotiz)
    term = _drive_direct_if_needed(term)
    banco = _drive_direct_if_needed(banco)
    logo = _drive_direct_if_needed(logo)

    _ensure_marcas_assets_cols()

    try:
        with engine.begin() as cn:
            # Evita race-condition si dos admins crean marca al mismo tiempo.
            # (Si el server no soporta advisory locks, simplemente seguimos.)
            try:
                cn.execute(text("SELECT pg_advisory_xact_lock(25032026)"))
            except Exception:
                pass

            # Detectar columnas reales (compat entre esquemas antiguos/nuevos)
            colmeta: Dict[str, Dict[str, Any]] = {}
            try:
                rows = cn.execute(
                    text(
                        """
                        SELECT column_name, data_type, udt_name, is_nullable, column_default
                        FROM information_schema.columns
                        WHERE table_schema='public' AND table_name='marcas'
                        """
                    )
                ).mappings().all()
                colmeta = {str(r["column_name"]): dict(r) for r in rows}
            except Exception:
                colmeta = {}
            colset = set(colmeta.keys())

            def _has(col: str) -> bool:
                return col in colset

            def _is_required(col: str) -> bool:
                m = colmeta.get(col) or {}
                return (str(m.get("is_nullable") or "").upper() == "NO") and (m.get("column_default") in (None, ""))

            def _default_for(col: str) -> Any:
                m = colmeta.get(col) or {}
                dt = str(m.get("data_type") or "").lower()
                if dt in ("text", "character varying", "varchar", "citext"):
                    return ""
                if dt in ("boolean",):
                    return True
                if dt in ("integer", "bigint", "smallint"):
                    return 0
                if dt in ("numeric", "double precision", "real", "decimal"):
                    return 0
                if dt in ("date",):
                    return datetime.utcnow().date()
                if "timestamp" in dt:
                    return datetime.utcnow()
                return ""

            # ¿Qué columna es el "nombre" real de la marca?
            name_expr = None
            if _has("nombre") and _has("marca"):
                name_expr = "COALESCE(nombre,marca)"
            elif _has("nombre"):
                name_expr = "nombre"
            elif _has("marca"):
                name_expr = "marca"
            else:
                # fallback ultra-legacy (no debería pasar)
                raise HTTPException(status_code=500, detail="Tabla marcas no tiene columna nombre/marca")

            # Buscar existente
            where = [f"UPPER({name_expr})=UPPER(:n)"]
            if _has("marca") and name_expr != "marca":
                where.append("UPPER(marca)=UPPER(:n)")
            if _has("nombre") and name_expr != "nombre":
                where.append("UPPER(nombre)=UPPER(:n)")

            mid = cn.execute(
                text(
                    f"""
                    SELECT id_marca
                    FROM public.marcas
                    WHERE {' OR '.join(where)}
                    ORDER BY id_marca DESC
                    LIMIT 1
                    """
                ),
                {"n": name},
            ).scalar()
            if mid:
                mid_i = int(mid)
                sets: List[str] = []
                params: Dict[str, Any] = {"id": mid_i}
                if _has("is_active"):
                    sets.append("is_active=TRUE")

                def _set_if(col: str, v: str | None) -> None:
                    if _has(col) and v:
                        sets.append(f"{_qident(col)}=:{col}")
                        params[col] = v

                _set_if("logo_url", logo)
                _set_if("logo_path", logo)
                _set_if("pdf_portada_url", portada)
                _set_if("pdf_cotizacion_url", cotiz)
                _set_if("pdf_terminos_url", term)
                _set_if("pdf_banco_url", banco)

                # form_token: si existe y está vacío, lo generamos para formularios
                if _has("form_token"):
                    tok_in = str(payload.get("form_token") or payload.get("token") or "").strip() or None
                    if tok_in:
                        sets.append(f'{_qident("form_token")}=:_tok')
                        params["_tok"] = tok_in
                    else:
                        cur_tok = ""
                        try:
                            cur_tok = str(
                                cn.execute(
                                    text("SELECT COALESCE(form_token,'') FROM public.marcas WHERE id_marca=:id"),
                                    {"id": mid_i},
                                ).scalar()
                                or ""
                            )
                        except Exception:
                            cur_tok = ""
                        if not cur_tok.strip():
                            sets.append(f'{_qident("form_token")}=:_tok')
                            params["_tok"] = secrets.token_urlsafe(18)

                if sets:
                    cn.execute(text(f"UPDATE public.marcas SET {', '.join(sets)} WHERE id_marca=:id"), params)
                return {"ok": True, "id_marca": mid_i, "updated_cols": [s.split("=")[0].strip() for s in sets]}

            # Insert nuevo
            cols: List[str] = []
            vals: Dict[str, Any] = {}
            # Ultra-legacy: hay instalaciones donde id_marca NO tiene default/serial.
            # En ese caso debemos asignar un id manualmente (MAX+1).
            if _has("id_marca") and _is_required("id_marca"):
                try:
                    next_id = int(cn.execute(text("SELECT COALESCE(MAX(id_marca),0)+1 FROM public.marcas")).scalar() or 1)
                except Exception:
                    next_id = 1
                cols.append("id_marca")
                vals["id_marca"] = next_id
            if _has("nombre"):
                cols.append("nombre")
                vals["nombre"] = name
            if _has("marca"):
                cols.append("marca")
                vals["marca"] = name
            if _has("is_active"):
                cols.append("is_active")
                vals["is_active"] = True
            if _has("form_token"):
                cols.append("form_token")
                tok = str(payload.get("form_token") or payload.get("token") or "").strip()
                vals["form_token"] = tok or secrets.token_urlsafe(18)
            if _has("logo_url") and logo:
                cols.append("logo_url")
                vals["logo_url"] = logo
            if _has("logo_path") and logo:
                cols.append("logo_path")
                vals["logo_path"] = logo
            if _has("pdf_portada_url"):
                cols.append("pdf_portada_url")
                vals["pdf_portada_url"] = portada
            if _has("pdf_cotizacion_url"):
                cols.append("pdf_cotizacion_url")
                vals["pdf_cotizacion_url"] = cotiz
            if _has("pdf_terminos_url"):
                cols.append("pdf_terminos_url")
                vals["pdf_terminos_url"] = term
            if _has("pdf_banco_url"):
                cols.append("pdf_banco_url")
                vals["pdf_banco_url"] = banco

            # Completa columnas NOT NULL sin default (legacy)
            for col in sorted(colset):
                if col in ("id_marca",) or col in vals:
                    continue
                if not _is_required(col):
                    continue
                cols.append(col)
                if col in ("nombre", "marca"):
                    vals[col] = name
                elif col == "form_token":
                    vals[col] = secrets.token_urlsafe(18)
                else:
                    vals[col] = _default_for(col)

            if not cols:
                raise HTTPException(status_code=500, detail="No hay columnas insertables en marcas")

            cols_sql = ", ".join([_qident(c) for c in cols])
            params_sql = ", ".join([f":{c}" for c in cols])
            cn.execute(text(f"INSERT INTO public.marcas({cols_sql}) VALUES ({params_sql})"), vals)

            mid2 = cn.execute(
                text(
                    f"""
                    SELECT id_marca
                    FROM public.marcas
                    WHERE UPPER({name_expr})=UPPER(:n)
                    ORDER BY id_marca DESC
                    LIMIT 1
                    """
                ),
                {"n": name},
            ).scalar()
            return {"ok": True, "id_marca": int(mid2 or 0), "inserted_cols": cols}
    except HTTPException:
        raise
    except DBAPIError as e:
        msg = str(getattr(e, "orig", "") or e).strip()
        raise HTTPException(status_code=400, detail=f"No pude importar assets. {msg[:240]}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"import_quote_assets failed: {type(e).__name__}: {str(e)[:240]}")


@router.post("/marcas/refresh_drive_assets")
def refresh_drive_assets(payload: Dict[str, Any] = Body(...), user: dict = Depends(get_current_user)):
    """
    Fuerza refresh de assets desde la carpeta Drive configurada para la marca.
    Sirve cuando se reemplazan imágenes en Drive y se quiere ver el cambio altiro.
    """
    if not (_is_privileged(_role(user)) or _is_privileged_user(user)):
        raise HTTPException(status_code=403, detail="Solo Admin/Operaciones.")

    marca_in = (payload.get("marca") or payload.get("nombre") or payload.get("name") or "").strip()
    if not marca_in:
        raise HTTPException(status_code=400, detail="marca requerida")
    try:
        from backend.core.quote_assets import DRIVE_ASSET_FOLDERS, normalize_marca
        from backend.core.drive_assets import resolve_brand_assets_from_folder
    except Exception:
        raise HTTPException(status_code=500, detail="Drive assets no disponible en este servidor")

    mkey = normalize_marca(marca_in)
    folder_id = DRIVE_ASSET_FOLDERS.get(mkey, "") or ""
    if not folder_id:
        raise HTTPException(status_code=404, detail=f"Marca sin carpeta Drive configurada: {mkey}")

    assets = resolve_brand_assets_from_folder(mkey, folder_id, refresh=True, ttl_seconds=0)
    return {"ok": True, "marca": mkey, "folder_id": folder_id, "assets": assets}

def _require_admin(user: dict) -> None:
    # Si quieres forzar Admin real:
    # if not user or (user.get("rol") not in ("Admin", "SuperAdmin")):
    #     raise HTTPException(403, "Solo Admin")
    # Por ahora: no bloqueo en dev.
    return


def _ensure_usuarios_marcas() -> None:
    """
    Tabla puente: qué marcas puede ver un usuario (ejecutivos).
    Si no existe, el ejecutivo queda con 0 leads.
    """
    try:
        with engine.begin() as cn:
            cn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS usuarios_marcas(
                      id_usuario INTEGER NOT NULL REFERENCES usuarios(id_usuario) ON DELETE CASCADE,
                      id_marca   INTEGER NOT NULL REFERENCES marcas(id_marca)   ON DELETE CASCADE,
                      created_at TIMESTAMP DEFAULT now(),
                      PRIMARY KEY (id_usuario, id_marca)
                    )
                    """
                )
            )
    except Exception:
        # no bloqueamos settings si falla, pero luego devolverá error al guardar
        pass


def _role(user: dict) -> str:
    return (user.get("role") or user.get("rol") or "").upper()


def _role_key(user: dict) -> str:
    """
    Normaliza el rol para comparaciones robustas.
    Evita casos reales donde el rol viene como "Administrador" o con tildes/espacios.
    """
    raw = str(user.get("role") or user.get("rol") or "").strip().lower()
    if not raw:
        return ""
    try:
        import unicodedata

        raw = unicodedata.normalize("NFD", raw)
        raw = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
    except Exception:
        pass
    raw = "".join(ch for ch in raw if ch.isalnum())
    return raw


def _is_privileged(role: str) -> bool:
    """
    Roles que ven todo (no filtramos por marcas).
    Si agregas nuevos roles "no ejecutivos", ponlos aquí.
    """
    return role in (
        "ADMIN",
        "SUPERADMIN",
        "JEFE DE OPERACIONES",
        "OPERACIONES",
        "COMPRAS",
        "BODEGUERO",
        "MICE",
    )


def _is_privileged_user(user: dict) -> bool:
    rk = _role_key(user)
    if not rk:
        return False
    return (
        ("admin" in rk)
        or ("superadmin" in rk)
        or ("operac" in rk)
        or ("compra" in rk)
        or ("bodeg" in rk)
        or ("mice" in rk)
    )


def _user_marcas_ids(user: dict) -> List[int]:
    # 1) token
    ids = []
    for x in (user.get("marcas") or []):
        try:
            ids.append(int(x))
        except Exception:
            pass
    ids = [i for i in ids if i > 0]
    if ids:
        return sorted(set(ids))

    # 2) fallback DB
    uid = user.get("id_usuario") or user.get("id") or user.get("user_id")
    try:
        uid = int(uid)
    except Exception:
        return []

    _ensure_usuarios_marcas()
    with engine.connect() as cn:
        rows = cn.execute(
            text("SELECT id_marca FROM usuarios_marcas WHERE id_usuario=:id ORDER BY id_marca"),
            {"id": uid},
        ).fetchall()
    return [int(r[0]) for r in rows if r and r[0] is not None]


def _resolve_table(entity: str) -> str:
    e = (entity or "").strip().lower()
    if e == "comisiones":
        _ensure_comisiones()
    if e in ENTITY_MAP:
        return ENTITY_MAP[e]
    raise HTTPException(status_code=404, detail="Entidad no soportada")


def _qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'

def _norm_expr(expr: str) -> str:
    """
    Normaliza texto para búsquedas sin acentos (es-CL).
    """
    return f"translate(lower({expr}), 'áéíóúüñÁÉÍÓÚÜÑ', 'aeiouunAEIOUUN')"

def _norm_param(param: str) -> str:
    return f"translate(lower({param}), 'áéíóúüñÁÉÍÓÚÜÑ', 'aeiouunAEIOUUN')"

def _canon_marca_code_py(s: str) -> str | None:
    """
    Canoniza nombres de marca a los códigos usados por el CRM (texto en productos/leads).
    Retorna uno de: CAMALEON | DEL SABOR | GOURMET | EXPRESS
    """
    if not s:
        return None
    key = (
        str(s)
        .strip()
        .lower()
        .translate(str.maketrans("áéíóúüñ", "aeiouun"))
    )
    # Normaliza fuerte: solo alfanumérico
    key = "".join(ch for ch in key if ch.isalnum())
    if not key:
        return None
    # Nota: en data real hemos visto "Camaleón" escrito de muchas formas
    # (acentos raros, encoding, etc) que terminan en keys tipo "camalen".
    # Por eso usamos un match más laxo: "camale".
    if "camale" in key:
        return "CAMALEON"
    # Similar: algunos casos quedan como "delsabr" (por tildes/encoding),
    # usamos "delsab" para capturar variaciones seguras.
    if "delsab" in key:
        return "DEL SABOR"
    if "gourmet" in key:
        return "GOURMET"
    if "express" in key:
        return "EXPRESS"
    if "brontos" in key:
        return "BRONTOS"
    return None


def _allowed_marca_codes(user: dict) -> List[str]:
    role = _role(user)
    if _is_privileged(role) or _is_privileged_user(user):
        return ["GOURMET", "CAMALEON", "DEL SABOR", "EXPRESS", "BRONTOS"]
    ids = _user_marcas_ids(user)
    if not ids:
        return []
    try:
        with engine.connect() as cn:
            rows = cn.execute(
                text("SELECT nombre, marca FROM marcas WHERE id_marca = ANY(:m)"),
                {"m": ids},
            ).fetchall()
        out: List[str] = []
        for r in rows:
            label = (r[0] or r[1] or "")
            code = _canon_marca_code_py(str(label))
            if code:
                out.append(code)
        return sorted(set(out))
    except Exception:
        return []


def _normalize_productos_marcas_once() -> None:
    """
    Normaliza `public.productos.marca` a códigos en MAYÚSCULA:
    GOURMET | CAMALEON | DEL SABOR | EXPRESS

    Se ejecuta como max 1 vez por día (guardado en system_kv) y es idempotente.
    """
    _normalize_productos_marcas(force=False)


def _normalize_productos_marcas(force: bool = False) -> Dict[str, Any]:
    """
    Normaliza `public.productos.marca` a MAYÚSCULA con los 4 códigos oficiales:
    GOURMET | CAMALEON | DEL SABOR | EXPRESS

    En entornos reales hay dos causas de “no hay productos para la marca”:
    1) productos.marca viene con textos variados (acentos/puntos/sufijos)
    2) productos.id_marca puede estar (pero marca texto quedó vieja) o viceversa

    Este job:
    - Si existe id_marca en productos: setea marca por el nombre de marcas (robusto).
    - Siempre intenta normalizar por el texto actual como fallback.
    - En modo normal corre 1 vez por día; force=True lo ejecuta siempre.
    """
    out = {
        "ok": True,
        "force": bool(force),
        "has_id_marca": False,
        "total_productos": None,
        "null_marca": None,
        "updated_by_id_marca": 0,
        "updated_by_text": 0,
    }
    try:
        with engine.begin() as cn:
            # lock para evitar concurrencia
            got = bool(cn.execute(text("SELECT pg_try_advisory_lock(25032026)")).scalar())
            if not got:
                return {**out, "ok": False, "detail": "lock_busy"}
            try:
                cn.execute(
                    text(
                        """
                        CREATE TABLE IF NOT EXISTS system_kv (
                          key TEXT PRIMARY KEY,
                          value TEXT NOT NULL,
                          updated_at TIMESTAMP DEFAULT now()
                        )
                        """
                    )
                )
                today = datetime.utcnow().date().isoformat()
                if not force:
                    last = cn.execute(
                        text("SELECT value FROM system_kv WHERE key='normalize_productos_marcas_last_run' LIMIT 1")
                    ).scalar()
                    if (last or "") == today:
                        return {**out, "skipped": True, "reason": "already_ran_today"}

                # Normalización fuerte en SQL (sin depender de unaccent):
                def _norm(expr: str) -> str:
                    return (
                        "regexp_replace("
                        f"translate(lower(coalesce({expr},'')), 'áéíóúüñÁÉÍÓÚÜÑ', 'aeiouunAEIOUUN')"
                        ", '[^a-z0-9]+', '', 'g')"
                    )

                # 1) Por id_marca (si existe): usa nombre/marca de la tabla marcas.
                has_id_marca = bool(
                    cn.execute(
                        text(
                            """
                            SELECT 1
                            FROM information_schema.columns
                            WHERE table_schema='public' AND table_name='productos' AND column_name='id_marca'
                            """
                        )
                    ).first()
                )
                out["has_id_marca"] = has_id_marca

                try:
                    out["total_productos"] = int(cn.execute(text("SELECT COUNT(*) FROM public.productos")).scalar() or 0)
                    out["null_marca"] = int(
                        cn.execute(text("SELECT COUNT(*) FROM public.productos WHERE marca IS NULL OR btrim(marca)=''")).scalar()
                        or 0
                    )
                except Exception:
                    pass
                if has_id_marca:
                    mkey = _norm("coalesce(m.nombre, m.marca, '')")
                    canon_case = (
                        f"CASE "
                        f"WHEN {mkey} LIKE '%camale%' THEN 'CAMALEON' "
                        f"WHEN {mkey} LIKE '%delsab%' THEN 'DEL SABOR' "
                        f"WHEN {mkey} LIKE '%gourmet%' THEN 'GOURMET' "
                        f"WHEN {mkey} LIKE '%express%' THEN 'EXPRESS' "
                        f"ELSE NULL END"
                    )
                    res = cn.execute(
                        text(
                            f"""
                            UPDATE public.productos p
                            SET marca = x.canon
                            FROM (
                              SELECT m.id_marca, {canon_case} AS canon
                              FROM public.marcas m
                            ) x
                            WHERE p.id_marca = x.id_marca
                              AND x.canon IS NOT NULL
                              AND upper(coalesce(p.marca,'')) <> x.canon
                            """
                        )
                    )
                    out["updated_by_id_marca"] = int(res.rowcount or 0)

                # 2) Fallback por texto actual en productos.marca
                pkey = _norm("p.marca")
                rules = [
                    ("CAMALEON", "camale"),
                    ("DEL SABOR", "delsab"),
                    ("GOURMET", "gourmet"),
                    ("EXPRESS", "express"),
                ]
                total_text = 0
                for canon, key in rules:
                    res = cn.execute(
                        text(
                            f"""
                            UPDATE public.productos p
                            SET marca=:canon
                            WHERE {pkey} LIKE ('%' || :key || '%')
                              AND upper(coalesce(p.marca,'')) <> :canon
                            """
                        ),
                        {"canon": canon, "key": key},
                    )
                    total_text += int(res.rowcount or 0)
                out["updated_by_text"] = total_text

                cn.execute(
                    text(
                        """
                        INSERT INTO system_kv(key,value,updated_at)
                        VALUES ('normalize_productos_marcas_last_run', :v, now())
                        ON CONFLICT(key) DO UPDATE SET value=:v, updated_at=now()
                        """
                    ),
                    {"v": today},
                )
            finally:
                try:
                    cn.execute(text("SELECT pg_advisory_unlock(25032026)"))
                except Exception:
                    pass
        return out
    except Exception:
        # nunca debe romper Settings
        return {**out, "ok": False}


@router.post("/productos/normalize_marcas")
def normalize_productos_marcas(
    force: bool = Query(default=False),
    user: dict = Depends(get_current_user),
):
    # Solo roles privilegiados
    if not (_is_privileged(_role(user)) or _is_privileged_user(user)):
        raise HTTPException(status_code=403, detail="Solo Admin/Operaciones puede normalizar marcas.")
    return _normalize_productos_marcas(force=force)


@router.get("/productos/marca_stats")
def productos_marca_stats(user: dict = Depends(get_current_user)):
    # Solo roles privilegiados
    if not (_is_privileged(_role(user)) or _is_privileged_user(user)):
        raise HTTPException(status_code=403, detail="Solo Admin/Operaciones.")

    out: Dict[str, Any] = {"ok": True}
    with engine.connect() as cn:
        out["total_productos"] = int(cn.execute(text("SELECT COUNT(*) FROM public.productos")).scalar() or 0)
        out["null_marca"] = int(
            cn.execute(text("SELECT COUNT(*) FROM public.productos WHERE marca IS NULL OR btrim(marca)=''")).scalar() or 0
        )
        # Top valores de marca (texto)
        out["by_marca"] = [
            {"marca": r[0], "n": int(r[1])}
            for r in cn.execute(
                text(
                    """
                    SELECT COALESCE(NULLIF(btrim(marca),''), '(NULL/EMPTY)') AS marca, COUNT(*) AS n
                    FROM public.productos
                    GROUP BY 1
                    ORDER BY n DESC, marca ASC
                    LIMIT 80
                    """
                )
            ).fetchall()
        ]

        has_id_marca = bool(
            cn.execute(
                text(
                    """
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema='public' AND table_name='productos' AND column_name='id_marca'
                    """
                )
            ).first()
        )
        out["has_id_marca"] = has_id_marca
        if has_id_marca:
            out["by_id_marca"] = [
                {"id_marca": int(r[0]) if r[0] is not None else None, "n": int(r[1])}
                for r in cn.execute(
                    text(
                        """
                        SELECT id_marca, COUNT(*) AS n
                        FROM public.productos
                        GROUP BY id_marca
                        ORDER BY n DESC, id_marca NULLS LAST
                        LIMIT 80
                        """
                    )
                ).fetchall()
            ]

    return out

def _pk_for(table: str) -> str:
    """
    PK por information_schema (NO usa ::regclass, NO rompe en Postgres).
    """
    q = text(
        """
        SELECT kcu.column_name AS pk
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        WHERE tc.constraint_type = 'PRIMARY KEY'
          AND tc.table_schema = 'public'
          AND tc.table_name = :t
        ORDER BY kcu.ordinal_position
        LIMIT 1
        """
    )
    with engine.connect() as cn:
        r = cn.execute(q, {"t": table}).fetchone()
        if not r:
            raise HTTPException(500, detail=f"No PK para {table}")
        return _as_text(r._mapping["pk"]).strip()


def _cols_for(table: str) -> List[Dict[str, Any]]:
    q = text(
        """
        SELECT
          column_name,
          data_type,
          is_nullable,
          column_default
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name=:t
        ORDER BY ordinal_position
        """
    )
    with engine.connect() as cn:
        rows = cn.execute(q, {"t": table}).mappings().all()
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        # En algunos ambientes el driver devuelve bytes; normalizamos para no romper el CRUD.
        if "column_name" in d:
            d["column_name"] = _as_text(d.get("column_name")).strip()
        if "data_type" in d:
            d["data_type"] = _as_text(d.get("data_type")).strip()
        if "is_nullable" in d:
            d["is_nullable"] = _as_text(d.get("is_nullable")).strip()
        if "column_default" in d and d.get("column_default") is not None:
            d["column_default"] = _as_text(d.get("column_default"))
        out.append(d)
    return out


def _has_col(cols: List[Dict[str, Any]], name: str) -> bool:
    return any(c["column_name"] == name for c in cols)


def _is_text_type(dt: str) -> bool:
    dt = (dt or "").lower()
    return dt in ("text", "character varying", "varchar", "citext")


def _hidden_for(table: str) -> List[str]:
    h: List[str] = []
    h.extend(HIDDEN_COLS.get("default", []))
    h.extend(HIDDEN_COLS.get(table, []))
    out: List[str] = []
    for x in h:
        if x not in out:
            out.append(x)
    return out


def _readonly_for(table: str) -> List[str]:
    h: List[str] = []
    h.extend(READONLY_COLS.get("default", []))
    h.extend(READONLY_COLS.get(table, []))
    out: List[str] = []
    for x in h:
        if x not in out:
            out.append(x)
    return out


def _labels_for(table: str) -> Dict[str, str]:
    return LABELS.get(table, {})


def _ui_hint(table: str, col: Dict[str, Any]) -> Dict[str, Any]:
    """
    Hint para el frontend: widget recomendado.
    """
    name = col["column_name"]
    dt = (col["data_type"] or "").lower()

    if name == "is_active":
        return {"widget": "toggle", "trueLabel": "Activo", "falseLabel": "Inactivo"}

    if table == "estados_lead" and name == "color":
        return {"widget": "color"}

    if table == "usuarios" and name == "rol":
        return {"widget": "select", "source": "roles", "labelField": "nombre", "valueField": "nombre"}

    if table == "comisiones" and name == "marca":
        return {"widget": "select", "source": "marcas", "labelField": "nombre", "valueField": "nombre"}

    if table == "comisiones" and name == "rol":
        return {"widget": "select", "source": "roles", "labelField": "nombre", "valueField": "nombre"}

    if table == "productos" and name == "id_marca":
        return {"widget": "select", "source": "marcas", "labelField": "nombre", "valueField": "id_marca"}

    if dt in ("integer", "bigint", "numeric", "double precision", "real"):
        return {"widget": "number"}

    if dt in ("boolean",):
        return {"widget": "toggle"}

    if _is_text_type(dt):
        return {"widget": "text"}

    if dt in ("date",):
        return {"widget": "date"}

    if "timestamp" in dt:
        return {"widget": "datetime"}

    return {"widget": "text"}


def _fetch_choices(source: str) -> List[Dict[str, Any]]:
    src = _resolve_table(source)
    cols = _cols_for(src)
    pk = _pk_for(src)

    if _has_col(cols, "nombre"):
        label_col = "nombre"
    elif _has_col(cols, "marca"):
        # Legacy: public.marcas suele usar columna `marca` como nombre.
        label_col = "marca"
    elif _has_col(cols, "tipo"):
        label_col = "tipo"
    else:
        label_col = pk

    where_sql = ""
    if _has_col(cols, "is_active"):
        where_sql = 'WHERE "is_active"=TRUE '

    q = text(
        f"SELECT {_qident(pk)} AS id, {_qident(label_col)} AS nombre "
        f"FROM {_qident(src)} "
        f"{where_sql}"
        f"ORDER BY {_qident(label_col)}"
    )

    with engine.connect() as cn:
        rows = cn.execute(q).mappings().all()

    return [dict(r) for r in rows]


@router.get("/meta/{entity}")
def meta(entity: str, user: dict = Depends(get_current_user)):
    try:
        _require_admin(user)

        table = _resolve_table(entity)
        if table == "usuarios":
            _ensure_users_extra_cols()
        cols = _cols_for(table)
        pk = _pk_for(table)

        hidden = _hidden_for(table)
        readonly = _readonly_for(table)
        labels = _labels_for(table)

        out_cols: List[Dict[str, Any]] = []
        for c in cols:
            name = c["column_name"]
            if name in hidden:
                continue
            c2 = dict(c)
            c2["label"] = labels.get(name, name.replace("_", " ").title())
            c2["readonly"] = (name in readonly) or (name == pk)
            c2["ui"] = _ui_hint(table, c)
            out_cols.append(c2)

        choices: Dict[str, List[Dict[str, Any]]] = {}
        for c in out_cols:
            ui = c.get("ui") or {}
            if ui.get("widget") == "select":
                src = ui.get("source")
                if src and src not in choices:
                    choices[src] = _fetch_choices(src)

        return {
            "ok": True,
            "entity": entity,
            "table": table,
            "pk": pk,
            "columns": out_cols,
            "choices": choices,
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback

        raise HTTPException(
            500,
            detail={
                "where": "settings.meta",
                "entity": entity,
                "type": e.__class__.__name__,
                "msg": str(e),
                "trace": traceback.format_exc().splitlines()[-25:],
            },
        )


@router.get("/{entity}")
@router.get("/rows/{entity}")  # compat
def list_rows(
    entity: str,
    limit: int = Query(25, ge=1, le=500),
    offset: int = Query(0, ge=0),
    q: str = Query("", max_length=120),
    active_only: bool = Query(False),
    user: dict = Depends(get_current_user),
):
    _require_admin(user)

    table = _resolve_table(entity)
    if table == "usuarios":
        _ensure_users_extra_cols()
    if table == "productos":
        # Esto evita que ejecutivos pierdan catálogos por marcas escritas distinto.
        _normalize_productos_marcas_once()
    cols = _cols_for(table)
    pk = _pk_for(table)
    hidden = set(_hidden_for(table))

    colnames = [c["column_name"] for c in cols if c["column_name"] not in hidden]
    if not colnames:
        raise HTTPException(500, detail="No hay columnas visibles")

    where_parts: List[str] = []
    params: Dict[str, Any] = {"limit": limit, "offset": offset}

    # Productos (venta): ejecutivos ven SOLO sus marcas (paginación incluida).
    if table == "productos":
        role = _role(user)
        if not _is_privileged(role):
            allowed_codes = _allowed_marca_codes(user)
            if not allowed_codes:
                return {"ok": True, "total": 0, "items": []}

            if _has_col(cols, "id_marca"):
                marcas_ids = _user_marcas_ids(user)
                where_parts.append(f'{_qident("id_marca")} = ANY(:_user_marcas)')
                params["_user_marcas"] = marcas_ids
            elif _has_col(cols, "marca"):
                where_parts.append(f"upper(coalesce({_qident('marca')},'')) = ANY(:_user_marcas_codes)")
                params["_user_marcas_codes"] = allowed_codes
            else:
                return {"ok": True, "total": 0, "items": []}

    if active_only and _has_col(cols, "is_active"):
        where_parts.append(f'{_qident("is_active")} = TRUE')

    qtxt = (q or "").strip()
    if qtxt:
        params["q"] = f"%{qtxt}%"
        text_cols = [
            c["column_name"]
            for c in cols
            if _is_text_type(c["data_type"]) and c["column_name"] not in hidden
        ]
        if text_cols:
            ors = " OR ".join([f'{_norm_expr(_qident(c))} LIKE {_norm_param(":q")}' for c in text_cols])
            where_parts.append(f"({ors})")

    where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    sel_cols = ", ".join([_qident(c) for c in colnames])

    order_sql = f'ORDER BY {_qident(pk)} DESC'
    if table == "productos" and _has_col(cols, "orden"):
        order_sql = f'ORDER BY {_qident("orden")} DESC NULLS LAST, {_qident(pk)} DESC'
    sql_items = text(
        f'SELECT {sel_cols} FROM {_qident(table)} {where_sql} '
        f'{order_sql} LIMIT :limit OFFSET :offset'
    )
    sql_total = text(f'SELECT COUNT(*) AS n FROM {_qident(table)} {where_sql}')

    with engine.connect() as cn:
        total = int(cn.execute(sql_total, params).scalar_one())
        items = [dict(r) for r in cn.execute(sql_items, params).mappings().all()]

    return {"ok": True, "total": total, "items": items}


@router.post("/{entity}")
@router.post("/rows/{entity}")  # compat
def create_row(
    entity: str,
    payload: Dict[str, Any] = Body(...),
    user: dict = Depends(get_current_user),
):
    _require_admin(user)

    table = _resolve_table(entity)
    if table == "usuarios":
        _ensure_users_extra_cols()
    cols = _cols_for(table)
    pk = _pk_for(table)
    hidden = set(_hidden_for(table))
    readonly = set(_readonly_for(table))

    allowed = {
        c["column_name"]
        for c in cols
        if c["column_name"] not in hidden
        and c["column_name"] not in readonly
        and c["column_name"] != pk
    }
    raw = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
    data = {}
    for k, v in (raw or {}).items():
        if k not in allowed:
            continue
        if v in ("", None):
            continue
        data[k] = v

    # usuarios: permitir password plano (lo hasheamos)
    if table == "usuarios":
        pw = (raw or {}).get("password")
        if pw:
            data["hashed_password"] = hash_password(pw)

    if not data:
        raise HTTPException(400, detail="Nada para crear")

    if table == "usuarios" and "hashed_password" not in data:
        raise HTTPException(400, detail="Password requerido")

    # roles: evitar error genérico por UNIQUE(nombre)
    if table == "roles" and data.get("nombre"):
        try:
            nm = str(data.get("nombre") or "").strip()
            if nm:
                with engine.connect() as cn:
                    exists = cn.execute(
                        text("SELECT 1 FROM roles WHERE lower(nombre)=lower(:n) LIMIT 1"),
                        {"n": nm},
                    ).scalar()
                if exists:
                    raise HTTPException(400, detail="Ya existe un rol con ese nombre.")
        except HTTPException:
            raise
        except Exception:
            pass

    # roles: algunos entornos tienen id_rol NOT NULL sin default/serial.
    # Si detectamos ese caso, generamos un id_rol (max+1) para que el insert funcione.
    # Ideal: arreglar schema (SEQUENCE/IDENTITY). Esto es un fallback seguro en baja concurrencia.
    if table == "roles":
        try:
            id_col = next((c for c in cols if c["column_name"] == "id_rol"), None)
            if id_col and not (id_col.get("column_default") or ""):
                with engine.connect() as cn:
                    next_id = cn.execute(text("SELECT COALESCE(MAX(id_rol),0)+1 FROM roles")).scalar()
                if next_id is not None:
                    data["id_rol"] = int(next_id)
        except Exception:
            pass

    # marcas: solo exigimos nombre (los assets se pueden cargar/después cachear)
    if table == "marcas":
        required = []
        if "nombre" in allowed:
            required.append("nombre")
        elif "marca" in allowed:
            required.append("marca")
        missing = [f for f in required if not (raw or {}).get(f)]
        if missing:
            raise HTTPException(400, detail=f"Falta campo obligatorio: {', '.join(missing)}")

    # Defaults seguros
    if "orden" in allowed and "orden" not in data:
        data["orden"] = 0

    # roles: algunos entornos tienen created_at/updated_at NOT NULL sin default.
    # Aunque estén ocultas en Settings, las llenamos server-side para que el INSERT no falle.
    if table == "roles":
        col_names = {c["column_name"] for c in cols}
        now_ts = datetime.now()
        if "created_at" in col_names and "created_at" not in data:
            data["created_at"] = now_ts
        if "updated_at" in col_names and "updated_at" not in data:
            data["updated_at"] = now_ts

    # usuarios: map rol -> id_rol si falta
    if table == "usuarios" and "id_rol" not in data and data.get("rol"):
        try:
            with engine.connect() as cn:
                rid = cn.execute(
                    text("SELECT id_rol FROM roles WHERE nombre=:n LIMIT 1"),
                    {"n": data.get("rol")},
                ).scalar()
            if rid is not None:
                data["id_rol"] = int(rid)
        except Exception:
            pass

    # marcas: set marca/logo_path si faltan
    if table == "marcas":
        try:
            from backend.core.quote_assets import logo_for, normalize_marca
            name = data.get("nombre") or data.get("marca") or ""
            if "marca" in allowed and "marca" not in data and name:
                data["marca"] = name
            if data.get("logo_url") and not data.get("logo_path"):
                data["logo_path"] = data.get("logo_url")
            if "logo_path" in allowed and not data.get("logo_path"):
                key = normalize_marca(name)
                if key:
                    data["logo_path"] = logo_for(key, prefer_local=True)
        except Exception:
            pass

    # productos: marca debe ser elegible para el usuario + canonizada a MAYÚSCULA
    if table == "productos":
        allowed_codes = _allowed_marca_codes(user)
        role = _role(user)
        if not _is_privileged(role) and not allowed_codes:
            raise HTTPException(403, detail="No tienes marcas asignadas para crear productos.")

        raw_marca = (raw or {}).get("marca") or data.get("marca") or ""
        code = _canon_marca_code_py(str(raw_marca)) or (str(raw_marca).strip().upper() if raw_marca else None)

        if not _is_privileged(role):
            if len(allowed_codes) == 1:
                code = allowed_codes[0]
            else:
                if not code:
                    raise HTTPException(400, detail="Marca requerida")
                if code not in allowed_codes:
                    raise HTTPException(403, detail="No puedes crear productos para esa marca.")
        else:
            # Admin: si coincide con nuestras marcas conocidas, la canonizamos; si no, la dejamos en upper.
            code = code or None

        if not code:
            raise HTTPException(400, detail="Marca requerida")
        data["marca"] = str(code).upper()

    keys = list(data.keys())
    cols_sql = ", ".join([_qident(k) for k in keys])
    vals_sql = ", ".join([f":{k}" for k in keys])

    q = text(
        f'INSERT INTO {_qident(table)} ({cols_sql}) VALUES ({vals_sql}) RETURNING {_qident(pk)}'
    )
    try:
        with engine.begin() as cn:
            # UX: usuarios suele chocar con email/username UNIQUE.
            # En vez de devolver el SQL crudo, detectamos el caso y damos un mensaje útil.
            if table == "usuarios":
                # Busca por email/username (case-insensitive). Si existe y está inactivo, lo reactivamos.
                email = (data.get("email") or raw.get("email") or "").strip()
                username = (data.get("username") or raw.get("username") or "").strip()

                if email:
                    existing = cn.execute(
                        text('SELECT id_usuario, is_active FROM usuarios WHERE lower(email)=lower(:e) LIMIT 1'),
                        {"e": email},
                    ).mappings().first()
                    if existing:
                        if existing.get("is_active") is False:
                            # Reactiva + actualiza campos entregados.
                            uid = int(existing["id_usuario"])
                            sets = []
                            params = {"id": uid}
                            for k, v in data.items():
                                if k in ("hashed_password",):  # se permite
                                    sets.append(f'{_qident(k)}=:{k}')
                                    params[k] = v
                                elif k in ("nombre", "avatar_url", "email", "username", "telefono", "rol", "cargo", "id_rol"):
                                    sets.append(f'{_qident(k)}=:{k}')
                                    params[k] = v
                            sets.append('"is_active"=TRUE')
                            sets.append('"updated_at"=now()')
                            cn.execute(text(f'UPDATE usuarios SET {", ".join(sets)} WHERE id_usuario=:id'), params)
                            new_id = uid
                            # Seguimos flujo normal (envío de correo más abajo)
                        else:
                            raise HTTPException(
                                400,
                                detail="Ya existe un usuario con ese correo (activo). Búscalo y edítalo en vez de crearlo de nuevo.",
                            )

                if "new_id" not in locals() and username:
                    existing_u = cn.execute(
                        text('SELECT id_usuario, is_active FROM usuarios WHERE lower(username)=lower(:u) LIMIT 1'),
                        {"u": username},
                    ).mappings().first()
                    if existing_u:
                        if existing_u.get("is_active") is False:
                            uid = int(existing_u["id_usuario"])
                            sets = []
                            params = {"id": uid}
                            for k, v in data.items():
                                if k in ("hashed_password",):
                                    sets.append(f'{_qident(k)}=:{k}')
                                    params[k] = v
                                elif k in ("nombre", "avatar_url", "email", "username", "telefono", "rol", "cargo", "id_rol"):
                                    sets.append(f'{_qident(k)}=:{k}')
                                    params[k] = v
                            sets.append('"is_active"=TRUE')
                            sets.append('"updated_at"=now()')
                            cn.execute(text(f'UPDATE usuarios SET {", ".join(sets)} WHERE id_usuario=:id'), params)
                            new_id = uid
                        else:
                            raise HTTPException(
                                400,
                                detail="Ya existe un usuario con ese username (activo). Búscalo y edítalo en vez de crearlo de nuevo.",
                            )

            if "new_id" not in locals():
                new_id = cn.execute(q, data).scalar_one()
    except HTTPException:
        raise
    except DBAPIError as e:
        # Evita filtrar SQL/stack al frontend (mensaje específico por tabla)
        if table == "roles":
            hint = ""
            try:
                hint = str(getattr(e, "orig", "") or "").strip().splitlines()[0]
            except Exception:
                hint = ""
            msg = "No pude crear el rol. Revisa si ya existe o si faltan datos obligatorios."
            if hint:
                msg += f" ({hint})"
            raise HTTPException(400, detail=msg)
        raise HTTPException(400, detail="No pude crear el registro. Revisa si ya existe (correo/usuario) o si faltan datos obligatorios.")
    except Exception:
        raise HTTPException(400, detail="No pude crear el registro (error interno).")

    # usuarios: enviar credenciales por correo al crear (por defecto)
    email_sent = False
    email_error = None
    if table == "usuarios":
        try:
            to_email = (data.get("email") or raw.get("email") or "").strip()
            plain_pw = (raw or {}).get("password") or ""
            if to_email and plain_pw:
                app_url = (os.getenv("APP_URL") or "").rstrip("/")
                login_url = f"{app_url}/crm/web/login.html" if app_url else "/crm/web/login.html"
                subject = "Tus credenciales de acceso (Green Diamond CRM)"
                usuario = (data.get("username") or raw.get("username") or "").strip() or to_email
                nombre = (data.get("nombre") or raw.get("nombre") or "").strip() or usuario

                plain_text = (
                    f"Hola {nombre},\n\n"
                    "Se creó tu usuario para el CRM.\n\n"
                    f"Usuario: {usuario}\n"
                    f"Clave: {plain_pw}\n\n"
                    f"Ingreso: {login_url}\n\n"
                    "Puedes cambiar tu clave desde el login una vez que ingreses.\n"
                )
                html = (
                    f"<p>Hola <b>{nombre}</b>,</p>"
                    "<p>Se creó tu usuario para el CRM.</p>"
                    f"<p><b>Usuario:</b> {usuario}<br/>"
                    f"<b>Clave:</b> {plain_pw}</p>"
                    f"<p><b>Ingreso:</b> {login_url}</p>"
                    "<p>Puedes cambiar tu clave desde el login una vez que ingreses.</p>"
                )
                send_email(to_email, subject, plain_text, html=html)
                email_sent = True
        except EmailConfigError as e:
            email_error = str(e)
        except Exception as e:
            email_error = f"SMTP error: {e}"

    return {"ok": True, "id": new_id, "email_sent": email_sent, "email_error": email_error}


@router.put("/{entity}/{row_id}")
@router.put("/rows/{entity}/{row_id}")  # compat
def update_row(
    entity: str,
    row_id: str = Path(...),
    payload: Dict[str, Any] = Body(...),
    user: dict = Depends(get_current_user),
):
    _require_admin(user)

    table = _resolve_table(entity)
    if table == "usuarios":
        _ensure_users_extra_cols()
    cols = _cols_for(table)
    pk = _pk_for(table)
    hidden = set(_hidden_for(table))
    readonly = set(_readonly_for(table))

    allowed = {
        c["column_name"]
        for c in cols
        if c["column_name"] not in hidden
        and c["column_name"] not in readonly
        and c["column_name"] != pk
    }
    raw = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
    data = {}
    for k, v in (raw or {}).items():
        if k not in allowed:
            continue
        if v in ("", None):
            continue
        data[k] = v

    # usuarios: permitir password plano (lo hasheamos)
    if table == "usuarios":
        pw = (raw or {}).get("password")
        if pw:
            data["hashed_password"] = hash_password(pw)

    if not data:
        raise HTTPException(400, detail="Nada que actualizar")

    # usuarios: map rol -> id_rol si falta
    if table == "usuarios" and "id_rol" not in data and data.get("rol"):
        try:
            with engine.connect() as cn:
                rid = cn.execute(
                    text("SELECT id_rol FROM roles WHERE nombre=:n LIMIT 1"),
                    {"n": data.get("rol")},
                ).scalar()
            if rid is not None:
                data["id_rol"] = int(rid)
        except Exception:
            pass

    # marcas: set marca/logo_path si faltan
    if table == "marcas":
        try:
            from backend.core.quote_assets import logo_for, normalize_marca
            name = data.get("nombre") or data.get("marca") or ""
            if "marca" in allowed and "marca" not in data and name:
                data["marca"] = name
            if data.get("logo_url") and not data.get("logo_path"):
                data["logo_path"] = data.get("logo_url")
            if "logo_path" in allowed and not data.get("logo_path"):
                key = normalize_marca(name)
                if key:
                    data["logo_path"] = logo_for(key, prefer_local=True)
        except Exception:
            pass

    # productos: marca debe ser elegible para el usuario + canonizada a MAYÚSCULA
    if table == "productos":
        allowed_codes = _allowed_marca_codes(user)
        role = _role(user)
        if not _is_privileged(role) and not allowed_codes:
            raise HTTPException(403, detail="No tienes marcas asignadas para editar productos.")

        raw_marca = (raw or {}).get("marca") or data.get("marca") or ""
        code = _canon_marca_code_py(str(raw_marca)) or (str(raw_marca).strip().upper() if raw_marca else None)

        if not _is_privileged(role):
            if len(allowed_codes) == 1:
                code = allowed_codes[0]
            else:
                if not code:
                    raise HTTPException(400, detail="Marca requerida")
                if code not in allowed_codes:
                    raise HTTPException(403, detail="No puedes editar productos para esa marca.")
        else:
            code = code or None

        if code:
            data["marca"] = str(code).upper()

    sets = ", ".join([f"{_qident(k)}=:{k}" for k in data.keys()])
    data["__id"] = row_id

    q = text(f'UPDATE {_qident(table)} SET {sets} WHERE {_qident(pk)} = :__id')
    try:
        with engine.begin() as cn:
            res = cn.execute(q, data)
            if res.rowcount == 0:
                raise HTTPException(404, detail="No existe")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, detail=str(e))

    return {"ok": True}


@router.delete("/{entity}/{row_id}")
@router.delete("/rows/{entity}/{row_id}")  # compat
def delete_row(
    entity: str,
    row_id: str = Path(...),
    user: dict = Depends(get_current_user),
):
    _require_admin(user)

    table = _resolve_table(entity)
    cols = _cols_for(table)
    pk = _pk_for(table)

    with engine.begin() as cn:
        if _has_col(cols, "is_active"):
            q = text(
                f'UPDATE {_qident(table)} SET "is_active"=FALSE WHERE {_qident(pk)}=:id'
            )
            res = cn.execute(q, {"id": row_id})
        else:
            q = text(f'DELETE FROM {_qident(table)} WHERE {_qident(pk)}=:id')
            res = cn.execute(q, {"id": row_id})

        if res.rowcount == 0:
            raise HTTPException(404, detail="No existe")

    return {"ok": True}


@router.post("/usuarios/{user_id}/reset_password")
def reset_password(
    user_id: int,
    new_password: str = Query(..., min_length=4),
    send_email: bool = Query(default=True),
    user: dict = Depends(get_current_user),
):
    _require_admin(user)

    try:
        hashed = hash_password(new_password)
        with engine.begin() as cn:
            row = cn.execute(
                text("SELECT * FROM usuarios WHERE id_usuario=:id"),
                {"id": user_id},
            ).mappings().first()
            if not row:
                raise HTTPException(404, detail="Usuario no existe")
            cn.execute(
                text("UPDATE usuarios SET hashed_password=:hp, updated_at=now() WHERE id_usuario=:id"),
                {"hp": hashed, "id": user_id},
            )
            # marca solicitudes como resueltas
            try:
                cn.execute(
                    text("UPDATE password_requests SET status='resuelto' WHERE email=:e"),
                    {"e": row.get("email")},
                )
            except Exception:
                pass
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, detail=str(e))

    email_sent = False
    email_error = None
    to_email = (row.get("email") or "").strip()

    if to_email and send_email:
        try:
            app_url = (os.getenv("APP_URL") or "").rstrip("/")
            login_url = f"{app_url}/web/login.html" if app_url else "/web/login.html"
            subject = "Credenciales actualizadas"
            plain_text = (
                "Tu clave ha sido actualizada por un administrador.\n\n"
                f"Usuario: {row.get('email') or row.get('id_usuario')}\n"
                f"Clave nueva: {new_password}\n\n"
                "Por favor cambia tu clave al ingresar.\n"
                f"Ingreso: {login_url}\n"
                "Si olvidaste tu clave, usa la opción 'Olvidaste tu contraseña' en el login."
            )
            html = (
                "<p>Tu clave ha sido actualizada por un administrador.</p>"
                f"<p><b>Usuario:</b> {row.get('email') or row.get('id_usuario')}</p>"
                f"<p><b>Clave nueva:</b> {new_password}</p>"
                "<p>Por favor cambia tu clave al ingresar.</p>"
                f"<p><b>Ingreso:</b> {login_url}</p>"
                "<p>Si olvidaste tu clave, usa la opción <b>Olvidaste tu contraseña</b> en el login.</p>"
            )
            send_email(to_email, subject, plain_text, html=html)
            email_sent = True
        except EmailConfigError as e:
            email_error = str(e)
        except Exception as e:
            email_error = f"SMTP error: {e}"

    return {
        "ok": True,
        "email_sent": email_sent,
        "email_error": email_error,
        "usuario": {
            "id_usuario": row.get("id_usuario"),
            "email": row.get("email"),
            "telefono": row.get("telefono") or row.get("phone") or row.get("celular"),
            "nombre": row.get("nombre") or row.get("username") or row.get("usuario"),
        },
    }


@router.get("/usuarios/{user_id}/brands")
def get_user_brands(
    user_id: int,
    user: dict = Depends(get_current_user),
):
    _require_admin(user)
    _ensure_usuarios_marcas()
    with engine.connect() as cn:
        rows = cn.execute(
            text("SELECT id_marca FROM usuarios_marcas WHERE id_usuario=:u ORDER BY id_marca"),
            {"u": user_id},
        ).fetchall()
    return {"ok": True, "id_usuario": user_id, "marcas": [int(r[0]) for r in rows]}


@router.put("/usuarios/{user_id}/brands")
def set_user_brands(
    user_id: int,
    payload: Dict[str, Any] = Body(default_factory=dict),
    user: dict = Depends(get_current_user),
):
    _require_admin(user)
    _ensure_usuarios_marcas()
    marcas = payload.get("marcas") or []
    mids: List[int] = []
    for m in marcas:
        try:
            mids.append(int(m))
        except Exception:
            pass
    with engine.begin() as cn:
        ok = cn.execute(
            text("SELECT 1 FROM usuarios WHERE id_usuario=:u"),
            {"u": user_id},
        ).scalar()
        if not ok:
            raise HTTPException(status_code=404, detail="Usuario no existe")
        cn.execute(text("DELETE FROM usuarios_marcas WHERE id_usuario=:u"), {"u": user_id})
        for mid in mids:
            cn.execute(
                text("INSERT INTO usuarios_marcas(id_usuario,id_marca) VALUES(:u,:m) ON CONFLICT DO NOTHING"),
                {"u": user_id, "m": mid},
            )
    return {"ok": True, "id_usuario": user_id, "marcas": mids}
