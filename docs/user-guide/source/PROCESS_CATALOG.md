# Catálogo de procesos

Este catálogo describe procesos sustentados por pantallas y endpoints inventariados. No sustituye QA; cuando una condición no puede demostrarse desde código se marca para revisión manual.

## Lead comercial

1. El usuario autorizado abre `leads_ver` y crea el lead con un origen comercial comprensible.
2. Una campaña existente es opcional; el Ejecutivo no escribe UTM técnicas.
3. Si existe `gd_session_id`, backend vincula la sesión y persiste `wi_lead_attribution` con first/last touch.
4. La sección Atribución Digital es read-only para Ejecutivo. Una edición autorizada conserva auditoría.
5. Cotizaciones y estado confirmado alimentan métricas CRM; no reemplazan conversiones reportadas por plataformas.

Resultado esperado: lead real, fuente declarada o atribuida, historial y evidencia de permisos. Excepción: sin identificador fuerte, el método queda `UNKNOWN`/confidence baja; no se inventa matching.

## Campaña y URL UTM

1. Marketing abre `gdi_utm`, selecciona sitio y parámetros.
2. Backend normaliza y crea/relaciona `wi_marketing_campaigns` y `wi_utm_links`.
3. El tracker conserva visitor/session/referrer/landing/UTM y registra eventos.
4. La UI puede agregar Visits, Leads, Quotes, Conversion y Revenue sólo cuando existen relaciones persistidas.

Resultado esperado: URL reproducible y métricas inicialmente cero, nunca datos simulados.

## Paid Media

1. Admin configura secretos fuera de Git y asocia IDs públicos a sitios.
2. El conector lee Google/Meta y normaliza entidades y métricas diarias idempotentes.
3. Paid Media distingue métricas de plataforma de métricas CRM.
4. Recomendaciones evalúan cooldown, learning, volumen y riesgo.
5. En esta etapa no hay acción automática sobre campañas; Change Log declara explícitamente cero cambios ejecutados.

Resultado esperado: cuentas/estado o un `NOT_CONFIGURED` accionable. Excepción: credencial ausente no bloquea Tracking, UTM, Site Health ni Ayuda.

## Ayuda contextual

1. El botón global `?` transmite el `screen_id` activo.
2. `/api/help/context/{screen_id}` aplica auth y filtro de rol.
3. La búsqueda consulta artículos BASIC/INTERMEDIATE/ADVANCED y conserva la guía estática como fallback.

Resultado esperado: artículo específico cuando exista; en caso contrario, manual general y cobertura marcada pendiente.

## Omnicanal operativo existente

Correo, Instagram y WhatsApp permanecen en Tools y sus routers existentes. BI consume resultados agregados; no reemplaza webhooks ni crea una segunda bandeja. La unificación completa de conversaciones requiere una capa adaptadora y QA por canal antes de declararse terminada.
