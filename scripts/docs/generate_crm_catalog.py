#!/usr/bin/env python3
"""Generate evidence-backed CRM catalogs from navigation, HTML and FastAPI source."""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "user-guide" / "source"


def decode_js(value: str) -> str:
    """Decode the escape forms used by the bundled navigation without damaging UTF-8."""
    value = re.sub(r"\\u\{([0-9A-Fa-f]+)\}", lambda m: chr(int(m.group(1), 16)), value)
    value = re.sub(r"\\u([0-9A-Fa-f]{4})", lambda m: chr(int(m.group(1), 16)), value)
    value = re.sub(r"\\x([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), value)
    return value.replace("\\/", "/").replace("\\\"", "\"")


class ViewParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(); self.title = ""; self._title = False; self._button = False
        self.buttons: list[str] = []; self.fields: list[dict] = []; self._button_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title": self._title = True
        if tag == "button": self._button = True; self._button_text = []
        if tag in {"input", "select", "textarea"}:
            self.fields.append({"tag": tag, "name": a.get("name") or a.get("id") or "No determinado", "type": a.get("type", tag), "required": "required" in a})

    def handle_endtag(self, tag):
        if tag == "title": self._title = False
        if tag == "button":
            value = " ".join("".join(self._button_text).split())
            if value: self.buttons.append(value)
            self._button = False

    def handle_data(self, data):
        if self._title: self.title += data
        if self._button: self._button_text.append(data)


def navigation() -> list[dict]:
    text = (ROOT / "web/js/panel.js").read_text(encoding="utf-8")
    pattern = re.compile(r'\{\s*id:\s*"(?P<id>[^"]+)"\s*,\s*label:\s*"(?P<label>[^"]+)"\s*,\s*url:\s*"(?P<url>[^"]+)"(?P<tail>[^}]*)\}')
    items = []; seen: set[tuple[str, str]] = set()
    for m in pattern.finditer(text):
        url = decode_js(m.group("url"))
        if "/web/views/" not in url: continue
        key = (m.group("id"), url)
        if key in seen: continue
        seen.add(key)
        prefix = text[max(0, m.start()-1800):m.start()]
        titles = re.findall(r'title:\s*"([^"]+)"', prefix)
        items.append({"id": m.group("id"), "label": decode_js(m.group("label")), "url": url,
                      "menu_group": titles[-1] if titles else "No determinado", "gd_permission": (re.search(r'gdPermission:\s*"([^"]+)"', m.group("tail")) or [None, None])[1]})
    return items


def view_evidence(item: dict) -> dict:
    path = urlsplit(item["url"]).path.lstrip("/")
    file = ROOT / path
    result = {**item, "file": str(file.relative_to(ROOT)), "exists": file.exists(), "title": "No determinado", "buttons": [], "fields": [], "api_references": []}
    if not file.exists(): return result
    raw = file.read_text(encoding="utf-8", errors="replace")
    p = ViewParser(); p.feed(raw)
    result.update(title=" ".join(p.title.split()) or "No determinado", buttons=sorted(set(p.buttons)), fields=p.fields,
                  api_references=sorted(set(re.findall(r"['\"`](/(?:crm/)?api/[A-Za-z0-9_./?={}&:$-]+)", raw))))
    return result


def endpoints() -> list[dict]:
    found = []
    for file in sorted((ROOT / "backend/routers").glob("*.py")):
        raw = file.read_text(encoding="utf-8", errors="replace")
        prefix_match = re.search(r"APIRouter\([^\n]*prefix\s*=\s*[\"']([^\"']+)", raw)
        prefix = prefix_match.group(1) if prefix_match else ""
        for method, path, func in re.findall(r"@router\.(get|post|put|patch|delete)\([\"']([^\"']*)[\"'][^\n]*\)\s*(?:\n@[^\n]+)*\s*\ndef\s+(\w+)", raw):
            found.append({"method": method.upper(), "path": prefix + path, "handler": func, "file": str(file.relative_to(ROOT))})
    return found


def md_table(headers, rows):
    clean = lambda v: str(v if v not in (None, "") else "—").replace("|", "\\|").replace("\n", " ")
    return "| " + " | ".join(headers) + " |\n|" + "|".join("---" for _ in headers) + "|\n" + "\n".join("| " + " | ".join(clean(v) for v in row) + " |" for row in rows) + "\n"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    screens = [view_evidence(x) for x in navigation()]
    routes = endpoints()
    payload = {"generated_from": ["web/js/panel.js", "web/views/*.html", "backend/routers/*.py"], "screens": screens, "routes": routes}
    (OUT / "screen_catalog.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    screen_rows = [(x["menu_group"], x["id"], x["label"], x["title"], x["file"], x["gd_permission"] or "Permiso de menú por rol", "Sí" if x["exists"] else "NO") for x in screens]
    (OUT / "SCREEN_CATALOG.md").write_text("# Catálogo de pantallas\n\nFuente: navegación y HTML reales. Los datos no inferibles quedan declarados como tales.\n\n" + md_table(["Módulo","ID","Pantalla","Título","Archivo","Acceso","Existe"], screen_rows), encoding="utf-8")
    actions = []
    for x in screens:
        if not x["buttons"]: actions.append((x["id"], x["label"], "No determinado automáticamente; revisión manual requerida", x["file"]))
        else:
            actions.extend((x["id"], x["label"], b, x["file"]) for b in x["buttons"])
    (OUT / "ACTION_CATALOG.md").write_text("# Catálogo de acciones\n\nAcciones visibles extraídas de botones HTML; deben verificarse en pruebas de interacción.\n\n" + md_table(["Pantalla ID","Pantalla","Acción visible","Evidencia"], actions), encoding="utf-8")
    route_rows = [(x["method"], x["path"], x["handler"], x["file"]) for x in routes]
    (OUT / "CRM_FEATURE_INVENTORY.md").write_text(f"# Inventario funcional CRM\n\nCobertura descubierta: **{len(screens)} accesos de menú** y **{len(routes)} endpoints FastAPI**. Esto es inventario técnico, no afirmación de QA funcional.\n\n## Endpoints\n\n" + md_table(["Método","Ruta","Handler","Archivo"], route_rows), encoding="utf-8")
    permission_rows = [(x["id"], x["label"], x["gd_permission"] or "Controlado por PERMISSIONS del panel", "Backend y UI deben validarse conjuntamente") for x in screens]
    (OUT / "PERMISSION_MATRIX.md").write_text("# Matriz de permisos\n\nLa visibilidad del menú no sustituye autorización backend.\n\n" + md_table(["ID","Función","Permiso explícito","Gate"], permission_rows), encoding="utf-8")
    coverage = [(x["id"], x["label"], "PASS" if x["exists"] else "FAIL", "CONTEXTUAL" if x["id"] in {"leads_ver","gdi_utm","gdi_integrations","gdi_paid_media","tool_wapp"} else "PENDIENTE DE ARTÍCULO ESPECÍFICO") for x in screens]
    (OUT / "DOCUMENTATION_COVERAGE.md").write_text("# Cobertura documental\n\nEl botón global `?` envía el `screen_id` activo al motor. Esta tabla separa acceso descubierto de artículo contextual específico.\n\n" + md_table(["ID","Pantalla","Archivo","Ayuda específica"], coverage), encoding="utf-8")
    print(f"CATALOG_OK screens={len(screens)} routes={len(routes)} out={OUT}")


if __name__ == "__main__": main()
