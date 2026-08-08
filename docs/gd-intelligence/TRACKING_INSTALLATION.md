# Instalación de tracking first-party

Estado: diseño preparado; el endpoint público y `gd-tracker.js` todavía no se deben instalar.

La instalación final usará un token público por sitio, validación de dominio/origin, límite de payload/eventos y rate limiting. El script será async/defer, mantendrá IDs visitor/session first-party y enviará lotes con `sendBeacon`. Capturará UTM/gclid/fbclid y sólo los eventos enumerados en la especificación. No capturará contenido de inputs, mensajes, RUT, email, teléfono ni texto libre.

Antes de publicar: definir consentimiento, CSP, retención, política de privacidad, prueba Lighthouse y mecanismo de desactivación por sitio.
