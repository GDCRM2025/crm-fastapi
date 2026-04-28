from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Query
from sqlalchemy import text

# Reutilizamos el generador actual, pero lo llamamos con refresh/rebuild forzado.
# Motivo: quotes_override puede servir cotizaciones desde pdf_path/cache antes de
# validar si los assets de Drive cambiaron. Eso hace que un cambio/versionado de
# fondos, portada, terminos, banco o logo no aparezca en la cotizacion.
from backend.core.db import get_connection
from backend.routers.quotes_override import pdf_placeholder as _pdf_placeholder

router = APIRouter(prefix="/quotes", tags=["quotes"])


def _env_on(name: str, default: str = "1") -> bool:
    return str(os.getenv(name, default) or "").strip().lower() in {"1", "true", "yes", "on"}


def _latest_quote_id(id_cotizacion: int) -> int:
    """
    Resuelve el PDF a la ultima version real de la cotizacion.

    Contexto:
    - El versionado crea un nuevo registro en cotizaciones.
    - El frontend a veces sigue llamando /quotes/{id_viejo}/pdf.
    - Si renderizamos el id viejo, el usuario ve la cotizacion antigua aunque
      refresh/rebuild limpien cache correctamente.

    Prioridad:
    1) leads.id_cotizacion_vigente si apunta a una cotizacion posterior.
    2) max(id_cotizacion) para mismo lead + numero + marca.
    3) id original.
    """
    try:
        with get_connection() as cn:
            row = cn.execute(
                text(
                    """
                    SELECT id_cotizacion, id_lead, numero, marca
                    FROM public.cotizaciones
                    WHERE id_cotizacion=:id
                    """
                ),
                {"id": int(id_cotizacion)},
            ).mappings().first()
            if not row:
                return int(id_cotizacion)

            base_id = int(row.get("id_cotizacion") or id_cotizacion)
            id_lead: Optional[int] = int(row["id_lead"]) if row.get("id_lead") is not None else None
            numero = row.get("numero")
            marca = row.get("marca")

            if id_lead:
                vigente = cn.execute(
                    text(
                        """
                        SELECT id_cotizacion_vigente
                        FROM public.leads
                        WHERE id_lead=:id_lead
                        """
                    ),
                    {"id_lead": id_lead},
                ).scalar()
                try:
                    if vigente and int(vigente) > base_id:
                        return int(vigente)
                except Exception:
                    pass

                params = {"id_lead": id_lead, "base_id": base_id, "numero": numero, "marca": marca}
                where = ["id_lead=:id_lead", "id_cotizacion>=:base_id"]
                if numero is not None:
                    where.append("numero=:numero")
                if marca:
                    where.append("COALESCE(marca,'')=COALESCE(:marca,'')")

                latest = cn.execute(
                    text(f"SELECT MAX(id_cotizacion) FROM public.cotizaciones WHERE {' AND '.join(where)}"),
                    params,
                ).scalar()
                try:
                    if latest and int(latest) > base_id:
                        return int(latest)
                except Exception:
                    pass
    except Exception:
        return int(id_cotizacion)

    return int(id_cotizacion)


@router.get("/{id_cotizacion}/pdf")
def pdf_fresh_assets(
    id_cotizacion: int,
    debug: int = Query(default=0, ge=0, le=1),
    strict_assets: int = Query(default=0, ge=0, le=1),
    download: int = Query(default=0, ge=0, le=1),
    refresh: int = Query(default=0, ge=0, le=1),
    rebuild: int = Query(default=0, ge=0, le=1),
    latest: int = Query(default=1, ge=0, le=1),
):
    """
    Ruta de PDF sin cache obsoleto y sin quedarse pegada al ID viejo.

    Por defecto:
    - fuerza refresh+rebuild de assets Drive/PDF;
    - si el id solicitado tiene una version nueva, renderiza la vigente.

    Para ver historico exacto:
      /quotes/{id}/pdf?latest=0

    Para volver al comportamiento cacheado, setear:
      GD_QUOTES_FORCE_FRESH_ASSETS=0
    """
    if _env_on("GD_QUOTES_FORCE_FRESH_ASSETS", "1"):
        refresh = 1
        rebuild = 1

    effective_id = _latest_quote_id(id_cotizacion) if int(latest or 0) == 1 else int(id_cotizacion)

    return _pdf_placeholder(
        id_cotizacion=effective_id,
        debug=debug,
        strict_assets=strict_assets,
        download=download,
        refresh=refresh,
        rebuild=rebuild,
    )
