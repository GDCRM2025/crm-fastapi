-- GD Intelligence core: additive and idempotent PostgreSQL migration.
-- Execute only after backup/restore verification and the pre-migration gates in AUDIT.md.
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE IF NOT EXISTS public.gd_role_permissions (
  role_key varchar(100) NOT NULL,
  permission_key varchar(100) NOT NULL,
  allowed boolean NOT NULL DEFAULT false,
  updated_by varchar(200),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (role_key, permission_key)
);

CREATE TABLE IF NOT EXISTS public.gd_user_permissions (
  user_id integer NOT NULL,
  permission_key varchar(100) NOT NULL,
  allowed boolean NOT NULL DEFAULT false,
  updated_by varchar(200),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, permission_key)
);

CREATE TABLE IF NOT EXISTS public.wi_sites (
  id bigserial PRIMARY KEY,
  code varchar(16) NOT NULL UNIQUE,
  name varchar(160) NOT NULL,
  domain varchar(253) NOT NULL UNIQUE,
  timezone varchar(64) NOT NULL DEFAULT 'America/Santiago',
  currency char(3) NOT NULL DEFAULT 'CLP',
  enabled boolean NOT NULL DEFAULT true,
  config jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_by varchar(200),
  updated_by varchar(200),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT wi_sites_code_format CHECK (code ~ '^[A-Z][A-Z0-9_-]{1,15}$'),
  CONSTRAINT wi_sites_domain_not_blank CHECK (btrim(domain) <> '')
);

CREATE TABLE IF NOT EXISTS public.wi_integrations (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES public.wi_sites(id) ON DELETE RESTRICT,
  provider varchar(40) NOT NULL,
  status varchar(24) NOT NULL DEFAULT 'NOT_CONFIGURED',
  enabled boolean NOT NULL DEFAULT false,
  external_id varchar(255),
  config jsonb NOT NULL DEFAULT '{}'::jsonb,
  last_sync_at timestamptz,
  last_success_at timestamptz,
  last_duration_ms integer,
  last_rows integer,
  last_error_code varchar(80),
  last_error_safe text,
  retry_count integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (site_id, provider),
  CONSTRAINT wi_integrations_status_check CHECK (
    status IN ('CONNECTED','NOT_CONFIGURED','WARNING','ERROR','DISABLED')
  ),
  CONSTRAINT wi_integrations_retry_nonnegative CHECK (retry_count >= 0)
);

CREATE INDEX IF NOT EXISTS idx_wi_integrations_status
  ON public.wi_integrations(status, provider);

INSERT INTO public.wi_sites(code,name,domain)
VALUES
  ('CAM','Carritos Camaleón','www.carritoscamaleon.cl'),
  ('EXP','Carritos Express','www.carritosexpress.cl'),
  ('GOU','Carritos Gourmet','www.carritosgourmet.cl'),
  ('DEL','Carritos del Sabor','www.carritosdelsabor.cl')
ON CONFLICT DO NOTHING;

INSERT INTO public.wi_integrations(site_id,provider,status,enabled)
SELECT s.id, p.provider, 'NOT_CONFIGURED', false
FROM public.wi_sites s
CROSS JOIN unnest(ARRAY[
  'GA4','SEARCH_CONSOLE','PAGESPEED','CRUX','CLARITY','TRACKING'
]) AS p(provider)
ON CONFLICT(site_id,provider) DO NOTHING;

COMMIT;
