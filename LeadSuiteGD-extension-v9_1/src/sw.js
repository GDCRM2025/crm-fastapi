const API_BASE = "http://127.0.0.1:8000";
const MENU_ROOT = "gd_root";
let _ensuringMenus = false;
let _ensureQueued = false;
const FIELDS = [
  { id: "to_nombre",     title: "ENVIAR A: NOMBRE" },
  { id: "to_telefono",   title: "ENVIAR A: TELÉFONO" },
  { id: "to_email",      title: "ENVIAR A: EMAIL" },
  { id: "to_comuna",     title: "ENVIAR A: COMUNA" },
  { id: "to_direccion",  title: "ENVIAR A: DIRECCIÓN" },
  { id: "to_plataforma", title: "ENVIAR A: PLATAFORMA" },
  { id: "to_fecha",      title: "ENVIAR A: FECHA (YYYY-MM-DD)" }
];

function setDraftPatch(patch) {
  return new Promise((resolve) => {
    chrome.storage.local.get(["gdDraft"], (r) => {
      const cur = r.gdDraft || {};
      const next = Object.assign({}, cur, patch, { _updatedAt: Date.now() });
      chrome.storage.local.set({ gdDraft: next }, () => resolve());
    });
  });
}

function openPopupWindow() {
  return new Promise((resolve) => {
    const url = chrome.runtime.getURL("src/popup.html");
    chrome.windows.create(
      { url, type: "popup", width: 420, height: 640, focused: true },
      () => resolve()
    );
  });
}

function safeMenuCreate(createProps){
  try{
    chrome.contextMenus.create(createProps, () => {
      void chrome.runtime.lastError;
    });
  }catch(_){}
}

function ensureMenus() {
  if (_ensuringMenus){
    _ensureQueued = true;
    return;
  }
  _ensuringMenus = true;
  chrome.contextMenus.removeAll(() => {
    safeMenuCreate({
      id: MENU_ROOT,
      title: "GD — Lead",
      contexts: ["selection"]
    });

    FIELDS.forEach((f) => {
      safeMenuCreate({
        id: f.id,
        parentId: MENU_ROOT,
        title: f.title,
        contexts: ["selection"]
      });
    });

    safeMenuCreate({
      id: "open_create",
      parentId: MENU_ROOT,
      title: "ABRIR: CREAR LEAD",
      contexts: ["selection"]
    });

    _ensuringMenus = false;
    if (_ensureQueued){
      _ensureQueued = false;
      setTimeout(() => ensureMenus(), 0);
    }
  });
}

chrome.runtime.onInstalled.addListener(() => ensureMenus());
chrome.runtime.onStartup.addListener(() => ensureMenus());

chrome.contextMenus.onClicked.addListener(async (info) => {
  try {
    const sel = (info.selectionText || "").trim();
    if (info.menuItemId === "open_create") {
      await openPopupWindow();
      return;
    }
    if (!sel) return;

    const patch = {};
    switch (info.menuItemId) {
      case "to_nombre": patch.nombre_cliente = sel; break;
      case "to_telefono": patch.telefono = sel; break;
      case "to_email": patch.email = sel; break;
      case "to_comuna": patch.comuna_text = sel; break;
      case "to_direccion": patch.direccion = sel; break;
      case "to_plataforma": patch.plataforma = sel; break;
      case "to_fecha": patch.fecha_evento = sel; break;
      default: return;
    }
    await setDraftPatch(patch);
    await openPopupWindow();
  } catch (e) {
    // noop
  }
});


function setBadgeFromToken(token){
  const on = !!(token && token.length > 10);
  chrome.action.setBadgeText({ text: on ? "ON" : "" });
  chrome.action.setBadgeBackgroundColor({ color: on ? "#27e48b" : "#64748b" });
  chrome.action.setTitle({ title: on ? "GD (ON)" : "GD" });
}

chrome.storage.local.get(["gdToken"], (r)=> setBadgeFromToken(r.gdToken || ""));
chrome.storage.onChanged.addListener((changes, area)=>{
  if(area !== "local") return;
  if(changes.gdToken){
    setBadgeFromToken(changes.gdToken.newValue || "");
  }
});
