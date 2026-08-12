# Estado de implementación

Actualizado: 2026-08-12

SHA funcional local validado: `8c9697a` (inteligencia con fuentes reales, Ads read-only, Omnichannel y hotfixes reconciliados; sin deployment).

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
| 19 Git deployment | PREPARED | PASS_LOCAL | BLOCKED_GATE | NOT_DEPLOYED | Ubuntu: 54 tracked preservados, 5.965 untracked y 6.284 ignored aún no clasificados; repo privado/rotación pendientes |
| 20 Rollback | PENDING | PENDING | PENDING | N/A | — |
| 21 Experiments | PENDING | PENDING | PENDING | N/A | — |
| 22 Change impact | PENDING | PENDING | PENDING | N/A | — |
| 23 Alerts | PENDING | PENDING | PENDING | N/A | — |
| 24 Executive dashboard | PENDING | PENDING | PENDING | N/A | — |
| 25 Paid Media data model | DONE_LOCAL | PASS | EMPTY_READY | NOT_DEPLOYED | Cuentas, entidades, métricas diarias, search terms y change log; constraints idempotentes |
| 26 Google Ads adapter | CORE_READY | PASS | READY_FOR_CREDENTIAL | NOT_DEPLOYED | Parser normalizado y gate backend; sync externo pendiente |
| 27 Meta Ads adapter | CORE_READY | PASS | READY_FOR_CREDENTIAL | NOT_DEPLOYED | Parser normalizado y gate backend; sync externo pendiente |
| 28 Paid Media UI | DONE_LOCAL | PASS | DONE_LOCAL | NOT_DEPLOYED | Modo READ/ANALYZE/RECOMMEND; plataforma y CRM separados; sin writes |
| 29 Change Risk | CORE_READY | PASS | DONE_LOCAL | NOT_DEPLOYED | RECENT_CHANGE, LEARNING, LOW_DATA y budget risk; sin recomendaciones sin evidencia |
| 30 Help Engine | DONE_LOCAL | PASS | DONE_LOCAL | NOT_DEPLOYED | Artículos persistentes, búsqueda por nivel/rol y ayuda por `screen_id` |
| 31 CRM inventory | DONE_LOCAL | PASS | GENERATED | NOT_DEPLOYED | 77 accesos de menú y 501 endpoints extraídos desde código |
| 32 Manual source package | DONE_LOCAL | PASS | GENERATED | NOT_DEPLOYED | Pantallas, acciones, permisos, procesos, cobertura, capturas y handoff |
| 33 Omnichannel Inbox | DONE_LOCAL | PASS | PARTIAL_REAL | NOT_DEPLOYED | Bandeja por referencia, navegación a Tools real y controles comerciales sin duplicar mensajes |
| 34 CredentialVault | DONE_LOCAL | PASS | DONE_LOCAL | NOT_DEPLOYED | Write-only, Fernet at-rest, replace/revoke/verify, key local 0600 y eventos sanitizados |
| 35 Integration Center UX | DONE_LOCAL | PASS | DONE_LOCAL | NOT_DEPLOYED | Navegación lateral, filtros, tarjetas legibles, estados humanizados, wizards y confirmaciones |
| 36 Site Health UX | DONE_LOCAL | PASS | DONE_LOCAL | NOT_DEPLOYED | Hallazgos traducidos a Crítico/Importante/Mejora y last-known-good |
| 37 Google Ads real | READY_FOR_CREDENTIAL | PASS | API_READY_NO_DATA | NOT_DEPLOYED | OAuth refresh/service credential, selección Customer ID/sitio y sync REST READ ONLY; credencial externa ausente; API v25 vigente |
| 38 Meta Ads real | READY_FOR_CREDENTIAL | PASS | API_READY_NO_DATA | NOT_DEPLOYED | Business/Ad Account/Page/Instagram selector, paginación por cursor y sync Graph GET; token externo ausente |
| 39 Paid Media Attribution | DONE_LOCAL | PASS | READY_NO_AD_DATA | NOT_DEPLOYED | GCLID/FBCLID hash, UTM y CRM; EXACT/STRONG/INFERRED/UNKNOWN fail-closed |
| 40 Search Terms Intelligence | DONE_LOCAL | PASS | READY_NO_AD_DATA | NOT_DEPLOYED | HIGH_VALUE/WASTE/NEGATIVE_CANDIDATE/INSUFFICIENT_DATA; cero publicaciones |
| 41 Creative Intelligence | DONE_LOCAL | PASS | READY_NO_AD_DATA | NOT_DEPLOYED | Frequency, CTR, CPA CRM, conversión, edad y umbral de volumen |
| 42 Change Risk ampliado | DONE_LOCAL | PASS | DONE_LOCAL | NOT_DEPLOYED | Siete estados; reason/confidence/risk/next_review_date/métricas |
| 43 SEO Opportunity Engine | DONE_LOCAL | PASS | PARTIAL_REAL | NOT_DEPLOYED | Score 0–100; Site Health real disponible, Search Console pendiente; no simula |
| 44 Inbox omnicanal | DONE_LOCAL | PASS | PARTIAL_REAL | NOT_DEPLOYED | Reutiliza WABA/Instagram/Messenger/Email; work items por referencia sin copiar mensajes |
| 45 BI por canal | DONE_LOCAL | PASS | REAL_LOCAL | NOT_DEPLOYED | WhatsApp y Email con datos; Instagram/Messenger sin registros; sin inferencias |
| 46 Alert Engine | DONE_LOCAL | PASS | DONE_LOCAL | NOT_DEPLOYED | Deduplicación, cooldown, evidencia, acción y próxima revisión |
| 47 Executive Dashboard | DONE_LOCAL | PASS | PARTIAL_REAL | NOT_DEPLOYED | CRM sales, tracking y Site Health habilitan el tablero; Paid Spend/CPA/ROAS muestran Sin datos hasta sincronizar Ads |
| 48 GD AI base | DONE_LOCAL | PASS | CONTEXT_READY | NOT_DEPLOYED | Context builder allowlisted por rol; read/analyze/suggest; sin SQL ni writes |

