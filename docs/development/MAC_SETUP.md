# Preparación de un Mac

## Requisitos

- Git.
- Python 3.12 compatible con `requirements.txt`.
- Node.js/npm compatible con los lockfiles.
- PostgreSQL 16 sólo para restores/tests aislados cuando sea necesario.
- Gitleaks para comprobaciones locales.

## Instalación

```bash
git clone <REPOSITORIO_PRIVADO> GreenDiamond-CRM
cd GreenDiamond-CRM
bash scripts/dev/bootstrap_mac.sh
```

El bootstrap crea `.venv`, instala dependencias y copia `.env.example` a `.env` con permisos restrictivos. Los valores se obtienen del secret store aprobado; nunca de Git, Slack, documentos o logs.

## Ejecución local

Configurar una base de desarrollo/fixture en `DATABASE_URL` y su directorio en `GD_LOCAL_PGDATA`. Nunca apuntar tests destructivos a producción. Ambos valores viven en `.env`, que está ignorado por Git. El arranque rechaza hosts no-loopback y nombres de base que no indiquen `test`, `restore`, `dev` o `local`.

```bash
bash scripts/dev/start_mac_local.sh --foreground
```

El comando levanta PostgreSQL 16 si está detenido, aplica todas las migraciones GD idempotentes —incluyendo tracking y atribución— e inicia FastAPI en primer plano. Mantener esa terminal abierta; el entorno está disponible cuando Uvicorn informa que escucha en `127.0.0.1:8000`. Para automatizaciones locales también existe el modo sin `--foreground`, que espera `/healthz`, imprime `MAC_LOCAL_READY` y deja logs/PID bajo `runtime/mac/` (ignorado por Git).

Para detener FastAPI o todo el entorno:

```bash
bash scripts/dev/stop_mac_local.sh
bash scripts/dev/stop_mac_local.sh --postgres
```

FastAPI sirve API y frontend en el mismo origen; Vite no es necesario para las vistas legacy/GD Intelligence. Las URLs correctas son:

- Login CRM: `http://127.0.0.1:8000/web/login.html`
- Panel autenticado: `http://127.0.0.1:8000/web/index.html`
- GD Intelligence: abrir **📡 GD Intelligence** desde el menú del panel. No abrir `gd_intelligence.html` mediante `file://`.
- Integration Center: dentro de GD Intelligence, abrir **Integration Center** desde su submenú o pestaña.

Para una prueba de autenticación completamente limpia, incluso si el navegador conserva un JWT anterior, abrir una vez `http://127.0.0.1:8000/web/login.html?reset_session=1`. El parámetro sólo elimina la sesión local del navegador y deja visible el formulario; no desactiva auth.

El frontend deriva el prefijo API desde la ruta (`/` o `/crm`) y no contiene IPs, hosts ni URLs productivas hardcodeadas.

Para crear una cuenta exclusivamente en una base local cuyo nombre incluya `test`, `restore`, `dev` o `local`:

```bash
python scripts/dev/create_local_admin.py
```

El script rechaza hosts que no sean loopback y solicita la contraseña sin imprimirla.

## PostgreSQL aislada validada el 2026-08-08

La restauración aislada se ejecuta en el puerto `55432`. Con FastAPI conectado a `gd_restore_test_20260808` se verificó:

- `/healthz`: HTTP 200.
- `/login`: HTTP 200 con fixture local autenticado.
- `/api/gd-intelligence/permissions/me`: HTTP 200.
- `/api/gd-intelligence/overview`: HTTP 200, cuatro sitios.
- `/api/gd-intelligence/web/sites`: HTTP 200, CAM/EXP/GOU/DEL.
- `/api/gd-intelligence/campaigns/utm`: HTTP 200.
- `/api/gd-intelligence/permissions/roles`: HTTP 200, cuatro roles.
- `/api/gd-intelligence/web/site-health/latest`: HTTP 200.

Resultado: `LOCAL_API_CONNECTIVITY=PASS`. Producción no fue consultada ni modificada para esta validación.

## Verificación

```bash
bash scripts/dev/verify_environment.sh
```

Incluye compilación Python, tests GD Intelligence, build frontend, `git diff --check` y secret scan cuando Gitleaks está instalado.

## Migraciones

Ejecutar primero contra un restore/base aislada. Confirmar backup, timeouts, idempotencia, conteos y rollback antes de considerar producción.

## VPN y producción

La VPN permite administración read-only/operativa explícita. El Mac no usa producción como base de desarrollo. Un deployment siempre parte de un commit validado.
