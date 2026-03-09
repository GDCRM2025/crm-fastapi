-- migrations/2026_01_28_cotizador.sql
-- Ejecuta: psql -d BDGD -U BDGD -f migrations/2026_01_28_cotizador.sql

BEGIN;

-- Normalizamos columnas mínimas (no falla si ya existen)
ALTER TABLE productos
  ADD COLUMN IF NOT EXISTS producto    TEXT,
  ADD COLUMN IF NOT EXISTS descripcion TEXT,
  ADD COLUMN IF NOT EXISTS marca       TEXT,
  ADD COLUMN IF NOT EXISTS costo       NUMERIC(12,2),
  ADD COLUMN IF NOT EXISTS is_active   BOOLEAN DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS orden       INT DEFAULT 0;

-- Poblar producto desde columnas que existan (sin referenciar columnas inexistentes)
DO $$
DECLARE
  has_nombre BOOLEAN;
  has_nombre_producto BOOLEAN;
BEGIN
  SELECT EXISTS(
    SELECT 1 FROM information_schema.columns
    WHERE table_name='productos' AND column_name='nombre'
  ) INTO has_nombre;

  SELECT EXISTS(
    SELECT 1 FROM information_schema.columns
    WHERE table_name='productos' AND column_name='nombre_producto'
  ) INTO has_nombre_producto;

  IF has_nombre AND has_nombre_producto THEN
    EXECUTE 'UPDATE productos SET producto = COALESCE(producto, nombre, nombre_producto) WHERE producto IS NULL';
  ELSIF has_nombre THEN
    EXECUTE 'UPDATE productos SET producto = COALESCE(producto, nombre) WHERE producto IS NULL';
  ELSIF has_nombre_producto THEN
    EXECUTE 'UPDATE productos SET producto = COALESCE(producto, nombre_producto) WHERE producto IS NULL';
  END IF;
END$$;

-- Si existe "ingredientes", lo volcamos a descripcion (como texto)
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name='productos' AND column_name='ingredientes'
  ) THEN
    EXECUTE $m$
      UPDATE productos
      SET descripcion = COALESCE(descripcion, NULLIF(ingredientes::text, ''))
      WHERE descripcion IS NULL
    $m$;
  END IF;
END$$;

-- Si existen "precio_neto"/"precio", usamos para costo (sin romper si no existen)
DO $$
DECLARE
  has_precio_neto BOOLEAN;
  has_precio BOOLEAN;
BEGIN
  SELECT EXISTS(
    SELECT 1 FROM information_schema.columns
    WHERE table_name='productos' AND column_name='precio_neto'
  ) INTO has_precio_neto;

  SELECT EXISTS(
    SELECT 1 FROM information_schema.columns
    WHERE table_name='productos' AND column_name='precio'
  ) INTO has_precio;

  IF has_precio_neto THEN
    EXECUTE 'UPDATE productos SET costo = precio_neto WHERE costo IS NULL AND precio_neto IS NOT NULL';
  END IF;

  IF has_precio THEN
    EXECUTE 'UPDATE productos SET costo = precio WHERE costo IS NULL AND precio IS NOT NULL';
  END IF;
END$$;

-- Vista estable productos_vw (construcción dinámica según columnas presentes)
DO $$
DECLARE
  has_id_producto BOOLEAN;
  has_id BOOLEAN;
  has_producto BOOLEAN;
  has_nombre BOOLEAN;
  has_nombre_producto BOOLEAN;
  has_descripcion BOOLEAN;
  has_marca BOOLEAN;
  has_costo BOOLEAN;
  has_is_active BOOLEAN;
  has_orden BOOLEAN;

  id_expr TEXT;
  prod_expr TEXT;
  desc_expr TEXT;
  marca_expr TEXT;
  costo_expr TEXT;
  is_active_expr TEXT;
  orden_expr TEXT;

  sql_view TEXT;
BEGIN
  SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_name='productos' AND column_name='id_producto') INTO has_id_producto;
  SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_name='productos' AND column_name='id')            INTO has_id;
  SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_name='productos' AND column_name='producto')     INTO has_producto;
  SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_name='productos' AND column_name='nombre')       INTO has_nombre;
  SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_name='productos' AND column_name='nombre_producto') INTO has_nombre_producto;
  SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_name='productos' AND column_name='descripcion')  INTO has_descripcion;
  SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_name='productos' AND column_name='marca')        INTO has_marca;
  SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_name='productos' AND column_name='costo')        INTO has_costo;
  SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_name='productos' AND column_name='is_active')    INTO has_is_active;
  SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_name='productos' AND column_name='orden')        INTO has_orden;

  -- id
  IF has_id_producto THEN
    id_expr := 'p.id_producto';
  ELSIF has_id THEN
    id_expr := 'p.id';
  ELSE
    id_expr := 'ROW_NUMBER() OVER ()';
  END IF;

  -- producto (COALESCE solo con columnas que realmente existan)
  IF has_producto THEN
    prod_expr := 'p.producto';
    IF has_nombre THEN prod_expr := 'COALESCE(' || prod_expr || ', p.nombre)'; END IF;
    IF has_nombre_producto THEN prod_expr := 'COALESCE(' || prod_expr || ', p.nombre_producto)'; END IF;
    prod_expr := 'COALESCE(' || prod_expr || ', ''Producto'')';
  ELSIF has_nombre OR has_nombre_producto THEN
    prod_expr := '';
    IF has_nombre THEN prod_expr := 'p.nombre'; END IF;
    IF has_nombre_producto THEN
      IF prod_expr <> '' THEN
        prod_expr := 'COALESCE(' || prod_expr || ', p.nombre_producto)';
      ELSE
        prod_expr := 'p.nombre_producto';
      END IF;
    END IF;
    prod_expr := 'COALESCE(' || prod_expr || ', ''Producto'')';
  ELSE
    prod_expr := '''Producto''';
  END IF;

  -- resto
  IF has_descripcion THEN desc_expr := 'COALESCE(p.descripcion, '''')'; ELSE desc_expr := ''''''; END IF;
  IF has_marca       THEN marca_expr := 'COALESCE(p.marca, '''')';       ELSE marca_expr := ''''''; END IF;
  IF has_costo       THEN costo_expr := 'COALESCE(p.costo, 0)::NUMERIC(12,2)'; ELSE costo_expr := '0::NUMERIC(12,2)'; END IF;
  IF has_is_active   THEN is_active_expr := 'COALESCE(p.is_active, TRUE)'; ELSE is_active_expr := 'TRUE'; END IF;
  IF has_orden       THEN orden_expr := 'COALESCE(p.orden, 0)'; ELSE orden_expr := '0'; END IF;

  EXECUTE 'DROP VIEW IF EXISTS productos_vw';

  sql_view :=
    'CREATE VIEW productos_vw AS ' ||
    'SELECT ' ||
    id_expr        || ' AS id_producto, ' ||
    prod_expr      || ' AS producto, '   ||
    desc_expr      || ' AS descripcion, '||
    marca_expr     || ' AS marca, '      ||
    costo_expr     || ' AS costo, '      ||
    is_active_expr || ' AS is_active, '  ||
    orden_expr     || ' AS orden '       ||
    'FROM productos p';

  EXECUTE sql_view;
END$$;

COMMIT;
