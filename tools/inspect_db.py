import sqlite3
import sys
from textwrap import indent

def inspect(db_path: str):
    print("=" * 80)
    print(f"DB: {db_path}")
    print("=" * 80)

    cx = sqlite3.connect(db_path)
    cx.row_factory = sqlite3.Row
    c = cx.cursor()

    # Listar tablas y vistas
    objs = c.execute("""
        SELECT name, type
        FROM sqlite_master
        WHERE type IN ('table','view')
          AND name NOT LIKE 'sqlite_%'
        ORDER BY type, name
    """).fetchall()

    if not objs:
        print(">> (sin tablas ni vistas)")
        cx.close()
        return

    print("\nOBJETOS (tablas / vistas):")
    for r in objs:
        print(f" - {r['type'].upper():5} {r['name']}")

    # Mostrar schema y algunos datos de cada tabla
    for r in objs:
        name = r["name"]
        tipo = r["type"]

        print("\n" + "-" * 80)
        print(f"{tipo.upper()} {name}")
        print("-" * 80)

        # DDL
        ddl = c.execute(
            "SELECT sql FROM sqlite_master WHERE name=?",
            (name,)
        ).fetchone()
        if ddl and ddl["sql"]:
            print("SQL:")
            print(indent(ddl["sql"], "  "))
        else:
            print("SQL: (no disponible)")

        if tipo == "table":
            # columnas
            cols = c.execute(f"PRAGMA table_info('{name}')").fetchall()
            if cols:
                print("\nColumnas:")
                for col in cols:
                    cid, col_name, col_type, notnull, dflt, pk = col
                    print(f"  - {col_name} {col_type} "
                          f"{'NOT NULL' if notnull else ''} "
                          f"{'PK' if pk else ''} "
                          f"{'(def=' + str(dflt) + ')' if dflt is not None else ''}")
            # conteo
            try:
                n = c.execute(f"SELECT COUNT(*) AS n FROM '{name}'").fetchone()["n"]
                print(f"\nTotal filas: {n}")
                # sample
                sample = c.execute(f"SELECT * FROM '{name}' LIMIT 5").fetchall()
                if sample:
                    print("Muestras (hasta 5 filas):")
                    for row in sample:
                        print("  ", dict(row))
            except Exception as e:
                print(f"\n(No se pudo leer datos de {name}: {e})")

    cx.close()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        inspect(sys.argv[1])
    else:
        # por defecto usamos backend/crm.db
        inspect("backend/crm.db")
