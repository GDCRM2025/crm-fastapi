BEGIN;

-- Tabla de roles (RBAC simple)
CREATE TABLE IF NOT EXISTS roles (
  id_rol       INTEGER PRIMARY KEY AUTOINCREMENT,
  nombre       TEXT NOT NULL UNIQUE,       -- Admin, Vendedor, Operaciones, Finanzas, Viewer
  descripcion  TEXT
);

-- Asegura roles base
INSERT OR IGNORE INTO roles (nombre, descripcion) VALUES
('Admin','Acceso total'),
('Vendedor','Gestión de leads y cotizaciones'),
('Operaciones','Operaciones y logística'),
('Finanzas','Módulos financieros'),
('Viewer','Solo lectura');

-- Asegura columnas nuevas en usuarios (por si ya existía la tabla)
-- Nota: SQLite no soporta ADD COLUMN con constraints complejos; usamos IF NOT EXISTS por compatibilidad.
-- Si tu tabla es nueva, puedes crearla de cero con estas columnas.

-- Crea tabla usuarios si no existe (versión completa con rol)
CREATE TABLE IF NOT EXISTS usuarios (
  id_usuario       INTEGER PRIMARY KEY AUTOINCREMENT,
  nombre           TEXT NOT NULL,
  email            TEXT NOT NULL UNIQUE,
  username         TEXT NOT NULL UNIQUE,
  hashed_password  TEXT NOT NULL,
  telefono         TEXT,
  cargo            TEXT,
  id_rol           INTEGER NOT NULL DEFAULT 1,      -- FK a roles (por defecto Admin para bootstrap)
  is_active        INTEGER NOT NULL DEFAULT 1,
  last_login       TEXT,
  created_at       TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at       TEXT,
  FOREIGN KEY (id_rol) REFERENCES roles(id_rol)
);

-- Índices útiles
CREATE INDEX IF NOT EXISTS idx_usuarios_username ON usuarios(username);
CREATE INDEX IF NOT EXISTS idx_usuarios_email ON usuarios(email);
CREATE INDEX IF NOT EXISTS idx_usuarios_rol ON usuarios(id_rol);

-- Admin por defecto (solo si la tabla está vacía)
-- password: green123  (bcrypt)
INSERT INTO usuarios (nombre,email,username,hashed_password,telefono,cargo,id_rol,is_active)
SELECT
  'Green Diamond','admin@greendiamond.cl','greengd',
  '$2b$12$IhFNyhx.RX23OOahLgMjKOhWEtgN6JogUGHGK5ylz0hq8ToQ/1SKq', -- bcrypt('green123')
  '','Administrador',(SELECT id_rol FROM roles WHERE nombre='Admin'),1
WHERE NOT EXISTS (SELECT 1 FROM usuarios);

COMMIT;
