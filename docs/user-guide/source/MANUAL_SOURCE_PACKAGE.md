# Paquete fuente para el manual CRM

## Fuentes canónicas

- `screen_catalog.json`: índice estructurado de navegación, vistas, campos, botones y referencias API.
- `SCREEN_CATALOG.md`: accesos visibles y archivos que los implementan.
- `ACTION_CATALOG.md`: acciones extraídas del HTML; deben probarse visualmente.
- `CRM_FEATURE_INVENTORY.md`: endpoints FastAPI por método, ruta, handler y archivo.
- `PERMISSION_MATRIX.md`: gate de menú explícito o control por rol, recordando que el backend es autoridad.
- `PROCESS_CATALOG.md`: secuencias operativas confirmadas.
- `DOCUMENTATION_COVERAGE.md`: diferencias entre vista descubierta y ayuda específica.

## Reglas de redacción

1. No afirmar que una acción funciona sólo porque existe un botón.
2. No publicar contraseñas, tokens, cookies, teléfonos, correos ni payloads reales.
3. Identificar versión de módulo, rol, precondición, resultado esperado, errores y escalamiento.
4. Diferenciar conversión de plataforma, lead CRM, cotización y venta confirmada.
5. No usar benchmarks universales para CTR, CPC, CPL, CPA o ROAS.
6. Si el extractor dice “No determinado”, validar en el Mac autenticado antes de documentar.

## Regeneración

Ejecutar `.venv/bin/python scripts/docs/generate_crm_catalog.py` después de cambiar navegación, vistas o routers. Los archivos generados son evidencia versionada; no contienen valores de `.env`.
