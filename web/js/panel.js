const qs = (s, el = document) => el.querySelector(s);
let PREF_KEY = "gd_user_prefs";
function setPrefKey(username) {
  const key = (username || "default").toString().trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "_");
  PREF_KEY = `gd_prefs_${key}`;
}

// Shared hosting stability mode: disable chat (polling) to reduce concurrent load.
const CHAT_ENABLED = false;

function _b64urlToUtf8(b64url) {
  try {
    const s = String(b64url || "").replace(/-/g, "+").replace(/_/g, "/");
    const pad = s.length % 4 ? "=".repeat(4 - s.length % 4) : "";
    const bin = atob(s + pad);
    try {
      return decodeURIComponent(Array.from(bin).map((c) => "%" + c.charCodeAt(0).toString(16).padStart(2, "0")).join(""));
    } catch (_) {
      return bin;
    }
  } catch (_) {
    return "";
  }
}

function decodeJwtPayload(token) {
  try {
    const t = String(token || "");
    const parts = t.split(".");
    if (parts.length < 2) return null;
    const json = _b64urlToUtf8(parts[1]);
    const obj = JSON.parse(json);
    return obj && typeof obj === "object" ? obj : null;
  } catch (_) {
    return null;
  }
}
const API_BASE = (() => {
  try {
    const h = String(location.hostname || "").toLowerCase();
    const isLocal = h === "localhost" || h === "127.0.0.1";
    if (isLocal) return "";
    // Hosting puede montar el CRM en /crm o en / (según Passenger/Apache).
    const p = String(location.pathname || "");
    return p.startsWith("/crm/") || p === "/crm" ? "/crm" : "";
  } catch (_) {
    return "";
  }
})();
const Swal = (() => {
  if (window.Swal && typeof window.Swal.fire === "function") return window.Swal;
  const strip = (s) => String(s || "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
  return {
    fire: async (a, b, c) => {
      try {
        if (typeof a === "object" && a) {
          const title = String(a.title || "Info");
          const msg = String(a.text || "").trim() || strip(a.html || "");
          if (a.showCancelButton) {
            const ok = confirm(`${title}${msg ? "\n\n" + msg : ""}`);
            return { isConfirmed: ok };
          }
          alert(`${title}${msg ? "\n\n" + msg : ""}`);
          return { isConfirmed: true };
        }
        alert(String(a || b || "Info"));
      } catch (_) {
      }
      return { isConfirmed: false };
    }
  };
})();
function viewURL(u) {
  const s = String(u || "");
  if (!s) return s;
  if (/^https?:\/\//i.test(s)) return s;
  if (s.startsWith(API_BASE + "/")) return s;
  if (API_BASE && s.startsWith("/web/")) return API_BASE + s;
  return s;
}
function getToken() {
  return localStorage.getItem("token") || localStorage.getItem("gd_token") || sessionStorage.getItem("token") || sessionStorage.getItem("gd_token") || "";
}
function authHeaders(extra = {}) {
  const t = getToken();
  return t ? { ...extra, Authorization: `Bearer ${t}` } : extra;
}
function requireAuth() {
  if (!getToken()) {
    location.href = `${API_BASE}/web/login.html`;
    return false;
  }
  return true;
}
const IDLE_KEY = "gd_last_activity";
const IDLE_TIMEOUT_MS = 60 * 60 * 1e3;
function _now() {
  return Date.now();
}
function lastActivity() {
  try {
    return Number(localStorage.getItem(IDLE_KEY) || "0") || 0;
  } catch (_) {
    return 0;
  }
}
function markActivity() {
  try {
    localStorage.setItem(IDLE_KEY, String(_now()));
  } catch (_) {
  }
}
function clearAuth() {
  try {
    localStorage.removeItem("token");
    localStorage.removeItem("gd_token");
    localStorage.removeItem("nombre");
    sessionStorage.removeItem("token");
    sessionStorage.removeItem("gd_token");
    sessionStorage.removeItem("nombre");
  } catch (_) {
  }
}

function enterRrhhOnlyMode(reason) {
  try {
    document.body.classList.add("rrhh-only");
  } catch (_) {
  }
  try {
    const sidebar = qs("#sidebar");
    if (sidebar) sidebar.style.display = "none";
    const btnSidebar = qs("#btnSidebar");
    if (btnSidebar) btnSidebar.style.display = "none";
    const btnSidebarPinTop = qs("#btnSidebarPinTop");
    if (btnSidebarPinTop) btnSidebarPinTop.style.display = "none";
  } catch (_) {
  }
  try {
    const btnNotifs = qs("#btnNotifs");
    const btnGpt = qs("#btnGpt");
    const btnChat = qs("#btnChatTop");
    if (btnNotifs) btnNotifs.style.display = "none";
    if (btnGpt) btnGpt.style.display = "none";
    if (btnChat) btnChat.style.display = "none";
  } catch (_) {
  }
  try {
    const fr = qs("#mainFrame");
    if (fr) {
      const v = "20260423-rrhh2";
      fr.src = viewURL(`/web/views/rrhh.html?v=${v}#portal`);
    }
  } catch (_) {
  }
  try {
    if (reason) console.log("RRHH-only mode:", reason);
  } catch (_) {
  }
}

function leaveRrhhOnlyMode() {
  try {
    document.body.classList.remove("rrhh-only");
  } catch (_) {
  }
  // Rehabilita UI principal (sidebar + botones) si antes se ocultó.
  try {
    const sidebar = qs("#sidebar");
    if (sidebar) sidebar.style.display = "";
    const btnSidebar = qs("#btnSidebar");
    if (btnSidebar) btnSidebar.style.display = "";
    const btnSidebarPinTop = qs("#btnSidebarPinTop");
    if (btnSidebarPinTop) btnSidebarPinTop.style.display = "";
  } catch (_) {
  }
  try {
    const btnNotifs = qs("#btnNotifs");
    const btnGpt = qs("#btnGpt");
    const btnChat = qs("#btnChatTop");
    if (btnNotifs) btnNotifs.style.display = "";
    if (btnGpt) btnGpt.style.display = "";
    if (btnChat) btnChat.style.display = "";
  } catch (_) {
  }
}
async function _serverLogout(reason = "manual", extra = {}) {
  try {
    const t = getToken();
    if (!t) return;
    const ctrl = new AbortController();
    const to = setTimeout(() => {
      try {
        ctrl.abort();
      } catch (_) {
      }
    }, 2500);
    try {
      await fetch(`${API_BASE}/auth/logout`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ reason, ...(extra || {}) }),
        signal: ctrl.signal
      });
    } finally {
      clearTimeout(to);
    }
  } catch (_) {
  }
}
async function idleLogout() {
  const la = lastActivity();
  const inactivityMs = la ? Math.max(0, _now() - la) : IDLE_TIMEOUT_MS;
  await _serverLogout("idle", {
    inactivity_ms: inactivityMs,
    inactivity_minutes: Math.max(1, Math.round(inactivityMs / 60000))
  });
  clearAuth();
  location.href = `${API_BASE}/web/login.html?reason=idle`;
}
function setupIdleLogout() {
  if (!lastActivity()) markActivity();
  const evs = ["pointerdown", "mousemove", "keydown", "scroll", "touchstart", "wheel"];
  const opts = { passive: true, capture: true };
  evs.forEach((e) => window.addEventListener(e, markActivity, opts));
  window.addEventListener("focus", markActivity);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) markActivity();
  });
  const bridge = (sel) => {
    const fr = qs(sel);
    if (!fr) return;
    fr.addEventListener("load", () => {
      try {
        const doc = fr.contentDocument;
        if (!doc || !doc.addEventListener) return;
        const on = () => markActivity();
        evs.forEach((e) => doc.addEventListener(e, on, opts));
      } catch (_) {
      }
    });
  };
  bridge("#mainFrame");
  bridge("#chatFrame");
  setInterval(() => {
    if (!getToken()) return;
    const la = lastActivity();
    if (la && _now() - la > IDLE_TIMEOUT_MS) {
      idleLogout();
    }
  }, 25e3);
}
function ensureToastHost() {
  let host = document.getElementById("gdToastHost");
  if (host) return host;
  host = document.createElement("div");
  host.id = "gdToastHost";
  host.style.position = "fixed";
  host.style.right = "16px";
  host.style.bottom = "16px";
  host.style.display = "grid";
  host.style.gap = "10px";
  host.style.zIndex = "99999";
  document.body.appendChild(host);
  return host;
}
function toast(text, { kind = "info", ms = 7e3, onClick = null } = {}) {
  const host = ensureToastHost();
  const el = document.createElement("div");
  const border = kind === "ok" ? "rgba(25,195,125,.45)" : kind === "err" ? "rgba(239,68,68,.55)" : "rgba(148,163,184,.22)";
  const bg = kind === "ok" ? "rgba(25,195,125,.10)" : kind === "err" ? "rgba(239,68,68,.10)" : "rgba(2,6,23,.55)";
  el.style.border = `1px solid ${border}`;
  el.style.background = bg;
  el.style.backdropFilter = "blur(10px)";
  el.style.borderRadius = "14px";
  el.style.padding = "10px 12px";
  el.style.color = "var(--text)";
  el.style.boxShadow = "0 16px 50px rgba(0,0,0,.35)";
  el.style.fontWeight = "950";
  el.style.maxWidth = "420px";
  el.style.cursor = onClick ? "pointer" : "default";
  el.textContent = text;
  if (onClick) {
    el.addEventListener("click", () => {
      try {
        onClick();
      } catch (_) {
      }
      ;
    });
  }
  host.appendChild(el);
  setTimeout(() => {
    try {
      el.remove();
    } catch (_) {
    }
  }, ms);
}
let _backupWatchTimer = null;
async function pollBackupJob() {
  const jobId = localStorage.getItem("gd_backup_job_id") || "";
  if (!jobId) return;
  try {
    const r = await fetch(`${API_BASE}/backups/jobs/${encodeURIComponent(jobId)}`, { headers: authHeaders() });
    if (!r.ok) return;
    const j = await r.json();
    const job = (j == null ? void 0 : j.job) || {};
    if (job.status === "done") {
      localStorage.removeItem("gd_backup_job_id");
      toast(`Backup creado: ${job.backup_id || jobId}`, {
        kind: "ok",
        onClick: () => {
          const bid = job.backup_id || jobId;
          qs("#mainFrame").src = viewURL(`/web/views/backups.html?select=${encodeURIComponent(bid)}`);
        }
      });
    } else if (job.status === "error") {
      localStorage.removeItem("gd_backup_job_id");
      toast(`Error creando backup: ${(job.error || "").slice(0, 140) || "revisa servidor"}`, { kind: "err" });
    }
  } catch (_) {
  }
}
function setupBackupJobWatch() {
  if (_backupWatchTimer) return;
  _backupWatchTimer = setInterval(pollBackupJob, 4e3);
  pollBackupJob();
}
window.GD = window.GD || {};
window.GD.getToken = getToken;
window.GD.authHeaders = authHeaders;
function renderTopTools() {
  // Pedido: no mostrar “quick tools” en el topbar (van dentro de Tools, embebidos por separado).
  try {
    const topbar = qs(".topbar");
    if (!topbar) return;
    const host = qs("#gdQuickTools", topbar);
    if (host) host.remove();

    // Compat: si quedó algún botón viejo por cache (IG/Correo/WSP/Ext/Backup), lo removemos igual.
    const legacyIds = [
      "qt_instagram",
      "qt_correo",
      "qt_whatsapp",
      "qt_extension",
      "qt_backup",
      "btnInstagram",
      "btnCorreo",
      "btnWhatsApp",
      "btnExtension",
      "btnBackup"
    ];
    legacyIds.forEach((id) => {
      try {
        const el = qs(`#${id}`, topbar);
        if (el) el.remove();
      } catch (_) {
      }
    });

    // Último seguro: elimina cualquier botón/link del topbar cuyo texto sea alguno de estos atajos.
    // OJO: no borrar el Backup del menú Sistema (admin-only).
    const killWords = ["instagram", "correo", "whatsapp", "extensi"];
    topbar.querySelectorAll("a,button").forEach((el) => {
      try {
        const t = String(el.textContent || "").toLowerCase();
        if (el.id === "sysBackup" || el.closest("#systemMenuTop")) return;
        if (killWords.some((w) => t.includes(w))) el.remove();
      } catch (_) {
      }
    });
  } catch (_) {
  }
}
function refreshMainFrame() {
  var _a, _b;
  const frame = qs("#mainFrame");
  try {
    (_b = (_a = frame == null ? void 0 : frame.contentWindow) == null ? void 0 : _a.location) == null ? void 0 : _b.reload();
  } catch (_) {
    if (frame) frame.src = frame.src;
  }
}
async function performLogout() {
  const ok = await maybePromptSgjoMarkOutBeforeLogout();
  if (!ok) return;
  await _serverLogout("manual");
  localStorage.removeItem("token");
  localStorage.removeItem("nombre");
  sessionStorage.removeItem("token");
  sessionStorage.removeItem("nombre");
  location.href = `${API_BASE}/web/login.html`;
}
function moveUserStatusToPerfil(menu) {
  const tabPerfil = qs('[data-um-tab="perfil"]', menu);
  if (!tabPerfil) return;
  const wrapSelectors = [
    "#userStatusWrap",
    "#statusWrap",
    "#estadoWrap",
    "#statusSection",
    "#estadoSection",
    "[data-user-status-wrap]",
    "[data-estado-wrap]"
  ];
  let block = null;
  for (const sel of wrapSelectors) {
    block = qs(sel, menu);
    if (block) break;
  }
  if (!block) {
    const control = qs('#statusSelect, #estadoSelect, [name="status"], [name="estado"]', menu);
    if (control) {
      block = control.closest(".field,.row,.section,.block,.menu-section,div") || control.parentElement;
    }
  }
  if (block && !tabPerfil.contains(block)) {
    tabPerfil.appendChild(block);
  }
}
function ensureSystemActionsInUserMenu(menu) {
  const tabSystem = qs('[data-um-tab="sistema"]', menu);
  if (!tabSystem) return;
  let wrap = qs("#gdUserSystemActions", tabSystem);
  if (!wrap) {
    wrap = document.createElement("div");
    wrap.id = "gdUserSystemActions";
    wrap.style.display = "grid";
    wrap.style.gap = "8px";
    wrap.style.marginTop = "12px";
    const mkBtn = (text, onClick) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "btn";
      b.style.width = "100%";
      b.style.textAlign = "left";
      b.style.fontWeight = "900";
      b.textContent = text;
      b.addEventListener("click", onClick);
      return b;
    };
    wrap.appendChild(mkBtn("Actualizar", async () => {
      refreshMainFrame();
      try {
        const data = await fetchNotifications();
        renderNotifications(data);
      } catch (_) {
      }
    }));
    wrap.appendChild(mkBtn("Cerrar sesi\xF3n", async () => {
      const ok = await Swal.fire({
        title: "Cerrar sesi\xF3n",
        text: "\xBFSeguro que deseas cerrar sesi\xF3n?",
        icon: "question",
        showCancelButton: true,
        confirmButtonText: "S\xED, salir",
        cancelButtonText: "Cancelar",
        confirmButtonColor: "#19C37D"
      }).then((r) => r.isConfirmed);
      if (!ok) return;
      await performLogout();
    }));
    tabSystem.appendChild(wrap);
  }
}
function normalizeUserMenuLayout(menu) {
  if (!menu) return;
  const tabPerfil = qs('[data-um-tab="perfil"]', menu);
  const tabPersonal = qs('[data-um-tab="personalizacion"]', menu);
  const tabSystem = qs('[data-um-tab="sistema"]', menu);
  const prefSave = qs("#prefSave", menu);
  if (prefSave && tabPersonal) {
    let saveWrap = qs("#gdPrefSaveWrap", tabPersonal);
    if (!saveWrap) {
      saveWrap = document.createElement("div");
      saveWrap.id = "gdPrefSaveWrap";
      saveWrap.style.marginTop = "12px";
      tabPersonal.appendChild(saveWrap);
    }
    saveWrap.appendChild(prefSave);
  }
  if (tabSystem) {
    tabSystem.querySelectorAll("[data-open]").forEach((el) => el.remove());
  }
  moveUserStatusToPerfil(menu);
  ensureSystemActionsInUserMenu(menu);
  if (tabPerfil) {
    let title = qs("#gdPerfilStatusTitle", tabPerfil);
    if (!title) {
      title = document.createElement("div");
      title.id = "gdPerfilStatusTitle";
      title.style.marginTop = "10px";
      title.style.fontSize = "12px";
      title.style.fontWeight = "900";
      title.style.opacity = ".8";
      title.textContent = "Estado";
      tabPerfil.appendChild(title);
    }
  }
}
let _chatWatchTimer = null;
function setupChatWatch() {
  if (!CHAT_ENABLED) {
    try {
      const btnTop = qs("#btnChatTop");
      if (btnTop) btnTop.style.display = "none";
    } catch (_) {
    }
    try {
      qs("#chatLauncher")?.remove?.();
    } catch (_) {
    }
    try {
      qs("#chatDock")?.remove?.();
    } catch (_) {
    }
    try {
      qs("#chatModal")?.remove?.();
    } catch (_) {
    }
    return;
  }
  if (_chatWatchTimer) return;
  const onceOpts = { once: true, capture: true, passive: true };
  window.addEventListener("pointerdown", enableSoundOnce, onceOpts);
  window.addEventListener("keydown", enableSoundOnce, onceOpts);
  window.addEventListener("touchstart", enableSoundOnce, onceOpts);
  const isChatActive = () => {
    try {
      if (document.hidden) return false;
      const chatModal = qs("#chatModal");
      if (chatModal && chatModal.classList.contains("open")) return true;
      const dock = qs("#chatDock");
      if (dock && dock.querySelector(".chat-win.open")) return true;
    } catch (_) {
    }
    return false;
  };
  const baseDelay = 25e3;
  const activeDelay = 5e3;
  const tick = async () => {
    try {
      if (!getToken()) return;
      if (document.hidden) {
        _chatWatchTimer = setTimeout(tick, baseDelay);
        return;
      }
      await pollChatThreads();
    } catch (_) {
    } finally {
      _chatWatchTimer = setTimeout(tick, isChatActive() ? activeDelay : baseDelay);
    }
  };
  tick();
  _ensureChatLauncher();
}
let _chatLauncher = null;
function _ensureChatLauncher() {
  if (_chatLauncher) return _chatLauncher;
  const st = document.createElement("style");
  st.textContent = `
    #chatLauncher{
      position: fixed;
      right: 18px;
      bottom: 18px;
      z-index: 9998;
      width: 52px;
      height: 52px;
      border-radius: 999px;
      border: 1px solid rgba(148,163,184,.22);
      background: rgba(2,6,23,.55);
      backdrop-filter: blur(12px);
      box-shadow: 0 22px 60px rgba(0,0,0,.38);
      color: rgba(226,232,240,.95);
      display: grid;
      place-items: center;
      cursor: pointer;
      user-select: none;
      font-weight: 1100;
    }
    #chatLauncher:hover{ filter: brightness(1.08); }
    #chatLauncher .b{
      width: 38px; height: 38px; border-radius: 999px;
      background: color-mix(in srgb, var(--accent) 18%, transparent);
      border: 1px solid color-mix(in srgb, var(--accent) 40%, transparent);
      display:grid; place-items:center;
      font-size: 18px;
    }
  `;
  document.head.appendChild(st);
  const btnTop = qs("#btnChatTop");
  if (btnTop) btnTop.style.display = "none";
  const b = document.createElement("button");
  b.type = "button";
  b.id = "chatLauncher";
  b.title = "Chat interno";
  b.innerHTML = `<div class="b">\u{1F4AC}</div>`;
  b.addEventListener("click", () => {
    enableSoundOnce();
    try {
      openChatThread("");
    } catch (_) {
    }
  });
  document.body.appendChild(b);
  _chatLauncher = b;
  return b;
}
function getTheme() {
  const v = (localStorage.getItem("gd_theme") || "").trim();
  if (v === "light" || v === "dark") return v;
  const legacy = (localStorage.getItem("THEME") || "").trim().toLowerCase();
  if (legacy === "day") return "light";
  if (legacy === "night") return "dark";
  return "dark";
}
function setTheme(mode) {
  const m = mode === "light" ? "light" : "dark";
  localStorage.setItem("gd_theme", m);
  localStorage.setItem("THEME", m === "light" ? "day" : "night");
  try {
    const p = getPrefs();
    p.theme = m;
    savePrefs(p);
  } catch (_) {
  }
  applyTheme(m);
}
function applyTheme(mode) {
  var _a;
  const m = mode === "light" ? "light" : "dark";
  document.documentElement.classList.toggle("light", m === "light");
  document.documentElement.setAttribute("data-theme", m === "light" ? "day" : "night");
  const fr = qs("#mainFrame");
  try {
    (_a = fr == null ? void 0 : fr.contentWindow) == null ? void 0 : _a.postMessage({ type: "theme", mode: m }, "*");
  } catch (_) {
  }
}
const FONT_MAP = {
  system: 'ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, "Apple Color Emoji","Segoe UI Emoji"',
  rounded: '"Space Grotesk", "Segoe UI", system-ui, sans-serif',
  manrope: '"Manrope", "Segoe UI", system-ui, sans-serif',
  sora: '"Sora", "Segoe UI", system-ui, sans-serif',
  jakarta: '"Plus Jakarta Sans", "Segoe UI", system-ui, sans-serif',
  rubik: '"Rubik", "Segoe UI", system-ui, sans-serif',
  dmsans: '"DM Sans", "Segoe UI", system-ui, sans-serif',
  poppins: '"Poppins", "Segoe UI", system-ui, sans-serif',
  nunito: '"Nunito", "Segoe UI", system-ui, sans-serif',
  montserrat: '"Montserrat", "Segoe UI", system-ui, sans-serif',
  urbanist: '"Urbanist", "Segoe UI", system-ui, sans-serif',
  sourcesans: '"Source Sans 3", "Segoe UI", system-ui, sans-serif',
  serif: '"Fraunces", "Iowan Old Style", "Palatino", "Times New Roman", serif',
  playfair: '"Playfair Display", "Times New Roman", serif',
  mono: '"JetBrains Mono","SFMono-Regular","Menlo","Monaco","Consolas","Liberation Mono","Courier New", monospace'
};
function getPrefs() {
  try {
    return JSON.parse(localStorage.getItem(PREF_KEY) || "{}");
  } catch (_) {
    return {};
  }
}
function savePrefs(p) {
  localStorage.setItem(PREF_KEY, JSON.stringify(p || {}));
}
function applyPrefs(p) {
  var _a, _b;
  if (!p) return;
  // Importante: el CRM usa tema global estilo Windows XP Royale.
  // Para evitar problemas de legibilidad (especialmente en Noir/noche),
  // NO aplicamos overrides persistidos de font/accent/text.
  document.documentElement.style.removeProperty("--font");
  document.documentElement.style.removeProperty("--accent");
  document.documentElement.style.removeProperty("--text");
  if (p.fontSize) {
    document.documentElement.style.setProperty("font-size", `${p.fontSize}px`);
  }
  if (p.theme) {
    applyTheme(p.theme);
  }
  try {
    const fr = qs("#mainFrame");
    (_a = fr == null ? void 0 : fr.contentWindow) == null ? void 0 : _a.postMessage({ type: "prefs", prefs: { theme: p.theme, fontSize: p.fontSize } }, "*");
    const doc = (_b = fr == null ? void 0 : fr.contentDocument) == null ? void 0 : _b.documentElement;
    if (doc) {
      doc.style.removeProperty("--font");
      doc.style.removeProperty("--accent");
      doc.style.removeProperty("--text");
      if (p.fontSize) doc.style.setProperty("font-size", `${p.fontSize}px`);
      if (p.theme) doc.classList.toggle("light", p.theme === "light");
    }
    const iframeDoc = fr == null ? void 0 : fr.contentDocument;
    if (iframeDoc) {
      let st = iframeDoc.getElementById("gd-pref-style");
      if (!st) {
        st = iframeDoc.createElement("style");
        st.id = "gd-pref-style";
        iframeDoc.head.appendChild(st);
      }
      const sizeCss = p.fontSize ? `font-size:${p.fontSize}px;` : "";
      st.textContent = `
        :root{${sizeCss}}
      `;
    }
  } catch (_) {
  }
}
function initThemeToggle() {
  const tgl = qs("#themeToggle");
  if (!tgl) return;
  const p = getPrefs();
  const mode = p.theme || getTheme();
  // UI: 🌙 (izq) → ☀️ (der). El knob se mueve a la derecha cuando está "checked",
  // por lo que checked = DÍA (light).
  tgl.checked = mode === "light";
  setTheme(mode);
  tgl.addEventListener("change", () => {
    const m = tgl.checked ? "light" : "dark";
    setTheme(m);
  });
  qs("#mainFrame").addEventListener("load", () => {
    var _a;
    applyTheme(getTheme());
    applyPrefs(getPrefs());
    // Propaga auth al iframe (evita vistas sin token cuando el storage difiere).
    try {
      const frame = qs("#mainFrame");
      const t = getToken();
      if (t && (frame == null ? void 0 : frame.contentWindow)) {
        frame.contentWindow.postMessage({ type: "auth", token: t }, "*");
      }
    } catch (_) {
    }
    try {
      const fr = qs("#mainFrame");
      const doc = fr == null ? void 0 : fr.contentDocument;
      if (doc && doc.addEventListener) {
        const onGesture = () => enableSoundOnce();
        doc.addEventListener("pointerdown", onGesture, { once: true, capture: true });
        doc.addEventListener("keydown", onGesture, { once: true, capture: true });
      }
    } catch (_) {
    }
    if (pendingLeadOpen) {
      try {
        const frame = qs("#mainFrame");
        (_a = frame == null ? void 0 : frame.contentWindow) == null ? void 0 : _a.postMessage({ type: "openLead", id: pendingLeadOpen.id, stale_ids: pendingLeadOpen.ids }, "*");
      } catch (_) {
      }
      pendingLeadOpen = null;
    }
  });
}
function startClock() {
  const el = qs("#clockBox");
  if (!el) return;
  const fmt = new Intl.DateTimeFormat("es-CL", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
  const tick = () => {
    el.textContent = fmt.format(/* @__PURE__ */ new Date());
  };
  tick();
  setInterval(tick, 3e4);
}
async function fetchNotifications() {
  try {
    if (!getToken()) return { ok: false, total: 0, items: [] };
    const r = await fetch(`${API_BASE}/notifications?light=1`, { headers: authHeaders() });
    if (r.status === 401 || r.status === 403) {
      location.href = `${API_BASE}/web/login.html`;
      return { ok: false, total: 0, items: [] };
    }
    if (!r.ok) throw new Error("notifications failed");
    return await r.json();
  } catch (_) {
    return { ok: false, total: 0, items: [] };
  }
}
let leadLockPollHandle = null;
async function checkLeadLockOnce() {
  try {
    if (!canUseTasksBadge()) return;
    const r = await fetch(`${API_BASE}/notifications/lead_lock`, { headers: authHeaders() });
    if (!r.ok) return;
    const data = await r.json();
    if (data && data.ok) renderLeadLock(data);
  } catch (_) {
  }
}
function startLeadLockPolling() {
  try {
    if (!canUseTasksBadge()) return;
    if (leadLockPollHandle) return;
    setTimeout(checkLeadLockOnce, 2500);
    leadLockPollHandle = setInterval(checkLeadLockOnce, 5 * 60 * 1000);
  } catch (_) {
  }
}
async function fetchSoldLatest() {
  try {
    if (!getToken()) return { ok: false, item: null };
    const r = await fetch(`${API_BASE}/notifications/sold_latest`, { headers: authHeaders() });
    if (r.status === 401 || r.status === 403) return { ok: false, item: null };
    if (!r.ok) return { ok: false, item: null };
    return await r.json();
  } catch (_) {
    return { ok: false, item: null };
  }
}
async function markSystemNotifRead(id) {
  try {
    if (!getToken()) return false;
    const r = await fetch(`${API_BASE}/notifications/system/${encodeURIComponent(String(id))}/read`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" })
    });
    return r.ok;
  } catch (_) {
    return false;
  }
}
let lastNotifCount = null;
let lastLeadCount = null;
let lastStaleCount = null;
let lastSystemCount = null;
let canSound = false;
let lastSoundAt = 0;
let seenNotifCount = Number(localStorage.getItem("gd_notif_seen") || "0");
function enableSoundOnce() {
  var _a;
  if (canSound) return;
  canSound = true;
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (Ctx) {
      const ctx = new Ctx();
      (_a = ctx.close) == null ? void 0 : _a.call(ctx);
    }
  } catch (_) {
  }
}
function playNotifSound(kind = "default") {
  if (!canSound) return;
  const now = Date.now();
  if (now - lastSoundAt < 1200) return;
  lastSoundAt = now;
  if ((kind === "lead" || kind === "event") && "speechSynthesis" in window) {
    try {
      const u = new SpeechSynthesisUtterance(kind === "event" ? "Nuevo evento" : "Nuevo lead");
      u.lang = "es-CL";
      u.rate = 1.05;
      u.pitch = 1;
      window.speechSynthesis.cancel();
      window.speechSynthesis.speak(u);
      return;
    } catch (_) {
    }
  }
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    gain.gain.value = 0.06;
    osc.connect(gain);
    gain.connect(ctx.destination);
    if (kind === "chat") {
      osc.frequency.value = 880;
      osc.start();
      setTimeout(() => {
        osc.frequency.value = 660;
      }, 90);
      setTimeout(() => {
        gain.gain.value = 0;
      }, 140);
      setTimeout(() => {
        gain.gain.value = 0.06;
        osc.frequency.value = 990;
      }, 200);
      setTimeout(() => {
        osc.frequency.value = 740;
      }, 270);
      setTimeout(() => {
        osc.stop();
        ctx.close();
      }, 340);
    } else if (kind === "event") {
      osc.frequency.value = 523;
      osc.start();
      setTimeout(() => {
        osc.frequency.value = 784;
      }, 120);
      setTimeout(() => {
        osc.frequency.value = 659;
      }, 220);
      setTimeout(() => {
        osc.stop();
        ctx.close();
      }, 360);
    } else {
      osc.frequency.value = 740;
      osc.start();
      setTimeout(() => {
        osc.frequency.value = 990;
      }, 120);
      setTimeout(() => {
        osc.stop();
        ctx.close();
      }, 240);
    }
  } catch (_) {
  }
}
let _chatInitSeen = false;
let _chatSeen = {};
try {
  _chatSeen = JSON.parse(localStorage.getItem("gd_chat_seen") || "{}") || {};
} catch (_) {
  _chatSeen = {};
}
let _chatDock = null;
function _ensureChatDock() {
  if (_chatDock) return _chatDock;
  const st = document.createElement("style");
  st.textContent = `
    #chatDock{
      position:fixed; right:16px; bottom:86px; z-index:9999;
      display:flex; flex-direction:row; gap:10px; align-items:flex-end;
      flex-wrap:wrap;
      pointer-events:none;
    }
    .chat-win{
      width:min(360px, calc(100vw - 34px));
      height:min(520px, calc(100vh - 120px));
      border:1px solid rgba(148,163,184,.22);
      background:rgba(2,6,23,.84);
      backdrop-filter:blur(14px);
      border-radius:14px;
      box-shadow:0 18px 70px rgba(0,0,0,.45);
      overflow:hidden;
      pointer-events:auto;
      display:flex;
      flex-direction:column;
    }
    .chat-win .h{
      display:flex; align-items:center; justify-content:space-between; gap:10px;
      padding:10px 10px 8px;
      border-bottom:1px solid rgba(148,163,184,.14);
    }
    .chat-win .ttl{ font-weight:1100; font-size:13px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .chat-win .hbtns{ display:flex; gap:6px; align-items:center; }
    .chat-win .hb{
      border:1px solid rgba(148,163,184,.22);
      background:rgba(2,6,23,.22);
      color:rgba(226,232,240,.9);
      border-radius:10px;
      padding:4px 8px;
      font-weight:1100;
      cursor:pointer;
    }
    .chat-win .preview{ padding:10px; }
    .chat-win .msg{
      color:rgba(226,232,240,.86);
      font-weight:900;
      font-size:13px;
      line-height:1.3;
      display:-webkit-box;
      -webkit-line-clamp:3;
      -webkit-box-orient:vertical;
      overflow:hidden;
      white-space:pre-wrap;
      word-break:break-word;
    }
    .chat-win .a{
      display:flex; justify-content:flex-end; gap:8px;
      padding:0 10px 10px;
    }
    .chat-win .btn{
      border:1px solid rgba(148,163,184,.22);
      background:rgba(2,6,23,.22);
      color:rgba(226,232,240,.92);
      border-radius:12px;
      padding:8px 10px;
      font-weight:1100;
      cursor:pointer;
    }
    .chat-win .btn.primary{
      background:color-mix(in srgb, var(--accent) 22%, transparent);
      border-color:color-mix(in srgb, var(--accent) 50%, transparent);
      color:#fff;
    }
    .chat-win iframe{
      width:100%;
      border:0;
      flex:1;
      display:none;
      background:transparent;
    }
    .chat-win.open iframe{ display:block; }
    .chat-win.open .preview, .chat-win.open .a{ display:none; }
    .chat-win.attn .h{ background: color-mix(in srgb, var(--accent) 14%, rgba(2,6,23,.18)); }
  `;
  document.head.appendChild(st);
  const d = document.createElement("div");
  d.id = "chatDock";
  document.body.appendChild(d);
  _chatDock = d;
  return d;
}
function openChatThread(threadId) {
  if (!CHAT_ENABLED) {
    toast("Chat deshabilitado (modo estabilidad).", { kind: "info", ms: 5e3 });
    return;
  }
  enableSoundOnce();
  try {
    const chatModal = qs("#chatModal");
    const fr = qs("#chatFrame") || (chatModal == null ? void 0 : chatModal.querySelector("iframe"));
    if (fr) {
      fr.src = viewURL(`/web/views/chat.html?thread=${encodeURIComponent(String(threadId || ""))}&v=${Date.now()}`);
    }
    chatModal == null ? void 0 : chatModal.classList.add("open");
    chatModal == null ? void 0 : chatModal.setAttribute("aria-hidden", "false");
  } catch (_) {
  }
}
function openChatWindow(tid, title) {
  const dock = _ensureChatDock();
  let el = dock.querySelector(`.chat-win[data-tid="${tid}"]`);
  if (el) return el;
  el = document.createElement("div");
  el.className = "chat-win";
  el.dataset.tid = String(tid);
  el.innerHTML = `
    <div class="h">
      <div class="ttl"></div>
      <div class="hbtns">
        <button class="hb" type="button" data-open-full="1" title="Abrir en Chat">\u2197</button>
        <button class="hb" type="button" data-toggle="1" title="Minimizar / abrir">\u25A2</button>
        <button class="hb" type="button" data-close="1" title="Cerrar">\u2715</button>
      </div>
    </div>
    <div class="preview">
      <div class="msg"></div>
    </div>
    <div class="a">
      <button class="btn" type="button" data-toggle="1">Abrir</button>
      <button class="btn primary" type="button" data-toggle="1">Responder</button>
    </div>
    <iframe loading="lazy" data-src=""></iframe>
  `;
  {
    const tEl = el.querySelector(".ttl");
    if (tEl) tEl.textContent = title || "Chat";
  }
  const close = () => {
    try {
      el.remove();
    } catch (_) {
    }
  };
  const toggle = () => {
    enableSoundOnce();
    el.classList.toggle("open");
    el.classList.remove("attn");
    const fr = el.querySelector("iframe");
    if (el.classList.contains("open") && fr && !fr.getAttribute("src")) {
      const src = fr.dataset.src || "";
      if (src) fr.setAttribute("src", src);
    }
  };
  el.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", close));
  el.querySelectorAll("[data-toggle]").forEach((b) => b.addEventListener("click", toggle));
  el.querySelectorAll("[data-open-full]").forEach((b) => b.addEventListener("click", () => openChatThread(tid)));
  dock.appendChild(el);
  return el;
}
function showChatPopup(th) {
  const tid = Number((th == null ? void 0 : th.id_thread) || 0);
  if (!tid) return;
  const title = String((th == null ? void 0 : th.title) || "Chat");
  const msg = String((th == null ? void 0 : th.last_msg) || "");
  const el = openChatWindow(tid, title);
  const ttlEl = el.querySelector(".ttl");
  if (ttlEl) ttlEl.textContent = title;
  const msgEl = el.querySelector(".msg");
  if (msgEl) msgEl.textContent = msg || "(sin texto)";
  const fr = el.querySelector("iframe");
  if (fr) {
    const src = viewURL(`/web/views/chat.html?embed=1&thread=${encodeURIComponent(String(tid))}&v=${Date.now()}`);
    fr.dataset.src = src;
    if (!fr.getAttribute("src")) fr.setAttribute("src", src);
    fr.addEventListener("load", () => {
      try {
        const w = fr.contentWindow;
        const d = w && w.document;
        const inp = d && d.querySelector && d.querySelector("#msg");
        if (inp && typeof inp.focus === "function") inp.focus();
      } catch (_) {
      }
    }, { once: true });
  }
  if (!el.classList.contains("open")) el.classList.add("open");
  el.classList.remove("attn");
}
async function pollChatThreads() {
  var _a;
  try {
    if (!getToken()) return;
    const me = ((_a = window.GD) == null ? void 0 : _a.me) || {};
    const role = String((me == null ? void 0 : me.role) || (me == null ? void 0 : me.rol) || "").toUpperCase();
    if (isOpsAppRole(role)) return;
    const r = await fetch(`${API_BASE}/chat/threads?limit=40`, { headers: authHeaders() });
    if (!r.ok) return;
    const j = await r.json();
    const items = Array.isArray(j == null ? void 0 : j.items) ? j.items : [];
    if (!_chatInitSeen) {
      for (const it of items) {
        const tid = Number((it == null ? void 0 : it.id_thread) || 0);
        const mid = Number((it == null ? void 0 : it.last_message_id) || 0);
        if (tid && mid) _chatSeen[String(tid)] = mid;
      }
      _chatInitSeen = true;
      try {
        localStorage.setItem("gd_chat_seen", JSON.stringify(_chatSeen));
      } catch (_) {
      }
      return;
    }
    const meEmail = String((me == null ? void 0 : me.email) || "").toLowerCase();
    let changed = false;
    for (const it of items) {
      const tid = Number((it == null ? void 0 : it.id_thread) || 0);
      const mid = Number((it == null ? void 0 : it.last_message_id) || 0);
      if (!tid || !mid) continue;
      const prev = Number(_chatSeen[String(tid)] || 0);
      if (mid > prev) {
        _chatSeen[String(tid)] = mid;
        changed = true;
        const senderEmail = String((it == null ? void 0 : it.last_sender_email) || "").toLowerCase();
        if (!meEmail || senderEmail && senderEmail !== meEmail) {
          playNotifSound("chat");
          showChatPopup(it);
        }
      }
    }
    if (changed) {
      try {
        localStorage.setItem("gd_chat_seen", JSON.stringify(_chatSeen));
      } catch (_) {
      }
    }
  } catch (_) {
  }
}
function renderNotifications(data) {
  const badge = qs("#notifBadge");
  const menu = qs("#notifMenu");
  const btn = qs("#btnNotifs");
  const canSeeLeadAlerts = CURRENT_ROLE_ID === 1 || CURRENT_ROLE_ID === 12 || CURRENT_ROLE_ID === 2;
  let items = Array.isArray(data == null ? void 0 : data.items) ? data.items.slice() : [];
  if (!canSeeLeadAlerts) {
    items = items.filter((it) => !["leads_nuevos", "leads_sin_mov"].includes(it.key));
  }
  const total = items.reduce((acc, it) => acc + Number((it == null ? void 0 : it.count) || 0), 0);
  if (badge) {
    badge.style.display = "flex";
    badge.setAttribute("data-count", String(total));
    badge.textContent = String(total);
    badge.style.opacity = total > seenNotifCount ? "1" : ".6";
  }
  if (btn) {
    btn.title = `Notificaciones (${total})`;
  }
  if (!menu) return;
  menu.innerHTML = "";
  const totalTxt = `<div class="notif-empty" style="font-weight:900;margin-bottom:6px">Resumen (${total})</div>`;
  menu.innerHTML = totalTxt;
  if (!items.length) {
    menu.innerHTML += `<div class="notif-empty">Sin notificaciones.</div>`;
    return;
  }
  for (const it of items) {
    const div = document.createElement("div");
    div.className = "notif-item";
    const url = it.url || "/web/views/leads.html";
    div.innerHTML = `
      <div class="notif-title">${it.title || "Notificaci\xF3n"}</div>
      <div class="notif-count">${it.count || 0}</div>
    `;
    div.addEventListener("click", () => {
      if (url) {
        const frame = qs("#mainFrame");
        frame.src = viewURL(url);
      }
      menu.classList.remove("open");
    });
    menu.appendChild(div);
  }
  if (canSeeLeadAlerts) {
    const staleObj = (data == null ? void 0 : data.stale_leads) || {};
    const staleAll = Array.isArray(staleObj) ? staleObj : [
      ...staleObj.NUEVO || [],
      ...staleObj.CONTACTADO || [],
      ...staleObj.COTIZADO || []
    ];
    if (staleAll.length) {
      const sep = document.createElement("div");
      sep.className = "notif-empty";
      sep.style.fontWeight = "900";
      sep.textContent = "Leads sin movimiento";
      menu.appendChild(sep);
      staleAll.slice(0, 8).forEach((l) => {
        const row = document.createElement("div");
        row.className = "notif-item";
        row.dataset.lead = l.id_lead || "";
        row.innerHTML = `
          <div class="notif-title">${l.cliente || "Lead"}</div>
          <div class="notif-count">${l.created_at || ""}</div>
        `;
        row.addEventListener("click", () => {
          const id = row.dataset.lead;
          const ids = staleAll.map((x) => x.id_lead).filter(Boolean);
          openLeadsFromLock(id, ids);
          menu.classList.remove("open");
        });
        menu.appendChild(row);
      });
      if (staleAll.length > 8) {
        const more = document.createElement("div");
        more.className = "notif-item";
        more.innerHTML = `
          <div class="notif-title">Ver todos los leads sin movimiento</div>
          <div class="notif-count">${staleAll.length}</div>
        `;
        more.addEventListener("click", () => {
          var _a;
          openLeadsFromLock((_a = staleAll[0]) == null ? void 0 : _a.id_lead, staleAll.map((x) => x.id_lead).filter(Boolean));
          menu.classList.remove("open");
        });
        menu.appendChild(more);
      }
    }
  }
  {
    const leadItem = (items || []).find((x) => x.key === "leads_nuevos");
    const leadCount = Number((leadItem == null ? void 0 : leadItem.count) || 0);
    const staleItem = (items || []).find((x) => x.key === "leads_sin_mov");
    const staleCount = Number((staleItem == null ? void 0 : staleItem.count) || 0);
    const sysItem = (items || []).find((x) => x.key === "system_notifs");
    const sysCount = Number((sysItem == null ? void 0 : sysItem.count) || 0);
    if (CURRENT_ROLE_ID !== 2) {
      if (lastSystemCount !== null && sysCount > lastSystemCount) {
        playNotifSound("event");
        try{
          const delta = sysCount - lastSystemCount;
          const txt2 = delta === 1 ? "Nueva alerta" : `Nuevas alertas (+${delta})`;
          toast(`${txt2} — click para ver`, {
            kind: "ok",
            ms: 9000,
            onClick: () => openItem({ id: "system_notifs", label: "Alertas", url: "/web/views/system_notifs.html?v=20260326-1" })
          });
        }catch(_){}
      }
      lastSystemCount = sysCount;
    }
    if (lastLeadCount !== null && leadCount > lastLeadCount) {
      playNotifSound("lead");
      try{
        const delta = leadCount - lastLeadCount;
        const txt2 = delta === 1 ? "Nuevo lead" : `Nuevos leads (+${delta})`;
        toast(`${txt2} — click para abrir`, {
          kind: "ok",
          ms: 9000,
          onClick: () => openItem({ id: "leads_ver", label: "Ver Leads", url: "/web/views/leads.html?v=20260728-month1" })
        });
      }catch(_){}
    } else if (staleCount > 0 && (lastStaleCount === null || staleCount > lastStaleCount)) {
      playNotifSound("lead");
    } else if (lastNotifCount !== null && total > lastNotifCount) {
      playNotifSound();
    }
    lastLeadCount = leadCount;
    lastStaleCount = staleCount;
  }
  lastNotifCount = total;
}
let leadLockEl = null;
let pendingLeadOpen = null;
function openLeadById(id) {
  const frame = qs("#mainFrame");
  if (!frame) return;
  const lid = Number(id || 0);
  if (!lid) return;
  pendingLeadOpen = { id: lid };
  frame.src = viewURL(`/web/views/leads.html?v=20260728-month1`);
}
function openLeadsFromLock(openId, ids) {
  const frame = qs("#mainFrame");
  if (!frame) return;
  const idList = (ids || []).map((x) => String(x)).filter(Boolean);
  const params = new URLSearchParams();
  params.set("stale", "1");
  if (idList.length) params.set("stale_ids", idList.join(","));
  pendingLeadOpen = null;
  frame.src = viewURL(`/web/views/leads.html?v=20260728-month1&${params.toString()}`);
}
function renderLeadLock(data) {
  var _a, _b;
  if (!canUseTasksBadge()) {
    document.body.classList.remove("lead-lock");
    if (leadLockEl) leadLockEl.style.display = "none";
    return;
  }
  const lock = !!(data == null ? void 0 : data.lock);
  const stale = (data == null ? void 0 : data.stale_leads) || {};
  const staleNew = stale.NUEVO || [];
  const staleContact = stale.CONTACTADO || [];
  const staleCot = stale.COTIZADO || [];
  const bypass = (data == null ? void 0 : data.bypass) || {};
  const isUnlocked = !!bypass.active;
  const remainingToday = Number(bypass.remaining_today || 0);
  const maxDaily = Number(bypass.max_daily || 3);
  const minutes = Number(bypass.minutes || 60);
  if (!leadLockEl) {
    leadLockEl = document.createElement("div");
    leadLockEl.id = "leadLock";
    leadLockEl.innerHTML = `
      <div class="lead-lock-card">
        <div class="lead-lock-title">Leads sin movimiento</div>
        <div class="lead-lock-sub" id="leadLockSub">Debes trabajar estos leads antes de continuar.</div>
        <div class="lead-lock-list" id="leadLockList"></div>
        <div class="lead-lock-actions">
          <button class="btn" id="leadLockUnlock">Aplazar urgencia</button>
          <button class="btn" id="leadLockOpen">Ir a leads</button>
        </div>
      </div>
    `;
    document.body.appendChild(leadLockEl);
  }
  if (lock && !isUnlocked) {
    document.body.classList.add("lead-lock");
    const sub = leadLockEl.querySelector("#leadLockSub");
    if (sub) {
      sub.textContent = `Debes mover o comentar estos leads. Si hay una urgencia real, puedes aplazar ${minutes} min (${remainingToday}/${maxDaily} disponibles hoy).`;
    }
    const list = leadLockEl.querySelector("#leadLockList");
    if (list) {
      const allIds = [
        ...staleNew.map((x) => x.id_lead),
        ...staleContact.map((x) => x.id_lead)
      ].filter(Boolean);
      const block = (arr, label) => {
        if (!arr || !arr.length) return "";
        return `
          <div class="lead-lock-group">${label}</div>
          ${arr.map((l) => `
            <div class="lead-lock-item" data-lead="${l.id_lead}">
              <div class="lead-lock-name">${l.cliente || "Lead"}</div>
              <div class="lead-lock-date">${l.created_at || ""}</div>
            </div>
          `).join("")}
        `;
      };
      list.innerHTML = block(staleNew, "NUEVO (1\u20137 d\xEDas)") + block(staleContact, "CONTACTADO (+3 d\xEDas)") + (staleCot && staleCot.length ? `<div class="lead-lock-group">COTIZADO (+2 d\xEDas) \xB7 Solo alerta</div>` : "") || `<div class="lead-lock-empty">Sin detalle disponible.</div>`;
      list.querySelectorAll("[data-lead]").forEach((el) => {
        el.addEventListener("click", () => {
          openLeadsFromLock(null, allIds);
        });
      });
    }
    const btn = leadLockEl.querySelector("#leadLockOpen");
    if (btn) {
      const first = ((_a = staleNew[0]) == null ? void 0 : _a.id_lead) || ((_b = staleContact[0]) == null ? void 0 : _b.id_lead) || null;
      btn.onclick = () => {
        openLeadsFromLock(first, [
          ...staleNew.map((x) => x.id_lead),
          ...staleContact.map((x) => x.id_lead)
        ]);
      };
    }
    const unlockBtn = leadLockEl.querySelector("#leadLockUnlock");
    if (unlockBtn) {
      unlockBtn.disabled = remainingToday <= 0;
      unlockBtn.textContent = remainingToday <= 0 ? "L\xEDmite diario" : `Aplazar ${minutes} min`;
      unlockBtn.onclick = async () => {
        if (remainingToday <= 0) return;
        const reason = window.prompt("Motivo obligatorio de la urgencia. Esto queda en auditoría.");
        const txt = String(reason || "").trim();
        if (txt.length < 12) {
          alert("Debes escribir un motivo claro (mínimo 12 caracteres).");
          return;
        }
        try {
          const r = await fetch(`${API_BASE}/notifications/lead_lock/bypass`, {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ reason: txt })
          });
          const j = await r.json().catch(() => ({}));
          if (!r.ok) throw new Error(j.detail || j.message || `HTTP ${r.status}`);
          leadLockEl.style.display = "none";
          document.body.classList.remove("lead-lock");
          setTimeout(checkLeadLockOnce, 500);
        } catch (err) {
          alert("No pude aplazar: " + String((err && err.message) || err));
        }
      };
    }
    leadLockEl.style.display = "flex";
  } else {
    document.body.classList.remove("lead-lock");
    if (leadLockEl) leadLockEl.style.display = "none";
  }
}
function bindNotifications() {
  const btn = qs("#btnNotifs");
  const menu = qs("#notifMenu");
  if (!btn || !menu) return;
  // Shared hosting: notifications menu disabled (keep only "evento vendido" alert for SUPERADMIN).
  try {
    btn.style.display = "none";
  } catch (_) {
  }
  return;
  // Shared hosting: keep background polling minimal.
  // Requirement: notifications only for RRHH topics.
  try {
    const me = (window.GD && window.GD.me) || {};
    const roleName = String(me.role || me.rol || "").toUpperCase();
    const can = roleName.includes("RRHH");
    if (!can) {
      btn.style.display = "none";
      return;
    }
  } catch (_) {
    try {
      btn.style.display = "none";
    } catch (_2) {
    }
    return;
  }
  // Evita polling agresivo en roles masivos (operadores/conductores) cuando están usando el CRM general.
  // En portal_ops.html tienen su propia UI.
  try {
    if (CURRENT_ROLE_ID === 6 || CURRENT_ROLE_ID === 7) return;
  } catch (_) {
  }
  const openMenu = async () => {
    const data = await fetchNotifications();
    renderNotifications(data);
    menu.classList.add("open");
    menu.setAttribute("aria-hidden", "false");
    const total = Number((data == null ? void 0 : data.total) || 0);
    seenNotifCount = total;
    localStorage.setItem("gd_notif_seen", String(total));
  };
  const closeMenu = () => {
    menu.classList.remove("open");
    menu.setAttribute("aria-hidden", "true");
  };
  btn.addEventListener("mouseenter", openMenu);
  btn.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    if (menu.classList.contains("open")) closeMenu();
    else await openMenu();
  });
  let closeTimer = null;
  const scheduleClose = () => {
    if (closeTimer) clearTimeout(closeTimer);
    closeTimer = setTimeout(closeMenu, 220);
  };
  btn.addEventListener("mouseleave", scheduleClose);
  menu.addEventListener("mouseenter", () => {
    if (closeTimer) clearTimeout(closeTimer);
  });
  menu.addEventListener("mouseleave", scheduleClose);
  document.addEventListener("click", (ev) => {
    if (!menu.classList.contains("open")) return;
    if (ev.target.closest("#notifMenu") || ev.target.closest("#btnNotifs")) return;
    closeMenu();
  });
  document.addEventListener("click", enableSoundOnce, { once: true, capture: true });
  document.addEventListener("pointerdown", enableSoundOnce, { once: true, capture: true });
  document.addEventListener("keydown", enableSoundOnce, { once: true, capture: true });
  const baseDelay = (CURRENT_ROLE_ID === 1 || CURRENT_ROLE_ID === 12 || CURRENT_ROLE_ID === 2) ? 2e4 : 45e3;
  let delay = baseDelay;
  const maxDelay = 12e4;
  const tick = async () => {
    try {
      if (document.hidden) {
        setTimeout(tick, Math.min(maxDelay, Math.max(3e4, delay)));
        return;
      }
    } catch (_) {
    }
    const data = await fetchNotifications();
    if (data && data.ok) {
      delay = baseDelay;
      renderNotifications(data);
      renderLeadLock(data);
    } else {
      delay = Math.min(maxDelay, Math.max(8e3, delay * 2));
    }
    setTimeout(tick, delay);
  };
  // Avoid a thundering herd on login: add jitter before the first poll.
  setTimeout(tick, 8e3 + Math.floor(Math.random() * 6e3));
}

