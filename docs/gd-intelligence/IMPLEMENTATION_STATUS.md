# Estado de implementación

Actualizado: 2026-08-08

| Fase / módulo | Código | Tests | Integración | Producción | Notas |
|---|---|---|---|---|---|
| 0 Auditoría | DONE | PASS | N/A | N/A | VM/DB/backup verificados; consola Proxmox pendiente |
| 1 Arquitectura base | DONE | PASS | DONE | NOT_DEPLOYED | Módulo y API base |
| 2 RBAC y views | DONE | PASS | DONE | NOT_DEPLOYED | Menú y matriz Allow/Deny/Inherit respaldados por API y auditoría |
| 3 Sitios | DONE | PASS | DONE_LOCAL | NOT_DEPLOYED | CAM/EXP/GOU/DEL; matriz pública por sitio, configuración sin secretos y verificación live |
| 4 GA4 | PARTIAL | PASS | DISCOVERED_LOCAL | NOT_DEPLOYED | Measurement IDs detectados vía GTM en 4/4; Property IDs y OAuth pendientes |
| 5 Search Console | PARTIAL | PASS | PARTIAL_LOCAL | NOT_DEPLOYED | Verificación pública CAM; propiedades/API pendientes |
| 6 Dashboard web | DONE | PASS | DONE_LOCAL | NOT_DEPLOYED | Nueva sección lateral Resumen/Sitios/Site Health/UTM/Permisos; cuatro sitios reales del restore aislado |
| 7 Tracking first-party | PENDING | PENDING | PENDING | N/A | — |
| 8 Atribución | PENDING | PENDING | PENDING | N/A | — |
| 9 PageSpeed | PARTIAL | PASS | ORIGINS_DISCOVERED | NOT_DEPLOYED | Parser lab/field y orígenes listos; API key pendiente |
| 10 CrUX | PARTIAL | PASS | ORIGINS_DISCOVERED | NOT_DEPLOYED | Orígenes listos; disponibilidad/API pendiente, datos ausentes no se fabrican |
| 11 Site Health | DONE | PASS | DONE_LOCAL | NOT_DEPLOYED | Scanner seguro, historial, UI, ejecución manual y timer systemd preparado |
| 12 SEO Opportunity Engine | PENDING | PENDING | PENDING | N/A | — |
| 13 UTM Builder | DONE | PASS | DONE | NOT_DEPLOYED | API + UI generan, identifican, guardan y listan URLs |
| 14 Marketing campaigns | PENDING | PENDING | PENDING | N/A | — |
| 15 GD AI base | PENDING | PENDING | NOT_CONFIGURED | N/A | API key local existe; uso no validado |
| 16 AI por rol | PENDING | PENDING | PENDING | N/A | — |
| 17 WABA AI assistant | PENDING | PENDING | PENDING | N/A | Reutilizar WABA actual |
| 18 Change Requests | PENDING | PENDING | PENDING | N/A | — |
| 19 Git deployment | PREPARED | PASS_LOCAL | BLOCKED_GATE | NOT_DEPLOYED | Ubuntu: 54 tracked + 192 untracked; repo privado/rotación pendientes |
| 20 Rollback | PENDING | PENDING | PENDING | N/A | — |
| 21 Experiments | PENDING | PENDING | PENDING | N/A | — |
| 22 Change impact | PENDING | PENDING | PENDING | N/A | — |
| 23 Alerts | PENDING | PENDING | PENDING | N/A | — |
| 24 Executive dashboard | PENDING | PENDING | PENDING | N/A | — |

## Gates transversales

| Gate | Estado | Evidencia |
|---|---|---|
| Backup Git Mac | PASS | Bundle + snapshot + checksums |
| Backup código servidor | PASS | Bundle + snapshot + checksums |
| Reconciliación Mac/Git/servidor | PASS | Divergencias críticas resueltas en baseline limpio |
| Clean baseline local | PASS | Commit raíz `721a6e7`; sin remoto |
| Repositorio privado | FAIL | GitHub informa PUBLIC; acción manual crítica |
| Secret scan baseline | PASS | Gitleaks directory/history: 0 hallazgos |
| Rotación secretos legacy | FAIL | Secretos reales históricos; revocación/rotación pendiente |
| Restore aislado | PASS | 112 tablas, 213 índices, 34 FKs |
| Migraciones en restore | PASS | Dos ejecuciones, core intacto |
| Migración producción | NOT_RUN | Prohibida hasta readiness |

## Evidencia actual

- 32 pruebas unitarias/contrato GD Intelligence: PASS.
- `LOCAL_API_CONNECTIVITY=PASS`: login, auth, overview, sitios, UTM, RBAC y Site Health responden por FastAPI contra PostgreSQL aislada.
- QA visual autenticada: el menú **📡 GD Intelligence** y las vistas Resumen/Sitios/Site Health/UTM/Permisos cargan por HTTP; cuatro sitios visibles.
- Compilación Python del módulo y router: PASS.
- `git diff --check` sobre archivos GD Intelligence: PASS.
- Conectividad VM y PostgreSQL: PASS mediante VPN.
- CRM `/healthz`: HTTP 200; PostgreSQL sin locks ni transacciones largas.
- Backup 20260808: checksums y `pg_restore --list` PASS.
- Restore aislado 20260808: PASS.
- Migraciones GD Intelligence ejecutadas dos veces en restore: PASS; core intacto.
- Migración Site Health ejecutada dos veces en restore: PASS; cuatro resultados almacenados por ejecución.
- Arranque Mac desde cero: PASS; navegador cerrado, PostgreSQL/FastAPI detenidos, arranque por `MAC_SETUP.md`, sesión limpia, login y cuatro sitios visibles.
- Inventario público: GTM, GA4 y GD Tracker detectados en 4/4; Clarity 0/4; secretos no consultados ni persistidos.
- Migración productiva: no ejecutada; Git/secret/rollback/readiness pendientes.
- Preflight Ubuntu 2026-08-08: servicio activo, PostgreSQL 16.14 y rollback SHA `ec43b36`; deployment bloqueado antes de escrituras por worktree remoto con 246 entradas y gates Git/rotación pendientes.
