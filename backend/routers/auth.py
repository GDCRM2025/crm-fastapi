from __future__ import annotations

import os
import secrets
import hashlib
import hmac
import json
import base64
import traceback
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Header, Depends, Body
from sqlalchemy import text

from backend.core.db import get_connection
from backend.core.stale_leads import auto_decline_stale_leads
from backend.core.password import verify_password, hash_password
from backend.core.settings import settings
from backend.core.email import send_email, EmailConfigError
from backend.core.activity_log import log_activity
from pydantic import BaseModel

router = APIRouter(tags=["auth"])

# -----------------------------
# JWT helpers (jose o PyJWT)
# -----------------------------
SECRET_KEY = settings.JWT_SECRET or settings.SECRET_KEY
ALGORITHM = settings.ALGORITHM
ACCESS_TOKEN_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "12"))

# Intento jose primero (habitual en FastAPI)
_jose_jwt = None
_pyjwt = None
try:
    from jose import jwt as _jose_jwt  # type: ignore
except Exception:
    _jose_jwt = None

if _jose_jwt is None:
    try:
        import jwt as _pyjwt  # PyJWT
    except Exception:
        _pyjwt = None


def _jwt_encode(payload: dict) -> str:
    if not SECRET_KEY:
        raise RuntimeError("JWT SECRET_KEY no configurado (settings.JWT_SECRET / settings.SECRET_KEY).")
    if _jose_jwt is not None:
        return _jose_jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    if _pyjwt is not None:
        return _pyjwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)  # type: ignore

    # Fallback sin dependencias (HS256), para evitar que el login se caiga por paquetes faltantes.
    # Nota: Solo soporta HS256.
    alg = (ALGORITHM or "HS256").upper()
    if alg != "HS256":
        raise RuntimeError(f"JWT sin librería: ALGORITHM={alg} no soportado (instala python-jose/PyJWT).")

    def b64url(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")

    header = {"typ": "JWT", "alg": "HS256"}
    header_b64 = b64url(json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    payload_b64 = b64url(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    msg = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = hmac.new(str(SECRET_KEY).encode("utf-8"), msg, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{b64url(sig)}"


def _log_auth500(where: str, payload: dict, exc: Exception) -> str:
    rid = secrets.token_hex(4)
    try:
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        p = root / "data" / "debug" / "auth_500.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        safe = dict(payload or {})
        # nunca logueamos password
        if "password" in safe:
            safe["password"] = "***"
        p.open("a", encoding="utf-8").write(
            f"=== RID={rid} where={where} ===\npayload={safe}\nerr={type(exc).__name__}: {exc}\n{traceback.format_exc()}\n\n"
        )
    except Exception:
        pass
    return rid


# -----------------------------
# Users (hard + env override)
# -----------------------------
# Puedes sobreescribir con ENV:
# export CRM_USERS="admin:admin:admin,greengd:green123:admin,vendedor:1234:user"
def load_users() -> Dict[str, Dict[str, str]]:
    raw = os.getenv("CRM_USERS", "").strip()
    if raw:
        users: Dict[str, Dict[str, str]] = {}
        for chunk in raw.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            parts = chunk.split(":")
            if len(parts) < 2:
                continue
            u = parts[0].strip()
            p = parts[1].strip()
            r = (parts[2].strip() if len(parts) >= 3 else "user")
            users[u] = {"password": p, "role": r}
        if users:
            return users

    # Defaults: lo que tu UI sugiere + admin clásico
    return {
        "admin": {"password": "admin", "role": "admin"},
        "greengd": {"password": "green123", "role": "admin"},
    }


USERS = load_users()


# -----------------------------
# Schemas
# -----------------------------
class LoginIn(BaseModel):
    username: str
    password: str


class ResetRequestIn(BaseModel):
    email: str | None = None
    username: str | None = None


class ResetSetIn(BaseModel):
    token: str
    password: str
    password2: str


class RegisterIn(BaseModel):
    nombre: str
    email: str
    rut: str
    # Registro Operadores/CHOP:
    # - Usuario: EMAIL
    # - Contraseña: RUT (sin puntos, con guion) — definitiva.
    password: str | None = None  # compat (ignorado)
    telefono: str | None = None
    username: str | None = None


def _reset_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _ensure_password_resets(conn) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS password_resets (
                id_reset SERIAL PRIMARY KEY,
                email TEXT,
                username TEXT,
                token_hash TEXT NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                used_at TIMESTAMP NULL,
                created_at TIMESTAMP DEFAULT now()
            )
            """
        )
    )


def _ensure_operadores_allowlist(conn) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS operadores_allowlist (
              id_allow SERIAL PRIMARY KEY,
              rut TEXT NOT NULL,
              nombre TEXT NOT NULL,
              email TEXT NOT NULL,
              cargo TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'ACTIVO'
            )
            """
        )
    )
    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_operadores_email ON operadores_allowlist(lower(email))"))
    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_operadores_rut ON operadores_allowlist(lower(rut))"))


def _normalize(s: str) -> str:
    return (s or "").strip().lower()


def _role_id_for(conn, role_name: str) -> int:
    try:
        rid = conn.execute(
            text("SELECT id_rol FROM roles WHERE UPPER(nombre)=UPPER(:n) LIMIT 1"),
            {"n": role_name},
        ).scalar()
        if rid:
            return int(rid)
    except Exception:
        pass
    # Si el rol no existe, intentamos crearlo (idempotente).
    # Esto facilita agregar roles nuevos (ej: FINANZAS) sin migraciones manuales.
    try:
        conn.execute(
            text("INSERT INTO roles(nombre) VALUES (:n) ON CONFLICT (nombre) DO NOTHING"),
            {"n": role_name},
        )
        rid = conn.execute(
            text("SELECT id_rol FROM roles WHERE UPPER(nombre)=UPPER(:n) LIMIT 1"),
            {"n": role_name},
        ).scalar()
        if rid:
            return int(rid)
    except Exception:
        pass
    # fallback "OPERADOR" or default 5
    try:
        rid = conn.execute(
            text("SELECT id_rol FROM roles WHERE UPPER(nombre)=UPPER('OPERADOR') LIMIT 1")
        ).scalar()
        if rid:
            return int(rid)
    except Exception:
        pass
    return 5


def create_access_token(sub: str, role: str, marcas: list[int] | None = None, name: str = "") -> str:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload = {
        "sub": sub,
        "role": role,
        "marcas": marcas or [],
        "name": name or "",
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return _jwt_encode(payload)


def login_response(username: str, role: str, token: str, user_id: int | None = None, name: str = "", avatar_url: str | None = None) -> Dict[str, Any]:
    # Respuesta “compat” para todos tus frontends/llamadas viejas
    return {
        "ok": True,
        "success": True,
        "access_token": token,
        "token": token,
        "jwt": token,
        "token_type": "bearer",
        "role": role,
        "username": username,
        "nombre": name or username,
        "user": {"id": user_id, "username": username, "role": role, "name": name or username, "avatar_url": avatar_url},
        "avatar_url": avatar_url,
    }


def authenticate(username: str, password: str) -> Optional[Dict[str, Any]]:
    u = username.strip()
    p = password.strip()
    if not u or not p:
        return None

    # 1) DB usuarios (email o username)
    try:
        with get_connection() as conn:
            try:
                conn.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS avatar_url TEXT"))
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
            row = conn.execute(
                text(
                    """
                    SELECT id_usuario, nombre, email, username, hashed_password, rol, avatar_url
                    FROM usuarios
                    WHERE lower(email) = lower(:u) OR lower(username) = lower(:u)
                    LIMIT 1
                    """
                ),
                {"u": u},
            ).mappings().first()
            if row and row.get("hashed_password"):
                if verify_password(p, row["hashed_password"]):
                    # marcas asignadas
                    marcas = []
                    try:
                        rows = conn.execute(
                            text("SELECT id_marca FROM usuarios_marcas WHERE id_usuario=:id"),
                            {"id": row["id_usuario"]},
                        ).fetchall()
                        marcas = [int(r[0]) for r in rows]
                    except Exception:
                        marcas = []
                    return {
                        "id": int(row["id_usuario"]),
                        "username": row.get("email") or row.get("username") or u,
                        "role": row.get("rol") or "User",
                        "name": row.get("nombre") or u,
                        "marcas": marcas,
                        "avatar_url": row.get("avatar_url"),
                    }
    except Exception:
        pass

    # 2) fallback users hardcoded
    rec = USERS.get(u)
    if rec and rec.get("password") == p:
        return {"id": None, "username": u, "role": rec.get("role", "user"), "name": u, "marcas": []}
    return None


def _table_exists(conn, table: str) -> bool:
    return bool(conn.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}).scalar())


# -----------------------------
# Endpoints (aliases)
# -----------------------------
@router.post("/auth/login")
@router.post("/login")
@router.post("/web/auth/login")
@router.post("/web/login")
def login(data: LoginIn):
    try:
        user = authenticate(data.username, data.password)
        if not user:
            raise HTTPException(status_code=401, detail="Credenciales inválidas")

        # Auto-declina leads estancados al entrar al sistema.
        # Guard: corre como maximo 1 vez por dia para no castigar cada login.
        try:
            with get_connection() as conn:
                # Usamos lock para evitar concurrencia entre procesos Passenger.
                lock_key = 25022026  # numero estable (ASCII) - no tiene significado externo
                got_lock = bool(conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": lock_key}).scalar())
                if got_lock:
                    try:
                        conn.execute(
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
                        today = datetime.now(timezone.utc).date().isoformat()
                        last = conn.execute(
                            text("SELECT value FROM system_kv WHERE key='auto_decline_last_run' LIMIT 1")
                        ).scalar()
                        if (last or "") != today:
                            # Importante: no debe romper el login si falla.
                            auto_decline_stale_leads(
                                dry_run=False, triggered_by=str(user.get("username") or ""), conn=conn
                            )
                            conn.execute(
                                text(
                                    """
                                    INSERT INTO system_kv(key,value,updated_at)
                                    VALUES ('auto_decline_last_run', :v, now())
                                    ON CONFLICT(key) DO UPDATE SET value=:v, updated_at=now()
                                    """
                                ),
                                {"v": today},
                            )
                            conn.commit()
                    finally:
                        try:
                            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": lock_key})
                        except Exception:
                            pass
        except Exception:
            # Silencioso: si algo falla aca, el usuario igual debe poder entrar.
            pass

        token = create_access_token(
            str(user.get("id") or user["username"]),
            user["role"],
            user.get("marcas") or [],
            user.get("name") or user["username"],
        )
        # Activity log (BD). No rompe el login si falla.
        try:
            with get_connection() as c2:
                log_activity(
                    c2,
                    username=str(user.get("username") or ""),
                    user_id=int(user.get("id")) if user.get("id") is not None else None,
                    role=str(user.get("role") or ""),
                    action="LOGIN",
                    meta={},
                )
                c2.commit()
        except Exception:
            pass
        return login_response(
            user["username"],
            user["role"],
            token,
            user_id=user.get("id"),
            name=user.get("name") or user["username"],
            avatar_url=user.get("avatar_url"),
        )
    except HTTPException:
        raise
    except Exception as e:
        rid = _log_auth500("login", {"username": getattr(data, "username", None)}, e)
        raise HTTPException(status_code=500, detail=f"Internal Server Error (auth). RID={rid}")


@router.post("/auth/register")
@router.post("/register")
def register(data: RegisterIn):
    nombre = data.nombre.strip()
    email = data.email.strip().lower()
    rut_in = (data.rut or "").strip()
    telefono_in = (data.telefono or "").strip()
    # password/username entrantes se ignoran por regla negocio, se mantienen por compatibilidad

    # Normaliza RUT: sin puntos, con guion, y en lower para comparar.
    def _rut_norm(s: str) -> str:
        s = (s or "").strip()
        s = s.replace(".", "").replace(" ", "")
        s = s.replace("‐", "-").replace("‑", "-").replace("–", "-").replace("—", "-")
        return s.lower()

    rut = _rut_norm(rut_in)
    # Password definitiva: RUT (sin puntos, con guion).
    password = rut

    # Username definitivo: EMAIL
    username = email

    if not nombre or not email or not rut or not telefono_in:
        raise HTTPException(status_code=400, detail="Faltan datos requeridos")

    # Normaliza teléfono a E.164 Chile.
    # UX: usuario ingresa solo 9 dígitos, pero aceptamos espacios, +56 o 56.
    def _normalize_phone_cl(raw: str) -> str:
        s = (raw or "").strip()
        s = s.replace(" ", "").replace("-", "").replace("(", "").replace(")", "").replace(".", "")
        if s.startswith("+"):
            digits = "".join(ch for ch in s[1:] if ch.isdigit())
        else:
            digits = "".join(ch for ch in s if ch.isdigit())
        if digits.startswith("56") and len(digits) == 11:
            digits = digits[2:]
        if len(digits) != 9:
            raise HTTPException(status_code=400, detail="Teléfono inválido. Usa 9 dígitos (sin +56).")
        return "+56" + digits

    tel_norm = _normalize_phone_cl(telefono_in)

    with get_connection() as conn:
        _ensure_operadores_allowlist(conn)

        # Asegura columnas (deploy idempotente)
        try:
            conn.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS rut TEXT"))
            conn.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS avatar_url TEXT"))
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass

        # Allowlist:
        # - Si hay registros en operadores_allowlist, se exige match por RUT (modo seguro).
        # - Si está vacía, permitimos registro libre PERO solo como OPERADOR.
        enforce_allowlist = (os.getenv("OPERADORES_REQUIRE_ALLOWLIST") or "").strip().lower() in ("1", "true", "yes", "y", "on")
        try:
            if not enforce_allowlist:
                cnt = conn.execute(text("SELECT COUNT(*) FROM operadores_allowlist")).scalar() or 0
                enforce_allowlist = int(cnt) > 0
        except Exception:
            enforce_allowlist = False

        cargo = "OPERADOR"
        if enforce_allowlist:
            row = conn.execute(
                text(
                    """
                    SELECT nombre, email, rut, cargo, status
                    FROM operadores_allowlist
                    WHERE lower(rut)=:r
                    LIMIT 1
                    """
                ),
                {"r": rut},
            ).mappings().first()
            if not row:
                raise HTTPException(status_code=403, detail="No estás autorizado para registrarte")
            status = (row.get("status") or "").upper()
            if status and status not in ("ACTIVO", "ACTIVE", "OK"):
                raise HTTPException(status_code=403, detail="Usuario no activo")
            cargo = (row.get("cargo") or "OPERADOR").strip().upper()
            if cargo not in ("OPERADOR", "CHOP", "CHOFER", "CONDUCTOR"):
                cargo = "OPERADOR"

        exists = conn.execute(
            text("SELECT id_usuario FROM usuarios WHERE lower(email)=:e OR lower(username)=:u LIMIT 1"),
            {"e": email, "u": username},
        ).first()
        if exists:
            raise HTTPException(status_code=400, detail="Usuario ya existe")

        # Teléfono único (normalizado). Si ya está usado, no registramos otro usuario con el mismo número.
        try:
            dup_tel = conn.execute(
                text("SELECT id_usuario FROM usuarios WHERE telefono=:t LIMIT 1"),
                {"t": tel_norm},
            ).first()
            if dup_tel:
                raise HTTPException(
                    status_code=409,
                    detail="Teléfono ya registrado. Inicia sesión con ese usuario y activa notificaciones desde tu perfil.",
                )
        except HTTPException:
            raise
        except Exception:
            pass

        role_id = _role_id_for(conn, cargo)
        hp = hash_password(password)

        # avatar genérico (data URI SVG)
        default_avatar = (
            "data:image/svg+xml;utf8,"
            "<svg xmlns='http://www.w3.org/2000/svg' width='256' height='256' viewBox='0 0 256 256'>"
            "<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>"
            "<stop offset='0' stop-color='%2319c37d'/><stop offset='1' stop-color='%230b0e10'/>"
            "</linearGradient></defs>"
            "<circle cx='128' cy='128' r='124' fill='url(%23g)'/>"
            "<circle cx='128' cy='108' r='44' fill='rgba(255,255,255,0.92)'/>"
            "<path d='M48 224c16-44 48-66 80-66s64 22 80 66' fill='rgba(255,255,255,0.92)'/>"
            "</svg>"
        )

        row = conn.execute(
            text(
                """
                INSERT INTO usuarios(nombre,email,username,hashed_password,telefono,rut,cargo,id_rol,rol,is_active,avatar_url,created_at,updated_at)
                VALUES (:n,:e,:u,:hp,:tel,:rut,:cargo,:rid,:rol,TRUE,:av,now(),now())
                RETURNING id_usuario
                """
            ),
            {
                "n": nombre,
                "e": email,
                "u": username,
                "hp": hp,
                "tel": tel_norm,
                "rut": rut,
                "cargo": cargo,
                "rid": role_id,
                "rol": cargo,
                "av": default_avatar,
            },
        ).fetchone()
        conn.commit()

    return {"ok": True, "id_usuario": int(row[0]) if row else None}


# auth deps (compat con settings_live)
def get_current_user(authorization: str | None = Header(default=None)) -> Dict[str, Any]:
    # bypass dev si se activa
    if settings.DEV_NO_AUTH:
        return {"id": "dev", "role": "Admin", "marcas": [], "name": "Dev"}
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Token requerido")
    token = authorization.split(" ", 1)[1]
    try:
        if _jose_jwt is not None:
            payload = _jose_jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        elif _pyjwt is not None:
            payload = _pyjwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])  # type: ignore
        else:
            raise HTTPException(status_code=401, detail="JWT no disponible")
    except Exception:
        raise HTTPException(status_code=401, detail="Token inválido")
    user = {
        "id": int(payload.get("sub")) if str(payload.get("sub","")).isdigit() else payload.get("sub"),
        "username": payload.get("sub"),
        "role": payload.get("role") or "User",
        "marcas": payload.get("marcas") or [],
        "name": payload.get("name") or "",
    }

    # Si el token trae sub no-numérico (username/email), resolvemos el id_usuario desde la BD
    # para que módulos que dependen de user_id (push/chat membership) funcionen.
    try:
        sub = str(payload.get("sub") or "").strip()
        if sub and not sub.isdigit():
            with get_connection() as conn:
                row = conn.execute(
                    text(
                        """
                        SELECT id_usuario, nombre, email, username, avatar_url
                        FROM usuarios
                        WHERE lower(email)=lower(:u) OR lower(username)=lower(:u)
                        LIMIT 1
                        """
                    ),
                    {"u": sub},
                ).mappings().first()
                if row and row.get("id_usuario"):
                    user["id"] = int(row["id_usuario"])
                    # username usable para logs / filtros (prefiere email).
                    if row.get("email") or row.get("username"):
                        user["username"] = row.get("email") or row.get("username")
                    if not user.get("name") and row.get("nombre"):
                        user["name"] = row.get("nombre")
                    if row.get("avatar_url"):
                        user["avatar_url"] = row.get("avatar_url")
    except Exception:
        pass

    # Si el token viene sin `marcas`, intentamos inferirlas desde BD (usuarios_marcas / usuario_marcas / usuarios.id_marca).
    try:
        uid = int(user.get("id")) if str(user.get("id") or "").isdigit() else None
        if uid and not list(user.get("marcas") or []):
            with get_connection() as conn:
                # 1) tabla N:N
                join_table = None
                try:
                    v = conn.execute(text("SELECT to_regclass('public.usuarios_marcas') IS NOT NULL")).scalar()
                    if v:
                        join_table = "usuarios_marcas"
                except Exception:
                    join_table = None
                if join_table is None:
                    try:
                        v = conn.execute(text("SELECT to_regclass('public.usuario_marcas') IS NOT NULL")).scalar()
                        if v:
                            join_table = "usuario_marcas"
                    except Exception:
                        join_table = None
                marcas = []
                if join_table:
                    try:
                        rows = conn.execute(
                            text(f"SELECT id_marca FROM public.{join_table} WHERE id_usuario=:u ORDER BY id_marca"),
                            {"u": int(uid)},
                        ).fetchall()
                        for r in rows:
                            try:
                                marcas.append(int(r[0]))
                            except Exception:
                                pass
                    except Exception:
                        marcas = []
                # 2) fallback legacy: usuarios.id_marca
                if not marcas:
                    try:
                        mid = conn.execute(
                            text("SELECT id_marca FROM public.usuarios WHERE id_usuario=:u LIMIT 1"),
                            {"u": int(uid)},
                        ).scalar()
                        if mid is not None and str(mid).isdigit() and int(mid) > 0:
                            marcas = [int(mid)]
                    except Exception:
                        pass
                if marcas:
                    user["marcas"] = marcas
    except Exception:
        pass

    # enrich with avatar_url if available
    try:
        uid = int(user.get("id")) if str(user.get("id") or "").isdigit() else None
        if uid:
            with get_connection() as conn:
                try:
                    conn.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS avatar_url TEXT"))
                    conn.commit()
                except Exception:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                row = conn.execute(
                    text("SELECT avatar_url, nombre, email, username FROM usuarios WHERE id_usuario=:id"),
                    {"id": uid},
                ).mappings().first()
                if row:
                    user["avatar_url"] = row.get("avatar_url")
                    if not user.get("name") and row.get("nombre"):
                        user["name"] = row.get("nombre")
                    # username usable para logs / filtros (prefiere email).
                    if row.get("email") or row.get("username"):
                        user["username"] = row.get("email") or row.get("username")
    except Exception:
        pass

    return user


@router.post("/auth/logout")
@router.post("/logout")
def logout(payload: dict = Body(default_factory=dict), user: dict = Depends(get_current_user)):
    """
    Logout "server-side" (best-effort):
    - Registra activity_log (sin escribir archivos)
    - Si es PM (>= 12:00 America/Santiago), intenta mandar digest del día a Admins.
      (El envío por cron también existe, pero este cubre el requisito "logout PM".)
    """
    reason = str(payload.get("reason") or "manual").strip().lower()[:40] or "manual"

    # Activity log (BD): LOGOUT (no escribe archivos)
    try:
        uid_raw = user.get("id")
        uname = str(user.get("username") or user.get("name") or uid_raw or "").strip()
        uid_int = int(uid_raw) if str(uid_raw or "").isdigit() else None
        with get_connection() as c2:
            log_activity(
                c2,
                username=uname,
                user_id=uid_int,
                role=str(user.get("role") or ""),
                action="LOGOUT",
                entity_type="auth",
                entity_id=uid_int,
                meta={"reason": reason},
            )
            c2.commit()
    except Exception:
        pass

    # Enviar digest del día al logout en horario PM (best-effort).
    try:
        from datetime import datetime, timezone
        import os

        tz = os.getenv("CRM_TZ", "America/Santiago")
        try:
            # Python 3.9+: zoneinfo
            from zoneinfo import ZoneInfo  # type: ignore
            now_local = datetime.now(ZoneInfo(tz))
            start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            start_utc = start_local.astimezone(timezone.utc)
            end_utc = now_local.astimezone(timezone.utc)
        except Exception:
            now_local = datetime.now()
            start_utc = None
            end_utc = None

        if getattr(now_local, "hour", 0) >= 12:
            # Import lazy para no afectar arranque.
            from backend.routers.activity import _send_digest_window  # type: ignore
            import threading

            uid_raw = user.get("id")
            who = str(user.get("username") or user.get("name") or uid_raw or "").strip()

            # No bloquear la respuesta HTTP del logout (UX). El correo se intenta en background.
            def _bg():
                try:
                    _send_digest_window(
                        dt_from=start_utc,
                        dt_to=end_utc,
                        subject_prefix=f"CRM · Logout PM ({user.get('name') or who})",
                        filter_username=who,
                    )
                except Exception:
                    pass

            threading.Thread(target=_bg, daemon=True).start()
    except Exception:
        pass

    return {"ok": True}


@router.post("/auth/password-request")
@router.post("/password-request")
def request_password_reset(data: ResetRequestIn):
    """
    Inicia flujo de cambio de contraseña enviando link al usuario.
    """
    email = (data.email or "").strip()
    username = (data.username or "").strip()
    if not email and not username:
        raise HTTPException(400, "email o username requerido")

    with get_connection() as conn:
        _ensure_password_resets(conn)
        # busca usuario
        row = conn.execute(
            text(
                """
                SELECT id_usuario, email, username, nombre
                FROM usuarios
                WHERE (:email <> '' AND email = :email)
                   OR (:username <> '' AND username = :username)
                LIMIT 1
                """
            ),
            {"email": email, "username": username},
        ).mappings().first()

        if not row:
            # no filtramos demasiada info; devolvemos ok para evitar enumeración
            return {"ok": True, "email_sent": False, "message": "Si existe el usuario, recibirá un correo."}

        token = secrets.token_urlsafe(32)
        token_hash = _reset_token_hash(token)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=2)

        conn.execute(
            text(
                """
                INSERT INTO password_resets(email, username, token_hash, expires_at)
                VALUES (:email, :username, :token_hash, :expires_at)
                """
            ),
            {
                "email": row.get("email") or email or None,
                "username": row.get("username") or username or None,
                "token_hash": token_hash,
                "expires_at": expires_at,
            },
        )
        conn.commit()

    app_url = (os.getenv("APP_URL") or "").rstrip("/")
    if not app_url:
        app_url = "http://127.0.0.1:8000"
    reset_url = f"{app_url}/web/reset_password.html?token={token}"

    email_sent = False
    email_error = None
    to_email = (row.get("email") or email or "").strip()
    if to_email:
        try:
            subject = "Recupera tu contraseña"
            text_body = (
                "Recibimos una solicitud para cambiar tu contraseña.\n\n"
                f"Usuario: {row.get('username') or to_email}\n"
                f"Link de cambio: {reset_url}\n\n"
                "Este enlace expira en 2 horas."
            )
            html_body = (
                "<p>Recibimos una solicitud para cambiar tu contraseña.</p>"
                f"<p><b>Usuario:</b> {row.get('username') or to_email}</p>"
                f"<p><b>Link de cambio:</b> <a href=\"{reset_url}\">{reset_url}</a></p>"
                "<p>Este enlace expira en 2 horas.</p>"
            )
            send_email(to_email, subject, text_body, html=html_body)
            email_sent = True
        except EmailConfigError as e:
            email_error = str(e)
        except Exception as e:
            email_error = f"SMTP error: {e}"

    return {"ok": True, "email_sent": email_sent, "email_error": email_error, "reset_url": reset_url}


