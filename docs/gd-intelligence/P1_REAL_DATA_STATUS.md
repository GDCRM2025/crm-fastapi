# P1 — Datos reales y borde público

Actualizado: 2026-08-12

## Resultado verificado

El collector first-party está implementado y probado en el baseline limpio, pero todavía no se publica en los cuatro sitios. Por lo tanto, el estado correcto es `TRACKING_DATA=BLOCKED_NETWORK`; no se declara tráfico real hasta completar un recorrido desde un navegador público y comprobar el incremento en PostgreSQL productivo.

La auditoría pública directa encontró:

| Sitio | `gd-tracker.js` en HTML | `data-site-code` | Estado |
|---|---:|---:|---|
| carritoscamaleon.cl | No | No | NOT_CONFIGURED |
| carritosexpress.cl | No | No | NOT_CONFIGURED |
| carritosgourmet.cl | No | No | NOT_CONFIGURED |
| carritosdelsabor.cl | No | No | NOT_CONFIGURED |

La detección anterior era un falso positivo: buscaba también el texto genérico “first party” dentro del contenedor GTM. Discovery ahora exige una etiqueta `<script>` que referencie realmente `gd-tracker.js`.

## Collector preparado

Rutas públicas mínimas:

- `POST /collect/v1/session`
- `POST /collect/v1/event`
- `OPTIONS` en ambas rutas

Controles implementados:

- allowlist de sitios habilitados y coincidencia exacta entre `Origin` HTTPS, dominio y `site_code`;
- payload máximo de 16 KiB y rate limit;
- allowlist de campos y rechazo recursivo de contraseña, secreto, token, autorización, cookie, tarjeta, RUT, email, teléfono y contenido de mensajes;
- minimización de URL: conserva sólo UTM, GCLID y FBCLID; descarta fragmentos y parámetros potencialmente personales;
- idempotencia por `event_id` sin sobrescribir eventos existentes;
- CORS por sitio, respuesta mínima y métricas agregadas protegidas por RBAC;
- cero exposición del panel administrativo.

## Evidencia local aislada

El recorrido HTTP real contra FastAPI local creó una sesión y dos eventos (`session_start` y `page_view`). Repetir el mismo `event_id` devolvió `duplicate=true` y no incrementó el total. La sesión quedó asociada al sitio CAM; landing y referrer quedaron minimizados; CORS devolvió únicamente `https://carritoscamaleon.cl`.

Suite consolidada: 142 tests PASS, 0 FAIL. Python compile, parseo JavaScript, `git diff --check` y Gitleaks PASS. La migración aditiva `2026_08_12_tracking_collector.sql` fue aplicada dos veces en PostgreSQL aislado.

## Bloqueo de red

Ubuntu productivo vive en la red privada `192.168.100.51`. El servidor público `greendiamond.cl/crm` es una aplicación legacy separada: no contiene GD Intelligence ni `gd-tracker.js` y no puede abrir conexión hacia Ubuntu en 8000 o 5432. No se utilizará esa aplicación como si compartiera la base productiva.

Para cerrar `TRACKING_DATA=REAL` hace falta uno de estos bordes explícitamente operados:

1. proxy/túnel público restringido exclusivamente a `/collect/v1/*`, o
2. collector público con cola y pull firmado desde Ubuntu.

En ambos casos deben permanecer inaccesibles login, APIs administrativas y PostgreSQL. Después se instala una sola etiqueta por sitio —preferentemente vía GTM existente— y se ejecuta el recorrido navegador → collector → `wi_sessions`/`wi_events` → métricas de GD Intelligence.

## WABA

La vista Greenie incorpora menú contextual por clic derecho y pulsación larga. Reutiliza acciones existentes para responder, reaccionar, copiar, crear/vincular lead, asignar ejecutivo, crear seguimiento, cotizar, abrir Comercial 360, ver historial y cerrar conversación. “Preparar reenvío” sólo carga el redactor; nunca envía automáticamente. Eliminar se muestra deshabilitado porque Meta no ofrece una operación compatible para borrar un mensaje ya enviado.

