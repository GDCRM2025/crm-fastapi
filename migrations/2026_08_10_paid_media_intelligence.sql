-- Paid Media evidence, classification and risk snapshots. Additive/idempotent, read-only toward Ads platforms.
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='60s';

ALTER TABLE public.wi_sessions ADD COLUMN IF NOT EXISTS gclid_hash char(64);
ALTER TABLE public.wi_sessions ADD COLUMN IF NOT EXISTS fbclid_hash char(64);
CREATE INDEX IF NOT EXISTS idx_wi_sessions_gclid_hash ON public.wi_sessions(gclid_hash) WHERE gclid_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_wi_sessions_fbclid_hash ON public.wi_sessions(fbclid_hash) WHERE fbclid_hash IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.wi_paid_media_attribution (
  id bigserial PRIMARY KEY,
  lead_id bigint NOT NULL REFERENCES public.leads(id_lead) ON DELETE RESTRICT,
  lead_attribution_id bigint REFERENCES public.wi_lead_attribution(id) ON DELETE RESTRICT,
  session_id uuid REFERENCES public.wi_sessions(session_id) ON DELETE RESTRICT,
  account_id bigint REFERENCES public.wi_ad_accounts(id) ON DELETE RESTRICT,
  platform varchar(20), campaign_external_id varchar(160), ad_group_external_id varchar(160),
  ad_external_id varchar(160), creative_external_id varchar(160), click_id_hash char(64),
  attribution_method varchar(20) NOT NULL DEFAULT 'UNKNOWN', confidence numeric(5,4) NOT NULL DEFAULT 0,
  reason text NOT NULL, evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
  quote_count integer NOT NULL DEFAULT 0, sale_count integer NOT NULL DEFAULT 0,
  revenue numeric NOT NULL DEFAULT 0, calculated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(lead_id,platform,account_id),
  CONSTRAINT wi_pma_method CHECK(attribution_method IN ('EXACT','STRONG','INFERRED','UNKNOWN')),
  CONSTRAINT wi_pma_platform CHECK(platform IS NULL OR platform IN ('GOOGLE_ADS','META_ADS')),
  CONSTRAINT wi_pma_confidence CHECK(confidence >= 0 AND confidence <= 1),
  CONSTRAINT wi_pma_nonnegative CHECK(quote_count >= 0 AND sale_count >= 0 AND revenue >= 0)
);
CREATE INDEX IF NOT EXISTS idx_wi_pma_campaign ON public.wi_paid_media_attribution(platform,campaign_external_id);
CREATE INDEX IF NOT EXISTS idx_wi_pma_session ON public.wi_paid_media_attribution(session_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_wi_pma_evidence_scope
  ON public.wi_paid_media_attribution(lead_id,COALESCE(platform,''),COALESCE(account_id,0));

ALTER TABLE public.wi_search_terms ADD COLUMN IF NOT EXISTS classification_reason text;
ALTER TABLE public.wi_search_terms ADD COLUMN IF NOT EXISTS classification_confidence numeric(5,4) NOT NULL DEFAULT 0;
ALTER TABLE public.wi_search_terms ADD COLUMN IF NOT EXISTS crm_leads integer NOT NULL DEFAULT 0;
ALTER TABLE public.wi_search_terms ADD COLUMN IF NOT EXISTS crm_sales integer NOT NULL DEFAULT 0;
ALTER TABLE public.wi_search_terms ADD COLUMN IF NOT EXISTS crm_revenue numeric NOT NULL DEFAULT 0;
ALTER TABLE public.wi_search_terms ADD COLUMN IF NOT EXISTS evaluated_at timestamptz;
DO $$ BEGIN
  ALTER TABLE public.wi_search_terms ADD CONSTRAINT wi_search_term_state
    CHECK(opportunity_status IN ('HIGH_VALUE','WASTE','NEGATIVE_CANDIDATE','INSUFFICIENT_DATA'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS public.wi_creative_intelligence (
  id bigserial PRIMARY KEY, account_id bigint NOT NULL REFERENCES public.wi_ad_accounts(id) ON DELETE RESTRICT,
  creative_external_id varchar(160) NOT NULL, period_start date NOT NULL, period_end date NOT NULL,
  state varchar(30) NOT NULL, confidence numeric(5,4) NOT NULL, reason text NOT NULL,
  signals jsonb NOT NULL DEFAULT '[]'::jsonb, metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
  evaluated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(account_id,creative_external_id,period_start,period_end),
  CONSTRAINT wi_creative_state CHECK(state IN ('FATIGUE_LIKELY','FATIGUE_POSSIBLE','HEALTHY','INSUFFICIENT_DATA')),
  CONSTRAINT wi_creative_confidence CHECK(confidence >= 0 AND confidence <= 1)
);

CREATE TABLE IF NOT EXISTS public.wi_ads_change_risk (
  id bigserial PRIMARY KEY, account_id bigint REFERENCES public.wi_ad_accounts(id) ON DELETE RESTRICT,
  entity_type varchar(30) NOT NULL, entity_external_id varchar(160) NOT NULL,
  state varchar(30) NOT NULL, risk varchar(16) NOT NULL, confidence numeric(5,4) NOT NULL,
  reason text NOT NULL, recommendation varchar(40) NOT NULL, next_review_date date NOT NULL,
  metrics_to_watch jsonb NOT NULL DEFAULT '[]'::jsonb, metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
  evaluated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT wi_change_state CHECK(state IN ('STABLE','LEARNING','RECENT_CHANGE','COOLDOWN','LOW_DATA','LIMITED_BY_BUDGET','INSUFFICIENT_EVIDENCE')),
  CONSTRAINT wi_change_risk CHECK(risk IN ('LOW','MEDIUM','HIGH','CRITICAL','UNKNOWN')),
  CONSTRAINT wi_change_confidence CHECK(confidence >= 0 AND confidence <= 1)
);
CREATE INDEX IF NOT EXISTS idx_wi_change_risk_entity ON public.wi_ads_change_risk(account_id,entity_type,entity_external_id,evaluated_at DESC);

INSERT INTO public.help_articles(slug,category_id,screen_id,title,summary,content,level,version,module_version)
SELECT v.slug,c.id,v.screen_id,v.title,v.summary,v.content,v.level,'1.0','2026.08.10'
FROM (VALUES
 ('search-terms-intelligence','paid-media','gdi_search_terms','Términos de búsqueda y oportunidades','Cómo interpretar HIGH_VALUE, WASTE, NEGATIVE_CANDIDATE e INSUFFICIENT_DATA.','HIGH_VALUE cuenta con una señal de conversión o resultado CRM. WASTE consume gasto sin el resultado esperado según umbrales configurados. NEGATIVE_CANDIDATE requiere revisión humana y nunca se publica automáticamente. INSUFFICIENT_DATA evita concluir con poco volumen.','INTERMEDIATE'),
 ('creative-fatigue-intelligence','paid-media','gdi_creative_intelligence','Señales de fatiga creativa','Combinar frecuencia, CTR, CPA CRM, conversión y edad sin afirmar causalidad.','FATIGUE_POSSIBLE y FATIGUE_LIKELY son señales analíticas, no hechos. El motor exige volumen y compara periodos. Revise frecuencia, tendencia de CTR, CPA CRM, conversión y edad antes de decidir.','ADVANCED'),
 ('ads-change-risk','paid-media','gdi_change_risk','Riesgo y periodo de observación','Comprender aprendizaje, cambios recientes, cooldown, bajo volumen y limitación presupuestaria.','Toda recomendación informa motivo, confianza, riesgo, próxima revisión y métricas a observar. OBSERVE no modifica campañas: indica esperar evidencia. LIMITED_BY_BUDGET tampoco recomienda subir presupuesto sin comprobar CPA CRM y ROAS CRM.','BASIC')
) AS v(slug,category_slug,screen_id,title,summary,content,level)
JOIN public.help_categories c ON c.slug=v.category_slug
ON CONFLICT(slug) DO UPDATE SET title=excluded.title,summary=excluded.summary,content=excluded.content,
 level=excluded.level,version=excluded.version,module_version=excluded.module_version,updated_at=now();

INSERT INTO public.help_context_links(screen_id,article_id)
SELECT screen_id,id FROM public.help_articles
WHERE slug IN ('search-terms-intelligence','creative-fatigue-intelligence','ads-change-risk')
ON CONFLICT(screen_id) DO UPDATE SET article_id=excluded.article_id,updated_at=now();

COMMIT;
