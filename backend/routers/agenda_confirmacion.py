from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Path
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.routers.auth import get_current_user
from backend.routers.leads_agenda import move_lead_and_maybe_agenda
from backend.routers.tools import approve_agenda


router = APIRouter(prefix="/leads", tags=["agenda-confirmacion"])
_LOCK_BASE = 26073000


def _json_body(response: JSONResponse) -> dict[str, Any]:
    try:
        raw = response.body.decode("utf-8", "replace")
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {"detail": value}
    except Exception:
        return {"detail": "Respuesta inválida del proceso de agenda"}


def _confirmed_state_id(db: Session) -> int:
    value = db.execute(
        text(
            """
            SELECT id_estado
            FROM public.estados_lead
            WHERE UPPER(COALESCE(nombre,'')) LIKE '%CONFIRM%'
            ORDER BY id_estado
            LIMIT 1
            """
        )
    ).scalar()
    if value is None:
        raise HTTPException(status_code=500, detail="No existe el estado Confirmado")
    return int(value)


def _table_exists(db: Session, table: str) -> bool:
    try:
        return bool(
            db.execute(
                text("SELECT to_regclass(:name)"),
                {"name": f"public.{table}"},
            ).scalar()
        )
    except Exception:
        db.rollback()
        return False


def _stable_key(id_lead: int, payload: dict[str, Any], supplied: str | None) -> str:
    supplied = str(supplied or payload.get("idempotency_key") or "").strip()
    if supplied:
        return supplied[:180]
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(f"{id_lead}|{canonical}".encode("utf-8")).hexdigest()
    return f"agenda:{id_lead}:{digest}"


