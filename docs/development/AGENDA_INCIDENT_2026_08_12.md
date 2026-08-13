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

## Montaje operativo — actualización posterior

Release `aa9d24af24f506182fa5ae8d9ea7bc2721ff3117` separa formalmente el montaje del evento comercial.

- Montaje es un evento exclusivo de Google Calendar, relacionado al lead pero sin crear un segundo evento CRM, financiero ni de venta.
- Fecha, inicio, fin, comuna y dirección son independientes: puede realizarse antes, el mismo día o después del evento comercial.
- El montaje incluye los mismos equipos de la cotización/evento comercial y el montaje calculado en su descripción operacional.
- El evento CRM, el estado del lead y los campos `calendar_start`/`calendar_event_id` conservan como ancla el evento comercial; nunca el montaje.
- Cada evento Calendar usa una llave idempotente distinta, por lo que montaje y evento principal no se sobrescriben incluso si ocurren el mismo día.
- Rollback inmediato: `b14d547ca2f92fe6bcb2c9b87e22364fa24c51b7`.

## Cierre funcional del wizard — 2026-08-13

La prueba operacional posterior reveló dos fallas adicionales y ambas quedaron corregidas antes del siguiente release:

- El contenedor de montaje tenía `display:none` y `display:grid` en el mismo atributo. Los campos se veían aunque la opción interna seguía desmarcada; por eso el payload podía omitir `montaje_event` sin avisar.
- La transición Confirmado → otro estado usaba una referencia `DB` inexistente dentro de un bloque que ocultaba la excepción. El estado cambiaba, pero Calendar podía conservar el evento.

Contrato definitivo:

- El paso 1 pregunta explícitamente **¿Requiere montaje operativo separado?** con respuesta No/Sí.
- Sí activa fecha, hora inicial, hora final, comuna y dirección independientes. Un montaje incompleto devuelve HTTP 422; nunca se ignora.
- El preview simple produce `COMMERCIAL + MOUNTING`; multi-día conserva un evento comercial por día y añade un único `MOUNTING`. Multi-locación no fue reescrito.
- Al retirar un confirmado, se unen los IDs persistidos con una búsqueda de Google Calendar por propiedad privada `lead_id`; se eliminan evento comercial y montajes. La transición es fail-closed si Calendar no puede verificarse o limpiarse.
- En un confirmado quedan bloqueados por UI y API: marca, plataforma, fecha del evento, número y monto de cotización e historial. Teléfono, comuna, dirección y tipo de cliente sincronizan todos los hijos de Calendar sin convertir el montaje en evento comercial.
- Cambiar tipo de cliente no reescribe una cotización confirmada: deja alerta y nota de revisión de documento/IVA.
- La atribución digital existente se incorpora a Calendar y notas; medios `cpc`, `ppc`, `paid_search`, `paidsearch` o `sem` se identifican como SEM sin inferir datos ausentes.

Evidencia local aislada:

- 152 tests PASS, 0 FAIL; Python compile, JavaScript parse, diff-check y Gitleaks PASS.
- FastAPI/PostgreSQL levantados desde `MAC_SETUP.md`; health y login autenticado PASS.
- QA visual del wizard: decisión `yes`, checkbox interno `true`, bloque de montaje `grid`, fecha independiente y horario 09:00–10:00.
- QA visual multi-día: modo `multiday=true` y montaje conservado `true`.
- Preview API simple: 2 eventos (`COMMERCIAL`, `MOUNTING`). Preview multi-día: 3 eventos (`COMMERCIAL`, `COMMERCIAL`, `MOUNTING`).
- Ningún evento fue creado durante QA: todas las llamadas fueron `dry_run` y el wizard se canceló antes de confirmar.
