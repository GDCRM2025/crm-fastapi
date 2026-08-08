# Green Diamond CRM

Repositorio operativo del CRM Green Diamond, incluyendo backend FastAPI, frontend web, WABA y GD Intelligence.

## Inicio rápido

```bash
bash scripts/dev/bootstrap_mac.sh
bash scripts/dev/verify_environment.sh
```

La configuración real vive fuera de Git. `.env.example` contiene sólo nombres de variables; obtén valores desde el secret store aprobado.

## Arquitectura

- `backend/`: FastAPI, dominio CRM, routers e integraciones.
- `web/`: frontend operativo HTML/CSS/JavaScript.
- `frontend/`: frontend React/Vite complementario.
- `backend/gd_intelligence/`: Web Intelligence, permisos, conectores y servicios GD.
- `migrations/`: migraciones aditivas y auditadas.
- `tests/`: pruebas automatizadas.
- `deploy/`: configuración reproducible para Ubuntu/Proxmox.
- `docs/`: desarrollo, operación, seguridad, Git y recuperación.

## Seguridad

El repositorio debe permanecer privado. No se aceptan secretos, datos productivos, dumps, logs, uploads, conversaciones ni PDFs de clientes. Ejecuta Gitleaks antes de cada PR.

## Flujo

`feature/*` → tests → PR → `main` → SHA validado → deployment → healthcheck. Consulta `docs/git/GIT_WORKFLOW.md`.
