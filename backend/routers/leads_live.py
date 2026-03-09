from __future__ import annotations

from datetime import datetime, date, time, timedelta
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Body
from sqlalchemy import text

from backend.core.database import engine

router = APIRouter(tags=["leads-live"])


def _estado_id_by_name(nombre: str) -> Optional[int]:
    with engine.connect() as cn:
        r = cn.execute(
            text("SELECT id_estado FROM estados_lead WHERE LOWER(nombre)=LOWER(:n) LIMIT 1"),
            {"n": nombre},
        ).fetchone()
        return int(r[0]) if r else None


def _safe_parse_time(hhmm: str) -> time:
    hhmm = (hhmm or "").strip()
    if not hhmm:
        raise ValueError("Hora vacía")
    parts = hhmm.split(":")
    if len(parts) != 2:
        raise ValueError("Hora inválida")
    h = int(parts[0]); m = int(parts[1])
    return time(hour=h, minute=m)


def _latest_quote_for_lead(id_lead: int) -> Optional[Dict[str, Any]]:
    with engine.connect() as cn:
        q = cn.execute(
            text(
                """
                SELECT id_cotizacion, num_cotizacion, total
                FROM cotizaciones
                WHERE id_lead=:id
                ORDER BY created_at DESC NULLS LAST, id_cotizacion DESC
                LIMIT 1
                """
            ),
            {"id": id_lead},
        ).mappings().first()
        return dict(q) if q else None


def _quote_items(id_cotizacion: int) -> list[dict]:
    with engine.connect() as cn:
        rows = cn.execute(
            text(
                """
                SELECT nombre_producto, cantidad
                FROM cotizaciones_detalle
                WHERE id_cotizacion=:id
                ORDER BY nombre_producto
                """
            ),
            {"id": id_cotizacion},
        ).mappings().all()
        return [dict(r) for r in rows]


