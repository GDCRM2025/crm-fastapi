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

Configurar una base de desarrollo/fixture en `DATABASE_URL`. Nunca apuntar tests destructivos a producción.

```bash
source .venv/bin/activate
export DATABASE_URL='postgresql://USUARIO@127.0.0.1:PUERTO/BASE_DE_RESTORE_O_DEV'
export JWT_SECRET='SECRETO_LOCAL_NO_PRODUCTIVO'
uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

FastAPI sirve API y frontend en el mismo origen; Vite no es necesario para las vistas legacy/GD Intelligence. Las URLs correctas son:

- Login CRM: `http://127.0.0.1:8000/web/login.html`
- Panel autenticado: `http://127.0.0.1:8000/web/index.html`
- GD Intelligence: abrir **📡 GD Intelligence** desde el menú del panel. No abrir `gd_intelligence.html` mediante `file://`.

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
