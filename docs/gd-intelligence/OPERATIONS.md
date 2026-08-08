# Operaciones

- Site health: cada 15 minutos.
- GA4: horario; Search Console: diario; PageSpeed: diario o varias veces por semana; CrUX: diario/semanal.
- Jobs incrementales con cursor, timeout y exponential backoff; nunca dentro del request de dashboard.
- Alertar por job/integración fallida, sitio caído, degradación web, caída SEO/conversión y WABA sin respuesta.
- Revisar diariamente último éxito, filas, duración, retries, disco y backup.
- Logs no deben contener tokens, prompts completos con PII ni payloads Meta/Google sin redacción.
