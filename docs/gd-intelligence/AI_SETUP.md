# GD AI setup

- La API key vive sólo en el servidor (`OPENAI_API_KEY`); nunca en HTML, JavaScript o respuestas API.
- Configurar un modelo permitido, límites diarios/mensuales por rol y timeout.
- El backend construye contexto mínimo por usuario, marca, cliente, vista y permiso.
- Registrar versión de prompt, latencia, tokens/costo, aceptación/edición y entidad relacionada; minimizar PII.
- La IA propone. Un humano revisa y aprueba. Ninguna herramienta de producción se habilita automáticamente.
- Ante falta de key, cuota o política, responder `NOT_CONFIGURED`/error seguro y conservar el CRM operativo.
