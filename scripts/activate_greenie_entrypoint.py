from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "web" / "views" / "tools.html"
PANEL = ROOT / "web" / "js" / "panel.js"
GREENIE = ROOT / "web" / "views" / "tools_whatsapp_greenie.html"
VERSION = "20260727-greenie-live2"


def patch_tools() -> None:
    if not TOOLS.exists():
        raise SystemExit(f"No existe: {TOOLS}")
    text = TOOLS.read_text(encoding="utf-8")
    original = text

    text = re.sub(
        r'\{\s*id:"whatsapp",\s*label:"[^"]*",\s*ico:"💬",\s*url:"/web/views/tools_whatsapp(?:_greenie)?\.html(?:\?v=[^"]*)?"\s*\}',
        '{ id:"whatsapp", label:"WhatsApp Greenie", ico:"💬", url:"/web/views/tools_whatsapp_greenie.html?v=' + VERSION + '" }',
        text,
        count=1,
    )

    if text == original:
        raise SystemExit("No se encontro la entrada WhatsApp dentro de tools.html")

    if 'tools_whatsapp_greenie.html?v=' + VERSION not in text:
        raise SystemExit("No quedo activada la ruta Greenie en tools.html")

    TOOLS.write_text(text, encoding="utf-8")


def patch_panel() -> None:
    if not PANEL.exists():
        raise SystemExit(f"No existe: {PANEL}")
    text = PANEL.read_text(encoding="utf-8")
    original = text

    text = re.sub(
        r'(id:\s*"tool_wapp",\s*label:\s*)"[^"]*"(,\s*url:\s*"/web/views/tools\.html\?v=)[^"#]+(#whatsapp")',
        r'\1"WhatsApp Greenie"\2' + VERSION + r'\3',
        text,
        count=1,
    )

    # Actualiza las demas entradas del mismo wrapper para evitar que el navegador
    # reutilice tools.html con la version anterior.
    text = re.sub(
        r'/web/views/tools\.html\?v=[^"#]+#',
        '/web/views/tools.html?v=' + VERSION + '#',
        text,
    )

    if text == original:
        raise SystemExit("No se encontro el grupo Tools dentro de panel.js")
    if 'tool_wapp", label: "WhatsApp Greenie"' not in text:
        raise SystemExit("No quedo actualizado el item WhatsApp en panel.js")

    PANEL.write_text(text, encoding="utf-8")


def main() -> None:
    if not GREENIE.exists():
        raise SystemExit(
            "Falta web/views/tools_whatsapp_greenie.html. "
            "No se modificara el acceso para evitar dejar Tools en 404."
        )
    patch_tools()
    patch_panel()
    print("GREENIE_ENTRYPOINT_OK")
    print(f"TOOLS={TOOLS}")
    print(f"PANEL={PANEL}")
    print(f"VERSION={VERSION}")


if __name__ == "__main__":
    main()
