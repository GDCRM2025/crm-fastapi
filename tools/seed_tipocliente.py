import sqlite3

DB = "backend/crm.db"

def main():
    cx = sqlite3.connect(DB)
    c = cx.cursor()

    c.execute("CREATE TABLE IF NOT EXISTS tipocliente (id_tipo_cliente INTEGER PRIMARY KEY, nombre TEXT NOT NULL)")
    cx.commit()

    existentes = {r[0] for r in c.execute("SELECT nombre FROM tipocliente").fetchall()}
    to_insert = []
    for nombre in ("Empresa", "Particular"):
        if nombre not in existentes:
            to_insert.append((nombre,))

    if to_insert:
        c.executemany("INSERT INTO tipocliente(nombre) VALUES(?)", to_insert)
        cx.commit()
        print("[OK] Insertados tipos cliente:", ", ".join(n[0] for n in to_insert))
    else:
        print("[SKIP] Tipos cliente ya existen")

    cx.close()
    print("[done]", DB)


if __name__ == "__main__":
    main()
