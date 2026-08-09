-- Bandeja omnicanal: estado operativo mínimo sobre fuentes existentes.
-- No replica mensajes, cuerpos, adjuntos ni payloads de los canales.
SET lock_timeout = '5s';
SET statement_timeout = '60s';

CREATE TABLE IF NOT EXISTS public.wi_omnichannel_work_items (
  id BIGSERIAL PRIMARY KEY,
  channel TEXT NOT NULL CHECK (channel IN ('WHATSAPP','INSTAGRAM','MESSENGER','EMAIL')),
  source_ref TEXT NOT NULL,
  lead_id BIGINT REFERENCES public.leads(id_lead) ON DELETE SET NULL,
  quote_id BIGINT REFERENCES public.cotizaciones(id_cotizacion) ON DELETE SET NULL,
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

CREATE INDEX IF NOT EXISTS idx_wi_omnichannel_work_queue
  ON public.wi_omnichannel_work_items(status, follow_up_at, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_wi_omnichannel_work_lead
  ON public.wi_omnichannel_work_items(lead_id) WHERE lead_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_wi_omnichannel_work_assignee
  ON public.wi_omnichannel_work_items(assigned_user_id, status);

COMMENT ON TABLE public.wi_omnichannel_work_items IS
  'Referencias y estado operativo de Inbox; el contenido permanece exclusivamente en la tabla fuente de cada canal.';
COMMENT ON COLUMN public.wi_omnichannel_work_items.source_ref IS
  'Identificador estable del registro original; nunca contiene el cuerpo del mensaje.';

INSERT INTO public.help_articles(
  slug, category_id, screen_id, title, summary, content, level, version, module_version
)
SELECT v.slug, c.id, 'tool_omnichannel', v.title, v.summary, v.content, v.level, '1.0', '2026.08.11'
FROM (VALUES
  ('omnichannel-basic','Bandeja unificada','Atender todos los canales desde una cola ordenada.','La bandeja reúne referencias de WhatsApp, Instagram, Messenger y Email. Abre la conversación en su canal para responder; vincula el lead sólo cuando reconoces al contacto.','BASIC'),
  ('omnichannel-funnel','Embudo por canal','Leer conversaciones, leads, cotizaciones, ventas e ingresos.','Cada avance exige un vínculo real. Si una conversación no está asociada a un lead, no cuenta como lead ni como venta. NO_DATA significa que la fuente no entregó registros en el alcance consultado.','INTERMEDIATE'),
  ('omnichannel-evidence','Evidencia y límites por canal','Comprender capacidades, alcance y no duplicación.','Los mensajes permanecen en la tabla original de cada canal. La bandeja guarda sólo referencia, responsable, seguimiento, lead y cotización. Instagram y Messenger se muestran como sólo recepción mientras no exista envío Graph API autorizado.','ADVANCED')
) AS v(slug,title,summary,content,level)
JOIN public.help_categories c ON c.slug='operacion'
ON CONFLICT(slug) DO UPDATE SET
  title=excluded.title, summary=excluded.summary, content=excluded.content,
  level=excluded.level, module_version=excluded.module_version, updated_at=now();

INSERT INTO public.help_context_links(screen_id,article_id)
SELECT 'tool_omnichannel', id FROM public.help_articles WHERE slug='omnichannel-basic'
ON CONFLICT(screen_id) DO UPDATE SET article_id=excluded.article_id,updated_at=now();

INSERT INTO public.help_keywords(article_id,keyword)
SELECT a.id,k.keyword FROM public.help_articles a
CROSS JOIN LATERAL unnest(ARRAY['inbox','bandeja','whatsapp','instagram','messenger','email','canal']) AS k(keyword)
WHERE a.slug IN ('omnichannel-basic','omnichannel-funnel','omnichannel-evidence')
ON CONFLICT DO NOTHING;
