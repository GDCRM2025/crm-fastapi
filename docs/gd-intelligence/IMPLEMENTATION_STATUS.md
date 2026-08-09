# Estado de implementación

Actualizado: 2026-08-09

| Fase / módulo | Código | Tests | Integración | Producción | Notas |
|---|---|---|---|---|---|
| 0 Auditoría | DONE | PASS | N/A | N/A | VM/DB/backup verificados; consola Proxmox pendiente |
| 1 Arquitectura base | DONE | PASS | DONE | NOT_DEPLOYED | Módulo y API base |
| 2 RBAC y views | DONE | PASS | DONE | NOT_DEPLOYED | Menú y matriz Allow/Deny/Inherit respaldados por API y auditoría |
| 3 Sitios | DONE | PASS | DONE_LOCAL | NOT_DEPLOYED | CAM/EXP/GOU/DEL; matriz pública por sitio, configuración sin secretos y verificación live |
| 4 GA4 | READY_FOR_CREDENTIAL | PASS | DISCOVERED_LOCAL | NOT_DEPLOYED | Measurement IDs 4/4; selector Property ID mediante credencial backend; credencial externa pendiente |
| 5 Search Console | READY_FOR_CREDENTIAL | PASS | PARTIAL_LOCAL | NOT_DEPLOYED | Selector de propiedades backend listo; acceso externo pendiente |
| 6 Dashboard web | DONE | PASS | DONE_LOCAL | NOT_DEPLOYED | Nueva sección lateral Resumen/Sitios/Site Health/UTM/Permisos; cuatro sitios reales del restore aislado |
| 7 Tracking first-party | DONE_LOCAL | PASS | DONE_LOCAL | NOT_DEPLOYED | visitor/session persistentes, UTM/referrer/landing y click_whatsapp |
| 8 Atribución | DONE_LOCAL | PASS | DONE_LOCAL | NOT_DEPLOYED | sesión→lead automática, first/last touch, método/confidence y RBAC |
| 9 PageSpeed | PARTIAL | PASS | ORIGINS_DISCOVERED | NOT_DEPLOYED | Parser lab/field y orígenes listos; API key pendiente |
| 10 CrUX | PARTIAL | PASS | ORIGINS_DISCOVERED | NOT_DEPLOYED | Orígenes listos; disponibilidad/API pendiente, datos ausentes no se fabrican |
| 11 Site Health | DONE | PASS | DONE_LOCAL | NOT_DEPLOYED | Scanner seguro, historial, UI, ejecución manual y timer systemd preparado |
| 12 SEO Opportunity Engine | PENDING | PENDING | PENDING | N/A | — |
| 13 UTM Builder | DONE_LOCAL | PASS | DONE_LOCAL | NOT_DEPLOYED | URLs ligadas a campañas; Visits/Leads/Quotes/Conversion/Revenue |
| 14 Marketing campaigns | DONE_LOCAL | PASS | DONE_LOCAL | NOT_DEPLOYED | `wi_marketing_campaigns` y campaña opcional en lead manual |
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
| Reconciliación Mac/Git/servidor | FAIL | Ubuntu conserva 54 cambios tracked y 192 untracked; no se desplegará sobre ese worktree |
| Clean baseline local | PASS | Commit raíz `721a6e7`; sin remoto |
| Repositorio privado | FAIL | GitHub informa PUBLIC; acción manual crítica |
| Secret scan baseline | PASS | Gitleaks directory/history: 0 hallazgos |
| Rotación secretos legacy | FAIL | Secretos reales históricos; revocación/rotación pendiente |
| Restore aislado | PASS | 112 tablas, 213 índices, 34 FKs |
| Migraciones en restore | PASS | Dos ejecuciones, core intacto |
| Migración producción | NOT_RUN | Prohibida hasta readiness |

## Evidencia actual

- 43 pruebas unitarias/contrato GD Intelligence: PASS.
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
- Integration Center muestra detectado, faltante y acción por integración; Google/PageSpeed reportan sólo disponibilidad backend.
- Creación manual usa orígenes comerciales y campaña opcional; no expone campos UTM al Ejecutivo.
- WABA dispone de reglas explícitas de confidence y no atribuye sin evidencia suficiente.
- Migración productiva: no ejecutada; Git/secret/rollback/readiness pendientes.
- Preflight Ubuntu read-only 2026-08-09: servicio activo y rollback SHA `ec43b36`; deployment bloqueado antes de escrituras por 54 entradas tracked + 192 untracked y gates Git/rotación pendientes.
