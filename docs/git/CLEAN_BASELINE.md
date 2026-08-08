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

## Resultado verificado

- Repositorio local limpio: `/Users/oscarmendoza/Desktop/GreenDiamond-CRM`.
- Branch: `main`.
- Commit raíz: `721a6e7d90824189354d450e3f0277aecab3795f`.
- Remotos configurados: ninguno.
- Bundle recuperable: `clean-baseline-721a6e7.bundle`.
- SHA-256 del bundle: `9cfe41ae9609e0241f02f0a7863940e7f5eba05b92e69a03c14a2037359c96b6`.
- `git bundle verify`, `git fsck --full --strict` y Gitleaks sobre el historial: PASS.
- Las diez divergencias críticas y los assets/scripts funcionales del servidor fueron reconciliados antes del commit.

El baseline no se ha publicado. Debe permanecer sin remoto hasta crear y verificar un repositorio GitHub nuevo y privado y completar la rotación de credenciales del legacy.
