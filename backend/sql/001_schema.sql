PRAGMA foreign_keys = ON;

-- Marcas
CREATE TABLE IF NOT EXISTS marcas (
  id_marca      INTEGER PRIMARY KEY AUTOINCREMENT,
  nombre        TEXT NOT NULL UNIQUE
);

-- Comunas
CREATE TABLE IF NOT EXISTS comunas (
  id_comuna     INTEGER PRIMARY KEY AUTOINCREMENT,
  nombre        TEXT NOT NULL UNIQUE
);

-- Estados de Lead (catálogo)
CREATE TABLE IF NOT EXISTS estados_lead (
  id_estado     INTEGER PRIMARY KEY AUTOINCREMENT,
  nombre        TEXT NOT NULL UNIQUE
);

-- Leads
CREATE TABLE IF NOT EXISTS leads (
  id_lead          INTEGER PRIMARY KEY AUTOINCREMENT,
  nombre_cliente   TEXT NOT NULL,
  email            TEXT,
  telefono         TEXT,
  id_marca         INTEGER NOT NULL,
  id_estado        INTEGER NOT NULL,
  fecha_evento     TEXT,                 -- YYYY-MM-DD
  monto_cotizado   REAL DEFAULT 0,
  codigo_cliente   TEXT,                 -- lo genera backend al crear (p.ej. "MAR-AB-01")
  created_at       TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at       TEXT,
  FOREIGN KEY (id_marca)  REFERENCES marcas(id_marca),
  FOREIGN KEY (id_estado) REFERENCES estados_lead(id_estado)
);

CREATE INDEX IF NOT EXISTS idx_leads_estado ON leads(id_estado);
CREATE INDEX IF NOT EXISTS idx_leads_marca  ON leads(id_marca);
