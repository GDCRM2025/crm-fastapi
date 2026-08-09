-- Real-source selection, sync audit, SEO, alerts and safe AI context. Additive/idempotent.
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='60s';

ALTER TABLE public.wi_ad_accounts ADD COLUMN IF NOT EXISTS manager_external_id varchar(120);
ALTER TABLE public.wi_ad_accounts ADD COLUMN IF NOT EXISTS business_external_id varchar(120);
ALTER TABLE public.wi_ad_accounts ADD COLUMN IF NOT EXISTS page_external_id varchar(120);
ALTER TABLE public.wi_ad_accounts ADD COLUMN IF NOT EXISTS instagram_external_id varchar(120);
ALTER TABLE public.wi_ad_accounts ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE public.wi_ad_accounts ADD COLUMN IF NOT EXISTS last_error_code varchar(80);
ALTER TABLE public.wi_ad_accounts ADD COLUMN IF NOT EXISTS last_error_safe text;

CREATE TABLE IF NOT EXISTS public.wi_ad_sync_runs (
  id bigserial PRIMARY KEY,
  account_id bigint NOT NULL REFERENCES public.wi_ad_accounts(id) ON DELETE RESTRICT,
  source varchar(20) NOT NULL,
  mode varchar(20) NOT NULL DEFAULT 'READ_ONLY',
  period_start date NOT NULL,
  period_end date NOT NULL,
  status varchar(24) NOT NULL DEFAULT 'RUNNING',
  entities_count integer NOT NULL DEFAULT 0,
  metrics_count integer NOT NULL DEFAULT 0,
  search_terms_count integer NOT NULL DEFAULT 0,
  error_code varchar(80),
  started_by varchar(200) NOT NULL,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  CONSTRAINT wi_ad_sync_source CHECK(source IN ('GOOGLE_ADS','META_ADS')),
  CONSTRAINT wi_ad_sync_mode CHECK(mode='READ_ONLY'),
  CONSTRAINT wi_ad_sync_status CHECK(status IN ('RUNNING','COMPLETED','FAILED','PARTIAL'))
);
CREATE INDEX IF NOT EXISTS idx_wi_ad_sync_runs_account_started ON public.wi_ad_sync_runs(account_id,started_at DESC);