def _calc_ops_montaje(items: list[dict]) -> tuple[int, str]:
    """
    Placeholder (estable, sin 500). Ajustas reglas aquí cuando pegues tu lista de carros.
    - ops: base 2 + extra por volumen
    - montaje: listado resumido
    """
    total_qty = sum(int(i.get("cantidad") or 0) for i in items)
    ops = 2 + max(0, (total_qty - 40) // 30)
    lines = []
    for it in items[:30]:
        lines.append(f"- {it.get('nombre_producto')} x{it.get('cantidad')}")
    montaje = "Productos:\n" + ("\n".join(lines) if lines else "- (sin items)")
    return int(max(1, ops)), montaje


@router.post("/leads/{id_lead}/estado_ex")
def estado_ex(
    id_lead: int,
    payload: Dict[str, Any] = Body(...),
):
    """
    payload:
      - id_estado: int (requerido)
      - motivo: str (opcional; si Declinado -> se agrega a leads.notas)
      - agendar: { hora_inicio, duracion_min, direccion, telefono, montaje_text?, ops? } (opcional; si Confirmado)
    """
    if "id_estado" not in payload:
        raise HTTPException(400, detail="Falta id_estado")

    id_estado = int(payload["id_estado"])
    motivo = (payload.get("motivo") or "").strip()
    ag = payload.get("agendar") or {}

    # IDs conocidos por nombre (si existen)
    id_confirmado = _estado_id_by_name("Confirmado")
    id_declinado = _estado_id_by_name("Declinado")

    with engine.begin() as cn:
        lead = cn.execute(
            text("SELECT * FROM leads WHERE id_lead=:id"),
            {"id": id_lead},
        ).mappings().first()
        if not lead:
            raise HTTPException(404, detail="Lead no encontrado")

        # Declinado: motivo se postea a notas
        if id_declinado and id_estado == id_declinado:
            if motivo:
                stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
                add = f"\n[{stamp}] DECLINADO: {motivo}\n"
                cn.execute(
                    text("UPDATE leads SET notas = COALESCE(notas,'') || :add WHERE id_lead=:id"),
                    {"add": add, "id": id_lead},
                )

        # Confirmado: preagenda mínima sin romper (sin 500)
        if id_confirmado and id_estado == id_confirmado:
            # fecha_evento base
            fe: Optional[date] = lead.get("fecha_evento")
            if not fe:
                # si no hay fecha_evento, igual permite confirmar pero deja pendiente
                fe = date.today()

            # hora/duración (editable)
            hora_inicio = (ag.get("hora_inicio") or "").strip() or "TBD"
            dur_min = int(ag.get("duracion_min") or 240)  # default 4h

            # datos obligatorios para “agenda” (permitimos TBD pero marca pendiente)
            direccion = (ag.get("direccion") or "").strip()
            telefono = (ag.get("telefono") or "").strip()

            # comuna nombre
            comuna = cn.execute(
                text(
                    """
                    SELECT c.nombre
                    FROM comunas c
                    WHERE c.id_comuna=:id
                    """
                ),
                {"id": lead.get("id_comuna")},
            ).scalar()

            # marca nombre
            marca = cn.execute(
                text("SELECT m.nombre FROM marcas m WHERE m.id_marca=:id"),
                {"id": lead.get("id_marca")},
            ).scalar()

            title = f"{lead.get('nombre_cliente','(Sin nombre)')} - {marca or 'Marca'}"
            location = comuna or "TBD"

            # Items cotización (si existe)
            q = _latest_quote_for_lead(id_lead)
            items = _quote_items(int(q["id_cotizacion"])) if q else []
            ops_calc, montaje_calc = _calc_ops_montaje(items)

            ops = int(ag.get("ops") or ops_calc)
            montaje_text = (ag.get("montaje_text") or montaje_calc).strip()

            # products_text
            products_text = ""
            if q:
                products_text += f"Cotización: {q.get('num_cotizacion') or q.get('id_cotizacion')}\n"
            if items:
                products_text += "Items:\n" + "\n".join(
                    [f"- {i.get('nombre_producto')} x{i.get('cantidad')}" for i in items[:50]]
                )

            # description (estilo “google calendar” pero en texto)
            desc_parts = [
                f"CLIENTE: {lead.get('nombre_cliente','')}",
                f"MARCA: {marca or ''}",
                f"COMUNA: {location}",
                f"DIRECCIÓN: {direccion or 'TBD'}",
                f"TELÉFONO: {telefono or 'TBD'}",
                f"OPERADORES: {ops}",
                f"MONTAJE:\n{montaje_text}",
            ]
            if products_text:
                desc_parts.append(products_text)

            description = "\n\n".join(desc_parts)

            # Fechas start/end si hora_inicio tiene formato HH:MM
            pre_start = None
            pre_end = None
            try:
                if hora_inicio != "TBD":
                    t0 = _safe_parse_time(hora_inicio)
                    dt0 = datetime.combine(fe, t0)
                    dt1 = dt0 + timedelta(minutes=dur_min)
                    # si cae exactamente a medianoche, usa 23:59 del día anterior
                    if dt1.time() == time(0, 0):
                        dt1 = datetime.combine(dt1.date() - timedelta(days=1), time(23, 59))
                    pre_start = dt0
                    pre_end = dt1
            except Exception:
                # no rompemos por hora inválida
                pre_start = None
                pre_end = None

            cn.execute(
                text(
                    """
                    UPDATE leads SET
                      pre_start=:ps,
                      pre_end=:pe,
                      pre_location=:pl,
                      pre_products_text=:ppt,
                      pre_montaje_text=:pmt,
                      pre_ops=:ops,
                      pre_description=:pdesc,
                      pendiente_agendar=TRUE
                    WHERE id_lead=:id
                    """
                ),
                {
                    "ps": pre_start,
                    "pe": pre_end,
                    "pl": location,
                    "ppt": products_text or None,
                    "pmt": montaje_text or None,
                    "ops": ops,
                    "pdesc": description or None,
                    "id": id_lead,
                },
            )

        # Update estado (siempre)
        cn.execute(
            text("UPDATE leads SET id_estado=:e WHERE id_lead=:id"),
            {"e": id_estado, "id": id_lead},
        )

        updated = cn.execute(
            text("SELECT * FROM leads WHERE id_lead=:id"),
            {"id": id_lead},
        ).mappings().first()

    return dict(updated) if updated else {"ok": True}
