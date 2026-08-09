-- Paid Media read-only intelligence and CRM Help Engine. Additive/idempotent.
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='60s';

CREATE TABLE IF NOT EXISTS public.wi_ad_accounts (
  id bigserial PRIMARY KEY,
  platform varchar(20) NOT NULL,
  external_id varchar(120) NOT NULL,
  name varchar(240) NOT NULL,
  currency char(3), timezone varchar(64), status varchar(24) NOT NULL DEFAULT 'NOT_CONFIGURED',
  enabled boolean NOT NULL DEFAULT false, last_verified_at timestamptz, last_sync_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(platform,external_id),
  CONSTRAINT wi_ad_accounts_platform CHECK(platform IN ('GOOGLE_ADS','META_ADS')),
  CONSTRAINT wi_ad_accounts_status CHECK(status IN ('NOT_CONFIGURED','CONNECTED','WARNING','ERROR','DISABLED'))
);
CREATE TABLE IF NOT EXISTS public.wi_ad_account_sites (
  account_id bigint NOT NULL REFERENCES public.wi_ad_accounts(id) ON DELETE RESTRICT,
  site_id bigint NOT NULL REFERENCES public.wi_sites(id) ON DELETE RESTRICT,
  PRIMARY KEY(account_id,site_id)
);
CREATE TABLE IF NOT EXISTS public.wi_ad_entities (
  id bigserial PRIMARY KEY, account_id bigint NOT NULL REFERENCES public.wi_ad_accounts(id) ON DELETE RESTRICT,
  platform varchar(20) NOT NULL, entity_type varchar(30) NOT NULL, external_id varchar(160) NOT NULL,
  parent_external_id varchar(160), name text NOT NULL, status varchar(40),
  campaign_external_id varchar(160), ad_group_external_id varchar(160),
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb, first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(), UNIQUE(account_id,entity_type,external_id)
);
CREATE INDEX IF NOT EXISTS idx_wi_ad_entities_account_type ON public.wi_ad_entities(account_id,entity_type);
CREATE TABLE IF NOT EXISTS public.wi_ad_metrics_daily (
  id bigserial PRIMARY KEY, account_id bigint NOT NULL REFERENCES public.wi_ad_accounts(id) ON DELETE RESTRICT,
  metric_date date NOT NULL, campaign_external_id varchar(160) NOT NULL DEFAULT '',
  ad_group_external_id varchar(160) NOT NULL DEFAULT '', ad_external_id varchar(160) NOT NULL DEFAULT '',
  creative_external_id varchar(160) NOT NULL DEFAULT '', keyword_external_id varchar(160) NOT NULL DEFAULT '',
  device varchar(40) NOT NULL DEFAULT '', network varchar(60) NOT NULL DEFAULT '',
  spend numeric NOT NULL DEFAULT 0, impressions bigint NOT NULL DEFAULT 0, reach bigint,
  frequency numeric, clicks bigint NOT NULL DEFAULT 0, platform_conversions numeric NOT NULL DEFAULT 0,
  platform_conversion_value numeric NOT NULL DEFAULT 0, video_views bigint,
  ingested_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(account_id,metric_date,campaign_external_id,ad_group_external_id,ad_external_id,creative_external_id,keyword_external_id,device,network)
);
CREATE INDEX IF NOT EXISTS idx_wi_ad_metrics_account_date ON public.wi_ad_metrics_daily(account_id,metric_date DESC);
CREATE TABLE IF NOT EXISTS public.wi_search_terms (
  id bigserial PRIMARY KEY, account_id bigint NOT NULL REFERENCES public.wi_ad_accounts(id) ON DELETE RESTRICT,
  metric_date date NOT NULL, campaign_external_id varchar(160) NOT NULL,
  ad_group_external_id varchar(160) NOT NULL, keyword_external_id varchar(160),
  search_term text NOT NULL, match_type varchar(40), spend numeric NOT NULL DEFAULT 0,
  impressions bigint NOT NULL DEFAULT 0, clicks bigint NOT NULL DEFAULT 0,
  platform_conversions numeric NOT NULL DEFAULT 0, opportunity_status varchar(40) NOT NULL DEFAULT 'INSUFFICIENT_DATA',
  UNIQUE(account_id,metric_date,campaign_external_id,ad_group_external_id,search_term)
);
CREATE TABLE IF NOT EXISTS public.wi_ads_change_log (
  id bigserial PRIMARY KEY, platform varchar(20) NOT NULL, account_id bigint REFERENCES public.wi_ad_accounts(id),
  entity_type varchar(30) NOT NULL, entity_external_id varchar(160) NOT NULL, change_type varchar(80) NOT NULL,
  before_state jsonb NOT NULL DEFAULT '{}'::jsonb, after_state jsonb NOT NULL DEFAULT '{}'::jsonb,
  workflow_status varchar(24) NOT NULL DEFAULT 'DRAFT', risk varchar(16) NOT NULL DEFAULT 'UNKNOWN',
  reason text, created_by varchar(200) NOT NULL, approved_by varchar(200),
  created_at timestamptz NOT NULL DEFAULT now(), review_at timestamptz,
  CONSTRAINT wi_ads_change_workflow CHECK(workflow_status IN ('RECOMMENDED','DRAFT','REVIEWED','APPROVED','EXECUTED','OBSERVATION','RESULT')),
  CONSTRAINT wi_ads_change_risk CHECK(risk IN ('LOW','MEDIUM','HIGH','CRITICAL','UNKNOWN'))
);

