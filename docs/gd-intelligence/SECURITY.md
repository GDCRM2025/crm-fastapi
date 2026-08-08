# Seguridad

- JWT y permisos se validan en backend en cada endpoint.
- Denegación por defecto; deploy/rollback requieren grants independientes y auditoría.
- Secretos en environment/store protegido `0600`; rotación y separación por ambiente.
- SQL parametrizado, paginación, límites de payload y timeouts.
- Contexto IA mínimo y autorizado; no usar conversaciones fuera del propósito empresarial.
- Tracking no captura campos ni PII; retención configurable.
- No shell, DB directa, migraciones ni secretos desde el CRM.
- Incidentes: deshabilitar integración, rotar secreto, preservar logs/auditoría, evaluar alcance y restaurar sólo mediante procedimiento probado.
