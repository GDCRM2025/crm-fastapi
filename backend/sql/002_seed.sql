PRAGMA foreign_keys = ON;

INSERT OR IGNORE INTO marcas (nombre) VALUES
  ('Green Diamond'), ('Caterpillar'), ('Komatsu'), ('John Deere');

INSERT OR IGNORE INTO comunas (nombre) VALUES
  ('Santiago'), ('Providencia'), ('Las Condes'), ('Ñuñoa');

INSERT OR IGNORE INTO estados_lead (nombre) VALUES
  ('Pendiente'), ('Contactado'), ('Cotizado'), ('En Negociacion'), ('Confirmado'), ('Declinado');

-- Dummies de prueba (si no hay leads)
INSERT INTO leads (nombre_cliente,email,telefono,id_marca,id_estado,fecha_evento,monto_cotizado,codigo_cliente)
SELECT 'Acme Ltda','ventas@acme.cl','56911112222',
       (SELECT id_marca FROM marcas WHERE nombre='Green Diamond'),
       (SELECT id_estado FROM estados_lead WHERE nombre='Pendiente'),
       DATE('now'), 1250000, 'GRD-AC-01'
WHERE NOT EXISTS (SELECT 1 FROM leads);

INSERT INTO leads (nombre_cliente,email,telefono,id_marca,id_estado,fecha_evento,monto_cotizado,codigo_cliente)
SELECT 'TecnoPlus','contacto@tecnoplus.cl','56933334444',
       (SELECT id_marca FROM marcas WHERE nombre='Komatsu'),
       (SELECT id_estado FROM estados_lead WHERE nombre='Cotizado'),
       DATE('now','+2 days'), 2180000, 'KOM-TP-02'
WHERE (SELECT COUNT(*) FROM leads) < 2;
