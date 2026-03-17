from __future__ import annotations

import logging
import uuid
from pathlib import Path
from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

# Si usas CORS en local/front
from fastapi.middleware.cors import CORSMiddleware

from dotenv import load_dotenv

from backend.core.logging_setup import setup_logging
from fastapi import HTTPException


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
        # Guard: rol FINANZAS solo puede usar endpoints de finanzas (+ auth/me + web estático).
        try:
            auth = request.headers.get("authorization") or ""
            if auth.lower().startswith("bearer "):
                from backend.routers.auth import get_current_user as _get_user  # lazy import

                u = _get_user(authorization=auth)
                role = str((u or {}).get("role") or (u or {}).get("rol") or "").upper()
                if "FINAN" in role:
                    p = request.url.path or "/"
                    # Permitimos historial (quotes) para FINANZAS (requerimiento: FINANZAS ve finanzas + historial).
                    allowed_prefixes = ("/finanzas", "/quotes", "/auth", "/login", "/logout", "/me", "/web")
                    if not (p == "/" or p.startswith(allowed_prefixes)):
                        return JSONResponse(
                            {"detail": "Sin permiso"},
                            status_code=403,
                            headers={"X-RID": rid},
                        )
        except HTTPException:
            # Token inválido / expirado: lo maneja el endpoint que corresponda
            pass
        except Exception:
            # Nunca romper por el guard.
            pass

        resp = await call_next(request)
        resp.headers["X-RID"] = rid
        return resp
    except Exception:
        import traceback
        log.exception("RID=%s %s %s", rid, request.method, request.url.path)
        print(traceback.format_exc())
        return JSONResponse(
            {"detail": f"Internal Server Error. RID={rid}"},
            status_code=500,
            headers={"X-RID": rid},
        )



# CORS (local dev)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
include_router_safe(app, "backend.routers.chat")
include_router_safe(app, "backend.routers.calendar")
include_router_safe(app, "backend.routers.me")
include_router_safe(app, "backend.routers.users_admin")
include_router_safe(app, "backend.routers.backups")
include_router_safe(app, "backend.routers.assets")
include_router_safe(app, "backend.routers.assets_checklists")
include_router_safe(app, "backend.routers.activity")

# =========================
# Static /web (login, panel, views, assets)
# =========================
if WEB_DIR.exists():
    # Sirve /web/login.html, /web/panel.html, /web/views/...
    app.mount("/web", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    print(f"[main] Static /web OK -> {WEB_DIR}")
else:
    print(f"[main] Static /web SKIP (no existe) -> {WEB_DIR}")

# Root: manda al login
@app.get("/", include_in_schema=False)
def root(request: Request):
    rp = str(request.scope.get("root_path") or "")
    # En prod (Passenger sub-URI), el frontend vive bajo /crm/web/...
    # Si redirigimos a /web/login.html sin root_path, Apache puede no rutearlo al app.
    return RedirectResponse(url=(rp + "/web/login.html") if rp else "/web/login.html")
