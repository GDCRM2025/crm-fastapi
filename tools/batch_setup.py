import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB   = ROOT / "backend" / "crm.db"
DATA = ROOT / "data"
XLSX_CANDIDATES = [DATA / "productos bd.xlsx", DATA / "Productos BD.xlsx"]

COMUNAS_RAW = """
Algarrobo|100000|119000
Alhué|0|0
Batuco|50000|59500
Buin|45000|53550
Cabildo|0|0
Calera de Tango|50000|59500
Calle Larga|0|0
Cartagena|100000|119000
Casablanca|100000|119000
Catemu|0|0
Cerrillos|40000|47600
Cerro Navia|40000|47600
Chépica|0|0
Chimbarongo|1|1
Codegua|0|0
Coinco|0|0
Colina|35000|41650
Coltauco|0|0
Conchalí|40000|47600
Concón|100000|119000
Curacaví|70000|83300
Doñihue|0|0
El Arrayan|30000|35700
El Bosque|35000|41650
El Monte|60000|71400
El Quisco|100000|119000
El Tabo|100000|119000
Estación Central|35000|41650
Graneros|0|0
Hijuelas|0|0
Huechuraba|30000|35700
Independencia|35000|41650
Isla de Pascua|0|0
Isla de Maipo|0|0
Juan Fernández|0|0
LA CALERA|0|0
La Cisterna|35000|41650
La Cruz|0|0
La Estrella|0|0
La Florida|30000|35700
La Granja|35000|41650
La Ligua|0|0
La Pintana|35000|41650
La Reina|25000|29750
Lampa|45000|53550
Las Cabras|0|0
Las Condes|25000|29750
Limache|0|0
Litueche|0|0
Llaillay|0|0
Lo Barnechea|25000|29750
Lo Espejo|40000|47600
Lo Prado|40000|47600
Lolol|0|0
Los Andes|0|0
Machalí|0|0
Macul|30000|35700
Maipú|40000|47600
Malloa|0|0
Marchihue|0|0
María Pinto|70000|83300
Melipilla|70000|83300
Mostazal|120000|142800
Nancagua|0|0
Navidad|0|0
Nogales|0|0
Ñuñoa|25000|29750
Olivar|0|0
Olmué|0|0
Padre Hurtado|40000|47600
Paine|55000|65450
Palmilla|0|0
Panquehue|0|0
Papudo|0|0
Paredones|0|0
Pedro Aguirre Cerda|40000|47600
Peñaflor|50000|59500
Peñalolén|25000|29750
Peralillo|0|0
Petorca|0|0
Peumo|0|0
Pichidegua|0|0
Pichilemu|0|0
Pirque|50000|59500
Placilla|0|0
Providencia|25000|29750
Puchuncaví|0|0
Pudahuel|40000|47600
Puente Alto|35000|41650
Pumanque|0|0
Putaendo|0|0
Quilicura|40000|47600
Quillota|100000|119000
Quilpué|100000|119000
Quinta de Tilcoco|0|0
Quinta Normal|40000|47600
Quintero|100000|119000
Rancagua|120000|142800
Recoleta|35000|41650
Renca|40000|47600
Rengo|0|0
REÑACA|100000|119000
Requínoa|0|0
Rinconada|110000|130900
San Antonio|100000|119000
San Bernardo|45000|53550
San Esteban|0|0
San Felipe|0|0
San Fernando|0|0
San Joaquín|35000|41650
San José de Maipo|50000|59500
San Miguel|35000|41650
San Pedro|0|0
San Ramón|35000|41650
San Vicente|0|0
Santa Cruz|0|0
Santa María|0|0
Santiago|35000|41650
Santo Domingo|100000|119000
Talagante|50000|59500
Tiltil|0|0
Valparaiso|100000|119000
Villa Alemana|100000|119000
Viña del Mar|100000|119000
Vitacura|25000|29750
Zapallar|0|0
""".strip()

def table_exists(cx, name:str) -> bool:
    cur = cx.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cur.fetchone() is not None

def columns(cx, name:str) -> set:
    if not table_exists(cx, name): return set()
    return {r[1] for r in cx.execute(f"PRAGMA table_info({name})")}

def add_col_if_missing(cx, table, col_def):
    col_name = col_def.split()[0]
    if col_name not in columns(cx, table):
        cx.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")

