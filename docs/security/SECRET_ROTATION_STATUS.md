# Estado de rotación de secretos

Escaneo: Gitleaks 8.30.1, working tree e historial, redacción 100%. Los reportes completos están fuera del repositorio en el backup Mac protegido.

## Resultado

- Historial: 9 hallazgos (`private-key`: 7; `generic-api-key`: 2).
- Working tree: 16 hallazgos (`private-key`: 7; `generic-api-key`: 9).

| Tipo | Ubicación histórica/actual | Estado |
|---|---|---|
| Llaves SSH privadas | `data/id_rsa*` | FOUND_PENDING_ROTATION / PENDING_USER_ACTION |
| Google service account/OAuth | `backend/credentials`, `keys/sa.json`, `keys/gcal_oauth.json`, `data/drive.json`, `data/sa1.json` | FOUND_PENDING_ROTATION / PENDING_USER_ACTION |
| VAPID private key | `data/vapid_private_key.pem` | FOUND_PENDING_ROTATION / PENDING_USER_ACTION |
| API/webhook/app secrets | `.env`, `.env.backup.*` | FOUND_PENDING_ROTATION / PENDING_USER_ACTION |
| Credenciales en debug log | `data/debug/cotizador_500.log` | FOUND_PENDING_ROTATION / revisar origen |

No se documentan valores. Eliminar archivos actuales no revoca credenciales históricas. Deben rotarse/revocarse en cada proveedor y actualizarse únicamente en el secret store/`.env` protegido del servidor.

## Acción crítica GitHub

El repositorio es PUBLIC. Debe cambiarse inmediatamente a PRIVATE desde GitHub Settings → General → Danger Zone → Change repository visibility. Después: invalidar forks/caches cuando sea posible, rotar secretos, configurar secret scanning/push protection y branch protection. La integración disponible no permite efectuar este cambio de visibilidad de forma segura.

`SECRET_SCAN=FAIL` hasta rotación y baseline limpio. No hacer nuevos pushes al repositorio público.
