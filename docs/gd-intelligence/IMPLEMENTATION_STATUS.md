# Estado de implementación

Actualizado: 2026-08-13

SHA productivo inmutable: `414fc9a57339109178faa7f1e4dc84b1aa89a3b6`.

| Fase / módulo | Código | Tests | Integración | Producción | Notas |
|---|---|---|---|---|---|
| 0 Auditoría | DONE | PASS | N/A | N/A | VM/DB/backup verificados; consola Proxmox pendiente |
| 1 Arquitectura base | DONE | PASS | DONE | DEPLOYED | Módulo y API base |
| 2 RBAC y views | DONE | PASS | DONE | DEPLOYED | Menú y matriz Allow/Deny/Inherit respaldados por API y auditoría |
| 3 Sitios | DONE | PASS | DONE_LOCAL | DEPLOYED | CAM/EXP/GOU/DEL; matriz pública por sitio, configuración sin secretos y verificación live |
| 4 GA4 | READY_FOR_CREDENTIAL | PASS | DISCOVERED_LOCAL | DEPLOYED | Measurement IDs 4/4; selector Property ID mediante credencial backend; credencial externa pendiente |
| 5 Search Console | READY_FOR_CREDENTIAL | PASS | PARTIAL_LOCAL | DEPLOYED | Selector de propiedades backend listo; acceso externo pendiente |
| 6 Dashboard web | DONE | PASS | DONE_LOCAL | DEPLOYED | Nueva sección lateral Resumen/Sitios/Site Health/UTM/Permisos; cuatro sitios reales del restore aislado |
| 7 Tracking first-party | EDGE_DEPLOYED | PASS | CANARY_NO_DATA | CAM_GTM_PUBLISHED | Borde HTTPS, cola durable, HMAC, worker saliente y cron operativos. CAM GTM v20 publicado; primer navegador dejó 0 eventos, por lo que el rollout EXP/GOU/DEL se detuvo correctamente. |
| 8 Atribución | DONE_LOCAL | PASS | DONE_LOCAL | DEPLOYED | sesión→lead automática, first/last touch, método/confidence y RBAC |
| 9 PageSpeed | PARTIAL | PASS | ORIGINS_DISCOVERED | DEPLOYED | Parser lab/field y orígenes listos; API key pendiente |
| 10 CrUX | PARTIAL | PASS | ORIGINS_DISCOVERED | DEPLOYED | Orígenes listos; disponibilidad/API pendiente, datos ausentes no se fabrican |
| 11 Site Health | DONE | PASS | DONE_PROD | DEPLOYED | Scanner, historial y UI; ciclo productivo 4/4 HTTP 200 y cron cada 15 minutos |
| 12 SEO Opportunity Engine | PENDING | PENDING | PENDING | N/A | — |
| 13 UTM Builder | DONE_LOCAL | PASS | DONE_LOCAL | DEPLOYED | URLs ligadas a campañas; Visits/Leads/Quotes/Conversion/Revenue |
| 14 Marketing campaigns | DONE_LOCAL | PASS | DONE_LOCAL | DEPLOYED | `wi_marketing_campaigns` y campaña opcional en lead manual |
| 15 GD AI base | PENDING | PENDING | NOT_CONFIGURED | N/A | API key local existe; uso no validado |
| 16 AI por rol | PENDING | PENDING | PENDING | N/A | — |
| 17 WABA AI assistant | PENDING | PENDING | PENDING | N/A | Reutilizar WABA actual |
| 18 Change Requests | PENDING | PENDING | PENDING | N/A | — |
| 19 Git deployment | DEPLOYED | PASS | IMMUTABLE_RELEASE | DEPLOYED | `/opt/greendiamond/current` apunta a `414fc9a`; rollback inmediato `cb72fb9` y releases previos preservados |
| 20 Rollback | PENDING | PENDING | PENDING | N/A | — |
| 21 Experiments | PENDING | PENDING | PENDING | N/A | — |
| 22 Change impact | PENDING | PENDING | PENDING | N/A | — |
| 23 Alerts | PENDING | PENDING | PENDING | N/A | — |
| 24 Executive dashboard | PENDING | PENDING | PENDING | N/A | — |
| 25 Paid Media data model | DONE_LOCAL | PASS | EMPTY_READY | DEPLOYED | Cuentas, entidades, métricas diarias, search terms y change log; constraints idempotentes |
| 26 Google Ads adapter | CORE_READY | PASS | READY_FOR_CREDENTIAL | DEPLOYED | Parser normalizado y gate backend; sync externo pendiente |
| 27 Meta Ads adapter | CORE_READY | PASS | READY_FOR_CREDENTIAL | DEPLOYED | Parser normalizado y gate backend; sync externo pendiente |
| 28 Paid Media UI | DONE_LOCAL | PASS | DONE_LOCAL | DEPLOYED | Modo READ/ANALYZE/RECOMMEND; plataforma y CRM separados; sin writes |
| 29 Change Risk | CORE_READY | PASS | DONE_LOCAL | DEPLOYED | RECENT_CHANGE, LEARNING, LOW_DATA y budget risk; sin recomendaciones sin evidencia |
| 30 Help Engine | DONE_LOCAL | PASS | DONE_LOCAL | DEPLOYED | Artículos persistentes, búsqueda por nivel/rol y ayuda por `screen_id` |
| 31 CRM inventory | DONE_LOCAL | PASS | GENERATED | DEPLOYED | 77 accesos de menú y 501 endpoints extraídos desde código |
| 32 Manual source package | DONE_LOCAL | PASS | GENERATED | DEPLOYED | Pantallas, acciones, permisos, procesos, cobertura, capturas y handoff |
| 33 Omnichannel Inbox | DONE_LOCAL | PASS | PARTIAL_REAL | DEPLOYED | Bandeja por referencia, navegación a Tools real y controles comerciales sin duplicar mensajes |
| 34 CredentialVault | DONE | PASS | DONE_PROD | DEPLOYED | Write-only, Fernet at-rest, bootstrap persistente 0600, replace/revoke/verify y arranque fail-closed |
| 35 Integration Center UX | DONE | PASS | VERIFIED_PROD | DEPLOYED | Estado canónico y multidimensional: instalación, credencial, API, datos y salud; QA visual productiva PASS |
| 36 Site Health UX | DONE_LOCAL | PASS | DONE_LOCAL | DEPLOYED | Hallazgos traducidos a Crítico/Importante/Mejora y last-known-good |
| 37 Google Ads real | READY_FOR_CREDENTIAL | PASS | API_READY_NO_DATA | DEPLOYED | OAuth/service credential y sync READ ONLY disponibles; credencial externa ausente |
| 38 Meta Ads real | READY_FOR_CREDENTIAL | PASS | API_READY_NO_DATA | DEPLOYED | Selector de activos y sync Graph GET disponibles; token externo ausente |
| 39 Paid Media Attribution | DONE_LOCAL | PASS | READY_NO_AD_DATA | DEPLOYED | GCLID/FBCLID hash, UTM y CRM; EXACT/STRONG/INFERRED/UNKNOWN fail-closed |
| 40 Search Terms Intelligence | DONE_LOCAL | PASS | READY_NO_AD_DATA | DEPLOYED | HIGH_VALUE/WASTE/NEGATIVE_CANDIDATE/INSUFFICIENT_DATA; cero publicaciones |
| 41 Creative Intelligence | DONE_LOCAL | PASS | READY_NO_AD_DATA | DEPLOYED | Frequency, CTR, CPA CRM, conversión, edad y umbral de volumen |
| 42 Change Risk ampliado | DONE_LOCAL | PASS | DONE_LOCAL | DEPLOYED | Siete estados; reason/confidence/risk/next_review_date/métricas |
| 43 SEO Opportunity Engine | DONE_LOCAL | PASS | PARTIAL_REAL | DEPLOYED | Score 0–100; Site Health real disponible, Search Console pendiente; no simula |
| 44 Inbox omnicanal | DONE_LOCAL | PASS | PARTIAL_REAL | DEPLOYED | Reutiliza WABA/Instagram/Messenger/Email; work items por referencia sin copiar mensajes |
| 45 BI por canal | DONE_LOCAL | PASS | REAL_LOCAL | DEPLOYED | WhatsApp y Email con datos; Instagram/Messenger sin registros; sin inferencias |
| 46 Alert Engine | DONE_LOCAL | PASS | DONE_LOCAL | DEPLOYED | Deduplicación, cooldown, evidencia, acción y próxima revisión |
| 47 Executive Dashboard | DONE_LOCAL | PASS | PARTIAL_REAL | DEPLOYED | CRM sales, tracking y Site Health habilitan el tablero; Paid Spend/CPA/ROAS muestran Sin datos hasta sincronizar Ads |
| 48 GD AI base | DONE_LOCAL | PASS | CONTEXT_READY | DEPLOYED | Context builder allowlisted por rol; read/analyze/suggest; sin SQL ni writes |
| 49 Reportes CRM | DONE | PASS | REAL_PROD | DEPLOYED | Visión General, Tipo de Cliente, Productos y series por marca reparadas; assets gráficos locales |
| 50 GD Sales Command Center | DONE | PASS | LIVE_CRM | DEPLOYED | Pipeline vivo y accionable; frontend + backend exclusivos para SUPERADMIN; sin snapshot PII |
| 51 Encuestas post-evento UX | DONE | PASS | REAL_PROD | DEPLOYED | Ventana de 7 días, envío manual explícito y enlaces inválidos fail-closed |

