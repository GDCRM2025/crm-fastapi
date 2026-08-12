# Incidente: descarga de PDF de cotización

Fecha: 2026-08-12  
Severidad: P0 operativa  
Estado: RESUELTO

## Impacto

La cotización se guardaba en PostgreSQL, pero la descarga automática y la apertura desde Historial fallaban durante la generación del PDF.

## Causa raíz

`quotes_override.py` construía la salida en `<release>/data/quotes`. Los releases Ubuntu son inmutables y de solo lectura, por lo que `mkdir` terminaba en `PermissionError`.

## Corrección

Se añadió un resolver único de almacenamiento persistente. En Ubuntu el destino es `/opt/greendiamond/shared/persistent-data/data`; en desarrollo local continúa siendo `<proyecto>/data`. Cotizaciones, adjuntos de lead, caché de assets y debug del generador comparten la misma raíz escribible.

## Verificación

```text
PRODUCTION_SHA=ba89090c6d9d7c12a97fe638ad5ab9f16c3df58d
ROLLBACK_SHA=babfea832fcbbcc182e7779fbd0fe359a239c050
TESTS_TOTAL=125
TESTS_FAIL=0
CANDIDATE_PDF_HTTP=200
CANDIDATE_PDF_MAGIC=PASS
CANDIDATE_ATTACHMENT_HEADER=PASS
PRODUCTION_PDF_HTTP=200
PRODUCTION_PDF_MAGIC=PASS
SERVICE=ACTIVE
POST_CUTOVER_TRACEBACKS=0
ROLLBACK_EXECUTED=NO
```
