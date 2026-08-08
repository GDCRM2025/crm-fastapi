-- Reset data + estados_lead baseline (5 estados)
BEGIN;

-- Clean transactional tables if exist
DO $$
BEGIN
  IF to_regclass('public.cotizacion_items') IS NOT NULL THEN
    EXECUTE 'TRUNCATE TABLE public.cotizacion_items RESTART IDENTITY';
  END IF;
  IF to_regclass('public.cotizaciones') IS NOT NULL THEN
    EXECUTE 'TRUNCATE TABLE public.cotizaciones RESTART IDENTITY';
  END IF;
  IF to_regclass('public.lead_notas') IS NOT NULL THEN
    EXECUTE 'TRUNCATE TABLE public.lead_notas RESTART IDENTITY';
  END IF;
  IF to_regclass('public.eventos_calendario') IS NOT NULL THEN
    EXECUTE 'TRUNCATE TABLE public.eventos_calendario RESTART IDENTITY';
  END IF;
  IF to_regclass('public.leads') IS NOT NULL THEN
    EXECUTE 'TRUNCATE TABLE public.leads RESTART IDENTITY';
  END IF;
END $$;

-- Estados lead: dejar solo 5
TRUNCATE TABLE public.estados_lead RESTART IDENTITY;
INSERT INTO public.estados_lead (id_estado, nombre, color, orden, is_active) VALUES
  (1, 'NUEVO',       '#3b82f6', 10, TRUE),
  (2, 'CONTACTADO',  '#06b6d4', 20, TRUE),
  (3, 'COTIZADO',    '#a855f7', 30, TRUE),
  (4, 'CONFIRMADO',  '#22c55e', 40, TRUE),
  (5, 'DECLINADO',   '#ef4444', 90, TRUE);

COMMIT;