## Gates transversales

| Gate | Estado | Evidencia |
|---|---|---|
| Backup Git Mac | PASS | Bundle + snapshot + checksums |
| Backup código servidor | PASS | Bundle + snapshot + checksums |
| Reconciliación Mac/Git/servidor | PASS_RUNTIME | Runtime productivo reproducible desde `8870400`; árbol legacy preservado sólo para rollback |
| Clean baseline local | PASS | Commit raíz `721a6e7`; sin remoto |
| Repositorio privado | FAIL | GitHub informa PUBLIC; acción manual crítica |
| Secret scan baseline | PASS | Gitleaks directory/history: 0 hallazgos |
| Rotación secretos legacy | FAIL | Secretos reales históricos; revocación/rotación pendiente |
| Restore aislado | PASS | 112 tablas, 213 índices, 34 FKs |
| Migraciones en restore | PASS | Dos ejecuciones, core intacto |
| Migración producción | PASS | Diez migraciones aditivas ejecutadas el 2026-08-12 por autorización explícita |
| Bootstrap DB/Vault Ubuntu | PASS | Archivos externos al release 0700/0600; precedencia systemd → archivo seguro → legacy; persistencia verificada |
| Visibilidad temporal GD Intelligence | PASS | Backend global y menú limitados a SUPERADMIN; SUPERADMIN 200 / ADMIN 403 verificados en producción |

