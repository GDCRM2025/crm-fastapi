-- migrations/2026_01_28_cotizador.sql
-- Ejecuta: psql -d BDGD -U BDGD -f migrations/2026_01_28_cotizador.sql

BEGIN;

-- Columnas mínimas requeridas en productos
ALTER TABLE productos
  ADD COLUMN IF NOT EXISTS producto      TEXT,
  ADD COLUMN IF NOT EXISTS descripcion   TEXT,
  ADD COLUMN IF NOT EXISTS marca         TEXT,
  ADD COLUMN IF NOT EXISTS costo         NUMERIC(12,2),
  ADD COLUMN IF NOT EXISTS is_active     BOOLEAN DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS orden         INT DEFAULT 0;

-- Si la tabla tenía otros nombres de campos, intentamos poblar:
UPDATE productos
SET
  producto = COALESCE(producto, nombre, nombre_producto)
WHERE producto IS NULL;

-- Si había "ingredientes" (texto o json), lo volcamos a descripcion
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name='productos' AND column_name='ingredientes'
  ) THEN
    EXECUTE $m$
      UPDATE productos
      SET descripcion = COALESCE(
        descripcion,
        CASE
          WHEN jsonb_typeof((to_jsonb(productos)->'ingredientes'))='array'
          THEN (
            SELECT string_agg(x, ', ')
            FROM jsonb_array_elements_text(to_jsonb(productos)->'ingredientes') x
          )
          ELSE NULLIF((to_jsonb(productos)->>'ingredientes'), '')
        END
      )
      WHERE descripcion IS NULL
    $m$;
  END IF;
END$$;

-- Si existía "precio_neto" o "precio" y no hay costo, lo usamos como costo
UPDATE productos SET costo = precio_neto
WHERE costo IS NULL AND precio_neto IS NOT NULL;

UPDATE productos SET costo = precio
WHERE costo IS NULL AND precio IS NOT NULL;

-- Vista estable para el API (campos únicos y consistentes)
DROP VIEW IF EXISTS productos_vw;
CREATE VIEW productos_vw AS
SELECT
  COALESCE(p.id_producto, p.id) AS id_producto,
  COALESCE(p.producto, p.nombre, p.nombre_producto, 'Producto') AS producto,
  COALESCE(p.descripcion, '')   AS descripcion,
  COALESCE(p.marca, '')         AS marca,
  COALESCE(p.costo, 0)::NUMERIC(12,2) AS costo,
  COALESCE(p.is_active, TRUE)   AS is_active,
  COALESCE(p.orden, 0)          AS orden
FROM productos p;

COMMIT;
