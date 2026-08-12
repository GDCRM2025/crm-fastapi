# Production Deployment Report — Immutable Cutover

Fecha: 2026-08-12
Resultado: **IMMUTABLE_CUTOVER_PASS**

## Release y arquitectura

| Campo | Resultado |
|---|---|
| Release candidate | `88704006e10c87ecd813a7f24e3eb91da8718a50` |
| Release inmutable | `/opt/greendiamond/releases/88704006e10c87ecd813a7f24e3eb91da8718a50` |
| Symlink productivo | `/opt/greendiamond/current` → release inmutable |
| Compatibilidad service unit | `/opt/greendiamond/crm` → `/opt/greendiamond/current` |
| Runtime legacy preservado | `/opt/greendiamond/legacy/crm-pre-20260812_100459` |
| Runtime compartido | `/opt/greendiamond/shared/runtime` |
| Secretos compartidos | `/opt/greendiamond/shared/secrets` (`0700`; valores no consultados ni documentados) |
| Datos persistentes | `/opt/greendiamond/shared/persistent-data` |

El MainPID productivo `229950` confirmó cwd real en el release inmutable. El worktree legacy dejó de ser runtime activo. El release contiene `RELEASE_SHA`, `RELEASE_TIMESTAMP` y un manifest SHA-256; código y archivos regulares quedaron read-only. Logs, runtime, datos y secretos usan enlaces a `shared`.

## Validación previa

- Git limpio; HEAD congelado: `88704006e10c87ecd813a7f24e3eb91da8718a50`.
- 100 tests PASS, Python compile PASS, frontend build PASS y Gitleaks PASS.
- Diez migraciones GD Intelligence ejecutadas dos veces sobre PostgreSQL local aislado: PASS/idempotentes.
- Producción efectiva confirmada desde configuración de aplicación: `bf68ec5_crm2025b`, PostgreSQL `16.14`.
- Servicio legacy y PostgreSQL activos, `/healthz` 200, 0 locks en espera, 0 transacciones mayores a cinco minutos.

## Backup fresco

Ubicación: `/opt/greendiamond/backups/cutover/20260812_100459`

| Artefacto | SHA-256 | Verificación |
|---|---|---|
| `legacy_tree_before.tar.gz` | `1205273c4f410b45c0e593d6671773b817c08910dd43991d07f770839699bdc4` | `tar -tzf` PASS |
| `postgres_before.dump` | `f851d55fff1cdded66cc99de429354b50331bc628a0bbf47b7883e5673a368b1` | `pg_restore --list` PASS |
| `config_before.tar.gz` | `048efd818e8d11051e9b34e649a2b34068e85f35584b4ae6bc36026bb9f72db5` | PASS |

El backup de configuración conserva service unit, Nginx y crontab con permisos restrictivos. Ningún valor secreto se imprimió o incorporó a documentación.

## Candidato paralelo

El candidato ejecutó exactamente el release en `127.0.0.1:9090`. El primer arranque detectó que `logs/` debía residir en shared; se detuvo antes del cutover, se reconstruyó/selló el release con enlaces operativos y el segundo arranque pasó.

Smoke read-only candidato:

- health, login, auth y permissions: PASS;
- Leads, Filtro Leads y CRM360: PASS;
- Cotizaciones e Historial: PASS;
- Tasks, WABA, Tools, Email y Settings: PASS;
- GD Intelligence, Sitios, Integration Center, Site Health, UTM y Paid Media: PASS;
- Help, Omnichannel y vistas UI: PASS;
- HTML/JS `no-cache`: PASS;
- respuestas de Integration Center/Paid Media sin campos secretos: PASS;
- Instagram y Messenger: `RECEIVE_ONLY`, sin representación falsa de envío.

## QA de datos

Conteos productivos observados:

| Entidad | Conteo |
|---|---:|
| Leads | 4.001 |
| Cotizaciones | 3.370 |
| Usuarios | 204 |
| Roles | 13 |
| WABA conversations | 1 |
| WABA messages | 40 |
| Tasks | 23.660 |

Legacy y candidato devolvieron HTTP 200 y resultados compatibles para Leads, Cotizaciones, WABA, Tasks y permisos. Ambos apuntaban a la misma base efectiva confirmada.

## Cutover y smoke productivo

El cutover movió el runtime anterior a `legacy`, creó `/opt/greendiamond/current`, mantuvo `/opt/greendiamond/crm` sólo como symlink de compatibilidad y reinició el servicio mediante systemd. No se usó `git pull`, `reset`, `clean` ni rsync destructivo.

Todos pasaron con HTTP 200:

- `/healthz` interno y `/crm/healthz` por Nginx;
- login, auth y permisos;
- Leads, Filtro Leads, CRM360;
- Cotizaciones e Historial;
- Tasks, WABA, Tools, Email y Settings;
- GD Intelligence, Sitios, Integration Center, Site Health, UTM y Paid Media;
- Help, Omnichannel y las tres vistas UI nuevas.