## Evidencia actual

- Urgencia Agenda/GD Sales 2026-08-13: `cb72fb9` corrigió la previsualización de montaje incompleto sin debilitar la confirmación final; en evento único oculta la asignación manual de productos, alinea pago y destaca duración. GD Sales vuelve a ser página independiente SUPERADMIN con teléfono, WhatsApp, cotización/PDF y filtros por fecha, monto, estado, marca y búsqueda. Candidato y post-cutover PASS; PDF productivo `%PDF`.
- First-party edge 2026-08-13: `collect.greendiamond.cl` usa TLS Cloudflare sobre subdominio cPanel aislado, PHP 8.3, SQLite WAL fuera del release, leases/ACK at-least-once y HMAC SHA-256 con protección de replay. `/crm`, `/admin`, `/api`, `/docs`, `/openapi.json` y `/metrics` devuelven 404; `/.env` y `/.git`, 406. La credencial dedicada existe sólo en archivos 0600 del edge y Ubuntu.
- Worker productivo `python -m backend.jobs.tracking_edge_pull`: one-shot, advisory lock, revalidación, ingest transaccional, ACK posterior y cron único cada minuto. Último pull PASS, cola 0, errores 0. Release `414fc9a`; backup `/opt/greendiamond/backups/release_20260813_112953_pre_414fc9a` con checksum y catálogo `pg_restore` PASS.
- Canary CAM: tag **GD Tracker · CAM · First-party** publicado por GTM como versión 19 y bootstrap público como versión 20, sin secretos. El contenedor publicado contiene ambos tags, pero dos navegaciones reales QA dejaron 0 requests/eventos; `CAM_EVENTS=0`. Conforme al gate, no se amplió a EXP/GOU/DEL ni se declaró `TRACKING_DATA=REAL`. Siguiente corrección: cerrar la ejecución del loader externo en GTM y repetir navegador → edge → pull → PostgreSQL.
- Suite consolidada posterior al edge: 172 PASS, 0 FAIL; Python compile, frontend build, PHP lint, JS parse, migración local/productiva idempotente y Gitleaks PASS.

- Corrección P0 2026-08-13: release `5276a4b` activo, rollback `f1e2739` listo y no ejecutado. Dashboard del día devuelve sólo el lead CAMALEON `3903`; el lead Del Sabor `3837` conserva su registro comercial del 12 de agosto y ya no aparece falsamente el día 13.
- Reportes productivos: Funnel 4, Tipo de Cliente 3, Productos 20, Venta por Marca 4 y Comparativo 7 series/filas en el smoke autenticado.
- GD Sales usa exclusivamente datos vivos del CRM: SUPERADMIN 200 y ADMIN 403. Encuestas, Leads, Cotizaciones y WABA permanecen 200.
- Suite consolidada: 162 PASS, 0 FAIL; compile, JS parse, Gitleaks, candidato y post-cutover PASS.

