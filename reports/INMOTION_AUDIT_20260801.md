# Auditoría y actualización de InMotion — 2026-08-01

## Alcance real

La auditoría se ejecutó sobre el CRM activo de InMotion en `/home/bf68ec5/crm`, conectado a su base de datos de producción. Las pruebas transaccionales crearon únicamente registros sintéticos identificables, los verificaron y los eliminaron al finalizar. No enviaron WhatsApp, correos ni eventos reales.

## Resultado

- Salud pública: correcta; OpenAPI respondió HTTP 200.
- Pruebas del núcleo después del despliegue: **32 de 32 aprobadas**.
- Rutas OpenAPI: 370 paths; cero identificadores de operación duplicados.
- Creación, consulta, edición, seguimiento, estados y eliminación lógica de leads: aprobados.
- Creación, versiones, productos, historial y PDF de cotizaciones: aprobados.
- Encuestas, plataformas configurables, permisos por marca y feature flags: aprobados.
- El seguimiento por llamada, WhatsApp y “no contesta” cambia el estado según las reglas corregidas.

## Integridad posterior

Conteos antes y después de las pruebas y del despliegue:

- Leads: 3.786.
- Cotizaciones: 3.084.
- Usuarios: 203.
- Encuestas de eventos: 21.

Los datos sintéticos eliminados fueron dos leads y dos cotizaciones. No cambió el total real.

## Cambios aplicados

- Se desplegó una lista explícita de archivos corregidos; no se hizo sincronización destructiva ni `--delete`.
- Se corrigieron rutas duplicadas, estados de seguimiento, autenticación de cotizaciones/PDF, encuestas, permisos, plataformas configurables y controles de registro.
- Se normalizaron 31 revisiones históricas con número de versión duplicado, sin borrar cotizaciones.
- Los respaldos de `.env` quedaron con permisos `0600`.

## Respaldos verificables

- Base de datos: `/home/bf68ec5/crm_backups/crm_db_20260801_204017.dump` (4,1 MB, modo `0600`).
- Código previo al despliegue: `/home/bf68ec5/crm_backups/deploy/pre_release_20260801_204342.tar.gz`.
- También se conservaron respaldos completos de código y contenido en `/home/bf68ec5/crm_backups/`.

## Riesgos pendientes obligatorios

1. Rotar la contraseña PostgreSQL desde cPanel y actualizar `.env`; una herramienta de diagnóstico llegó a imprimir la cadena de conexión durante una prueba fallida, por lo que debe considerarse expuesta.
2. Rotar llaves SSH y credenciales Google que estuvieron versionadas históricamente.
3. Probar restauración del dump en una base separada; crear un backup no sustituye una restauración comprobada.
4. InMotion utiliza un intercambio SSH sin protección poscuántica; solicitar al proveedor actualización cuando esté disponible.
5. Mantener InMotion como webhook público de Meta durante el piloto local para no depender todavía de Cloudflare Zero Trust ni exponer Ubuntu.

## Veredicto

InMotion queda funcional para operación y como referencia de migración. El lunes se debe copiar hacia Ubuntu, comparar nuevamente conteos por marca/estado/mes y ejecutar el mismo auditor antes de cambiar usuarios al servidor local.
