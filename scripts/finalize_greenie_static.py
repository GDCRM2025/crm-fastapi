from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SRC_VIEW = ROOT / "web/views/tools_whatsapp.html"
SRC_WRAPPER = ROOT / "web/views/tools.html"
PANEL = ROOT / "web/js/panel.js"
DST_VIEW = ROOT / "web/views/tools_whatsapp_greenie.html"
DST_WRAPPER = ROOT / "web/views/tools_greenie.html"

view = SRC_VIEW.read_text(encoding="utf-8")
required = ["mediaFile", "micBtn", "send-media", "fetchMediaBlob"]
missing = [x for x in required if x not in view]
if missing:
    raise SystemExit("FALTAN MARCADORES MULTIMEDIA EN tools_whatsapp.html: " + ", ".join(missing))

view = view.replace("WhatsApp GIA", "WhatsApp Greenie")
view = view.replace("Ficha y nuevo requerimiento", "Ficha y nuevo requerimiento")
DST_VIEW.write_text(view, encoding="utf-8")
print("CREADO:", DST_VIEW.relative_to(ROOT))

wrapper = SRC_WRAPPER.read_text(encoding="utf-8")
wrapper = re.sub(
    r'url:"/web/views/tools_whatsapp(?:_greenie)?\.html(?:\?v=[^"]*)?"',
    'url:"/web/views/tools_whatsapp_greenie.html?v=20260726-greenie-physical1"',
    wrapper,
)
wrapper = wrapper.replace(
    '<iframe id="toolFrame" title="Tools"></iframe>',
    '<iframe id="toolFrame" title="Tools" allow="microphone"></iframe>',
)
wrapper = wrapper.replace("Correo (GIA)", "Correo (Greenie)")
wrapper = wrapper.replace("Instagram (GIA)", "Instagram (Greenie)")
DST_WRAPPER.write_text(wrapper, encoding="utf-8")
print("CREADO:", DST_WRAPPER.relative_to(ROOT))

panel = PANEL.read_text(encoding="utf-8")
panel = re.sub(
    r'/web/views/tools(?:_greenie)?\.html\?v=[^"#]*#whatsapp',
    '/web/views/tools_greenie.html?v=20260726-greenie-physical1#whatsapp',
    panel,
)
PANEL.write_text(panel, encoding="utf-8")
print("ACTUALIZADO:", PANEL.relative_to(ROOT))
print("GREENIE_STATIC_FINAL_OK")
