# Prueba de restauración aislada — 2026-08-08

## Alcance

Se restauró el backup lógico productivo de las 00:15 en un clúster PostgreSQL 16.11 aislado en el Mac. No se usó la base activa, no se requirió sudo en producción y no se modificó ningún servicio de la VM.

- Backup origen: `/opt/greendiamond/backups/nightly/20260808-001501/database.dump`.
- SHA-256: `410e14eea6faaedc7d53240adc0812d091d4bffa0233cdef5cbd8fe5a903dbab`.
- Destino: `gd_restore_test_20260808` en clúster local aislado.
- Evidencia protegida: `/Users/oscarmendoza/Desktop/CRM_2025_safety_20260808_173955/pg-restore-test-20260808`.
- `pg_restore --exit-on-error --no-owner --no-privileges`: PASS.
- Clúster detenido después de validar; archivos preservados con permisos restrictivos.

## Objetos restaurados

| Objeto | Cantidad |
|---|---:|
| Tablas public | 112 |
| Índices public | 213 |
| Foreign keys | 34 |
| Secuencias | 91 |
| Funciones public | 2 |

## Conteos críticos del dump

| Tabla | Filas |
|---|---:|
| leads | 3.897 |
| cotizaciones | 3.252 |
| usuarios | 204 |
| marcas | 7 |
| whatsapp_channels | 4 |
| whatsapp_contacts | 1 |
| whatsapp_conversations | 1 |
| whatsapp_messages | 40 |
| whatsapp_webhook_events | 160 |

## Validaciones

- Checksum remoto vs copia: PASS.
- Inventario `pg_restore --list`: PASS.
- Restore sin errores: PASS.
- Tablas core, constraints, índices, secuencias y funciones: PASS.
- Entidades leads/cotizaciones/usuarios/marcas: PASS.
- Tablas WABA: PASS.

`RESTORE_GATE=PASS`.