def _warnings(payload: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    start = str(payload.get("start_time") or "").strip()
    end = str(payload.get("end_time") or "").strip()
    address = str(payload.get("direccion") or "").strip()
    hr_tbd = bool(payload.get("hr_tbd")) or not start or not end
    dir_tbd = not address
    if hr_tbd and dir_tbd:
        warnings.append("HR Y DIR TBD")
    elif hr_tbd:
        warnings.append("HR TBD")
    elif dir_tbd:
        warnings.append("DIR TBD")
    return warnings


def _agenda_type(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("blocks"), list) and payload.get("blocks"):
        return "bloques"
    if bool(payload.get("multi_day_mode")) or int(payload.get("days_n") or 1) > 1:
        return "varios_dias"
    return "unico"


def _duration_text(start_at: Any, end_at: Any) -> str | None:
    try:
        start = datetime.fromisoformat(str(start_at).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(end_at).replace("Z", "+00:00"))
        minutes = int((end - start).total_seconds() // 60)
        if minutes <= 0:
            return None
        hours, mins = divmod(minutes, 60)
        if hours and mins:
            return f"{hours} h {mins} min"
        if hours:
            return f"{hours} h"
        return f"{mins} min"
    except Exception:
        return None


def _client_summary(lead: dict[str, Any], move: dict[str, Any], warnings: list[str]) -> str:
    events = move.get("eventos") if isinstance(move.get("eventos"), list) else []
    if not events and isinstance(move.get("evento"), dict):
        events = [move["evento"]]
    lines = [
        f"CLIENTE: {lead.get('cliente') or lead.get('nombre_cliente') or '—'}",
        f"TELÉFONO: {lead.get('telefono') or '—'}",
        f"DIRECCIÓN: {lead.get('direccion') or 'DIR TBD'}",
    ]
    for index, event in enumerate(events, start=1):
        lines.extend(
            [
                "",
                f"EVENTO {index}",
                f"FECHA: {str(event.get('day') or event.get('start_at') or '')[:10] or '—'}",
                f"HORARIO: {str(event.get('start_at') or '')[11:16] or 'HR TBD'} - {str(event.get('end_at') or '')[11:16] or 'HR TBD'}",
                f"DURACIÓN: {_duration_text(event.get('start_at'), event.get('end_at')) or 'HR TBD'}",
                f"LUGAR: {event.get('location') or 'DIR TBD'}",
                "PRODUCTOS:",
                str(event.get("products_text") or "—"),
                "MONTAJE:",
                str(event.get("montaje_text") or "—"),
                f"OPERADORES: {event.get('ops') or '—'}",
            ]
        )
    if warnings:
        lines.extend(["", "ALERTAS:", *[f"- {item}" for item in warnings]])
    return "\n".join(lines).strip()


def _existing_result(db: Session, id_lead: int, confirmado_id: int) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT id_lead, cliente, telefono, direccion, id_estado,
                   calendar_event_id, calendar_html_link,
                   calendar_event_ids_json, calendar_html_links_json,
                   calendar_start, calendar_end, pendiente_agendar
            FROM public.leads
            WHERE id_lead=:id
            LIMIT 1
            """
        ),
        {"id": id_lead},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Lead no existe")
    if int(row.get("id_estado") or 0) != int(confirmado_id):
        return None
    event_id = str(row.get("calendar_event_id") or "").strip()
    if not event_id:
        return None
    try:
        event_ids = json.loads(str(row.get("calendar_event_ids_json") or "[]"))
    except Exception:
        event_ids = [event_id]
    try:
        links = json.loads(str(row.get("calendar_html_links_json") or "[]"))
    except Exception:
        links = [row.get("calendar_html_link")]
    return {
        "ok": True,
        "idempotent_replay": True,
        "id_lead": id_lead,
        "estado": "Confirmado",
        "calendar_event_id": event_id,
        "calendar_html_link": row.get("calendar_html_link"),
        "calendar_event_ids": event_ids,
        "calendar_html_links": links,
        "calendar_start": row.get("calendar_start"),
        "calendar_end": row.get("calendar_end"),
        "pendiente_agendar": bool(row.get("pendiente_agendar")),
        "lead": dict(row),
        "warnings": [],
    }


def _audit_start(
    db: Session,
    *,
    id_lead: int,
    key: str,
    payload: dict[str, Any],
    created_by: str,
) -> None:
    if not _table_exists(db, "lead_agenda_confirmations"):
        return
    db.execute(
        text(
            """
            INSERT INTO public.lead_agenda_confirmations (
                id_lead, idempotency_key, request_payload, status, created_by
            )
            VALUES (:id, :key, CAST(:payload AS JSONB), 'processing', :created_by)
            ON CONFLICT (idempotency_key) DO UPDATE SET
                request_payload=EXCLUDED.request_payload,
                status=CASE
                    WHEN lead_agenda_confirmations.status='completed' THEN 'completed'
                    ELSE 'processing'
                END,
                updated_at=now()
            """
        ),
        {
            "id": id_lead,
            "key": key,
            "payload": json.dumps(payload, ensure_ascii=False, default=str),
            "created_by": created_by,
        },
    )
    db.commit()


def _audit_finish(
    db: Session,
    *,
    key: str,
    status: str,
    response: dict[str, Any],
) -> None:
    if not _table_exists(db, "lead_agenda_confirmations"):
        return
    db.execute(
        text(
            """
            UPDATE public.lead_agenda_confirmations
            SET status=:status,
                response_payload=CAST(:response AS JSONB),
                completed_at=CASE WHEN :status='completed' THEN now() ELSE completed_at END,
                updated_at=now()
            WHERE idempotency_key=:key
            """
        ),
        {
            "key": key,
            "status": status,
            "response": json.dumps(response, ensure_ascii=False, default=str),
        },
    )
    db.commit()


@router.post("/{id_lead}/confirmar_agendamiento")
def confirmar_agendamiento(
    id_lead: int = Path(..., ge=1),
    payload: dict[str, Any] = Body(default_factory=dict),
    x_idempotency_key: str | None = Header(default=None, alias="X-Idempotency-Key"),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    confirmado_id = _confirmed_state_id(db)
    key = _stable_key(id_lead, payload, x_idempotency_key)
    lock_key = _LOCK_BASE + (int(id_lead) % 999999)
    got_lock = False
    try:
        got_lock = bool(db.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key}).scalar())
        if not got_lock:
            raise HTTPException(status_code=409, detail="Este lead ya se está agendando. Espera unos segundos.")

        existing = _existing_result(db, id_lead, confirmado_id)
        if existing:
            return existing

        who = str(
            user.get("username")
            or user.get("email")
            or user.get("name")
            or user.get("nombre")
            or user.get("id")
            or "CRM"
        ).strip()

        normalized = dict(payload or {})
        normalized.update(
            {
                "id_estado": confirmado_id,
                "agendar": True,
                "dry_run": False,
                "idempotency_key": key,
                "tipo_agendamiento": _agenda_type(normalized),
            }
        )
        warnings = _warnings(normalized)
        normalized["tbd"] = {
            "hora": any("HR" in item for item in warnings),
            "direccion": any("DIR" in item for item in warnings),
        }

        _audit_start(
            db,
            id_lead=id_lead,
            key=key,
            payload=normalized,
            created_by=who,
        )

        move_result = move_lead_and_maybe_agenda(
            id_lead=id_lead,
            payload=normalized,
            user=user,
        )
        if isinstance(move_result, JSONResponse):
            detail = _json_body(move_result)
            raise HTTPException(status_code=move_result.status_code, detail=detail)
        if not isinstance(move_result, dict) or not move_result.get("ok"):
            raise HTTPException(status_code=500, detail="No se pudo preparar la agenda")

        approve_result = approve_agenda(
            id_lead=id_lead,
            x_user=who,
            db=db,
            me=user,
        )
        if isinstance(approve_result, JSONResponse):
            body = _json_body(approve_result)
            if approve_result.status_code >= 400:
                _audit_finish(db, key=key, status="failed", response=body)
                raise HTTPException(status_code=approve_result.status_code, detail=body)
            approve_payload = body
        elif isinstance(approve_result, dict):
            approve_payload = approve_result
        else:
            approve_payload = {"ok": False, "detail": "Respuesta inválida de Google Calendar"}

        if not approve_payload.get("ok") or not str(approve_payload.get("calendar_event_id") or "").strip():
            _audit_finish(db, key=key, status="failed", response=approve_payload)
            raise HTTPException(status_code=502, detail=approve_payload)

        lead_row = db.execute(
            text(
                """
                SELECT l.*, COALESCE(e.nombre,'') AS estado_nombre,
                       COALESCE(c.nombre, c.comuna, '') AS comuna_nombre,
                       COALESCE(m.nombre, m.marca, '') AS marca_nombre
                FROM public.leads l
                LEFT JOIN public.estados_lead e ON e.id_estado=l.id_estado
                LEFT JOIN public.comunas c ON c.id_comuna=l.id_comuna
                LEFT JOIN public.marcas m ON m.id_marca=l.id_marca
                WHERE l.id_lead=:id
                LIMIT 1
                """
            ),
            {"id": id_lead},
        ).mappings().first()
        lead = dict(lead_row or {})
        summary = _client_summary(lead, move_result, warnings)

        result = {
            "ok": True,
            "idempotent_replay": False,
            "idempotency_key": key,
            "id_lead": id_lead,
            "estado": "Confirmado",
            "tipo_agendamiento": normalized["tipo_agendamiento"],
            "tiene_montaje_previo": bool(normalized.get("montaje_event") or normalized.get("montaje_previo")),
            "warnings": warnings,
            "resumen_cliente": summary,
            "evento": move_result.get("evento"),
            "eventos": move_result.get("eventos") or [],
            "calendar_event_id": approve_payload.get("calendar_event_id"),
            "calendar_html_link": approve_payload.get("calendar_html_link"),
            "calendar_event_ids": approve_payload.get("calendar_event_ids") or [],
            "calendar_html_links": approve_payload.get("calendar_html_links") or [],
            "calendar_ids": approve_payload.get("calendar_ids") or [],
            "gcal_error": approve_payload.get("gcal_error"),
            "lead": approve_payload.get("lead") or lead,
        }
        _audit_finish(db, key=key, status="completed", response=result)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        failure = {
            "ok": False,
            "where": "confirmar_agendamiento",
            "type": exc.__class__.__name__,
            "error": str(exc),
        }
        try:
            _audit_finish(db, key=key, status="failed", response=failure)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=failure) from exc
    finally:
        if got_lock:
            try:
                db.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})
            except Exception:
                pass
