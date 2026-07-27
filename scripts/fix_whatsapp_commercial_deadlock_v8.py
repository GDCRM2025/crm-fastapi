from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTER = ROOT / "backend" / "routers" / "whatsapp_commercial.py"
MIGRATION = ROOT / "scripts" / "sql" / "whatsapp_commercial_schema_v8.sql"


def main() -> None:
    text = ROUTER.read_text(encoding="utf-8")

    # Nunca ejecutar ALTER TABLE dentro de una petición HTTP. Eso provoca locks y deadlocks.
    text = text.replace("    _ensure_schema(db)\n", "")

    # _ensure_schema queda como referencia, pero no debe ejecutarse desde endpoints.
    marker = "def _ensure_schema(db: Session) -> None:\n"
    if marker not in text:
        raise SystemExit("No se encontro _ensure_schema")

    ROUTER.write_text(text, encoding="utf-8")

    MIGRATION.parent.mkdir(parents=True, exist_ok=True)
    MIGRATION.write_text(
        """BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

ALTER TABLE public.whatsapp_channels
  ADD COLUMN IF NOT EXISTS assignment_mode TEXT NOT NULL DEFAULT 'direct';
ALTER TABLE public.whatsapp_channels
  ADD COLUMN IF NOT EXISTS is_dispatch BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE public.whatsapp_channels
  ADD COLUMN IF NOT EXISTS default_user_id TEXT;

ALTER TABLE public.whatsapp_conversations
  ADD COLUMN IF NOT EXISTS selected_lead_id BIGINT;
ALTER TABLE public.whatsapp_conversations
  ADD COLUMN IF NOT EXISTS assigned_user_id TEXT;
ALTER TABLE public.whatsapp_conversations
  ADD COLUMN IF NOT EXISTS assigned_user_name TEXT;

ALTER TABLE public.whatsapp_messages
  ADD COLUMN IF NOT EXISTS media_id TEXT;
ALTER TABLE public.whatsapp_messages
  ADD COLUMN IF NOT EXISTS mime_type TEXT;
ALTER TABLE public.whatsapp_messages
  ADD COLUMN IF NOT EXISTS filename TEXT;
ALTER TABLE public.whatsapp_messages
  ADD COLUMN IF NOT EXISTS caption TEXT;
ALTER TABLE public.whatsapp_messages
  ADD COLUMN IF NOT EXISTS media_size BIGINT;

CREATE INDEX IF NOT EXISTS ix_whatsapp_conversations_selected_lead
  ON public.whatsapp_conversations(selected_lead_id);

COMMIT;
""",
        encoding="utf-8",
    )

    remaining = text.count("_ensure_schema(db)")
    if remaining:
        raise SystemExit(f"Todavia quedan {remaining} llamadas a _ensure_schema(db)")

    print("GREENIE_DEADLOCK_V8_OK")
    print(f"ROUTER={ROUTER}")
    print(f"MIGRATION={MIGRATION}")


if __name__ == "__main__":
    main()