let soldWatcherStarted = false;
function startSoldEventPolling() {
  try {
    if (soldWatcherStarted) return;
    const me = (window.GD && window.GD.me) || {};
    const roleName = String(me.role || me.rol || "").toUpperCase();
    if (!roleName.includes("SUPER")) return;
    soldWatcherStarted = true;
  } catch (_) {
    return;
  }
  // Near-real-time for SUPERADMIN only. Keep it lightweight to avoid shared-host queue full.
  const baseDelay = 15e3;
  let delay = baseDelay;
  const maxDelay = 60e3;
  const jitter = () => Math.floor(Math.random() * 2500);
  const tick = async () => {
    try {
      if (document.hidden) {
        setTimeout(tick, Math.min(maxDelay, Math.max(30e3, delay)));
        return;
      }
    } catch (_) {
    }
    const data = await fetchSoldLatest();
    const item = (data && data.item) || null;
    if (!(data && data.ok)) {
      delay = Math.min(maxDelay, Math.max(5e3, delay * 2));
      setTimeout(tick, delay + jitter());
      return;
    }
    delay = baseDelay;
    if (item && item.id) {
      const lastId = Number(localStorage.getItem("gd_sold_last_id") || "0");
      if (Number(item.id) !== lastId) {
        localStorage.setItem("gd_sold_last_id", String(item.id));
        try {
          const p = (item && item.payload) || {};
          const marca = (p.marca || "").toString().trim();
          const cliente = (p.cliente || "").toString().trim();
          const fecha = (p.fecha_evento || "").toString().trim();
          const montoRaw = p.monto_cotizado ?? p.monto ?? p.total ?? null;
          const productos = Array.isArray(p.productos) ? p.productos : [];
          const fmtCLP = (v) => {
            try {
              const n = Number(v);
              if (!Number.isFinite(n)) return "";
              return `$${Math.round(n).toLocaleString("es-CL")}`;
            } catch (_) {
              return "";
            }
          };
          const mfmt = montoRaw != null ? fmtCLP(montoRaw) : "";
          const prodsHtml = (() => {
            try {
              const rows = [];
              for (const x of productos.slice(0, 10)) {
                const nm = x && x.producto ? String(x.producto).trim() : "";
                if (!nm) continue;
                const qty = x && x.cantidad != null ? String(x.cantidad) : "";
                rows.push(`<li style="margin:.15rem 0"><b>${escapeHtml(qty || "•")}</b> ${escapeHtml(nm)}</li>`);
              }
              return rows.length ? `<ul style="margin:.35rem 0 0 .95rem;padding:0">${rows.join("")}</ul>` : "";
            } catch (_) {
              return "";
            }
          })();
          const metaRows = [];
          if (cliente) metaRows.push(`<div style="margin:.15rem 0"><span style="opacity:.7;font-weight:1000">Cliente:</span> <b>${escapeHtml(cliente)}</b></div>`);
          if (marca) metaRows.push(`<div style="margin:.15rem 0"><span style="opacity:.7;font-weight:1000">Marca:</span> <b>${escapeHtml(marca)}</b></div>`);
          if (fecha) metaRows.push(`<div style="margin:.15rem 0"><span style="opacity:.7;font-weight:1000">Fecha:</span> <b>${escapeHtml(fecha)}</b></div>`);
          if (mfmt) metaRows.push(`<div style="margin:.15rem 0"><span style="opacity:.7;font-weight:1000">Monto:</span> <b>${escapeHtml(mfmt)}</b></div>`);
          if (item && item.id_lead) metaRows.push(`<div style="margin:.15rem 0"><span style="opacity:.7;font-weight:1000">Lead:</span> <b>#${escapeHtml(String(item.id_lead))}</b></div>`);
          const html = `
            <div style="text-align:left">
              <div style="padding:10px 12px;border-radius:14px;border:1px solid rgba(148,163,184,.18);background:rgba(2,6,23,.18)">
                ${metaRows.join("") || ""}
                ${prodsHtml ? `<div style="margin-top:.45rem;opacity:.85;font-weight:1100">Productos:</div>${prodsHtml}` : ""}
                ${item.body ? `<div style="opacity:.65;margin-top:.55rem;font-size:12px">${escapeHtml(item.body)}</div>` : ""}
              </div>
            </div>
          `.trim();
          const res = await Swal.fire({
            title: item.title || "Evento vendido",
            html,
            showCancelButton: true,
            confirmButtonText: "Abrir lead",
            cancelButtonText: "Marcar leído"
          });
          try {
            await markSystemNotifRead(item.id);
          } catch (_) {
          }
          if (res && res.isConfirmed) {
            openLeadById(item.id_lead);
          }
        } catch (_) {
        }
      }
    }
    setTimeout(tick, delay + jitter());
  };
  setTimeout(tick, 4e3 + jitter());
}
async function fetchMe() {
  var _a, _b, _c, _d, _e, _f, _g;
  try {
    if (!getToken()) {
      location.href = `${API_BASE}/web/login.html`;
      return;
    }
    const ctrl = new AbortController();
    const to = setTimeout(() => {
      try {
        ctrl.abort();
      } catch (_) {
      }
    }, 6e3);
    let r;
    try {
      r = await fetch(`${API_BASE}/me`, { headers: authHeaders(), signal: ctrl.signal });
    } finally {
      clearTimeout(to);
    }
    if (r.status === 401 || r.status === 403) {
      location.href = `${API_BASE}/web/login.html`;
      return;
    }
    if (!r.ok) throw new Error("me failed");
    const me = await r.json();
    window.GD = window.GD || {};
    window.GD.me = me;
    try {
      await maybeShowAutoDecline(me);
    } catch (_) {
    }
    try {
      await maybePromptSgjoMarkIn(me);
    } catch (_) {
    }
    const keyRaw = (_g = (_f = (_e = (_d = (_c = (_b = me.id) != null ? _b : (_a = me.user) == null ? void 0 : _a.id) != null ? _c : me.username) != null ? _d : me.email) != null ? _e : me.nombre) != null ? _f : me.name) != null ? _g : "default";
    setPrefKey(keyRaw);
    const name = me.username || me.nombre || me.name || "Usuario";
    const initials = (name || "U").split(" ").map((s) => s[0]).join("").slice(0, 2).toUpperCase();
    const userNameEl = qs("#userName");
    const avatarEl = qs("#userAvatar");
    if (userNameEl) userNameEl.textContent = name;
    if (avatarEl) {
      const prefs = getPrefs();
      const avatar = prefs.photoData || prefs.photo || me.avatar_url || "";
      if (avatar) {
        avatarEl.textContent = "";
        avatarEl.style.backgroundImage = `url('${avatar}')`;
        avatarEl.style.backgroundSize = "cover";
        avatarEl.style.backgroundPosition = "center";
      } else {
        avatarEl.textContent = initials;
      }
    }
    const roleName = normalizeRoleName((me.role || me.rol || "").toString());
    CURRENT_ROLE_ID = ROLE_IDS[roleName] || null;
    // Si el rol no está mapeado, no mostramos el CRM “vacío”.
    // EXCEPCIÓN: SUPERADMIN debe quedar con acceso total aunque el rol venga raro.
    if (!CURRENT_ROLE_ID) {
      if (roleName.includes("SUPER")) {
        CURRENT_ROLE_ID = 12;
      } else {
      enterRrhhOnlyMode(roleName || "UNKNOWN");
      return;
      }
    }
    // Si estamos en RRHH-only por alguna sesión anterior, salimos al tener rol válido.
    leaveRrhhOnlyMode();
    try {
      const sysBackup = qs("#sysBackup");
      if (sysBackup) sysBackup.style.display = (CURRENT_ROLE_ID === 1 || CURRENT_ROLE_ID === 12) ? "" : "none";
    } catch (_) {
    }
    const isOpsOnly = CURRENT_ROLE_ID === 6 || CURRENT_ROLE_ID === 7 || isOpsAppRole(roleName);
    if (isOpsOnly) {
      try {
        const here = String(location.pathname || "");
        if (!here.includes("/web/views/portal_ops.html")) {
          location.replace(`${API_BASE}/web/views/portal_ops.html?v=20260305-opsportal2`);
          return;
        }
      } catch (_) {
      }
      try {
        document.body.classList.add("ops-mode");
      } catch (_) {
      }
      const btnNotifs = qs("#btnNotifs");
      const btnGpt = qs("#btnGpt");
      const btnChat = qs("#btnChatTop");
      if (btnNotifs) btnNotifs.style.display = "none";
      if (btnGpt) btnGpt.style.display = "none";
      if (btnChat) btnChat.style.display = "none";
    }
    await Promise.all([loadUserMenuAccess(), loadSystemFeatures()]);
    buildMenu();
    try { enforceSgjoMarkInGate(); } catch (_) {}
    startLeadLockPolling();
    startTasksBadgePolling();
    startSoldEventPolling();
  } catch (_) {
    const userNameEl = qs("#userName");
    if (userNameEl) userNameEl.textContent = "Usuario";
  }
}

