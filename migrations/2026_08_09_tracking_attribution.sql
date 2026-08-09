-- First-party tracking, campaigns and lead attribution. Additive/idempotent only.
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE IF NOT EXISTS public.wi_marketing_campaigns (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES public.wi_sites(id) ON DELETE RESTRICT,
  name varchar(200) NOT NULL,
  status varchar(24) NOT NULL DEFAULT 'ACTIVE',
  created_by varchar(200) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(site_id,name),
  CONSTRAINT wi_marketing_campaigns_status CHECK(status IN ('DRAFT','ACTIVE','PAUSED','ARCHIVED'))
);

ALTER TABLE public.wi_utm_links ADD COLUMN IF NOT EXISTS campaign_id bigint;
DO $$ BEGIN
  ALTER TABLE public.wi_utm_links ADD CONSTRAINT wi_utm_links_campaign_fk
    FOREIGN KEY (campaign_id) REFERENCES public.wi_marketing_campaigns(id) ON DELETE RESTRICT;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
CREATE INDEX IF NOT EXISTS idx_wi_utm_links_campaign ON public.wi_utm_links(campaign_id);

CREATE TABLE IF NOT EXISTS public.wi_sessions (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES public.wi_sites(id) ON DELETE RESTRICT,
  visitor_id uuid NOT NULL,
  session_id uuid NOT NULL UNIQUE,
  started_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  referrer text,
  landing_url text NOT NULL,
  utm_source varchar(160),
  utm_medium varchar(160),
  utm_campaign varchar(200),
  utm_term varchar(200),
  utm_content varchar(200),
  device varchar(40),
  user_agent_family varchar(100),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_wi_sessions_visitor_started ON public.wi_sessions(visitor_id,started_at DESC);
CREATE INDEX IF NOT EXISTS idx_wi_sessions_campaign ON public.wi_sessions(site_id,utm_campaign);

CREATE TABLE IF NOT EXISTS public.wi_events (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES public.wi_sites(id) ON DELETE RESTRICT,
  session_id uuid NOT NULL REFERENCES public.wi_sessions(session_id) ON DELETE RESTRICT,
  event_id uuid NOT NULL UNIQUE,
  event_type varchar(60) NOT NULL,
  occurred_at timestamptz NOT NULL DEFAULT now(),
  page_url text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT wi_events_type_format CHECK(event_type ~ '^[a-z][a-z0-9_]{1,59}$')
);
CREATE INDEX IF NOT EXISTS idx_wi_events_session_time ON public.wi_events(session_id,occurred_at DESC);

CREATE TABLE IF NOT EXISTS public.wi_lead_attribution (
  id bigserial PRIMARY KEY,
  lead_id bigint NOT NULL REFERENCES public.leads(id_lead) ON DELETE RESTRICT,
  session_id uuid REFERENCES public.wi_sessions(session_id) ON DELETE RESTRICT,
  campaign_id bigint REFERENCES public.wi_marketing_campaigns(id) ON DELETE RESTRICT,
  first_touch jsonb NOT NULL DEFAULT '{}'::jsonb,
  last_touch jsonb NOT NULL DEFAULT '{}'::jsonb,
  source varchar(160),
  medium varchar(160),
  campaign varchar(200),
  landing_url text,
  device varchar(40),
  attribution_method varchar(60) NOT NULL,
  confidence numeric(5,4) NOT NULL DEFAULT 1.0,
  is_manual boolean NOT NULL DEFAULT false,
  updated_by varchar(200),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(lead_id),
  CONSTRAINT wi_lead_attribution_confidence CHECK(confidence >= 0 AND confidence <= 1)
);
CREATE INDEX IF NOT EXISTS idx_wi_lead_attribution_session ON public.wi_lead_attribution(session_id);

COMMIT;
