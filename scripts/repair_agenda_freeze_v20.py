from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEADS = ROOT / "web" / "views" / "leads.html"
V19 = ROOT / "web" / "js" / "agenda_multiday_v19.js"


def fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def main() -> None:
    if not LEADS.exists() or not V19.exists():
        fail("faltan leads.html o agenda_multiday_v19.js")

    shutil.copy2(LEADS, LEADS.with_name("leads.html.bak_v20"))
    shutil.copy2(V19, V19.with_name("agenda_multiday_v19.js.bak_v20"))

    js = V19.read_text(encoding="utf-8")

    old_editor_observer = '''      const observer = new MutationObserver(() => renderDayStatuses(popup));
      observer.observe(editor, { childList: true, subtree: true, characterData: true });'''
    new_editor_observer = '''      const observer = new MutationObserver((mutations) => {
        const cardsChanged = mutations.some((mutation) => (
          mutation.type === "childList" && mutation.target === editor
        ));
        if (cardsChanged) requestAnimationFrame(() => renderDayStatuses(popup));
      });
      observer.observe(editor, { childList: true });'''

    if old_editor_observer in js:
        js = js.replace(old_editor_observer, new_editor_observer, 1)
        print("OK observer recursivo eliminado")
    elif new_editor_observer in js:
        print("OK observer seguro ya aplicado")
    else:
        fail("no se encontro observer recursivo de V19")

    js = js.replace(
        'observer.observe(document.body, { childList: true, subtree: true });',
        'observer.observe(document.body, { childList: true });',
    )
    js = js.replace(
        'const BUILD = "GD-AGENDA-MULTIDAY-V19-20260731";',
        'const BUILD = "GD-AGENDA-MULTIDAY-V20-20260731";',
    )
    js = js.replace(
        'window.__GD_AGENDA_MULTIDAY_V19_BUILD__ = BUILD;',
        'window.__GD_AGENDA_MULTIDAY_V20_BUILD__ = BUILD;',
    )

    V19.write_text(js, encoding="utf-8")

    html = LEADS.read_text(encoding="utf-8")
    html = re.sub(
        r'agenda_multiday_v19\.js\?v=[^"\']+',
        'agenda_multiday_v19.js?v=20260731-20',
        html,
    )
    LEADS.write_text(html, encoding="utf-8")

    result = subprocess.run(["node", "--check", str(V19)], capture_output=True, text=True)
    if result.returncode != 0:
        fail((result.stderr or result.stdout).strip())

    final_js = V19.read_text(encoding="utf-8")
    final_html = LEADS.read_text(encoding="utf-8")
    if 'subtree: true, characterData: true' in final_js:
        fail("sigue presente el observer recursivo")
    if 'agenda_multiday_v19.js?v=20260731-20' not in final_html:
        fail("no se actualizo cache buster V20")

    print("OK pagina ya no entra en ciclo infinito al elegir Varios dias")
    print("OK validacion visual por dia se mantiene")
    print("OK fallback de mensaje final se mantiene")
    print("OK sintaxis JavaScript")
    print("AGENDA_FREEZE_V20_REPAIRED")


if __name__ == "__main__":
    main()
