from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend/routers/whatsapp_gia.py"

text = BACKEND.read_text(encoding="utf-8")

if "import unicodedata\n" not in text:
    anchor = "import urllib.request\n"
    if anchor not in text:
        raise SystemExit("ERROR: no se encontro el bloque de imports")
    text = text.replace(anchor, anchor + "import unicodedata\n", 1)

helper = r'''def _greenie_normalize_brand(value: Any) -> str:
    raw = unicodedata.normalize("NFKD", str(value or ""))
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    return "".join(ch for ch in raw.upper() if ch.isalnum())


def _greenie_brand_from_db(
    db: Session,
    brand_code: str,
    fallback_name: str | None = None,
) -> dict[str, Any]:
    columns = {
        str(row["column_name"])
        for row in db.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'marcas'
        """)).mappings().all()
    }
    if not columns:
        raise HTTPException(
            status_code=500,
            detail="No existe la tabla public.marcas en el CRM",
        )

    id_column = next(
        (name for name in ("id_marca", "id", "marca_id") if name in columns),
        None,
    )
    if not id_column:
        raise HTTPException(
            status_code=500,
            detail=(
                "La tabla public.marcas no tiene una columna ID reconocida. "
                "Columnas detectadas: " + ", ".join(sorted(columns))
            ),
        )

    candidate_columns = [
        name
        for name in (
            "nombre",
            "marca",
            "nombre_marca",
            "codigo",
            "code",
            "slug",
            "abreviatura",
            "descripcion",
        )
        if name in columns
    ]
    if not candidate_columns:
        raise HTTPException(
            status_code=500,
            detail=(
                "La tabla public.marcas no tiene columnas reconocidas para "
                "identificar la marca. Columnas detectadas: "
                + ", ".join(sorted(columns))
            ),
        )

    select_fields = ", ".join(
        [f'{id_column} AS id_marca']
        + [f'{column} AS "{column}"' for column in candidate_columns]
    )
    rows = db.execute(text(f"""
        SELECT {select_fields}
        FROM public.marcas
        ORDER BY {id_column}
    """)).mappings().all()

    requested = {
        _greenie_normalize_brand(brand_code),
        _greenie_normalize_brand(fallback_name),
        _greenie_normalize_brand(brand_code.replace("_", " ")),
    }
    requested.discard("")

    matches: list[tuple[int, dict[str, Any], str]] = []
    available: list[str] = []
    for row in rows:
        row_dict = dict(row)
        labels = [
            str(row_dict.get(column) or "").strip()
            for column in candidate_columns
            if str(row_dict.get(column) or "").strip()
        ]
        if labels:
            available.append(labels[0])
        normalized_labels = {
            _greenie_normalize_brand(label)
            for label in labels
            if _greenie_normalize_brand(label)
        }
        exact = requested.intersection(normalized_labels)
        if exact:
            display_name = next(
                (
                    str(row_dict.get(column) or "").strip()
                    for column in ("nombre", "marca", "nombre_marca", "descripcion")
                    if column in row_dict and str(row_dict.get(column) or "").strip()
                ),
                labels[0] if labels else brand_code,
            )
            matches.append((0, row_dict, display_name))
            continue

        # Respaldo controlado para códigos como DEL_SABOR frente a "DEL SABOR".
        for requested_value in requested:
            if any(
                requested_value and label
                and (requested_value in label or label in requested_value)
                for label in normalized_labels
            ):
                display_name = next(
                    (
                        str(row_dict.get(column) or "").strip()
                        for column in ("nombre", "marca", "nombre_marca", "descripcion")
                        if column in row_dict and str(row_dict.get(column) or "").strip()
                    ),
                    labels[0] if labels else brand_code,
                )
                matches.append((1, row_dict, display_name))
                break

    if not matches:
        listed = ", ".join(available[:20]) or "sin registros"
        raise HTTPException(
            status_code=400,
            detail=(
                f"No se pudo asociar el canal {brand_code} con una marca de la BD. "
                f"Marcas disponibles: {listed}"
            ),
        )

    matches.sort(key=lambda item: (item[0], int(item[1]["id_marca"])))
    _, selected, display_name = matches[0]
    return {
        "id_marca": int(selected["id_marca"]),
        "brand_name": display_name,
    }


'''

if "def _greenie_brand_from_db(" not in text:
    anchor = "def _conversation_or_404(db: Session, conversation_id: int):"
    if anchor not in text:
        raise SystemExit("ERROR: no se encontro ancla para insertar el resolver de marcas")
    text = text.replace(anchor, helper + anchor, 1)
    print("AGREGADO: resolver de marcas desde BD")
else:
    print("YA_OK: resolver de marcas desde BD")

old_block = re.compile(
    r'''    brand_name, executive_name = BRANDS\[brand_code\]\n'''
    r'''    marca = db\.execute\(text\(""".*?'''
    r'''    if not marca:\n'''
    r'''        raise HTTPException\(status_code=400, detail=f"No se encontr[oó] la marca \{brand_name\} en el CRM"\)\n''',
    re.DOTALL,
)

replacement = '''    fallback_brand_name, executive_name = BRANDS[brand_code]\n    brand_db = _greenie_brand_from_db(\n        db,\n        brand_code=brand_code,\n        fallback_name=fallback_brand_name,\n    )\n    marca = int(brand_db["id_marca"])\n    brand_name = str(brand_db["brand_name"])\n'''

match = old_block.search(text)
if match:
    text = text[:match.start()] + replacement + text[match.end():]
    print("REEMPLAZADO: lookup fijo de marca en create-lead")
elif "brand_db = _greenie_brand_from_db(" in text:
    print("YA_OK: create-lead usa marcas de BD")
else:
    # Respaldo para variaciones menores del código local.
    start = text.find("    brand_name, executive_name = BRANDS[brand_code]", text.find("def create_lead_from_whatsapp"))
    error = text.find("No se encontró la marca", start)
    if start < 0 or error < 0:
        raise SystemExit(
            "ERROR: no se pudo localizar el lookup antiguo de marca. "
            "No se aplicaron cambios parciales."
        )
    line_end = text.find("\n", error)
    if line_end < 0:
        line_end = len(text)
    text = text[:start] + replacement + text[line_end + 1:]
    print("REEMPLAZADO: lookup fijo de marca mediante respaldo")

# La inserción debe usar el ID resuelto desde public.marcas.
text = text.replace('"id_marca": int(marca),', '"id_marca": marca,')

BACKEND.write_text(text, encoding="utf-8")
print("GREENIE_BRAND_FROM_DB_OK")
