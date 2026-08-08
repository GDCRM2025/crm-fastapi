# Base de datos GD Intelligence

PostgreSQL productivo: 16.14, UTF-8, esquema `public`, 49 MB al 2026-08-08. GD Intelligence usa tablas aditivas prefijadas para ser compatible con la arquitectura existente.

## Núcleo inicial

- `gd_role_permissions`, `gd_user_permissions`.
- `wi_sites`, `wi_integrations`.
- `wi_sync_runs`, `wi_daily_metrics`, `wi_seo_query_daily`.
- `wi_performance_runs`, `wi_utm_links` y secuencia de identificadores.

No se agregan columnas analíticas a `leads` ni `cotizaciones`. Las extensiones futuras referenciarán entidades core con relaciones explícitas y conservarán históricos append-only.

## Ambientes

- Producción: VM Ubuntu, nunca usada para tests destructivos.
- Restore/migration test: clúster PostgreSQL local aislado y detenido al finalizar.
- Futuro desarrollo: fixtures/mocks o base dedicada sin PII.

Consultar `DATABASE_SAFETY.md`, `RESTORE_TEST_20260808.md` y `MIGRATION_VALIDATION.md` antes de cualquier operación.
