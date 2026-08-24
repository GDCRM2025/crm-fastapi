\set ON_ERROR_STOP on

BEGIN;

CREATE SCHEMA IF NOT EXISTS analytics AUTHORIZATION analytics_owner;
REVOKE ALL ON SCHEMA analytics FROM PUBLIC;
GRANT USAGE ON SCHEMA analytics TO joaquin_analytics;

CREATE OR REPLACE VIEW analytics.comercial_mensual AS
SELECT
  date_trunc('month', COALESCE(l.created_at, l.updated_at))::date AS mes,
  COALESCE(m.marca, m.nombre, 'SIN MARCA') AS marca,
  COALESCE(e.nombre, 'SIN ESTADO') AS estado,
  COALESCE(NULLIF(btrim(l.plataforma), ''), 'SIN ORIGEN') AS origen,
  COALESCE(NULLIF(btrim(l.tipo_cliente), ''), 'SIN TIPO') AS tipo_cliente,
  count(*)::bigint AS leads,
  count(*) FILTER (WHERE upper(COALESCE(e.nombre,''))='CONFIRMADO')::bigint AS confirmados,
  COALESCE(sum(l.monto_cotizado),0)::numeric AS monto_cotizado
FROM public.leads l
LEFT JOIN public.marcas m ON m.id_marca=l.id_marca
LEFT JOIN public.estados_lead e ON e.id_estado=l.id_estado
WHERE COALESCE(l.is_deleted,FALSE)=FALSE
GROUP BY 1,2,3,4,5;

CREATE OR REPLACE VIEW analytics.cotizaciones_mensual AS
WITH latest AS (
  SELECT DISTINCT ON (c.numero)
    c.numero,c.fecha,c.marca,c.estado,c.tipo_cliente,c.total,c.subtotal_productos,c.iva,c.traslado,c.descuento_valor
  FROM public.cotizaciones c
  ORDER BY c.numero,COALESCE(c.version,1) DESC,c.updated_at DESC,c.id_cotizacion DESC
)
SELECT
  date_trunc('month',fecha)::date AS mes,
  COALESCE(NULLIF(btrim(marca),''),'SIN MARCA') AS marca,
  COALESCE(NULLIF(btrim(estado),''),'SIN ESTADO') AS estado,
  COALESCE(NULLIF(btrim(tipo_cliente),''),'SIN TIPO') AS tipo_cliente,
  count(*)::bigint AS cotizaciones,
  COALESCE(sum(subtotal_productos),0)::numeric AS subtotal_productos,
  COALESCE(sum(iva),0)::numeric AS iva,
  COALESCE(sum(traslado),0)::numeric AS traslado,
  COALESCE(sum(descuento_valor),0)::numeric AS descuentos,
  COALESCE(sum(total),0)::numeric AS total
FROM latest GROUP BY 1,2,3,4;

CREATE OR REPLACE VIEW analytics.ventas_mensual AS
SELECT
  date_trunc('month',fecha_evento)::date AS mes,
  COALESCE(NULLIF(btrim(marca),''),'SIN MARCA') AS marca,
  COALESCE(NULLIF(btrim(tipo_cliente),''),'SIN TIPO') AS tipo_cliente,
  count(*)::bigint AS eventos,
  COALESCE(sum(monto_bruto),0)::numeric AS venta_bruta,
  COALESCE(sum(monto_neto),0)::numeric AS venta_neta,
  COALESCE(sum(iva),0)::numeric AS iva,
  COALESCE(sum(abono),0)::numeric AS abonos,
  COALESCE(sum(saldo),0)::numeric AS saldo
FROM public.fin_eventos GROUP BY 1,2,3;

CREATE OR REPLACE VIEW analytics.pagos_mensual AS
SELECT
  date_trunc('month',p.fecha)::date AS mes,
  COALESCE(NULLIF(btrim(e.marca),''),'SIN MARCA') AS marca,
  COALESCE(NULLIF(btrim(p.metodo),''),'SIN METODO') AS metodo,
  count(*)::bigint AS pagos,
  COALESCE(sum(p.monto),0)::numeric AS monto_pagado
FROM public.fin_pagos p
JOIN public.fin_eventos e ON e.id_evento=p.id_evento
GROUP BY 1,2,3;

CREATE OR REPLACE VIEW analytics.gastos_mensual AS
SELECT
  date_trunc('month',g.fecha)::date AS mes,
  COALESCE(le.legal_name,'SIN SOCIEDAD') AS sociedad_legal,
  COALESCE(NULLIF(btrim(g.marca),''),'SIN MARCA') AS marca,
  COALESCE(NULLIF(btrim(g.centro_costo),''),'SIN CENTRO') AS centro_costo,
  COALESCE(NULLIF(btrim(g.payable_status),''),'SIN ESTADO') AS estado_pago,
  count(*)::bigint AS gastos,
  COALESCE(sum(g.monto),0)::numeric AS monto,
  COALESCE(sum(g.balance),0)::numeric AS saldo
FROM public.fin_gastos g
LEFT JOIN public.fin_legal_entities le ON le.id_legal_entity=g.id_legal_entity
WHERE COALESCE(g.is_active,TRUE)=TRUE
GROUP BY 1,2,3,4,5;

CREATE OR REPLACE VIEW analytics.metas_mensual AS
SELECT
  make_date(mm.year,mm.month,1) AS mes,
  COALESCE(m.marca,m.nombre,'SIN MARCA') AS marca,
  mm.venta_base,
  mm.crecimiento_pct,
  mm.meta
FROM public.metas_marca_mensual mm
LEFT JOIN public.marcas m ON m.id_marca=mm.id_marca;

REVOKE ALL ON ALL TABLES IN SCHEMA analytics FROM PUBLIC;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO joaquin_analytics;
ALTER ROLE joaquin_analytics IN DATABASE bf68ec5_crm2025b SET search_path=analytics;

COMMIT;
