
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg?.cmd !== "get-token") return;

  try{
    const t = localStorage.getItem("token") || sessionStorage.getItem("token") || "";
    sendResponse({ ok:true, token: t });
  }catch(e){
    sendResponse({ ok:false, error: String(e?.message || e) });
  }
  return true;
});
