from __future__ import annotations

import logging
import os
import time
import threading
import uuid
from pathlib import Path
import base64
import hmac
import hashlib
import json
from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

# Si usas CORS en local/front
from fastapi.middleware.cors import CORSMiddleware

from dotenv import load_dotenv

from backend.core.logging_setup import setup_logging
from fastapi import HTTPException
from backend.core.settings import settings
from fastapi.openapi.utils import get_openapi


def include_router_safe(app: FastAPI, module_path: str, attr: str = "router") -> None:
    """
    Incluye routers sin reventar el arranque si algún módulo no existe.
    Si falta, lo verás en consola y sigues vivo.
    """
    try:
        mod = __import__(module_path, fromlist=[attr])
        router = getattr(mod, attr)
        app.include_router(router)
        print(f"[main] Router OK: {module_path}.{attr}")
    except Exception as e:
        print(f"[main] Router SKIP: {module_path}.{attr} -> {e}")


BASE_DIR = Path(__file__).resolve().parent.parent  # .../CRM 2025
WEB_DIR = BASE_DIR / "web"

# Carga variables desde .env si existe (no sobreescribe env ya seteadas por Passenger).
load_dotenv(BASE_DIR / ".env")

app = FastAPI(title="CRM BDGD")

# Logging a archivo con rotación (urgente en prod para investigar 500s).
setup_logging(BASE_DIR)
log = logging.getLogger("crm")

_OPENAPI_CACHE: dict | None = None


def _cached_openapi() -> dict:
    global _OPENAPI_CACHE
    if _OPENAPI_CACHE is not None:
        return _OPENAPI_CACHE
    _OPENAPI_CACHE = get_openapi(
        title=app.title,
        version=getattr(app, "version", "0.0.0"),
        description=getattr(app, "description", None),
        routes=app.routes,
    )
    return _OPENAPI_CACHE


app.openapi = _cached_openapi  # type: ignore[assignment]

def _apply_security_headers(request: Request, resp):
    """
    Hardening básico sin romper el CRM embebido (iframes same-origin) ni la PWA.
    """
    try:
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(self), notifications=(self), microphone=(), camera=(), payment=(), usb=()",
        )
        # HSTS (solo HTTPS). No forzamos includeSubDomains para evitar sorpresas.
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")

        # Evita cachear JSON sensible en navegadores/proxies.
        try:
            p = str(request.url.path or "/")
            ct = str(resp.headers.get("content-type") or "").lower()
            if ("/web/" not in p) and ("application/json" in ct or p.startswith(("/auth", "/me", "/leads", "/tasks", "/finanzas", "/rrhh", "/chat"))):
                resp.headers.setdefault("Cache-Control", "no-store")
                resp.headers.setdefault("Pragma", "no-cache")
        except Exception:
            pass
    except Exception:
        pass
    return resp


_RRHH_TICK_LOCK = threading.Lock()
_RRHH_TICK_LAST = 0.0
_RRHH_TICK_INFLIGHT = False


def _should_tick_rrhh(request: Request) -> bool:
    """
    Evita meter carga extra en cada request (especialmente /login y /web/*).
    El tick se limita a tráfico RRHH/SGJO y se throttlea in-process.
    """
    try:
        p = str(request.url.path or "/")
        if p.startswith("/web/"):
            return False
        if p in ("/", "/login", "/logout", "/openapi.json", "/docs", "/redoc", "/favicon.ico", "/healthz"):
            return False
        if not (p.startswith("/rrhh") or p.startswith("/sgjo")):
            return False
        if str(request.method or "").upper() == "OPTIONS":
            return False
        return True
    except Exception:
        return False


def _b64url_decode(s: str) -> bytes:
    s = (s or "").strip()
    pad = "=" * ((4 - (len(s) % 4)) % 4)
    return base64.urlsafe_b64decode((s + pad).encode("ascii"))


def _jwt_payload_verified(token: str) -> dict | None:
    """
    Decodifica y verifica JWT (HS256) sin tocar BD.
    Usado SOLO para guards rápidos en middleware.
    """
    tok = (token or "").strip()
    if not tok or tok.count(".") != 2:
        return None

    # Preferimos libs si existen (y soportan el algoritmo configurado).
    try:
        from backend.routers.auth import SECRET_KEY, ALGORITHM, _jose_jwt, _pyjwt  # type: ignore

        if _jose_jwt is not None:
            return _jose_jwt.decode(tok, SECRET_KEY, algorithms=[ALGORITHM])
        if _pyjwt is not None:
            return _pyjwt.decode(tok, SECRET_KEY, algorithms=[ALGORITHM])  # type: ignore

        alg = str(ALGORITHM or "HS256").upper()
        if alg != "HS256":
            return None

        header_b64, payload_b64, sig_b64 = tok.split(".", 2)
        msg = f"{header_b64}.{payload_b64}".encode("ascii")
        key = str(SECRET_KEY or "").encode("utf-8")
        if not key:
            return None
        sig = _b64url_decode(sig_b64)
        exp = hmac.new(key, msg, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, exp):
            return None
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8", "ignore") or "{}")
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _role_from_bearer(authorization: str) -> str:
    try:
        if not authorization or not authorization.lower().startswith("bearer "):
            return ""
        tok = authorization.split(" ", 1)[1]
        payload = _jwt_payload_verified(tok) or {}
        return str(payload.get("role") or payload.get("rol") or "").upper()
    except Exception:
        return ""

