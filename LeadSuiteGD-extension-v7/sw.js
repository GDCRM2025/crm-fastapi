const CRM_ORIGIN = "http://127.0.0.1:8000";
const CRM_LEADS  = CRM_ORIGIN + "/leads";
const CRM_CATS   = CRM_ORIGIN + "/leads/catalogos";

const DRAFT_KEY = "draftLead";
const AUTH_KEY  = "auth";

// ---------------- helpers ----------------
function clampStr(s, max=500) {
  s = String(s ?? "").trim();
  return s.length > max ? s.slice(0, max) : s;
}
function getSelectionFromInfo(info) { return clampStr(info.selectionText || ""); }

async function storageGet(keys) { return await chrome.storage.local.get(keys); }
async function storageSet(obj) { return await chrome.storage.local.set(obj); }

async function ensureDraft(patch = {}) {
  const { draftLead } = await storageGet([DRAFT_KEY]);
  const d = draftLead || {};
  const next = { ...d, ...patch, _updatedAt: Date.now() };
  await storageSet({ [DRAFT_KEY]: next });
  return next;
}

function openFormPopup() {
  chrome.windows.create({
    url: chrome.runtime.getURL("form.html"),
    type: "popup",
    width: 420,
    height: 640,
    focused: true
  });
}

async function notifyCrmLeadCreated(leadSummary) {
  const tabs = await chrome.tabs.query({ url: CRM_ORIGIN + "/*" });
  if (!tabs || !tabs.length) return;

  for (const t of tabs) {
    try {
      await chrome.scripting.executeScript({
        target: { tabId: t.id },
        args: [leadSummary],
        func: (lead) => {
          // 1) toast en CRM si existe Swal
          try {
            if (window.Swal && typeof window.Swal.fire === "function") {
              window.Swal.fire({
                toast: true,
                position: "top-end",
                timer: 2200,
                showConfirmButton: false,
                icon: "success",
                title: "Lead creado",
                text: (lead?.codigo_cliente || lead?.nombre_cliente || "").toString()
              });
            }
          } catch {}

          // 2) refrescar la vista actual si es leads.html (sin depender de listeners)
          try {
            const fr = document.getElementById("mainFrame");
            const src = fr?.getAttribute("src") || "";
            if (fr && /\/web\/views\/leads\.html/i.test(src)) {
              fr.setAttribute("src", src); // reload
            } else if (fr && /\/web\/views\/leads/i.test(src)) {
              fr.setAttribute("src", src); // reload
            }
          } catch {}

          // 3) además, manda un mensaje por si tienes listeners dentro de views
          try {
            const fr = document.getElementById("mainFrame");
            if (fr && fr.contentWindow) {
              fr.contentWindow.postMessage({ cmd: "gd-lead-created", lead }, "*");
            } else {
              window.postMessage({ cmd: "gd-lead-created", lead }, "*");
            }
          } catch {}
        }
      });
    } catch {}
  }
}

// ---------------- context menu ----------------
function createMenus() {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({ id: "gd_root", title: "LeadSuite GD", contexts: ["selection"] });

    const items = [
      ["gd_to_nombre",     "Enviar a NOMBRE"],
      ["gd_to_telefono",   "Enviar a TELÉFONO"],
      ["gd_to_email",      "Enviar a EMAIL"],
      ["gd_to_direccion",  "Enviar a DIRECCIÓN"],
      ["gd_to_comuna_txt", "Enviar a COMUNA (texto)"],
      ["gd_open_form",     "Abrir formulario (crear lead)"],
      ["gd_quick",         "Crear lead rápido con selección"],
      ["gd_clear",         "Limpiar borrador"]
    ];

    for (const [id, title] of items) {
      chrome.contextMenus.create({ id, parentId: "gd_root", title, contexts: ["selection"] });
    }
  });
}
chrome.runtime.onInstalled.addListener(() => createMenus());
chrome.runtime.onStartup.addListener(() => createMenus());

// click handler: ENVIAR A... => guarda draft + abre formulario
chrome.contextMenus.onClicked.addListener(async (info) => {
  const sel = getSelectionFromInfo(info);

  if (info.menuItemId === "gd_clear") {
    await storageSet({ [DRAFT_KEY]: {} });
    return;
  }

  if (info.menuItemId === "gd_open_form") {
    openFormPopup();
    return;
  }

  if (info.menuItemId === "gd_quick") {
    const s = sel;
    let patch = {};
    if (/@/.test(s)) patch.email = s;
    else if (/[0-9]{6,}/.test(s)) patch.telefono = s;
    else patch.nombre_cliente = s;
    await ensureDraft(patch);
    openFormPopup();
    return;
  }

  if (!sel) return;

  if (info.menuItemId === "gd_to_nombre")     { await ensureDraft({ nombre_cliente: sel }); openFormPopup(); return; }
  if (info.menuItemId === "gd_to_telefono")   { await ensureDraft({ telefono: sel });       openFormPopup(); return; }
  if (info.menuItemId === "gd_to_email")      { await ensureDraft({ email: sel });          openFormPopup(); return; }
  if (info.menuItemId === "gd_to_direccion")  { await ensureDraft({ direccion: sel });      openFormPopup(); return; }
  if (info.menuItemId === "gd_to_comuna_txt") { await ensureDraft({ comuna_texto: sel });   openFormPopup(); return; }
});

// ---------------- messages (popup/form/content_crm) ----------------
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    try {
      if (msg?.cmd === "getDraft") {
        const { draftLead } = await storageGet([DRAFT_KEY]);
        sendResponse({ ok: true, draft: draftLead || {} });
        return;
      }

      if (msg?.cmd === "setDraft") {
        const next = await ensureDraft(msg.patch || {});
        sendResponse({ ok: true, draft: next });
        return;
      }

      if (msg?.cmd === "getAuth") {
        const { auth } = await storageGet([AUTH_KEY]);
        sendResponse({ ok: true, auth: auth || {} });
        return;
      }

      // autologin: content script empuja token aquí
      if (msg?.cmd === "syncAuthFromCRM") {
        const token = String(msg?.token || "").trim();
        if (token) await storageSet({ [AUTH_KEY]: { token, syncedAt: Date.now() } });
        sendResponse({ ok: true });
        return;
      }

      if (msg?.cmd === "fetchCatalogos") {
        const res = await fetch(CRM_CATS, { method: "GET" });
        const data = await res.json().catch(() => null);
        sendResponse({ ok: res.ok, status: res.status, data });
        return;
      }

      if (msg?.cmd === "createLead") {
        const { payload } = msg || {};
        const { auth } = await storageGet([AUTH_KEY]);

        const headers = { "Content-Type": "application/json" };
        if (auth?.token) headers["Authorization"] = "Bearer " + auth.token;

        const res = await fetch(CRM_LEADS, {
          method: "POST",
          headers,
          body: JSON.stringify(payload || {})
        });

        const txt = await res.text().catch(() => "");
        let data = null;
        try { data = JSON.parse(txt); } catch { data = { raw: txt }; }

        if (res.ok) {
          if (msg.clearDraft) await storageSet({ [DRAFT_KEY]: {} });

          const leadSummary = {
            nombre_cliente: payload?.nombre_cliente,
            codigo_cliente: data?.lead?.codigo_cliente,
            id_lead: data?.lead?.id_lead
          };
          await notifyCrmLeadCreated(leadSummary);
        }

        sendResponse({ ok: res.ok, status: res.status, data });
        return;
      }

      sendResponse({ ok: false, error: "unknown_cmd" });
    } catch (e) {
      sendResponse({ ok: false, error: String(e?.message || e) });
    }
  })();

  return true;
});
