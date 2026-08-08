import sqlite3

DB = "backend/crm.db"

def main():
    cx = sqlite3.connect(DB)
    c = cx.cursor()

    cols = {r[1] for r in c.execute("PRAGMA table_info('lead')").fetchall()}

    def add(col_name: str, ddl: str):
        nonlocal cols
        if col_name not in cols:
            sql = f"ALTER TABLE lead ADD COLUMN {ddl}"
            try:
                c.execute(sql)
                cx.commit()
                print("[OK]", sql)
                cols.add(col_name)
            except Exception as e:
                print("[SKIP]", sql, "->", e)

    # Nuevos campos
    add("id_tipo_cliente", "id_tipo_cliente INTEGER REFERENCES tipocliente(id_tipo_cliente)")
    add("plataforma", "plataforma TEXT")
    add("id_comuna", "id_comuna INTEGER REFERENCES comunas(id_comuna)")
    add("fecha_ingreso", "fecha_ingreso TEXT")

    cx.close()
    print("[done]", DB)

if __name__ == "__main__":
    main()
