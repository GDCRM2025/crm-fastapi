# Inventario del repositorio

| Categoría | Ubicación / estado |
|---|---|
| CORE CRM | `backend/core`, `backend/routers`, `backend/models`, `backend/modules` |
| WABA | routers `whatsapp_*`, helpers y tablas existentes |
| RBAC | `backend/core/rbac.py`, permisos router y UI settings |
| GD Intelligence | `backend/gd_intelligence`, router, migraciones, tests y docs; committed |
| Frontend operativo | `web/` HTML/CSS/JS |
| Frontend secundario | `frontend/` React/Vite |
| Mobile/extensiones | `mobile/`, `extension/`, `LeadSuiteGD-*` |
| Deployment | `deploy/`, scripts de deployment y Passenger |
| Tests | `tests/` y scripts de auditoría |
| Runtime/datos | `data/`, logs, SQLite, cotizaciones PDF, uploads/caches |
| Generados | `node_modules`, `.venv`, `venv`, `.expo`, dist, caches |
| Backups/temporales | `backups`, `tmp`, `*.bak*`, `*.dump`, ZIP/RPM |
| Secretos | `.env*`, `keys`, credentials, private keys, OAuth/service accounts |

## Clasificación

- Código fuente necesario debe pasar al clean baseline.
- Assets estáticos de marca requeridos por la UI deben conservarse y optimizarse, no confundirse con runtime.
- PDFs de clientes, logs, dumps, caches y datos productivos no pertenecen a Git.
- Scripts únicos del servidor se revisan antes de incorporar.
- Backups `pre-uat`/`bak` se conservan en el snapshot de seguridad, no en el baseline operativo.
- Los dos virtualenvs, dependencias Node y cache Expo son regenerables.
