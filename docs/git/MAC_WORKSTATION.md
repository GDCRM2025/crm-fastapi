# Estación Mac

- Ruta oficial auditada: `/Users/oscarmendoza/Desktop/CRM 2025`.
- Tamaño total: 1,9 GiB; `.git`: 593 MiB.
- Disco disponible al inventario: 167 GiB.
- Rama de desarrollo: `feature/gd-intelligence`.
- Development SHA: `ae50d7e0`.
- Remote: `origin` → `GDCRM2025/crm-fastapi`.
- Worktrees: uno.
- Python: virtualenvs locales; Node/npm disponible; Gitleaks 8.30.1 instalado.

El Mac conserva el código completo y un backup externo verificado. No utiliza `DATABASE_URL` productiva localmente. Las consultas productivas read-only se realizan explícitamente por SSH/VPN en la VM.

## Gate

`MAC_CODE_BACKUP=PASS`. `MAC_BASELINE_SHA == GIT_BASELINE_SHA` permanece pendiente hasta crear el repositorio privado limpio.