async function loadUserMenuAccess() {
  USER_MENU_ACCESS = null;
  try {
    const r = await fetch(`${API_BASE}/me/permissions`, { headers: authHeaders() });
    if (!r.ok) return;
    const data = await r.json().catch(() => null);
    const perms = data && data.permissions && typeof data.permissions === "object" ? data.permissions : null;
    if (!perms) return;
    const ids = Object.entries(perms)
      .filter(([, access]) => String(access || "").toLowerCase() === "read" || String(access || "").toLowerCase() === "full")
      .map(([id]) => String(id));
    // Si no hay permisos guardados, mantenemos matriz antigua para no bloquear usuarios al activar el módulo.
    if (ids.length) USER_MENU_ACCESS = new Set(ids);
  } catch (_) {
    USER_MENU_ACCESS = null;
  }
}

function bootstrapFromToken() {
  try {
    const token = getToken();
    if (!token) return { ok: false, redirected: false };
    const payload = decodeJwtPayload(token);
    if (!payload) return { ok: false, redirected: false };
    const roleName = normalizeRoleName(String(payload.role || payload.rol || ""));
    const name = String(payload.name || payload.nombre || payload.username || payload.sub || "Usuario");
    const marcas = Array.isArray(payload.marcas) ? payload.marcas : [];
    window.GD = window.GD || {};
    window.GD.me = window.GD.me || {};
    Object.assign(window.GD.me, {
      role: payload.role || payload.rol || "",
      rol: payload.role || payload.rol || "",
      name,
      nombre: name,
      username: payload.sub || "",
      marcas
    });
    try {
      setPrefKey(payload.sub || name);
    } catch (_) {
    }
    CURRENT_ROLE_ID = ROLE_IDS[roleName] || null;
    if (!CURRENT_ROLE_ID) {
      enterRrhhOnlyMode(roleName || "UNKNOWN");
      return { ok: true, redirected: true };
    }
    leaveRrhhOnlyMode();
    try {
      const sysBackup = qs("#sysBackup");
      if (sysBackup) sysBackup.style.display = (CURRENT_ROLE_ID === 1 || CURRENT_ROLE_ID === 12) ? "" : "none";
    } catch (_) {
    }
    const isOpsOnly = CURRENT_ROLE_ID === 6 || CURRENT_ROLE_ID === 7 || isOpsAppRole(roleName);
    if (isOpsOnly) {
      try {
        const here = String(location.pathname || "");
        if (here.endsWith("/web/index.html") || here.endsWith("/web/") || here.endsWith("/web")) {
          location.replace(`${API_BASE}/web/views/portal_ops.html?v=20260305-opsportal2`);
          return { ok: true, redirected: true };
        }
      } catch (_) {
      }
      try {
        document.body.classList.add("ops-mode");
      } catch (_) {
      }
    }
    buildMenu();
    // Refuerzo UX: si el usuario debe marcar y aún no tiene IN hoy, abre marcación automáticamente (CRM).
    try { enforceSgjoMarkInGate(); } catch (_) {}
    startLeadLockPolling();
    startTasksBadgePolling();
    startSoldEventPolling();
    return { ok: true, redirected: false };
  } catch (_) {
    return { ok: false, redirected: false };
  }
}

