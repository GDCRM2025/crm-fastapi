# Rollback

Rollback normal: desactivar feature/integración, restaurar el tar del código anterior, reiniciar y ejecutar healthcheck/smoke tests. Conservar tablas y eventos append-only para investigación.

Si hay corrupción o incompatibilidad de datos, no improvisar SQL inverso. Aislar tráfico, preservar evidencia y restaurar el dump en una base/VM separada. Validar conteos y versión antes de promoverla. Registrar `previous_commit`, `new_commit`, backup, usuario, fecha, sitio, logs seguros y resultado.
