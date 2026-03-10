
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg?.cmd !== "extract") return;

  try{
    const url = location.href;
    const notas = `WHATSAPP: ${url}\n(Extracción avanzada: próxima iteración con DOM real + botón copiar al lead)`;
    sendResponse({ ok:true, data:{ nombre:"", email:"", telefono:"", notas }});
  }catch(e){
    sendResponse({ ok:false, error: String(e?.message || e) });
  }
  return true;
});