Observación posterior: servicio active, 0 HTTP 5xx, 0 tracebacks, 0 errores de aplicación/WABA/cotizaciones/leads, 0 locks, 0 transacciones largas, memoria disponible 5,6 GB y logs escribiendo en shared. El proceso candidato 9090 fue detenido tras el cutover.

## Migraciones y rollback

Las diez migraciones aditivas ya habían sido aplicadas con `ON_ERROR_STOP=1` en el deployment técnico inmediatamente anterior; el cutover inmutable no las repitió innecesariamente. La idempotencia se revalidó dos veces antes del cutover.

Rollback listo:

1. retirar el symlink `/opt/greendiamond/crm`;
2. restaurar `/opt/greendiamond/legacy/crm-pre-20260812_100459` como `/opt/greendiamond/crm`;
3. reiniciar `crm-gd.service`;
4. usar backup PostgreSQL sólo si una validación de datos lo exige.

`ROLLBACK_EXECUTED=NO`; ningún control posterior al cutover falló.

## Gates

```text
PRIVATE_REPOSITORY=FAIL
SECRET_ROTATION=FAIL
SERVER_RECONCILIATION=PASS
PRODUCTION_READINESS=FAIL
```

`SERVER_RECONCILIATION` pasa porque el código productivo ya depende del release reproducible y no del código único legacy. Los archivos legacy permanecen preservados sólo como rollback. Los otros dos gates requieren acciones reales en GitHub y proveedores; el cutover no los falsifica.

## Estado obligatorio

```text
RELEASE_CANDIDATE_SHA=88704006e10c87ecd813a7f24e3eb91da8718a50
SERVER_RELEASE_PATH=/opt/greendiamond/releases/88704006e10c87ecd813a7f24e3eb91da8718a50
LEGACY_RUNTIME_PRESERVED=PASS:/opt/greendiamond/legacy/crm-pre-20260812_100459

BACKUP_CODE=PASS:/opt/greendiamond/backups/cutover/20260812_100459/legacy_tree_before.tar.gz
BACKUP_DATABASE=PASS:/opt/greendiamond/backups/cutover/20260812_100459/postgres_before.dump
BACKUP_CHECKSUMS=PASS:/opt/greendiamond/backups/cutover/20260812_100459/SHA256SUMS

DATABASE_CONFIRMED=PASS:bf68ec5_crm2025b

CANDIDATE_SERVICE=PASS_THEN_STOPPED
CANDIDATE_HEALTH=PASS

PRODUCTION_CUTOVER=PASS
PRODUCTION_SHA=88704006e10c87ecd813a7f24e3eb91da8718a50
PRODUCTION_HEALTH=PASS

LOGIN_SMOKE=PASS
LEADS_SMOKE=PASS
QUOTES_SMOKE=PASS
WABA_SMOKE=PASS
TOOLS_SMOKE=PASS
GD_INTELLIGENCE_SMOKE=PASS
PAID_MEDIA_SMOKE=PASS
HELP_SMOKE=PASS

ROLLBACK_READY=PASS
ROLLBACK_EXECUTED=NO

PRIVATE_REPOSITORY=FAIL
SECRET_ROTATION=FAIL
SERVER_RECONCILIATION=PASS

GOOGLE_ADS=READY_FOR_CREDENTIAL
GOOGLE_ADS_HISTORY=NO_DATA
META_ADS=READY_FOR_CREDENTIAL
META_ADS_HISTORY=NO_DATA
PAID_MEDIA_ATTRIBUTION=READY_NO_AD_DATA
SEARCH_TERMS=READY_NO_AD_DATA
CREATIVE_INTELLIGENCE=READY_NO_AD_DATA
CHANGE_RISK=PASS_LOCAL_AND_DEPLOYED

SEO_OPPORTUNITY=PARTIAL_REAL

OMNICHANNEL=PARTIAL_REAL
INSTAGRAM=RECEIVE_ONLY_NO_DATA
MESSENGER=RECEIVE_ONLY_NO_DATA
EMAIL=PASS_REAL

ALERTS=PASS_DEPLOYED
EXECUTIVE_DASHBOARD=PASS_DEPLOYED_PARTIAL_REAL

GD_AI=PASS_CONTEXT_BUILDER_DEPLOYED
WABA_AI=PENDING

CHANGE_REQUESTS=PENDING
EXPERIMENTS=PENDING
CHANGE_IMPACT=PENDING

HELP_COVERAGE=NEW_FUNCTIONS_PASS_LEGACY_66_PENDING

TESTS_TOTAL=100
TESTS_PASS=100
TESTS_FAIL=0

PRODUCTION_READINESS=FAIL
```
