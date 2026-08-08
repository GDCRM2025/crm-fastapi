-- Public integration inventory: additive/idempotent. Secrets remain outside PostgreSQL/Git.
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '60s';

ALTER TABLE public.wi_integrations
  ADD COLUMN IF NOT EXISTS last_verified_at timestamptz;

INSERT INTO public.wi_integrations(site_id,provider,status,enabled)
SELECT s.id,p.provider,'NOT_CONFIGURED',false
FROM public.wi_sites s
CROSS JOIN unnest(ARRAY['GTM','META']) AS p(provider)
ON CONFLICT(site_id,provider) DO NOTHING;

COMMIT;
