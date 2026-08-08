# Auditoría técnica previa — GD Intelligence

Fecha: 2026-08-08
Repositorio: `GDCRM2025/crm-fastapi`
Worktree auditado: `/Users/oscarmendoza/Desktop/CRM 2025`

## 1. Veredicto ejecutivo

GD Intelligence debe integrarse al monolito FastAPI existente y a su frontend HTML/JavaScript actual. No hay fundamento para crear una aplicación paralela ni reemplazar autenticación, SQLAlchemy, PostgreSQL o el sistema visual existente.

La estrategia segura es aditiva: módulos Python bajo `backend/gd_intelligence`, un router con prefijo `/api/gd-intelligence`, vistas integradas bajo `web/views`, assets bajo `web/js`, y objetos PostgreSQL aislados mediante los prefijos `wi_`, `ai_` y `waba_`. El código actual usa casi exclusivamente `public` y crea múltiples tablas con SQL explícito; introducir schemas PostgreSQL separados ahora elevaría el riesgo y sería inconsistente con el patrón operativo.

No se ejecutó ninguna migración ni escritura en base de datos durante esta auditoría. El primer intento se hizo sin VPN y no alcanzó las IP privadas. Con la VPN activa se confirmó que la VM vigente es `192.168.100.51`; `192.168.10.51` es una referencia obsoleta. Las comprobaciones posteriores de Ubuntu, CRM, PostgreSQL y backups fueron read-only.

## 2. Estado del repositorio y Git

- Rama al iniciar: `codex/whatsapp-marcas-profesional-20260806`.
- Commit inicial: `bf81b6d6` (`fix(whatsapp): route embedded signup through local tunnel`).
- Rama principal remota: `origin/main`; rama principal local: `main`.
- Relación con `main`: 378 commits por delante y 30 por detrás.
- Worktrees detectados: uno, el directorio auditado.
- Remoto: GitHub, repositorio `GDCRM2025/crm-fastapi`.
- El worktree estaba muy modificado antes de GD Intelligence, con cambios staged, unstaged y untracked en backend, frontend, WABA, RBAC, despliegue, documentación y scripts.

### Regla de preservación

Los cambios preexistentes pertenecen al trabajo anterior y no se deben revertir, limpiar ni mezclar accidentalmente. Los commits de GD Intelligence deben usar listas explícitas de archivos. No se autoriza `git reset --hard`, despliegue total con borrado ni staging global.

## 3. Arquitectura encontrada

### Backend

- Python y FastAPI; aplicación principal en `backend/main.py`.
- Inclusión tolerante a fallos mediante `include_router_safe`.
- SQLAlchemy síncrono; engine y sesiones en `backend/core/database.py`.
- PostgreSQL obligatorio en el camino principal mediante `DATABASE_URL`.
- Pydantic Settings y `python-dotenv` para configuración.
- Routers funcionales en `backend/routers`; existen módulos alternativos/heredados bajo `backend/modules`.
- Logging rotativo inicializado por `backend/core/logging_setup.py`.
- No existe un worker general dedicado ni Celery. Hay endpoints cron, `BackgroundTasks`, polling controlado y tareas puntuales con threads.
- Migraciones mixtas: Alembic existe, pero una parte considerable del esquema se asegura con SQL idempotente dentro de módulos y scripts de release.

### Frontend

- La interfaz operativa principal es HTML/CSS/JavaScript estático bajo `web/` y `web/views/`.
- El CRM sirve `web/` desde FastAPI y Nginx mantiene compatibilidad para rutas absolutas.
- Existe un proyecto React/Vite/Tailwind bajo `frontend/`, pero no es la única ni la principal superficie del CRM actual.
- ECharts 5.5 y ExcelJS ya están disponibles localmente; deben reutilizarse para dashboards y exportaciones.
- Existe app móvil Expo/React Native y extensiones Chrome, fuera del alcance inicial de GD Intelligence.

### API y rutas

`backend/main.py` integra routers de autenticación, leads, cotizaciones, agenda, permisos, features, cron, finanzas, inventario, RRHH, chat, marketing, calendario, backups, actividad, CRM 360, tareas, encuestas y WABA. La auditoría anterior registró 370 paths en InMotion y otra revisión local registró 456 rutas; se debe generar un inventario nuevo cuando arranque el entorno consolidado.

## 4. Autenticación, RBAC y auditoría

