-- Site Health history. Additive and idempotent.
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE IF NOT EXISTS public.wi_site_health_runs (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES public.wi_sites(id) ON DELETE RESTRICT,
  url text NOT NULL,
  status varchar(12) NOT NULL,
  http_status integer,
  response_ms integer,
  https_ok boolean NOT NULL DEFAULT false,
  robots_ok boolean NOT NULL DEFAULT false,
  sitemap_ok boolean NOT NULL DEFAULT false,
  canonical_ok boolean NOT NULL DEFAULT false,
  title_ok boolean NOT NULL DEFAULT false,
  meta_description_ok boolean NOT NULL DEFAULT false,
  h1_ok boolean NOT NULL DEFAULT false,
  viewport_ok boolean NOT NULL DEFAULT false,
  schema_org_ok boolean NOT NULL DEFAULT false,
  not_found_ok boolean NOT NULL DEFAULT false,
  redirect_count integer NOT NULL DEFAULT 0,
  broken_internal_links integer NOT NULL DEFAULT 0,
  mixed_content_count integer NOT NULL DEFAULT 0,
  missing_alt_count integer NOT NULL DEFAULT 0,
  started_at timestamptz NOT NULL,
  finished_at timestamptz NOT NULL,
  duration_ms integer NOT NULL,
  error_safe text,
  result jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_by varchar(200),
  CONSTRAINT wi_site_health_status_check CHECK(status IN ('ONLINE','WARNING','DOWN')),
  CONSTRAINT wi_site_health_nonnegative CHECK(
    redirect_count >= 0 AND broken_internal_links >= 0
    AND mixed_content_count >= 0 AND missing_alt_count >= 0
    AND duration_ms >= 0
  )
);

CREATE INDEX IF NOT EXISTS idx_wi_site_health_site_started
  ON public.wi_site_health_runs(site_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_wi_site_health_status_started
  ON public.wi_site_health_runs(status, started_at DESC);

COMMIT;
