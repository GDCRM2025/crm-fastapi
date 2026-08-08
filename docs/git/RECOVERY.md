# Recuperación

## Pérdida del Mac

Clonar el repositorio privado, ejecutar bootstrap, recuperar secretos desde el store, usar base de desarrollo y ejecutar verificación completa.

## GitHub inaccesible

Restaurar/clonar el último bundle verificado en una ubicación nueva. No sobrescribir copias existentes. Verificar refs y checksums antes de trabajar.

## Working tree eliminado/corrupto

Preservar el disco/estado restante. Recuperar desde commit/bundle; aplicar parches staged/unstaged y copiar untracked desde snapshot en un directorio nuevo. Comparar antes de promover.

## Branch corrupta

Crear una rama de recuperación desde el último SHA bueno. No reescribir la única referencia. Cherry-pick de commits comprobados después de backup.

## Servidor perdido

Restaurar o reconstruir VM, instalar runtime, clonar/deployar SHA aprobado, restaurar PostgreSQL lógico validado, cargar secretos y ejecutar healthchecks/conteos.

## Deployment malo

Deshabilitar feature si es posible, volver al rollback SHA/artefacto anterior, reiniciar y ejecutar smoke tests. Conservar tablas append-only y evidencia; no improvisar SQL destructivo.

## Secreto filtrado

Revocar/rotar en proveedor, actualizar secret store/servidor, revisar accesos y logs, ejecutar secret scan, eliminar del baseline y documentar incidente sin incluir valores. Borrar un archivo no revoca el secreto.

## Base de datos

Restaurar primero en base/VM aislada. Validar objetos, conteos y aplicación. Nunca restaurar directamente sobre producción sin procedimiento y ventana aprobados.
