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
uvicorn backend.main:app --reload
npm --prefix frontend run dev
```

## Verificación

```bash
bash scripts/dev/verify_environment.sh
```

Incluye compilación Python, tests GD Intelligence, build frontend, `git diff --check` y secret scan cuando Gitleaks está instalado.

## Migraciones

Ejecutar primero contra un restore/base aislada. Confirmar backup, timeouts, idempotencia, conteos y rollback antes de considerar producción.

## VPN y producción

La VPN permite administración read-only/operativa explícita. El Mac no usa producción como base de desarrollo. Un deployment siempre parte de un commit validado.
