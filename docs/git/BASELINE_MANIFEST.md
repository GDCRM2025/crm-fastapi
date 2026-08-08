# Manifiesto clean baseline — 2026-08-08

## Fuente

El candidato se construyó mediante reconciliación explícita de:

- Mac: código actual con correcciones de seguridad/RBAC/WABA/GD Intelligence.
- Git legacy: historia preservada en bundle verificado.
- Servidor: snapshot verificado de producción, SHA `ec43b36` como referencia de Git y working tree productivo preservado.

No se usó rsync ciego servidor↔Mac ni se modificó producción.

## Decisiones de conflicto

- Mac prevalece en auth seguro, folios de cotización, compatibilidad de schema, PDFs autenticados, eliminación de decoradores duplicados y GD Intelligence.
- Servidor prevalece en el hotfix de espera/reintento de agenda (`backend/routers/tools.py` y `web/views/leads.html`).
- Mac prevalece en `event_surveys.py` (savepoint DDL) y `crm-vpn-peer` (endpoint no hardcodeado).
- Se incorporan desde servidor assets SVG/stickers de marca y scripts Git seguros.
- Snapshots `pre-uat`, `*.bak*` y runtime quedan sólo en backups, no en baseline.

## Exclusiones

Secretos, `.env`, keys/credentials, dumps, backups, logs, data/quotes, caches, virtualenvs, node_modules, Pods, build/dist, videos de evidencia, ZIP/RPM y PII.

## Gates del candidato

- Gitleaks directory scan: 0 hallazgos.
- Python compile: PASS.
- GD Intelligence unit tests: 14/14 PASS.
- Web JavaScript syntax: PASS.
- Frontend build: PASS.
- npm audit producción y desarrollo: 0 vulnerabilidades después de actualizar el lockfile.
- Restore aislado: PASS.
- Migraciones GD Intelligence, dos ejecuciones: PASS.

## Estado

Repositorio local nuevo en `/Users/oscarmendoza/Desktop/GreenDiamond-CRM`, branch `main`. No tiene remoto ni deployment. Su publicación sólo puede hacerse hacia un repositorio GitHub nuevo y privado.
