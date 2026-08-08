# Estrategia clean baseline

## Decisión

Se adopta estrategia **B: repositorio operativo nuevo con historia limpia**.

Motivos: secretos reales en historial, repositorio público, 577 MiB de objetos contaminados, datos/runtime/binarios, ramas muy divergentes y producción fuera de sincronía con Git.

## Procedimiento seguro

1. Mantener el repositorio actual y ambos bundles como legacy recuperable.
2. Cambiar inmediatamente la visibilidad del legacy a privada y archivarlo/read-only cuando el baseline esté validado.
3. Construir candidato en un directorio externo desde código reconciliado, no mediante copia ciega.
4. Incluir código, migraciones, tests, docs, assets necesarios y ejemplos de configuración.
5. Excluir secretos, datos, dumps, logs, uploads, caches, backups, dependencias y PII.
6. Ejecutar Gitleaks, tests, compile/build y smoke tests.
7. Crear commit inicial `chore(repo): establish clean production baseline`.
8. Crear repositorio GitHub **privado** nuevo o, sólo con estrategia aprobada, reemplazar el operativo preservando legacy.
9. Verificar `MAC_BASELINE_SHA == GIT_BASELINE_SHA`.
10. No desplegar hasta restore/migration/rollback gates.

No se ha creado ni publicado todavía el repositorio nuevo porque faltan resolver las diez divergencias críticas y los assets/scripts funcionales del servidor.
