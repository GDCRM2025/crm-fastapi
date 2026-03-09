BEGIN;

-- Relación N:M entre usuarios y marcas
CREATE TABLE IF NOT EXISTS usuarios_marcas (
  id_usuario INTEGER NOT NULL,
  id_marca   INTEGER NOT NULL,
  PRIMARY KEY (id_usuario, id_marca),
  FOREIGN KEY (id_usuario) REFERENCES usuarios(id_usuario),
  FOREIGN KEY (id_marca)   REFERENCES marcas(id_marca)
);

-- Índices para velocidad
CREATE INDEX IF NOT EXISTS idx_um_usuario ON usuarios_marcas(id_usuario);
CREATE INDEX IF NOT EXISTS idx_um_marca   ON usuarios_marcas(id_marca);

-- Asegura que roles base existan (por si no estaban)
INSERT OR IGNORE INTO roles(id_rol,nombre) VALUES
(1,'Admin'),(2,'Vendedor'),(3,'Operaciones'),(4,'Finanzas'),(5,'Viewer');

-- Asigna TODAS las marcas al usuario greengd (id=1) como ejemplo
INSERT OR IGNORE INTO usuarios_marcas(id_usuario, id_marca)
SELECT 1, m.id_marca FROM marcas m;

COMMIT;

