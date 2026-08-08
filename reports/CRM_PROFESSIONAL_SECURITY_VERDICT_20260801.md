# Veredicto técnico CRM Green Diamond — 2026-08-01

## Resultado ejecutivo

El núcleo del CRM queda como **candidato apto para piloto controlado**, no todavía como cierre definitivo de producción. La auditoría local ejecutó 32 comprobaciones y obtuvo 32 aprobadas. Los datos sintéticos fueron eliminados al terminar.

No se enviaron mensajes reales, correos, eventos de calendario ni llamadas a Meta durante la auditoría. Esos flujos requieren credenciales y una prueba controlada desde el servidor de oficina.

## Cobertura comprobada

- Rechazo de acceso anónimo y permisos de ejecutivo por marca.
- Creación, lectura, modificación, seguimiento, cambio de estado y eliminación lógica de leads.
- Creación, lectura, productos, historial, plantilla PDF y revisión de cotizaciones.
- Creación, envío registrado, respuesta, clic en reseña y resumen de encuestas.
- Catálogos y plataformas configurables.
- Compilación Python, validación JavaScript, build React y auditoría npm.
- Inventario de 456 rutas API: 223 GET, 163 POST, 38 PUT, 27 DELETE y 5 PATCH.

Evidencia automatizada: `reports/crm_core_audit_latest.json`.

## Problemas importantes corregidos

- Colisión del número de cotización entre marcas y revisiones.
- Transacciones PostgreSQL abortadas por esquemas heredados en productos y encuestas.
- Campos opcionales ausentes que impedían listar leads.
- Vista/descarga de PDF desde historial sin autenticación consistente.
- Registro inseguro con RUT como contraseña: ahora exige clave propia de 12 caracteres y lista autorizada por defecto.
- Credenciales PostgreSQL predeterminadas eliminadas del código.
- Webhooks de Instagram pasan a fallar de forma segura si faltan secretos.
- Dependencias npm: 0 vulnerabilidades en producción y desarrollo.
- Llaves SSH y credenciales Google heredadas retiradas del control de versiones sin borrar las copias locales.

## Acceso remoto preparado

### VPN privada (decisión actualizada)

NetBird Cloud Free será la puerta privada al CRM para administración y ejecutivos. No necesita abrir un puerto del router. La cuenta empresarial quedó creada con capacidad de cinco usuarios y 100 equipos, sin tarjeta. Tailscale Personal se descartó porque su plan gratuito se limita oficialmente a uso no comercial.

Archivos:

- `deploy/ubuntu/setup-netbird-crm.sh`
- `deploy/ubuntu/MANUAL_VPN_NETBIRD_EJECUTIVOS.md`

### Cloudflare Tunnel

Cloudflare DNS quedó preparado, pero Zero Trust no se activó porque su checkout exige tarjeta incluso para el plan gratuito. Durante el piloto, InMotion conservará el webhook público de Meta y el CRM local seguirá privado.

Archivos:

- `deploy/ubuntu/setup-cloudflare-webhook.sh`
- `deploy/ubuntu/crm-webhook-only.nginx`

## Condiciones obligatorias antes de declarar producción

1. Ejecutar el despliegue en `crm-gd`, aplicar la migración y repetir el auditor contra una copia/restauración verificable.
2. Comparar conteos y muestras por estado, marca y mes entre InMotion y el servidor local.
3. Instalar NetBird en Ubuntu, invitar los tres correos individuales y probar desde datos móviles.
4. Mantener y verificar el webhook de Meta en InMotion durante el piloto; no cambiarlo a Ubuntu todavía.
5. Completar secretos de Meta y realizar un mensaje entrante y uno saliente con un número de prueba.
6. Cambiar la contraseña PostgreSQL del servidor y confirmar que `.env` tenga permisos `0600`.
   Rotar además las llaves SSH y credenciales Google que estuvieron presentes en el historial Git.
7. Probar restauración real del backup y snapshot, no solamente su creación.
8. Mantener servidor, Ubuntu y proceso CRM encendidos 24/7 si Meta apunta al webhook local.

## Mejoras siguientes para nivel profesional

- Unificar los dos módulos de conexión a base de datos existentes.
- Incorporar rate limiting persistente para login y recuperación de contraseña.
- Agregar pruebas automatizadas de agenda, finanzas, RRHH, inventario y permisos por rol.
- Centralizar logs, alertas de salud, espacio en disco, backup fallido y webhook caído.
- Definir una matriz formal de roles/permisos y exigir segundo factor a administradores.
- Separar staging de producción para que futuras pruebas nunca usen BDGD directamente.

## Decisión

**Sí para piloto el lunes**, primero con Oscar y una ejecutiva Gourmet, por NetBird. **No para migración total ni apertura masiva** hasta aprobar las ocho condiciones anteriores.
