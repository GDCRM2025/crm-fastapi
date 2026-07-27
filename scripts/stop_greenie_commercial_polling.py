from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    ROOT / "web" / "views" / "tools_whatsapp_greenie.html",
    ROOT / "web" / "views" / "tools_whatsapp.html",
)


def patch(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    pattern = re.compile(
        r"async function refreshOpen\(\)\{.*?\}\n  const signature=",
        re.S,
    )
    replacement = """async function refreshOpen(){if(!current)return;const id=current.id;const j=await getMessages(id);const sig=signature(j.items||[]);current=j.conversation;if(sig!==lastSignature){messages=j.items||[];lastSignature=sig;renderMessages()}await loadList()}
  async function refreshCommercial(){if(!current)return;const c=await getCommercial(current.id);const commercialSig=JSON.stringify(c||{});commercial=c;if(commercialSig!==lastCommercialSignature){lastCommercialSignature=commercialSig;renderSide()}}
  async function refreshAll(){if(!current){await loadList();return}await Promise.all([refreshOpen(),refreshCommercial()])}
  const signature="""
    text, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise SystemExit(f"No se pudo reemplazar refreshOpen en {path}")

    old_poll = "async function poll(){if(pollBusy||document.hidden)return;pollBusy=true;try{await loadList();if(current)await refreshOpen()}catch(_){ }finally{pollBusy=false}}"
    new_poll = "async function poll(){if(pollBusy||document.hidden)return;pollBusy=true;try{await loadList();if(current)await refreshOpen()}catch(_){ }finally{pollBusy=false}}"
    if old_poll not in text:
        raise SystemExit(f"No se encontro poll en {path}")
    text = text.replace(old_poll, new_poll, 1)

    old_refresh = "$('#refresh').onclick=poll;"
    new_refresh = "$('#refresh').onclick=refreshAll;"
    if old_refresh not in text:
        raise SystemExit(f"No se encontro boton refresh en {path}")
    text = text.replace(old_refresh, new_refresh, 1)

    text = text.replace("await refreshOpen()}catch(e){r.innerHTML='<div class=\"err\">'+esc(e.message)+'</div>'}}", "await refreshAll()}catch(e){r.innerHTML='<div class=\"err\">'+esc(e.message)+'</div>'}}", 1)
    text = text.replace("BUILD 20260727-STABLE5", "BUILD 20260727-NO360POLL6")
    text = text.replace("BUILD 20260727-PRECISION4", "BUILD 20260727-NO360POLL6")
    text = text.replace("BUILD 20260727-REAL", "BUILD 20260727-NO360POLL6")

    path.write_text(text, encoding="utf-8")
    print(f"OK: polling Comercial 360 desactivado -> {path}")


def main() -> None:
    for path in FILES:
        patch(path)
    print("GREENIE_NO_360_POLL6_OK")


if __name__ == "__main__":
    main()
