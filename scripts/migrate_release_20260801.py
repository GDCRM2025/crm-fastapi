#!/usr/bin/env python3
"""Migración idempotente para encuestas y plataformas dinámicas."""

import sys
from pathlib import Path

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.db import engine


DDL = """
CREATE TABLE IF NOT EXISTS public.plataformas (
  id_plataforma SERIAL PRIMARY KEY,
  nombre TEXT NOT NULL UNIQUE,
  descripcion TEXT,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  orden INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.event_surveys (
  id_survey BIGSERIAL PRIMARY KEY,
  token TEXT NOT NULL UNIQUE,
  id_lead BIGINT NOT NULL,
  event_day DATE,
  cliente TEXT,
  email TEXT,
  telefono TEXT,
  marca TEXT,
  comuna TEXT,
  sent_at TIMESTAMPTZ,
  sent_by TEXT,
  responded_at TIMESTAMPTZ,
  ratings JSONB NOT NULL DEFAULT '{}'::jsonb,
  comment TEXT,
  google_review_clicked_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_event_surveys_lead_day
  ON public.event_surveys(id_lead,event_day);
CREATE INDEX IF NOT EXISTS ix_event_surveys_responded
  ON public.event_surveys(responded_at);

ALTER TABLE public.marcas ADD COLUMN IF NOT EXISTS color_primary TEXT;
ALTER TABLE public.marcas ADD COLUMN IF NOT EXISTS color_secondary TEXT;
ALTER TABLE public.marcas ADD COLUMN IF NOT EXISTS google_review_url TEXT;

ALTER TABLE public.cotizaciones ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 0;
ALTER TABLE public.cotizaciones
  ALTER COLUMN version TYPE INTEGER
  USING CASE
    WHEN btrim(COALESCE(version::text,'')) ~ '^[0-9]+$'
      THEN btrim(version::text)::integer
    ELSE 0
  END;
ALTER TABLE public.cotizaciones ALTER COLUMN version SET DEFAULT 0;
UPDATE public.cotizaciones SET version=0 WHERE version IS NULL;
ALTER TABLE public.cotizaciones ALTER COLUMN version SET NOT NULL;
ALTER TABLE public.cotizaciones DROP CONSTRAINT IF EXISTS cotizaciones_numero_key;
WITH ranked AS (
  SELECT
    id_cotizacion,
    row_number() OVER (
      PARTITION BY COALESCE(marca,''), numero
      ORDER BY version, created_at NULLS LAST, id_cotizacion
    ) - 1 AS normalized_version
  FROM public.cotizaciones
)
UPDATE public.cotizaciones AS c
SET version = ranked.normalized_version
FROM ranked
WHERE c.id_cotizacion = ranked.id_cotizacion
  AND c.version IS DISTINCT FROM ranked.normalized_version;
CREATE UNIQUE INDEX IF NOT EXISTS uq_cotizaciones_marca_numero_version
  ON public.cotizaciones ((COALESCE(marca,'')), numero, version);
"""

PLATFORMS = (
    ("PÁGINA WEB", "Formulario o contacto desde página web", 10),
    ("CARTA WEB", "Carta o catálogo digital", 20),
    ("BOTÓN WHATSAPP", "Botón de WhatsApp en página o campaña", 30),
    ("WHATSAPP", "Contacto directo por WhatsApp", 40),
    ("INSTAGRAM", "Contacto o campaña de Instagram", 50),
    ("FACEBOOK", "Contacto o campaña de Facebook", 60),
    ("REFERIDO", "Cliente recomendado por otra persona", 70),
    ("OTRO", "Origen no clasificado o plataforma futura", 999),
)


def main() -> None:
    with engine.begin() as connection:
        connection.execute(text(DDL))
        for name, description, order in PLATFORMS:
            connection.execute(
                text(
                    """
                    INSERT INTO public.plataformas(nombre,descripcion,orden,is_active)
                    VALUES (:name,:description,:order,TRUE)
                    ON CONFLICT (nombre) DO UPDATE SET
                      descripcion=EXCLUDED.descripcion,
                      orden=EXCLUDED.orden,
                      updated_at=now()
                    """
                ),
                {"name": name, "description": description, "order": order},
            )
    print("MIGRATION_20260801_OK")


if __name__ == "__main__":
    main()