- Autenticación JWT HS256; `JWT_SECRET` tiene preferencia y `SECRET_KEY` mantiene fallback de desarrollo.
- Dependencias de usuario/autorización distribuidas entre `backend/core/auth*.py` y `backend/routers/auth.py`.
- Catálogo histórico de roles fijo en `backend/roles_catalog.py`.
- El RBAC nuevo usa permisos de menú `none/read/full` por usuario y por rol en `user_menu_permissions` y `role_menu_permissions`.
- Admin y Super Admin reciben acceso completo en el helper actual; roles operativos excluidos se deniegan.
- Ya existe `activity_log` y helpers de auditoría. Toda aprobación, acción IA, configuración, sincronización, deployment y rollback de GD Intelligence debe reutilizar esta capa o una capa aditiva compatible.

### Decisión RBAC para GD Intelligence

Extender el sistema existente con permisos funcionales granulares, sin confiar sólo en el nombre del rol. Los permisos de menú controlarán visibilidad y una tabla aditiva de permisos funcionales controlará acciones como aprobar, desplegar, rollback, administrar integraciones y usar IA. El backend debe validar siempre; ocultar UI no es autorización.

## 5. Entidades empresariales existentes

Evidencia del dump local y del código:

- `leads`, estados/notas de lead y agenda/seguimientos.
- `cotizaciones`, detalle e items de cotización.
- `marcas`, `usuarios`, `roles`, comunas, productos y tipos de cliente.
- actividad, tareas, notificaciones, eventos, encuestas, finanzas e inventario.
- WABA: canales, contactos, conversaciones, mensajes, reacciones, relación conversación-lead y eventos webhook.

No se identificó una entidad canónica única y estable para ventas/facturación en el dump de referencia; el código contiene métricas financieras y estados comerciales derivados de leads/cotizaciones. La atribución debe usar adaptadores y relaciones externas, no asumir una tabla `ventas` inexistente.

## 6. WABA existente

- Webhook Meta entrante y verificación de firma.
- Canales por marca y metadata de WABA/número.
- Persistencia de eventos, contactos, conversaciones, mensajes y reacciones.
- Relación entre conversaciones y leads.
- Módulos de conversación comercial, Embedded Signup y asistente GIA.
- Integración en tiempo real mediante notificaciones PostgreSQL y sincronización best-effort.

GD Intelligence no debe duplicar `whatsapp_conversations` ni `whatsapp_messages`. La fase WABA AI debe agregar `waba_ai_actions` y referencias a las entidades existentes, comenzando por sugerencia humana y envío manual.

## 7. PostgreSQL y datos

### Evidencia verificable disponible

- Dump custom: `backups/local_before_crm_audit_20260801_145409.dump`.
- Tamaño del dump: aproximadamente 115 KiB.
- Base origen: `BDGD`.
- PostgreSQL origen: 16.11; `pg_dump`: 16.11.
- Esquema observado: `public`.
- El dump contiene 29 tablas de negocio/operación, además de vistas, secuencias, funciones, constraints e índices.
- Tablas críticas del dump: `leads`, `cotizaciones`, `cotizaciones_detalle`, `cotizacion_items`, `usuarios`, `roles`, `marcas` y las tablas WABA.
- Hay claves primarias, uniques, foreign keys e índices específicos para estados/fechas de leads, relación de cotización-lead y conversación/mensajes WABA.
- El reporte de InMotion del 2026-08-01 registró 3.786 leads, 3.084 cotizaciones, 203 usuarios y 21 encuestas, con dump productivo de 4,1 MiB.
- PostgreSQL activo en la VM: 16.14 sobre Ubuntu, base de 49 MB, UTF-8 y timezone de servidor UTC.
- Estado live: 112 tablas de usuario, 213 índices, 34 foreign keys, 11 conexiones (1 activa), cero locks en espera y cero transacciones de más de 5 minutos.
- Tablas mayores estimadas: `tasks` 12 MB/23.220 filas; `leads` 2,7 MB/3.897 filas; `activity_log` 2,3 MB/8.666 filas; `cotizaciones` 880 KB/3.252 filas.

### Limitación actual

El `.env` del Mac no define `DATABASE_URL`; la inspección live debe ejecutarse dentro de la VM, donde la configuración protegida sí existe. Quedan pendientes un análisis específico de bloat/índices redundantes y la restauración real en una base aislada. La consulta live de versión, tamaño, sesiones y locks ya fue completada.

### Estrategia de aislamiento