def _safe_web_path(rel: str) -> Path | None:
    """
    Resolve archivos bajo /web sin depender de StaticFiles (fallback para hostings).
    Previene traversal (`..`) y permite servir index.html en carpetas.
    """
    try:
        rel = (rel or "").lstrip("/")
        if not rel:
            rel = "index.html"
        parts = Path(rel).parts
        if any(p in ("..",) for p in parts):
            return None

        candidates = [
            WEB_DIR,
            BASE_DIR / "public" / "web",
            Path(__file__).resolve().parent.parent / "web",
        ]
        for base in candidates:
            try:
                if not base.exists():
                    continue
                p = (base / rel).resolve()
                # asegurar que sigue dentro del base (no traversal via symlinks)
                if str(p).startswith(str(base.resolve())):
                    if p.is_dir():
                        p = p / "index.html"
                    if p.exists() and p.is_file():
                        return p
            except Exception:
                continue
        return None
    except Exception:
        return None


@app.get("/web/{path:path}", include_in_schema=False)
def web_fallback(path: str):
    """
    Fallback manual para servir /web/* cuando StaticFiles no queda montado o falla por config de hosting.
    """
    p = _safe_web_path(path)
    if not p:
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(p)


@app.head("/web/{path:path}", include_in_schema=False)
def web_fallback_head(path: str):
    # curl -I usa HEAD; mantenemos el mismo comportamiento que GET para evitar confusiones.
    return web_fallback(path)


@app.get("/favicon.ico", include_in_schema=False)
def favicon_redirect():
    # Evita 404/errores en consola: el browser siempre pide /favicon.ico.
    return RedirectResponse(url="/web/pwa/gd-128.png")


# Middleware: RID por request + respuesta JSON útil en 500s.
@app.middleware("http")
async def rid_middleware(request: Request, call_next):
    # Some Passenger/cPanel deployments include the sub-URI (e.g. "/crm") in PATH_INFO.
    # Make routing robust by stripping a leading "/crm" from the incoming path.
    # This makes the app work for both:
    # - external "/crm/..." (sub-URI)
    # - internal "/..." (already stripped by the server)
    try:
        p0 = str(request.scope.get("path") or "")
        if p0 == "/crm":
            request.scope["path"] = "/"
        elif p0.startswith("/crm/"):
            request.scope["path"] = p0[len("/crm") :] or "/"
            rp = str(request.scope.get("root_path") or "")
            if not rp.endswith("/crm"):
                request.scope["root_path"] = (rp + "/crm") if rp else "/crm"
    except Exception:
        pass

    rid = uuid.uuid4().hex[:8]
    request.state.rid = rid
    try:
        # Tick RRHH reminders (best-effort throttled).
        # Important on shared hosting: never block the only Passenger worker with background jobs.
        try:
            if _should_tick_rrhh(request):
                now = time.monotonic()
                do = False
                with _RRHH_TICK_LOCK:
                    global _RRHH_TICK_LAST
                    global _RRHH_TICK_INFLIGHT
                    if (now - float(_RRHH_TICK_LAST or 0.0)) >= 25.0:
                        # If a previous tick is still running, skip this one.
                        if not _RRHH_TICK_INFLIGHT:
                            _RRHH_TICK_LAST = now
                            _RRHH_TICK_INFLIGHT = True
                            do = True
                if do:
                    from backend.core.rrhh_reminders import run_rrhh_mark_reminders

                    def _bg_tick():
                        global _RRHH_TICK_INFLIGHT
                        try:
                            run_rrhh_mark_reminders(dry_run=False)
                        except Exception:
                            pass
                        finally:
                            try:
                                with _RRHH_TICK_LOCK:
                                    _RRHH_TICK_INFLIGHT = False
                            except Exception:
                                _RRHH_TICK_INFLIGHT = False

                    threading.Thread(target=_bg_tick, daemon=True).start()
        except Exception:
            pass

        # Guard: rol FINANZAS solo puede usar endpoints de finanzas (+ auth/me + web estático).
        try:
            auth = request.headers.get("authorization") or ""
            if auth.lower().startswith("bearer "):
                role = _role_from_bearer(auth)
                if "FINAN" in role:
                    p = request.url.path or "/"
                    # Permitimos historial (quotes) para FINANZAS (requerimiento: FINANZAS ve finanzas + historial).
                    allowed_prefixes = ("/finanzas", "/quotes", "/auth", "/login", "/logout", "/me", "/web")
                    if not (p == "/" or p.startswith(allowed_prefixes)):
                        resp = JSONResponse(
                            {"detail": "Sin permiso"},
                            status_code=403,
                            headers={"X-RID": rid},
                        )
                        return _apply_security_headers(request, resp)
        except HTTPException:
            # Token inválido / expirado: lo maneja el endpoint que corresponda
            pass
        except Exception:
            # Nunca romper por el guard.
            pass

        resp = await call_next(request)
        resp.headers["X-RID"] = rid
        return _apply_security_headers(request, resp)
    except Exception:
        import traceback
        log.exception("RID=%s %s %s", rid, request.method, request.url.path)
        print(traceback.format_exc())
        resp = JSONResponse(
            {"detail": f"Internal Server Error. RID={rid}"},
            status_code=500,
            headers={"X-RID": rid},
        )
        return _apply_security_headers(request, resp)



