# Inbox omnicanal y analítica por canal

## Alcance implementado

La bandeja unificada es una capa de lectura sobre las fuentes actuales. No crea una segunda copia de mensajes, adjuntos ni payloads:

- WhatsApp: `whatsapp_conversations`, `whatsapp_messages` y `whatsapp_conversation_leads`.
- Email: `gia_email_messages`.
- Instagram y Messenger: eventos `messaging` verificados en `gia_ig_events`.
- Estado operativo: `wi_omnichannel_work_items`, que sólo conserva referencias, lead, cotización, responsable, seguimiento y estado.

Las métricas se calculan desde vínculos reales: conversación → lead → cotización → venta/ingreso. Si no existe vínculo, el sistema reporta cero o `NO_DATA`; no infiere resultados.

## Estado real por canal

| Canal | Recibir | Responder | Crear lead | Vincular lead | Asignar/seguir | Cotizar | Estado |
|---|---:|---:|---:|---:|---:|---:|---|
| WhatsApp | Sí | Sí | Sí | Sí | Sí | Sí | Operativo mediante Tools/WhatsApp |
| Email | Sí | Sí | Sí | Sí | Sí | Sí | Operativo si la cuenta SMTP/IMAP está configurada |
| Instagram | Sí | No | No | Sí | Sí | No | Sólo recepción; falta envío Graph API autorizado |
| Messenger | Sí | No | No | Sí | Sí | No | Sólo recepción; falta envío Graph API autorizado |

La bandeja no presenta como disponible una acción que el adaptador real no pueda ejecutar. Para responder o crear leads en WhatsApp/Email, devuelve la ruta del módulo existente; no reimplementa el envío.

## API autenticada

- `GET /api/omnichannel/capabilities`: capacidades efectivas y explicación por canal.
- `GET /api/omnichannel/inbox`: lectura cronológica unificada con filtro `channel`.
- `PATCH /api/omnichannel/items/{channel}/{source_ref}`: vínculo, asignación, seguimiento y estado.
- `GET /api/omnichannel/analytics`: conversaciones, leads, cotizaciones, ventas e ingresos por canal.

El acceso se limita a las marcas del usuario. Para roles no administrativos sin marcas explícitas se aplica cierre seguro y no se retornan conversaciones.

## Límites deliberados

- No hay endpoint genérico de envío: cada canal conserva sus validaciones, ventanas y credenciales.
- Instagram/Messenger permanecen `RECEIVE_ONLY` hasta disponer de activos Meta autorizados y una implementación de envío probada.
- La analítica actual usa la ventana visible de Inbox (`scope=current_inbox`). Una serie histórica materializada requerirá un proceso agregado posterior, nunca copia de mensajes.
- No se simulan mensajes, conversiones ni ingresos cuando una fuente está vacía.

