# Reconciliación final de gates productivos

Fecha de verificación: 2026-08-09  
Modo: auditoría read-only por VPN/SSH y APIs de solo lectura  
Servidor: `crm-gd` (`192.168.100.51`)  
Resultado: **BLOCKED_PREDEPLOY — PRODUCCIÓN NO MODIFICADA**

## Resumen ejecutivo

| Gate | Estado | Conclusión verificable |
|---|---|---|
| `PRIVATE_REPOSITORY` | **FAIL** | `GDCRM2025/crm-fastapi` continúa público; el clean baseline local no tiene remoto configurado. |
| `SECRET_ROTATION` | **FAIL** | El baseline limpio pasa Gitleaks, pero no existe evidencia de revocación/rotación en los proveedores de las credenciales legacy. |
| `SERVER_RECONCILIATION` | **FAIL** | El código productivo conocido está preservado o clasificado, pero el servidor sigue ejecutando un SHA legacy con un worktree no congelado: 54 entradas tracked y 5.965 untracked. |

Los tres gates deben estar en `PASS` antes de cualquier deployment. No se borró, movió, modificó ni copió desde producción ningún archivo.

## 1. Identidad productiva observada

| Campo | Evidencia |
|---|---|
| Host | `crm-gd` |
| Servicio | `crm-gd.service=active/running` |
| Working directory | `/opt/greendiamond/crm` |
| Proceso | Uvicorn ejecutando `backend.main:app` en loopback |
| Branch legacy | `feature/whatsapp-native-clean-20260806` |
| SHA productivo / rollback conocido | `ec43b363e52dc762b3b030ad421800f878ed6afc` |
| Inicio del proceso observado | 2026-08-07 17:25:26 -04:00 |
| Rutas OpenAPI observadas | 378 |

La sesión SSH funcionó mediante VPN. Todas las comprobaciones fueron de lectura.

## 2. Gate `PRIVATE_REPOSITORY`

### Evidencia actual

- La API pública de GitHub y la conexión GitHub autorizada devolvieron `visibility=public`, `private=false` para `GDCRM2025/crm-fastapi`.
- La sesión GitHub conectada tiene permiso administrativo sobre el legacy, pero el conector disponible no ofrece una operación segura para cambiar visibilidad ni crear el repositorio privado nuevo.
- `gh` no está instalado/autenticado en el Mac para completar esa operación por CLI.
- El repositorio clean baseline `/Users/oscarmendoza/Desktop/GreenDiamond-CRM` no tiene `origin` ni ningún otro remoto.
- El repositorio productivo tampoco informa remoto configurado.

### Decisión

`PRIVATE_REPOSITORY=FAIL`.

No se debe publicar el baseline en el legacy público. Para cerrar el gate se requiere una de estas acciones autorizadas y luego una verificación independiente:

1. Crear un repositorio nuevo privado para el clean baseline, configurar `origin`, publicar un SHA inmutable y comprobar `visibility=private`; o
2. Cambiar el legacy a privado, archivarlo/read-only y crear igualmente un repositorio operativo privado limpio.

La evidencia mínima de cierre debe incluir nombre del repositorio privado, visibilidad consultada por API, SHA local igual al SHA remoto, branch protection y secret scanning/push protection habilitados cuando el plan GitHub lo permita.

## 3. Gate `SECRET_ROTATION`

### Evidencia actual

- Gitleaks sobre los 13 commits del clean baseline: **PASS, 0 leaks**.
- El único nombre sensible coincidente versionado localmente es `.env.example`; no contiene credenciales reales.
- En el servidor siguen presentes, sin leer su contenido, las ubicaciones legacy ya inventariadas para runtime env, llave SSH, VAPID, credenciales Google, directorio `keys` y log de depuración.
- La presencia o fecha de un archivo no demuestra que el secreto haya sido rotado. No existe constancia verificable de revocación en Google, Meta, SSH/VPN, VAPID, correo, WABA/webhooks u otros proveedores afectados.

### Decisión

`SECRET_ROTATION=FAIL`.

El gate no puede cerrarse mediante cambios de código. Requiere rotar/revocar en cada proveedor, instalar los reemplazos únicamente en el secret store/`.env` protegido, reiniciar de forma controlada, verificar el servicio y registrar sólo metadatos no sensibles: sistema, responsable, fecha, identificador de versión, validación y estado de revocación. Nunca deben copiarse valores al repositorio, documentación, logs o respuestas API.

## 4. Reconciliación Ubuntu

### Estado Git real del servidor

| Clase | Conteo | Clasificación |
|---|---:|---|
| Entradas tracked con estado | 54 | 52 scripts agregados al índice y 2 vistas agregadas/modificadas |
| Tracked con contraparte local | 54 | Todas |
| Tracked byte-identical al baseline local actual | 54 | **PASS de preservación** |
| Untracked no ignorados | 5.965 | Mezcla de código, datos, documentos, binarios, runtime y snapshots |
| Ignorados | 6.284 | Runtime/dependencias/artefactos; no deben borrarse sin clasificación operativa |