CREATE TABLE IF NOT EXISTS public.help_categories (
  id bigserial PRIMARY KEY, slug varchar(100) NOT NULL UNIQUE, name varchar(160) NOT NULL,
  description text, sort_order integer NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS public.help_articles (
  id bigserial PRIMARY KEY, slug varchar(160) NOT NULL UNIQUE, category_id bigint REFERENCES public.help_categories(id),
  screen_id varchar(120), title varchar(240) NOT NULL, summary text NOT NULL, content text NOT NULL,
  level varchar(20) NOT NULL DEFAULT 'BASIC', version varchar(30) NOT NULL DEFAULT '1.0',
  module_version varchar(60), published boolean NOT NULL DEFAULT true,
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT help_articles_level CHECK(level IN ('BASIC','INTERMEDIATE','ADVANCED'))
);
CREATE INDEX IF NOT EXISTS idx_help_articles_screen ON public.help_articles(screen_id);
CREATE TABLE IF NOT EXISTS public.help_keywords (
  article_id bigint NOT NULL REFERENCES public.help_articles(id) ON DELETE CASCADE,
  keyword varchar(120) NOT NULL, PRIMARY KEY(article_id,keyword)
);
CREATE TABLE IF NOT EXISTS public.help_article_roles (
  article_id bigint NOT NULL REFERENCES public.help_articles(id) ON DELETE CASCADE,
  role_key varchar(100) NOT NULL, PRIMARY KEY(article_id,role_key)
);
CREATE TABLE IF NOT EXISTS public.help_context_links (
  screen_id varchar(120) PRIMARY KEY, article_id bigint NOT NULL REFERENCES public.help_articles(id) ON DELETE CASCADE,
  updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO public.help_categories(slug,name,description,sort_order) VALUES
 ('operacion','Operación CRM','Procedimientos operativos verificados',10),
 ('business-intelligence','Business Intelligence','Métricas, atribución y análisis',20),
 ('paid-media','Paid Media','Google Ads, Meta Ads y métricas CRM',30),
 ('errores','Errores frecuentes','Diagnóstico seguro para usuarios',40)
ON CONFLICT(slug) DO UPDATE SET name=excluded.name,description=excluded.description;

INSERT INTO public.help_articles(slug,category_id,screen_id,title,summary,content,level,version,module_version)
SELECT v.slug,c.id,v.screen_id,v.title,v.summary,v.content,v.level,'1.0','2026.08.09'
FROM (VALUES
 ('crear-lead','operacion','leads_ver','Crear un lead','Registrar una oportunidad manual sin escribir UTMs.','Abre Leads → Ver Leads → Crear lead. Completa nombre, marca, comuna, fecha y Origen del lead. La campaña es opcional. Los datos técnicos de atribución se completan automáticamente cuando existe una sesión enlazada.','BASIC'),
 ('utm-builder','business-intelligence','gdi_utm','Constructor UTM','Crear enlaces trazables asociados a campañas.','Selecciona sitio, URL, fuente, medio y campaña. La URL queda ligada a la campaña para medir visitas, leads, cotizaciones, conversión e ingresos cuando exista evidencia.','INTERMEDIATE'),
 ('attribution','business-intelligence','gdi_attribution','Atribución digital','Distinguir First Touch, Last Touch y nivel de evidencia.','EXACT usa identificadores fuertes; STRONG combina evidencia consistente; INFERRED es una correlación y nunca debe presentarse como hecho; UNKNOWN indica que no existe evidencia suficiente.','INTERMEDIATE'),
 ('paid-media-basico','paid-media','gdi_paid_media','Paid Media: conceptos básicos','Campañas, anuncios, impresiones y clics.','Una campaña organiza objetivo y presupuesto. Un anuncio es la pieza mostrada. Una impresión es una visualización registrada y un clic una interacción. Ninguna métrica aislada demuestra una venta.','BASIC'),
 ('paid-media-kpis','paid-media','gdi_paid_media','CTR, CPC, CPL, CPA y ROAS','Interpretar métricas sin benchmarks universales.','CTR = clics / impresiones. CPC = gasto / clics. CPL CRM = gasto / leads CRM. CPA CRM = gasto / ventas CRM. ROAS CRM = ingresos atribuidos / gasto. Su interpretación depende de canal, marca, objetivo, temporada, ticket y margen.','INTERMEDIATE'),
 ('paid-media-learning','paid-media','gdi_paid_media','Learning, cooldown y riesgo de cambios','Evitar cambios encadenados con evidencia insuficiente.','No toda modificación reinicia aprendizaje. El sistema distingue STABLE, RECENT_CHANGE, LEARNING, LOW_DATA, LIMITED e INSUFFICIENT_EVIDENCE. Durante cooldown se observa hasta la fecha de revisión.','ADVANCED'),
 ('integration-center','business-intelligence','gdi_integrations','Configurar integraciones','Conectar servicios sin exponer secretos.','La UI muestra IDs públicos, estado y última verificación. Google y Meta se autorizan sin pedir contraseñas. Los secretos permanecen en el backend o secret store.','INTERMEDIATE'),
 ('whatsapp','operacion','tool_wapp','WhatsApp operativo','Responder, vincular leads y conservar evidencia.','WhatsApp vive en Tools. Selecciona conversación, confirma marca y lead, responde y registra seguimiento. Una atribución web sólo se enlaza cuando existe una referencia verificable.','BASIC')
) AS v(slug,category_slug,screen_id,title,summary,content,level)
JOIN public.help_categories c ON c.slug=v.category_slug
ON CONFLICT(slug) DO UPDATE SET title=excluded.title,summary=excluded.summary,content=excluded.content,
 level=excluded.level,version=excluded.version,module_version=excluded.module_version,updated_at=now();

INSERT INTO public.help_context_links(screen_id,article_id)
SELECT DISTINCT ON (screen_id) screen_id,id FROM public.help_articles WHERE screen_id IS NOT NULL
ORDER BY screen_id, CASE level WHEN 'BASIC' THEN 1 WHEN 'INTERMEDIATE' THEN 2 ELSE 3 END, id
ON CONFLICT(screen_id) DO UPDATE SET article_id=excluded.article_id,updated_at=now();

INSERT INTO public.help_keywords(article_id,keyword)
SELECT a.id,k.keyword FROM public.help_articles a
CROSS JOIN LATERAL unnest(string_to_array(lower(a.title||' '||a.summary),' ')) AS k(keyword)
WHERE length(k.keyword)>2
ON CONFLICT DO NOTHING;

COMMIT;
