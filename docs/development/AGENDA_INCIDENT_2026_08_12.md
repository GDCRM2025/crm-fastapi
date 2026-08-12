# Incidente de agendamiento — 2026-08-12

## Resultado

Resuelto en producción con release `b14d547ca2f92fe6bcb2c9b87e22364fa24c51b7`. Rollback inmediato: `4cd4321564b7f3279a2c32e9b049b8ac79bf7453`.

## Causa raíz

El endpoint unificado ya usaba idempotencia y un advisory lock por lead, pero `tools.approve_agenda` adquiría además el lock global PostgreSQL `26042401`. Una sincronización lenta con Google Calendar bloqueaba todos los agendamientos, incluso de leads y marcas diferentes. Después de 25 segundos los demás requests devolvían `503 Agenda ocupada`.

La recuperación de locks también consideraba stale cualquier operación de más de 90 segundos y podía ejecutar `pg_terminate_backend` mientras Google seguía trabajando. El historial productivo contiene un fallo `AdminShutdown` compatible con esa conducta.

## Corrección

- Todos los caminos de mutación de Calendar usan una única llave derivada de `id_lead`.
- Leads diferentes pueden agendarse simultáneamente.
- Dos requests del mismo lead siguen serializados e idempotentes.
- La recuperación sólo considera una conexión `idle` durante al menos 15 minutos; nunca termina una conexión activa por superar 90 segundos.
- Google Calendar usa timeout HTTP acotado, 20 segundos por defecto y configurable con `GCAL_HTTP_TIMEOUT_SECONDS` entre 5 y 60 segundos.
- No se aumentaron workers: los cuatro existentes son suficientes para el hotfix y aumentar concurrencia bajo el lock global habría amplificado la espera.

## Evidencia

- 146 tests PASS, 0 FAIL; Python compile, JavaScript parse, diff-check y Gitleaks PASS.
- PostgreSQL local y productivo: primer lead `true`, mismo lead concurrente `false`, lead diferente concurrente `true`.
- Backup: `/opt/greendiamond/backups/agenda_hotfix/20260812_180424`.
- Código previo SHA-256: `677e66aca43855abacd00d239e4c27446bcccbdb1e5d6e40aaf4d01737cdff3f`.
- PostgreSQL previo SHA-256: `458f2939be5916808789218df7a7eca1dc3125b6d2a812e3e86aa29a061eecc6`; catálogo `pg_restore` PASS.
- Artefacto release SHA-256: `15945d834f168e32a8ba48847026f15ba6f102f9781198f7411b0f16cc280df1`.
- Candidato 9090: health, Leads, Agenda, Cotizador y WABA 200; cero routers omitidos.
- Post-cutover: mismos smokes 200, replay idempotente PASS, 0 locks de agenda, 0 tracebacks y candidato detenido.
- El proceso anterior atrapado en Google no aceptó shutdown graceful; se terminó únicamente el MainPID supervisado y systemd levantó correctamente el release nuevo.
- Lead 3903 preservado con `pendiente_agendar=true` y sin evento; listo para reintento desde la UI. No se creó ningún evento por intervención administrativa.

## Worker exclusivo — siguiente endurecimiento

Un worker de agenda sigue siendo recomendable para resiliencia, no como sustituto de este hotfix. Debe usar una cola durable con estados `queued/processing/completed/failed`, lease renovable, reintentos con backoff, dead-letter explícito e idempotencia por lead/request. La API debe responder con un job consultable y la UI mostrar progreso. No se debe separar el trabajo en un proceso en memoria ni aumentar workers indiscriminadamente.
