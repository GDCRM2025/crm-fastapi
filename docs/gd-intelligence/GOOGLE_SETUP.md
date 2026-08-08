# Google setup

1. Crear/seleccionar un proyecto Google Cloud empresarial.
2. Activar Google Analytics Data API, Search Console API, PageSpeed Insights API y Chrome UX Report API.
3. Crear una service account para lecturas server-to-server; no descargar su JSON al repositorio.
4. Conceder Viewer a la service account en cada propiedad GA4 y acceso de lectura a cada propiedad Search Console.
5. Configurar por sitio los IDs externos desde backend: `GA4_PROPERTY_ID_*`, propiedad Search Console y, si aplica, `GOOGLE_CLOUD_PROJECT`.
6. Entregar credenciales mediante `GOOGLE_APPLICATION_CREDENTIALS` apuntando a un archivo `0600` fuera del repo, o identidad del runtime.
7. Probar una ventana de un día, verificar paginación/cuotas y después habilitar el job incremental.

OAuth con redirect sólo será necesario para consentimiento delegado. Cualquier redirect debe usar HTTPS y coincidir exactamente con el registrado. PageSpeed/CrUX pueden no devolver field data; eso se registra como ausencia, no como cero.
