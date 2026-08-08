# Reconciliación Mac ↔ Git ↔ servidor

Comparación SHA-256 sobre código/configuración, excluyendo runtime, dependencias, minificados y backups obvios. Evidencia completa protegida en `code-reconciliation.json/tsv` dentro del backup Mac.

| Estado | Archivos |
|---|---:|
| SAME_ALL | 446 |
| MAC_ONLY | 19 |
| GIT_ONLY | 8 |
| SERVER_ONLY | 53 |
| MAC_GIT_ONLY | 18 |
| MAC_SERVER_ONLY | 61 |
| Modificados Mac sin servidor | 1 |
| Modificados Mac/servidor sin Git | 2 |
| Tres versiones diferentes | 10 |
| Git=servidor, Mac diferente | 5 |
| Mac=Git, servidor diferente | 6 |
| Mac=servidor, Git diferente | 36 |

## Alertas

Los diez archivos con tres versiones incluyen `backend/main.py`, auth, cotizador, leads, agenda, quotes/tools y vistas críticas de leads/PDF/historial.

`SERVER_ONLY` contiene principalmente snapshots `pre-uat`, pero también assets SVG/stickers de las cuatro marcas y `scripts/git_commit_safe.sh`/`git_safe_status.sh`. Estos assets pueden ser funcionales y ya están preservados; deben revisarse e incorporarse explícitamente si son usados.

`MAC_ONLY` contiene documentación/deploy, auditores y archivos `.DS_Store`; sólo los primeros son candidatos a baseline.

61 archivos iguales Mac/servidor pero ausentes de HEAD demuestran código productivo terminado todavía no versionado correctamente. Esto es `CRITICAL_SERVER_ONLY_CODE` desde la perspectiva de Git, aunque exista copia Mac.

## Gate

`MAC_GIT_RECONCILIATION=PENDING` y `SERVER_CODE_RECONCILIATION=PENDING`. Ningún rsync ciego está autorizado. Cada grupo funcional debe probarse y commitearse explícitamente en el baseline.
