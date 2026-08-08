# Validación de migraciones GD Intelligence

Base: restore aislado `gd_restore_test_20260808`. Producción no fue modificada.

## Migraciones

1. `migrations/2026_08_08_gd_intelligence_core.sql`.
2. `migrations/2026_08_08_web_intelligence_sources.sql`.

Cada migración se ejecutó dos veces con `ON_ERROR_STOP=1`.

| Comprobación | Resultado |
|---|---|
| Primera ejecución | PASS |
| Segunda ejecución/idempotencia | PASS |
| Transacciones y timeouts | PASS |
| DDL aditivo | PASS |
| Tablas GD creadas | 9 |
| Sitios seed | 4 |
| Integraciones seed | 24 |
| Integraciones deshabilitadas/NOT_CONFIGURED | 24 |
| Índices `idx_wi_*` | 7 |
| Foreign keys GD | 6 |

Sitios: CAM, EXP, GOU y DEL con dominios esperados.

## Integridad core

- Fingerprint de columnas core antes/después: idéntico.
- Conteos de leads, cotizaciones, usuarios, marcas y tablas WABA antes/después: idénticos.
- Ningún `DROP`, `TRUNCATE`, alter destructivo ni update/delete masivo.
- Rollback operativo: deshabilitar/no registrar router y conservar objetos append-only; no borrar historia durante una incidencia.

`MIGRATION_VALIDATION=PASS`.

Este resultado no autoriza migración productiva. Faltan clean baseline privado, secret rotation/scan PASS, tests globales y rollback/deployment desde SHA conocido.
