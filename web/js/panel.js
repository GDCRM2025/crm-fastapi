const qs = (s, el = document) => el.querySelector(s);
let PREF_KEY = "gd_user_prefs";
function setPrefKey(username) {
  const key = (username || "default").toString().trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "_");
  PREF_KEY = `gd_prefs_${key}`;
}
const API_BASE = (() => {
  try {
    const h = String(location.hostname || "").toLowerCase();
    const isLocal = h === "localhost" || h === "127.0.0.1";
    return isLocal ? "" : "/crm";
  } catch (_) {
    return "";
  }
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
async function _serverLogout(reason = "manual") {
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
        body: JSON.stringify({ reason }),
        signal: ctrl.signal
      });
    } finally {
      clearTimeout(to);
    }
  } catch (_) {
  }
}
async function idleLogout() {
  await _serverLogout("idle");
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
  if (_chatWatchTimer) return;
  const onceOpts = { once: true, capture: true, passive: true };
  window.addEventListener("pointerdown", enableSoundOnce, onceOpts);
  window.addEventListener("keydown", enableSoundOnce, onceOpts);
  window.addEventListener("touchstart", enableSoundOnce, onceOpts);
  _chatWatchTimer = setInterval(() => {
    if (document.hidden) return;
    pollChatThreads();
  }, 5e3);
  pollChatThreads();
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
  const fontVal = p.font && FONT_MAP[p.font] ? FONT_MAP[p.font] : p.font;
  if (fontVal) {
    document.documentElement.style.setProperty("--font", fontVal);
  }
  if (p.accent) {
    document.documentElement.style.setProperty("--accent", p.accent);
  }
  if (p.text) {
    document.documentElement.style.setProperty("--text", p.text);
  }
  if (p.fontSize) {
    document.documentElement.style.setProperty("font-size", `${p.fontSize}px`);
  }
  if (p.theme) {
    applyTheme(p.theme);
  }
  try {
    const fr = qs("#mainFrame");
    (_a = fr == null ? void 0 : fr.contentWindow) == null ? void 0 : _a.postMessage({ type: "prefs", prefs: { ...p, font: fontVal } }, "*");
    const doc = (_b = fr == null ? void 0 : fr.contentDocument) == null ? void 0 : _b.documentElement;
    if (doc) {
      if (fontVal) doc.style.setProperty("--font", fontVal);
      if (p.accent) doc.style.setProperty("--accent", p.accent);
      if (p.text) doc.style.setProperty("--text", p.text);
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
      const fontCss = fontVal ? `--font:${fontVal};` : "";
      const accentCss = p.accent ? `--accent:${p.accent};` : "";
      const textCss = p.text ? `--text:${p.text};` : "";
      const sizeCss = p.fontSize ? `font-size:${p.fontSize}px;` : "";
      st.textContent = `
        :root{${fontCss}${accentCss}${textCss}${sizeCss}}
        body{font-family:var(--font) !important; color:var(--text) !important;}
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
    const r = await fetch(`${API_BASE}/notifications`, { headers: authHeaders() });
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
    if (role.includes("OPERADOR") || role.includes("CONDUCTOR") || role.includes("CHOFER")) return;
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
  const canSeeLeadAlerts = CURRENT_ROLE_ID === 1 || CURRENT_ROLE_ID === 2;
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
          onClick: () => openItem({ id: "leads_ver", label: "Ver Leads", url: "/web/views/leads.html" })
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
function openLeadsFromLock(openId, ids) {
  const frame = qs("#mainFrame");
  if (!frame) return;
  const idList = (ids || []).map((x) => String(x)).filter(Boolean);
  const params = new URLSearchParams();
  params.set("stale", "1");
  if (idList.length) params.set("stale_ids", idList.join(","));
  pendingLeadOpen = null;
  frame.src = viewURL(`/web/views/leads.html?${params.toString()}`);
}
function renderLeadLock(data) {
  var _a, _b;
  if (CURRENT_ROLE_ID !== 2) {
    document.body.classList.remove("lead-lock");
    if (leadLockEl) leadLockEl.style.display = "none";
    return;
  }
  const lock = !!(data == null ? void 0 : data.lock);
  const stale = (data == null ? void 0 : data.stale_leads) || {};
  const staleNew = stale.NUEVO || [];
  const staleContact = stale.CONTACTADO || [];
  const staleCot = stale.COTIZADO || [];
  const dayKey = (/* @__PURE__ */ new Date()).toISOString().slice(0, 10);
  const countKey = `gd_lock_unlock_count_${dayKey}`;
  const unlockUntil = Number(localStorage.getItem("gd_lock_unlock_until") || "0");
  const unlockCount = Number(localStorage.getItem(countKey) || "0");
  const now = Date.now();
  const isUnlocked = unlockUntil > now && unlockCount < 3;
  if (!leadLockEl) {
    leadLockEl = document.createElement("div");
    leadLockEl.id = "leadLock";
    leadLockEl.innerHTML = `
      <div class="lead-lock-card">
        <div class="lead-lock-title">Leads sin movimiento</div>
        <div class="lead-lock-sub">Debes trabajar estos leads antes de continuar.</div>
        <div class="lead-lock-list" id="leadLockList"></div>
        <div class="lead-lock-actions">
          <button class="btn" id="leadLockUnlock">Liberar 1h</button>
          <button class="btn" id="leadLockOpen">Ir a leads</button>
        </div>
      </div>
    `;
    document.body.appendChild(leadLockEl);
  }
  if (lock && !isUnlocked) {
    document.body.classList.add("lead-lock");
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
      unlockBtn.disabled = unlockCount >= 3;
      unlockBtn.textContent = unlockCount >= 3 ? "L\xEDmite diario" : "Liberar 1h";
      unlockBtn.onclick = () => {
        if (unlockCount >= 3) return;
        localStorage.setItem("gd_lock_unlock_until", String(Date.now() + 60 * 60 * 1e3));
        localStorage.setItem(countKey, String(unlockCount + 1));
        leadLockEl.style.display = "none";
        document.body.classList.remove("lead-lock");
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
  let delay = 5e3;
  const maxDelay = 12e4;
  const tick = async () => {
    const data = await fetchNotifications();
    if (data && data.ok) {
      delay = 5e3;
      renderNotifications(data);
      renderLeadLock(data);
    } else {
      delay = Math.min(maxDelay, Math.max(8e3, delay * 2));
    }
    setTimeout(tick, delay);
  };
  tick();
}
async function fetchMe() {
  var _a, _b, _c, _d, _e, _f, _g;
  try {
    if (!getToken()) {
      location.href = `${API_BASE}/web/login.html`;
      return;
    }
    const r = await fetch(`${API_BASE}/me`, { headers: authHeaders() });
    if (r.status === 401 || r.status === 403) {
      location.href = `${API_BASE}/web/login.html`;
      return;
    }
    if (!r.ok) throw new Error("me failed");
    const me = await r.json();
    window.GD.me = me;
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
    const roleName = (me.role || me.rol || "").toString().toUpperCase();
    CURRENT_ROLE_ID = ROLE_IDS[roleName] || null;
    try {
      const sysBackup = qs("#sysBackup");
      if (sysBackup) sysBackup.style.display = CURRENT_ROLE_ID === 1 ? "" : "none";
    } catch (_) {
    }
    const isOpsOnly = CURRENT_ROLE_ID === 6 || CURRENT_ROLE_ID === 7 || roleName.includes("OPERADOR") || roleName.includes("CONDUCTOR") || roleName.includes("CHOFER");
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
    buildMenu();
  } catch (_) {
    const userNameEl = qs("#userName");
    if (userNameEl) userNameEl.textContent = "Usuario";
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
      { id: "leads_ver", label: "Ver Leads", url: "/web/views/leads.html" },
      { id: "leads_fil", label: "Filtrar Leads", url: "/web/views/filtro_leads.html" }
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
      { id: "rep_funnel", label: "Funnel de ventas", url: "/web/views/reportes.html#funnel" },
      { id: "rep_cierre", label: "% de cierre", url: "/web/views/reportes.html#cierre" },
      { id: "rep_total", label: "Total venta", url: "/web/views/reportes.html#total" },
      { id: "rep_sep1", label: "\u2014", url: null, sep: true },
      { id: "rep_com", label: "Comunas m\xE1s vendidas", url: "/web/views/reportes.html#comunas" },
      { id: "rep_prod", label: "Productos m\xE1s vendidos", url: "/web/views/reportes.html#productos" },
      { id: "rep_cli", label: "Clientes m\xE1s frecuentes", url: "/web/views/reportes.html#clientes" },
      { id: "rep_sep2", label: "\u2014", url: null, sep: true },
      { id: "rep_hoy", label: "Leads creados hoy", url: "/web/views/reportes.html#leads_hoy" },
      { id: "rep_dia", label: "Venta diaria", url: "/web/views/reportes.html#venta_diaria" }
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
      { id: "op_ca_dev", label: "Devolucion de Camiones", url: "/web/views/op_camiones_devolucion.html?v=20260304-1" }
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
      { id: "evt", label: "Registrar Evento", url: "/web/views/finanzas_evento.html" },
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
      { id: "rrhh_nomina", label: "N\xF3mina", url: "/web/views/rrhh_nomina.html" },
      { id: "rrhh_staff", label: "Colaboradores", url: "/web/views/rrhh_colaboradores.html" },
      { id: "rrhh_solicitudes", label: "Solicitudes", url: "/web/views/rrhh_solicitudes.html" }
    ]
  },
  {
    id: "tools",
    ico: "\u{1F9F0}",
    title: "Tools",
    items: [
      { id: "tool_gmail", label: "Correo (GIA)", url: "/web/views/tools.html?v=20260317-tools2#correo" },
      { id: "tool_ig", label: "Instagram (GIA)", url: "/web/views/tools.html?v=20260317-tools2#instagram" },
      { id: "tool_wapp", label: "WhatsApp", url: "/web/views/tools.html?v=20260317-tools2#whatsapp" },
      { id: "tool_calc", label: "Calculadora", url: "/web/views/tools.html?v=20260317-tools2#calc" },
      { id: "tool_wx", label: "Clima (7 días)", url: "/web/views/tools.html?v=20260317-tools2#clima" }
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
      { id: "set_comi", label: "Comisiones", url: "/web/views/settings.html?entity=comisiones&v=20260218-6" },
      { id: "set_com", label: "Comunas", url: "/web/views/settings.html?entity=comunas&v=20260218-6" },
      { id: "set_tc", label: "Tipos Cliente", url: "/web/views/settings.html?entity=tipos_cliente&v=20260218-6" },
      { id: "set_el", label: "Estados Lead", url: "/web/views/settings.html?entity=estados_lead&v=20260218-6" },
      { id: "set_roles", label: "Roles", url: "/web/views/settings.html?entity=roles&v=20260218-6" },
      { id: "set_bak", label: "Backups", url: "/web/views/backups.html", noSidebar: true }
    ]
  }
];
let ACTIVE_ITEM_ID = null;
let CURRENT_ROLE_ID = null;
let CURRENT_ALLOWED = null;
const ROLE_IDS = {
  "ADMIN": 1,
  "SUPERADMIN": 1,
  "EJECUTIVO DE VENTAS": 2,
  "VENDEDOR": 2,
  "JEFE DE OPERACIONES": 3,
  "BODEGUERO": 4,
  "COMPRAS": 5,
  "CONDUCTOR": 6,
  "CONDUCTOR (CHOP)": 6,
  "CHOP": 6,
  "OPERADOR": 7,
  "MICE": 8,
  "FINANZAS": 9
};
const PERMISSIONS = {
  1: /* @__PURE__ */ new Set([
    "dash_home",
    "op_gps",
    "op_vruta",
    "op_cal",
    "op_menu_cam",
    "op_menu_gou",
    "op_menu_exp",
    "op_menu_del",
    "op_uni",
    "op_vruta2",
    "leads_ver",
    "leads_fil",
    "historial",
    "rep_funnel",
    "rep_cierre",
    "rep_total",
    "rep_com",
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
    "inv_cat",
    "inv_uni",
    "inv_prov",
    "inv_mov",
    "tool_gmail",
    "tool_wapp",
    "tool_ig",
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
    "rrhh_solicitudes",
    "set_users",
    "set_marcas",
    "set_prod",
    "set_comi",
    "set_com",
    "set_tc",
    "set_el",
    "set_roles",
    "set_bak"
  ]),
  2: /* @__PURE__ */ new Set([
    "dash_home",
    "leads_ver",
    "leads_fil",
    "historial",
    "rep_funnel",
    "rep_cierre",
    "rep_total",
    "rep_com",
    "rep_prod",
    "rep_cli",
    "rep_hoy",
    "rep_dia",
    "tool_gmail",
    "tool_wapp",
    "tool_ig",
    "tool_cal",
    "tool_calc",
    "tool_wx",
    "tools_hub",
    "vruta",
    "set_prod",
    "set_com",
    "set_el"
  ]),
  3: /* @__PURE__ */ new Set([
    "dash_home",
    "rep_com",
    "rep_prod",
    "rep_cli",
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
    "tool_gmail",
    "tool_wapp",
    "tool_ig",
    "tool_cal",
    "tool_calc",
    "tool_wx",
    "tools_hub",
    "gps",
    "vruta",
    "set_marcas",
    "set_prod",
    "set_com",
    "rrhh_staff",
    "rrhh_solicitudes"
  ]),
  4: /* @__PURE__ */ new Set([
    "dash_home",
    "rep_prod",
    "inv_tomar",
    "inv_stock",
    "inv_prod",
    "inv_cat",
    "inv_uni",
    "inv_prov",
    "inv_mov",
    "tool_gmail",
    "tool_wapp",
    "tool_ig",
    "tool_cal",
    "tool_calc",
    "tool_wx",
    "tools_hub",
    "vruta",
    "set_prod"
  ]),
  9: /* @__PURE__ */ new Set([
    "pl",
    "gast",
    "evt",
    "plan_cuentas",
    "historial"
  ]),
  5: /* @__PURE__ */ new Set([
    "dash_home",
    "rep_prod",
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
    "tool_gmail",
    "tool_wapp",
    "tool_ig",
    "tool_cal",
    "tool_calc",
    "tool_wx",
    "tools_hub",
    "vruta",
    "gast",
    "set_prod"
  ]),
  6: /* @__PURE__ */ new Set([
    "op_gps",
    "op_vruta",
    "op_cal",
    "op_menu_cam",
    "op_menu_gou",
    "op_menu_exp",
    "op_menu_del",
    "op_uni",
    "op_vruta2"
  ]),
  7: /* @__PURE__ */ new Set([
    "op_cal",
    "op_menu_cam",
    "op_menu_gou",
    "op_menu_exp",
    "op_menu_del",
    "op_uni",
    "op_vruta2"
  ]),
  8: /* @__PURE__ */ new Set([
    "dash_home",
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
    "tool_gmail",
    "tool_wapp",
    "tool_ig",
    "tool_cal",
    "tool_calc",
    "tool_wx",
    "tools_hub"
  ])
};
function buildMenu() {
  const nav = qs("#sideMenu");
  nav.innerHTML = "";
  const allowed = CURRENT_ROLE_ID && PERMISSIONS[CURRENT_ROLE_ID] ? PERMISSIONS[CURRENT_ROLE_ID] : null;
  CURRENT_ALLOWED = allowed;
  const isDriver = CURRENT_ROLE_ID === 6;
  const isAdmin = CURRENT_ROLE_ID === 1;
  let lastGroupId = null;
  for (const g of MENU) {
    const visibleItems = allowed ? g.items.filter((it) => !it.sep && !it.noSidebar && allowed.has(it.id) && (!it.driverOnly || isDriver)) : g.items.filter((it) => !it.sep && !it.noSidebar && (!it.driverOnly || isDriver));
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
      if (allowed && !allowed.has(it.id)) continue;
      if (it.driverOnly && !isDriver && !isAdmin) continue;
      const b = document.createElement("button");
      b.type = "button";
      b.className = "menu-item";
      b.dataset.item = it.id;
      b.title = it.label;
      b.innerHTML = `<span class="dot"></span><span class="lbl">${it.label}</span>`;
      b.addEventListener("click", () => openItem(it));
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
  ACTIVE_ITEM_ID = it.id;
  try {
    const isNarrow = window.matchMedia && window.matchMedia("(max-width: 860px)").matches;
    if (isNarrow && (it.id === "leads_ver" || it.id === "leads_fil")) {
      it = { ...it, url: "/web/views/leads_mobile.html" };
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
    const pinned = isSidebarPinned();
    setSidebarPinned(!pinned);
    if (!pinned) {
      sb.classList.remove("collapsed");
    } else {
      sb.classList.add("collapsed");
      closeAllGroups();
    }
  });
  document.addEventListener("click", (ev) => {
    if (!isMobile()) return;
    if (!document.body.classList.contains("sb-open")) return;
    if (ev.target.closest("#sidebar") || ev.target.closest("#btnSidebar")) return;
    sb.classList.remove("open-mobile");
    document.body.classList.remove("sb-open");
  }, { capture: true });
}
function bindTopbar() {
  var _a, _b, _c, _d;
  (_a = qs("#brandHome")) == null ? void 0 : _a.addEventListener("click", () => {
    if (CURRENT_ALLOWED && CURRENT_ALLOWED.has("dash_home")) {
      openItem({ id: "dash_home", url: "/web/views/dashboard.html" });
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
      if (it.sep || !it.url) continue;
      if (allowed && !allowed.has(it.id)) continue;
      return it;
    }
  }
  return null;
}
function openDefault() {
  if (CURRENT_ROLE_ID === 6 || CURRENT_ROLE_ID === 7) {
    openItem({ id: "ops_portal", url: "/web/views/portal_ops.html?v=20260305-opsportal2" });
    return;
  }
  if (CURRENT_ALLOWED && CURRENT_ALLOWED.has("dash_home")) {
    openItem({ id: "dash_home", url: "/web/views/dashboard.html" });
    return;
  }
  const first = findFirstAllowedItem();
  if (first) openItem(first);
}
(function init() {
  if (!requireAuth()) return;
  setupIdleLogout();
  renderTopTools();
  buildMenu();
  bindSidebarBehavior();
  bindTopbar();
  bindNotifications();
  startClock();
  fetchMe().finally(() => {
    initThemeToggle();
    initUserMenu();
    // Solo Admin necesita el watcher de backups; y solo si hay job_id guardado.
    try {
      const jobId = localStorage.getItem("gd_backup_job_id") || "";
      if (jobId && CURRENT_ROLE_ID === 1) setupBackupJobWatch();
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
    if (((_d = ev.data) == null ? void 0 : _d.type) === "logout") {
      localStorage.removeItem("token");
      localStorage.removeItem("nombre");
      sessionStorage.removeItem("token");
      sessionStorage.removeItem("nombre");
      location.href = `${API_BASE}/web/login.html`;
    }
  });
})();
