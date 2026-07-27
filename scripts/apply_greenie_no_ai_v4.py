from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "backend" / "main.py"
VIEWS = [
    ROOT / "web" / "views" / "tools_whatsapp_greenie.html",
    ROOT / "web" / "views" / "tools_whatsapp.html",
]


def patch_main() -> None:
    text = MAIN.read_text(encoding="utf-8")
    text = text.replace('include_router_safe(app, "backend.routers.whatsapp_ai")\n', "")
    MAIN.write_text(text, encoding="utf-8")


def patch_view(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        '<script src="/web/js/greenie_precision_v3.js?v=20260727-precision3"></script>\n',
        "",
    )
    script = '<script src="/web/js/greenie_precision_v4.js?v=20260727-precision4-no-ai"></script>'
    if script not in text:
        text = text.replace("</body>", script + "\n</body>", 1)
    text = text.replace("BUILD 20260727-PRECISION3", "BUILD 20260727-PRECISION4")
    text = text.replace("BUILD 20260727-REAL", "BUILD 20260727-PRECISION4")
    path.write_text(text, encoding="utf-8")


def main() -> None:
    patch_main()
    for view in VIEWS:
        patch_view(view)
    print("GREENIE_NO_AI_V4_OK")
    print("- IA removida del backend y de la interfaz")
    print("- modal de lead preservado")
    print("- estabilizacion visual preservada")
    print("- llamadas quedan pendientes de WhatsApp Business Calling API")


if __name__ == "__main__":
    main()
