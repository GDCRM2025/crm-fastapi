# Auditoría Tools, Finanzas y GD Intelligence · 24-08-2026

## Resultado ejecutivo

| Frente | Estado | Evidencia / acción |
|---|---|---|
| Conciliación bancaria | Operativo después del hotfix R5.4.4 | 4 cuentas, 516 movimientos, 462 pendientes; cuenta Green devuelve 186 movimientos conciliables. |
| Facturas abiertas para conciliación | Corregido | El endpoint que respondía 500 ahora devuelve 149 obligaciones abiertas. |
| Navegación a Conciliación | Corregido | `#reconciliation` abre directamente “Movimientos bancarios”; antes siempre abría Resumen. |
| Google Calendar | Operativo | OAuth conectado, 12 calendarios, 19 eventos en estadísticas y 34 eventos en listado. |
| Dashboards / reportes Tools | Operativo | Dashboard, reportes, agenda, leads del día y eventos responden HTTP 200. |
| Instagram | Operativo | 4 marcas disponibles para el flujo omnicanal. |
| WhatsApp | Operativo con observación de seguridad | API y bandeja responden; el router permite lectura a cualquier usuario autenticado y debe endurecerse por permisos `waba_view/waba_reply`. |
| Correo GIA | Parcial | Existen 323 mensajes históricos, pero el conector reporta `configured=false` y no hay credenciales de correo cargadas en el servicio. |
| Chat interno | Deshabilitado | Feature flag `internal_chat=false`. |
| GD Intelligence para Control de Gestión | Corregido | Se retiró el bloqueo global SUPERADMIN y se respetan permisos específicos de lectura/escritura. Joaquín obtiene HTTP 200 en vistas y 403 en administración. |
| Search Console | Avance real | 395 filas sincronizadas: Camaleón 246, Express 17 y Gourmet 132; Del Sabor sin datos en la ventana actual. |
| SEO Intelligence | Operativo | 184 oportunidades persistidas; 181 visibles en la ventana consultada. |
| Alertas ejecutivas | Operativo | 4 alertas actualizadas con deduplicación. |
| Site Health | Operativo | 4.668 ejecuciones históricas. |
| Tracking propio | Parcial | 40 sesiones, 114 eventos y 232 atribuciones; el inventario aún marca las cuatro integraciones Tracking como no configuradas. |
| GA4 | Conectado, sin datos | Propiedad `www.carritoscamaleon.cl` autorizada y sincronización exitosa con cero filas en siete días. |
| Paid Media | Pendiente de credenciales/cuentas | No existen cuentas publicitarias ni métricas de Google Ads/Meta Ads. |
| Analytics DBeaver | Bloqueado por activación administrativa | Rol seguro creado, pero el schema `analytics` aún no contiene vistas y el peer WireGuard `10.77.0.28` no está cargado/alcanzable. |

## Correcciones desplegadas

- Cast explícito de filtros PostgreSQL opcionales para eliminar el error `AmbiguousParameter`.
- Selección de vista por hash en GD Finance.
- Etiqueta de menú “Conciliación bancaria” y cache bust R5.4.4.
- Eliminación del bloqueo global SUPERADMIN en los tres routers de GD Intelligence.
- Conservación de autorización granular: lectura para Control de Gestión; configuración, credenciales y administración siguen bloqueadas.
- Compatibilidad del estado de integraciones con el constraint real (`REQUIRES_ATTENTION`, no `WARNING`).
- Persistencia correcta de `CONNECTED` versus `NO_DATA` al sincronizar Google.
- Asociación y sincronización inicial de propiedades de Search Console por sitio.

## Pendientes priorizados

### P0 · Infraestructura Joaquín

1. Instalar las vistas `analytics` como PostgreSQL admin.
2. Cargar/verificar el peer WireGuard `10.77.0.28` y entregar el perfil al notebook.
3. Entregar la contraseña de `joaquin_analytics` por canal privado.

### P1 · Tools

1. Endurecer WhatsApp GIA con `waba_view`, `waba_reply` y `waba_admin` por endpoint.
2. Decidir si se configura el correo GIA o se deshabilita temporalmente su feature para no mostrar una integración incompleta.
3. Eliminar o documentar archivos legacy no referenciados, incluido `web/views/whatsapp.html` vacío.

### P1 · GD Intelligence

1. Configurar cuentas Google Ads y Meta Ads de sólo lectura; hoy no existen `wi_ad_accounts`.
2. Revisar por qué GA4 no entrega filas en la ventana de siete días.
3. Instrumentar Tracking en los cuatro sitios o reconciliar el inventario con los eventos que ya llegan.
4. Crear UTMs gobernadas; actualmente `wi_utm_links` está vacío.

## Release validada

`/opt/greendiamond/releases/finance-gdi-tools-r544-20260824_133300`

Pruebas posteriores al corte: health 200, permisos 200, cuentas 200, movimientos 200, CxP 200, GD Intelligence 200, Paid Media 200, Data Quality 200, administración de Joaquín 403 y PDF de cotización 7612 válido.