def ensure_schema(cx):
    # comunas
    cx.execute("""
    CREATE TABLE IF NOT EXISTS comunas(
        id_comuna INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT UNIQUE NOT NULL,
        neto REAL DEFAULT 0,
        bruto REAL DEFAULT 0
    )""")
    add_col_if_missing(cx, "comunas", "neto REAL DEFAULT 0")
    add_col_if_missing(cx, "comunas", "bruto REAL DEFAULT 0")

    # categorias
    cx.execute("""
    CREATE TABLE IF NOT EXISTS categorias(
        id_categoria INTEGER PRIMARY KEY AUTOINCREMENT,
        codigo TEXT UNIQUE NOT NULL,
        nombre TEXT NOT NULL
    )""")

    # segmentacion (compat: puede existir id_segmentacion en tu DB)
    cx.execute("""
    CREATE TABLE IF NOT EXISTS segmentacion(
        id_segmento INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT UNIQUE NOT NULL
    )""")
    add_col_if_missing(cx, "segmentacion", "min REAL DEFAULT 0")
    add_col_if_missing(cx, "segmentacion", "max REAL DEFAULT 0")

    # productos (solo estos 4 campos como pediste)
    cx.execute("""
    CREATE TABLE IF NOT EXISTS productos(
        id_producto INTEGER PRIMARY KEY AUTOINCREMENT,
        producto TEXT,
        ingredientes TEXT,
        marca TEXT,
        costo REAL DEFAULT 0
    )""")
    add_col_if_missing(cx, "productos", "producto TEXT")
    add_col_if_missing(cx, "productos", "ingredientes TEXT")
    add_col_if_missing(cx, "productos", "marca TEXT")
    add_col_if_missing(cx, "productos", "costo REAL DEFAULT 0")

    cx.commit()

def execmany(cur, sql, rows):
    if rows: cur.executemany(sql, rows)

def load_comunas(cx):
    rows=[]
    for line in COMUNAS_RAW.splitlines():
        nombre, neto, bruto = [p.strip() for p in line.split("|")]
        rows.append((nombre, float(neto or 0), float(bruto or 0)))
    rows.sort(key=lambda t: t[0].lower())
    execmany(cx.cursor(), "INSERT OR REPLACE INTO comunas(nombre,neto,bruto) VALUES(?,?,?)", rows)
    cx.commit()
    print(f"[batch] comunas: {len(rows)}")

def load_categorias(cx):
    execmany(cx.cursor(),
             "INSERT OR REPLACE INTO categorias(codigo,nombre) VALUES(?,?)",
             [("1","Empresa"),("2","Particular")])
    cx.commit(); print("[batch] categorias: OK")

def load_segmentacion(cx):
    execmany(cx.cursor(),
             "INSERT OR REPLACE INTO segmentacion(nombre,min,max) VALUES(?,?,?)",
             [("Tipo 1", 990000, 9999999999),
              ("Tipo 2", 500000, 989000),
              ("Tipo 3",      0, 499999)])
    cx.commit(); print("[batch] segmentacion: OK")

def find_excel():
    for p in XLSX_CANDIDATES:
        if p.exists(): return p
    return None

def load_productos_from_excel(cx, xlsx_path:Path):
    import pandas as pd
    df = pd.read_excel(xlsx_path)

    # case-insensitive pick
    def pick(*names):
        low = {c.lower(): c for c in df.columns}
        for n in names:
            if n.lower() in low:
                return low[n.lower()]
        return None

    def col_strip(series):
        # Series-safe strip
        return series.astype("string").fillna("").str.strip()

    col_producto     = pick("producto","nombre","product","Producto","Nombre")
    col_marca        = pick("marca","brand","Marca","Brand")
    col_ingredientes = pick("ingredientes","descripcion","descripción","Ingredientes","Descripcion","Descripción","desc","Desc")
    col_costo        = pick("costo","precio","neto","precio_neto","valor","Costo","Precio","Neto","Valor")

    if not col_producto or not col_marca or not col_costo:
        print(f"[batch] ⚠️ columnas mínimas no encontradas. columnas={list(df.columns)}")
        return

    # Limpieza de texto
    df['__producto'] = col_strip(df[col_producto])
    df['__marca']    = col_strip(df[col_marca])
    df['__ingred']   = col_strip(df[col_ingredientes]) if col_ingredientes else ""

    # Limpieza de moneda: quita símbolos, quita separadores de miles, usa '.' decimal
    money = col_strip(df[col_costo])
    money = (money
             .str.replace(r"[^0-9,.\-]", "", regex=True)
             .str.replace(".", "", regex=False)     # quita miles como "1.234.567"
             .str.replace(",", ".", regex=False))   # coma -> punto
    df['__costo'] = pd.to_numeric(money, errors='coerce').fillna(0.0).astype(float)

    rows = []
    for p, i, m, c in zip(
        df['__producto'],
        df['__ingred'] if isinstance(df['__ingred'], type(df['__producto'])) else [""]*len(df),
        df['__marca'],
        df['__costo']
    ):
        if p and m:
            rows.append((str(p), str(i or ""), str(m), float(c or 0.0)))

    cur = cx.cursor()
    execmany(cur, "INSERT OR REPLACE INTO productos(producto,ingredientes,marca,costo) VALUES(?,?,?,?)", rows)
    cx.commit()
    print(f"[batch] productos importados: {len(rows)}")

def main():
    print(f"[batch] DB -> {DB}")
    DB.parent.mkdir(parents=True, exist_ok=True)
    cx = sqlite3.connect(str(DB))
    ensure_schema(cx)
    load_comunas(cx)
    load_categorias(cx)
    load_segmentacion(cx)

    xlsx = find_excel()
    print(f"[batch] Excel -> {xlsx}" if xlsx else "[batch] Excel -> no encontrado (ok)")
    if xlsx:
        try:
            load_productos_from_excel(cx, xlsx)
        except Exception as e:
            print("[batch] ⚠️ import productos falló:", e)
    cx.close()
    print("[batch] OK")

if __name__ == "__main__":
    main()
