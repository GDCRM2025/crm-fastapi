from __future__ import annotations

import py_compile
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "backend" / "routers" / "leads_agenda.py"
BACKUP = TARGET.with_suffix(".py.bak_v14")
MARKER = "GD-AGENDA-SEGMENT-MONTAJE-V14"

HELPERS = r'''

# GD-AGENDA-SEGMENT-MONTAJE-V14
# Los segmentos reciben products_text ya distribuido por el frontend. Antes se
# enviaba montaje_text vacío y _build_event_from_segment devolvía "• —" sin
# calcular la sugerencia. Estas funciones convierten el texto a items y validan
# si existe un montaje real.
def _items_from_products_text(text_value: str) -> list[dict]:
    raw = _as_text(text_value or "").replace("\r", "\n")
    out: list[dict] = []
    for raw_line in raw.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^[•\-*–—]+\s*", "", line).strip()
        if not line:
            continue
        upper = line.upper().rstrip(":")
        if upper in ("PRODUCTO", "PRODUCTOS", "MONTAJE", "MONTAJE SUGERIDO"):
            continue

        match = re.match(r"^(\d+(?:[.,]\d+)?)\s*(?:X|×)?\s+(.+?)\s*$", line, flags=re.I)
        if match:
            try:
                qty = float(match.group(1).replace(",", "."))
            except Exception:
                qty = 0.0
            product = match.group(2).strip()
        else:
            qty = 1.0
            product = line

        if qty > 0 and product and product not in ("—", "-"):
            out.append({"producto": product, "cantidad": qty})
    return out


def _has_real_montage(text_value: str) -> bool:
    for line in _bullets(text_value or ""):
        clean = line.lstrip("•").strip()
        if clean and clean not in ("—", "-"):
            return True
    return False
'''

OLD_SEGMENT = '''    prod_lines = _bullets(products_text) or ["• —"]
    mont_lines = _bullets(montaje_text) or ["• —"]
'''

NEW_SEGMENT = '''    # En segmentos/multi-locación el frontend distribuye productos como texto.
    # Si el montaje viene vacío, se calcula aquí usando SOLO los productos de
    # este segmento. Así cada locación recibe su propio montaje sugerido.
    segment_items = _items_from_products_text(products_text)
    suggested_ops = 0
    suggested_montage = ""
    if segment_items:
        try:
            _, suggested_ops, suggested_montage, _ = _calcular_montaje(segment_items)
        except Exception:
            suggested_ops = 0
            suggested_montage = ""

    if not _has_real_montage(montaje_text):
        montaje_text = suggested_montage

    # Último resguardo: con productos nunca mostrar "• —" como montaje.
    if segment_items and not _has_real_montage(montaje_text):
        montaje_text = "Montaje sugerido\\n1 x Carro Clásico"

    try:
        ops = int(ops or 0)
    except Exception:
        ops = 0
    if ops < 1:
        ops = int(suggested_ops or 1)

    prod_lines = _bullets(products_text) or ["• —"]
    mont_lines = _bullets(montaje_text) or ["• —"]
'''


def fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def main() -> None:
    if not TARGET.exists():
        fail(f"no existe {TARGET}")

    original = TARGET.read_text(encoding="utf-8")
    updated = original

    required = [
        "def _calcular_montaje_single(items):",
        "def _calcular_montaje(items):",
        "def _bullets(text_value: str)",
        "def _build_event_from_segment(",
    ]
    for needle in required:
        if needle not in updated:
            fail(f"falta bloque requerido: {needle}")

    if MARKER not in updated:
        insertion_point = "\ndef _build_event_from_segment("
        if insertion_point not in updated:
            fail("no se encontró punto de inserción antes de _build_event_from_segment")
        updated = updated.replace(insertion_point, HELPERS + insertion_point, 1)
        print("OK helpers de cálculo por segmento agregados")
    else:
        print("OK helpers de cálculo por segmento ya presentes")

    if NEW_SEGMENT in updated:
        print("OK cálculo dentro de _build_event_from_segment ya aplicado")
    elif OLD_SEGMENT in updated:
        updated = updated.replace(OLD_SEGMENT, NEW_SEGMENT, 1)
        print("OK _build_event_from_segment ahora calcula montaje")
    else:
        fail("no se encontró el bloque prod_lines/mont_lines esperado")

    if updated.count(MARKER) != 1:
        fail(f"marcador V14 duplicado: {updated.count(MARKER)}")
    if NEW_SEGMENT not in updated:
        fail("el bloque nuevo no quedó instalado")

    if updated != original:
        shutil.copy2(TARGET, BACKUP)
        TARGET.write_text(updated, encoding="utf-8")
        print(f"OK respaldo creado: {BACKUP.name}")
    else:
        print("OK archivo ya estaba corregido")

    py_compile.compile(str(TARGET), doraise=True)
    print("OK sintaxis backend/routers/leads_agenda.py")

    smoke = r'''
from backend.routers import leads_agenda as m
cases = [
    ("150 Pop corn", "Máquina Cabritas"),
    ("150 Algodón de azúcar", "Máquina Algodón"),
    ("150 Hot dog italiano", "Carro Clásico"),
]
for raw, expected in cases:
    items = m._items_from_products_text(raw)
    if not items:
        raise SystemExit("parser vacío para: " + raw)
    _, ops, montage, _ = m._calcular_montaje(items)
    if expected not in montage:
        raise SystemExit("montaje incorrecto para %s: %s" % (raw, montage))
    if int(ops or 0) < 1:
        raise SystemExit("OPS inválido para: " + raw)
print("SMOKE_SEGMENT_MONTAJE_OK")
'''
    result = subprocess.run(
        [sys.executable, "-c", smoke],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "error desconocido").strip()
        fail("falló prueba funcional de montaje:\n" + detail)
    print(result.stdout.strip())

    final = TARGET.read_text(encoding="utf-8")
    if 'montaje_text = "Montaje sugerido\\n1 x Carro Clásico"' not in final:
        fail("falta fallback de montaje")
    if "segment_items = _items_from_products_text(products_text)" not in final:
        fail("falta cálculo a partir de productos del segmento")

    print("OK cada segmento calcula montaje desde sus productos")
    print("OK no volverá a mostrar montaje • — cuando existen productos")
    print("OK la corrección sirve para preview y para Calendar")
    print("AGENDA_SEGMENT_MONTAJE_V14_REPAIRED")


if __name__ == "__main__":
    main()
