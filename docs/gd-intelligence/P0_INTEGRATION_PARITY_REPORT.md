# P0 Integration Center — informe de paridad productiva

Fecha: 2026-08-12  
Release anterior: `8600cc2c4e51f7abc52ba362670f43af0016c276`  
Release activo: `babfea832fcbbcc182e7779fbd0fe359a239c050`

## Diagnóstico y corrección

La base contenía 32 filas seed en `NOT_CONFIGURED` y el discovery público no se había ejecutado después del cutover inmutable. Además, la UI reducía instalación, credencial, API, datos y salud a un único conteo `CONNECTED / total`.

La corrección introduce estados canónicos y dimensiones independientes, ejecuta discovery real sin credenciales, conserva el último estado bueno ante fallos temporales y exige un Pixel ID numérico para detectar Meta. No se fabricaron datos ni conexiones API.

## Evidencia productiva

- Sitios activos: CAM, DEL, EXP y GOU; 4/4.
- GTM: detectado 4/4.
- GA4: detectado 4/4; API pendiente de autorización.
- GD Tracker: detectado 4/4; todavía sin sesiones/eventos reales.
- Meta Pixel: detectado 3/4 mediante ID público numérico.
- Search Console: una verificación pública detectada; API pendiente.
- Site Health: servicio operativo 4/4, HTTP 200 y HTTPS; los cuatro sitios tienen mejoras accionables.
- QA visual autenticada: Resumen e Integraciones muestran estados, detalle y acciones coherentes; PASS.
- Seguridad: SUPERADMIN 200, ADMIN 403; respuestas sin campos secretos; PASS.

## Deployment

Backup previo: `/opt/greendiamond/backups/parity/20260812_124421`.

| Gate | Resultado |
|---|---|
| Backup de release y PostgreSQL | PASS |
| Checksums y `pg_restore --list` | PASS |
| Migración aditiva aplicada dos veces | PASS |
| Tests | 122 PASS / 0 FAIL |
| Compile, frontend, JS y Gitleaks | PASS |
| Candidato aislado y smoke | PASS |
| Cutover atómico | PASS |
| Smoke post-cutover | PASS |
| Rollback listo | PASS: `8600cc2` |
| Rollback ejecutado | NO |

## Estado obligatorio

```text
PRODUCTION_SHA=babfea832fcbbcc182e7779fbd0fe359a239c050
ROLLBACK_SHA=8600cc2c4e51f7abc52ba362670f43af0016c276
GA4_INSTALLATION=DETECTED_4_OF_4
GA4_API=READY_FOR_CREDENTIAL
SEARCH_CONSOLE_API=READY_FOR_CREDENTIAL
GOOGLE_ADS=READY_FOR_CREDENTIAL
META_ADS=READY_FOR_CREDENTIAL
TRACKING_INSTALLATION=DETECTED_4_OF_4
TRACKING_DATA=NO_DATA
INSTALLATIONS_DETECTED=16
APIS_CONNECTED=0
READY_FOR_CREDENTIAL=20
REQUIRES_ATTENTION=4
NOT_CONFIGURED=4
ERRORS=0
CONFIG_PROGRESS=60%
SITE_HEALTH_SERVICE=OPERATIONAL_4_OF_4
SITE_HEALTH_FINDINGS=REQUIRES_ATTENTION_4_OF_4
INTEGRATION_CENTER_PARITY=PASS
SUPERADMIN_VISIBILITY=PASS
TESTS_TOTAL=122
TESTS_FAIL=0
ROLLBACK_READY=PASS
ROLLBACK_EXECUTED=NO
```

Repositorio privado y rotación de secretos legacy continúan como gates externos independientes. No se limpiarán artefactos históricos antes de revocar/reemplazar y verificar las credenciales con cada proveedor.
