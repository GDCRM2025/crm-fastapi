# Inventario seguro de integraciones externas

Actualizado: 2026-08-08. Fuente primaria: inspección pública de HTML y contenedores GTM. La planilla **CLAVES 2024- GDGROUP** se consultó únicamente en la columna de nombres de servicio de las cuatro pestañas de marca; confirmó presencia de Google y Meta. No se consultaron, copiaron ni almacenaron columnas de usuarios, contraseñas o tokens.

| Sitio | GTM container | GA4 Measurement ID vía GTM | GA4 Property ID | Search Console | Clarity | PageSpeed/CrUX | GD Tracker | Meta Pixel público |
|---|---|---|---|---|---|---|---|---|
| CAM | `GTM-52G8CVS` | `G-4ZMLEC3SWJ` | Pendiente de API/secret store | Verificación HTML detectada; propiedad API pendiente | No instalado | Origen identificado; API pendiente | Detectado en GTM | `1485976343313245` |
| EXP | `GTM-NGRGQC3` | `G-RZ4PBH9X8N` | Pendiente de API/secret store | No verificado públicamente | No instalado | Origen identificado; API pendiente | Detectado en GTM | Tag Meta detectado; ID no expuesto con certeza |
| GOU | `GTM-KM5BFH9` | `G-LH278CFR50` | Pendiente de API/secret store | No verificado públicamente | No instalado | Origen identificado; API pendiente | Detectado en GTM | `2117438535686358` |
| DEL | `GTM-KZS7S6NL` | `G-J57LXCMDG4` | Pendiente de API/secret store | No verificado públicamente | No instalado | Origen identificado; API pendiente | Detectado en GTM | `1230571445550373` |

## Decisión de instalación

Los cuatro sitios ya tienen GTM. Es el mecanismo principal obligatorio para GA4, Clarity y `gd-tracker.js`; no se deben insertar snippets directos adicionales. GA4 y GD Tracker ya aparecen en los cuatro contenedores. Clarity debe agregarse una sola vez por contenedor después de obtener cada Project ID desde el secret store/aplicación correspondiente.

Los IDs `G-…` son Measurement IDs públicos, no GA4 numeric Property IDs. Los Property IDs, cuentas autorizadas de Search Console y credenciales API no se deducen del HTML ni se leen desde columnas sensibles de la planilla: permanecen `NOT_CONFIGURED`/pendientes hasta migración al secret store.

## Seguridad y rotación

- `wi_integrations.external_id` sólo contiene identificadores públicos; el backend rechaza formatos de password, token, secret o API key.
- La API/UI no devuelve el campo JSON de configuración ni secretos.
- Toda credencial que haya sido compartida históricamente en la planilla debe considerarse comprometida y quedar en cola de rotación antes de usarse en servidor.
- Ningún secreto fue agregado a Git, documentación, logs o PostgreSQL durante este inventario.
