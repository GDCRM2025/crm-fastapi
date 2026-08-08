import os, sqlite3
from pathlib import Path

def parse_sqlite_url(v: str) -> str:
    if not v: return ""
    v = v.strip().strip('"').strip("'")
    if v.startswith("file://"): return v.replace("file://","",1)
    if v.startswith("sqlite:"):
        raw = v[len("sqlite:"):]
        slash4 = "////" in v
        while raw.startswith("/"): raw = raw[1:]
        return ("/" if slash4 else "") + raw
    return v

ROOT = Path(__file__).resolve().parents[1]
ENV  = ROOT / "backend" / ".env"

DB = ""
if ENV.exists():
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            DB = parse_sqlite_url(line.split("=",1)[1]); break
if not DB:
    DB = str(ROOT / "backend" / "crm.db")

DB = os.path.expanduser(os.path.expandvars(DB))
Path(DB).parent.mkdir(parents=True, exist_ok=True)
print(f"[seed] DB -> {DB}")

cx = sqlite3.connect(DB)
c  = cx.cursor()

# ===== Catálogos =====
c.execute("CREATE TABLE IF NOT EXISTS roles (id_rol INTEGER PRIMARY KEY, nombre TEXT UNIQUE)")
for r in [(1,"Admin"),(2,"Bodeguero"),(3,"Cocinero"),(4,"Conductor"),
          (5,"Ejecutivo de Ventas"),(6,"Jefe de Cocina"),(7,"Jefe de Compras"),
          (8,"Jefe de Operaciones"),(9,"Operaciones")]:
    c.execute("INSERT OR IGNORE INTO roles(id_rol,nombre) VALUES(?,?)", r)

c.execute("CREATE TABLE IF NOT EXISTS marca (id_marca INTEGER PRIMARY KEY, nombre TEXT UNIQUE)")
for m in [(1,"Green Diamond"),(2,"GD Eventos"),(3,"GD Catering")]:
    c.execute("INSERT OR IGNORE INTO marca(id_marca,nombre) VALUES(?,?)", m)

c.execute("CREATE TABLE IF NOT EXISTS estados_lead (id_estado INTEGER PRIMARY KEY, nombre TEXT UNIQUE)")
c.execute("INSERT OR IGNORE INTO estados_lead(id_estado,nombre) VALUES(1,'Nuevo')")

# ===== Usuarios / usuario-marcas =====
c.execute("""
CREATE TABLE IF NOT EXISTS usuarios(
  id_usuario INTEGER PRIMARY KEY,
  nombre TEXT, email TEXT, username TEXT UNIQUE,
  hashed_password TEXT,
  telefono TEXT, cargo TEXT,
  id_rol INTEGER, is_active INTEGER DEFAULT 1,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT
)""")

c.execute("""CREATE TABLE IF NOT EXISTS usuariomarcas(
  id_usuario INTEGER,
  id_marca   INTEGER
)""")

# Si 'usuarios_marcas' existe como tabla o como vista, elimínala de forma segura
row = c.execute("SELECT type FROM sqlite_master WHERE name='usuarios_marcas'").fetchone()
if row and row[0] == "table":
    c.execute("DROP TABLE usuarios_marcas")
elif row and row[0] == "view":
    c.execute("DROP VIEW usuarios_marcas")

# Crea la vista canónica
c.execute("""
CREATE VIEW usuarios_marcas AS
SELECT id_usuario,id_marca FROM usuariomarcas
""")

# ===== Leads (tabla real + vista de compatibilidad) =====
c.execute("""
CREATE TABLE IF NOT EXISTS lead(
  id_lead INTEGER PRIMARY KEY,
  nombre_cliente TEXT, email TEXT, telefono TEXT,
  id_marca INTEGER, id_estado INTEGER,
  fecha_evento TEXT, monto_cotizado REAL, codigo_cliente TEXT,
  create_at TEXT DEFAULT CURRENT_TIMESTAMP,
  update_at TEXT
)""")

row = c.execute("SELECT type FROM sqlite_master WHERE name='leads'").fetchone()
if row and row[0] == "table":
    # Migración legacy: copiar datos a lead y borrar tabla antigua
    cols = ("id_lead,nombre_cliente,email,telefono,id_marca,id_estado,fecha_evento,monto_cotizado,codigo_cliente,created_at,updated_at")
    try:
        c.execute(f"INSERT INTO lead(id_lead,nombre_cliente,email,telefono,id_marca,id_estado,fecha_evento,monto_cotizado,codigo_cliente,create_at,update_at) SELECT {cols} FROM leads")
        c.execute("DROP TABLE leads")
    except Exception:
        pass

# Re-crear vista 'leads' apuntando a 'lead'
row = c.execute("SELECT type FROM sqlite_master WHERE name='leads'").fetchone()
if row and row[0] == "view":
    c.execute("DROP VIEW leads")
c.execute("""
CREATE VIEW leads AS
SELECT id_lead,nombre_cliente,email,telefono,id_marca,id_estado,
       fecha_evento,monto_cotizado,codigo_cliente,
       create_at AS created_at, update_at AS updated_at
FROM lead
""")

# ===== Admin por defecto =====
hashed = "green123"
try:
    from passlib.context import CryptContext
    hashed = CryptContext(schemes=['bcrypt'], deprecated='auto').hash("green123"[:72])
except Exception:
    pass

if not c.execute("SELECT 1 FROM usuarios WHERE username='greengd'").fetchone():
    c.execute("""INSERT INTO usuarios(nombre,email,username,hashed_password,id_rol,is_active,created_at)
                 VALUES(?,?,?,?,1,1,datetime('now'))""",
              ("Green Diamond","admin@greendiamond.cl","greengd",hashed))
    uid = c.execute("SELECT id_usuario FROM usuarios WHERE username='greengd'").fetchone()[0]
    for (mid,) in c.execute("SELECT id_marca FROM marca"):
        c.execute("INSERT INTO usuariomarcas(id_usuario,id_marca) VALUES(?,?)", (uid, mid))

# ===== Lead demo =====
if not c.execute("SELECT 1 FROM lead WHERE codigo_cliente='CLT-001'").fetchone():
    c.execute("""INSERT INTO lead(nombre_cliente,email,telefono,id_marca,id_estado,fecha_evento,monto_cotizado,codigo_cliente,create_at,update_at)
                 VALUES(?,?,?,?,?,?,?, ?, datetime('now'), datetime('now'))""",
              ("Cliente Demo","demo@cliente.cl","+56911111111",1,1,"2025-11-30",123456,"CLT-001"))

cx.commit(); cx.close()
print("[seed] OK")
