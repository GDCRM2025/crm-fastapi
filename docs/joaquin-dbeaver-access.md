# Acceso DBeaver · Joaquín Salamanca

## Conexión

1. Activar primero el perfil WireGuard `joaquin_analytics` en el notebook.
2. Crear una conexión PostgreSQL en DBeaver con estos valores:

| Campo | Valor |
|---|---|
| Host | `127.0.0.1` (destino visto desde el túnel SSH) |
| Puerto | `5432` |
| Base de datos | `bf68ec5_crm2025b` |
| Usuario | `joaquin_analytics` |
| Contraseña | Entrega privada de Gerencia; no guardarla en este documento |
| SSL | `prefer` |
| Schema activo | `analytics` |

En la pestaña **SSH**, activar el túnel y configurar:

| Campo SSH | Valor |
|---|---|
| Host | `10.77.0.1` |
| Puerto | `22` |
| Usuario | `oscar` |
| Autenticación | Llave pública |
| Llave privada | `joaquin-dbeaver-ed25519` |
| Passphrase | Vacía |

La llave está restringida en el servidor a reenviar exclusivamente hacia
`127.0.0.1:5432`; no entrega terminal ni permite otros destinos. El usuario de
base de datos es `joaquin_analytics`, no `usr_joaquinsal`.

En **Driver properties**, mantener `connectTimeout=10` y `socketTimeout=60`.
En **Connection settings → Initialization**, no agregar comandos de escritura.

## Permisos efectivos

- La sesión está forzada a `default_transaction_read_only=on`.
- `search_path=analytics, pg_catalog`.
- `statement_timeout=60s`, `lock_timeout=5s` e `idle_in_transaction_session_timeout=5min`.
- Tiene `CONNECT` sobre la base y `USAGE` sobre `analytics`.
- No tiene `USAGE`, `CREATE` ni `SELECT` sobre `public`.
- No tiene privilegios de superusuario, creación de roles ni creación de bases.

## Prueba de aceptación

Ejecutar en DBeaver:

```sql
SELECT current_user, current_database(), current_schema(),
       current_setting('transaction_read_only') AS read_only,
       current_setting('search_path') AS search_path;

SELECT table_name
FROM information_schema.views
WHERE table_schema='analytics'
ORDER BY table_name;
```

Debe devolver `joaquin_analytics`, la base `bf68ec5_crm2025b`, modo `on` y exclusivamente vistas del schema `analytics`.

Una escritura debe fallar:

```sql
CREATE TABLE analytics.prueba_no_permitida(id integer);
```

## Acceso de red aplicado

El peer de Joaquín es `10.77.0.13/32`. El firewall conserva PostgreSQL cerrado
a conexiones WireGuard directas. DBeaver entra mediante SSH por el puerto 22 y
la llave restringida reenvía únicamente al PostgreSQL local.

Las vistas se instalan con privilegios PostgreSQL administrativos:

```bash
sudo -u postgres psql bf68ec5_crm2025b \
  -f /opt/greendiamond/current/scripts/joaquin_analytics_views.sql
```

Luego validar desde el servidor:

```bash
sudo wg show wg0
sudo -u postgres psql bf68ec5_crm2025b -c \
  "SELECT table_name FROM information_schema.views WHERE table_schema='analytics';"
```
