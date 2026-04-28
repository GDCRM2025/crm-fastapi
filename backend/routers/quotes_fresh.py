from __future__ import annotations

import os
from fastapi import APIRouter, Query

# Reutilizamos el generador actual, pero lo llamamos con refresh/rebuild forzado.
# Motivo: quotes_override puede servir cotizaciones desde pdf_path/cache antes de
# validar si los assets de Drive cambiaron. Eso hace que un cambio/versionado de
# fondos, portada, terminos, banco o logo no aparezca en la cotizacion.
from backend.routers.quotes_override import pdf_placeholder as _pdf_placeholder

router = APIRouter(prefix="/quotes", tags=["quotes"])


def _env_on(name: str, default: str = "1") -> bool:
    return str(os.getenv(name, default) or "").strip().lower() in {"1", "true", "yes", "on"}


@router.get("/{id_cotizacion}/pdf")
def pdf_fresh_assets(
    id_cotizacion: int,
    debug: int = Query(default=0, ge=0, le=1),
    strict_assets: int = Query(default=0, ge=0, le=1),
    download: int = Query(default=0, ge=0, le=1),
    refresh: int = Query(default=0, ge=0, le=1),
    rebuild: int = Query(default=0, ge=0, le=1),
):
    """
    Ruta de PDF sin cache obsoleto.

    Por defecto fuerza refresh+rebuild para que los cambios en archivos Drive
    se reflejen inmediatamente en la cotizacion generada.

    Para volver al comportamiento cacheado, setear:
      GD_QUOTES_FORCE_FRESH_ASSETS=0
    """
    if _env_on("GD_QUOTES_FORCE_FRESH_ASSETS", "1"):
        refresh = 1
        rebuild = 1

    return _pdf_placeholder(
        id_cotizacion=id_cotizacion,
        debug=debug,
        strict_assets=strict_assets,
        download=download,
        refresh=refresh,
        rebuild=rebuild,
    )
