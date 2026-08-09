# Guía de administración de integraciones

Esta guía está dirigida a Admin y Super Admin. La operación normal se realiza en **Settings → Integraciones** o **GD Intelligence → Integraciones**; no requiere terminal.

## Principio de seguridad

Una credencial sólo puede configurarse, reemplazarse o revocarse. Nunca puede consultarse, copiarse ni recuperarse desde el CRM, incluso con rol Super Admin. Si el valor original se pierde, se reemplaza.

## Configurar una API

1. Filtra por sitio y servicio.
2. Selecciona **Configurar**.
3. Ingresa la nueva credencial dos veces. El formulario siempre aparece vacío.
4. Selecciona **Probar y guardar**.
5. El CRM verifica al proveedor antes de cifrar y almacenar.

Una credencial rechazada no sustituye una conexión válida previa. El error se registra sin conservar el valor ingresado.

## Conectar Google

1. Selecciona **Conectar Google** en Analytics o Search Console.
2. Autoriza la cuenta cuando el entorno tenga OAuth habilitado.
3. Selecciona sitio, propiedad Analytics y propiedad Search Console.
4. Prueba y guarda la selección.

El CRM nunca solicita la contraseña de Google.

## Verificar

**Verificar conexión** realiza una comprobación real cuando la integración dispone de API. Para tags públicos analiza el sitio publicado. La tarjeta muestra última verificación, sincronización y último funcionamiento correcto.

## Reemplazar

Selecciona **Reemplazar**, ingresa sólo la credencial nueva y confirma. El CRM no precarga ni devuelve la anterior. Si la prueba falla, la credencial válida previa se conserva.

## Desconectar

1. Selecciona **Desconectar**.
2. Revisa el impacto y confirma.
3. El valor cifrado se revoca y deja de estar disponible para procesos internos.

Los datos históricos no se eliminan. Volver a conectar exige una credencial nueva.

## Diagnóstico

- **Falta configurar:** sigue la acción principal de la tarjeta.
- **Requiere atención:** revisa qué falta y ejecuta la verificación.
- **Error de conexión:** reintenta; si persiste, reemplaza o reautoriza.
- **Deshabilitado:** no se ejecutan nuevas sincronizaciones.

Nunca envíes credenciales por correo, chat, tickets o capturas. El historial administrativo registra usuario, integración, acción, fecha y resultado, pero no el valor.