Usar tablas aditivas prefijadas `wi_`, `ai_` y `waba_`. No agregar decenas de columnas analíticas a `leads`, `clientes` o `cotizaciones`. Las relaciones hacia entidades core serán nullable y explícitas. Históricos, sincronizaciones, performance, SEO, auditoría, deployments y acciones IA serán append-only salvo estados de workflow claramente auditados.

### Estrategia de migración

1. SQL transaccional e idempotente, con `lock_timeout` y `statement_timeout` conservadores.
2. Sólo `CREATE TABLE`, `CREATE INDEX` sobre tablas nuevas, inserts seed idempotentes y grants necesarios.
3. No usar `DROP`, `TRUNCATE`, renames ni alters destructivos.
4. Índices grandes futuros sobre tablas productivas sólo con evaluación previa y `CREATE INDEX CONCURRENTLY` fuera de transacción.
5. `downgrade` de Alembic no debe borrar historia en producción; el rollback operativo inicial será de código/feature flag. Una reversión física requiere backup y ventana aprobada.

## 8. Backups y restore

### Implementado/documentado

- Ubuntu: `deploy/ubuntu/backup-crm-nightly` crea `pg_dump` custom, inventario `pg_restore --list`, tar Zstandard del código, manifest y SHA-256; retención de 7 días.
- Proxmox: VM ID 100; backup snapshot diario documentado a las 00:30, retención de 2 copias.
- Backup Ubuntu más reciente: `20260808-001501`, dump 4,2 MB y archivos 1,3 GB. Los cuatro SHA-256 aprobaron y `pg_restore --list` leyó correctamente el dump.
- Código antes de deployment: tar remoto con lista explícita y restauración automática ante healthcheck fallido.
- InMotion: evidencia de dump `/home/bf68ec5/crm_backups/crm_db_20260801_204017.dump` y tar pre-release.

### Riesgo

Crear un dump no demuestra restaurabilidad. Los reportes existentes mantienen pendiente restaurar en una base/VM aislada. Antes de la primera migración GD Intelligence se debe comprobar: checksum, `pg_restore --list`, restore a destino no productivo, conteos y smoke tests. Un snapshot Proxmox complementa, pero no sustituye, el backup lógico.

## 9. Infraestructura y despliegue

### Ubuntu VM diseñada

- VM Proxmox: ID 100.
- Ruta de aplicación: `/opt/greendiamond/crm`.
- Usuario de servicio: `oscar`.
- FastAPI con Uvicorn, 4 workers, escuchando sólo en `127.0.0.1:8000`.
- Nginx como reverse proxy bajo `/crm/`.
- PostgreSQL como servicio requerido.
- systemd con reinicio automático, `NoNewPrivileges`, `PrivateTmp` y filesystem protegido.
- Acceso privado activo mediante VPN; no publicar SSH, PostgreSQL ni Proxmox.
- IP confirmada: `192.168.100.51`; WireGuard: `10.77.0.1/24`.
- Ubuntu 24.04.4 LTS, kernel 6.8, 4 CPU, 7,8 GiB RAM, 4 GiB swap y disco de 97 GB con 65 GB libres (30% usado).
- PostgreSQL, `crm-gd.service` y Nginx activos. CRM con 4 workers, aproximadamente 450 MB RAM y `/healthz` HTTP 200 en 1,5 ms durante la medición.

### Proxmox

- Host permanente y VM 100 con Guest Agent previsto.
- Rutina documentada: backup, apagado ordenado y encendido con healthcheck.
- El Guest Agent confirma `guest-fsfreeze` diario a las 00:30 hasta 2026-08-08, evidencia de ejecución del backup/snapshot Proxmox.
- El host que figuraba como `192.168.100.50` no respondió por SSH ni 8006. CPU/RAM/storage y lista de snapshots del hipervisor siguen pendientes de acceso directo a Proxmox.

### InMotion

- Despliegue público existente mediante Passenger/restart file, con sincronización explícita y healthcheck HTTPS.
- El reporte del 2026-08-01 confirmó 32/32 pruebas y 370 paths tras deployment.
- InMotion se mantiene como referencia y webhook Meta durante el piloto local según la documentación existente.

### Rollback actual

Los scripts crean un tar previo, instalan una lista explícita, compilan, ejecutan migración, reinician y hacen healthcheck. Ante error restauran el tar. Falta formalizar rollback de datos aditivos y probar restore completo en entorno aislado.

