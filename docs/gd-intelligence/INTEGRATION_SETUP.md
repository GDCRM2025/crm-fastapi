# Configuración de integraciones

Este documento enumera IDs públicos y requisitos técnicos. No pegues credenciales en HTML, documentación, logs ni Git. Las API keys operables se introducen únicamente en el formulario write-only del CRM; las credenciales bootstrap de la aplicación OAuth permanecen en el mecanismo protegido del servicio.

La operación diaria no utiliza este documento: Admin trabaja desde **Settings → Integraciones**. Este archivo conserva únicamente requisitos de plataforma y recuperación DevOps.

## CredentialVault

Las API keys introducidas desde el CRM se validan antes de persistir y se cifran at-rest en `wi_integration_credentials`. La clave maestra nunca reside en PostgreSQL ni Git. En Mac aislado se crea automáticamente un archivo `runtime/mac/credential_vault.key` con permisos `0600`; en servidor debe configurarse `GD_CREDENTIAL_MASTER_KEY` o `GD_CREDENTIAL_MASTER_KEY_FILE` desde el mecanismo protegido del servicio.

No existe endpoint de lectura. Backend sólo puede descifrar mediante `use_internal()` durante una llamada al proveedor. Rotación y revocación conservan eventos sanitizados en `wi_integration_credential_events`.

## Google Analytics 4 y Search Console

GD Intelligence no solicita usuario ni contraseña. El flujo operativo usa una service account backend con acceso de lectura.

1. En Google Cloud habilita `Google Analytics Admin API`, `Google Analytics Data API` y `Search Console API`.
2. Crea una service account y guarda su JSON directamente en el secret store del host, fuera del repositorio.
3. Agrega el correo de esa cuenta como Viewer en GA4 y como usuario de las propiedades Search Console.
4. Configura `GOOGLE_SERVICE_ACCOUNT_FILE` con la ruta absoluta protegida y reinicia el backend.
5. En **GD Intelligence → Integration Center → CONECTAR GOOGLE**, lista y selecciona por sitio el `GA4 Property ID` numérico y la propiedad Search Console (`sc-domain:dominio` o URL-prefix).

OAuth delegado requiere `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REDIRECT_URI` y el refresh token protegido correspondiente. El refresh token nunca se devuelve al frontend y se configura como `GOOGLE_ADS_REFRESH_TOKEN` mediante el secret store; alternativamente puede utilizarse `GOOGLE_SERVICE_ACCOUNT_FILE` cuando la cuenta de servicio dispone de acceso explícito. El `GA4 Measurement ID` (`G-…`) es público, pero no reemplaza al Property ID numérico de API.

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

- Google Ads: habilita la API y configura `GOOGLE_ADS_DEVELOPER_TOKEN` más OAuth refresh o `GOOGLE_SERVICE_ACCOUNT_FILE`. Integration Center llama `customers:listAccessibleCustomers`, permite seleccionar Customer ID y marca/sitio, y sincroniza mediante `googleAds:searchStream` en modo READ ONLY. `GOOGLE_ADS_LOGIN_CUSTOMER_ID` es público y sólo se usa cuando existe una cuenta manager.
- Meta Ads: configura `META_ACCESS_TOKEN` en secret store con permisos de lectura aprobados. Integration Center lista Business, Ad Account, Page e Instagram Account, permite asociarlos a marca/sitio y sincroniza entidades e insights mediante solicitudes GET. No existe ninguna operación de publicación o mutación.
- Histórico inicial: 90 días por defecto, ampliable hasta 730 cuando el proveedor lo permite. Las referencias GCLID disponibles en Google se limitan al periodo soportado por `click_view` y se persisten únicamente como hash.
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
| `GOOGLE_ADS_REFRESH_TOKEN` | OAuth Google Ads delegado | Sí |
| `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | Cuenta manager pública | No |
| `GOOGLE_ADS_API_VERSION` | Versión REST validada | No |
| `META_ACCESS_TOKEN` | Lectura de Meta Marketing API | Sí |

Toda credencial históricamente compartida debe revocarse y rotarse antes del deployment. La UI y la API sólo devuelven booleanos de disponibilidad, IDs públicos y diagnósticos sanitizados.
