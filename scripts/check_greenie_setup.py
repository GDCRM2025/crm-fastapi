from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from backend.core.database import engine
from backend.main import app
from backend.routers.auth import get_current_user
from backend.routers.whatsapp_webhook import _runtime_env


REQUIRED_ENV = (
    "META_APP_SECRET",
    "WHATSAPP_ACCESS_TOKEN",
    "WHATSAPP_PHONE_NUMBER_ID",
    "WHATSAPP_WABA_ID",
    "WHATSAPP_WEBHOOK_VERIFY_TOKEN",
)

REQUIRED_TABLES = (
    "whatsapp_webhook_events",
    "whatsapp_channels",
    "whatsapp_contacts",
    "whatsapp_conversations",
    "whatsapp_messages",
    "whatsapp_conversation_leads",
    "whatsapp_message_reactions",
)

REQUIRED_ROUTES = (
    "/gia/whatsapp/conversations",
    "/gia/whatsapp/conversations/{conversation_id}/messages-v2",
    "/gia/whatsapp/conversations/{conversation_id}/send-v2",
    "/gia/whatsapp/conversations/{conversation_id}/send-media-v2",
    "/gia/whatsapp/conversations/{conversation_id}/commercial-360",
    "/gia/whatsapp/conversations/{conversation_id}/create-lead",
    "/gia/whatsapp/conversations/{conversation_id}/leads/{lead_id}/send-quote",
)


def _configured(name: str) -> bool:
    value = _runtime_env(name).strip()
    lowered = value.lower()
    return bool(value) and not any(
        marker in lowered
        for marker in ("change_me", "replace-with", "example", "placeholder")
    )


def main() -> int:
    failures: list[str] = []

    for name in REQUIRED_ENV:
        ok = _configured(name)
        print(f"ENV {name}: {'OK' if ok else 'FALTA'}")
        if not ok:
            failures.append(f"Variable requerida no configurada: {name}")

    validate_signature = _runtime_env(
        "WHATSAPP_VALIDATE_SIGNATURE", "true"
    ).lower() in ("1", "true", "yes", "on")
    print(
        "FIRMAS WEBHOOK: "
        + ("ACTIVAS" if validate_signature else "DESACTIVADAS")
    )
    if not validate_signature:
        failures.append("WHATSAPP_VALIDATE_SIGNATURE debe estar activo")

    greenie_routes = {
        route.path: route
        for route in app.routes
        if route.path.startswith("/gia/whatsapp")
    }
    print(f"RUTAS GREENIE: {len(greenie_routes)}")
    for path in REQUIRED_ROUTES:
        if path not in greenie_routes:
            failures.append(f"Ruta Greenie ausente: {path}")

    for path, route in greenie_routes.items():
        dependencies = {
            dependency.call for dependency in route.dependant.dependencies
        }
        if get_current_user not in dependencies:
            failures.append(f"Ruta Greenie sin autenticación: {path}")

    try:
        with engine.connect() as connection:
            database = connection.execute(
                text("SELECT current_database()")
            ).scalar()
            print(f"BASE DE DATOS: OK ({database})")
            for table in REQUIRED_TABLES:
                exists = connection.execute(
                    text("SELECT to_regclass(:name)"),
                    {"name": f"public.{table}"},
                ).scalar()
                print(f"TABLA {table}: {'OK' if exists else 'FALTA'}")
                if not exists:
                    failures.append(f"Tabla Greenie ausente: {table}")
    except Exception as exc:
        failures.append(
            f"No se pudo verificar PostgreSQL: {type(exc).__name__}"
        )

    if failures:
        print("\nGREENIE NO ESTA LISTO:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("\nGREENIE LISTO")
    return 0


if __name__ == "__main__":
    sys.exit(main())
