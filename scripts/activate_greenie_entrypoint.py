from __future__ import annotations

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "web" / "views" / "tools.html"
PANEL = ROOT / "web" / "js" / "panel.js"
GREENIE = ROOT / "web" / "views" / "tools_whatsapp_greenie.html"
LEGACY = ROOT / "web" / "views" / "tools_whatsapp.html"
VERSION = "20260727-greenie-real3"


def replace_matching_line(
    path: Path,
    matcher: re.Pattern[str],
    replacement_body: str,
    label: str,
) -> None:
    if not path.exists():
        raise SystemExit(f"No existe: {path}")

    original = path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    found = False

    for index, line in enumerate(lines):
        if not matcher.search(line):
            continue
        indent = line[: len(line) - len(line.lstrip())]
        newline = "\n" if line.endswith("\n") else ""
        lines[index] = indent + replacement_body + newline
        found = True
        break

    if not found:
        raise SystemExit(
            f"No se encontro {label} en {path}. "
            "Ejecuta: grep -n \"whatsapp\" " + str(path)
        )

    updated = "".join(lines)
    path.write_text(updated, encoding="utf-8")
    print(f"ACTUALIZADO: {label} -> {path}")


def patch_tools() -> None:
    replace_matching_line(
        TOOLS,
        re.compile(r'id\s*:\s*["\']whatsapp["\'].*tools_whatsapp', re.IGNORECASE),
        (
            '{ id:"whatsapp", label:"WhatsApp Greenie", ico:"💬", '
            f'url:"/web/views/tools_whatsapp_greenie.html?v={VERSION}" }},'
        ),
        "entrada WhatsApp del wrapper Tools",
    )


def patch_panel() -> None:
    replace_matching_line(
        PANEL,
        re.compile(r'id\s*:\s*["\']tool_wapp["\']', re.IGNORECASE),
        (
            '{ id: "tool_wapp", label: "WhatsApp Greenie", '
            f'url: "/web/views/tools.html?v={VERSION}#whatsapp" }},'
        ),
        "entrada WhatsApp del menu principal",
    )


def install_legacy_fallback() -> None:
    shutil.copyfile(GREENIE, LEGACY)
    print(f"REEMPLAZADO: vista legacy -> {LEGACY}")


def validate() -> None:
    tools_text = TOOLS.read_text(encoding="utf-8")
    panel_text = PANEL.read_text(encoding="utf-8")
    greenie_text = GREENIE.read_text(encoding="utf-8")
    legacy_text = LEGACY.read_text(encoding="utf-8")

    checks = {
        "tools apunta a Greenie": f"tools_whatsapp_greenie.html?v={VERSION}" in tools_text,
        "panel abre Greenie": f"tools.html?v={VERSION}#whatsapp" in panel_text,
        "vista Greenie real": "BUILD 20260727-REAL" in greenie_text,
        "fallback legacy Greenie": "BUILD 20260727-REAL" in legacy_text,
    }
    failed = [name for name, ok in checks.items() if not ok]
    for name, ok in checks.items():
        print(("OK" if ok else "ERROR") + ": " + name)
    if failed:
        raise SystemExit("Validacion fallida: " + ", ".join(failed))


def main() -> None:
    if not GREENIE.exists():
        raise SystemExit(
            "Falta web/views/tools_whatsapp_greenie.html. "
            "Descargalo desde origin/codex/venta-kpis-v2 antes de continuar."
        )
    patch_tools()
    patch_panel()
    install_legacy_fallback()
    validate()
    print("GREENIE_ENTRYPOINT_OK")
    print(f"VERSION={VERSION}")


if __name__ == "__main__":
    main()