# CORS: solo si realmente lo necesitas (dev / herramientas externas).
# En prod (mismo origen), no se requiere y es más seguro dejarlo apagado.
_enable_cors = str(os.getenv("CRM_ENABLE_CORS") or "").strip().lower() in ("1", "true", "yes", "on")
if _enable_cors:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(getattr(settings, "CORS_ORIGINS", []) or []),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# =========================
# Routers (ajusta SOLO si tu estructura difiere)
# =========================
# IMPORTANT: esto es lo que te falta hoy; por eso todo está 404.
include_router_safe(app, "backend.routers.greeni_instagram")
include_router_safe(app, "backend.routers.auth")
include_router_safe(app, "backend.routers.leads")
include_router_safe(app, "backend.routers.public_links")
include_router_safe(app, "backend.routers.catalogos")
include_router_safe(app, "backend.routers.lead_estados")
include_router_safe(app, "backend.routers.settings_live")
include_router_safe(app, "backend.routers.settings")
include_router_safe(app, "backend.routers.cotizador")
include_router_safe(app, "backend.routers.quotes_override")
include_router_safe(app, "backend.routers.productos")
include_router_safe(app, "backend.routers.cotizaciones")
include_router_safe(app, "backend.routers.leads_agenda")
include_router_safe(app, "backend.routers.notifications")
include_router_safe(app, "backend.routers.tools")
include_router_safe(app, "backend.routers.gps")
include_router_safe(app, "backend.routers.recetas")
include_router_safe(app, "backend.routers.finanzas")
include_router_safe(app, "backend.routers.inventario")
include_router_safe(app, "backend.routers.rrhh")
include_router_safe(app, "backend.routers.rrhh_sgjo")
include_router_safe(app, "backend.routers.chat")
include_router_safe(app, "backend.routers.staffing")
include_router_safe(app, "backend.routers.push")
include_router_safe(app, "backend.routers.marketing")
include_router_safe(app, "backend.routers.calendar")
include_router_safe(app, "backend.routers.me")
include_router_safe(app, "backend.routers.users_admin")
include_router_safe(app, "backend.routers.backups")
include_router_safe(app, "backend.routers.assets")
include_router_safe(app, "backend.routers.assets_checklists")
include_router_safe(app, "backend.routers.activity")
include_router_safe(app, "backend.routers.tasks")
include_router_safe(app, "backend.routers.event_checklist")
include_router_safe(app, "backend.routers.gia_email")
include_router_safe(app, "backend.routers.sgjo")

# =========================
# Static /web (login, panel, views, assets)
# =========================
if WEB_DIR.exists():
    # Sirve /web/login.html, /web/panel.html, /web/views/...
    app.mount("/web", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    print(f"[main] Static /web OK -> {WEB_DIR}")
else:
    print(f"[main] Static /web SKIP (no existe) -> {WEB_DIR}")

# Health check (no DB). Use for external keep-warm pings on shared hosting.
@app.get("/healthz", include_in_schema=False)
def healthz() -> JSONResponse:
    return JSONResponse({"ok": True, "ts": int(time.time())})

@app.head("/healthz", include_in_schema=False)
def healthz_head() -> JSONResponse:
    return healthz()

# Root: manda al login
@app.get("/", include_in_schema=False)
def root(request: Request):
    rp = str(request.scope.get("root_path") or "")
    # En prod (Passenger sub-URI), el frontend vive bajo /crm/web/...
    # Si redirigimos a /web/login.html sin root_path, Apache puede no rutearlo al app.
    return RedirectResponse(url=(rp + "/web/login.html") if rp else "/web/login.html")