@router.get("/auth/password-requests")
@router.get("/password-requests")
def list_password_requests(status: str = "pendiente"):
    with get_connection() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS password_requests (
                    id_request SERIAL PRIMARY KEY,
                    email TEXT,
                    username TEXT,
                    status TEXT DEFAULT 'pendiente',
                    created_at TIMESTAMP DEFAULT now()
                )
                """
            )
        )
        rows = conn.execute(
            text(
                "SELECT id_request, email, username, status, created_at FROM password_requests WHERE status=:s ORDER BY created_at DESC"
            ),
            {"s": status},
        ).mappings().all()
    return {"items": list(rows)}


@router.post("/auth/password-requests/{id_request}/resolve")
def resolve_password_request(id_request: int):
    with get_connection() as conn:
        conn.execute(
            text("UPDATE password_requests SET status='resuelto' WHERE id_request=:id"),
            {"id": id_request},
        )
        conn.commit()
    return {"ok": True}


@router.post("/auth/password-reset")
@router.post("/password-reset")
def password_reset(body: ResetSetIn):
    token = (body.token or "").strip()
    p1 = (body.password or "").strip()
    p2 = (body.password2 or "").strip()
    if not token or not p1:
        raise HTTPException(400, "token y password requeridos")
    if p1 != p2:
        raise HTTPException(400, "Las contraseñas no coinciden")

    token_hash = _reset_token_hash(token)
    with get_connection() as conn:
        _ensure_password_resets(conn)
        row = conn.execute(
            text(
                """
                SELECT id_reset, email, username, expires_at, used_at
                FROM password_resets
                WHERE token_hash=:th
                LIMIT 1
                """
            ),
            {"th": token_hash},
        ).mappings().first()

        if not row:
            raise HTTPException(400, "Token inválido")
        if row.get("used_at"):
            raise HTTPException(400, "Token ya utilizado")
        if row.get("expires_at") and row["expires_at"] < datetime.now(timezone.utc):
            raise HTTPException(400, "Token expirado")

        user = conn.execute(
            text(
                """
                SELECT id_usuario
                FROM usuarios
                WHERE (:email <> '' AND email = :email)
                   OR (:username <> '' AND username = :username)
                LIMIT 1
                """
            ),
            {
                "email": row.get("email") or "",
                "username": row.get("username") or "",
            },
        ).mappings().first()
        if not user:
            raise HTTPException(404, "Usuario no existe")

        hp = hash_password(p1)
        conn.execute(
            text("UPDATE usuarios SET hashed_password=:hp, updated_at=now() WHERE id_usuario=:id"),
            {"hp": hp, "id": user["id_usuario"]},
        )
        conn.execute(
            text("UPDATE password_resets SET used_at=now() WHERE id_reset=:id"),
            {"id": row["id_reset"]},
        )
        conn.commit()

    return {"ok": True}
