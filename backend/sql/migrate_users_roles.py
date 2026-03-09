import sqlite3, os, sys

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "crm.bd")
DB_PATH = os.path.abspath(DB_PATH)

def col_exists(cur, table, col):
    cur.execute(f"PRAGMA table_info({table})")
    return any(r[1] == col for r in cur.fetchall())

def ensure_roles(cur):
    cur.execute("""
      CREATE TABLE IF NOT EXISTS roles (
        id_rol       INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre       TEXT NOT NULL UNIQUE,
        descripcion  TEXT
      )
    """)
    cur.execute("INSERT OR IGNORE INTO roles (nombre,descripcion) VALUES (?,?)", ("Admin","Acceso total"))
    cur.execute("INSERT OR IGNORE INTO roles (nombre,descripcion) VALUES (?,?)", ("Vendedor","Gestión de leads y cotizaciones"))
    cur.execute("INSERT OR IGNORE INTO roles (nombre,descripcion) VALUES (?,?)", ("Operaciones","Operaciones y logística"))
    cur.execute("INSERT OR IGNORE INTO roles (nombre,descripcion) VALUES (?,?)", ("Finanzas","Módulos financieros"))
    cur.execute("INSERT OR IGNORE INTO roles (nombre,descripcion) VALUES (?,?)", ("Viewer","Solo lectura"))

def ensure_usuarios_columns(cur):
    # Crea tabla si no existe (versión mínima)
    cur.execute("""
      CREATE TABLE IF NOT EXISTS usuarios (
        id_usuario       INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre           TEXT NOT NULL,
        email            TEXT NOT NULL UNIQUE,
        username         TEXT NOT NULL UNIQUE,
        hashed_password  TEXT NOT NULL
      )
    """)
    # Agrega columnas que falten (ADD COLUMN simple es válido en SQLite)
    for col_sql in [
        ("telefono", "TEXT"),
        ("cargo", "TEXT"),
        ("id_rol", "INTEGER NOT NULL DEFAULT 1"),
        ("is_active", "INTEGER NOT NULL DEFAULT 1"),
        ("last_login", "TEXT"),
        ("created_at", "TEXT NOT NULL DEFAULT (datetime('now'))"),
        ("updated_at", "TEXT"),
    ]:
        col, decl = col_sql
        if not col_exists(cur, "usuarios", col):
            cur.execute(f"ALTER TABLE usuarios ADD COLUMN {col} {decl}")

def ensure_indexes(cur):
    # Crea índices solo si las columnas existen
    if col_exists(cur, "usuarios", "username"):
        cur.execute("CREATE INDEX IF NOT EXISTS idx_usuarios_username ON usuarios(username)")
    if col_exists(cur, "usuarios", "email"):
        cur.execute("CREATE INDEX IF NOT EXISTS idx_usuarios_email ON usuarios(email)")
    if col_exists(cur, "usuarios", "id_rol"):
        cur.execute("CREATE INDEX IF NOT EXISTS idx_usuarios_rol ON usuarios(id_rol)")

def seed_admin(cur):
    # Inserta admin por defecto si no hay usuarios
    cur.execute("SELECT COUNT(*) FROM usuarios")
    if cur.fetchone()[0] == 0:
        # bcrypt('green123') -> hash fijo de tu entorno
        hpw = "$2b$12$IhFNyhx.RX23OOahLgMjKOhWEtgN6JogUGHGK5ylz0hq8ToQ/1SKq"
        cur.execute("SELECT id_rol FROM roles WHERE nombre='Admin'")
        id_rol = cur.fetchone()
        id_rol = id_rol[0] if id_rol else 1
        cur.execute("""
          INSERT INTO usuarios (nombre,email,username,hashed_password,telefono,cargo,id_rol,is_active)
          VALUES (?,?,?,?,?,?,?,?)
        """, ("Green Diamond","admin@greendiamond.cl","greengd",hpw,"","Administrador",id_rol,1))

def main():
    print("Usando BD:", DB_PATH)
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    try:
        cur.execute("BEGIN")
        ensure_roles(cur)
        ensure_usuarios_columns(cur)
        ensure_indexes(cur)
        seed_admin(cur)
        con.commit()
        print("Migración OK.")
    except Exception as e:
        con.rollback()
        print("Error en migración:", e)
        sys.exit(1)
    finally:
        con.close()

if __name__ == "__main__":
    main()

