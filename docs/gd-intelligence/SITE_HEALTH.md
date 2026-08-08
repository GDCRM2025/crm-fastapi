# Site Health

## Alcance implementado

Site Health comprueba cada sitio habilitado desde el backend y conserva historial en `wi_site_health_runs`. La UI muestra estado, HTTP, latencia y señales técnicas: HTTPS, robots, sitemap, canonical, title, meta description, H1, viewport, schema.org, página 404, redirects, enlaces rotos básicos, contenido mixto y atributos `alt` faltantes.

La descarga está protegida contra destinos privados/loopback, limita tamaño, redirects y enlaces internos. Ningún resultado ausente se inventa.

## API y permisos

- `GET /api/gd-intelligence/web/site-health/latest`: permiso `web_intelligence_view`.
- `GET /api/gd-intelligence/web/site-health/history`: permiso `web_intelligence_view`.
- `POST /api/gd-intelligence/web/site-health/run`: permiso `web_intelligence_performance` y registro de auditoría.

## Ejecución periódica

El job manual/local es:

```bash
.venv/bin/python scripts/jobs/run_site_health.py
```

Los archivos `deploy/ubuntu/gd-site-health.service` y `gd-site-health.timer` son plantillas listas para el layout actual `/opt/greendiamond`. No están instalados ni habilitados en producción. Antes de hacerlo se deben cumplir los gates de deployment, confirmar usuario/grupo, `EnvironmentFile`, ruta del virtualenv y probar primero contra un entorno aislado.

## Validación local 2026-08-08

Se analizaron CAM, EXP, GOU y DEL desde FastAPI contra PostgreSQL aislada. Los cuatro respondieron HTTP 200 y quedaron en estado `WARNING` por hallazgos accionables (principalmente imágenes sin `alt`; además canonical ausente en GOU y un enlace básico fallido en EXP). La ejecución programable también terminó con cuatro resultados almacenados.

Producción no fue consultada, migrada ni modificada.
