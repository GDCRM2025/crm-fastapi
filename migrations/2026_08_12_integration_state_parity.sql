BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='60s';

ALTER TABLE public.wi_integrations DROP CONSTRAINT IF EXISTS wi_integrations_status_check;
UPDATE public.wi_integrations SET status='REQUIRES_ATTENTION',updated_at=now() WHERE status='WARNING';
ALTER TABLE public.wi_integrations ADD CONSTRAINT wi_integrations_status_check CHECK (
  status IN ('DETECTED','CONNECTED','READY_FOR_CREDENTIAL','NO_DATA','REQUIRES_ATTENTION','ERROR','DISABLED','NOT_CONFIGURED')
);

COMMIT;
