-- migrations/2026_01_29_crm_ajustes.sql
-- Ejecutar en BD (schema public)

-- Helper: updated_at trigger
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS trigger AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- =========================
-- MARCAS
-- =========================
CREATE TABLE IF NOT EXISTS public.marcas (
  id_marca    serial PRIMARY KEY,
  marca       text NOT NULL UNIQUE,
  logo_path   text,
  is_active   boolean NOT NULL DEFAULT true
);

-- Si la tabla existe pero no tiene columnas
DO $$
BEGIN
  IF to_regclass('public.marcas') IS NOT NULL THEN
    IF NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema='public' AND table_name='marcas' AND column_name='logo_path'
    ) THEN
      ALTER TABLE public.marcas ADD COLUMN logo_path text;
    END IF;

    IF NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema='public' AND table_name='marcas' AND column_name='is_active'
    ) THEN
      ALTER TABLE public.marcas ADD COLUMN is_active boolean NOT NULL DEFAULT true;
    END IF;
  END IF;
END $$;

-- =========================
-- PRODUCTOS
-- =========================
CREATE TABLE IF NOT EXISTS public.productos (
  id_producto serial PRIMARY KEY,
  producto    text NOT NULL,
  descripcion text,
  marca       text NOT NULL,
  costo       numeric(12,2) NOT NULL DEFAULT 0,
  is_active   boolean NOT NULL DEFAULT true,
  orden       int NOT NULL DEFAULT 0,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Drop precio_lista si existe
DO $$
BEGIN
  IF to_regclass('public.productos') IS NOT NULL THEN
    IF EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema='public' AND table_name='productos' AND column_name='precio_lista'
    ) THEN
      ALTER TABLE public.productos DROP COLUMN precio_lista;
    END IF;

    -- Asegurar columnas
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='productos' AND column_name='descripcion')
      THEN ALTER TABLE public.productos ADD COLUMN descripcion text;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='productos' AND column_name='marca')
      THEN ALTER TABLE public.productos ADD COLUMN marca text;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='productos' AND column_name='costo')
      THEN ALTER TABLE public.productos ADD COLUMN costo numeric(12,2) NOT NULL DEFAULT 0;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='productos' AND column_name='is_active')
      THEN ALTER TABLE public.productos ADD COLUMN is_active boolean NOT NULL DEFAULT true;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='productos' AND column_name='orden')
      THEN ALTER TABLE public.productos ADD COLUMN orden int NOT NULL DEFAULT 0;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='productos' AND column_name='created_at')
      THEN ALTER TABLE public.productos ADD COLUMN created_at timestamptz NOT NULL DEFAULT now();
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='productos' AND column_name='updated_at')
      THEN ALTER TABLE public.productos ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();
    END IF;

  END IF;
END $$;

-- Trigger updated_at productos
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_productos_updated_at') THEN
    CREATE TRIGGER trg_productos_updated_at
    BEFORE UPDATE ON public.productos
    FOR EACH ROW
    EXECUTE FUNCTION public.set_updated_at();
  END IF;
END $$;

-- =========================
-- LEADS
-- =========================
CREATE TABLE IF NOT EXISTS public.leads (
  id_lead          serial PRIMARY KEY,
  cliente          text NOT NULL,
  email            text,
  telefono         text,
  direccion        text,
  fecha_evento     date,
  estado           text NOT NULL DEFAULT 'NUEVO',
  notas            text NOT NULL DEFAULT '',
  declinado_motivo text,
  declinado_at     timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()
);

-- Ajustes leads si ya existe
DO $$
BEGIN
  IF to_regclass('public.leads') IS NOT NULL THEN
    -- Si existe nombre_cliente, backfill a cliente
    IF EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema='public' AND table_name='leads' AND column_name='nombre_cliente'
    ) THEN
      IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name='leads' AND column_name='cliente'
      ) THEN
        ALTER TABLE public.leads ADD COLUMN cliente text;
      END IF;

      UPDATE public.leads
      SET cliente = COALESCE(cliente, nombre_cliente)
      WHERE cliente IS NULL;
    END IF;

    -- cliente
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='leads' AND column_name='cliente')
      THEN ALTER TABLE public.leads ADD COLUMN cliente text;
    END IF;

    -- estado
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='leads' AND column_name='estado')
      THEN ALTER TABLE public.leads ADD COLUMN estado text NOT NULL DEFAULT 'NUEVO';
    END IF;

    -- quitar estado_color si existe
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='leads' AND column_name='estado_color')
      THEN ALTER TABLE public.leads DROP COLUMN estado_color;
    END IF;

    -- notas y declinado
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='leads' AND column_name='notas')
      THEN ALTER TABLE public.leads ADD COLUMN notas text NOT NULL DEFAULT '';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='leads' AND column_name='declinado_motivo')
      THEN ALTER TABLE public.leads ADD COLUMN declinado_motivo text;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='leads' AND column_name='declinado_at')
      THEN ALTER TABLE public.leads ADD COLUMN declinado_at timestamptz;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='leads' AND column_name='created_at')
      THEN ALTER TABLE public.leads ADD COLUMN created_at timestamptz NOT NULL DEFAULT now();
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='leads' AND column_name='updated_at')
      THEN ALTER TABLE public.leads ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();
    END IF;
  END IF;
END $$;

-- Trigger updated_at leads
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_leads_updated_at') THEN
    CREATE TRIGGER trg_leads_updated_at
    BEFORE UPDATE ON public.leads
    FOR EACH ROW
    EXECUTE FUNCTION public.set_updated_at();
  END IF;
END $$;

-- =========================
-- LEAD_NOTAS (bitácora)
-- =========================
CREATE TABLE IF NOT EXISTS public.lead_notas (
  id_nota    serial PRIMARY KEY,
  id_lead    int NOT NULL REFERENCES public.leads(id_lead) ON DELETE CASCADE,
  tipo       text NOT NULL, -- NOTA | COTIZACION | DECLINADO | ESTADO
  texto      text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by text
);

-- =========================
-- COTIZACIONES
-- =========================
CREATE TABLE IF NOT EXISTS public.cotizaciones (
  id_cotizacion serial PRIMARY KEY,
  id_lead       int NOT NULL REFERENCES public.leads(id_lead) ON DELETE CASCADE,
  numero        text UNIQUE,
  fecha_emision date NOT NULL DEFAULT current_date,
  vigencia_dias int NOT NULL DEFAULT 15,
  subtotal      numeric(12,2) NOT NULL DEFAULT 0,
  iva           numeric(12,2) NOT NULL DEFAULT 0,
  total         numeric(12,2) NOT NULL DEFAULT 0,
  condiciones   text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  created_by    text
);

CREATE TABLE IF NOT EXISTS public.cotizacion_items (
  id_item         serial PRIMARY KEY,
  id_cotizacion   int NOT NULL REFERENCES public.cotizaciones(id_cotizacion) ON DELETE CASCADE,
  id_producto     int REFERENCES public.productos(id_producto),
  producto        text NOT NULL,
  descripcion     text,
  cantidad        int NOT NULL DEFAULT 1,
  precio_unitario numeric(12,2) NOT NULL DEFAULT 0,
  total_linea     numeric(12,2) NOT NULL DEFAULT 0,
  marca           text
);

-- =========================
-- Indexes
-- =========================
CREATE INDEX IF NOT EXISTS idx_leads_estado ON public.leads(estado);
CREATE INDEX IF NOT EXISTS idx_cotizaciones_lead ON public.cotizaciones(id_lead);
CREATE INDEX IF NOT EXISTS idx_items_cotizacion ON public.cotizacion_items(id_cotizacion);
