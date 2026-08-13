BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '30s';

CREATE TABLE IF NOT EXISTS public.wi_tracking_edge_pull_runs (
  id bigserial PRIMARY KEY,
  status varchar(20) NOT NULL CHECK(status IN ('PASS','ERROR')),
  pulled integer NOT NULL DEFAULT 0,
  sessions_ingested integer NOT NULL DEFAULT 0,
  events_ingested integer NOT NULL DEFAULT 0,
  duplicates integer NOT NULL DEFAULT 0,
  rejected integer NOT NULL DEFAULT 0,
  queue_depth integer NOT NULL DEFAULT 0,
  oldest_unacked_age integer NOT NULL DEFAULT 0,
  last_event_at timestamptz,
  error_code varchar(80),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_wi_tracking_edge_runs_created
  ON public.wi_tracking_edge_pull_runs(created_at DESC);

CREATE TABLE IF NOT EXISTS public.wi_tracking_edge_rejections (
  edge_id bigint PRIMARY KEY,
  event_id varchar(80),
  site_code varchar(16) NOT NULL,
  reason_code varchar(80) NOT NULL,
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_wi_tracking_edge_rejections_site
  ON public.wi_tracking_edge_rejections(site_code,last_seen_at DESC);

COMMIT;
