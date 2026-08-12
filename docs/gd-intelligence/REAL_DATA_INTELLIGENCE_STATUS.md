# Inteligencia con datos reales

Actualizado: 2026-08-12

## Principio operativo

Los módulos distinguen explícitamente entre motor disponible, autorización externa y datos sincronizados. La ausencia de credenciales o histórico nunca se representa como cero rendimiento ni se completa con ejemplos simulados.

## Google Ads

- Adaptador REST READ ONLY con OAuth refresh o service account autorizada.
- Developer token obligatorio desde secret store.
- Lista cuentas accesibles y permite seleccionar Customer ID, manager y sitio/marca desde UI.
- Sincroniza Campaign, Ad Group, Ad, métricas diarias, Search Terms y referencias GCLID hasheadas.
- Histórico estándar de 90 días; máximo configurable de 730 días. `click_view` se limita a su ventana soportada.
- Estado local: `READY_FOR_CREDENTIAL`; no existen credenciales Ads autorizadas ni histórico importado.

## Meta Ads

- Adaptador Graph API exclusivamente GET, con paginación por cursor sobre el host configurado.
- Lista Business, Ad Account, Page e Instagram Business Account.
- Sincroniza Campaign, Ad Set, Ad, Creative asociado, insights diarios y `action_values` disponibles.
- Estado local: `READY_FOR_CREDENTIAL`; no existe token Marketing API autorizado ni histórico importado.

## Paid Media Intelligence

- Atribución: `EXACT`, `STRONG`, `INFERRED`, `UNKNOWN`; empates no exactos cierran en `UNKNOWN`.
- GCLID/FBCLID se conservan únicamente como SHA-256.
- Search Terms: `HIGH_VALUE`, `WASTE`, `NEGATIVE_CANDIDATE`, `INSUFFICIENT_DATA`.
- Creative Intelligence cruza frecuencia, CTR, CPA CRM, conversión, edad y volumen mínimo.
- Change Risk implementa `STABLE`, `LEARNING`, `RECENT_CHANGE`, `COOLDOWN`, `LOW_DATA`, `LIMITED_BY_BUDGET`, `INSUFFICIENT_EVIDENCE`.
- Ninguna recomendación publica cambios en plataformas.

## SEO y alertas

- SEO Opportunity Score 0–100 usa únicamente Search Console, tráfico, conversión, revenue y salud técnica que estén disponibles.
- Estados: `READY`, `PARTIAL`, `TECHNICAL_ONLY`, `INSUFFICIENT_DATA`.
- En el entorno local actual existe evidencia técnica de Site Health; Search Console no está autorizado.
- Alertas deduplicadas para integraciones, Site Health, Ads, SEO y conversaciones comerciales sin atender. Cada alerta incluye evidencia, acción y próxima revisión.

## Omnicanal real

La Inbox no copia mensajes. Guarda sólo referencia al registro fuente, lead, cotización, responsable, seguimiento y estado.

| Canal | Fuente reutilizada | Estado local | Capacidad |
|---|---|---|---|
| WhatsApp | `whatsapp_conversations` | Datos reales | Responder, crear/vincular lead, asignar, seguir y cotizar mediante Tools |
| Email | `gia_email_messages` | Datos reales | Responder y operar comercialmente cuando la cuenta está configurada |
| Instagram | `gia_ig_events` | Sin registros locales | Recepción solamente; envío Graph no autorizado |
| Messenger | `gia_ig_events` | Sin registros locales | Recepción solamente; envío Graph no autorizado |

La analítica por canal calcula conversaciones → leads → cotizaciones → ventas → revenue únicamente cuando existe vínculo real.

## Dashboard Ejecutivo y GD AI

- Dashboard Ejecutivo se habilita con CRM sales y al menos tres fuentes reales suficientes. En el entorno local usa CRM, tracking y Site Health; Paid Spend, CPA CRM y ROAS CRM permanecen `Sin datos` hasta sincronizar Ads.
- GD AI dispone de un context builder allowlisted por rol en modo `READ_ANALYZE_SUGGEST`.
- El context builder no acepta SQL, no entrega acceso arbitrario a tablas y no tiene capacidad de escritura.
