# Operación nocturna del CRM

## Horario diario

- 00:15: backup lógico de PostgreSQL y archivos del CRM en Ubuntu.
- 00:30: backup completo de la VM 100 en Proxmox, modo snapshot y compresión Zstandard.
- 01:15: apagado ordenado de la VM.
- 07:45: encendido de la VM, espera del QEMU Guest Agent y comprobación HTTP del CRM.

Proxmox permanece encendido. La VM nunca se apaga a la fuerza y el apagado se omite si existe un backup activo.

## Retención

- Backup lógico Ubuntu: 7 días.
- Backup completo Proxmox: 2 copias diarias.
- Snapshot `pre-automatizacion-20260729`: conservar hasta validar varios ciclos y tener backup en el SATA.

## Ubicaciones

- Backup lógico: `/opt/greendiamond/backups/nightly/`
- Backup completo: `/var/lib/vz/dump/`
- Log backup lógico: `/opt/greendiamond/backups/nightly.log`
- Log encendido/apagado: `/var/log/crm-vm-power.log`

## Comandos de comprobación

En Ubuntu:

```bash
crontab -l
systemctl is-active postgresql crm-gd.service
ls -lah /opt/greendiamond/backups/nightly/
```

En Proxmox:

```bash
qm status 100
qm listsnapshot 100
pvesm list local --content backup
pvesh get /cluster/backup --output-format json-pretty
tail -100 /var/log/crm-vm-power.log
```

## Recuperación

No restaurar ni revertir snapshots durante operación normal. Una restauración debe hacerse primero como VM de prueba con red aislada para validar base de datos, archivos y servicios antes de reemplazar la VM activa.