async function maybePromptSgjoMarkIn(me) {
  try {
    if (!getToken()) return;
    const r = await fetch(`${API_BASE}/rrhh/sgjo/today`, { headers: authHeaders({ "Accept": "application/json" }) });
    if (!r.ok) return;
    const j = await r.json().catch(() => null);
    if (!(j && j.ok)) return;
    const today = String(j.today || "").trim();
    if (!today) return;
    // Solo aplica a usuarios que realmente deben/pueden marcar.
    // SUPERADMIN/ADMIN típicamente devuelven puede_marcar=null: no los molestamos.
    if (j.puede_marcar !== true) return;
    if (j.has_in) return;
    const k = `gd_sgjo_in_dismissed_${today}`;
    // Fallback: algunos entornos no persisten localStorage (iframes/privado). Usamos sessionStorage también.
    try {
      if (localStorage.getItem(k) === "1") return;
    } catch (_) {
    }
    try {
      if (sessionStorage.getItem(k) === "1") return;
    } catch (_) {
    }
    const punto = String(j.default_punto_code || "").trim();
    // Si no hay punto default, igual abrimos la vista de marcación para que el usuario seleccione el punto.
    const markURL = punto ? `/web/views/rrhh_sgjo_marcacion.html?p=${encodeURIComponent(punto)}&v=${Date.now()}` : `/web/views/rrhh_sgjo_marcacion.html?v=${Date.now()}`;
    const openMark = () => {
      const frame = qs("#mainFrame");
      if (frame) frame.src = viewURL(markURL);
    };
    const role = String((me && (me.role || me.rol)) || "").toUpperCase();
    const isEjecutivo = role.includes("EJECUTIV");
    if (window.Swal) {
      const res = await Swal.fire({
        icon: "info",
        title: "Marcación de entrada",
        html: `<div style="text-align:left;opacity:.9">Para iniciar la jornada, registra tu <b>ENTRADA</b> (QR/GPS según tu permiso RRHH).</div>`,
        showCancelButton: !isEjecutivo,
        confirmButtonText: "Marcar ahora",
        cancelButtonText: "Más tarde",
        allowOutsideClick: !isEjecutivo,
        allowEscapeKey: !isEjecutivo
      });
      if (res.isConfirmed) openMark();
      else {
        try {
          localStorage.setItem(k, "1");
        } catch (_) {
        }
        try {
          sessionStorage.setItem(k, "1");
        } catch (_) {
        }
      }
      return;
    }
    if (confirm("RRHH: registra tu ENTRADA ahora?")) openMark();
    else {
      try {
        localStorage.setItem(k, "1");
      } catch (_) {
      }
      try {
        sessionStorage.setItem(k, "1");
      } catch (_) {
      }
    }
  } catch (_) {
  }
}

let _sgjoGateTimer = null;
function _ensureSgjoGateHost() {
  let el = document.getElementById("sgjoGateHost");
  if (el) return el;
  el = document.createElement("div");
  el.id = "sgjoGateHost";
  el.style.position = "fixed";
  el.style.left = "0";
  el.style.right = "0";
  el.style.bottom = "0";
  el.style.zIndex = "99998";
  el.style.padding = "10px 12px";
  el.style.display = "none";
  el.style.gap = "10px";
  el.style.alignItems = "center";
  el.style.justifyContent = "center";
  el.style.backdropFilter = "blur(10px)";
  el.style.background = "rgba(2,6,23,.85)";
  el.style.borderTop = "1px solid rgba(148,163,184,.18)";
  el.style.color = "var(--text)";
  el.innerHTML = `
    <div style="max-width:1100px;width:100%;display:flex;gap:10px;align-items:center;justify-content:space-between">
      <div style="font-weight:1100">
        RRHH: debes registrar tu <b>ENTRADA (IN)</b> para iniciar la jornada.
        <span style="opacity:.75;font-weight:900">Si estás saliendo, marca <b>SALIDA (OUT)</b>.</span>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end">
        <button type="button" class="btn" id="sgjoGateGo" style="font-weight:1000">Ir a marcar</button>
        <button type="button" class="btn" id="sgjoGateDismiss" style="opacity:.8">Ocultar</button>
      </div>
    </div>
  `;
  document.body.appendChild(el);
  return el;
}
function _showSgjoGate({ onGo = null } = {}) {
  const host = _ensureSgjoGateHost();
  host.style.display = "flex";
  const btnGo = host.querySelector("#sgjoGateGo");
  const btnDismiss = host.querySelector("#sgjoGateDismiss");
  if (btnGo) {
    btnGo.onclick = () => {
      try {
        if (onGo) onGo();
      } catch (_) {
      }
    };
  }
  // Regla negocio: ejecutivos NO pueden ocultar el recordatorio; deben marcar sí o sí.
  try {
    const role = String(((window.GD && (GD.me?.role || GD.me?.rol)) || "")).toUpperCase();
    const isEjecutivo = role.includes("EJECUTIV");
    if (btnDismiss) {
      if (isEjecutivo) {
        btnDismiss.style.display = "none";
      } else {
        btnDismiss.style.display = "";
        btnDismiss.onclick = () => {
          try {
            host.style.display = "none";
          } catch (_) {
          }
        };
      }
    }
  } catch (_) {
  }
}
function _hideSgjoGate() {
  try {
    const host = document.getElementById("sgjoGateHost");
    if (host) host.style.display = "none";
  } catch (_) {
  }
}
async function enforceSgjoMarkInGate() {
  try {
    if (!getToken()) return;
    const r = await fetch(`${API_BASE}/rrhh/sgjo/today`, { headers: authHeaders({ "Accept": "application/json" }) });
    if (!r.ok) return;
    const j = await r.json().catch(() => null);
    if (!(j && j.ok)) return;
    if (j.puede_marcar !== true) return;
    const punto = String(j.default_punto_code || "").trim();
    const markURL = punto ? `/web/views/rrhh_sgjo_marcacion.html?p=${encodeURIComponent(punto)}&auto=1&v=${Date.now()}` : `/web/views/rrhh_sgjo_marcacion.html?auto=1&v=${Date.now()}`;
    const openMark = () => {
      const frame = qs("#mainFrame");
      if (frame) {
        try {
          const cur = String(frame.src || "");
          // Evita loops: si ya estamos en la vista de marcación, no recargues.
          if (cur.includes("/web/views/rrhh_sgjo_marcacion.html")) return;
        } catch (_) {}
        frame.src = viewURL(markURL);
      }
    };
    if (j.has_in) {
      _hideSgjoGate();
      if (_sgjoGateTimer) {
        clearInterval(_sgjoGateTimer);
        _sgjoGateTimer = null;
      }
      return;
    }
    // Bloqueo suave: muestra gate y además abre marcación en el frame.
    _showSgjoGate({ onGo: openMark });
    try { openMark(); } catch (_) {}
    if (!_sgjoGateTimer) {
      _sgjoGateTimer = setInterval(() => {
        enforceSgjoMarkInGate();
      }, 35e3);
    }
  } catch (_) {
  }
}

async function maybePromptSgjoMarkOutBeforeLogout() {
  try {
    if (!getToken()) return true;
    const r = await fetch(`${API_BASE}/rrhh/sgjo/today`, { headers: authHeaders({ "Accept": "application/json" }) });
    if (!r.ok) return true;
    const j = await r.json().catch(() => null);
    if (!(j && j.ok)) return true;
    if (j.puede_marcar !== true) return true;
    if (!(j.has_in && !j.has_out)) return true;
    const punto = String(j.default_punto_code || "").trim();
    const markURL = punto ? `/web/views/rrhh_sgjo_marcacion.html?p=${encodeURIComponent(punto)}&v=${Date.now()}` : `/web/views/rrhh_sgjo_marcacion.html?v=${Date.now()}`;
    const openMark = () => {
      const frame = qs("#mainFrame");
      if (frame) frame.src = viewURL(markURL);
    };
    if (window.Swal) {
      const res = await Swal.fire({
        icon: "warning",
        title: "¿Marcar salida?",
        html: `<div style="text-align:left;opacity:.9">Hoy ya marcaste <b>ENTRADA</b> y aún no tienes <b>SALIDA</b>.<br/>Si estás terminando jornada, marca salida. Si solo sales del CRM, puedes cerrar sin marcar salida.</div>`,
        showCancelButton: true,
        showDenyButton: true,
        confirmButtonText: "Marcar salida",
        denyButtonText: "Cerrar sin marcar salida",
        cancelButtonText: "Cancelar"
      });
      if (res.isConfirmed) {
        openMark();
        toast("Marca tu SALIDA (OUT) y luego vuelve a cerrar sesión.", { kind: "info", ms: 9e3 });
        return false;
      }
      if (res.isDenied) return true;
      return false;
    }
    if (confirm("Hoy tienes IN pero no OUT. ¿Quieres marcar salida antes de cerrar sesión?")) {
      openMark();
      return false;
    }
    return true;
  } catch (_) {
    return true;
  }
}

