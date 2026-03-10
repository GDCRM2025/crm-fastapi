(() => {
  try {
    const t =
      window.localStorage.getItem("token") ||
      window.sessionStorage.getItem("token") ||
      "";

    if (!t || t.length < 10) return;

    chrome.storage.local.get(["gdToken"], (r) => {
      const prev = r.gdToken || "";
      if (prev === t) return;
      chrome.storage.local.set({ gdToken: t, gdTokenAt: Date.now() });
    });
  } catch (e) {}
})();
