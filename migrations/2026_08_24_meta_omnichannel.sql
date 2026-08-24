BEGIN;

CREATE TABLE IF NOT EXISTS public.wi_meta_events (
    id BIGSERIAL PRIMARY KEY,
    event_key TEXT NOT NULL UNIQUE,
    channel TEXT NOT NULL CHECK (channel IN ('INSTAGRAM','MESSENGER')),
    object_name TEXT,
    brand_code TEXT,
    external_account_id TEXT,
    payload JSONB NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_wi_meta_events_channel_received
    ON public.wi_meta_events(channel, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_wi_meta_events_brand_received
    ON public.wi_meta_events(brand_code, received_at DESC);

CREATE TABLE IF NOT EXISTS public.wi_meta_conversations (
    id BIGSERIAL PRIMARY KEY,
    channel TEXT NOT NULL CHECK (channel IN ('INSTAGRAM','MESSENGER')),
    external_account_id TEXT NOT NULL,
    peer_id TEXT NOT NULL,
    brand_code TEXT,
    contact_name TEXT,
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','PENDING','RESOLVED')),
    unread_count INTEGER NOT NULL DEFAULT 0 CHECK (unread_count >= 0),
    last_message_at TIMESTAMPTZ,
    last_message_preview TEXT,
    last_direction TEXT CHECK (last_direction IS NULL OR last_direction IN ('IN','OUT')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(channel, external_account_id, peer_id)
);

CREATE INDEX IF NOT EXISTS idx_wi_meta_conversations_channel_last
    ON public.wi_meta_conversations(channel, last_message_at DESC);
CREATE INDEX IF NOT EXISTS idx_wi_meta_conversations_brand_last
    ON public.wi_meta_conversations(brand_code, last_message_at DESC);

CREATE TABLE IF NOT EXISTS public.wi_meta_messages (
    id BIGSERIAL PRIMARY KEY,
    conversation_id BIGINT NOT NULL REFERENCES public.wi_meta_conversations(id) ON DELETE CASCADE,
    external_message_id TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('IN','OUT')),
    kind TEXT NOT NULL DEFAULT 'message',
    body TEXT,
    status TEXT,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw_payload JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(conversation_id, external_message_id)
);

CREATE INDEX IF NOT EXISTS idx_wi_meta_messages_conversation_sent
    ON public.wi_meta_messages(conversation_id, sent_at, id);

CREATE TABLE IF NOT EXISTS public.wi_omnichannel_work_items (
    id BIGSERIAL PRIMARY KEY,
    channel TEXT NOT NULL CHECK (channel IN ('WHATSAPP','INSTAGRAM','MESSENGER','EMAIL')),
    source_ref TEXT NOT NULL,
    lead_id BIGINT,
    quote_id BIGINT,
    assigned_user_id TEXT,
    assigned_user_name TEXT,
    follow_up_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','PENDING','RESOLVED')),
    created_by TEXT,
    updated_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(channel, source_ref)
);

CREATE INDEX IF NOT EXISTS idx_wi_omnichannel_work_items_lead
    ON public.wi_omnichannel_work_items(lead_id);
CREATE INDEX IF NOT EXISTS idx_wi_omnichannel_work_items_follow_up
    ON public.wi_omnichannel_work_items(follow_up_at)
    WHERE follow_up_at IS NOT NULL;

-- Compatibilidad no destructiva con el almacenamiento GIA anterior.
ALTER TABLE IF EXISTS public.gia_ig_events ADD COLUMN IF NOT EXISTS event_key TEXT;
ALTER TABLE IF EXISTS public.gia_ig_events ADD COLUMN IF NOT EXISTS external_account_id TEXT;
ALTER TABLE IF EXISTS public.gia_ig_events ADD COLUMN IF NOT EXISTS brand_code TEXT;

COMMIT;
