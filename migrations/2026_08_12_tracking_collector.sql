BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '30s';

CREATE TABLE IF NOT EXISTS public.wi_tracking_collector_metrics (
  metric_date date NOT NULL DEFAULT CURRENT_DATE,
  site_code varchar(16) NOT NULL,
  events_received bigint NOT NULL DEFAULT 0,
  events_accepted bigint NOT NULL DEFAULT 0,
  events_rejected bigint NOT NULL DEFAULT 0,
  sessions_created bigint NOT NULL DEFAULT 0,
  duplicate_events bigint NOT NULL DEFAULT 0,
  last_event_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(metric_date, site_code)
);

COMMIT;