## Gates transversales

| Gate | Estado | Evidencia |
|---|---|---|
| Backup Git Mac | PASS | Bundle + snapshot + checksums |
| Backup código servidor | PASS | Bundle + snapshot + checksums |
| Reconciliación Mac/Git/servidor | FAIL | 54/54 tracked preservados; 6.299 untracked, 6.302 ignored y 14 snapshots recientes requieren clasificación |
| Clean baseline local | PASS | Commit raíz `721a6e7`; sin remoto |
| Repositorio privado | FAIL | GitHub informa PUBLIC; acción manual crítica |
| Secret scan baseline | PASS | Gitleaks directory/history: 0 hallazgos |
| Rotación secretos legacy | FAIL | Secretos reales históricos; revocación/rotación pendiente |
| Restore aislado | PASS | 112 tablas, 213 índices, 34 FKs |
| Migraciones en restore | PASS | Dos ejecuciones, core intacto |
| Migración producción | NOT_RUN | Prohibida hasta readiness |

## Evidencia actual

- 100 pruebas unitarias/contrato GD Intelligence, Omnichannel y reconciliación: PASS.
- OpenAPI local incorpora 16 rutas nuevas de inteligencia real y Omnichannel; `/healthz` PASS.
- Google Ads y Meta Ads no se marcan conectados: los conectores, selectores y sync están listos, pero las credenciales externas autorizadas no existen en el entorno. Meta pagina por cursor sin seguir URLs absolutas que puedan contener token, sanea activos anidados e ingiere `action_values`.
- Inbox local usa datos reales: WhatsApp 1 conversación/1 lead; Email 199 registros consultados/8 leads/31 cotizaciones; Instagram y Messenger 0 registros. No se inventaron ventas ni revenue.
- CredentialVault no expone endpoint GET ni método frontend; ciphertext y master key permanecen fuera de respuestas, logs y auditoría.
- Migración CredentialVault ejecutada dos veces en PostgreSQL aislada: PASS; no importa credenciales legacy.
- Formulario de credencial siempre vacío, tipo password, confirma valor, prueba proveedor antes de persistir y permite reemplazar/revocar.
- RBAC backend: configurar/verificar requiere `web_intelligence_configure`; desconectar requiere `system_integrations_manage`.
- Integration Center profesional: filtros sitio/proveedor/estado/búsqueda, 2–3 columnas, progreso, estados en español y acciones concretas.
- Migración Paid Media + Help aplicada dos veces en PostgreSQL aislada: PASS; 8 artículos y 6 contextos seed; cero cuentas publicitarias inventadas.
- Paid Media local está visible como sección de GD Intelligence y Settings incluye acceso explícito a Integraciones.
- Motor de ayuda conserva fallback estático y añade búsqueda autenticada por nivel/rol y contexto de pantalla.
- Catálogo reproducible generado desde navegación y routers: 77 accesos de menú, 501 endpoints y 0 archivos faltantes. Las cinco pantallas nuevas tienen ayuda contextual; permanecen 66 lagunas legacy explícitas.
- La Inbox abre WhatsApp, Email e Instagram en las rutas reales de Tools con IDs aceptados por el allowlist del panel; pruebas de regresión cubren rutas y `screen_id` contextual.
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
- Integration Center muestra detectado, faltante, última verificación/sync, last-known-good y acción; PageSpeed puede configurarse desde CRM sin terminal.
- Creación manual usa orígenes comerciales y campaña opcional; no expone campos UTM al Ejecutivo.
- WABA dispone de reglas explícitas de confidence y no atribuye sin evidencia suficiente.
- Migración productiva: no ejecutada; Git/secret/rollback/readiness pendientes.
- Preflight Ubuntu read-only 2026-08-09: servicio activo y rollback SHA `ec43b36`; deployment bloqueado antes de escrituras por 54 entradas tracked + 192 untracked y gates Git/rotación pendientes.