async function maybeShowAutoDecline(me) {
  try {
    const role = String((me == null ? void 0 : me.role) || (me == null ? void 0 : me.rol) || "").toUpperCase();
    const okRole = role.includes("ADMIN") || role.includes("SUPERADMIN") || role.includes("EJECUTIV");
    if (!okRole) return;
    const payload = (me == null ? void 0 : me.auto_decline_last) || null;
    if (!payload || typeof payload !== "object") return;
    const run = String(payload.run_date || "");
    const total = Number(payload.total || 0) || 0;
    if (!run || total <= 0) return;
    const key = `gd_auto_decline_seen_${run}`;
    if (localStorage.getItem(key)) return;
    localStorage.setItem(key, "1");
    const monto = Number(payload.monto_total || 0) || 0;
    const msg = `Se auto-declinaron ${total} lead(s) por reglas (evento pasado / sin movimiento).\n${monto ? `Monto total declinado: $${Math.round(monto).toLocaleString("es-CL")}` : ""}`.trim();
    await Swal.fire({
      icon: "info",
      title: "Auto‑decline",
      text: msg,
      confirmButtonText: "OK"
    });
  } catch (_) {
  }
}
function findItemById(itemId) {
  for (const g of MENU) {
    for (const it of g.items || []) {
      if (it.id === itemId) return it;
    }
  }
  return null;
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (m) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#39;"
  })[m] || m);
}
function initUserMenu() {
  var _a, _b;
  const btn = qs("#btnUserMenu");
  const menu = qs("#userMenu");
  if (!btn || !menu) return;
  if (menu.parentElement !== document.body) {
    document.body.appendChild(menu);
  }
  const prefs = getPrefs();
  applyPrefs(prefs);
  const me = ((_a = window.GD) == null ? void 0 : _a.me) || {};
  const roleName = String(me.role || me.rol || "").toLowerCase();
  const nameEl = qs("#prefName");
  const emailEl = qs("#prefEmail");
  const phoneEl = qs("#prefPhone");
  const photoEl = qs("#prefPhoto");
  const photoFileEl = qs("#photoFile");
  const fontEl = qs("#fontSelect");
  const fontSizeEl = qs("#fontSize");
  const accentEl = qs("#accentPick");
  const textEl = qs("#textPick");
  const preview = qs("#prefPreview");
  const previewText = qs("#fontPreview");
  const previewModal = qs("#prefPreviewModal");
  const previewModalText = qs("#fontPreviewModal");
  const tgl = qs("#themeToggle");
  const photoBtn = qs("#prefPhotoBtn");
  const photoModal = qs("#photoModal");
  const photoModalBg = qs("#photoModalBg");
  const photoApply = qs("#photoApply");
  const photoCancel = qs("#photoCancel");
  const photoPreview = qs("#photoPreview");
  const prefModal = qs("#prefModal");
  const prefModalBg = qs("#prefModalBg");
  const prefApply = qs("#prefApply");
  const prefCancel = qs("#prefCancel");
  const chatBtn = qs("#btnChat");
  const chatModal = qs("#chatModal");
  const chatModalBg = qs("#chatModalBg");
  const chatClose = qs("#chatClose");
  const btnGpt = qs("#btnGpt");
  if (nameEl) nameEl.value = prefs.name || me.nombre || me.name || me.username || "";
  if (emailEl) emailEl.value = prefs.email || me.email || "";
  if (phoneEl) phoneEl.value = prefs.phone || me.telefono || "";
  if (photoEl) photoEl.value = prefs.photo || me.avatar_url || "";
  let photoDataTmp = prefs.photoData || "";
  if (fontEl) fontEl.value = prefs.font || "system";
  if (fontSizeEl) fontSizeEl.value = String(prefs.fontSize || 13);
  if (accentEl) accentEl.value = prefs.accent || "#19c37d";
  if (textEl) textEl.value = prefs.text || "#e2e8f0";
  const updatePreview = () => {
    if (!preview) return;
    const p = getPrefs();
    const fontKey = p.font || "system";
    const fontVal = fontKey && FONT_MAP[fontKey] ? FONT_MAP[fontKey] : fontKey;
    preview.style.setProperty("--accent", p.accent || "#19c37d");
    preview.style.setProperty("--text", p.text || "#e2e8f0");
    preview.style.fontFamily = fontVal || "";
    if (previewText) {
      previewText.style.fontFamily = fontVal || "";
      previewText.textContent = `Fuente actual: ${fontKey} \u2014 012345`;
    }
  };
  updatePreview();
  const setModalPreview = (next) => {
    const fontKey = next.font || "system";
    const fontVal = fontKey && FONT_MAP[fontKey] ? FONT_MAP[fontKey] : fontKey;
    if (previewModal) {
      previewModal.style.setProperty("--accent", next.accent || "#19c37d");
      previewModal.style.setProperty("--text", next.text || "#e2e8f0");
      previewModal.style.fontFamily = fontVal || "";
    }
    if (previewModalText) {
      previewModalText.style.fontFamily = fontVal || "";
      previewModalText.textContent = `Fuente nueva: ${fontKey} \u2014 012345`;
    }
  };
  const userNameEl = qs("#userName");
  const avatarEl = qs("#userAvatar");
  if (prefs.name && userNameEl) userNameEl.textContent = prefs.name;
  if ((prefs.photoData || prefs.photo || me.avatar_url) && avatarEl) {
    avatarEl.textContent = "";
    avatarEl.style.backgroundImage = `url('${prefs.photoData || prefs.photo || me.avatar_url}')`;
    avatarEl.style.backgroundSize = "cover";
    avatarEl.style.backgroundPosition = "center";
  }
  const openPhoto = () => {
    if (!photoModal) return;
    if (photoPreview) {
      const src = photoDataTmp || (photoEl == null ? void 0 : photoEl.value);
      if (src) {
        photoPreview.textContent = "";
        photoPreview.style.backgroundImage = `url('${src}')`;
        photoPreview.style.backgroundSize = "cover";
        photoPreview.style.backgroundPosition = "center";
      } else {
        photoPreview.textContent = "Sin foto";
        photoPreview.style.backgroundImage = "";
      }
    }
    photoModal.classList.add("open");
    photoModal.setAttribute("aria-hidden", "false");
  };
  const closePhoto = () => {
    if (!photoModal) return;
    photoModal.classList.remove("open");
    photoModal.setAttribute("aria-hidden", "true");
  };
  photoBtn == null ? void 0 : photoBtn.addEventListener("click", openPhoto);
  photoModalBg == null ? void 0 : photoModalBg.addEventListener("click", closePhoto);
  photoCancel == null ? void 0 : photoCancel.addEventListener("click", closePhoto);
  photoEl == null ? void 0 : photoEl.addEventListener("input", () => {
    if (!photoPreview) return;
    if (photoEl.value) {
      photoPreview.textContent = "";
      photoPreview.style.backgroundImage = `url('${photoEl.value}')`;
      photoPreview.style.backgroundSize = "cover";
      photoPreview.style.backgroundPosition = "center";
    } else {
      photoPreview.textContent = "Sin foto";
      photoPreview.style.backgroundImage = "";
    }
  });
  photoFileEl == null ? void 0 : photoFileEl.addEventListener("change", async () => {
    const file = photoFileEl.files && photoFileEl.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      photoDataTmp = reader.result || "";
      if (photoPreview) {
        photoPreview.textContent = "";
        photoPreview.style.backgroundImage = `url('${photoDataTmp}')`;
        photoPreview.style.backgroundSize = "cover";
        photoPreview.style.backgroundPosition = "center";
      }
    };
    reader.readAsDataURL(file);
  });
  photoApply == null ? void 0 : photoApply.addEventListener("click", () => {
    const p = getPrefs();
    if (photoDataTmp) {
      p.photoData = photoDataTmp;
      p.photo = "";
    } else {
      p.photo = (photoEl == null ? void 0 : photoEl.value) || "";
      p.photoData = "";
    }
    savePrefs(p);
    applyPrefs(p);
    if (avatarEl) {
      if (p.photoData || p.photo) {
        avatarEl.textContent = "";
        avatarEl.style.backgroundImage = `url('${p.photoData || p.photo}')`;
        avatarEl.style.backgroundSize = "cover";
        avatarEl.style.backgroundPosition = "center";
      } else if (p.name) {
        avatarEl.textContent = p.name.split(" ").map((s) => s[0]).join("").slice(0, 2).toUpperCase();
        avatarEl.style.backgroundImage = "";
      }
    }
    closePhoto();
  });
  const openUserMenu = () => {
    try {
      const p0 = getPrefs();
      if (tgl) tgl.checked = (p0.theme || getTheme()) === "light";
    } catch (_) {
    }
    try {
      const rect = btn.getBoundingClientRect();
      menu.style.position = "fixed";
      const inSidebar = !!btn.closest("#sidebar");
      if (inSidebar) {
        // Bottom sidebar: abrir hacia arriba, alineado al botón.
        menu.style.top = "auto";
        menu.style.right = "auto";
        menu.style.left = Math.max(12, Math.round(rect.left)) + "px";
        menu.style.bottom = Math.max(12, Math.round(window.innerHeight - rect.top + 8)) + "px";
      } else {
        // Topbar: dropdown clásico a la derecha
        menu.style.bottom = "auto";
        menu.style.top = rect.bottom + 8 + "px";
        menu.style.right = "14px";
        menu.style.left = "auto";
      }
    } catch (_) {
    }
    menu.classList.add("open");
    menu.style.display = "block";
    menu.style.pointerEvents = "auto";
    menu.setAttribute("aria-hidden", "false");
  };
  const closeUserMenu = () => {
    menu.classList.remove("open");
    menu.style.display = "none";
    menu.style.pointerEvents = "none";
    menu.setAttribute("aria-hidden", "true");
  };
  const toggleUserMenu = (e) => {
    var _a2;
    (_a2 = e == null ? void 0 : e.stopPropagation) == null ? void 0 : _a2.call(e);
    if (menu.classList.contains("open")) closeUserMenu();
    else openUserMenu();
  };
  btn.addEventListener("click", toggleUserMenu);
  document.addEventListener("click", (ev) => {
    if (ev.target.closest("#userMenu") || ev.target.closest("#btnUserMenu")) return;
    closeUserMenu();
  });
  window.addEventListener("blur", closeUserMenu);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) closeUserMenu();
  });
  document.addEventListener("focusin", (ev) => {
    if (!menu.classList.contains("open")) return;
    if (ev.target.closest("#userMenu") || ev.target.closest("#btnUserMenu")) return;
    closeUserMenu();
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && menu.classList.contains("open")) closeUserMenu();
  });
  const TAB_KEY = "gd_user_menu_tab";
  const tabBtns = Array.from(menu.querySelectorAll("[data-um-tab-btn]"));
  const tabSecs = Array.from(menu.querySelectorAll("[data-um-tab]"));
  const setTab = (tab) => {
    for (const b of tabBtns) {
      const on = b.getAttribute("data-um-tab-btn") === tab;
      b.classList.toggle("active", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    }
    for (const s of tabSecs) {
      const on = s.getAttribute("data-um-tab") === tab;
      s.classList.toggle("active", on);
    }
    try {
      localStorage.setItem(TAB_KEY, tab);
    } catch (_) {
    }
  };
  const initialTab = (() => {
    try {
      return localStorage.getItem(TAB_KEY) || "perfil";
    } catch (_) {
      return "perfil";
    }
  })();
  const validTabs = new Set(tabBtns.map((b) => b.getAttribute("data-um-tab-btn") || ""));
  setTab(validTabs.has(initialTab) ? initialTab : "perfil");
  tabBtns.forEach((b) => b.addEventListener("click", () => setTab(b.getAttribute("data-um-tab-btn") || "perfil")));
  window.GD = window.GD || {};
  window.GD.openUserMenu = openUserMenu;
  window.GD.closeUserMenu = closeUserMenu;
  normalizeUserMenuLayout(menu);
  if (chatModal) {
    const openChat = () => {
      enableSoundOnce();
      try {
        const fr = qs("#chatFrame") || chatModal.querySelector("iframe");
        if (fr) {
          fr.src = viewURL(`/web/views/chat.html?v=${Date.now()}`);
        }
      } catch (_) {
      }
      chatModal.classList.add("open");
      chatModal.setAttribute("aria-hidden", "false");
    };
    const closeChat = () => {
      chatModal.classList.remove("open");
      chatModal.setAttribute("aria-hidden", "true");
    };
    chatBtn == null ? void 0 : chatBtn.addEventListener("click", openChat);
    chatModalBg == null ? void 0 : chatModalBg.addEventListener("click", closeChat);
    chatClose == null ? void 0 : chatClose.addEventListener("click", closeChat);
    const topChat = qs("#btnChatTop");
    topChat == null ? void 0 : topChat.addEventListener("click", openChat);
  }
  if (btnGpt) {
    btnGpt.addEventListener("click", () => {
      window.open("https://chat.openai.com/", "_blank", "noopener");
    });
  }
  (_b = qs("#prefSave")) == null ? void 0 : _b.addEventListener("click", () => {
    const previous = getPrefs();
    const next = { ...previous };
    next.name = (nameEl == null ? void 0 : nameEl.value) || next.name;
    next.email = (emailEl == null ? void 0 : emailEl.value) || next.email;
    next.phone = (phoneEl == null ? void 0 : phoneEl.value) || next.phone;
    if (photoDataTmp) {
      next.photoData = photoDataTmp;
      next.photo = "";
    } else {
      next.photo = (photoEl == null ? void 0 : photoEl.value) || next.photo || "";
      next.photoData = next.photoData || "";
    }
    if (fontEl) next.font = fontEl.value;
    if (fontSizeEl) next.fontSize = Number(fontSizeEl.value || 13) || 13;
    if (accentEl) next.accent = accentEl.value;
    if (textEl) next.text = textEl.value;
    if (tgl) next.theme = tgl.checked ? "light" : "dark";
    if (prefModal) {
      setModalPreview(next);
      prefModal.classList.add("open");
      prefModal.setAttribute("aria-hidden", "false");
      prefModal.style.display = "block";
      prefModal.style.zIndex = "10000";
      const closePreview = () => {
        prefModal.classList.remove("open");
        prefModal.setAttribute("aria-hidden", "true");
        prefModal.style.display = "";
      };
      const revert = () => {
        applyPrefs(previous);
        updatePreview();
        if (userNameEl) userNameEl.textContent = previous.name || (me.username || me.nombre || me.name || "Usuario");
        const av = qs("#userAvatar");
        if (av) {
          if (previous.photoData || previous.photo) {
            av.textContent = "";
            av.style.backgroundImage = `url('${previous.photoData || previous.photo}')`;
            av.style.backgroundSize = "cover";
            av.style.backgroundPosition = "center";
          } else {
            av.style.backgroundImage = "";
            av.textContent = (previous.name || (userNameEl == null ? void 0 : userNameEl.textContent) || "U").split(" ").map((s) => s[0]).join("").slice(0, 2).toUpperCase();
          }
        }
      };
      prefCancel == null ? void 0 : prefCancel.addEventListener("click", () => {
        revert();
        closePreview();
      }, { once: true });
      prefModalBg == null ? void 0 : prefModalBg.addEventListener("click", () => {
        revert();
        closePreview();
      }, { once: true });
      prefApply == null ? void 0 : prefApply.addEventListener("click", () => {
        savePrefs(next);
        applyPrefs(next);
        updatePreview();
        const userNameEl2 = qs("#userName");
        if (userNameEl2 && next.name) userNameEl2.textContent = next.name;
        const av = qs("#userAvatar");
        if (av && (next.photoData || next.photo)) {
          av.textContent = "";
          av.style.backgroundImage = `url('${next.photoData || next.photo}')`;
          av.style.backgroundSize = "cover";
          av.style.backgroundPosition = "center";
        }
        closePreview();
      }, { once: true });
    } else {
      savePrefs(next);
      applyPrefs(next);
      updatePreview();
    }
  });
  menu.addEventListener("click", (ev) => {
    const target = ev.target.closest("[data-open]");
    if (!target) return;
    const id = target.dataset.open;
    const it = findItemById(id);
    if (it) openItem(it);
  });
}
const MENU = [
  {
    id: "leads",
    ico: "\u{1F4CC}",
    title: "Leads",
    items: [
      { id: "leads_ver", label: "Ver Leads", url: "/web/views/leads.html?v=20260728-month1" },
      { id: "leads_fil", label: "Filtrar Leads", url: "/web/views/filtro_leads.html?v=20260326-fil1" },
      { id: "crm360", label: "Comercial 360", url: "/web/views/crm360.html?v=20260723-fin-task1" }
    ]
  },
  {
    id: "checklist",
    ico: "\u2705",
    title: "Checklist",
    items: [
      { id: "chk_hoy", label: "Eventos (día)", url: "/web/views/checklist_eventos.html?v=20260728-ops2" },
      { id: "encuestas_eventos", label: "Encuestas post-evento", url: "/web/views/encuestas_eventos.html?v=20260728-surveys5" }
    ]
  },
  {
    id: "cotizador",
    ico: "\u{1F9FE}",
    title: "Cotizador",
    items: [
      { id: "historial", label: "Historial", url: "/web/views/historial_cotizaciones.html" }
    ]
  },
  {
    id: "reportes",
    ico: "\u{1F4CA}",
    title: "Reportes",
    items: [
      // Un solo acceso: la vista Reportes maneja tabs internos.
      { id: "rep_total", label: "Ir a Reportes", url: "/web/views/reportes_v2.html?v=20260723-rep-layout2" }
    ]
  },
  {
    id: "emkt",
    ico: "\u{1F4E3}",
    title: "E\u2011Mkt",
    items: [
      { id: "emkt_email", label: "Email marketing", url: "/web/views/emkt.html" }
    ]
  },
  {
    id: "alertas",
    ico: "\u{1F514}",
    title: "Alertas",
    items: [
      { id: "system_notifs", label: "Ver alertas", url: "/web/views/system_notifs.html?v=20260326-1" }
    ]
  },
  {
    id: "operaciones",
    ico: "\u{1F6E0}\uFE0F",
    title: "Operaciones",
    items: [
      { id: "op_rec", label: "Recetas", url: "/web/views/operaciones_recetas.html?v=20260304-1" },
      { id: "op_mice", label: "Mice and Place", url: "/web/views/operaciones_mice.html" },
      { id: "op_sep1", label: "\u2014", url: null, sep: true },
      { id: "op_ruta", label: "Ruta", url: "/web/views/ruta.html" },
      { id: "op_sep2", label: "\u2014", url: null, sep: true },
      { id: "op_ma_cat", label: "Categorias Maquinaria", url: "/web/views/op_maquinaria_categorias.html?v=20260304-1" },
      { id: "op_ma_inv", label: "Inventario Maquinaria", url: "/web/views/op_maquinaria_inventario.html?v=20260304-1" },
      { id: "op_ma_ficha", label: "Ficha Maquinaria", url: "/web/views/op_maquinaria_ficha.html?v=20260304-1" },
      { id: "op_sep3", label: "\u2014", url: null, sep: true },
      { id: "op_ca_ficha", label: "Ficha Camiones", url: "/web/views/op_camiones_ficha.html?v=20260304-1" },
      { id: "op_ca_ent", label: "Entrega de Camiones", url: "/web/views/op_camiones_entrega.html?v=20260304-1" },
      { id: "op_ca_dev", label: "Devolucion de Camiones", url: "/web/views/op_camiones_devolucion.html?v=20260304-1" },
      { id: "op_sep_chk", label: "\u2014", url: null, sep: true },
      { id: "op_chk_ev", label: "Checklist eventos", url: "/web/views/checklist_eventos.html?v=20260728-ops2" }
    ]
  },
  {
    id: "inventario",
    ico: "\u{1F4E6}",
    title: "Inventario",
    items: [
      { id: "inv_tomar", label: "Tomar inventario", url: "/web/views/inventario_mercancia.html#tomar" },
      { id: "inv_sep1", label: "\u2014", url: null, sep: true },
      { id: "inv_stock", label: "Stock ingredientes", url: "/web/views/inventario_mercancia.html#stock" },
      { id: "inv_class", label: "Clasificación MICE/OPS", url: "/web/views/inventario_clasificacion.html?v=20260504-1" },
      { id: "inv_cat", label: "Categor\xEDas", url: "/web/views/inventario_mercancia.html#categorias" },
      { id: "inv_uni", label: "Unidades", url: "/web/views/inventario_mercancia.html#unidades" },
      { id: "inv_prov", label: "Proveedores", url: "/web/views/inventario_mercancia.html#proveedores" },
      { id: "inv_sep2", label: "\u2014", url: null, sep: true },
      { id: "inv_mov", label: "Movimiento de Inventario", url: "/web/views/inventario_mercancia.html#movimientos" }
    ]
  },
  {
    id: "finanzas",
    ico: "\u{1F4B0}",
    title: "Finanzas",
    items: [
      { id: "pl", label: "P&L", url: "/web/views/finanzas_pl.html" },
      { id: "gast", label: "Cargar Gastos", url: "/web/views/finanzas_gastos.html" },
      { id: "plan_cuentas", label: "Plan de Cuentas", url: "/web/views/finanzas_pl.html#plan" }
    ]
  },
  {
    id: "operadores",
    ico: "\u{1F9D1}\u200D\u{1F373}",
    title: "Operadores/CHOPS",
    items: [
      { id: "op_gps", label: "Conectar GPS", url: "/web/views/conductores_gps.html", driverOnly: true },
      { id: "op_vruta", label: "Ver ruta (actual)", url: "/web/views/conductores_ruta.html", driverOnly: true },
      { id: "op_sep0", label: "\u2014", url: null, sep: true },
      { id: "op_cal", label: "Calendario", url: "/web/views/calendar.html?v=20260305-gcal1" },
      { id: "op_sep1", label: "\u2014", url: null, sep: true },
      { id: "op_menu_cam", label: "Menu Camale\xF3n", url: "/web/views/operadores.html?only=menu&brand=CAMALEON&v=20260305-m1#recetas" },
      { id: "op_menu_gou", label: "Menu Gourmet", url: "/web/views/operadores.html?only=menu&brand=GOURMET&v=20260305-m1#recetas" },
      { id: "op_menu_exp", label: "Menu Express", url: "/web/views/operadores.html?only=menu&brand=EXPRESS&v=20260305-m1#recetas" },
      { id: "op_menu_del", label: "Menu Del Sabor", url: "/web/views/operadores.html?only=menu&brand=DEL%20SABOR&v=20260305-m1#recetas" },
      { id: "op_menu_pet", label: "Menu Petras", url: "/web/views/operadores.html?only=menu&brand=PETRAS&v=20260305-m1#recetas" },
      { id: "op_menu_mf", label: "Menu Mas Flow", url: "/web/views/operadores.html?only=menu&brand=MAS%20FLOW&v=20260305-m1#recetas" },
      { id: "op_sep2", label: "\u2014", url: null, sep: true },
      { id: "op_uni", label: "Universidad GD", url: "/web/views/operadores.html?only=uni&v=20260305-m1#videos" },
      { id: "op_sep3", label: "\u2014", url: null, sep: true },
      { id: "op_vruta2", label: "Ver Ruta (pr\xF3x.)", url: "/web/views/operadores_ruta_futura.html" }
    ]
  },
  {
    id: "rrhh",
    ico: "\u{1F465}",
    title: "RRHH",
    items: [
      { id: "rrhh_hub", label: "RRHH", url: "/web/views/rrhh.html?v=20260518-rrhh5" },
      { id: "rrhh_mark_plan", label: "Turnos (planificador)", url: "/web/views/rrhh_marcaciones_planificador.html?v=20260518-1" },
      { id: "rrhh_turnos_visor", label: "Mi turno (teórico vs real)", url: "/web/views/rrhh_turnos_visor.html?v=20260518-1" }
    ]
  },
	  {
	    id: "tools",
	    ico: "\u{1F9F0}",
	    title: "Tools",
	    items: [
	      { id: "tool_gmail", label: "Correo", url: "/web/views/tools.html?v=20260806-video-ready-v1#correo" },
	      { id: "tool_ig", label: "Instagram", url: "/web/views/tools.html?v=20260806-video-ready-v1#instagram" },
	      { id: "tool_wapp", label: "WhatsApp", url: "/web/views/tools.html?v=20260806-video-ready-v1#whatsapp" },
	      { id: "tool_calc", label: "Calculadora", url: "/web/views/tools.html?v=20260806-video-ready-v1#calc" },
	      // Clima removido (Tools unificado). Si lo reactivamos, vuelve como item de Tools.
	    ]
	  },
  {
    id: "settings",
    ico: "\u2699\uFE0F",
    title: "Settings",
    items: [
      { id: "set_users", label: "Usuarios", url: "/web/views/settings.html?entity=usuarios&v=20260218-6" },
      { id: "set_marcas", label: "Marcas", url: "/web/views/settings.html?entity=marcas&v=20260218-6" },
      { id: "set_prod", label: "Productos (venta)", url: "/web/views/settings.html?entity=productos&v=20260218-6" },
      { id: "set_commissions_config", label: "Configuración comisiones", url: "/web/views/settings_comisiones.html?v=20260722-comm3" },
      { id: "set_com", label: "Comunas", url: "/web/views/settings.html?entity=comunas&v=20260218-6" },
      { id: "set_tc", label: "Tipos Cliente", url: "/web/views/settings.html?entity=tipos_cliente&v=20260218-6" },
      { id: "set_platforms", label: "Plataformas / origen de leads", url: "/web/views/settings.html?entity=plataformas&v=20260729-plat2" },
      { id: "set_el", label: "Estados Lead", url: "/web/views/settings.html?entity=estados_lead&v=20260218-6" },
      { id: "set_roles", label: "Roles", url: "/web/views/settings.html?entity=roles&v=20260218-6" },
      { id: "set_permissions", label: "Permisos", url: "/web/views/settings_permissions.html?v=20260610-1" },
      { id: "set_features", label: "Funcionamiento de módulos", url: "/web/views/settings_features.html?v=20260728-1" },
      { id: "set_whatsapp_coexistence", label: "Coexistencia WhatsApp", url: "/web/views/settings_whatsapp_coexistence.html?v=20260728-1" },
      { id: "set_audit", label: "Auditoría", url: "/web/views/settings_audit.html?v=20260720-audit-lead-modal1" },
      { id: "set_notify_email", label: "Notificaciones (correo)", url: "/web/views/settings_notifs_email.html?v=20260601-3" },
      { id: "set_metas", label: "Metas ventas", url: "/web/views/settings_metas.html?v=20260723-metas1" },
      { id: "set_bak", label: "Backups", url: "/web/views/backups.html", noSidebar: true }
    ]
  }
];

