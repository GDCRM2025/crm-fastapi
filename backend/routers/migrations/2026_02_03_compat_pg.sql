-- 2026_02_03_compat_pg.sql
-- Compatibilidad para BD antigua (lead, tipocliente, marcas.nombre)
BEGIN;

-- marcas: agregar columnas esperadas por la app
ALTER TABLE public.marcas ADD COLUMN IF NOT EXISTS marca text;
ALTER TABLE public.marcas ADD COLUMN IF NOT EXISTS logo_path text;
ALTER TABLE public.marcas ADD COLUMN IF NOT EXISTS is_active boolean NOT NULL DEFAULT true;
UPDATE public.marcas SET marca = COALESCE(marca, nombre) WHERE marca IS NULL;

-- tipos_cliente desde tipocliente
CREATE TABLE IF NOT EXISTS public.tipos_cliente (
  id_tipo_cliente integer PRIMARY KEY,
  tipo text NOT NULL,
  is_active boolean NOT NULL DEFAULT true
);
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema='public' AND table_name='tipocliente' AND column_name='tipo'
  ) THEN
    INSERT INTO public.tipos_cliente (id_tipo_cliente, tipo, is_active)
    SELECT t.id_tipo_cliente, t.tipo, COALESCE(t.is_active, true)
    FROM public.tipocliente t
    ON CONFLICT (id_tipo_cliente) DO UPDATE SET tipo=EXCLUDED.tipo, is_active=EXCLUDED.is_active;
  ELSIF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema='public' AND table_name='tipocliente' AND column_name='nombre'
  ) THEN
    INSERT INTO public.tipos_cliente (id_tipo_cliente, tipo, is_active)
    SELECT t.id_tipo_cliente, t.nombre, COALESCE(t.is_active, true)
    FROM public.tipocliente t
    ON CONFLICT (id_tipo_cliente) DO UPDATE SET tipo=EXCLUDED.tipo, is_active=EXCLUDED.is_active;
  END IF;
END $$;

-- leads (tabla esperada por el backend)
CREATE TABLE IF NOT EXISTS public.leads (
  id_lead serial PRIMARY KEY,
  cliente text NOT NULL,
  email text,
  telefono text,
  direccion text,
  id_marca integer,
  id_estado integer,
  id_comuna integer,
  id_tipo_cliente integer,
  fecha_evento date,
  monto_cotizado numeric(12,2) NOT NULL DEFAULT 0,
  plataforma text,
  notas text,
  num_cotizacion integer,
  pendiente_agendar boolean NOT NULL DEFAULT false,
  pre_title text,
  pre_start text,
  pre_end text,
  pre_location text,
  pre_telefono text,
  pre_direccion text,
  pre_products_text text,
  pre_montaje_text text,
  pre_ops integer DEFAULT 0,
  pre_description text,
  calendar_html_link text,
  agenda_approved_at text,
  agenda_approved_by text,
  declinado_motivo text,
  declinado_at timestamptz,
  id_cotizacion_vigente integer,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- migrar desde tabla antigua lead si existen datos
INSERT INTO public.leads (
  id_lead, cliente, email, telefono, direccion, id_marca, id_estado, id_comuna, id_tipo_cliente,
  fecha_evento, monto_cotizado, plataforma, notas,
  pendiente_agendar, pre_title, pre_start, pre_end, pre_location, pre_telefono, pre_direccion,
  pre_products_text, pre_montaje_text, pre_ops, pre_description,
  calendar_html_link, agenda_approved_at, agenda_approved_by,
  created_at, updated_at
)
SELECT
  l.id_lead,
  COALESCE(l.nombre_cliente, ''),
  l.email,
  l.telefono,
  l.direccion,
  l.id_marca,
  l.id_estado,
  l.id_comuna,
  l.id_tipo_cliente,
  CASE WHEN l.fecha_evento ~ '^\\d{4}-\\d{2}-\\d{2}' THEN to_date(substring(l.fecha_evento,1,10),'YYYY-MM-DD') ELSE NULL END,
  COALESCE(l.monto_cotizado,0),
  l.plataforma,
  l.notas,
  (l.pendiente_agendar::int = 1),
  l.pre_title,
  l.pre_start,
  l.pre_end,
  l.pre_location,
  l.pre_telefono,
  l.pre_direccion,
  l.pre_products_text,
  l.pre_montaje_text,
  l.pre_ops,
  l.pre_description,
  l.calendar_html_link,
  l.agenda_approved_at,
  l.agenda_approved_by,
  l.create_at,
  COALESCE(l.update_at, l.create_at)
FROM public.lead l
ON CONFLICT (id_lead) DO NOTHING;

-- productos + cotizaciones (necesarios para cotizador)
CREATE TABLE IF NOT EXISTS public.productos (
  id_producto serial PRIMARY KEY,
  producto text NOT NULL,
  descripcion text,
  ingredientes text,
  marca text NOT NULL,
  costo numeric(12,2) NOT NULL DEFAULT 0,
  is_active boolean NOT NULL DEFAULT true,
  orden int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.cotizaciones (
  id_cotizacion serial PRIMARY KEY,
  id_lead int NOT NULL,
  numero int,
  fecha timestamptz,
  traslado numeric(12,2) NOT NULL DEFAULT 0,
  descuento_valor numeric(12,2) NOT NULL DEFAULT 0,
  descuento_tipo text,
  subtotal_productos numeric(12,2) NOT NULL DEFAULT 0,
  iva numeric(12,2) NOT NULL DEFAULT 0,
  total numeric(12,2) NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz,
  estado text,
  nombre_cliente text,
  marca text,
  fecha_evento date,
  tipo_cliente text,
  version int NOT NULL DEFAULT 0,
  pdf_path text
);

CREATE TABLE IF NOT EXISTS public.cotizacion_items (
  id_item serial PRIMARY KEY,
  id_cotizacion int NOT NULL,
  id_producto int,
  producto text NOT NULL,
  descripcion text,
  cantidad int NOT NULL DEFAULT 1,
  precio_unitario numeric(12,2) NOT NULL DEFAULT 0,
  total_linea numeric(12,2) NOT NULL DEFAULT 0,
  marca text
);

COMMIT;
