# Seguridad de base de datos — GD Intelligence

## Antes de migrar

1. Confirmar `git status`, rama y SHA.
2. Confirmar espacio libre, versión PostgreSQL, tamaño de base, conexiones y locks pendientes.
3. Crear `pg_dump --format=custom --no-owner --no-privileges`.
4. Guardar SHA-256 y `pg_restore --list`.
5. Probar el dump en una base aislada y ejecutar conteos/smoke tests.
6. Confirmar snapshot/backup de VM 100 sin tratarlo como reemplazo del dump.

## Ejecución

La migración core usa transacción, `lock_timeout=3s`, `statement_timeout=60s`, DDL aditivo e inserts idempotentes. Si no obtiene el lock rápidamente, debe fallar y reprogramarse; no se aumentará el timeout a ciegas.

No ejecutar `DROP`, `TRUNCATE`, `DELETE` sin predicado, alters destructivos ni actualizaciones masivas. Los índices sobre tablas core grandes requieren plan separado y, cuando aplique, `CREATE INDEX CONCURRENTLY`.

## Rollback

El rollback normal es: deshabilitar feature, restaurar código anterior y conservar tablas/historia aditiva. No borrar objetos GD Intelligence durante una incidencia. La reversión de datos sólo se realizará desde un restore aislado y con una ventana aprobada.

## Validación posterior

- CRM `/healthz` y OpenAPI responden.
- Conteos de leads, cotizaciones, usuarios y WABA no cambian inesperadamente.
- No hay locks pendientes ni sesiones anómalas.
- Las cuatro filas seed de `wi_sites` existen una sola vez.
- Todas las integraciones comienzan `NOT_CONFIGURED` y deshabilitadas.
