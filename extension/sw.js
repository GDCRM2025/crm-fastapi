const MENU_ROOT = "gd_root";
const DRAFT_KEY = "draft";
let _ensuringMenus = false;
let _ensureQueued = false;

const FIELDS = [
  { id: "to_nombre",     title: "ENVIAR A: NOMBRE", key: "nombre_cliente" },
  { id: "to_telefono",   title: "ENVIAR A: TELÉFONO", key: "telefono" },
  { id: "to_email",      title: "ENVIAR A: EMAIL", key: "email" },
  { id: "to_comuna",     title: "ENVIAR A: COMUNA", key: "comuna_text" },
  { id: "to_direccion",  title: "ENVIAR A: DIRECCIÓN", key: "direccion" },
  { id: "to_plataforma", title: "ENVIAR A: PLATAFORMA", key: "plataforma" },
  { id: "to_fecha",      title: "ENVIAR A: FECHA (YYYY-MM-DD)", key: "fecha_evento" },
];

function setDraftPatch(patch){
  return new Promise((resolve) => {
    chrome.storage.local.get([DRAFT_KEY], (r) => {
      const cur = r[DRAFT_KEY] || {};
      const next = Object.assign({}, cur, patch, { _updatedAt: Date.now() });
      chrome.storage.local.set({ [DRAFT_KEY]: next }, () => resolve());
    });
  });
}

function openPopupWindow(){
  // Preferimos el popup nativo (chico) del ícono, no una ventana gigante.
  // Nota: openPopup() puede fallar si Chrome no lo considera "user gesture".
  return new Promise((resolve) => {
    try{
      chrome.action.openPopup(() => {
        const err = chrome.runtime.lastError;
        resolve(!err);
      });
    }catch(_){
      resolve(false);
    }
  });
}

function safeMenuCreate(createProps){
  try{
    chrome.contextMenus.create(createProps, () => {
      // Evita que el service worker “crashee” por duplicados (MV3 puede ejecutar listeners varias veces).
      void chrome.runtime.lastError;
    });
  }catch(_){}
}

function ensureMenus(){
  // MV3: onInstalled + onStartup + wakeups pueden disparar esto muy seguido => duplicado de ids (gd_root).
  if (_ensuringMenus){
    _ensureQueued = true;
    return;
  }
  _ensuringMenus = true;
  chrome.contextMenus.removeAll(() => {
    safeMenuCreate({
      id: MENU_ROOT,
      title: "GD — Lead",
      contexts: ["selection", "page", "link", "editable"],
    });

    for (const f of FIELDS){
      safeMenuCreate({
        id: f.id,
        parentId: MENU_ROOT,
        title: f.title,
        contexts: ["selection"],
      });
    }

    safeMenuCreate({
      id: "open_create",
      parentId: MENU_ROOT,
      title: "ABRIR: CREAR LEAD",
      contexts: ["selection", "page", "link", "editable"],
    });

    _ensuringMenus = false;
    if (_ensureQueued){
      _ensureQueued = false;
      setTimeout(() => ensureMenus(), 0);
    }
  });
}

function setBadgeFromToken(token){
  const on = !!(token && token.length > 10);
  chrome.action.setBadgeText({ text: on ? "ON" : "" });
  chrome.action.setBadgeBackgroundColor({ color: on ? "#27e48b" : "#64748b" });
  chrome.action.setTitle({ title: on ? "LeadSuite GD (ON)" : "LeadSuite GD" });
}

function nudgeDraftReady(){
  // Pequeña señal visual de que hay borrador listo desde el menú contextual.
  chrome.action.setBadgeText({ text: "•" });
  chrome.action.setBadgeBackgroundColor({ color: "#f59e0b" });
  chrome.action.setTitle({ title: "LeadSuite GD — Borrador listo (haz click en el ícono)" });
  setTimeout(() => {
    chrome.storage.local.get(["token"], (r) => setBadgeFromToken(r.token || ""));
  }, 3500);
}

chrome.runtime.onInstalled.addListener(() => ensureMenus());
chrome.runtime.onStartup.addListener(() => ensureMenus());

chrome.contextMenus.onClicked.addListener(async (info) => {
  try{
    const sel = String(info.selectionText || "").trim();
    if (info.menuItemId === "open_create"){
      const ok = await openPopupWindow();
      if (!ok) nudgeDraftReady();
      return;
    }
    if (!sel){
      // Si no hay selección, igual abrimos el popup para pegar manual.
      const ok = await openPopupWindow();
      if (!ok) nudgeDraftReady();
      return;
    }
    const f = FIELDS.find(x => x.id === info.menuItemId);
    if (!f) return;
    await setDraftPatch({ [f.key]: sel });
    const ok = await openPopupWindow();
    if (!ok) nudgeDraftReady();
  }catch(_){}
});

chrome.storage.local.get(["token"], (r) => setBadgeFromToken(r.token || ""));
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local") return;
  if (changes.token) setBadgeFromToken(changes.token.newValue || "");
});

chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.type === "TOKEN_UPDATED"){
    chrome.storage.local.get(["token"], (r) => setBadgeFromToken(r.token || ""));
  }
});
