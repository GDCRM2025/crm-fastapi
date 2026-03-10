import os, sqlite3
from pathlib import Path

def parse_sqlite_url(v: str) -> str:
    if not v: return ""
    v=v.strip().strip('"').strip("'")
    if v.startswith("file://"): return v.replace("file://","",1)
    if v.startswith("sqlite:"):
        raw=v[len("sqlite:"):]
        slash4="////" in v
        while raw.startswith("/"): raw=raw[1:]
        return ("/" if slash4 else "") + raw
    return v

ROOT=Path(__file__).resolve().parents[1]
ENV =ROOT/"backend"/".env"
DB  =""
if ENV.exists():
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            DB=parse_sqlite_url(line.split("=",1)[1]); break
if not DB: DB=str(ROOT/"backend"/"crm.db")

cx=sqlite3.connect(DB); c=cx.cursor()

def smoke(name, sql):
    try:
        c.execute(sql); c.fetchall()
        print(name,"OK")
    except Exception as e:
        print(name,"FAIL:",e); raise

smoke("USERS_QUERY", """
SELECT u.id_usuario,u.nombre,u.email,u.username,u.telefono,u.cargo,
       u.id_rol,u.is_active, r.nombre AS rol
FROM usuarios u LEFT JOIN roles r ON r.id_rol=u.id_rol
ORDER BY u.id_usuario DESC
""")

smoke("LEADS_QUERY", """
SELECT l.id_lead,l.nombre_cliente,l.email,l.telefono,l.id_marca,
       l.id_estado, e.nombre AS estado,l.fecha_evento,l.monto_cotizado,l.codigo_cliente
FROM leads l LEFT JOIN estados_lead e ON e.id_estado = l.id_estado
ORDER BY l.id_lead DESC
""")

cx.close()
print("SMOKE DB OK ->", DB)
