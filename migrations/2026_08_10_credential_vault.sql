-- Write-only integration credential vault. Additive/idempotent; never imports legacy secrets.
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='60s';

ALTER TABLE public.wi_integrations
  ADD COLUMN IF NOT EXISTS last_failure_at timestamptz;

CREATE TABLE IF NOT EXISTS public.wi_integration_credentials (
  id bigserial PRIMARY KEY,
  integration_id bigint NOT NULL REFERENCES public.wi_integrations(id) ON DELETE RESTRICT,
  credential_type varchar(60) NOT NULL,
  encrypted_value text,
  key_version varchar(30) NOT NULL DEFAULT 'v1',
  masked_suffix varchar(8),
  status varchar(24) NOT NULL DEFAULT 'CONFIGURED',
  created_by varchar(200),
  updated_by varchar(200),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  last_verified_at timestamptz,
  last_success_at timestamptz,
  last_error_at timestamptz,
  last_error_code varchar(80),
  UNIQUE(integration_id,credential_type),
  CONSTRAINT wi_credential_status CHECK(status IN ('CONFIGURED','REVOKED')),
  CONSTRAINT wi_credential_no_plaintext CHECK(
    (status='CONFIGURED' AND encrypted_value IS NOT NULL) OR
    (status='REVOKED' AND encrypted_value IS NULL)
  )
);
CREATE INDEX IF NOT EXISTS idx_wi_credentials_integration
  ON public.wi_integration_credentials(integration_id,status);

CREATE TABLE IF NOT EXISTS public.wi_integration_credential_events (
  id bigserial PRIMARY KEY,
  integration_id bigint NOT NULL REFERENCES public.wi_integrations(id) ON DELETE RESTRICT,
  credential_type varchar(60) NOT NULL,
  action varchar(50) NOT NULL,
  actor varchar(200) NOT NULL,
  success boolean NOT NULL,
  error_code varchar(80),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT wi_credential_event_action CHECK(action IN (
    'CREDENTIAL_CONFIGURED','CREDENTIAL_REPLACED','CONNECTION_VERIFIED',
    'CONNECTION_FAILED','INTEGRATION_DISCONNECTED'
  ))
);
CREATE INDEX IF NOT EXISTS idx_wi_credential_events_integration
  ON public.wi_integration_credential_events(integration_id,created_at DESC);

INSERT INTO public.help_articles(slug,category_id,screen_id,title,summary,content,level,version,module_version)
SELECT v.slug,c.id,'gdi_integrations',v.title,v.summary,v.content,v.level,'1.1','2026.08.10'
FROM (VALUES
 ('integraciones-seguras','Conexiones seguras','Qué ocurre al configurar o reemplazar una credencial.','Una credencial se ingresa dos veces, se prueba y se cifra. Nunca puede consultarse desde el CRM, ni siquiera por Super Admin. Si se pierde, se reemplaza. Desconectar revoca el valor y conserva los datos históricos.','BASIC'),
 ('integracion-google','Conectar Google','Analytics, Search Console y Google Ads sin contraseñas.','Selecciona Conectar Google, autoriza la cuenta y elige las propiedades accesibles. El CRM no solicita la contraseña de Google. Puedes verificar, reautorizar o desconectar.','INTERMEDIATE'),
 ('integracion-meta','Conectar Meta','Business, Ads, Page e Instagram mediante autorización.','Conectar Meta utiliza la autorización oficial. Después se eligen activos públicos y el CRM almacena tokens cifrados. Los tokens nunca se muestran.','INTERMEDIATE'),
 ('integracion-pagespeed','Configurar PageSpeed y CrUX','Medición de rendimiento y experiencia real.','Selecciona Configurar, ingresa la API Key dos veces y prueba. PageSpeed entrega mediciones; CrUX puede no disponer de datos para un origen y eso no significa que la clave sea inválida.','INTERMEDIATE'),
 ('integraciones-publicas','IDs públicos','GTM, Measurement ID, Clarity Project ID y Meta Pixel.','Estos identificadores pueden mostrarse. No son contraseñas ni tokens. El CRM los detecta o permite registrarlos y luego verifica el sitio publicado para evitar tags duplicados.','BASIC')
) AS v(slug,title,summary,content,level)
JOIN public.help_categories c ON c.slug='business-intelligence'
ON CONFLICT(slug) DO UPDATE SET title=excluded.title,summary=excluded.summary,content=excluded.content,
 level=excluded.level,version=excluded.version,module_version=excluded.module_version,updated_at=now();

INSERT INTO public.help_context_links(screen_id,article_id)
SELECT 'gdi_integrations',id FROM public.help_articles WHERE slug='integraciones-seguras'
ON CONFLICT(screen_id) DO UPDATE SET article_id=excluded.article_id,updated_at=now();

COMMIT;
