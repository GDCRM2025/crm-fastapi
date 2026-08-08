# Deployment controlado

1. Resolver sitio, branch y commit exactos; confirmar diff y responsable.
2. Ejecutar tests/compilación/build y revisar secretos.
3. Crear dump lógico verificable y backup de archivos; confirmar rollback.
4. Aplicar migraciones aditivas primero en restore/staging.
5. Desplegar lista explícita de archivos, nunca sincronización destructiva.
6. Aplicar migración con timeouts conservadores.
7. Reiniciar proceso, ejecutar `/healthz`, OpenAPI, login y smoke tests core/GD.
8. Comparar conteos, errores, latencia y locks; registrar commit, actor, backup y resultado.

GD Intelligence no expone shell ni despliega directamente hasta que las fases 18–20 implementen aprobación y auditoría completas.
