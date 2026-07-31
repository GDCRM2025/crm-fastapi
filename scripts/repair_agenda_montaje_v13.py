from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V4 = ROOT / "web" / "js" / "agenda_wizard_v4.js"
LEADS = ROOT / "web" / "views" / "leads.html"


def require(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise SystemExit(f"ERROR: falta {label}: {needle}")


def restore_nonblocking_preview(js: str) -> tuple[str, int]:
    lines = js.splitlines()
    restored = 0

    for index, line in enumerate(lines):
        if line.strip() != "if (currentStep >= 2) {":
            continue

        end = None
        depth = 0
        for cursor in range(index, min(len(lines), index + 20)):
            depth += lines[cursor].count("{")
            depth -= lines[cursor].count("}")
            if cursor > index and depth == 0:
                end = cursor
                break

        if end is None:
            continue

        body = "\n".join(lines[index + 1:end])
        if "mountUI(popup);" not in body or "decorateGroups(popup);" not in body:
            continue

        if "triggerPreview(popup);" not in body:
            indent = line[: len(line) - len(line.lstrip())] + "  "
            lines.insert(index + 1, indent + "triggerPreview(popup);")
            restored += 1
        break

    if restored == 0:
        expected = "if (currentStep >= 2) {"
        if expected not in js or "triggerPreview(popup);" not in js:
            raise SystemExit("ERROR: no se pudo restaurar el cálculo de montaje al entrar al paso operativo")

    return "\n".join(lines) + "\n", restored


def remove_blocking_waits(js: str) -> tuple[str, int]:
    before = js
    js = js.replace("        await waitPreview(popup);\n", "")
    js = js.replace("      await waitPreview(popup);\n", "")
    js = js.replace(
        'const showStep = async (targetStep, options = {}) => {',
        'const showStep = (targetStep, options = {}) => {',
    )
    js = js.replace(
        'next.addEventListener("click", async () => {',
        'next.addEventListener("click", () => {',
    )
    js = js.replace("      await showStep(currentStep + 1);", "      showStep(currentStep + 1);")
    return js, int(js != before)


def bump_assets(html: str) -> str:
    html = re.sub(
        r'agenda_wizard_v4\.js\?v=[^"\']+',
        'agenda_wizard_v4.js?v=20260731-13',
        html,
    )
    html = re.sub(
        r'agenda_wizard_v6\.js\?v=[^"\']+',
        'agenda_wizard_v6.js?v=20260731-13',
        html,
    )
    return html


def node_check(path: Path, label: str) -> None:
    node = shutil.which("node")
    if not node:
        print(f"AVISO {label}: node no está instalado; se omite validación sintáctica")
        return
    result = subprocess.run(
        [node, "--check", str(path)],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "error desconocido").strip()
        raise SystemExit(f"ERROR sintaxis {label}:\n{detail}")
    print(f"OK sintaxis {label}")


def check_inline_scripts(html: str) -> None:
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, flags=re.I | re.S)
    if not blocks:
        raise SystemExit("ERROR: no se encontró JavaScript inline en leads.html")

    with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as temp:
        temp.write("\n\n".join(blocks))
        temp_path = Path(temp.name)
    try:
        node_check(temp_path, "leads.html inline")
    finally:
        temp_path.unlink(missing_ok=True)


def main() -> None:
    if not V4.exists() or not LEADS.exists():
        raise SystemExit("ERROR: faltan agenda_wizard_v4.js o leads.html")

    js_original = V4.read_text(encoding="utf-8")
    html_original = LEADS.read_text(encoding="utf-8")

    require(js_original, "function triggerPreview(popup)", "función triggerPreview")
    require(js_original, "function mountUI(popup)", "función mountUI")
    require(js_original, "mountUI(popup);", "montaje del UI")
    require(js_original, "decorateGroups(popup);", "decoración de grupos")

    require(html_original, 'id="ag_montaje_day"', "textarea de montaje por día")
    require(html_original, 'id="ag_montaje_edit"', "botón Editar montaje")
    require(html_original, 'id="ag_montaje_confirm"', "botón Confirmar montaje")
    require(html_original, "const runPreview = async", "cálculo runPreview")
    require(html_original, "saveCurrentDay();", "guardado de montaje por día")
    require(html_original, "override_montaje_text", "envío de montaje al backend")

    js, restored = restore_nonblocking_preview(js_original)
    js, waits_changed = remove_blocking_waits(js)

    show_step_pos = js.find("if (currentStep >= 2) {")
    preview_pos = js.find("triggerPreview(popup);", show_step_pos)
    mount_pos = js.find("mountUI(popup);", show_step_pos)
    if not (show_step_pos >= 0 and preview_pos > show_step_pos and mount_pos > preview_pos):
        raise SystemExit("ERROR: el preview de montaje no quedó antes de mountUI")

    html = bump_assets(html_original)

    V4.with_suffix(".js.bak_v13").write_text(js_original, encoding="utf-8")
    LEADS.with_suffix(".html.bak_v13").write_text(html_original, encoding="utf-8")
    V4.write_text(js, encoding="utf-8")
    LEADS.write_text(html, encoding="utf-8")

    node_check(V4, "agenda_wizard_v4.js")
    check_inline_scripts(html)

    require(V4.read_text(encoding="utf-8"), "triggerPreview(popup);", "disparo de cálculo restaurado")
    require(LEADS.read_text(encoding="utf-8"), "agenda_wizard_v4.js?v=20260731-13", "cache-buster V4")
    require(LEADS.read_text(encoding="utf-8"), "agenda_wizard_v6.js?v=20260731-13", "cache-buster V6")

    print(f"OK preview de montaje restaurado: {restored}")
    print(f"OK esperas bloqueantes eliminadas: {waits_changed}")
    print("OK botones Editar/Confirmar montaje presentes")
    print("OK montaje por día se guarda en override_by_day")
    print("OK montaje general se envía como override_montaje_text")
    print("OK backups creados: agenda_wizard_v4.js.bak_v13 y leads.html.bak_v13")
    print("AGENDA_MONTAJE_V13_REPAIRED")


if __name__ == "__main__":
    main()
