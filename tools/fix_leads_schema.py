# tools/fix_leads_schema.py
import sqlite3, sys

DB = "backend/crm.db"
cx = sqlite3.connect(DB)
c = cx.cursor()

def colnames(tbl):
    return [r[1] for r in c.execute(f"PRAGMA table_info('{tbl}')").fetchall()]

cols = colnames("leads")

def try_alter(sql):
    try:
        c.execute(sql); cx.commit()
        print("[OK]", sql)
    except Exception as e:
        print("[SKIP]", sql, "->", e)

# Renombrar columnas si existen viejos nombres
if "create_at" in cols and "created_at" not in cols:
    try_alter("ALTER TABLE leads RENAME COLUMN create_at TO created_at")
if "update_at" in cols and "updated_at" not in cols:
    try_alter("ALTER TABLE leads RENAME COLUMN update_at TO updated_at")

# Normalizar monto_cotizado (vacío -> NULL)
try:
    c.execute("UPDATE leads SET monto_cotizado=NULL WHERE TRIM(COALESCE(monto_cotizado,''))=''")
    cx.commit()
    print("[OK] limpia monto_cotizado vacíos")
except Exception as e:
    print("[WARN] monto_cotizado:", e)

cx.close()
print("[done]", DB)