- 142 pruebas unitarias/contrato CRM, GD Intelligence, almacenamiento persistente, Omnichannel, WABA contextual, collector, bootstrap y reconciliación: PASS; 0 fallas.
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
- Inventario público corregido: GTM y GA4 detectados; `gd-tracker.js` y `data-site-code` ausentes 4/4; Clarity 0/4. El falso positivo de GD Tracker se eliminó y no se consultaron ni persistieron secretos.
- Integration Center muestra detectado, faltante, última verificación/sync, last-known-good y acción; PageSpeed puede configurarse desde CRM sin terminal.
- Creación manual usa orígenes comerciales y campaña opcional; no expone campos UTM al Ejecutivo.
- WABA dispone de reglas explícitas de confidence y no atribuye sin evidencia suficiente.
- Paridad visual P0 permanece operativa, pero la verdad de discovery de GD Tracker requiere el release P1: el estado correcto de los cuatro sitios es `NOT_CONFIGURED`, no `NO_DATA`. El collector está listo en baseline; `TRACKING_DATA=BLOCKED_NETWORK` hasta publicar un borde exclusivo y completar navegador → evento → PostgreSQL productivo.
- WABA contextual P1: acciones comerciales reales por clic derecho/pulsación larga, sin auto-send y sin endpoints Meta ficticios; liberación productiva agrupada con la corrección de discovery.
- Cutover P1 `4cd4321`: PASS. Backup PostgreSQL + código con checksum y catálogo validado; migración collector aplicada dos veces; candidato y post-cutover PASS; rollback `ba89090` listo y no ejecutado. QA visual sobre una conversación real confirmó el menú contextual completo, incluido “Eliminar” deshabilitado.
- Hotfix Agenda `b14d547`: PASS. Se eliminó el lock global de Calendar en favor de locks por lead, se acotó Google HTTP a 20 segundos y se impidió terminar operaciones activas a los 90 segundos. 146 tests PASS; rollback `4cd4321` listo y no ejecutado.
- Montaje operativo `aa9d24a`: PASS. Evento exclusivo de Calendar con fecha/hora independiente, mismos equipos del evento comercial y claves idempotentes separadas. No crea evento CRM/financiero ni reemplaza el ancla comercial del lead. 149 tests PASS; rollback `b14d547` listo y no ejecutado.
- Cierre funcional Agenda 2026-08-13: PASS_PROD. El paso 1 usa una decisión explícita No/Sí; el backend ya no ignora montajes incompletos; preview simple y multi-día validan eventos `MOUNTING` separados. La baja de Confirmado es fail-closed y elimina todos los hijos Calendar por IDs + `lead_id`. Edición confirmada bloquea campos comerciales inmutables y sincroniza teléfono/comuna/dirección/tipo; atribución SEM se agrega sólo con evidencia real. 152 tests PASS, 0 FAIL. Release productivo `f1e2739`; candidato y post-cutover con health/login/Leads/Agenda/Cotizador/WABA 200; smoke Google real dejó 0 eventos QA activos; rollback `aa9d24a` listo y no ejecutado.
- Smoke productivo P1: health, login, Leads, Cotizador, WABA, GD Intelligence, Site Health y métricas HTTP 200; collector interno preflight 204; ADMIN 403; 0 Router SKIP y 0 tracebacks. Candidato 9090 detenido y sesión QA temporal eliminada.
- Cutover inmutable `babfea8`: PASS; candidato aislado, migración idempotente, QA visual, Vault persistente, todos los routers cargados y smoke tests ampliados PASS. Rollback `8600cc2` listo y no ejecutado. Git privado y rotación de credenciales históricas siguen pendientes.
- Hotfix crítico PDF `ba89090`: PASS. El generador de cotizaciones usa `/opt/greendiamond/shared/persistent-data/data/quotes`, no escribe dentro del release inmutable. PDF real verificado HTTP 200, `application/pdf`, `%PDF`, descarga attachment y archivo persistente; rollback `babfea8` listo y no ejecutado.
- Preflight Ubuntu read-only 2026-08-09: servicio activo y rollback SHA `ec43b36`; deployment bloqueado antes de escrituras por 54 entradas tracked + 192 untracked y gates Git/rotación pendientes.
