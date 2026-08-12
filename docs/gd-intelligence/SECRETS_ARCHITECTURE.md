# Arquitectura de secretos e integraciones

## Estado operativo

El CRM separa tres capas:

1. PostgreSQL conserva configuración persistente, IDs públicos, estados y auditoría.
2. `CredentialVault` conserva secretos operacionales cifrados y nunca ofrece lectura por API.
3. Dos secretos bootstrap —URL de PostgreSQL y llave maestra del Vault— se cargan fuera del release.

La precedencia de carga es fija: `systemd LoadCredential=` → archivo protegido → variable de entorno legacy. Los releases contienen sólo código.

## Ubuntu

Nombres esperados por `LoadCredential=`:

- `database_url`
- `credential_vault_key`

Si aún no existe autoridad para editar systemd, el fallback soportado es:

- `/opt/greendiamond/shared/secrets/bootstrap/database_url`
- `/opt/greendiamond/shared/secrets/bootstrap/credential_vault_key`

El directorio debe tener modo `0700` y los archivos `0600`. El instalador seguro es:

```bash
/opt/greendiamond/venv/bin/python scripts/security/install_bootstrap_credentials.py \
  --env-file /opt/greendiamond/shared/secrets/crm.env \
  --target-dir /opt/greendiamond/shared/secrets/bootstrap
```

El instalador nunca imprime valores. Preserva archivos existentes y sólo crea una llave nueva cuando el Vault tiene cero registros configurados. Si existen registros y falta la llave válida, termina con error.

Para migrar a systemd, un administrador del host debe enlazar ambos archivos mediante `LoadCredential=` (o `LoadCredentialEncrypted=`), ejecutar `systemctl daemon-reload` y reiniciar `crm-gd.service`. El backend detecta `CREDENTIALS_DIRECTORY` automáticamente.

## Control de acceso temporal

GD Intelligence, Paid Media e Intelligence Platform están bloqueados en backend para cualquier rol distinto de `SUPERADMIN`. El menú aplica el mismo criterio. RBAC interno permanece preparado, pero no amplía este gate temporal.

## Reglas de operación

- Nunca pegar secretos en frontend, documentación, logs o Git.
- Al reemplazar una credencial, el valor previo se sobreescribe cifrado y el evento queda auditado.
- Desconectar revoca el valor sin borrar históricos.
- Antes de cada arranque, el CRM verifica que todos los registros activos puedan descifrarse; si no, el proceso falla cerrado.
- La ruta autenticada `GET /api/gd-intelligence/bootstrap/status` devuelve sólo fuente, conteos y resultado del chequeo.
- Las credenciales legacy se adoptan una por una, sólo después de validarlas con el proveedor. WhatsApp/WABA se migra al final y no se toca durante este bootstrap.