const FAV_KEY = "gd_sidebar_favs";
function getFavIds() {
  try {
    const raw = localStorage.getItem(FAV_KEY) || "[]";
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr.map((x) => String(x)).filter(Boolean) : [];
  } catch (_) {
    return [];
  }
}
function setFavIds(ids) {
  try {
    const uniq = [...new Set((ids || []).map((x) => String(x)).filter(Boolean))];
    localStorage.setItem(FAV_KEY, JSON.stringify(uniq));
  } catch (_) {
  }
}
function isFav(id) {
  return getFavIds().includes(String(id));
}
function toggleFav(id) {
  const ids = getFavIds();
  const sid = String(id);
  const next = ids.includes(sid) ? ids.filter((x) => x !== sid) : [sid, ...ids];
  setFavIds(next);
}
function listVisibleMenuItems() {
  // Some installs map ADMIN-like roles to id=12; ensure it inherits SUPERADMIN permissions.
  const allowed = (() => {
    if (!CURRENT_ROLE_ID) return /* @__PURE__ */ new Set();
    if (PERMISSIONS[CURRENT_ROLE_ID]) return PERMISSIONS[CURRENT_ROLE_ID];
    if (CURRENT_ROLE_ID === 12 && PERMISSIONS[1]) return PERMISSIONS[1];
    return /* @__PURE__ */ new Set();
  })();
  const isDriver = CURRENT_ROLE_ID === 6;
  const isAdmin = (CURRENT_ROLE_ID === 1 || CURRENT_ROLE_ID === 12);
  const out = [];
  for (const g of MENU) {
    for (const it of g.items) {
      if (it.sep || it.noSidebar || it.disabled || !it.url || !isItemFeatureEnabled(it.id)) continue;
      if (allowed && !allowed.has(it.id)) continue;
      if (USER_MENU_ACCESS && !USER_MENU_ACCESS.has(String(it.id))) continue;
      if (it.driverOnly && !isDriver && !isAdmin) continue;
      out.push({ ...it, groupId: g.id, groupTitle: g.title, groupIco: g.ico });
    }
  }
  return out;
}
let ACTIVE_ITEM_ID = null;
let CURRENT_ROLE_ID = null;
let CURRENT_ALLOWED = /* @__PURE__ */ new Set();
let USER_MENU_ACCESS = null;
let SYSTEM_FEATURES = null;
let TASKS_BADGE = { open_total: 0, overdue_total: 0, open_contactar: 0, overdue_contactar: 0 };
let TASKS_POLL_HANDLE = null;
let TASKS_SYNC_INFLIGHT = false;
const ROLE_IDS = {
  // IMPORTANT: must match Settings → roles table (id_rol).
  "ADMIN": 1,
  "EJECUTIVO DE VENTAS": 2,
  "VENDEDOR": 2,
  "JEFE DE OPERACIONES": 3,
  "BODEGUERO": 4,
  "COMPRAS": 5,
  "CONDUCTOR": 6,
  "CONDUCTOR (CHOP)": 6,
  "CHOP": 6,
  "CHOFER": 6,
  "OPERADOR": 7,
  "MICE": 8,
  "OPERADOR PATIO": 9,
  "OPERADOR DE PATIO": 9,
  "OP PATIO": 9,
  "FINANZAS": 11,
  "SUPERADMIN": 12,
  "SUPER_ADMIN": 12,
  "SUPER ADMIN": 12,
  "MARKETING": 13,
  "RRHH": 14,
  "RECURSOS HUMANOS": 14
};

function normalizeRoleName(roleName) {
  const r = String(roleName || "").trim().toUpperCase();
  if (!r) return "";
  if (/^\\d+$/.test(r)) {
    const mp = {
      // From Settings → roles (id_rol)
      "1": "ADMIN",
      "2": "EJECUTIVO DE VENTAS",
      "3": "JEFE DE OPERACIONES",
      "4": "BODEGUERO",
      "5": "COMPRAS",
      "6": "CONDUCTOR",
      "7": "OPERADOR",
      "8": "MICE",
      "9": "OPERADOR PATIO",
      "11": "FINANZAS",
      "12": "SUPERADMIN",
      "13": "MARKETING",
      "14": "RRHH"
    };
    return mp[r] || r;
  }
  return r;
}

async function loadSystemFeatures() {
  SYSTEM_FEATURES = null;
  try {
    const response = await fetch(`${API_BASE}/features`, { headers: authHeaders() });
    if (!response.ok) return;
    const data = await response.json().catch(() => null);
    const map = new Map();
    for (const feature of (data?.items || [])) {
      for (const itemId of (feature?.menu_ids || [])) {
        map.set(String(itemId), Boolean(feature.enabled));
      }
    }
    SYSTEM_FEATURES = map;
  } catch (_) {
    SYSTEM_FEATURES = null;
  }
}

function isItemFeatureEnabled(itemId) {
  return !SYSTEM_FEATURES || SYSTEM_FEATURES.get(String(itemId)) !== false;
}

function isOpsAppRole(roleName) {
  const v = normalizeRoleName(roleName);
  // OJO: "OPERADOR PATIO" NO es portal de Operaciones; va a RRHH portal (marcación/horario).
  return v === "OPERADOR" || v === "CONDUCTOR" || v === "CHOFER" || v === "CHOP" || v === "CONDUCTOR (CHOP)";
}
const PERMISSIONS = {
		  1: /* @__PURE__ */ new Set([
    "dash_home",
    "rrhh_hub",
    "op_gps",
    "op_vruta",
    "op_cal",
    "op_menu_cam",
    "op_menu_gou",
    "op_menu_exp",
    "op_menu_del",
    "op_menu_pet",
    "op_menu_mf",
    "op_uni",
    "op_vruta2",
    "leads_ver",
    "leads_fil",
    "crm360",
    "historial",
    "rep_funnel",
    "rep_cierre",
    "rep_tipo",
    "rep_total",
    "rep_cxc",
    "rep_cxp",
    "emkt_email",
    "rep_com",
    "rep_surveys",
    "rep_prod",
    "rep_cli",
    "rep_hoy",
    "rep_dia",
    "op_rec",
    "op_mice",
    "op_ruta",
    "op_ma_cat",
    "op_ma_inv",
    "op_ma_ficha",
    "op_ca_ficha",
    "op_ca_ent",
    "op_ca_dev",
	    "inv_tomar",
	    "inv_stock",
	    "inv_prod",
	    "inv_class",
	    "inv_cat",
	    "inv_uni",
	    "inv_prov",
	    "inv_mov",
	    "tool_gmail",
	    "tool_wapp",
	    "tool_ig",
      "tool_chat",
    "tool_cal",
    "tool_calc",
    "tool_wx",
    "tools_hub",
    "gps",
    "vruta",
    "pl",
    "gast",
    "evt",
    "plan_cuentas",
    "rrhh_nomina",
    "rrhh_staff",
    "rrhh_sgjo",
    "rrhh_solicitudes",
    "set_users",
    "set_marcas",
    "set_prod",
    "set_commissions_config",
    "set_com",
    "set_tc",
    "set_el",
    "set_roles",
    "set_permissions",
    "set_features",
    "set_whatsapp_coexistence",
    "set_audit",
    "set_notify_email",
    "set_metas",
	    "set_bak",
		    "chk_hoy"
		    ,"tasks_my"
		    ,"system_notifs"
		    ,"events_calendar"
		  ]),
	  10: /* @__PURE__ */ new Set([
	    "rrhh_hub",
	    "leads_ver",
	    "leads_fil",
	    "crm360",
	    "historial",
	    "system_notifs",
	    "tool_wapp",
	    "tool_calc",
	    "tools_hub",
	    "tool_chat",
	    "set_prod",
	    "set_com",
	    "set_el"
	  ]),
	  2: /* @__PURE__ */ new Set([
	    "dash_home",
	    "rrhh_hub",
	    "leads_ver",
	    "leads_fil",
    "crm360",
    "historial",
    "rep_funnel",
    "rep_cierre",
    "rep_tipo",
    "rep_total",
    "rep_com",
    "rep_surveys",
    "rep_prod",
    "rep_cli",
    "rep_hoy",
    "rep_dia",
    "tool_gmail",
    "tool_wapp",
    "tool_ig",
    "tool_chat",
    "tool_cal",
    "tool_calc",
    "tool_wx",
    "tools_hub",
    "vruta",
    "set_prod",
    "set_com",
	    "set_el",
		    "chk_hoy"
		    ,"tasks_my"
		    ,"system_notifs"
		    ,"events_calendar"
		  ]),
		  3: /* @__PURE__ */ new Set([
		    "rrhh_hub",
		    "system_notifs",
		    "op_rec",
		    "op_mice",
		    "op_ruta",
	    "op_ma_cat",
	    "op_ma_inv",
    "op_ma_ficha",
    "op_ca_ficha",
    "op_ca_ent",
    "op_ca_dev",
    "inv_tomar",
    "inv_stock",
    "inv_prod",
    "inv_cat",
    "inv_uni",
    "inv_prov",
    "inv_mov",
    "tool_wapp",
    "tool_cal",
    "tool_calc",
    "tool_wx",
    "tools_hub",
    "tool_chat",
    "gps",
    "vruta",
	    "set_marcas",
		    "set_prod",
		    "set_com",
		    "rrhh_staff",
        "rrhh_sgjo",
			    "rrhh_solicitudes",
			    "chk_hoy"
		  ]),
	  4: /* @__PURE__ */ new Set([
	    "rrhh_hub",
    "inv_tomar",
    "inv_stock",
    "inv_prod",
    "inv_cat",
    "inv_uni",
    "inv_prov",
    "inv_mov",
    "tool_wapp",
    "tool_cal",
    "tool_calc",
    "tool_wx",
    "tools_hub",
	    "tool_chat",
	    "vruta",
	    "set_prod"
	  ]),
	  // FINANZAS (id_rol=11): ve RRHH + Finanzas + Historial (cotizaciones)
	  11: /* @__PURE__ */ new Set([
	    "rrhh_hub",
	    "pl",
	    "gast",
	    "evt",
	    "plan_cuentas",
	    "historial"
	  ]),
	  // RRHH (id_rol=14): ve RRHH + Finanzas + Historial (para auditoría/abonos)
	  14: /* @__PURE__ */ new Set([
	    "rrhh_hub",
	    "pl",
	    "gast",
	    "evt",
	    "plan_cuentas",
	    "historial"
	  ]),
	  5: /* @__PURE__ */ new Set([
	    "rrhh_hub",
	    "op_rec",
	    "op_mice",
	    "op_ruta",
    "op_ma_cat",
    "op_ma_inv",
    "op_ma_ficha",
    "op_ca_ficha",
    "op_ca_ent",
    "op_ca_dev",
    "inv_tomar",
    "inv_stock",
    "inv_prod",
    "inv_cat",
    "inv_uni",
    "inv_prov",
    "inv_mov",
    "tool_wapp",
    "tool_cal",
    "tool_calc",
    "tool_wx",
    "tools_hub",
    "tool_chat",
	    "vruta",
	    "gast",
	    "set_prod"
	  ]),
  6: /* @__PURE__ */ new Set([
    "rrhh_hub",
    "op_gps",
    "op_vruta",
    "op_cal",
    "op_menu_cam",
    "op_menu_gou",
    "op_menu_exp",
    "op_menu_del",
    "op_menu_pet",
    "op_menu_mf",
    "op_uni",
    "op_vruta2"
  ]),
  7: /* @__PURE__ */ new Set([
    "rrhh_hub",
    "op_cal",
    "op_menu_cam",
    "op_menu_gou",
    "op_menu_exp",
    "op_menu_del",
    "op_menu_pet",
    "op_menu_mf",
    "op_uni",
    "op_vruta2"
  ]),
		  8: /* @__PURE__ */ new Set([
		    "rrhh_hub",
		    "system_notifs",
		    "op_rec",
		    "op_mice",
		    "op_ruta",
	    "inv_tomar",
	    "inv_stock",
    "inv_prod",
    "inv_cat",
    "inv_uni",
    "inv_prov",
    "inv_mov",
    "tool_wapp",
    "tool_cal",
    "tool_calc",
    "tool_wx",
    "tools_hub"
    ,"tool_chat"
    ,"set_prod"
  ])
};

