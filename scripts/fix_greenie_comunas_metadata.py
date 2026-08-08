from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend/routers/whatsapp_gia.py"

text = BACKEND.read_text(encoding="utf-8")

new_helper = r'''def _greenie_comunas(db: Session) -> list[dict[str, Any]]:
    columns = {
        str(row["column_name"])
        for row in db.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'comunas'
        """)).mappings().all()
    }
    if not columns:
        raise HTTPException(
            status_code=500,
            detail="No existe la tabla public.comunas",
        )

    id_column = next(
        (name for name in ("id_comuna", "id", "comuna_id") if name in columns),
        None,
    )
    name_column = next(
        (
            name
            for name in (
                "nombre",
                "comuna",
                "name",
                "descripcion",
                "nombre_comuna",
            )
            if name in columns
        ),
        None,
    )
    if not name_column:
        raise HTTPException(
            status_code=500,
            detail=(
                "La tabla public.comunas no tiene una columna reconocida para "
                "el nombre. Columnas detectadas: " + ", ".join(sorted(columns))
            ),
        )

    id_expression = id_column if id_column else "ROW_NUMBER() OVER (ORDER BY " + name_column + ")"
    rows = db.execute(text(f"""
        SELECT
            {id_expression} AS id_comuna,
            TRIM(CAST({name_column} AS TEXT)) AS nombre
        FROM public.comunas
        WHERE NULLIF(TRIM(CAST({name_column} AS TEXT)), '') IS NOT NULL
        ORDER BY TRIM(CAST({name_column} AS TEXT))
    """)).mappings().all()

    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for row in rows:
        nombre = str(row.get("nombre") or "").strip()
        key = nombre.casefold()
        if not nombre or key in seen:
            continue
        seen.add(key)
        items.append({
            "id_comuna": row.get("id_comuna"),
            "nombre": nombre,
        })
    return items
'''

pattern = re.compile(
    r"def _greenie_comunas\(db: Session\) -> list\[dict\[str, Any\]\]:\n.*?(?=^def \w+|^@router\.|\Z)",
    re.MULTILINE | re.DOTALL,
)
match = pattern.search(text)
if match:
    text = text[:match.start()] + new_helper + "\n\n" + text[match.end():]
    print("REEMPLAZADO: _greenie_comunas")
else:
    anchor = "def _conversation_or_404(db: Session, conversation_id: int):"
    if anchor not in text:
        raise SystemExit("ERROR: no se encontro ancla para _greenie_comunas")
    text = text.replace(anchor, new_helper + "\n\n" + anchor, 1)
    print("AGREGADO: _greenie_comunas")

endpoint = '''@router.get("/metadata/comunas")
def whatsapp_comunas(db: Session = Depends(get_db)):
    _ensure_tables(db)
    return {"ok": True, "items": _greenie_comunas(db)}
'''
if '@router.get("/metadata/comunas")' not in text:
    anchor = '@router.get("/conversations")'
    if anchor not in text:
        raise SystemExit("ERROR: no se encontro ancla para endpoint comunas")
    text = text.replace(anchor, endpoint + "\n\n" + anchor, 1)
    print("AGREGADO: endpoint /metadata/comunas")

BACKEND.write_text(text, encoding="utf-8")
print("GREENIE_COMUNAS_METADATA_OK")
