-- Local snapshots for GA4, Search Console, PageSpeed/CrUX and UTM campaigns.
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE IF NOT EXISTS public.wi_sync_runs (
  id bigserial PRIMARY KEY,
  integration_id bigint REFERENCES public.wi_integrations(id) ON DELETE RESTRICT,
  provider varchar(40) NOT NULL,
  status varchar(24) NOT NULL,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  duration_ms integer,
  rows_received integer NOT NULL DEFAULT 0,
  retry_count integer NOT NULL DEFAULT 0,
  error_type varchar(80),
  error_safe text,
  cursor jsonb NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT wi_sync_runs_status_check CHECK (status IN ('RUNNING','SUCCESS','WARNING','ERROR'))
);
CREATE INDEX IF NOT EXISTS idx_wi_sync_runs_provider_started
  ON public.wi_sync_runs(provider, started_at DESC);

CREATE TABLE IF NOT EXISTS public.wi_daily_metrics (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES public.wi_sites(id) ON DELETE RESTRICT,
  metric_date date NOT NULL,
  source varchar(80) NOT NULL DEFAULT '',
  medium varchar(80) NOT NULL DEFAULT '',
  campaign varchar(200) NOT NULL DEFAULT '',
  landing_page text NOT NULL DEFAULT '',
  device varchar(40) NOT NULL DEFAULT '',
  users numeric NOT NULL DEFAULT 0,
  new_users numeric NOT NULL DEFAULT 0,
  sessions numeric NOT NULL DEFAULT 0,
  engaged_sessions numeric NOT NULL DEFAULT 0,
  page_views numeric NOT NULL DEFAULT 0,
  conversions numeric NOT NULL DEFAULT 0,
  revenue numeric NOT NULL DEFAULT 0,
  ingested_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(site_id,metric_date,source,medium,campaign,landing_page,device)
);
CREATE INDEX IF NOT EXISTS idx_wi_daily_metrics_site_date
  ON public.wi_daily_metrics(site_id, metric_date DESC);

CREATE TABLE IF NOT EXISTS public.wi_seo_query_daily (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES public.wi_sites(id) ON DELETE RESTRICT,
  metric_date date NOT NULL,
  query text NOT NULL,
  page text NOT NULL DEFAULT '',
  country varchar(8) NOT NULL DEFAULT '',
  device varchar(24) NOT NULL DEFAULT '',
  search_appearance varchar(80) NOT NULL DEFAULT '',
  clicks integer NOT NULL DEFAULT 0,
  impressions integer NOT NULL DEFAULT 0,
  ctr numeric NOT NULL DEFAULT 0,
  average_position numeric NOT NULL DEFAULT 0,
  ingested_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(site_id,metric_date,query,page,country,device,search_appearance)
);
CREATE INDEX IF NOT EXISTS idx_wi_seo_query_daily_site_date
  ON public.wi_seo_query_daily(site_id, metric_date DESC);
CREATE INDEX IF NOT EXISTS idx_wi_seo_query_daily_query
  ON public.wi_seo_query_daily(site_id, query, metric_date DESC);

CREATE TABLE IF NOT EXISTS public.wi_performance_runs (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES public.wi_sites(id) ON DELETE RESTRICT,
  url text NOT NULL,
  strategy varchar(12) NOT NULL,
  source varchar(20) NOT NULL,
  measured_at timestamptz NOT NULL DEFAULT now(),
  performance numeric,
  accessibility numeric,
  best_practices numeric,
  seo numeric,
  lcp_ms numeric,
  inp_ms numeric,
  cls numeric,
  fcp_ms numeric,
  tbt_ms numeric,
  ttfb_ms numeric,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT wi_performance_strategy_check CHECK(strategy IN ('MOBILE','DESKTOP')),
  CONSTRAINT wi_performance_source_check CHECK(source IN ('LAB','FIELD'))
);
CREATE INDEX IF NOT EXISTS idx_wi_performance_site_measured
  ON public.wi_performance_runs(site_id, measured_at DESC);

CREATE SEQUENCE IF NOT EXISTS public.wi_campaign_identifier_seq;
CREATE TABLE IF NOT EXISTS public.wi_utm_links (
  id bigserial PRIMARY KEY,
  identifier varchar(40) NOT NULL UNIQUE,
  site_id bigint NOT NULL REFERENCES public.wi_sites(id) ON DELETE RESTRICT,
  base_url text NOT NULL,
  generated_url text NOT NULL,
  utm_source varchar(160) NOT NULL,
  utm_medium varchar(160) NOT NULL,
  utm_campaign varchar(200) NOT NULL,
  utm_term varchar(200),
  utm_content varchar(200),
  created_by varchar(200) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_wi_utm_links_site_created
  ON public.wi_utm_links(site_id, created_at DESC);

COMMIT;