// Alias: SUPER ADMIN (id_rol=12) debe tener exactamente los permisos del ADMIN (id_rol=1),
// de lo contrario el menú queda vacío y el sistema lo manda erróneamente al portal RRHH-only.
try {
  if (PERMISSIONS[1] && !PERMISSIONS[12]) {
    PERMISSIONS[12] = PERMISSIONS[1];
  }
} catch (_) {
}
function buildMenu() {
  const nav = qs("#sideMenu");
  nav.innerHTML = "";
  let allowed = (() => {
    if (!CURRENT_ROLE_ID) return /* @__PURE__ */ new Set();
    if (PERMISSIONS[CURRENT_ROLE_ID]) return PERMISSIONS[CURRENT_ROLE_ID];
    if (CURRENT_ROLE_ID === 12 && PERMISSIONS[1]) return PERMISSIONS[1];
    return /* @__PURE__ */ new Set();
  })();
  // Safety: SUPERADMIN siempre debe tener menú completo aunque CURRENT_ROLE_ID venga raro.
  try {
    const me = (window.GD && window.GD.me) || {};
    const rk = normalizeRoleName(me.role || me.rol || "");
    if ((rk === "SUPERADMIN" || rk === "SUPER ADMIN" || rk === "SUPER_ADMIN") && PERMISSIONS[1]) {
      allowed = PERMISSIONS[1];
    }
  } catch (_) {
  }
  CURRENT_ALLOWED = allowed;
  const isDriver = CURRENT_ROLE_ID === 6;
  const isAdmin = (CURRENT_ROLE_ID === 1 || CURRENT_ROLE_ID === 12);
  let lastGroupId = null;

  // Favoritos (items fijados)
  try {
    const favIds = getFavIds();
    if (favIds.length) {
      const visible = listVisibleMenuItems();
      const byId = /* @__PURE__ */ new Map(visible.map((x) => [String(x.id), x]));
      const favItems = [];
      for (const id of favIds) {
        const it = byId.get(String(id));
        if (it) favItems.push(it);
      }
      if (favItems.length) {
        const group = document.createElement("section");
        group.className = "menu-group open";
        group.dataset.group = "favoritos";
        const head = document.createElement("button");
        head.type = "button";
        head.className = "menu-group-head";
        head.title = "Favoritos";
        head.innerHTML = `
          <span class="menu-ico">★</span>
          <span class="menu-title">Favoritos</span>
          <span class="menu-chevron">\u203A</span>
        `;
        const items = document.createElement("div");
        items.className = "menu-items";
        for (const it of favItems) {
          const b = document.createElement("button");
          b.type = "button";
          b.className = "menu-item";
          b.dataset.item = it.id;
          b.title = it.label;
          b.innerHTML = `<span class="dot"></span><span class="lbl">${it.label}</span><span class="fav-ico on" data-fav="${it.id}" title="Quitar fijado">★</span>`;
          b.addEventListener("click", () => openItem(it));
          b.querySelector("[data-fav]")?.addEventListener("click", (ev) => {
            ev.preventDefault();
            ev.stopPropagation();
            toggleFav(it.id);
            buildMenu();
          });
          items.appendChild(b);
        }
        head.addEventListener("click", () => {
          const isOpen = group.classList.contains("open");
          if (isOpen) group.classList.remove("open");
          else group.classList.add("open");
        });
        group.appendChild(head);
        group.appendChild(items);
        nav.appendChild(group);

        const divider = document.createElement("div");
        divider.className = "menu-sep";
        nav.appendChild(divider);
        lastGroupId = "favoritos";
      }
    }
  } catch (_) {
  }
  for (const g of MENU) {
    const visibleItems = allowed
      ? g.items.filter((it) => !it.sep && !it.noSidebar && !it.disabled && isItemFeatureEnabled(it.id) && allowed.has(it.id) && (!USER_MENU_ACCESS || USER_MENU_ACCESS.has(String(it.id))) && (!it.driverOnly || isDriver))
      : g.items.filter((it) => !it.sep && !it.noSidebar && !it.disabled && isItemFeatureEnabled(it.id) && (!USER_MENU_ACCESS || USER_MENU_ACCESS.has(String(it.id))) && (!it.driverOnly || isDriver));
    if (!visibleItems.length) continue;
    if (lastGroupId === "operadores" && (g.id === "rrhh" || g.id === "tools" || g.id === "settings")) {
      const divider = document.createElement("div");
      divider.className = "menu-sep";
      nav.appendChild(divider);
    }
    const group = document.createElement("section");
    group.className = "menu-group";
    group.dataset.group = g.id;
    const head = document.createElement("button");
    head.type = "button";
    head.className = "menu-group-head";
    head.title = g.title;
    head.innerHTML = `
      <span class="menu-ico">${g.ico}</span>
      <span class="menu-title">${g.title}</span>
      <span class="menu-chevron">\u203A</span>
    `;
    const items = document.createElement("div");
    items.className = "menu-items";
    for (const it of g.items) {
      if (it.sep) {
        const sep = document.createElement("div");
        sep.style.height = "8px";
        items.appendChild(sep);
        continue;
      }
      if (it.noSidebar) continue;
      if (it.disabled) continue;
      if (allowed && !allowed.has(it.id)) continue;
      if (it.driverOnly && !isDriver && !isAdmin) continue;
      const b = document.createElement("button");
      b.type = "button";
      b.className = "menu-item";
      b.dataset.item = it.id;
      b.title = it.label;
      const favOn = isFav(it.id);
      const tasksBadge = it.id === "crm360" && TASKS_BADGE && Number(TASKS_BADGE.overdue_total || 0) > 0 ? `<span class="pill tasks-pill" style="margin-left:auto;border-color:rgba(239,68,68,.45);background:rgba(239,68,68,.14);font-size:11px;font-weight:1100;padding:3px 8px;border-radius:999px">${Number(TASKS_BADGE.overdue_total || 0)}</span>` : "";
      b.innerHTML = `<span class="dot"></span><span class="lbl">${it.label}</span>${tasksBadge}<span class="fav-ico ${favOn ? "on" : ""}" data-fav="${it.id}" title="${favOn ? "Quitar fijado" : "Fijar"}">★</span>`;
      b.addEventListener("click", () => openItem(it));
      b.querySelector("[data-fav]")?.addEventListener("click", (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        toggleFav(it.id);
        buildMenu();
      });
      items.appendChild(b);
    }
    head.addEventListener("click", () => {
      const isOpen = group.classList.contains("open");
      closeAllGroups();
      if (!isOpen) group.classList.add("open");
    });
    group.appendChild(head);
    group.appendChild(items);
    nav.appendChild(group);
    lastGroupId = g.id;
  }

  // Si el menú queda vacío (por rol sin permisos), entramos al portal RRHH.
  try {
    const anyItem = nav.querySelector(".menu-item");
    if (!anyItem && getToken()) {
      enterRrhhOnlyMode("EMPTY_MENU");
    }
  } catch (_) {
  }
}

function canUseTasksBadge() {
  const me = (window.GD && window.GD.me) || {};
  const roleName = String(me.role || me.rol || "").toUpperCase();
  return CURRENT_ROLE_ID === 1 || CURRENT_ROLE_ID === 12 || CURRENT_ROLE_ID === 2 || roleName.includes("EJECUTIVO");
}

function renderTasksBadge() {
  try {
    const overdue = Number((TASKS_BADGE && TASKS_BADGE.overdue_total) || 0);
    const btn = qs('#sideMenu [data-item="crm360"]');
    if (!btn) return;
    const existing = btn.querySelector(".tasks-pill");
    if (overdue > 0) {
      const pill = existing || document.createElement("span");
      pill.className = "pill tasks-pill";
      pill.style.marginLeft = "auto";
      pill.style.borderColor = "rgba(239,68,68,.45)";
      pill.style.background = "rgba(239,68,68,.14)";
      pill.style.fontSize = "11px";
      pill.style.fontWeight = "1100";
      pill.style.padding = "3px 8px";
      pill.style.borderRadius = "999px";
      pill.textContent = String(overdue);
      if (!existing) {
        const fav = btn.querySelector("[data-fav]");
        if (fav && fav.parentElement === btn) btn.insertBefore(pill, fav);
        else btn.appendChild(pill);
      }
    } else {
      existing?.remove?.();
    }
  } catch (_) {
  }
}

function maybeWarnTasks() {
  try {
    const overdueContact = Number((TASKS_BADGE && TASKS_BADGE.overdue_contactar) || 0);
    if (!overdueContact || overdueContact <= 0) return;
    const today = new Date();
    const key = `gd_tasks_warn_${today.getFullYear()}${String(today.getMonth() + 1).padStart(2, "0")}${String(today.getDate()).padStart(2, "0")}`;
    if (localStorage.getItem(key) === "1") return;
    localStorage.setItem(key, "1");
    Swal.fire({
      icon: "warning",
      title: "Tareas vencidas",
      html: `<div style="text-align:left">Tienes <b>${overdueContact}</b> seguimiento(s) vencido(s).<br/>Revisa <b>Comercial 360</b> para priorizar contactos.</div>`,
      confirmButtonText: "Ir a Comercial 360",
      showCancelButton: true,
      cancelButtonText: "Más tarde",
      customClass: { popup: "gdModal" }
    }).then((r) => {
      if (r.isConfirmed) {
        const it = findItemById("crm360");
        if (it) openItem(it);
      }
    });
  } catch (_) {
  }
}

async function fetchTasksSummary() {
  if (!canUseTasksBadge()) return;
  try {
    const s = await apiJSON("/tasks/summary");
    if (s && s.ok) {
      TASKS_BADGE = {
        open_total: Number(s.open_total || 0),
        overdue_total: Number(s.overdue_total || 0),
        open_contactar: Number(s.open_contactar || 0),
        overdue_contactar: Number(s.overdue_contactar || 0)
      };
      renderTasksBadge();
      maybeWarnTasks();
    }
  } catch (_) {
  }
}

async function syncTasksOnce() {
  if (!canUseTasksBadge()) return;
  if (TASKS_SYNC_INFLIGHT) return;
  TASKS_SYNC_INFLIGHT = true;
  try {
    await apiJSON("/tasks/sync", { method: "POST" });
  } catch (_) {
  } finally {
    TASKS_SYNC_INFLIGHT = false;
  }
  await fetchTasksSummary();
}

function startTasksBadgePolling() {
  if (!canUseTasksBadge()) return;
  if (TASKS_POLL_HANDLE) return;
  // Shared hosting: avoid heavy background activity on every login.
  // - No auto-sync (can be triggered from the Tasks view when needed).
  // - Poll summary less frequently with initial jitter.
  const jitter = Math.floor(Math.random() * 8e3);
  setTimeout(() => {
    fetchTasksSummary();
  }, 12e3 + jitter);
  TASKS_POLL_HANDLE = setInterval(() => {
    fetchTasksSummary();
  }, 12 * 60 * 1000);
}

function closeAllGroups() {
  for (const el of document.querySelectorAll(".menu-group.open")) {
    el.classList.remove("open");
  }
}
async function openItem(it) {
  var _a;
  if (!(it == null ? void 0 : it.url)) return;
  if (CURRENT_ALLOWED && !CURRENT_ALLOWED.has(it.id)) return;
  if (!isItemFeatureEnabled(it.id)) {
    try { toast("Este módulo está temporalmente deshabilitado por administración.", "warning"); } catch (_) {}
    return;
  }
  ACTIVE_ITEM_ID = it.id;
  try {
    const isNarrow = window.matchMedia && window.matchMedia("(max-width: 860px)").matches;
    if (isNarrow && (it.id === "leads_ver" || it.id === "leads_fil")) {
      let mobileURL = "/web/views/leads_mobile.html?v=20260728-month1";
      try {
        const u = new URL(it.url, location.origin);
        const openLead = u.searchParams.get("open_lead") || u.searchParams.get("id_lead") || u.searchParams.get("lead_id");
        const auditFocus = u.searchParams.get("audit_focus");
        const staleIds = u.searchParams.get("stale_ids");
        const stale = u.searchParams.get("stale");
        const params = new URLSearchParams();
        if (openLead) params.set("open_lead", openLead);
        if (auditFocus) params.set("audit_focus", auditFocus);
        if (stale) params.set("stale", stale);
        if (staleIds) params.set("stale_ids", staleIds);
        const qs = params.toString();
        if (qs) mobileURL += `&${qs}`;
      } catch (_) {
      }
      it = { ...it, url: mobileURL };
    }
  } catch (_) {
  }
  if (it.external) {
    window.open(it.url, "_blank", "noopener");
    return;
  }
  const frame = qs("#mainFrame");
  const cacheBust = !it.external;
  if (cacheBust) {
    if ((it.url || "").includes("#")) {
      const [base, hash] = it.url.split("#");
      frame.src = viewURL(`${base}${base.includes("?") ? "&" : "?"}v=${Date.now()}#${hash}`);
    } else {
      frame.src = viewURL(`${it.url}${it.url.includes("?") ? "&" : "?"}v=${Date.now()}`);
    }
  } else {
    frame.src = viewURL(it.url);
  }
  for (const b of document.querySelectorAll(".menu-item")) {
    b.classList.toggle("active", b.dataset.item === it.id);
  }
  const groupEl = findGroupByItemId(it.id);
  if (groupEl) {
    closeAllGroups();
    groupEl.classList.add("open");
  }
  if (!isSidebarPinned()) {
    collapseSidebarSoon();
  }
  try {
    if (document.body.classList.contains("sb-open")) {
      (_a = qs("#sidebar")) == null ? void 0 : _a.classList.remove("open-mobile");
      document.body.classList.remove("sb-open");
    }
  } catch (_) {
  }
}
function findGroupByItemId(itemId) {
  for (const g of document.querySelectorAll(".menu-group")) {
    if (g.querySelector(`.menu-item[data-item="${itemId}"]`)) return g;
  }
  return null;
}
function isSidebarPinned() {
  return localStorage.getItem("gd_sidebar_pinned") === "1";
}
function setSidebarPinned(v) {
  localStorage.setItem("gd_sidebar_pinned", v ? "1" : "0");
}
let collapseTimer = null;
function collapseSidebarSoon() {
  if (collapseTimer) clearTimeout(collapseTimer);
  collapseTimer = setTimeout(() => {
    if (!isSidebarPinned()) {
      qs("#sidebar").classList.add("collapsed");
      closeAllGroups();
    }
  }, 250);
}
function bindSidebarBehavior() {
  const sb = qs("#sidebar");
  const sideMenu = qs("#sideMenu");
  const sbInner = qs("#sidebar .sidebar-inner");
  const isMobile = () => window.matchMedia && window.matchMedia("(max-width: 980px)").matches;
  if (isSidebarPinned()) {
    sb.classList.remove("collapsed");
  } else {
    sb.classList.add("collapsed");
  }
  sb.addEventListener("mouseenter", () => {
    if (isMobile()) return;
    if (!isSidebarPinned()) {
      sb.classList.remove("collapsed");
      const g = findGroupByItemId(ACTIVE_ITEM_ID);
      if (g) g.classList.add("open");
    }
  });
  sb.addEventListener("mouseleave", () => {
    if (isMobile()) return;
    if (!isSidebarPinned()) {
      collapseSidebarSoon();
    }
  });
  qs("#btnSidebar").addEventListener("click", () => {
    if (isMobile()) {
      const open = sb.classList.toggle("open-mobile");
      document.body.classList.toggle("sb-open", open);
      if (open) {
        sb.classList.remove("collapsed");
        const g = findGroupByItemId(ACTIVE_ITEM_ID);
        if (g) g.classList.add("open");
      } else {
        closeAllGroups();
      }
      return;
    }
    // Desktop: el botón menú solo expande/colapsa. El pin vive en btnSidebarPinTop.
    const collapsed = sb.classList.contains("collapsed");
    if (collapsed) {
      sb.classList.remove("collapsed");
      const g = findGroupByItemId(ACTIVE_ITEM_ID);
      if (g) g.classList.add("open");
      return;
    }
    // Si el usuario colapsa manualmente, soltamos el pin (evita estados confusos).
    if (isSidebarPinned()) setSidebarPinned(false);
    sb.classList.add("collapsed");
    closeAllGroups();
  });
  document.addEventListener("click", (ev) => {
    if (!isMobile()) return;
    if (!document.body.classList.contains("sb-open")) return;
    if (ev.target.closest("#sidebar") || ev.target.closest("#btnSidebar")) return;
    sb.classList.remove("open-mobile");
    document.body.classList.remove("sb-open");
  }, { capture: true });

  // Fallback de scroll (rueda/trackpad) para evitar que el menú quede "clavado"
  // por CSS/overlays del navegador. Scroll real lo maneja `#sideMenu`.
  // Nota: este handler no debería ser necesario, pero en producción ha sido intermitente.
  if (sb && sideMenu) {
    try {
      if (!sideMenu.hasAttribute("tabindex")) sideMenu.setAttribute("tabindex", "0");
      // Asegura overflow (por si algún CSS viejo quedó cacheado).
      sideMenu.style.overflowY = "auto";
      sideMenu.style.overflowX = "hidden";
    } catch (_) {
    }
    sb.addEventListener(
      "wheel",
      (e) => {
        try {
          const t = e.target;
          // Si el usuario está scrolleando un panel interno (resultados búsqueda / menú usuario), no interceptar.
          if (t && t.closest && (t.closest(".sb-results") || t.closest(".user-menu"))) return;
          // Detecta target scrollable real (depende del CSS cargado/caché).
          const candidates = [sideMenu, sbInner, sb];
          let target = null;
          for (const c of candidates) {
            if (!c) continue;
            if ((c.scrollHeight || 0) > (c.clientHeight || 0) + 2) {
              target = c;
              break;
            }
          }
          if (!target) return;
          target.scrollTop += e.deltaY;
          // Evita que el wheel se vaya al iframe/viewport y "parezca" que no scrollea.
          e.preventDefault();
        } catch (_) {
        }
      },
      { passive: false }
    );

    // Extra: si el wheel cae directo en el <nav>, igual forzamos scroll.
    sideMenu.addEventListener(
      "wheel",
      (e) => {
        try {
          if ((sideMenu.scrollHeight || 0) <= (sideMenu.clientHeight || 0) + 2) {
            if (sb && (sb.scrollHeight || 0) > (sb.clientHeight || 0) + 2) {
              sb.scrollTop += e.deltaY;
              e.preventDefault();
            }
            return;
          }
          // Dejar scroll nativo del sideMenu si existe.
        } catch (_) {
        }
      },
      { passive: false }
    );
  }
}

