from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "backend" / "main.py"
LEADS = ROOT / "web" / "views" / "leads.html"


def patch_main() -> None:
    text = MAIN.read_text(encoding="utf-8")
    line = 'include_router_safe(app, "backend.routers.agenda_confirmacion")'
    if line not in text:
        anchor = 'include_router_safe(app, "backend.routers.leads_agenda")'
        if anchor not in text:
            raise SystemExit("No se encontró el router leads_agenda en backend/main.py")
        text = text.replace(anchor, anchor + "\n" + line, 1)
    MAIN.write_text(text, encoding="utf-8")
    print("OK router agenda_confirmacion")


def patch_frontend() -> None:
    text = LEADS.read_text(encoding="utf-8")

    old = '''  const out = await apiJSON(`/leads/${lead.id_lead}/move`, {
    method:"POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify(payload),
  });
  const ev = out?.evento || {};

  let gcal = null;
  try{
    gcal = await approveAgendaWithRetry();
  }catch(err){
    await Swal.fire({
      icon:"error",
      title:"No se pudo agendar",
      text: String(err?.message || err),
    });
    return false;
  }
'''

    new = '''  const idempotencyKey = `agenda-${lead.id_lead}-${Date.now()}-${Math.random().toString(36).slice(2,10)}`;
  await Swal.fire({
    title:"Agendando…",
    text:"No cierre esta ventana. Estamos confirmando el lead y creando el calendario.",
    allowOutsideClick:false,
    allowEscapeKey:false,
    showConfirmButton:false,
    didOpen:()=>Swal.showLoading(),
    timer:350,
  });

  let out = null;
  try{
    out = await apiJSON(`/leads/${lead.id_lead}/confirmar_agendamiento`, {
      method:"POST",
      headers:{
        "Content-Type":"application/json",
        "X-Idempotency-Key":idempotencyKey,
      },
      body:JSON.stringify(payload),
    });
  }catch(err){
    await Swal.fire({
      icon:"error",
      title:"No se pudo confirmar y agendar",
      text:String(err?.message || err),
    });
    return false;
  }
  const ev = out?.evento || {};
  const gcal = out;
'''

    if old not in text:
        if "/confirmar_agendamiento" in text:
            print("OK frontend ya usa endpoint único")
            return
        # Tolerancia a espacios/tabs distintos.
        pattern = re.compile(
            r'''\s*const out = await apiJSON\(`/leads/\$\{lead\.id_lead\}/move`, \{\s*method:"POST",\s*headers: \{"Content-Type":"application/json"\},\s*body: JSON\.stringify\(payload\),\s*\}\);\s*const ev = out\?\.evento \|\| \{\};\s*let gcal = null;\s*try\{\s*gcal = await approveAgendaWithRetry\(\);\s*\}catch\(err\)\{\s*await Swal\.fire\(\{\s*icon:"error",\s*title:"No se pudo agendar",\s*text: String\(err\?\.message \|\| err\),\s*\}\);\s*return false;\s*\}''',
            re.S,
        )
        text, count = pattern.subn("\n" + new.rstrip(), text, count=1)
        if count != 1:
            raise SystemExit("No se encontró el bloque de doble llamada en leads.html")
    else:
        text = text.replace(old, new, 1)

    marker = "<!-- build: GD-LEADS-2026-02-25-13 -->"
    if marker in text:
        text = text.replace(marker, "<!-- build: GD-LEADS-AGENDA-UNIFICADA-V1 -->", 1)

    LEADS.write_text(text, encoding="utf-8")
    print("OK frontend endpoint único")


def main() -> None:
    patch_main()
    patch_frontend()
    print("AGENDA_UNIFICADA_V1_INSTALLED")


if __name__ == "__main__":
    main()
