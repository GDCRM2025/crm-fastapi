from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
GREENIE = ROOT / "web/views/tools_whatsapp_greenie.html"
LEGACY = ROOT / "web/views/tools_whatsapp.html"
TOOLS = ROOT / "web/views/tools.html"
COTIZADOR = ROOT / "web/js/cotizador.js"
VERSION = "20260727-workspace2"


def patch_greenie(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    marker = '<script src="/web/js/greenie_workspace_v2.js?v=' + VERSION + '"></script>'
    if marker not in text:
        if "</body>" not in text:
            raise SystemExit(f"ERROR: {path} no tiene </body>")
        text = text.replace("</body>", f"  {marker}\n</body>", 1)
    path.write_text(text, encoding="utf-8")
    print("GREENIE PATCH:", path)


def patch_tools() -> None:
    text = TOOLS.read_text(encoding="utf-8")
    # Tools debe mostrar sólo módulos que hoy tienen flujo operativo.
    text = re.sub(
        r'\s*\{ id:"correo".*?\n',
        "",
        text,
        count=1,
    )
    text = re.sub(
        r'\s*\{ id:"instagram".*?\n',
        "",
        text,
        count=1,
    )
    text = re.sub(
        r'\{ id:"whatsapp",\s*label:"[^"]*",\s*ico:"💬",\s*url:"[^"]+"\s*\}',
        '{ id:"whatsapp", label:"WhatsApp Greenie", ico:"💬", url:"/web/views/tools_whatsapp_greenie.html?v=' + VERSION + '" }',
        text,
        count=1,
    )
    TOOLS.write_text(text, encoding="utf-8")
    print("TOOLS PATCH:", TOOLS)


def patch_cotizador() -> None:
    text = COTIZADOR.read_text(encoding="utf-8")
    if "const greenieConversationId" not in text:
        anchor = '  const leadId = +(qs.get("id_lead") || qs.get("lead") || qs.get("lead_id") || 0);\n'
        if anchor not in text:
            raise SystemExit("ERROR: no se encontró leadId en cotizador.js")
        text = text.replace(
            anchor,
            anchor
            + '  const greenieConversationId = +(qs.get("conversation_id") || 0);\n'
            + '  const greenieSource = String(qs.get("source") || "").toLowerCase();\n'
            + '  const isGreenieEmbed = greenieSource === "greenie" && greenieConversationId > 0;\n',
            1,
        )

    old = '''      // Descargar PDF de inmediato (sin abrir pestañas).
      if (currentQuoteId){
        try{
          await downloadQuotePdf(currentQuoteId, currentQuoteNumero, cliente.value || lead?.cliente || "");
        }catch(err){
          console.error(err);
          if (window.Swal){
            await Swal.fire({
              icon:"info",
              title:"PDF listo",
              text:"No pude iniciar la descarga automática. Puedes descargar desde Historial.",
              timer: 1800,
              showConfirmButton: false
            });
          }
        }
      }

      setTimeout(() => {
        location.href = apiURL(`/web/views/historial_cotizaciones.html?id_lead=${leadId}`);
      }, 220);
'''
    new = '''      if (isGreenieEmbed && currentQuoteId){
        try{
          await fetchJson(`/gia/whatsapp/conversations/${greenieConversationId}/leads/${leadId}/send-quote`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id_cotizacion: currentQuoteId })
          });
          if (window.Swal){
            await Swal.fire({
              icon:"success",
              title:"Cotización guardada y enviada",
              text:"El PDF fue enviado al cliente por WhatsApp.",
              timer:1200,
              showConfirmButton:false
            });
          }
          try{
            window.parent?.postMessage({
              type:"greenie:quote-sent",
              lead_id:leadId,
              conversation_id:greenieConversationId,
              id_cotizacion:currentQuoteId
            }, location.origin);
          }catch(_){ }
          return;
        }catch(err){
          console.error(err);
          if (window.Swal){
            await Swal.fire({
              icon:"warning",
              title:"Cotización guardada",
              text:"Se guardó, pero no se pudo enviar automáticamente por WhatsApp: " + String(err?.message || err).slice(0,220)
            });
          }
          return;
        }
      }

      // Flujo normal fuera de Greenie: descarga y abre historial.
      if (currentQuoteId){
        try{
          await downloadQuotePdf(currentQuoteId, currentQuoteNumero, cliente.value || lead?.cliente || "");
        }catch(err){
          console.error(err);
          if (window.Swal){
            await Swal.fire({
              icon:"info",
              title:"PDF listo",
              text:"No pude iniciar la descarga automática. Puedes descargar desde Historial.",
              timer: 1800,
              showConfirmButton: false
            });
          }
        }
      }

      setTimeout(() => {
        location.href = apiURL(`/web/views/historial_cotizaciones.html?id_lead=${leadId}`);
      }, 220);
'''
    if new not in text:
        if old not in text:
            raise SystemExit("ERROR: no se encontró bloque post-guardado del cotizador")
        text = text.replace(old, new, 1)

    text = text.replace(
        '  $("btnCerrar").addEventListener("click", () => { location.href = apiURL("/web/views/leads.html"); });',
        '  $("btnCerrar").addEventListener("click", () => {\n'
        '    if (isGreenieEmbed){\n'
        '      try{ window.parent?.postMessage({ type:"greenie:close-workspace" }, location.origin); }catch(_){}\n'
        '      return;\n'
        '    }\n'
        '    location.href = apiURL("/web/views/leads.html");\n'
        '  });',
    )
    COTIZADOR.write_text(text, encoding="utf-8")
    print("COTIZADOR PATCH:", COTIZADOR)


def main() -> None:
    if not GREENIE.exists():
        raise SystemExit("ERROR: falta tools_whatsapp_greenie.html")
    if not (ROOT / "web/js/greenie_workspace_v2.js").exists():
        raise SystemExit("ERROR: falta greenie_workspace_v2.js")
    patch_greenie(GREENIE)
    LEGACY.write_bytes(GREENIE.read_bytes())
    print("LEGACY SYNC:", LEGACY)
    patch_tools()
    patch_cotizador()
    print("GREENIE_WORKSPACE_V2_OK")
    print("VERSION=" + VERSION)


if __name__ == "__main__":
    main()