function bindSidebarTools() {
  const sb = qs("#sidebar");
  const pinBtn = qs("#btnSidebarPinTop");
  const search = qs("#sideSearch");
  const results = qs("#sideSearchResults");
  if (!sb || !results) return;

  const refreshPinUI = () => {
    if (!pinBtn) return;
    const pinned = isSidebarPinned();
    pinBtn.setAttribute("aria-pressed", pinned ? "true" : "false");
    pinBtn.title = pinned ? "Menú fijado (click para soltar)" : "Fijar menú";
    try {
      pinBtn.textContent = pinned ? "🔒" : "🔓";
    } catch (_) {
    }
  };
  refreshPinUI();

  if (pinBtn) {
    pinBtn.addEventListener("click", () => {
      const pinned = isSidebarPinned();
      setSidebarPinned(!pinned);
      if (!pinned) {
        sb.classList.remove("collapsed");
        const g = findGroupByItemId(ACTIVE_ITEM_ID);
        if (g) g.classList.add("open");
      }
      refreshPinUI();
    });
  }

  const closeResults = () => {
    results.classList.remove("open");
    results.setAttribute("aria-hidden", "true");
    results.innerHTML = "";
  };

  const renderResults = (q) => {
    const query = String(q || "").trim().toLowerCase();
    if (!query) {
      closeResults();
      return;
    }
    const items = listVisibleMenuItems();
    const scored = [];
    for (const it of items) {
      const hay = `${it.label} ${it.groupTitle}`.toLowerCase();
      if (!hay.includes(query)) continue;
      const l = it.label.toLowerCase();
      let s = 0;
      if (l.startsWith(query)) s += 50;
      if (l.includes(query)) s += 20;
      if (it.groupTitle.toLowerCase().includes(query)) s += 5;
      scored.push({ s, it });
    }
    scored.sort((a, b) => b.s - a.s || a.it.label.localeCompare(b.it.label));
    const top = scored.slice(0, 14).map((x) => x.it);
    if (!top.length) {
      results.innerHTML = `<div style="padding:10px;color:var(--muted);font-weight:900">Sin resultados.</div>`;
      results.classList.add("open");
      results.setAttribute("aria-hidden", "false");
      return;
    }
    results.innerHTML = top.map((it) => {
      const on = isFav(it.id);
      return `
        <button type="button" class="sb-result" data-open="${it.id}">
          <span class="sb-fav ${on ? "on" : ""}" data-fav="${it.id}" title="${on ? "Quitar fijado" : "Fijar"}">★</span>
          <span class="meta">
            <span class="top">${escapeHtml(it.label)}</span>
            <span class="sub">${escapeHtml(it.groupTitle)}</span>
          </span>
        </button>
      `;
    }).join("");
    results.classList.add("open");
    results.setAttribute("aria-hidden", "false");

    results.querySelectorAll("[data-open]").forEach((b) => {
      b.addEventListener("click", (ev) => {
        const fav = ev.target && ev.target.closest && ev.target.closest("[data-fav]");
        if (fav) {
          ev.preventDefault();
          ev.stopPropagation();
          toggleFav(fav.getAttribute("data-fav"));
          buildMenu();
          renderResults(query);
          return;
        }
        const id = b.getAttribute("data-open");
        const it = findItemById(id);
        if (it) {
          sb.classList.remove("collapsed");
          const g = findGroupByItemId(it.id);
          if (g) g.classList.add("open");
          openItem(it);
        }
        closeResults();
        if (search) search.value = "";
      });
    });
  };

  if (search) {
    search.addEventListener("input", () => renderResults(search.value));
    search.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") {
        closeResults();
        search.value = "";
      } else if (ev.key === "Enter") {
        const first = results.querySelector(".sb-result[data-open]");
        if (first) {
          first.click();
          ev.preventDefault();
        }
      }
    });
  }

  document.addEventListener("click", (ev) => {
    if (ev.target.closest("#sidebarTools")) return;
    closeResults();
  }, { capture: true });
}
function bindTopbar() {
  var _a, _b, _c, _d;
  const helpBtn = qs("#btnHelp");
  helpBtn == null ? void 0 : helpBtn.addEventListener("click", () => {
    const frame = qs("#mainFrame");
    if (frame) frame.src = viewURL(`/web/views/ayuda.html?v=${Date.now()}`);
  });
  (_a = qs("#brandHome")) == null ? void 0 : _a.addEventListener("click", () => {
    if (CURRENT_ALLOWED && CURRENT_ALLOWED.has("dash_home")) {
      openItem({ id: "dash_home", url: "/web/views/dashboard.html?v=20260728-surveys1" });
    } else {
      const first = findFirstAllowedItem();
      if (first) openItem(first);
    }
  });

  // Weather modal (usado por la vista Tools → Clima; acá dejamos close handlers).
  const closeWx = () => {
    try {
      const m = qs("#wxModal");
      m == null ? void 0 : m.classList.remove("open");
      m == null ? void 0 : m.setAttribute("aria-hidden", "true");
    } catch (_) {
    }
  };
  (_b = qs("#wxClose")) == null ? void 0 : _b.addEventListener("click", closeWx);
  (_c = qs("#wxModalBg")) == null ? void 0 : _c.addEventListener("click", closeWx);

  // Topbar: Sistema dropdown (Backup/admin + actualizar + logout)
  const sysBtn = qs("#btnSystemTop");
  const sysMenu = qs("#systemMenuTop");
  if (sysBtn && sysMenu) {
    const openSys = () => {
      try {
        const rect = sysBtn.getBoundingClientRect();
        sysMenu.style.position = "fixed";
        sysMenu.style.top = rect.bottom + 8 + "px";
        sysMenu.style.right = "14px";
        sysMenu.style.left = "auto";
      } catch (_) {
      }
      sysMenu.classList.add("open");
      sysMenu.setAttribute("aria-hidden", "false");
    };
    const closeSys = () => {
      sysMenu.classList.remove("open");
      sysMenu.setAttribute("aria-hidden", "true");
    };
    sysBtn.addEventListener("click", (e) => {
      var _a2;
      (_a2 = e == null ? void 0 : e.stopPropagation) == null ? void 0 : _a2.call(e);
      if (sysMenu.classList.contains("open")) closeSys();
      else openSys();
    });
    document.addEventListener("click", (ev) => {
      if (ev.target.closest("#systemMenuTop") || ev.target.closest("#btnSystemTop")) return;
      closeSys();
    });
    window.addEventListener("blur", closeSys);
    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape" && sysMenu.classList.contains("open")) closeSys();
    });

    (_d = qs("#sysRefresh")) == null ? void 0 : _d.addEventListener("click", async () => {
      closeSys();
      refreshMainFrame();
      try {
        const data = await fetchNotifications();
        renderNotifications(data);
      } catch (_) {
      }
    });
    const sysLogout = qs("#sysLogout");
    sysLogout == null ? void 0 : sysLogout.addEventListener("click", async () => {
      closeSys();
      const ok = await Swal.fire({
        title: "Cerrar sesión",
        text: "¿Seguro que deseas cerrar sesión?",
        icon: "question",
        showCancelButton: true,
        confirmButtonText: "Sí, salir",
        cancelButtonText: "Cancelar",
        confirmButtonColor: "#19C37D"
      }).then((r) => r.isConfirmed);
      if (!ok) return;
      await performLogout();
    });
    const sysBackup = qs("#sysBackup");
    sysBackup == null ? void 0 : sysBackup.addEventListener("click", () => {
      closeSys();
      if (CURRENT_ROLE_ID !== 1) {
        toast("Backup: solo Admin", { kind: "warn" });
        return;
      }
      const frame = qs("#mainFrame");
      if (frame) frame.src = viewURL(`/web/views/backups.html?v=${Date.now()}`);
    });
  }
}
function initLetterGlitch(target, opts = {}) {
  if (!target) return;
  const {
    glitchColors = ["#2b4539", "#61dca3", "#61b3dc"],
    glitchSpeed = 70,
    smooth = true,
    characters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ!@#$&*()-_+=/[]{};:<>.,0123456789"
  } = opts;
  const wrap = document.createElement("div");
  wrap.className = "glitch-wrap";
  const canvas = document.createElement("canvas");
  canvas.className = "glitch-canvas";
  wrap.appendChild(canvas);
  target.appendChild(wrap);
  const ctx = canvas.getContext("2d");
  const letters = [];
  const grid = { columns: 0, rows: 0 };
  const fontSize = 14;
  const charWidth = 10;
  const charHeight = 18;
  const glyphs = Array.from(characters);
  let last = Date.now();
  let raf = null;
  const rand = (arr) => arr[Math.floor(Math.random() * arr.length)];
  const randChar = () => rand(glyphs);
  const randColor = () => rand(glitchColors);
  const hexToRgb = (hex) => {
    const h = hex.replace("#", "").trim();
    if (h.length !== 6) return null;
    return {
      r: parseInt(h.slice(0, 2), 16),
      g: parseInt(h.slice(2, 4), 16),
      b: parseInt(h.slice(4, 6), 16)
    };
  };
  const lerpColor = (a, b, f) => {
    const r = Math.round(a.r + (b.r - a.r) * f);
    const g = Math.round(a.g + (b.g - a.g) * f);
    const b2 = Math.round(a.b + (b.b - a.b) * f);
    return `rgb(${r},${g},${b2})`;
  };
  const calcGrid = (w, h) => ({ columns: Math.ceil(w / charWidth), rows: Math.ceil(h / charHeight) });
  const initLetters = (cols, rows) => {
    grid.columns = cols;
    grid.rows = rows;
    letters.length = cols * rows;
    for (let i = 0; i < letters.length; i++) {
      letters[i] = { char: randChar(), color: randColor(), target: randColor(), t: 1 };
    }
  };
  const resize = () => {
    const rect = target.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    canvas.style.width = rect.width + "px";
    canvas.style.height = rect.height + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const { columns, rows } = calcGrid(rect.width, rect.height);
    initLetters(columns, rows);
    draw();
  };
  const draw = () => {
    const rect = canvas.getBoundingClientRect();
    ctx.clearRect(0, 0, rect.width, rect.height);
    ctx.font = `${fontSize}px monospace`;
    ctx.textBaseline = "top";
    letters.forEach((l, i) => {
      const x = i % grid.columns * charWidth;
      const y = Math.floor(i / grid.columns) * charHeight;
      ctx.fillStyle = l.color;
      ctx.fillText(l.char, x, y);
    });
  };
  const update = () => {
    const count = Math.max(1, Math.floor(letters.length * 0.05));
    for (let i = 0; i < count; i++) {
      const idx = Math.floor(Math.random() * letters.length);
      const l = letters[idx];
      if (!l) continue;
      l.char = randChar();
      l.target = randColor();
      l.t = smooth ? 0 : 1;
      if (!smooth) l.color = l.target;
    }
  };
  const smoothStep = () => {
    let needs = false;
    for (const l of letters) {
      if (l.t < 1) {
        l.t = Math.min(1, l.t + 0.05);
        const a = hexToRgb(l.color) || hexToRgb("#2b4539");
        const b = hexToRgb(l.target) || hexToRgb("#61dca3");
        if (a && b) {
          l.color = lerpColor(a, b, l.t);
          needs = true;
        }
      }
    }
    if (needs) draw();
  };
  const animate = () => {
    const now = Date.now();
    if (now - last >= glitchSpeed) {
      update();
      draw();
      last = now;
    }
    if (smooth) smoothStep();
    raf = requestAnimationFrame(animate);
  };
  resize();
  animate();
  window.addEventListener("resize", () => {
    cancelAnimationFrame(raf);
    resize();
    animate();
  });
}
function findFirstAllowedItem() {
  const allowed = CURRENT_ALLOWED;
  for (const g of MENU) {
    for (const it of g.items) {
      if (it.sep || it.disabled || !it.url || !isItemFeatureEnabled(it.id)) continue;
      if (allowed && !allowed.has(it.id)) continue;
      return it;
    }
  }
  return null;
}
function openDefault() {
  if (ACTIVE_ITEM_ID) return;
  if (CURRENT_ROLE_ID === 6 || CURRENT_ROLE_ID === 7) {
    openItem({ id: "ops_portal", url: "/web/views/portal_ops.html?v=20260305-opsportal2" });
    return;
  }
  if (CURRENT_ALLOWED && CURRENT_ALLOWED.has("dash_home")) {
    openItem({ id: "dash_home", url: "/web/views/dashboard.html?v=20260728-surveys1" });
    return;
  }
  const first = findFirstAllowedItem();
  if (first) openItem(first);
}
(function init() {
  if (!requireAuth()) return;
  const boot = bootstrapFromToken();
  if (boot.redirected) return;
  setupIdleLogout();
  renderTopTools();
  bindSidebarBehavior();
  bindSidebarTools();
  bindTopbar();
  bindNotifications();
  startClock();
  openDefault();
  fetchMe().finally(() => {
    initThemeToggle();
    initUserMenu();
    // Solo Admin necesita el watcher de backups; y solo si hay job_id guardado.
    try {
      const jobId = localStorage.getItem("gd_backup_job_id") || "";
      if (jobId && (CURRENT_ROLE_ID === 1 || CURRENT_ROLE_ID === 12)) setupBackupJobWatch();
    } catch (_) {
    }
    setupChatWatch();
    openDefault();
    initLetterGlitch(document.querySelector(".topbar"), { glitchSpeed: 50 });
  });
  window.addEventListener("message", (ev) => {
    var _a, _b, _c, _d;
    if (((_a = ev.data) == null ? void 0 : _a.type) === "openItem" && ((_b = ev.data) == null ? void 0 : _b.url) && ((_c = ev.data) == null ? void 0 : _c.id)) {
      openItem({ id: ev.data.id, url: ev.data.url });
      return;
    }
    if ((ev == null ? void 0 : ev.data) && ev.data.type === "openLead") {
      try {
        const id = ev.data.id_lead || ev.data.id || ev.data.lead_id;
        const staleIds = Array.isArray(ev.data.stale_ids) ? ev.data.stale_ids : [];
        const params = new URLSearchParams();
        if (id) params.set("open_lead", String(id));
        if (ev.data.audit_focus) params.set("audit_focus", "1");
        if (staleIds.length) params.set("stale_ids", staleIds.map((x) => String(x)).filter(Boolean).join(","));
        openItem({ id: "leads_ver", label: "Ver Leads", url: `/web/views/leads.html?v=20260728-month1&${params.toString()}` });
      } catch (_) {
      }
      return;
    }
    if ((ev == null ? void 0 : ev.data) && ev.data.type === "toast") {
      const text = String(ev.data.text || ev.data.msg || "").trim();
      if (!text) return;
      const kind = String(ev.data.kind || "info");
      const ms = Number(ev.data.ms || 7e3);
      toast(text, { kind, ms });
      return;
    }
    if ((ev == null ? void 0 : ev.data) && ev.data.type === "sgjo_marked") {
      try {
        _hideSgjoGate();
      } catch (_) {
      }
      try {
        openDefault();
      } catch (_) {
      }
      return;
    }
    if (((_d = ev.data) == null ? void 0 : _d.type) === "logout") {
      localStorage.removeItem("token");
      localStorage.removeItem("nombre");
      sessionStorage.removeItem("token");
      sessionStorage.removeItem("nombre");
      location.href = `${API_BASE}/web/login.html`;
    }
  });

  // ---- Global poll: Correo ----
  // Muestra toast aunque el usuario esté en otra sección.
  let __giaInit = false;
  async function pollGiaEmail() {
    try {
      if (!getToken()) return;
      // Solo Admin (1) y Ejecutivos (2) ven GIA (correo/IG).
      // Además, requiere permiso explícito en el menú (para evitar que otros roles vean toasts).
      try{
        const allowed = CURRENT_ROLE_ID && PERMISSIONS[CURRENT_ROLE_ID] ? PERMISSIONS[CURRENT_ROLE_ID] : null;
        const can = (CURRENT_ROLE_ID === 1 || CURRENT_ROLE_ID === 12 || CURRENT_ROLE_ID === 2) && (!!allowed && allowed.has("tool_gmail"));
        if (!can) return;
      }catch(_){ return; }
      const lastMax = Number(localStorage.getItem("gd_gia_email_max_id") || "0") || 0;
      const lastOpen = Number(localStorage.getItem("gd_gia_email_open_total") || "0") || 0;
      const r = await fetch(`${API_BASE}/gia/email/summary`, { headers: authHeaders() });
      if (!r.ok) return;
      const j = await r.json();
      if (!j || j.ok !== true) return;
      if (!j.configured) return;
      const maxId = Number(j.max_id || 0) || 0;
      const openTotal = Number(j.open_total || 0) || 0;
      if (!__giaInit) {
        __giaInit = true;
        localStorage.setItem("gd_gia_email_max_id", String(maxId));
        localStorage.setItem("gd_gia_email_open_total", String(openTotal));
        return;
      }
      // Toast si llegó correo nuevo (maxId sube), o si sube la cola pendiente.
      const grew = maxId > lastMax;
      const grewOpen = openTotal > lastOpen;
      if (grew || grewOpen) {
        toast(`Correo: ${openTotal} pendiente(s)`, {
          kind: "ok",
          ms: 8e3,
	          onClick: () => openItem({ id: "tool_gmail", url: "/web/views/tools.html?v=20260806-video-ready-v1#correo" })
	        });
	      }
      localStorage.setItem("gd_gia_email_max_id", String(Math.max(lastMax, maxId)));
      localStorage.setItem("gd_gia_email_open_total", String(openTotal));
    } catch (_) {
    }
  }
  setInterval(() => {
    if (document.hidden) return;
    pollGiaEmail();
  }, 45e3);
})();
