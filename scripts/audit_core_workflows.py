#!/usr/bin/env python3
"""Prueba CRUD integrada del CRM en una base aislada de auditoría.

Se niega a ejecutar si el nombre de la base no contiene ``audit`` o ``test``.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url

from backend.db import engine

# El proyecto aún conserva dos capas de conexión. Para la auditoría ambas deben
# apuntar inequívocamente a la misma base autorizada antes de importar routers.
os.environ.setdefault("DATABASE_URL", str(engine.url))

from backend.main import app
from backend.routers.auth import get_current_user


REPORT: list[dict[str, Any]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    REPORT.append({"name": name, "ok": bool(ok), "detail": detail[:500]})
    print(f"[{'OK' if ok else 'FAIL'}] {name}: {detail}")


def expect(name: str, response, statuses: set[int] = {200}) -> dict[str, Any]:
    ok = response.status_code in statuses
    try:
        body = response.json()
    except Exception:
        body = {}
    detail = f"HTTP {response.status_code}"
    if not ok:
        detail += f" {str(body or response.text)[:300]}"
    record(name, ok, detail)
    if not ok:
        raise RuntimeError(f"{name}: {detail}")
    return body if isinstance(body, dict) else {"items": body}


def main() -> int:
    url = make_url(str(engine.url))
    database = str(url.database or "")
    isolated = any(marker in database.lower() for marker in ("audit", "test"))
    explicitly_allowed_local = (
        os.getenv("CRM_AUDIT_ALLOW_LOCAL_BDGD") == "YES_I_HAVE_A_BACKUP"
        and str(url.host or "") in {"127.0.0.1", "localhost", "::1"}
        and any((ROOT / "backups").glob("local_before_crm_audit_*.dump"))
    )
    if not (isolated or explicitly_allowed_local):
        raise SystemExit(f"SEGURIDAD: la base {database!r} no está autorizada para auditoría destructiva")

    inspector = inspect(engine)
    marca_columns = {x["name"] for x in inspector.get_columns("marcas", schema="public")}
    product_columns = {x["name"] for x in inspector.get_columns("productos", schema="public")}
    marca_label = "COALESCE(NULLIF(marca,''),'')" if "marca" in marca_columns else "COALESCE(NULLIF(nombre,''),'')"
    if {"marca", "nombre"} <= marca_columns:
        marca_label = "COALESCE(NULLIF(nombre,''),NULLIF(marca,''),'')"
    product_label = "producto" if "producto" in product_columns else "nombre"
    product_price = next((x for x in ("precio", "precio_venta", "valor", "precio_unitario") if x in product_columns), None)
    product_price_expr = f"COALESCE({product_price},0)" if product_price else "0"

    with engine.connect() as connection:
        brand_rows = connection.execute(
            text(f"SELECT id_marca, {marca_label} FROM marcas WHERE id_marca > 0 ORDER BY id_marca")
        ).all()
        state_rows = connection.execute(
            text("SELECT id_estado, nombre FROM estados_lead ORDER BY id_estado")
        ).all()
        product = connection.execute(
            text(f"SELECT id_producto, {product_label}, {product_price_expr} FROM productos ORDER BY id_producto LIMIT 1")
        ).first()
        comuna = connection.execute(text("SELECT id_comuna FROM comunas ORDER BY id_comuna LIMIT 1")).scalar()

    if not brand_rows or not state_rows or not product:
        raise SystemExit("La copia de auditoría no tiene catálogos suficientes")
    brands = {str(name): int(identifier) for identifier, name in brand_rows}
    states = {str(name).upper(): int(identifier) for identifier, name in state_rows}
    first_brand_name, first_brand_id = next(iter(brands.items()))
    second_brand_id = list(brands.values())[1] if len(brands) > 1 else first_brand_id
    new_state = next((v for k, v in states.items() if "NUEVO" in k), next(iter(states.values())))
    contacted_state = next((v for k, v in states.items() if "CONTACT" in k), None)
    confirmed_state = next((v for k, v in states.items() if "CONFIRM" in k), None)

    current = {
        "user": {
            "id": 1,
            "id_usuario": 1,
            "username": "codex-audit-admin",
            "name": "Codex Audit",
            "role": "SUPERADMIN",
            "rol": "SUPERADMIN",
            "marcas": list(brands.values()),
        }
    }
    app.dependency_overrides[get_current_user] = lambda: current["user"]
    client = TestClient(app, raise_server_exceptions=False)

    # Seguridad: sin identidad debe ser rechazado.
    app.dependency_overrides.pop(get_current_user, None)
    expect("auth.leads.reject_anonymous", client.get("/leads"), {401})
    app.dependency_overrides[get_current_user] = lambda: current["user"]

    expect("health.openapi", client.get("/openapi.json"))
    expect("catalog.leads", client.get("/leads/catalogos"))
    expect("settings.platforms", client.get("/settings/meta/plataformas"))

    event_day = (date.today() + timedelta(days=45)).isoformat()
    lead_payload = {
        "cliente": "CODEX_AUDIT CLIENTE CRUD",
        "email": "codex-audit@example.invalid",
        "telefono": "+56911111111",
        "direccion": "Dirección sintética 123",
        "id_marca": first_brand_id,
        "id_estado": new_state,
        "id_comuna": int(comuna or 0),
        "fecha_evento": event_day,
        "plataforma": "CODEX_AUDIT",
        "notas": "Registro sintético de auditoría",
    }
    created = expect("lead.create", client.post("/leads", json=lead_payload))
    lead_id = int(created["id_lead"])
    lead = expect("lead.read", client.get(f"/leads/{lead_id}"))
    record("lead.read.identity", int((lead.get("lead") or lead).get("id_lead") or 0) == lead_id, str(lead_id))

    expect(
        "lead.update",
        client.put(
            f"/leads/{lead_id}",
            json={
                **lead_payload,
                "cliente": "CODEX_AUDIT CLIENTE ACTUALIZADO",
                "comentario": "Actualización controlada durante auditoría integral",
            },
        ),
    )
    follow = expect(
        "lead.followup_whatsapp",
        client.post(
            f"/leads/{lead_id}/append_note",
            json={"kind": "WSP", "title": "Prueba", "text": "Contacto sintético", "followup": True},
        ),
    )
    if contacted_state:
        record("lead.followup_moves_status", int((follow.get("estado") or {}).get("id_estado") or 0) == contacted_state)
    expect("lead.vcard", client.get(f"/leads/{lead_id}/vcard"))

    product_id, product_name, product_price = int(product[0]), str(product[1]), float(product[2] or 1000)
    quote_payload = {
        "id_lead": lead_id,
        "cliente": "CODEX_AUDIT CLIENTE ACTUALIZADO",
        "marca": first_brand_name,
        "fecha_evento": event_day,
        "tipo_cliente": "EMPRESA",
        "traslado": 10000,
        "descuento_valor": 5,
        "descuento_tipo": "%",
        "items": [{"id_producto": product_id, "cantidad": 2, "precio_unitario": max(product_price, 1000)}],
    }
    quote = expect("quote.create", client.post("/cotizador/cotizar", json=quote_payload))
    quote_id = int(quote["id_cotizacion"])
    expect("quote.read", client.get(f"/quotes/{quote_id}"))
    items = expect("quote.items", client.get(f"/quotes/{quote_id}/items"))
    record("quote.items.snapshot", bool(items.get("items")), product_name)
    expect("quote.history", client.get(f"/quotes/history?id_lead={lead_id}"))
    expect("quote.pdf_template", client.get(f"/quotes/{quote_id}/pdf?debug=1"))
    quote_payload["items"][0]["cantidad"] = 3
    revised = expect("quote.revise", client.put(f"/cotizador/cotizaciones/{quote_id}", json=quote_payload))
    record("quote.revision_created", int(revised.get("id_cotizacion") or 0) != quote_id, str(revised.get("version")))

    # Permisos: un ejecutivo no puede crear en una marca ajena ni borrar.
    current["user"] = {
        "id": 2,
        "id_usuario": 2,
        "username": "codex-audit-executive",
        "name": "Ejecutivo Audit",
        "role": "EJECUTIVO",
        "rol": "EJECUTIVO",
        "marcas": [first_brand_id],
    }
    foreign = dict(lead_payload)
    foreign["cliente"] = "CODEX_AUDIT MARCA AJENA"
    foreign["id_marca"] = second_brand_id
    expect("rbac.executive_foreign_brand", client.post("/leads", json=foreign), {403})
    expect(
        "rbac.executive_delete_denied",
        client.request("DELETE", f"/leads/{lead_id}", json={"motivo": "Intento sintético no autorizado"}),
        {403},
    )

    current["user"] = {
        "id": 1,
        "id_usuario": 1,
        "username": "codex-audit-admin",
        "name": "Codex Audit",
        "role": "SUPERADMIN",
        "rol": "SUPERADMIN",
        "marcas": list(brands.values()),
    }

    if confirmed_state:
        survey_payload = dict(lead_payload)
        survey_payload.update(
            cliente="CODEX_AUDIT ENCUESTA",
            id_estado=confirmed_state,
            fecha_evento=(date.today() - timedelta(days=1)).isoformat(),
        )
        survey_lead = expect("survey.lead_create", client.post("/leads", json=survey_payload))
        survey_lead_id = int(survey_lead["id_lead"])
        pending = expect(
            "survey.pending",
            client.get(f"/surveys/pending?day={(date.today()-timedelta(days=1)).isoformat()}"),
        )
        survey_item = next((x for x in pending.get("items", []) if int(x.get("id_lead") or 0) == survey_lead_id), None)
        record("survey.generated", bool(survey_item))
        if survey_item:
            token = str(survey_item["token"])
            public = expect("survey.public_read", client.get(f"/public/surveys/{token}"))
            questions = public.get("questions") or []
            ratings = {str(q["key"]): 5 for q in questions}
            expect("survey.public_submit", client.post(f"/public/surveys/{token}", json={"ratings": ratings, "comment": "CODEX_AUDIT"}))
            expect("survey.mark_sent", client.post(f"/surveys/{token}/mark_sent"))
            expect("survey.review_click", client.post(f"/public/surveys/{token}/google_review_click"))
            expect("survey.summary", client.get("/surveys/summary?days=30"))

    expect(
        "lead.soft_delete",
        client.request(
            "DELETE",
            f"/leads/{lead_id}",
            json={"motivo": "Eliminación sintética controlada de auditoría"},
        ),
    )
    hidden = expect("lead.deleted_hidden", client.get("/leads?all=1&limit=5000"))
    rows = hidden.get("items") if isinstance(hidden, dict) else []
    record("lead.deleted_not_listed", not any(int(x.get("id_lead") or 0) == lead_id for x in (rows or [])))

    output = ROOT / "reports" / "crm_core_audit_latest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"database": database, "results": REPORT}, ensure_ascii=False, indent=2), encoding="utf-8")
    failed = [x for x in REPORT if not x["ok"]]
    print(f"AUDIT_RESULTS={len(REPORT)} FAILED={len(failed)} REPORT={output}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