La cifra de 5.965 reemplaza el conteo preliminar de 192: la medición final usa `git ls-files --others --exclude-standard`, sin truncar directorios.

### Clasificación de untracked

Principales grupos observados, sin leer secretos ni datos:

| Grupo superior | Archivos | Tratamiento |
|---|---:|---|
| `data/` | 5.204 | Datos/runtime/PII potencial; fuera de Git, conservar y migrar sólo mediante procedimiento de datos. |
| `web/` | 312 | Código/assets; comparado por checksum contra baseline. |
| `backend/` | 155 | Código/runtime; comparado con exclusiones de datos, credenciales, PDFs y backups. |
| `mobile/` | 122 | Producto separado; preservar, inventariar y decidir repositorio propio. |
| `frontend/` | 28 | Código candidato; preservar y clasificar fuera de un deployment backend. |
| `IMAGENES/` | 27 | Assets; preservar, no inferir uso por extensión. |
| `tools/` | 23 | Herramientas; preservar y revisar por dueño funcional. |
| Otros | 94 | Videos, extensiones, paquetes, temporales, documentos y configuración; conservar hasta clasificación explícita. |

Dentro de `backend/`, `web/`, `scripts/`, `deploy/` y `migrations/` se evaluaron 412 archivos fuente/configuración candidatos, excluyendo credenciales, datos, PDFs, dependencias, backups y runtime:

- 361 son byte-identical al baseline actual.
- 48 tienen contraparte local con una versión distinta seleccionada durante la reconciliación del baseline.
- 3 existen sólo en el servidor: `backend/modules/quotes/{models,routes,schemas}.py`.
- 364 coincidían exactamente con el commit raíz limpio `721a6e7`; 366 están representados byte a byte en el root limpio o en el baseline actual.

### Código aparentemente único

Los tres archivos `backend/modules/quotes/*` datan de 2025, no están importados desde otro módulo, su ruta `/quotes` no aparece en el OpenAPI productivo y duplican parcialmente el cotizador/quotes activo. Se clasifican como **legacy dormido, no incorporable sin una decisión funcional**. No constituyen evidencia de un hotfix productivo y no fueron copiados ni borrados.

Otros server-only observados:

- `backend/crm.bd`: base SQLite/runtime, no código; debe conservarse fuera de Git hasta confirmar propietario y retención.
- `web/downloads/LeadSuiteGD-extension.zip`: artefacto binario distribuible; debe preservarse y reconciliarse con el repositorio/versionado propio de la extensión.

El hotfix válido de espera/reintento de agenda identificado en la reconciliación original sí está presente en el baseline (`backend/routers/tools.py` y `web/views/leads.html`). El frontend conserva reintentos acotados ante conflicto `429`/agenda ocupada. Assets de marca y scripts Git seguros también están incorporados según `docs/git/BASELINE_MANIFEST.md`.

### Decisión

`SERVER_RECONCILIATION=FAIL` como gate de deployment, con estos subestados:

- `SERVER_CODE_PRESERVATION=PASS`: los 54 tracked productivos están preservados exactamente; los tres fuentes server-only están identificados como legacy dormido; los demás candidatos tienen contraparte o decisión de baseline documentada.
- `SERVER_UNCLASSIFIED_FILES=PENDING`: permanecen 5.965 untracked; ninguna limpieza está autorizada por extensión o antigüedad.
- `SERVER_IMMUTABLE_SHA=FAIL`: producción ejecuta `ec43b363…`, pero su contenido efectivo depende del worktree y no puede reproducirse sólo desde Git.
- `SERVER_CLEAN_BASELINE_DEPLOYED=FAIL`: el baseline limpio no fue desplegado, conforme al gate maestro.

Para cerrar este gate sin pérdida:

1. Crear un manifest firmado/checksummed de los 5.965 untracked y asignar cada grupo a `runtime/data`, `backup`, `artefacto`, `producto separado`, `legacy dormido` o `código a incorporar`.
2. Preservar fuera del árbol desplegable todos los grupos no código; no eliminar archivos no clasificados.
3. Resolver explícitamente el repositorio de `mobile`, `frontend`, extensiones y binarios distribuibles.
4. Congelar el clean baseline en un repositorio privado y repetir tests/secret scan.
5. Hacer backup nuevo de PostgreSQL y archivos con checksum.
6. Desplegar mediante lista explícita y migraciones aditivas; nunca mediante rsync destructivo.
7. Verificar que el servidor queda en un SHA conocido, sin código tracked/untracked ejecutable fuera de ese SHA.
8. Ejecutar smoke tests y conservar rollback al SHA/backup anterior.

## 5. Resultado de Production Readiness

```text
PRIVATE_REPOSITORY=FAIL
SECRET_ROTATION=FAIL
SERVER_RECONCILIATION=FAIL
SERVER_CODE_PRESERVATION=PASS
SERVER_UNCLASSIFIED_FILES=PENDING
SERVER_IMMUTABLE_SHA=FAIL
PRODUCTION_MUTATIONS=0
PRODUCTION_READINESS=FAIL
```

No se autoriza deployment mientras cualquiera de los tres gates críticos continúe en `FAIL`.
