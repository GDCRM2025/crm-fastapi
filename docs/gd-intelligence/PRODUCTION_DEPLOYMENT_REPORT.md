# Production Deployment Report

Fecha: 2026-08-09
Resultado: **BLOCKED_PREDEPLOY — NO_PRODUCTION_CHANGES**

## Identidad del release

| Campo | Valor |
|---|---|
| Servidor | `crm-gd` / `192.168.100.51` |
| Servicio | `crm-gd.service` (`active`) |
| SHA anterior / rollback conocido | `ec43b363e52dc762b3b030ad421800f878ed6afc` |
| Branch remota observada | `feature/whatsapp-native-clean-20260806` |
| SHA candidato funcional validado | `d0408c8` |
| Alcance candidato | GD Intelligence core, RBAC, Sites, Integration Center, UTM, Site Health, tracking, atribución, Paid Media read-only y Help Engine |
| SHA nuevo desplegado | `NOT_DEPLOYED` |

## Gates previos

| Gate | Estado | Evidencia |
|---|---|---|
| Servicio productivo previo | PASS | `crm-gd.service=active` |
| PostgreSQL compatible | PASS | PostgreSQL 16.14 |
| SHA anterior conocido | PASS | `ec43b363…` |
| Tests/build local | PASS | 43 tests, compile, frontend build y Gitleaks PASS |
| Restore/migraciones aisladas | PASS | Migraciones GD/Site Health idempotentes; inventario ejecutado sólo en restore local |
| Worktree servidor controlado | **FAIL CRÍTICO** | 54 entradas tracked y 192 untracked |
| Repositorio privado/off-host | **FAIL CRÍTICO** | repo limpio Mac sin remoto configurado |
| Rotación credenciales históricas | **FAIL CRÍTICO** | credenciales históricamente compartidas marcadas para rotación; no confirmada |
| Secret scan candidato | PASS local | Gitleaks sin hallazgos en historial limpio; debe repetirse sobre SHA final |

## Acciones productivas

| Acción | Estado |
|---|---|
| Backup PostgreSQL nuevo + checksum | `NOT_RUN` (gate falló antes de escrituras) |
| Backup de archivos/manifest | `NOT_RUN` |
| Migraciones core/sources/site_health | `NOT_RUN` |
| Transferencia de archivos | `NOT_RUN` |
| Reinicio de servicio | `NOT_RUN` |
| Instalación/enable timer Site Health | `NOT_RUN` |
| Variables Google/Clarity/GTM | `NOT_RUN` |

## Smoke tests

Los smoke tests productivos post-deploy (`/healthz`, login, leads, cotizaciones, WABA, GD Intelligence y Site Health) están `NOT_RUN` porque no hubo deployment. El preflight read-only confirmó servicio activo, host correcto y acceso SSH. Las pruebas equivalentes de GD Intelligence pasaron en Mac/PostgreSQL aislada con auth real y cuatro sitios.

## Migraciones candidatas

- `2026_08_08_gd_intelligence_core.sql`
- `2026_08_08_web_intelligence_sources.sql`
- `2026_08_08_site_health.sql`
- `2026_08_08_integration_inventory.sql`
- `2026_08_09_tracking_attribution.sql`
- `2026_08_09_paid_media_help.sql`

Todas permanecen `NOT_RUN` en producción hasta que los tres gates críticos estén en PASS y exista un SHA candidato inmutable.

## Errores y decisión

No ocurrió error durante una mutación productiva: el bloqueo fue preventivo. Desplegar sobre 246 cambios no reconciliados impediría demostrar exactamente qué se conserva o revierte. La ausencia de un remoto privado y la rotación pendiente también contradicen los gates maestros.

## Rollback status

`NOT_NEEDED`; producción no cambió. El SHA de referencia permanece `ec43b363…`. Antes del siguiente intento se debe reconciliar/congelar el worktree remoto, publicar el baseline en repositorio privado, confirmar rotación de credenciales, crear backup nuevo con checksum y repetir todos los gates.
