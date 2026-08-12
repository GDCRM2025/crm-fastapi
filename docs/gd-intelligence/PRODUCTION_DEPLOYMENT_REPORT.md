# Production Deployment Report

Fecha: 2026-08-12
Resultado: **DEPLOYED_STABLE — USER_ACCEPTED_PENDING_GOVERNANCE_GATES**

## Identidad del release

| Campo | Valor |
|---|---|
| Servidor | `crm-gd` / `192.168.100.51` |
| Servicio | `crm-gd.service` (`active`) |
| SHA anterior / rollback | `ec43b363e52dc762b3b030ad421800f878ed6afc` |
| SHA desplegado | `b35e07579364fe99325b5b1db38ffe94a81ced99` |
| Release | `/opt/greendiamond/releases/20260812_095522` |
| Backup | `/opt/greendiamond/backups/deploy/20260812_095522` |
| Alcance | CRM baseline versionado, GD Intelligence, RBAC, Sites, UTM, Site Health, Integration Center, Paid Media read-only, atribución, SEO, alertas, Dashboard Ejecutivo, Omnichannel y Help |

## Backups y checksums

| Artefacto | SHA-256 | Verificación |
|---|---|---|
| `code_before.tar.gz` | `ac9d97366a90109642163a3cbd535e8525afbdd7aaf42c67aeeb98d580784a1b` | PASS |
| `postgres_before.dump` | `90483a4a3864f84b69914df2b4add75e9d35552e57c6751f0374e118f4fcc0cc` | PASS, `pg_restore --list` |
| release `b35e075` | `e5a462464a89351f537bc235d54172ff0de18fd64a95da442d6389ea84f44dea` | PASS remoto |

El release se instaló sin `--delete`. `.env`, datos, uploads, backups y archivos no clasificados no fueron eliminados ni reemplazados por el paquete.

## Gates y decisión

| Gate | Estado |
|---|---|
| Tests/build/compile local | PASS, 100 tests |
| Gitleaks | PASS, 0 hallazgos |
| Backup código y PostgreSQL | PASS |
| Migraciones aditivas | PASS |
| Repositorio privado | FAIL — GitHub sigue público |
| Rotación legacy | FAIL — sin evidencia de revocación de proveedor |
| Reconciliación total de archivos Ubuntu | PENDING |

El usuario autorizó explícitamente implementar con el estado disponible. La aplicación quedó estable, pero los tres gates de gobierno anteriores continúan abiertos y `PRODUCTION_READINESS` no se considera cerrado.

## Migraciones ejecutadas

- `2026_08_08_gd_intelligence_core.sql`
- `2026_08_08_web_intelligence_sources.sql`
- `2026_08_08_site_health.sql`
- `2026_08_08_integration_inventory.sql`
- `2026_08_09_tracking_attribution.sql`
- `2026_08_09_paid_media_help.sql`
- `2026_08_10_paid_media_intelligence.sql`
- `2026_08_10_credential_vault.sql`
- `2026_08_10_intelligence_platform.sql`
- `2026_08_11_omnichannel_inbox.sql`

Todas finalizaron con `ON_ERROR_STOP=1` y estado PASS.

## Reinicio y smoke tests

El servicio pasó de MainPID `226664` a `226820`; systemd quedó `active`. OpenAPI aumentó de 378 a 420 paths.

| Smoke test | Estado |
|---|---|
| `/healthz` FastAPI y `/crm/healthz` Nginx | PASS, HTTP 200 |
| Login page y autenticación JWT con usuario canónico | PASS, HTTP 200 |
| Leads | PASS, HTTP 200 |
| Cotizaciones | PASS, HTTP 200 |
| WABA | PASS, HTTP 200 |
| GD Intelligence overview | PASS, HTTP 200 |
| Site Health | PASS, HTTP 200 |
| Paid Media | PASS, HTTP 200 |
| Omnichannel | PASS, HTTP 200 |
| Sitios habilitados | PASS, 4 |

Site Health se ejecutó contra CAM/DEL/EXP/GOU: los cuatro respondieron HTTP 200 y registraron `WARNING` por hallazgos. Como `sudo` no estaba disponible para instalar el timer systemd, quedó un crontab preservando entradas existentes, cada 15 minutos, bajo el usuario de servicio.

## Incidencias y rollback

1. El primer `pg_dump` no tomó la URL del ORM y generó un archivo vacío; se reemplazó antes de continuar mediante resolución segura de la configuración de aplicación. El dump definitivo pasó checksum y `pg_restore --list`.
2. El primer staging no validó JavaScript porque Ubuntu no tiene Node. No llegó a producción; el mismo código había pasado build y validación JS en Mac.
3. El primer copy encontró `.pyc` versionados con permisos incompatibles. El trap restauró `code_before.tar.gz` y `/healthz` quedó PASS. Los artefactos runtime se excluyeron y la segunda instalación terminó correctamente.

`ROLLBACK_STATUS=TESTED_AUTOMATIC_PASS`; no fue necesario mantener el rollback porque el release final y todos los smoke tests quedaron estables.
