# Plan de capturas del manual

Las capturas se generan únicamente en el CRM local con datos seed o anonimizados. Recortar topbar sólo cuando sea necesario y ocultar PII, JWT, cookies, IDs de conversación y secretos.

| Prioridad | Pantalla | Estado que debe mostrar | Rol |
|---|---|---|---|
| P0 | Login | Formulario limpio, URL HTTP local documentada | Sin sesión |
| P0 | Panel | Menú GD Intelligence desplegado | Admin |
| P0 | Integration Center | Cuatro sitios; estados, detectado, faltante y acción | Admin |
| P0 | Paid Media | Resumen sin datos fabricados y estado de conectores | Admin/Marketing |
| P0 | Leads | Origen comercial y campaña opcional, sin UTM técnicas | Ejecutivo |
| P0 | Lead detalle | Atribución Digital read-only | Ejecutivo |
| P1 | UTM | Constructor y tabla de resultados CRM | Marketing |
| P1 | Site Health | Estado por sitio y fecha de verificación | Marketing |
| P1 | Ayuda | Artículo contextual Paid Media y selector de nivel | Marketing |
| P1 | Tools | Correo/Instagram/WhatsApp existentes, separados de BI | Admin |

Cada captura requiere: fecha, SHA, URL local, rol, resolución y checklist de ausencia de secretos/PII.
