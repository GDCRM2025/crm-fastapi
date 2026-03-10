const CRM_ORIGIN = "http://127.0.0.1:8000";

// draft guardado por usuario (en storage local)
async function getDraft() {
  const { draft = {} } = await chrome.storage.local.get(["draft"]);
  return draft || {};
}
async function setDraft(patch) {
  const draft = await getDraft();
  await chrome.storage.local.set({ draft: { ...draft, ...patch } });
}

function getSelText(info) {
  const t = (info.selectionText || "").trim();
  return t.length ? t : "";
}

async function ensureTokenFromCrmTab() {
  // busca un tab del CRM abierto y lee token desde localStorage/sessionStorage
  const tabs = await chrome.tabs.query({ url: CRM_ORIGIN + "/*" });
  if (!tabs.length) return null;

  const tabId = tabs[0].id;
  const [{ result } = {}] = await chrome.scripting.executeScript({
    target: { tabId },
    func: () => {
      return localStorage.getItem("token") || sessionStorage.getItem("token") || "";
    }
  });
  const token = (result || "").trim();
  if (token) await chrome.storage.local.set({ token });
  return token || null;
}

async function getToken() {
  const { token } = await chrome.storage.local.get(["token"]);
  if (token) return token;
  return await ensureTokenFromCrmTab();
}

function openSmallPopup(path = "form.html") {
  const url = chrome.runtime.getURL(path);
  const w = 420, h = 680;
  chrome.windows.create({
    url,
    type: "popup",
    width: w,
    height: h,
    focused: true
  });
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    const parent = chrome.contextMenus.create({
      id: "gd_parent",
      title: "LeadSuite GD",
      contexts: ["selection"]
    });

    const items = [
      ["name", "Usar como NOMBRE"],
      ["phone", "Usar como TELÉFONO"],
      ["email", "Usar como EMAIL"],
      ["address", "Usar como DIRECCIÓN"],
      ["comuna_text", "Usar como COMUNA (texto)"]
    ];

    for (const [k, title] of items) {
      chrome.contextMenus.create({
        id: "gd_set_" + k,
        parentId: parent,
        title,
        contexts: ["selection"]
      });
    }

    chrome.contextMenus.create({
      id: "gd_open_form",
      parentId: parent,
      title: "Abrir formulario (crear lead)",
      contexts: ["selection"]
    });

    chrome.contextMenus.create({
      id: "gd_quick_lead",
      parentId: parent,
      title: "Crear lead rápido con selección",
      contexts: ["selection"]
    });

    chrome.contextMenus.create({
      id: "gd_clear",
      parentId: parent,
      title: "Limpiar borrador",
      contexts: ["selection"]
    });
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const sel = getSelText(info);

  if (info.menuItemId.startsWith("gd_set_")) {
    const k = info.menuItemId.replace("gd_set_", "");
    if (sel) await setDraft({ [k]: sel });
    // asegura token en background (autologin)
    await getToken();
    return;
  }

  if (info.menuItemId === "gd_clear") {
    await chrome.storage.local.set({ draft: {} });
    return;
  }

  if (info.menuItemId === "gd_open_form") {
    await getToken(); // autologin
    openSmallPopup("form.html");
    return;
  }

  if (info.menuItemId === "gd_quick_lead") {
    await getToken(); // autologin
    const token = await getToken();
    const draft = await getDraft();

    // fallback: si no enviaron nombre, usa la selección como nombre
    const nombre = draft.name || sel || "";
    const telefono = draft.phone || "";
    const email = draft.email || "";

    const payload = {
      nombre_cliente: nombre,
      telefono,
      email,
      plataforma: "WHATSAPP"
    };

    const headers = { "Content-Type": "application/json" };
    if (token) headers["Authorization"] = "Bearer " + token;

    const res = await fetch(CRM_ORIGIN + "/leads", {
      method: "POST",
      headers,
      body: JSON.stringify(payload)
    });

    if (res.status === 401) {
      // token malo o API exige auth: abre form para que el usuario termine
      openSmallPopup("form.html");
      return;
    }

    // si ok, limpia borrador
    if (res.ok) await chrome.storage.local.set({ draft: {} });
  }
});
