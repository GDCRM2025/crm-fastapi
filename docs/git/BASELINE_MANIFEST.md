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
- `git diff --cached --check`: PASS.
- Política de archivos secretos/runtime y archivos mayores de 10 MiB: PASS.
- Workflow CI parseable: PASS.
- Gitleaks sobre el historial del commit: 0 hallazgos.
- `git fsck --full --strict`: PASS después de podar objetos inalcanzables del staging previo.

## Estado

Repositorio local nuevo en `/Users/oscarmendoza/Desktop/GreenDiamond-CRM`, branch `main`, commit raíz `721a6e7d90824189354d450e3f0277aecab3795f`. No tiene remoto ni deployment. Su publicación sólo puede hacerse hacia un repositorio GitHub nuevo y privado.

Bundle verificado: `/Users/oscarmendoza/Desktop/CRM_2025_safety_20260808_173955/clean-baseline-721a6e7.bundle`, SHA-256 `9cfe41ae9609e0241f02f0a7863940e7f5eba05b92e69a03c14a2037359c96b6`.
