
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg?.cmd !== "extract") return;

  try{
    const url = location.href;
    let nombre = "";
    let notas = `INSTAGRAM: ${url}`;

    // intento simple de username desde url
    const m = url.match(/instagram\.com\/([^\/\?\#]+)/i);
    if (m && m[1] && m[1] !== "direct") {
      nombre = m[1].toUpperCase();
      notas += `\nUSUARIO: @${m[1]}`;
    }

    sendResponse({ ok:true, data:{ nombre, email:"", telefono:"", notas }});
  }catch(e){
    sendResponse({ ok:false, error: String(e?.message || e) });
  }
  return true;
});
