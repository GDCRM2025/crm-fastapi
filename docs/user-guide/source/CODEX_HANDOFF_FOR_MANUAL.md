# Handoff para completar el manual

1. Arrancar desde cero siguiendo `docs/development/MAC_SETUP.md` y confirmar `/healthz`.
2. Regenerar catálogos y revisar el diff; cualquier acceso eliminado debe explicarse, no borrarse silenciosamente.
3. Recorrer `DOCUMENTATION_COVERAGE.md`, priorizando P0 y pantallas sin artículo contextual.
4. Probar cada acción del `ACTION_CATALOG.md` con el rol mínimo aplicable y registrar precondición, resultado y error.
5. Tomar capturas según `SCREENSHOT_PLAN.md` sólo con datos aislados.
6. Redactar artículos versionados y añadir su `screen_id` en la migración de ayuda.
7. Ejecutar tests, secret scan y una búsqueda de PII antes de publicar el manual.

Pendientes deliberados: sync real de Google/Meta requiere credenciales externas; unificación omnicanal completa requiere adaptadores y pruebas de conversaciones. Ninguno autoriza cambios productivos.
