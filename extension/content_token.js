(() => {
  function readToken(){
    try{
      return (
        window.localStorage.getItem("token") ||
        window.sessionStorage.getItem("token") ||
        window.localStorage.getItem("gd_token") ||
        window.sessionStorage.getItem("gd_token") ||
        ""
      );
    }catch(_){
      return "";
    }
  }

  function syncOnce(){
    try{
      const t = readToken();
      if (!t || t.length < 10) return;
      chrome.storage.local.get(["token"], (r) => {
        const prev = r.token || "";
        if (prev === t) return;
        chrome.storage.local.set({ token: t, tokenAt: Date.now() }, () => {
          try{ chrome.runtime.sendMessage({ type: "TOKEN_UPDATED" }); }catch(_){}
        });
      });
    }catch(_){}
  }

  // 1) al cargar
  syncOnce();

  // 2) si el token cambia por login/logout sin recargar, lo capturamos igual
  let last = readToken();
  setInterval(() => {
    const cur = readToken();
    if (cur && cur !== last){
      last = cur;
      syncOnce();
    }
  }, 2500);
})();
