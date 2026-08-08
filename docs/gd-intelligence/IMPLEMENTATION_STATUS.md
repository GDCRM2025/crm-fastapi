# Estado de implementación

Actualizado: 2026-08-08

| Fase / módulo | Código | Tests | Integración | Producción | Notas |
|---|---|---|---|---|---|
| 0 Auditoría | DONE | PASS | N/A | N/A | VM/DB/backup verificados; consola Proxmox pendiente |
| 1 Arquitectura base | DONE | PASS | DONE | NOT_DEPLOYED | Módulo y API base |
| 2 RBAC y views | PARTIAL | PASS | PARTIAL | NOT_DEPLOYED | Menú GD usa permisos backend por rol; administración explícita de grants aún pendiente |
| 3 Sitios | DONE | PASS | NOT_CONFIGURED | NOT_DEPLOYED | CAM/EXP/GOU/DEL seed; UI de alta/estado incluida |
| 4 GA4 | PARTIAL | PASS | NOT_CONFIGURED | N/A | Parser y snapshot local; cliente/sync pendiente |
| 5 Search Console | PARTIAL | PASS | NOT_CONFIGURED | N/A | Parser y snapshot local; cliente/sync pendiente |
| 6 Dashboard web | DONE | PASS | PARTIAL | NOT_DEPLOYED | Nueva sección lateral Resumen/Sitios/UTM; error de backend amigable; fuentes externas aún deshabilitadas |
| 7 Tracking first-party | PENDING | PENDING | PENDING | N/A | — |
| 8 Atribución | PENDING | PENDING | PENDING | N/A | — |
| 9 PageSpeed | PARTIAL | PASS | NOT_CONFIGURED | N/A | Parser lab/field y almacenamiento listos |
| 10 CrUX | PARTIAL | PASS | NOT_CONFIGURED | N/A | Datos ausentes no se fabrican |
| 11 Site Health | PENDING | PENDING | PENDING | N/A | — |
| 12 SEO Opportunity Engine | PENDING | PENDING | PENDING | N/A | — |
| 13 UTM Builder | DONE | PASS | DONE | NOT_DEPLOYED | API + UI generan, identifican, guardan y listan URLs |
| 14 Marketing campaigns | PENDING | PENDING | PENDING | N/A | — |
| 15 GD AI base | PENDING | PENDING | NOT_CONFIGURED | N/A | API key local existe; uso no validado |
| 16 AI por rol | PENDING | PENDING | PENDING | N/A | — |
| 17 WABA AI assistant | PENDING | PENDING | PENDING | N/A | Reutilizar WABA actual |
| 18 Change Requests | PENDING | PENDING | PENDING | N/A | — |
| 19 Git deployment | PENDING | PENDING | PENDING | N/A | — |
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

- 17 pruebas unitarias/contrato GD Intelligence: PASS.
- QA visual local: tabs Resumen/Sitios/UTM renderizan y el error de API sin backend se presenta de forma controlada.
- Compilación Python del módulo y router: PASS.
- `git diff --check` sobre archivos GD Intelligence: PASS.
- Conectividad VM y PostgreSQL: PASS mediante VPN.
- CRM `/healthz`: HTTP 200; PostgreSQL sin locks ni transacciones largas.
- Backup 20260808: checksums y `pg_restore --list` PASS.
- Restore aislado 20260808: PASS.
- Migraciones GD Intelligence ejecutadas dos veces en restore: PASS; core intacto.
- Migración productiva: no ejecutada; Git/secret/rollback/readiness pendientes.
