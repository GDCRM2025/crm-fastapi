import os
from pathlib import Path
import re
import csv

# Lee lista en formato TSV: producto \t descripcion \t marca \t costo
# Si hay mas columnas, usa la ultima como costo y la penultima como marca.
# Si falta costo, usa 500.

def parse_rows(path: Path):
    out = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter="\t", quotechar='"')
        for i, row in enumerate(reader):
            if not row:
                continue
            # salta encabezado
            if i == 0 and "producto" in row[0].lower():
                continue
            parts = [p.strip() for p in row]
            if len(parts) < 2:
                continue
            if len(parts) == 2:
                producto, marca = parts
                desc = ""
                costo = 500
            elif len(parts) == 3:
                producto, desc, marca = parts
                costo = 500
            else:
                producto = parts[0]
                marca = parts[-2]
                costo_raw = parts[-1]
                desc = " ".join(parts[1:-2]).strip()
                try:
                    costo = float(re.sub(r"[^0-9.]", "", costo_raw))
                    if costo == 0:
                        costo = 500
                except Exception:
                    costo = 500
            out.append((producto, desc, marca, costo))
    return out


def main():
    root = Path(__file__).resolve().parents[2]
    txt_path = root / "backend" / "data" / "productos_raw.txt"
    if not txt_path.exists():
        print("No existe productos_raw.txt. Pega la lista en:", txt_path)
        return

    rows = parse_rows(txt_path)
    if not rows:
        print("No se encontraron filas validas")
        return

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        env = root / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.startswith("DATABASE_URL="):
                    dsn = line.split("=",1)[1].strip()
                    break
    if not dsn:
        print("DATABASE_URL no configurado")
        return
    # psycopg no acepta el prefijo sqlalchemy "postgresql+psycopg"
    if dsn.startswith("postgresql+psycopg://"):
        dsn = "postgresql://" + dsn.split("://", 1)[1]

    import psycopg

    def _try_connect(dsn_value: str):
        return psycopg.connect(dsn_value)

    conn = None
    try:
        conn = _try_connect(dsn)
    except Exception:
        # fallback a socket local si el TCP está bloqueado (Mac)
        if "@127.0.0.1:5432/" in dsn:
            dsn_sock = dsn.replace("@127.0.0.1:5432/", "@/")
            conn = _try_connect(dsn_sock)
        elif "localhost:5432" in dsn:
            dsn_sock = dsn.replace("localhost:5432", "")
            conn = _try_connect(dsn_sock)
        else:
            raise

    with conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE public.productos RESTART IDENTITY")
            cur.executemany(
                """
                INSERT INTO public.productos (producto, descripcion, marca, costo, is_active, orden)
                VALUES (%s, %s, %s, %s, TRUE, 0)
                """,
                rows,
            )
        conn.commit()

    print(f"OK: insertados {len(rows)} productos")


if __name__ == "__main__":
    main()
