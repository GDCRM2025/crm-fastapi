from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    ROOT / "web" / "views" / "tools_whatsapp_greenie.html",
    ROOT / "web" / "views" / "tools_whatsapp.html",
)

REFRESH_OPEN = """async function refreshOpen(){if(!current)return;const id=current.id;const j=await getMessages(id);const sig=signature(j.items||[]);current=j.conversation;if(sig!==lastSignature){messages=j.items||[];lastSignature=sig;renderMessages()}await loadList()}
  async function refreshCommercial(){if(!current)return;const c=await getCommercial(current.id);const commercialSig=JSON.stringify(c||{});commercial=c;if(commercialSig!==lastCommercialSignature){lastCommercialSignature=commercialSig;renderSide()}}
  async function refreshAll(){if(!current){await loadList();return}await Promise.all([refreshOpen(),refreshCommercial()])}
  const signature="""


def replace_refresh_open(text: str, path: Path) -> str:
    if "async function refreshCommercial()" in text and "async function refreshAll()" in text:
        return text

    patterns = (
        re.compile(r"async function refreshOpen\(\)\{.*?\}\n\s*const signature=", re.S),
        re.compile(r"async function refreshOpen\s*\(\s*\)\s*\{.*?\}\s*const signature=", re.S),
    )
    for pattern in patterns:
        updated, count = pattern.subn(REFRESH_OPEN, text, count=1)
        if count == 1:
            return updated

    raise SystemExit(f"No se pudo localizar refreshOpen en {path}")


def patch_refresh_button(text: str) -> tuple[str, bool]:
    replacements = (
        (
            re.compile(r"\$\(\s*['\"]#refresh['\"]\s*\)\.onclick\s*=\s*poll\s*;"),
            "$('#refresh').onclick=refreshAll;",
        ),
        (
            re.compile(r"document\.getElementById\(\s*['\"]refresh['\"]\s*\)\.onclick\s*=\s*poll\s*;"),
            "document.getElementById('refresh').onclick=refreshAll;",
        ),
        (
            re.compile(r"\$\(\s*['\"]#refresh['\"]\s*\)\.addEventListener\(\s*['\"]click['\"]\s*,\s*poll\s*\)\s*;"),
            "$('#refresh').addEventListener('click',refreshAll);",
        ),
        (
            re.compile(r"document\.getElementById\(\s*['\"]refresh['\"]\s*\)\.addEventListener\(\s*['\"]click['\"]\s*,\s*poll\s*\)\s*;"),
            "document.getElementById('refresh').addEventListener('click',refreshAll);",
        ),
    )
    for pattern, replacement in replacements:
        updated, count = pattern.subn(replacement, text, count=1)
        if count == 1:
            return updated, True

    if "refreshAll" in text and "refresh" in text:
        return text, True

    return text, False


def patch(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = replace_refresh_open(text, path)

    # El polling periódico conserva solo mensajes/listado. Comercial 360 ya no se consulta aquí.
    poll_pattern = re.compile(
        r"async function poll\(\)\{if\(pollBusy\|\|document\.hidden\)return;pollBusy=true;try\{await loadList\(\);if\(current\)await refreshOpen\(\)\}catch\(_\)\{\s*\}finally\{pollBusy=false\}\}"
    )
    if not poll_pattern.search(text):
        if "async function poll()" not in text:
            raise SystemExit(f"No se encontro poll en {path}")

    text, refresh_found = patch_refresh_button(text)

    # Tras acciones manuales sí se refresca Comercial 360 una vez.
    text = text.replace(
        "await refreshOpen()}catch(e){r.innerHTML='<div class=\"err\">'+esc(e.message)+'</div>'}}",
        "await refreshAll()}catch(e){r.innerHTML='<div class=\"err\">'+esc(e.message)+'</div>'}}",
        1,
    )

    for old in (
        "BUILD 20260727-STABLE5",
        "BUILD 20260727-PRECISION4",
        "BUILD 20260727-REAL",
    ):
        text = text.replace(old, "BUILD 20260727-NO360POLL6")

    path.write_text(text, encoding="utf-8")
    if refresh_found:
        print(f"OK: polling Comercial 360 desactivado -> {path}")
    else:
        print(f"OK: polling Comercial 360 desactivado -> {path} (boton refresh sin patron conocido; no bloquea)")


def main() -> None:
    for path in FILES:
        patch(path)
    print("GREENIE_NO_360_POLL6_OK")


if __name__ == "__main__":
    main()
