# Workflow Git oficial

## Fuente de verdad

Mac desarrolla y prueba; GitHub privado versiona; Ubuntu ejecuta un SHA conocido. Datos, VM y secretos tienen backups/mecanismos separados.

## Trabajo normal

```bash
git fetch origin
git switch main
git pull --ff-only
git switch -c feature/nombre-claro
```

Modificar, testear y revisar:

```bash
bash scripts/dev/verify_environment.sh
git status
git diff --check
git add ruta/explicita1 ruta/explicita2
git commit -m "feat(area): descripción"
git push -u origin feature/nombre-claro
```

Abrir PR hacia `main`. Requerir tests, secret scan y revisión. `main` debe permanecer deployable. No crear `develop` sin necesidad demostrada.

## Prohibiciones

- Nunca `git add .` o `git add -A`.
- Nunca force push, hard reset, clean destructivo o restore global sin backup/plan explícito.
- Nunca secretos, `.env`, llaves, dumps, logs, uploads, conversaciones, PDFs de clientes o runtime.
- Nunca desarrollar permanentemente en producción.

## Deployment

1. Registrar SHA aprobado de `main`.
2. Crear backup de datos/código y confirmar rollback SHA.
3. Desplegar artefacto/commit exacto.
4. Aplicar migración aprobada.
5. Healthcheck, smoke tests y conteos.
6. Registrar branch, SHA, fecha, actor y resultado.

Objetivo: `GIT_DEPLOY_SHA == SERVER_DEPLOY_SHA`.

## Hotfix

Crear `hotfix/*`, testear, commit, desplegar SHA, healthcheck y PR inmediato a `main`. Nunca dejar el cambio sólo en servidor.
