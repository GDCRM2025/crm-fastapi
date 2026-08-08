# Manifiesto clean baseline — 2026-08-08

## Fuente reconciliada

- Mac: código actual con correcciones de seguridad, RBAC, WABA y GD Intelligence.
- Git legacy: historia preservada en bundle verificado.
- Servidor: snapshot verificado de producción, SHA `ec43b36` como referencia y working tree preservado.
- Mac prevalece en auth, folios, schema, PDFs, decoradores duplicados, `event_surveys.py` y VPN sin endpoint hardcodeado.
- Servidor prevalece en el hotfix de agenda de `backend/routers/tools.py` y `web/views/leads.html`.
- Se incorporaron assets SVG/stickers y scripts Git seguros del servidor.

No se modificó producción y no se usó sincronización ciega servidor↔Mac.

## Exclusiones

Secretos, `.env`, keys/credentials, bases locales, dumps, backups, logs, runtime, uploads, caches, virtualenvs, node_modules, Pods, build/dist, ZIP/RPM y PII.

## Gates

- Gitleaks directory e historial: 0 hallazgos.
- Python compile: PASS.
- GD Intelligence unit tests: 14/14 PASS.
- Web JavaScript syntax: PASS.
- Frontend build: PASS.
- npm audit: 0 vulnerabilidades.
- `git diff --check`: PASS.
- Política de archivos y tamaños: PASS.
- Restore aislado y migraciones GD idempotentes: PASS.
- `git fsck --full --strict`: PASS.

## Identidad recuperable

- Directorio: `/Users/oscarmendoza/Desktop/GreenDiamond-CRM`.
- Branch: `main`.
- Commit raíz: `721a6e7d90824189354d450e3f0277aecab3795f`.
- Commit de evidencia: `0a0d08d777ce1cc138140d587b38911315869f00`.
- Bundle: `/Users/oscarmendoza/Desktop/CRM_2025_safety_20260808_173955/clean-baseline-0a0d08d.bundle`.
- SHA-256: `3b06aad412daf6527c81aa290a7d31c5cf948b8b90094cb135f52ea23a8ffe78`.
- Remoto/deployment: ninguno.