CREATE TABLE IF NOT EXISTS public.wi_ad_click_refs (
  id bigserial PRIMARY KEY,
  account_id bigint NOT NULL REFERENCES public.wi_ad_accounts(id) ON DELETE RESTRICT,
  platform varchar(20) NOT NULL,
  click_id_hash char(64) NOT NULL,
  clicked_at timestamptz,
  campaign_external_id varchar(160),
  ad_group_external_id varchar(160),
  ad_external_id varchar(160),
  ingested_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(platform,click_id_hash),
  CONSTRAINT wi_ad_click_platform CHECK(platform IN ('GOOGLE_ADS','META_ADS'))
);
CREATE INDEX IF NOT EXISTS idx_wi_ad_click_refs_account_time ON public.wi_ad_click_refs(account_id,clicked_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_wi_paid_media_attribution_one_per_lead ON public.wi_paid_media_attribution(lead_id);

CREATE TABLE IF NOT EXISTS public.wi_seo_opportunities (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES public.wi_sites(id) ON DELETE RESTRICT,
  opportunity_key varchar(240) NOT NULL,
  query text,
  page_url text,
  score smallint NOT NULL,
  state varchar(32) NOT NULL,
  reason text NOT NULL,
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  next_action text,
  observed_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(site_id,opportunity_key),
  CONSTRAINT wi_seo_score CHECK(score BETWEEN 0 AND 100),
  CONSTRAINT wi_seo_state CHECK(state IN ('READY','PARTIAL','INSUFFICIENT_DATA','TECHNICAL_ONLY'))
);
CREATE INDEX IF NOT EXISTS idx_wi_seo_opportunities_site_score ON public.wi_seo_opportunities(site_id,score DESC);

CREATE TABLE IF NOT EXISTS public.wi_actionable_alerts (
  id bigserial PRIMARY KEY,
  alert_key varchar(240) NOT NULL UNIQUE,
  category varchar(30) NOT NULL,
  severity varchar(16) NOT NULL,
  status varchar(20) NOT NULL DEFAULT 'OPEN',
  title text NOT NULL,
  reason text NOT NULL,
  action_label varchar(120) NOT NULL,
  action_target text,
  entity_type varchar(40),
  entity_id varchar(160),
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  next_review_at timestamptz,
  resolved_at timestamptz,
  CONSTRAINT wi_alert_category CHECK(category IN ('ADS','SEO','SITE_HEALTH','INTEGRATION','CONVERSATION')),
  CONSTRAINT wi_alert_severity CHECK(severity IN ('INFO','IMPORTANT','CRITICAL')),
  CONSTRAINT wi_alert_status CHECK(status IN ('OPEN','ACKNOWLEDGED','SNOOZED','RESOLVED'))
);
CREATE INDEX IF NOT EXISTS idx_wi_actionable_alerts_status_review ON public.wi_actionable_alerts(status,next_review_at);

INSERT INTO public.help_articles(slug,category_id,screen_id,title,summary,content,level,version,module_version)
SELECT v.slug,c.id,v.screen_id,v.title,v.summary,v.content,v.level,'1.0','2026.08.10'
FROM (VALUES
 ('search-terms-intelligence','paid-media','gdi_search_terms','Inteligencia de términos de búsqueda','Clasificar demanda sin publicar negativas automáticamente.','HIGH_VALUE combina intención y resultados CRM. WASTE indica gasto sin evidencia de valor. NEGATIVE_CANDIDATE requiere revisión humana. INSUFFICIENT_DATA evita conclusiones prematuras. El CRM nunca publica negativas automáticamente.','INTERMEDIATE'),
 ('creative-intelligence','paid-media','gdi_creatives','Inteligencia creativa','Detectar señales de posible fatiga, no certezas.','La señal cruza frecuencia, tendencia CTR, tendencia CPA CRM, conversión y edad creativa. Una alerta de fatiga es una hipótesis para revisar; no modifica anuncios.','ADVANCED'),
 ('seo-opportunity','business-intelligence','gdi_seo','Oportunidades SEO','Priorizar mejoras con evidencia disponible.','El score 0–100 combina Search Console cuando está conectado, tráfico, conversión, ingresos y salud técnica. PARTIAL o TECHNICAL_ONLY significa que faltan fuentes y nunca se completan con datos simulados.','INTERMEDIATE'),
 ('actionable-alerts','business-intelligence','gdi_alerts','Alertas accionables','Entender por qué aparece una alerta y qué hacer.','Cada alerta muestra evidencia, severidad, acción y fecha de próxima revisión. La deduplicación y cooldown evitan repetir ruido mientras no cambia la evidencia.','BASIC'),
 ('executive-readiness','business-intelligence','gdi_executive','Dashboard Ejecutivo y suficiencia de fuentes','Comprender por qué el tablero puede permanecer bloqueado.','El dashboard se habilita sólo con ventas CRM y suficientes fuentes reales vigentes. INSUFFICIENT_REAL_SOURCES indica qué está disponible y qué falta; nunca se completan KPIs con datos simulados.','INTERMEDIATE'),
 ('gd-ai-safe-context','business-intelligence','gdi_ai','GD AI seguro','Analizar únicamente información autorizada por rol.','GD AI usa servicios de contexto con campos permitidos y permisos del usuario. No ejecuta SQL arbitrario ni modifica campañas. Su modo inicial es leer, analizar y sugerir.','ADVANCED')
) AS v(slug,category_slug,screen_id,title,summary,content,level)
JOIN public.help_categories c ON c.slug=v.category_slug
ON CONFLICT(slug) DO UPDATE SET title=excluded.title,summary=excluded.summary,content=excluded.content,
 level=excluded.level,module_version=excluded.module_version,updated_at=now();

INSERT INTO public.help_context_links(screen_id,article_id)
SELECT DISTINCT ON (screen_id) screen_id,id FROM public.help_articles WHERE screen_id IS NOT NULL
ORDER BY screen_id,CASE level WHEN 'BASIC' THEN 1 WHEN 'INTERMEDIATE' THEN 2 ELSE 3 END,id
ON CONFLICT(screen_id) DO UPDATE SET article_id=excluded.article_id,updated_at=now();

COMMIT;
