from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
main_path = ROOT / "backend" / "main.py"
source_view = ROOT / "web" / "views" / "tools_whatsapp_v2.html"
target_view = ROOT / "web" / "views" / "tools_whatsapp.html"

if not main_path.exists():
    raise SystemExit(f"No existe {main_path}")
if not source_view.exists():
    raise SystemExit(f"No existe {source_view}")

content = main_path.read_text(encoding="utf-8")
anchor = 'include_router_safe(app, "backend.routers.whatsapp_webhook")'
new_line = 'include_router_safe(app, "backend.routers.whatsapp_gia")'

if new_line not in content:
    if anchor not in content:
        raise SystemExit("No se encontró el router whatsapp_webhook en backend/main.py")
    content = content.replace(anchor, anchor + "\n" + new_line, 1)
    main_path.write_text(content, encoding="utf-8")
    print("Router whatsapp_gia registrado en backend/main.py")
else:
    print("Router whatsapp_gia ya estaba registrado")

shutil.copy2(source_view, target_view)
print("Vista WhatsApp GIA instalada en web/views/tools_whatsapp.html")
print("INSTALACION_WHATSAPP_GIA_OK")
