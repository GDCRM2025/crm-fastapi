from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise SystemExit(f"No se encontro bloque esperado en {path}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


main = ROOT / "backend" / "main.py"
replace_once(
    main,
    'include_router_safe(app, "backend.routers.whatsapp_webhook")\n',
    'include_router_safe(app, "backend.routers.whatsapp_webhook")\n'
    'include_router_safe(app, "backend.routers.whatsapp_ai")\n',
)

for relative in (
    "web/views/tools_whatsapp_greenie.html",
    "web/views/tools_whatsapp.html",
):
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    script = '<script src="/web/js/greenie_precision_v3.js?v=20260727-precision3"></script>'
    if script not in text:
        marker = "</body>"
        if marker not in text:
            raise SystemExit(f"No se encontro </body> en {path}")
        text = text.replace(marker, f"{script}\n{marker}", 1)
    text = text.replace("BUILD 20260727-REAL", "BUILD 20260727-PRECISION3")
    path.write_text(text, encoding="utf-8")

print("GREENIE_PRECISION_V3_OK")
print("FILES:")
print("- backend/main.py")
print("- backend/routers/whatsapp_ai.py")
print("- web/js/greenie_precision_v3.js")
print("- web/views/tools_whatsapp_greenie.html")
print("- web/views/tools_whatsapp.html")
