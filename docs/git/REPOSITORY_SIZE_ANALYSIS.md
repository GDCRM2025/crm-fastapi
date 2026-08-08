# Análisis de tamaño

## Estado inicial

- Working tree: 1,9 GiB.
- Object database Git: 577,51 MiB packed + 14,26 MiB loose.
- Directorios principales: `.git` 593 MiB, `venv` 296 MiB, `data` 233 MiB, `.venv` 224 MiB, `frontend` 202 MiB, `VIDEOS` 135 MiB, `mobile` 120 MiB y `web` 81 MiB.

## Objetos históricos principales

- Video de login: ~103 MB.
- Videos MOV: ~86 MB y ~50 MB.
- PDFs de cotizaciones: hasta ~31 MB por archivo y múltiples duplicados.
- ZIP/RPM históricos.
- Virtualenvs macOS y Linux (`.venv`, `env39`) completos.
- Cache/log Expo y datos/caches de cotización.

## Objetivo baseline

Excluir virtualenvs, dependencias, runtime, PDFs/clientes, backups, logs, dumps y videos de evidencia. Mantener únicamente assets de aplicación realmente utilizados; optimizarlos en una fase separada. No borrar objetos del repositorio legacy: el ahorro se materializará en un repositorio clean-baseline nuevo.
