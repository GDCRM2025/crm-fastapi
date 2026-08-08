# Arquitectura GD Intelligence

## Integración

GD Intelligence vive dentro del CRM FastAPI existente. El backend se organiza en `backend/gd_intelligence`, expone rutas autenticadas desde `backend/routers/gd_intelligence.py` y conserva el frontend operativo bajo `web/`.

## Capas

- **API:** valida JWT, permisos funcionales, payloads y códigos de error estables.
- **Dominio/servicios:** reglas de atribución, SEO, IA, alertas y workflows sin dependencia HTTP.
- **Repositorio:** SQL pequeño y explícito contra tablas aisladas; no agrega carga analítica a las tablas core.
- **Jobs:** sincronización incremental invocable por cron, fuera del request del usuario.
- **UI:** consume snapshots/agregados locales; nunca consulta GA4, Search Console o PageSpeed al abrir una pantalla.

## Datos

- `wi_*`: sitios, integraciones, tráfico, eventos, SEO, performance, campañas, atribución, alertas, cambios y experimentos.
- `ai_*`: perfiles, conversaciones, contexto autorizado, acciones, feedback, versiones de prompt y uso.
- `waba_*`: únicamente extensiones IA; conversaciones y mensajes reutilizan las tablas WhatsApp actuales.
- `gd_*`: permisos transversales y configuración estrictamente común.

## Seguridad

- Denegación por defecto para roles desconocidos.
- Admin no puede desplegar ni hacer rollback sin grant explícito.
- Super Admin mantiene auditoría obligatoria.
- Credenciales sólo en backend/environment.
- La IA recibe snapshots mínimos construidos por usuario, marca, entidad y permiso.
- Ningún flujo IA modifica producción autónomamente.

## Estados de integración

`CONNECTED`, `NOT_CONFIGURED`, `WARNING`, `ERROR`, `DISABLED`.

Cada conector registra última ejecución, último éxito, duración, filas, error seguro y reintentos. Los mensajes almacenados no deben contener tokens ni respuestas completas del proveedor cuando incluyan PII.

## Escalamiento

El diseño inicial funciona con cron y agregados locales. Una réplica de lectura o un worker dedicado se puede introducir posteriormente mediante los repositorios/jobs, sin cambiar contratos API ni el modelo de atribución.