La copia desplegada está en la rama `feature/whatsapp-native-clean-20260806`, SHA `ec43b36`, y expone 378 paths OpenAPI. Su worktree contiene numerosos archivos staged/untracked, incluidos directorios completos de backend/frontend/deploy; no se debe hacer `git pull`, merge, checkout ni deployment basado en el estado Git remoto hasta inventariarlo y aislarlo. Los deployments GD Intelligence deberán transferir una lista explícita desde un artefacto/commit probado.

## 10. Secretos y configuración

- `.env` se carga desde la raíz y no está versionado en el índice actual.
- Variables locales detectadas, sin exponer valores: Meta/Instagram, WABA, Embedded Signup, OpenAI, modelo Greenie y flags de desarrollo.
- `DATABASE_URL` y `JWT_SECRET` no están presentes en el `.env` local auditado.
- El repositorio todavía versiona archivos de configuración/datos sensibles potenciales, entre ellos `data/backup_config.json` y `keys/gcal_oauth.json`; no se inspeccionaron ni reprodujeron sus valores.
- Reportes previos exigen rotar contraseña PostgreSQL, llaves SSH y credenciales Google que aparecieron históricamente.
- Las credenciales GD Intelligence deben existir sólo en environment/secret store del servidor, nunca en frontend, logs, commits o snapshots de contexto IA.

## 11. Riesgos prioritarios

1. **Crítico — worktree mezclado:** muchos cambios previos sin aislar; riesgo de commit/deploy accidental.
2. **Medio — Proxmox no consultable directamente:** la VM y Guest Agent están sanos, pero falta inventario live del hipervisor, storage y snapshots.
3. **Alto — restore no probado:** hay dumps y snapshots documentados, no una prueba reciente de restauración aislada.
4. **Alto — secretos históricos:** rotaciones pendientes y archivos sensibles aún versionados.
5. **Medio — migraciones heterogéneas:** Alembic y creación de tablas en runtime coexisten; GD Intelligence debe centralizar su propio esquema.
6. **Medio — dos frontends:** evitar implementar UI en la superficie React si el CRM operativo continúa usando `web/`.
7. **Medio — RBAC en evolución:** validar permisos en backend y conservar compatibilidad con permisos por menú existentes.
8. **Medio — jobs sin worker dedicado:** integraciones pesadas no pueden ejecutarse en request/response; usar un runner incremental invocable por cron hasta justificar otra infraestructura.

## 12. Gates antes de la primera migración de servidor

- Confirmar rama/commit exactos y worktree limpio o staging explícito.
- Generar dump lógico nuevo, checksum e inventario.
- Confirmar restore probado o snapshot pre-cambio más dump lógico recuperable.
- Consultar PostgreSQL en modo read-only: versión, tamaño, conexiones, locks pendientes, tablas/índices/constraints y espacio en disco.
- Confirmar servicio, Nginx, CPU, RAM y disco de Ubuntu.
- Confirmar estado/snapshot/backup de VM 100 en Proxmox.
- Ejecutar migración primero en una base restaurada o de desarrollo.
- Ejecutar tests, build, healthcheck y conteos core antes/después.

## 13. Arquitectura base recomendada

```text
CRM FastAPI existente
├── /api/gd-intelligence
│   ├── overview / sites / integrations
│   ├── web / seo / performance / campaigns
│   ├── ai
│   └── waba
├── backend/gd_intelligence
│   ├── domain y permisos
│   ├── repositories SQLAlchemy/SQL explícito
│   ├── services e integraciones
│   └── jobs incrementales
├── public.wi_*      analytics, SEO, performance, atribución
├── public.ai_*      perfiles, contexto, acciones, uso
└── public.waba_*    sólo extensiones IA; reutiliza WABA existente
```

Los dashboards leerán datos locales agregados, no APIs externas en cada request. Todos los conectores tendrán estado `CONNECTED`, `NOT_CONFIGURED`, `WARNING`, `ERROR` o `DISABLED`, más `last_sync`, `last_success`, duración, filas, errores y reintentos.

## 14. Decisión de avance

La Fase 0 queda documentada con evidencia live de Ubuntu, CRM, PostgreSQL y backup lógico. Se puede avanzar con código, tests y migraciones no ejecutadas. La ejecución sobre PostgreSQL productivo y cualquier deployment permanecen bloqueados hasta probar restore aislado y confirmar rollback; la falta de acceso directo a Proxmox no impide implementar módulos como `NOT_CONFIGURED` cuando falten credenciales.
