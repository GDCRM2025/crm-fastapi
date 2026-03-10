(() => {
  // Lee token del CRM y lo sincroniza a la extensión (autologin)
  function readToken() {
    try {
      return localStorage.getItem("token") || sessionStorage.getItem("token") || "";
    } catch {
      return "";
    }
  }

  let last = "";
  async function sync() {
    const t = String(readToken() || "").trim();
    if (t && t !== last) {
      last = t;
      try {
        await chrome.runtime.sendMessage({ cmd: "syncAuthFromCRM", token: t });
      } catch {}
    }
  }

  // intenta al cargar y luego cada 2s mientras el CRM esté abierto
  sync();
  setInterval(sync, 2000);
})();
