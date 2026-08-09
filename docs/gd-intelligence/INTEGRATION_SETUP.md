# Configuración de integraciones

Este documento enumera IDs públicos y variables requeridas. No pegues credenciales en GD Intelligence, HTML, API payloads, logs ni Git. En Mac se cargan desde `.env` ignorado; en Ubuntu, desde el secret store o archivo de entorno protegido del servicio.

## Google Analytics 4 y Search Console

GD Intelligence no solicita usuario ni contraseña. El flujo operativo usa una service account backend con acceso de lectura.

1. En Google Cloud habilita `Google Analytics Admin API`, `Google Analytics Data API` y `Search Console API`.
2. Crea una service account y guarda su JSON directamente en el secret store del host, fuera del repositorio.
3. Agrega el correo de esa cuenta como Viewer en GA4 y como usuario de las propiedades Search Console.
4. Configura `GOOGLE_SERVICE_ACCOUNT_FILE` con la ruta absoluta protegida y reinicia el backend.
5. En **GD Intelligence → Integration Center → CONECTAR GOOGLE**, lista y selecciona por sitio el `GA4 Property ID` numérico y la propiedad Search Console (`sc-domain:dominio` o URL-prefix).

OAuth opcional requiere `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` y `GOOGLE_OAUTH_REDIRECT_URI` desde el secret store. No se persisten refresh tokens hasta disponer de almacenamiento cifrado administrado. El `GA4 Measurement ID` (`G-…`) es público, pero no reemplaza al Property ID numérico de API.

## Google Tag Manager

El Container ID `GTM-…` es público. Usa un único contenedor publicado por sitio como mecanismo principal para GA4, Clarity y `gd-tracker.js`. Ejecuta **VERIFICAR** antes de publicar para evitar duplicados.

## PageSpeed Insights y CrUX

Configura `PAGESPEED_API_KEY` únicamente en el backend, restringida por API, host/IP y cuota. PageSpeed y CrUX muestran estado por sitio. La ausencia de datos CrUX es `WARNING`, nunca datos inventados ni un bloqueo para otros módulos.

## Microsoft Clarity

1. Crea un proyecto separado por dominio.
2. Copia sólo el Project ID público.
3. Usa **CONFIGURAR**, selecciona el sitio y registra el ID.
4. Publica el tag una vez mediante GTM y ejecuta **VERIFICAR**.

## Meta Pixel

El Pixel ID numérico es público. Regístralo por sitio y verifica su existencia antes de publicar. Los access tokens de Meta son secretos backend y no forman parte de esta configuración pública.

## Google Ads y Meta Ads (Paid Media)

La primera etapa es `READ · ANALYZE · RECOMMEND`: no publica campañas, presupuestos, anuncios ni palabras negativas.

- Google Ads: habilita la API y configura en backend `GOOGLE_ADS_DEVELOPER_TOKEN`, `GOOGLE_SERVICE_ACCOUNT_FILE` y los Customer ID públicos elegidos en Integration Center. Si la cuenta requiere manager account, registra su ID público como configuración, no como secreto.
- Meta Ads: configura `META_ACCESS_TOKEN` en secret store y registra el Ad Account ID público por sitio. Usa permisos de lectura mínimos (`ads_read`, según la app aprobada).
- Los conectores deben sincronizar en forma idempotente por cuenta, fecha, entidad, dispositivo y red. Un reintento actualiza la misma clave; no duplica gasto.
- `platform_conversions` y valor de conversión de plataforma se conservan separados de Leads, Cotizaciones, Ventas, Revenue y ROAS del CRM.

## GD Tracker first-party

Publica `/web/js/gd-tracker.js` mediante GTM con `data-site-code` (`CAM`, `EXP`, `GOU` o `DEL`). Si el colector vive en otro origen, `data-api-base` apunta a un endpoint aprobado o proxy same-origin; no se codifican IP, localhost ni URLs productivas en el archivo.

El tracker conserva `gd_visitor_id`, `gd_session_id`, referrer, landing y UTM; añade `gd_session_id` oculto a formularios y registra `click_whatsapp`. El servidor debe permitir sólo orígenes autorizados.

## Variables y rotación

| Variable | Uso | Secreta |
|---|---|---|
| `GOOGLE_SERVICE_ACCOUNT_FILE` | Ruta al JSON de service account | Sí, por el contenido referenciado |
| `GOOGLE_OAUTH_CLIENT_ID` | OAuth opcional | ID restringido |
| `GOOGLE_OAUTH_CLIENT_SECRET` | OAuth opcional | Sí |
| `GOOGLE_OAUTH_REDIRECT_URI` | Callback exacto | No |
| `PAGESPEED_API_KEY` | PageSpeed/CrUX | Sí |
| `GOOGLE_ADS_DEVELOPER_TOKEN` | Lectura de Google Ads API | Sí |
| `META_ACCESS_TOKEN` | Lectura de Meta Marketing API | Sí |

Toda credencial históricamente compartida debe revocarse y rotarse antes del deployment. La UI y la API sólo devuelven booleanos de disponibilidad, IDs públicos y diagnósticos sanitizados.
