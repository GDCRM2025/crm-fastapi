BEGIN;

CREATE TABLE IF NOT EXISTS public.lead_agenda_confirmations (
    id BIGSERIAL PRIMARY KEY,
    id_lead BIGINT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    request_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    response_payload JSONB,
    status TEXT NOT NULL DEFAULT 'processing',
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_lead_agenda_confirmations_lead
    ON public.lead_agenda_confirmations (id_lead, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_lead_agenda_confirmations_status
    ON public.lead_agenda_confirmations (status, updated_at DESC);

COMMIT;
