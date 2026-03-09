/* GREEN DIAMOND — Shell controller (menu + theme + auth + topbar) */

const qs = (s, el=document) => el.querySelector(s);
let PREF_KEY = "gd_user_prefs";
function setPrefKey(username){
  const key = (username || "default").toString().trim().toLowerCase().replace(/[^a-z0-9_-]+/g,"_");
  PREF_KEY = `gd_prefs_${key}`;
}

// En prod el CRM vive bajo /crm (Passenger). Algunas vistas se abren como /web/...
// por reglas del server; por eso NO dependemos de pathname para detectar.
const API_BASE = (() => {
  try{
    const h = String(location.hostname || "").toLowerCase();
    const isLocal = (h === "localhost" || h === "127.0.0.1");
    return isLocal ? "" : "/crm";
  }catch(_){
    return "";
  }
})();

// Convierte rutas de UI (/web/...) a la ruta real cuando el CRM está montado bajo /crm.
// Evita depender de reglas .htaccess y arregla 404 en descargas/vistas.
function viewURL(u){
  const s = String(u || "");
  if (!s) return s;
  if (/^https?:\/\//i.test(s)) return s;
  if (s.startsWith(API_BASE + "/")) return s; // ya viene prefijado
  if (API_BASE && s.startsWith("/web/")) return API_BASE + s;
  return s;
}

function getToken(){
  return (
    localStorage.getItem("token") ||
    localStorage.getItem("gd_token") ||
    sessionStorage.getItem("token") ||
    sessionStorage.getItem("gd_token") ||
    ""
  );
}
function authHeaders(extra={}){
  const t = getToken();
  return t ? { ...extra, Authorization: `Bearer ${t}` } : extra;
}
function requireAuth(){
  if (!getToken()){
    location.href = `${API_BASE}/web/login.html`;
    return false;
  }
  return true;
}

/* =========================
   IDLE AUTO-LOGOUT (1h)
========================= */
const IDLE_KEY = "gd_last_activity";
const IDLE_TIMEOUT_MS = 60 * 60 * 1000; // 1 hora

function _now(){ return Date.now(); }
function lastActivity(){
  try{ return Number(localStorage.getItem(IDLE_KEY) || "0") || 0; }catch(_){ return 0; }
}
function markActivity(){
  try{ localStorage.setItem(IDLE_KEY, String(_now())); }catch(_){}
}
function clearAuth(){
  try{
    localStorage.removeItem("token");
    localStorage.removeItem("gd_token");
    localStorage.removeItem("nombre");
    sessionStorage.removeItem("token");
    sessionStorage.removeItem("gd_token");
    sessionStorage.removeItem("nombre");
  }catch(_){}
}
async function _serverLogout(reason="manual"){
  // Best-effort: no rompemos el UX si falla.
  try{
    const t = getToken();
    if (!t) return;
    // No bloqueamos el logout del usuario si el servidor se demora (p.ej. email/digest PM).
    const ctrl = new AbortController();
    const to = setTimeout(() => { try{ ctrl.abort(); }catch(_){} }, 2500);
    try{
      await fetch(`${API_BASE}/auth/logout`, { method:"POST", headers: authHeaders({"Content-Type":"application/json"}), body: JSON.stringify({ reason }), signal: ctrl.signal });
    }finally{
      clearTimeout(to);
    }
  }catch(_){}
}
async function idleLogout(){
  await _serverLogout("idle");
  clearAuth();
  location.href = `${API_BASE}/web/login.html?reason=idle`;
}
function setupIdleLogout(){
  // Set inicial (por si viene vacío)
  if (!lastActivity()) markActivity();

  const evs = ["pointerdown","mousemove","keydown","scroll","touchstart","wheel"];
  const opts = { passive:true, capture:true };
  evs.forEach(e => window.addEventListener(e, markActivity, opts));
  window.addEventListener("focus", markActivity);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) markActivity(); });

  // Bridge activity desde iframes (main + chat) para que moverse adentro cuente como actividad.
  const bridge = (sel) => {
    const fr = qs(sel);
    if (!fr) return;
    fr.addEventListener("load", () => {
      try{
        const doc = fr.contentDocument;
        if (!doc || !doc.addEventListener) return;
        const on = () => markActivity();
        evs.forEach(e => doc.addEventListener(e, on, opts));
      }catch(_){}
    });
  };
  bridge("#mainFrame");
  bridge("#chatFrame");

  setInterval(() => {
    if (!getToken()) return;
    const la = lastActivity();
    if (la && (_now() - la) > IDLE_TIMEOUT_MS){
      idleLogout();
    }
  }, 25_000);
}

/* =========================
   TOAST + BACKUP JOB WATCH
========================= */
function ensureToastHost(){
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
function toast(text, { kind="info", ms=7000, onClick=null } = {}){
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
  if (onClick){
    el.addEventListener("click", () => { try{ onClick(); }catch(_){}; });
  }
  host.appendChild(el);
  setTimeout(() => { try{ el.remove(); }catch(_){} }, ms);
}

let _backupWatchTimer = null;
async function pollBackupJob(){
  const jobId = localStorage.getItem("gd_backup_job_id") || "";
  if (!jobId) return;
  try{
    const r = await fetch(`${API_BASE}/backups/jobs/${encodeURIComponent(jobId)}`, { headers: authHeaders() });
    if (!r.ok) return;
    const j = await r.json();
    const job = j?.job || {};
    if (job.status === "done"){
      localStorage.removeItem("gd_backup_job_id");
      toast(`Backup creado: ${job.backup_id || jobId}`, {
        kind:"ok",
        onClick: () => {
          const bid = job.backup_id || jobId;
          qs("#mainFrame").src = viewURL(`/web/views/backups.html?select=${encodeURIComponent(bid)}`);
        }
      });
    } else if (job.status === "error"){
      localStorage.removeItem("gd_backup_job_id");
      toast(`Error creando backup: ${(job.error||"").slice(0,140) || "revisa servidor"}`, { kind:"err" });
    }
  }catch(_){}
}
function setupBackupJobWatch(){
  if (_backupWatchTimer) return;
  _backupWatchTimer = setInterval(pollBackupJob, 4000);
  pollBackupJob();
}

// expone token para iframes (settings)
window.GD = window.GD || {};
window.GD.getToken = getToken;
window.GD.authHeaders = authHeaders;

/* =========================
   CHAT WATCH (sonido + popups)
========================= */
let _chatWatchTimer = null;
function setupChatWatch(){
  if (_chatWatchTimer) return;
  // Necesitamos una "user gesture" para habilitar AudioContext.
  const onceOpts = { once:true, capture:true, passive:true };
  window.addEventListener("pointerdown", enableSoundOnce, onceOpts);
  window.addEventListener("keydown", enableSoundOnce, onceOpts);
  window.addEventListener("touchstart", enableSoundOnce, onceOpts);

  _chatWatchTimer = setInterval(() => {
    if (document.hidden) return;
    pollChatThreads();
  }, 5000);
  pollChatThreads();

  // Launcher tipo Gmail (abajo-derecha). El chat interno NO debería depender del topbar.
  _ensureChatLauncher();
}

let _chatLauncher = null;
function _ensureChatLauncher(){
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
  b.innerHTML = `<div class="b">💬</div>`;
  b.addEventListener("click", () => {
    enableSoundOnce();
    try{ openChatThread(""); }catch(_){}
  });
  document.body.appendChild(b);
  _chatLauncher = b;
  return b;
}

/* =========================
   THEME
========================= */
function getTheme(){
  // Preferimos gd_theme. Si no existe, soportamos legacy THEME=day|night
  const v = (localStorage.getItem("gd_theme") || "").trim();
  if (v === "light" || v === "dark") return v;
  const legacy = (localStorage.getItem("THEME") || "").trim().toLowerCase();
  if (legacy === "day") return "light";
  if (legacy === "night") return "dark";
  return "dark";
}
function setTheme(mode){
  const m = (mode === "light") ? "light" : "dark";
  // Persistimos en ambos formatos para que ninguna vista “se salga” del tema.
  localStorage.setItem("gd_theme", m);
  localStorage.setItem("THEME", m === "light" ? "day" : "night");
  try{
    const p = getPrefs();
    p.theme = m;
    savePrefs(p);
  }catch(_){}
  applyTheme(m);
}
function applyTheme(mode){
  const m = (mode === "light") ? "light" : "dark";
  document.documentElement.classList.toggle("light", m === "light");
  document.documentElement.setAttribute("data-theme", m === "light" ? "day" : "night");

  // Propaga a iframe (si el view escucha postMessage)
  const fr = qs("#mainFrame");
  try{
    fr?.contentWindow?.postMessage({ type:"theme", mode: m }, "*");
  }catch(_){}
}

/* =========================
   USER PREFS
========================= */
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
  mono: '"JetBrains Mono","SFMono-Regular","Menlo","Monaco","Consolas","Liberation Mono","Courier New", monospace',
};
function getPrefs(){
  try{
    return JSON.parse(localStorage.getItem(PREF_KEY) || "{}");
  }catch(_){
    return {};
  }
}
function savePrefs(p){
  localStorage.setItem(PREF_KEY, JSON.stringify(p || {}));
}
function applyPrefs(p){
  if (!p) return;
  const fontVal = (p.font && FONT_MAP[p.font]) ? FONT_MAP[p.font] : p.font;
  if (fontVal){
    document.documentElement.style.setProperty("--font", fontVal);
  }
  if (p.accent){
    document.documentElement.style.setProperty("--accent", p.accent);
  }
  if (p.text){
    document.documentElement.style.setProperty("--text", p.text);
  }
  if (p.fontSize){
    document.documentElement.style.setProperty("font-size", `${p.fontSize}px`);
  }
  if (p.theme){
    applyTheme(p.theme);
  }

  // Propaga a iframe
  try{
    const fr = qs("#mainFrame");
    fr?.contentWindow?.postMessage({ type:"prefs", prefs: { ...p, font: fontVal } }, "*");
    const doc = fr?.contentDocument?.documentElement;
    if (doc){
      if (fontVal) doc.style.setProperty("--font", fontVal);
      if (p.accent) doc.style.setProperty("--accent", p.accent);
      if (p.text) doc.style.setProperty("--text", p.text);
      if (p.fontSize) doc.style.setProperty("font-size", `${p.fontSize}px`);
      if (p.theme) doc.classList.toggle("light", p.theme === "light");
    }
    const iframeDoc = fr?.contentDocument;
    if (iframeDoc){
      let st = iframeDoc.getElementById("gd-pref-style");
      if (!st){
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
  }catch(_){}
}
function initThemeToggle(){
  const tgl = qs("#themeToggle");
  if (!tgl) return;
  const p = getPrefs();
  const mode = (p.theme || getTheme());
  tgl.checked = (mode === "light");
  // setTheme() alinea gd_theme + THEME + prefs.theme para evitar que “se cambie solo”
  setTheme(mode);

  tgl.addEventListener("change", () => {
    const m = tgl.checked ? "light" : "dark";
    setTheme(m);
  });

  // al cargar cualquier view, re-propaga el tema
  qs("#mainFrame").addEventListener("load", () => {
    applyTheme(getTheme());
    applyPrefs(getPrefs());

    // Importante: si el usuario solo interactúa dentro del iframe, el parent no recibe "click".
    // Puenteamos gestos desde el iframe para habilitar audio ("Nuevo lead").
    try{
      const fr = qs("#mainFrame");
      const doc = fr?.contentDocument;
      if (doc && doc.addEventListener){
        const onGesture = () => enableSoundOnce();
        doc.addEventListener("pointerdown", onGesture, { once:true, capture:true });
        doc.addEventListener("keydown", onGesture, { once:true, capture:true });
      }
    }catch(_){}

    if (pendingLeadOpen){
      try{
        const frame = qs("#mainFrame");
        frame?.contentWindow?.postMessage({ type:"openLead", id: pendingLeadOpen.id, stale_ids: pendingLeadOpen.ids }, "*");
      }catch(_){}
      pendingLeadOpen = null;
    }
  });
}

/* =========================
   CLOCK
========================= */
function startClock(){
  const el = qs("#clockBox");
  if (!el) return;
  const fmt = new Intl.DateTimeFormat("es-CL", {
    year:"numeric", month:"2-digit", day:"2-digit",
    hour:"2-digit", minute:"2-digit"
  });
  const tick = () => { el.textContent = fmt.format(new Date()); };
  tick();
  setInterval(tick, 30_000);
}

/* =========================
   NOTIFICATIONS
========================= */
async function fetchNotifications(){
  try{
    if (!getToken()) return { ok:false, total:0, items:[] };
    const r = await fetch(`${API_BASE}/notifications`, { headers: authHeaders() });
    if (r.status === 401 || r.status === 403){
      // Evita spam de errores cada 5s si se pierde el token.
      location.href = `${API_BASE}/web/login.html`;
      return { ok:false, total:0, items:[] };
    }
    if (!r.ok) throw new Error("notifications failed");
    return await r.json();
  }catch(_){
    return { ok:false, total:0, items:[] };
  }
}

let lastNotifCount = null;
let lastLeadCount = null;
let lastStaleCount = null;
let lastSystemCount = null;
let canSound = false;
let lastSoundAt = 0;
let seenNotifCount = Number(localStorage.getItem("gd_notif_seen") || "0");

function enableSoundOnce(){
  // Se llama cuando detectamos una "user gesture" (click/touch/teclado).
  if (canSound) return;
  canSound = true;
  try{
    const Ctx = (window.AudioContext || window.webkitAudioContext);
    if (Ctx){
      const ctx = new Ctx();
      ctx.close?.();
    }
  }catch(_){}
}

function playNotifSound(kind="default"){
  if (!canSound) return;
  const now = Date.now();
  if (now - lastSoundAt < 1200) return;
  lastSoundAt = now;

  // Voz "Nuevo lead" cuando sea posible
  if ((kind === "lead" || kind === "event") && "speechSynthesis" in window){
    try{
      const u = new SpeechSynthesisUtterance(kind === "event" ? "Nuevo evento" : "Nuevo lead");
      u.lang = "es-CL";
      u.rate = 1.05;
      u.pitch = 1.0;
      window.speechSynthesis.cancel();
      window.speechSynthesis.speak(u);
      return;
    }catch(_){}
  }

  // fallback: beep más notorio
  try{
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    gain.gain.value = 0.06;
    osc.connect(gain);
    gain.connect(ctx.destination);
    if (kind === "chat"){
      // beep corto doble, más "mensaje"
      osc.frequency.value = 880;
      osc.start();
      setTimeout(() => { osc.frequency.value = 660; }, 90);
      setTimeout(() => { gain.gain.value = 0.0; }, 140);
      setTimeout(() => { gain.gain.value = 0.06; osc.frequency.value = 990; }, 200);
      setTimeout(() => { osc.frequency.value = 740; }, 270);
      setTimeout(() => {
        osc.stop();
        ctx.close();
      }, 340);
    } else if (kind === "event"){
      osc.frequency.value = 523;
      osc.start();
      setTimeout(() => { osc.frequency.value = 784; }, 120);
      setTimeout(() => { osc.frequency.value = 659; }, 220);
      setTimeout(() => {
        osc.stop();
        ctx.close();
      }, 360);
    } else {
      osc.frequency.value = 740;
      osc.start();
      setTimeout(() => { osc.frequency.value = 990; }, 120);
      setTimeout(() => {
        osc.stop();
        ctx.close();
      }, 240);
    }
  }catch(_){}
}

/* =========================
   CHAT NOTIFY (popups + sonido)
========================= */
let _chatInitSeen = false;
let _chatSeen = {};
try{ _chatSeen = JSON.parse(localStorage.getItem("gd_chat_seen") || "{}") || {}; }catch(_){ _chatSeen = {}; }
let _chatDock = null;

function _ensureChatDock(){
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
      height: min(520px, calc(100vh - 120px));
      border:1px solid rgba(148,163,184,.22);
      background: rgba(2,6,23,.84);
      backdrop-filter: blur(14px);
      border-radius: 14px;
      box-shadow: 0 18px 70px rgba(0,0,0,.45);
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
      background: rgba(2,6,23,.22);
      color: rgba(226,232,240,.9);
      border-radius: 10px;
      padding:4px 8px;
      font-weight:1100;
      cursor:pointer;
    }
    .chat-win .preview{ padding:10px; }
    .chat-win .msg{
      color: rgba(226,232,240,.86);
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
      background: rgba(2,6,23,.22);
      color: rgba(226,232,240,.92);
      border-radius: 12px;
      padding:8px 10px;
      font-weight:1100;
      cursor:pointer;
    }
    .chat-win .btn.primary{
      background: color-mix(in srgb, var(--accent) 22%, transparent);
      border-color: color-mix(in srgb, var(--accent) 50%, transparent);
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

function openChatThread(threadId){
  enableSoundOnce();
  try{
    const chatModal = qs("#chatModal");
    const fr = qs("#chatFrame") || chatModal?.querySelector("iframe");
    if (fr){
      fr.src = viewURL(`/web/views/chat.html?thread=${encodeURIComponent(String(threadId || ""))}&v=${Date.now()}`);
    }
    chatModal?.classList.add("open");
    chatModal?.setAttribute("aria-hidden","false");
  }catch(_){}
}

function openChatWindow(tid, title){
  const dock = _ensureChatDock();
  let el = dock.querySelector(`.chat-win[data-tid="${tid}"]`);
  if (el) return el;

  el = document.createElement("div");
  el.className = "chat-win";
  el.dataset.tid = String(tid);
  // iframe src se setea lazy (al abrir)
  el.innerHTML = `
    <div class="h">
      <div class="ttl"></div>
      <div class="hbtns">
        <button class="hb" type="button" data-open-full="1" title="Abrir en Chat">↗</button>
        <button class="hb" type="button" data-toggle="1" title="Minimizar / abrir">▢</button>
        <button class="hb" type="button" data-close="1" title="Cerrar">✕</button>
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

  const close = () => { try{ el.remove(); }catch(_){} };
  const toggle = () => {
    enableSoundOnce();
    el.classList.toggle("open");
    el.classList.remove("attn");
    const fr = el.querySelector("iframe");
    if (el.classList.contains("open") && fr && !fr.getAttribute("src")){
      const src = fr.dataset.src || "";
      if (src) fr.setAttribute("src", src);
    }
  };

  el.querySelectorAll("[data-close]").forEach(b => b.addEventListener("click", close));
  el.querySelectorAll("[data-toggle]").forEach(b => b.addEventListener("click", toggle));
  el.querySelectorAll("[data-open-full]").forEach(b => b.addEventListener("click", () => openChatThread(tid)));

  dock.appendChild(el);
  return el;
}

function showChatPopup(th){
  const dock = _ensureChatDock();
  const tid = Number(th?.id_thread || 0);
  if (!tid) return;
  const title = String(th?.title || "Chat");
  const msg = String(th?.last_msg || "");
  const el = openChatWindow(tid, title);
  const ttlEl = el.querySelector(".ttl");
  if (ttlEl) ttlEl.textContent = title;
  const msgEl = el.querySelector(".msg");
  if (msgEl) msgEl.textContent = msg || "(sin texto)";
  // Set iframe lazy src (embed)
  const fr = el.querySelector("iframe");
  if (fr){
    const src = viewURL(`/web/views/chat.html?embed=1&thread=${encodeURIComponent(String(tid))}&v=${Date.now()}`);
    fr.dataset.src = src;
    // Para experiencia tipo Gmail: abrimos la ventanita y cargamos el chat de una.
    if (!fr.getAttribute("src")) fr.setAttribute("src", src);
    // Best-effort: enfocar el input dentro del iframe (same-origin).
    fr.addEventListener("load", () => {
      try{
        const w = fr.contentWindow;
        const d = w && w.document;
        const inp = d && d.querySelector && d.querySelector("#msg");
        if (inp && typeof inp.focus === "function") inp.focus();
      }catch(_){}
    }, { once: true });
  }
  // Si está cerrado/minimizado, lo abrimos y marcamos atención.
  if (!el.classList.contains("open")) el.classList.add("open");
  el.classList.remove("attn");
}

async function pollChatThreads(){
  try{
    if (!getToken()) return;
    const me = window.GD?.me || {};
    const role = String(me?.role || me?.rol || "").toUpperCase();
    if (role.includes("OPERADOR") || role.includes("CONDUCTOR") || role.includes("CHOFER")) return;

    const r = await fetch(`${API_BASE}/chat/threads?limit=40`, { headers: authHeaders() });
    if (!r.ok) return;
    const j = await r.json();
    const items = Array.isArray(j?.items) ? j.items : [];

    // baseline: primera corrida no notifica
    if (!_chatInitSeen){
      for (const it of items){
        const tid = Number(it?.id_thread || 0);
        const mid = Number(it?.last_message_id || 0);
        if (tid && mid) _chatSeen[String(tid)] = mid;
      }
      _chatInitSeen = true;
      try{ localStorage.setItem("gd_chat_seen", JSON.stringify(_chatSeen)); }catch(_){}
      return;
    }

    const meEmail = String(me?.email || "").toLowerCase();
    let changed = false;
    for (const it of items){
      const tid = Number(it?.id_thread || 0);
      const mid = Number(it?.last_message_id || 0);
      if (!tid || !mid) continue;
      const prev = Number(_chatSeen[String(tid)] || 0);
      if (mid > prev){
        _chatSeen[String(tid)] = mid;
        changed = true;

        const senderEmail = String(it?.last_sender_email || "").toLowerCase();
        if (!meEmail || (senderEmail && senderEmail !== meEmail)){
          playNotifSound("chat");
          showChatPopup(it);
        }
      }
    }
    if (changed){
      try{ localStorage.setItem("gd_chat_seen", JSON.stringify(_chatSeen)); }catch(_){}
    }
  }catch(_){}
}

function renderNotifications(data){
  const badge = qs("#notifBadge");
  const menu = qs("#notifMenu");
  const btn = qs("#btnNotifs");
  const canSeeLeadAlerts = (CURRENT_ROLE_ID === 1 || CURRENT_ROLE_ID === 2);
  let items = Array.isArray(data?.items) ? data.items.slice() : [];
  if (!canSeeLeadAlerts){
    items = items.filter(it => !["leads_nuevos","leads_sin_mov"].includes(it.key));
  }
  const total = items.reduce((acc, it) => acc + Number(it?.count || 0), 0);
  if (badge){
    badge.style.display = "flex";
    badge.setAttribute("data-count", String(total));
    badge.textContent = String(total);
    badge.style.opacity = (total > seenNotifCount) ? "1" : ".6";
  }
  if (btn){
    btn.title = `Notificaciones (${total})`;
  }

  if (!menu) return;
  menu.innerHTML = "";
  const totalTxt = `<div class="notif-empty" style="font-weight:900;margin-bottom:6px">Resumen (${total})</div>`;
  menu.innerHTML = totalTxt;
  if (!items.length){
    menu.innerHTML += `<div class="notif-empty">Sin notificaciones.</div>`;
    return;
  }
  for (const it of items){
    const div = document.createElement("div");
    div.className = "notif-item";
    const url = it.url || "/web/views/leads.html";
    div.innerHTML = `
      <div class="notif-title">${it.title || "Notificación"}</div>
      <div class="notif-count">${it.count || 0}</div>
    `;
    div.addEventListener("click", () => {
      if (url){
        const frame = qs("#mainFrame");
        frame.src = viewURL(url);
      }
      menu.classList.remove("open");
    });
    menu.appendChild(div);
  }

  if (canSeeLeadAlerts){
    const staleObj = data?.stale_leads || {};
    const staleAll = Array.isArray(staleObj) ? staleObj : [
      ...(staleObj.NUEVO || []),
      ...(staleObj.CONTACTADO || []),
      ...(staleObj.COTIZADO || []),
    ];
    if (staleAll.length){
      const sep = document.createElement("div");
      sep.className = "notif-empty";
      sep.style.fontWeight = "900";
      sep.textContent = "Leads sin movimiento";
      menu.appendChild(sep);
      staleAll.slice(0, 8).forEach(l => {
        const row = document.createElement("div");
        row.className = "notif-item";
        row.dataset.lead = l.id_lead || "";
        row.innerHTML = `
          <div class="notif-title">${l.cliente || "Lead"}</div>
          <div class="notif-count">${l.created_at || ""}</div>
        `;
        row.addEventListener("click", () => {
          const id = row.dataset.lead;
          const ids = staleAll.map(x => x.id_lead).filter(Boolean);
          openLeadsFromLock(id, ids);
          menu.classList.remove("open");
        });
        menu.appendChild(row);
      });
      if (staleAll.length > 8){
        const more = document.createElement("div");
        more.className = "notif-item";
        more.innerHTML = `
          <div class="notif-title">Ver todos los leads sin movimiento</div>
          <div class="notif-count">${staleAll.length}</div>
        `;
        more.addEventListener("click", () => {
          openLeadsFromLock(staleAll[0]?.id_lead, staleAll.map(x => x.id_lead).filter(Boolean));
          menu.classList.remove("open");
        });
        menu.appendChild(more);
      }
    }
  }

  // Sonidos:
  // - Ejecutivos: leads/stale
  // - Operaciones/Admin: eventos agendados (system_notifs)
  {
    const leadItem = (items || []).find(x => x.key === "leads_nuevos");
    const leadCount = Number(leadItem?.count || 0);
    const staleItem = (items || []).find(x => x.key === "leads_sin_mov");
    const staleCount = Number(staleItem?.count || 0);
    const sysItem = (items || []).find(x => x.key === "system_notifs");
    const sysCount = Number(sysItem?.count || 0);

    // Evento agendado: beep/voz para roles no-ventas.
    if (CURRENT_ROLE_ID !== 2){
      if (lastSystemCount !== null && sysCount > lastSystemCount){
        playNotifSound("event");
      }
      lastSystemCount = sysCount;
    }

    if (lastLeadCount !== null && leadCount > lastLeadCount){
      playNotifSound("lead");
    } else if (staleCount > 0 && (lastStaleCount === null || staleCount > lastStaleCount)){
      playNotifSound("lead");
    } else if (lastNotifCount !== null && total > lastNotifCount){
      playNotifSound();
    }
    lastLeadCount = leadCount;
    lastStaleCount = staleCount;
  }
  lastNotifCount = total;
}

let leadLockEl = null;
let pendingLeadOpen = null;
function openLeadsFromLock(openId, ids){
  const frame = qs("#mainFrame");
  if (!frame) return;
  const idList = (ids || []).map(x => String(x)).filter(Boolean);
  const params = new URLSearchParams();
  params.set("stale", "1");
  if (idList.length) params.set("stale_ids", idList.join(","));
  // Importante: NO abrimos un lead automáticamente. Solo filtramos la vista.
  pendingLeadOpen = null;
  frame.src = viewURL(`/web/views/leads.html?${params.toString()}`);
}
function renderLeadLock(data){
  if (CURRENT_ROLE_ID !== 2){
    document.body.classList.remove("lead-lock");
    if (leadLockEl) leadLockEl.style.display = "none";
    return;
  }
  const lock = !!data?.lock;
  const stale = data?.stale_leads || {};
  const staleNew = stale.NUEVO || [];
  const staleContact = stale.CONTACTADO || [];
  const staleCot = stale.COTIZADO || [];
  const dayKey = new Date().toISOString().slice(0,10);
  const countKey = `gd_lock_unlock_count_${dayKey}`;
  const unlockUntil = Number(localStorage.getItem("gd_lock_unlock_until") || "0");
  const unlockCount = Number(localStorage.getItem(countKey) || "0");
  const now = Date.now();
  const isUnlocked = unlockUntil > now && unlockCount < 3;
  if (!leadLockEl){
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
  if (lock && !isUnlocked){
    document.body.classList.add("lead-lock");
    const list = leadLockEl.querySelector("#leadLockList");
    if (list){
      const allIds = [
        ...staleNew.map(x => x.id_lead),
        ...staleContact.map(x => x.id_lead),
      ].filter(Boolean);
      const block = (arr, label) => {
        if (!arr || !arr.length) return "";
        return `
          <div class="lead-lock-group">${label}</div>
          ${arr.map(l => `
            <div class="lead-lock-item" data-lead="${l.id_lead}">
              <div class="lead-lock-name">${l.cliente || "Lead"}</div>
              <div class="lead-lock-date">${l.created_at || ""}</div>
            </div>
          `).join("")}
        `;
      };
      list.innerHTML = (
        block(staleNew, "NUEVO (1–7 días)") +
        block(staleContact, "CONTACTADO (+3 días)") +
        (staleCot && staleCot.length ? `<div class="lead-lock-group">COTIZADO (+2 días) · Solo alerta</div>` : "")
      ) || `<div class="lead-lock-empty">Sin detalle disponible.</div>`;
      list.querySelectorAll("[data-lead]").forEach(el => {
        el.addEventListener("click", () => {
          // Al click, vamos a la lista filtrada completa (sin auto abrir el 1er lead).
          openLeadsFromLock(null, allIds);
        });
      });
    }
    const btn = leadLockEl.querySelector("#leadLockOpen");
    if (btn){
      const first = staleNew[0]?.id_lead || staleContact[0]?.id_lead || null;
      btn.onclick = () => {
        openLeadsFromLock(first, [
          ...staleNew.map(x => x.id_lead),
          ...staleContact.map(x => x.id_lead),
        ]);
      };
    }
    const unlockBtn = leadLockEl.querySelector("#leadLockUnlock");
    if (unlockBtn){
      unlockBtn.disabled = unlockCount >= 3;
      unlockBtn.textContent = unlockCount >= 3 ? "Límite diario" : "Liberar 1h";
      unlockBtn.onclick = () => {
        if (unlockCount >= 3) return;
        localStorage.setItem("gd_lock_unlock_until", String(Date.now() + 60*60*1000));
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

function bindNotifications(){
  const btn = qs("#btnNotifs");
  const menu = qs("#notifMenu");
  if (!btn || !menu) return;

  const openMenu = async () => {
    const data = await fetchNotifications();
    renderNotifications(data);
    menu.classList.add("open");
    menu.setAttribute("aria-hidden", "false");
    // mark as seen
    const total = Number(data?.total || 0);
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
  menu.addEventListener("mouseenter", () => { if (closeTimer) clearTimeout(closeTimer); });
  menu.addEventListener("mouseleave", scheduleClose);

  document.addEventListener("click", (ev) => {
    if (!menu.classList.contains("open")) return;
    if (ev.target.closest("#notifMenu") || ev.target.closest("#btnNotifs")) return;
    closeMenu();
  });

  // habilitar sonido con cualquier gesto del usuario (no solo click)
  document.addEventListener("click", enableSoundOnce, { once:true, capture:true });
  document.addEventListener("pointerdown", enableSoundOnce, { once:true, capture:true });
  document.addEventListener("keydown", enableSoundOnce, { once:true, capture:true });

  // refresh badge periodically (con backoff para evitar spam de errores si /notifications falla)
  let delay = 5000;
  const maxDelay = 120000;
  const tick = async () => {
    const data = await fetchNotifications();
    if (data && data.ok){
      delay = 5000;
      renderNotifications(data);
      renderLeadLock(data);
    }else{
      delay = Math.min(maxDelay, Math.max(8000, delay * 2));
    }
    setTimeout(tick, delay);
  };
  tick();
}

/* =========================
   USER
========================= */
async function fetchMe(){
  try{
    if (!getToken()) { location.href = `${API_BASE}/web/login.html`; return; }
    const r = await fetch(`${API_BASE}/me`, { headers: authHeaders() });
    if (r.status === 401 || r.status === 403){
      location.href = `${API_BASE}/web/login.html`;
      return;
    }
    if (!r.ok) throw new Error("me failed");
    const me = await r.json();
    window.GD.me = me;
    const keyRaw = (me.id ?? me.user?.id ?? me.username ?? me.email ?? me.nombre ?? me.name ?? "default");
    setPrefKey(keyRaw);
    const name = me.username || me.nombre || me.name || "Usuario";
    const initials = (name || "U").split(" ").map(s => s[0]).join("").slice(0,2).toUpperCase();
    const userNameEl = qs("#userName");
    const avatarEl = qs("#userAvatar");
    if (userNameEl) userNameEl.textContent = name;
    if (avatarEl){
      const prefs = getPrefs();
      const avatar = prefs.photoData || prefs.photo || me.avatar_url || "";
      if (avatar){
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
    const isOpsOnly =
      (CURRENT_ROLE_ID === 6 || CURRENT_ROLE_ID === 7) ||
      roleName.includes("OPERADOR") ||
      roleName.includes("CONDUCTOR") ||
      roleName.includes("CHOFER");
    if (isOpsOnly){
      // Operadores/Conductores NO deben entrar al panel completo (sidebar).
      // El portal es horizontal y muestra el calendario embebido a pantalla completa.
      try{
        const here = String(location.pathname || "");
        if (!here.includes("/web/views/portal_ops.html")){
          location.replace(`${API_BASE}/web/views/portal_ops.html?v=20260305-opsportal2`);
          return;
        }
      }catch(_){}
      try{ document.body.classList.add("ops-mode"); }catch(_){}
      const btnNotifs = qs("#btnNotifs");
      const btnGpt = qs("#btnGpt");
      const btnChat = qs("#btnChatTop");
      if (btnNotifs) btnNotifs.style.display = "none";
      if (btnGpt) btnGpt.style.display = "none";
      if (btnChat) btnChat.style.display = "none";
    }
    buildMenu();
    // Tools fue removido del menú para mantener el CRM enfocado.
    // Dejamos esta llamada como no-op para no romper despliegues antiguos.
    try{ renderTopTools?.(); }catch(_){}
  }catch(_){
    const userNameEl = qs("#userName");
    if (userNameEl) userNameEl.textContent = "Usuario";
  }
}

// (Tools removido) accesos rápidos y vista Tools se eliminan para mantener el CRM enfocado.
// Mantenemos un no-op para compatibilidad por si algún HTML antiguo lo llama.
function renderTopTools(){ /* no-op */ }

function findItemById(itemId){
  for (const g of MENU){
    for (const it of (g.items || [])){
      if (it.id === itemId) return it;
    }
  }
  return null;
}

function initUserMenu(){
  const btn = qs("#btnUserMenu");
  const menu = qs("#userMenu");
  if (!btn || !menu) return;

  // Asegura que el menú quede por encima del iframe
  if (menu.parentElement !== document.body){
    document.body.appendChild(menu);
  }

  const prefs = getPrefs();
  applyPrefs(prefs);

  const me = window.GD?.me || {};
  const roleName = String(me.role || me.rol || "").toLowerCase();
  const isAdmin = roleName === "admin" || CURRENT_ROLE_ID === (ROLE_IDS["ADMIN"] || 1);

  // Gate "Backups" UI: admin only (dangerous).
  const backupsBtn = qs('[data-open="set_bak"]', menu);
  if (backupsBtn) backupsBtn.style.display = isAdmin ? "" : "none";

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
    const fontVal = (fontKey && FONT_MAP[fontKey]) ? FONT_MAP[fontKey] : fontKey;
    preview.style.setProperty("--accent", p.accent || "#19c37d");
    preview.style.setProperty("--text", p.text || "#e2e8f0");
    preview.style.fontFamily = fontVal || "";
    if (previewText){
      previewText.style.fontFamily = fontVal || "";
      previewText.textContent = `Fuente actual: ${fontKey} — 012345`;
    }
  };
  updatePreview();

  const setModalPreview = (next) => {
    const fontKey = next.font || "system";
    const fontVal = (fontKey && FONT_MAP[fontKey]) ? FONT_MAP[fontKey] : fontKey;
    if (previewModal){
      previewModal.style.setProperty("--accent", next.accent || "#19c37d");
      previewModal.style.setProperty("--text", next.text || "#e2e8f0");
      previewModal.style.fontFamily = fontVal || "";
    }
    if (previewModalText){
      previewModalText.style.fontFamily = fontVal || "";
      previewModalText.textContent = `Fuente nueva: ${fontKey} — 012345`;
    }
  };

  const userNameEl = qs("#userName");
  const avatarEl = qs("#userAvatar");
  if (prefs.name && userNameEl) userNameEl.textContent = prefs.name;
  if ((prefs.photoData || prefs.photo || me.avatar_url) && avatarEl){
    avatarEl.textContent = "";
    avatarEl.style.backgroundImage = `url('${prefs.photoData || prefs.photo || me.avatar_url}')`;
    avatarEl.style.backgroundSize = "cover";
    avatarEl.style.backgroundPosition = "center";
  }

  // photo modal
  const openPhoto = () => {
    if (!photoModal) return;
    if (photoPreview){
      const src = photoDataTmp || photoEl?.value;
      if (src){
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
    photoModal.setAttribute("aria-hidden","false");
  };
  const closePhoto = () => {
    if (!photoModal) return;
    photoModal.classList.remove("open");
    photoModal.setAttribute("aria-hidden","true");
  };
  photoBtn?.addEventListener("click", openPhoto);
  photoModalBg?.addEventListener("click", closePhoto);
  photoCancel?.addEventListener("click", closePhoto);
  photoEl?.addEventListener("input", () => {
    if (!photoPreview) return;
    if (photoEl.value){
      photoPreview.textContent = "";
      photoPreview.style.backgroundImage = `url('${photoEl.value}')`;
      photoPreview.style.backgroundSize = "cover";
      photoPreview.style.backgroundPosition = "center";
    } else {
      photoPreview.textContent = "Sin foto";
      photoPreview.style.backgroundImage = "";
    }
  });
  photoFileEl?.addEventListener("change", async () => {
    const file = photoFileEl.files && photoFileEl.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      photoDataTmp = reader.result || "";
      if (photoPreview){
        photoPreview.textContent = "";
        photoPreview.style.backgroundImage = `url('${photoDataTmp}')`;
        photoPreview.style.backgroundSize = "cover";
        photoPreview.style.backgroundPosition = "center";
      }
    };
    reader.readAsDataURL(file);
  });
  photoApply?.addEventListener("click", () => {
    const p = getPrefs();
    if (photoDataTmp){
      p.photoData = photoDataTmp;
      p.photo = "";
    } else {
      p.photo = photoEl?.value || "";
      p.photoData = "";
    }
    savePrefs(p);
    applyPrefs(p);
    if (avatarEl){
      if (p.photoData || p.photo){
        avatarEl.textContent = "";
        avatarEl.style.backgroundImage = `url('${p.photoData || p.photo}')`;
        avatarEl.style.backgroundSize = "cover";
        avatarEl.style.backgroundPosition = "center";
      } else if (p.name){
        avatarEl.textContent = p.name.split(" ").map(s => s[0]).join("").slice(0,2).toUpperCase();
        avatarEl.style.backgroundImage = "";
      }
    }
    closePhoto();
  });

  const openUserMenu = () => {
    // Mantiene el toggle de tema sincronizado al abrir (se había perdido la propagación).
    try{
      const p0 = getPrefs();
      if (tgl) tgl.checked = ((p0.theme || getTheme()) === "light");
    }catch(_){}

    try{
      const rect = btn.getBoundingClientRect();
      menu.style.top = (rect.bottom + 8) + "px";
      menu.style.right = "14px";
      menu.style.left = "auto";
      menu.style.position = "fixed";
    }catch(_){}
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
    e?.stopPropagation?.();
    if (menu.classList.contains("open")) closeUserMenu();
    else openUserMenu();
  };
  btn.addEventListener("click", toggleUserMenu);
  document.addEventListener("click", (ev) => {
    if (ev.target.closest("#userMenu") || ev.target.closest("#btnUserMenu")) return;
    closeUserMenu();
  });

  // Si pierde foco (cambio de pestaña/ventana), cerramos automáticamente.
  window.addEventListener("blur", closeUserMenu);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) closeUserMenu();
  });
  // Si el foco de teclado se va fuera del menú, también cerramos.
  document.addEventListener("focusin", (ev) => {
    if (!menu.classList.contains("open")) return;
    if (ev.target.closest("#userMenu") || ev.target.closest("#btnUserMenu")) return;
    closeUserMenu();
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && menu.classList.contains("open")) closeUserMenu();
  });

  // Tabs (Perfil / Personalización / Sistema)
  const TAB_KEY = "gd_user_menu_tab";
  const tabBtns = Array.from(menu.querySelectorAll("[data-um-tab-btn]"));
  const tabSecs = Array.from(menu.querySelectorAll("[data-um-tab]"));
  const setTab = (tab) => {
    for (const b of tabBtns){
      const on = (b.getAttribute("data-um-tab-btn") === tab);
      b.classList.toggle("active", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    }
    for (const s of tabSecs){
      const on = (s.getAttribute("data-um-tab") === tab);
      s.classList.toggle("active", on);
    }
    try{ localStorage.setItem(TAB_KEY, tab); }catch(_){}
  };
  const initialTab = (() => {
    try{ return localStorage.getItem(TAB_KEY) || "perfil"; }catch(_){ return "perfil"; }
  })();
  setTab(initialTab);
  tabBtns.forEach(b => b.addEventListener("click", () => setTab(b.getAttribute("data-um-tab-btn") || "perfil")));
  window.GD = window.GD || {};
  window.GD.openUserMenu = openUserMenu;
  window.GD.closeUserMenu = closeUserMenu;

  if (chatModal){
    const openChat = () => {
      enableSoundOnce();
      // Recarga iframe para evitar cache/estado viejo (y para tomar la última versión de chat.html)
      try{
        const fr = qs("#chatFrame") || chatModal.querySelector("iframe");
        if (fr){
          fr.src = viewURL(`/web/views/chat.html?v=${Date.now()}`);
        }
      }catch(_){}
      chatModal.classList.add("open");
      chatModal.setAttribute("aria-hidden","false");
    };
    const closeChat = () => {
      chatModal.classList.remove("open");
      chatModal.setAttribute("aria-hidden","true");
    };
    chatBtn?.addEventListener("click", openChat);
    chatModalBg?.addEventListener("click", closeChat);
    chatClose?.addEventListener("click", closeChat);
    const topChat = qs("#btnChatTop");
    topChat?.addEventListener("click", openChat);
  }

  if (btnGpt){
    btnGpt.addEventListener("click", () => {
      window.open("https://chat.openai.com/", "_blank", "noopener");
    });
  }

  qs("#prefSave")?.addEventListener("click", () => {
    const previous = getPrefs();
    const next = { ...previous };
    next.name = nameEl?.value || next.name;
    next.email = emailEl?.value || next.email;
    next.phone = phoneEl?.value || next.phone;
    if (photoDataTmp){
      next.photoData = photoDataTmp;
      next.photo = "";
    } else {
      next.photo = photoEl?.value || next.photo || "";
      next.photoData = next.photoData || "";
    }
    if (fontEl) next.font = fontEl.value;
    if (fontSizeEl) next.fontSize = Number(fontSizeEl.value || 13) || 13;
    if (accentEl) next.accent = accentEl.value;
    if (textEl) next.text = textEl.value;
    if (tgl) next.theme = tgl.checked ? "light" : "dark";

    if (prefModal){
      setModalPreview(next);
      prefModal.classList.add("open");
      prefModal.setAttribute("aria-hidden","false");
      prefModal.style.display = "block";
      prefModal.style.zIndex = "10000";
      const closePreview = () => {
        prefModal.classList.remove("open");
        prefModal.setAttribute("aria-hidden","true");
        prefModal.style.display = "";
      };
      const revert = () => {
        applyPrefs(previous);
        updatePreview();
        if (userNameEl) userNameEl.textContent = previous.name || (me.username || me.nombre || me.name || "Usuario");
        const av = qs("#userAvatar");
        if (av){
          if (previous.photoData || previous.photo){
            av.textContent = "";
            av.style.backgroundImage = `url('${previous.photoData || previous.photo}')`;
            av.style.backgroundSize = "cover";
            av.style.backgroundPosition = "center";
          } else {
            av.style.backgroundImage = "";
            av.textContent = (previous.name || userNameEl?.textContent || "U").split(" ").map(s=>s[0]).join("").slice(0,2).toUpperCase();
          }
        }
      };
      prefCancel?.addEventListener("click", () => { revert(); closePreview(); }, { once:true });
      prefModalBg?.addEventListener("click", () => { revert(); closePreview(); }, { once:true });
      prefApply?.addEventListener("click", () => {
        savePrefs(next);
        applyPrefs(next);
        updatePreview();
        const userNameEl = qs("#userName");
        if (userNameEl && next.name) userNameEl.textContent = next.name;
        const av = qs("#userAvatar");
        if (av && (next.photoData || next.photo)){
          av.textContent = "";
          av.style.backgroundImage = `url('${next.photoData || next.photo}')`;
          av.style.backgroundSize = "cover";
          av.style.backgroundPosition = "center";
        }
        closePreview();
      }, { once:true });
    } else {
      savePrefs(next);
      applyPrefs(next);
      updatePreview();
    }
  });

  // Cambios se aplican en previsualización al guardar

  menu.addEventListener("click", (ev) => {
    const target = ev.target.closest("[data-open]");
    if (!target) return;
    const id = target.dataset.open;
    const it = findItemById(id);
    if (it) openItem(it);
  });
}

/* =========================
   MENU (SIN HOME)
========================= */
const MENU = [
  {
    id:"leads",
    ico:"📌",
    title:"Leads",
    items:[
      { id:"leads_ver",   label:"Ver Leads",    url:"/web/views/leads.html" },
      { id:"leads_fil",   label:"Filtrar Leads",url:"/web/views/filtro_leads.html" },
    ]
},
{
  id:"cotizador",
  ico:"🧾",
  title:"Cotizador",
  items:[
    { id:"historial", label:"Historial", url:"/web/views/historial_cotizaciones.html" },
  ]
},

  {
    id:"reportes",
    ico:"📊",
    title:"Reportes",
    items:[
      { id:"rep_funnel", label:"Funnel de ventas",        url:"/web/views/reportes.html#funnel" },
      { id:"rep_cierre", label:"% de cierre",            url:"/web/views/reportes.html#cierre" },
      { id:"rep_total",  label:"Total venta",            url:"/web/views/reportes.html#total" },

      { id:"rep_sep1",   label:"—",                       url:null, sep:true },

      { id:"rep_com",    label:"Comunas más vendidas",    url:"/web/views/reportes.html#comunas" },
      { id:"rep_prod",   label:"Productos más vendidos",  url:"/web/views/reportes.html#productos" },
      { id:"rep_cli",    label:"Clientes más frecuentes", url:"/web/views/reportes.html#clientes" },

      { id:"rep_sep2",   label:"—",                       url:null, sep:true },

      { id:"rep_hoy",    label:"Leads creados hoy",       url:"/web/views/reportes.html#leads_hoy" },
      { id:"rep_dia",    label:"Venta diaria",            url:"/web/views/reportes.html#venta_diaria" },
    ]
  },
	  {
	    id:"operaciones",
	    ico:"🛠️",
	    title:"Operaciones",
	    items:[
	      { id:"op_rec",    label:"Recetas",              url:"/web/views/operaciones_recetas.html?v=20260304-1" },
	      { id:"op_mice",   label:"Mice and Place",       url:"/web/views/operaciones_mice.html" },
	      { id:"op_sep1",   label:"—",                    url:null, sep:true },
	      { id:"op_ruta",   label:"Ruta",                 url:"/web/views/ruta.html" },
	      { id:"op_sep2",   label:"—",                    url:null, sep:true },
	      { id:"op_ma_cat", label:"Categorias Maquinaria", url:"/web/views/op_maquinaria_categorias.html?v=20260304-1" },
	      { id:"op_ma_inv", label:"Inventario Maquinaria", url:"/web/views/op_maquinaria_inventario.html?v=20260304-1" },
	      { id:"op_ma_ficha", label:"Ficha Maquinaria",    url:"/web/views/op_maquinaria_ficha.html?v=20260304-1" },
	      { id:"op_sep3",   label:"—",                    url:null, sep:true },
	      { id:"op_ca_ficha", label:"Ficha Camiones",     url:"/web/views/op_camiones_ficha.html?v=20260304-1" },
	      { id:"op_ca_ent", label:"Entrega de Camiones",  url:"/web/views/op_camiones_entrega.html?v=20260304-1" },
	      { id:"op_ca_dev", label:"Devolucion de Camiones", url:"/web/views/op_camiones_devolucion.html?v=20260304-1" },
	    ]
	  },
  {
    id:"inventario",
    ico:"📦",
    title:"Inventario",
    items:[
      { id:"inv_tomar", label:"Tomar inventario",          url:"/web/views/inventario_mercancia.html#tomar" },
      { id:"inv_sep1",  label:"—",                         url:null, sep:true },
      { id:"inv_stock", label:"Stock ingredientes",        url:"/web/views/inventario_mercancia.html#stock" },
      { id:"inv_cat",   label:"Categorías",                url:"/web/views/inventario_mercancia.html#categorias" },
      { id:"inv_uni",   label:"Unidades",                  url:"/web/views/inventario_mercancia.html#unidades" },
      { id:"inv_prov",  label:"Proveedores",               url:"/web/views/inventario_mercancia.html#proveedores" },
      { id:"inv_sep2",  label:"—",                         url:null, sep:true },
      { id:"inv_mov",   label:"Movimiento de Inventario",  url:"/web/views/inventario_mercancia.html#movimientos" },
    ]
  },
  {
    id:"finanzas",
    ico:"💰",
    title:"Finanzas",
    items:[
      { id:"pl",    label:"P&L",            url:"/web/views/finanzas_pl.html" },
      { id:"gast",  label:"Cargar Gastos",  url:"/web/views/finanzas_gastos.html" },
      { id:"evt",   label:"Registrar Evento",url:"/web/views/finanzas_evento.html" },
      { id:"plan_cuentas", label:"Plan de Cuentas", url:"/web/views/finanzas_pl.html#plan" },
    ]
  },
  {
    id:"operadores",
    ico:"🧑‍🍳",
    title:"Operadores/CHOPS",
    items:[
      { id:"op_gps",      label:"Conectar GPS",    url:"/web/views/conductores_gps.html", driverOnly:true },
      { id:"op_vruta",    label:"Ver ruta (actual)", url:"/web/views/conductores_ruta.html", driverOnly:true },
      { id:"op_sep0",     label:"—",               url:null, sep:true },
      // Operaciones: usar Google Calendar embebido (el calendario interno confundía y no mostraba los detalles).
      { id:"op_cal",      label:"Calendario",      url:"/web/views/calendar.html?v=20260305-gcal1" },
      { id:"op_sep1",     label:"—",               url:null, sep:true },
      { id:"op_menu_cam", label:"Menu Camaleón",   url:"/web/views/operadores.html?only=menu&brand=CAMALEON&v=20260305-m1#recetas" },
      { id:"op_menu_gou", label:"Menu Gourmet",    url:"/web/views/operadores.html?only=menu&brand=GOURMET&v=20260305-m1#recetas" },
      { id:"op_menu_exp", label:"Menu Express",    url:"/web/views/operadores.html?only=menu&brand=EXPRESS&v=20260305-m1#recetas" },
      { id:"op_menu_del", label:"Menu Del Sabor",  url:"/web/views/operadores.html?only=menu&brand=DEL%20SABOR&v=20260305-m1#recetas" },
      { id:"op_sep2",     label:"—",               url:null, sep:true },
      { id:"op_uni",      label:"Universidad GD",  url:"/web/views/operadores.html?only=uni&v=20260305-m1#videos" },
      { id:"op_sep3",     label:"—",               url:null, sep:true },
      { id:"op_vruta2",   label:"Ver Ruta (próx.)", url:"/web/views/operadores_ruta_futura.html" },
    ]
  },
  {
    id:"rrhh",
    ico:"👥",
    title:"RRHH",
    items:[
      { id:"rrhh_nomina", label:"Nómina", url:"/web/views/rrhh_nomina.html" },
      { id:"rrhh_staff", label:"Colaboradores", url:"/web/views/rrhh_colaboradores.html" },
      { id:"rrhh_solicitudes", label:"Solicitudes", url:"/web/views/rrhh_solicitudes.html" },
    ]
  },
	  {
	    id:"settings",
	    ico:"⚙️",
	    title:"Settings",
	    items:[
      { id:"set_users",  label:"Usuarios",       url:"/web/views/settings.html?entity=usuarios&v=20260218-6" },
      { id:"set_marcas", label:"Marcas",         url:"/web/views/settings.html?entity=marcas&v=20260218-6" },
      { id:"set_prod",   label:"Productos (venta)",      url:"/web/views/settings.html?entity=productos&v=20260218-6" },
      { id:"set_comi",   label:"Comisiones",     url:"/web/views/settings.html?entity=comisiones&v=20260218-6" },
      { id:"set_com",    label:"Comunas",        url:"/web/views/settings.html?entity=comunas&v=20260218-6" },
      { id:"set_tc",     label:"Tipos Cliente",  url:"/web/views/settings.html?entity=tipos_cliente&v=20260218-6" },
      { id:"set_el",     label:"Estados Lead",   url:"/web/views/settings.html?entity=estados_lead&v=20260218-6" },
      { id:"set_roles",  label:"Roles",          url:"/web/views/settings.html?entity=roles&v=20260218-6" },
	      // Backups es "peligroso": lo mostramos desde el menú de Usuario → Sistema, no en la sidebar.
	      { id:"set_bak",    label:"Backups",        url:"/web/views/backups.html", noSidebar:true },
      // (Tools removido) La extensión se descarga desde Usuario → Sistema.
	    ]
	  },
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
};

const PERMISSIONS = {
  1: new Set([
    "dash_home",
    "op_gps","op_vruta","op_cal",
    "op_menu_cam","op_menu_gou","op_menu_exp","op_menu_del",
    "op_uni","op_vruta2",
    "leads_ver","leads_fil","historial",
    "rep_funnel","rep_cierre","rep_total","rep_com","rep_prod","rep_cli","rep_hoy","rep_dia",
    "op_rec","op_mice","op_ruta",
    "op_ma_cat","op_ma_inv","op_ma_ficha",
    "op_ca_ficha","op_ca_ent","op_ca_dev",
    "inv_tomar","inv_stock","inv_prod","inv_cat","inv_uni","inv_prov","inv_mov",
    "tool_gmail","tool_wapp","tool_ig","tool_cal","tool_calc",
    "gps","vruta",
    "pl","gast","evt","plan_cuentas",
    "rrhh_nomina","rrhh_staff","rrhh_solicitudes",
    "set_users","set_marcas","set_prod","set_comi","set_com","set_tc","set_el","set_roles",
    "set_bak",
  ]),
  2: new Set([
    "dash_home",
    "leads_ver","leads_fil","historial",
    "rep_funnel","rep_cierre","rep_total","rep_com","rep_prod","rep_cli","rep_hoy","rep_dia",
    "tool_gmail","tool_wapp","tool_ig","tool_cal","tool_calc",
    "vruta",
    "set_prod","set_com","set_el",
  ]),
  3: new Set([
    "dash_home",
    "rep_com","rep_prod","rep_cli",
    "op_rec","op_mice","op_ruta",
    "op_ma_cat","op_ma_inv","op_ma_ficha",
    "op_ca_ficha","op_ca_ent","op_ca_dev",
    "inv_tomar","inv_stock","inv_prod","inv_cat","inv_uni","inv_prov","inv_mov",
    "tool_gmail","tool_wapp","tool_ig","tool_cal","tool_calc",
    "gps","vruta",
    "set_marcas","set_prod","set_com",
    "rrhh_staff","rrhh_solicitudes",
  ]),
  4: new Set([
    "dash_home",
    "rep_prod",
    "inv_tomar","inv_stock","inv_prod","inv_cat","inv_uni","inv_prov","inv_mov",
    "tool_gmail","tool_wapp","tool_ig","tool_cal","tool_calc",
    "vruta",
    "set_prod",
  ]),
  5: new Set([
    "dash_home",
    "rep_prod",
    "op_rec","op_mice","op_ruta",
    "op_ma_cat","op_ma_inv","op_ma_ficha",
    "op_ca_ficha","op_ca_ent","op_ca_dev",
    "inv_tomar","inv_stock","inv_prod","inv_cat","inv_uni","inv_prov","inv_mov",
    "tool_gmail","tool_wapp","tool_ig","tool_cal","tool_calc",
    "vruta",
    "gast",
    "set_prod",
  ]),
  6: new Set([
    "op_gps","op_vruta","op_cal",
    "op_menu_cam","op_menu_gou","op_menu_exp","op_menu_del",
    "op_uni","op_vruta2",
  ]),
  7: new Set([
    "op_cal",
    "op_menu_cam","op_menu_gou","op_menu_exp","op_menu_del",
    "op_uni","op_vruta2",
  ]),
  8: new Set([
    "dash_home",
    "op_rec","op_mice","op_ruta",
    "inv_tomar","inv_stock","inv_prod","inv_cat","inv_uni","inv_prov","inv_mov",
    "tool_gmail","tool_wapp","tool_ig","tool_cal","tool_calc",
  ]),
};

function buildMenu(){
  const nav = qs("#sideMenu");
  nav.innerHTML = "";
  const allowed = (CURRENT_ROLE_ID && PERMISSIONS[CURRENT_ROLE_ID]) ? PERMISSIONS[CURRENT_ROLE_ID] : null;
  CURRENT_ALLOWED = allowed;
  const isDriver = CURRENT_ROLE_ID === 6;
  const isAdmin = CURRENT_ROLE_ID === 1;
  let lastGroupId = null;

  for (const g of MENU){
    const visibleItems = allowed
      ? g.items.filter(it => !it.sep && !it.noSidebar && allowed.has(it.id) && (!it.driverOnly || isDriver))
      : g.items.filter(it => !it.sep && !it.noSidebar && (!it.driverOnly || isDriver));
    if (!visibleItems.length) continue;
    if (lastGroupId === "operadores" && (g.id === "rrhh" || g.id === "settings")){
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
    head.title = g.title; // en collapsed ayuda
    head.innerHTML = `
      <span class="menu-ico">${g.ico}</span>
      <span class="menu-title">${g.title}</span>
      <span class="menu-chevron">›</span>
    `;

    const items = document.createElement("div");
    items.className = "menu-items";

    for (const it of g.items){
      if (it.sep){
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
      b.title = it.label; // tooltip en collapsed
      b.innerHTML = `<span class="dot"></span><span class="lbl">${it.label}</span>`;
      b.addEventListener("click", () => openItem(it));
      items.appendChild(b);
    }

    head.addEventListener("click", () => {
      // toggle group
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

function closeAllGroups(){
  for (const el of document.querySelectorAll(".menu-group.open")){
    el.classList.remove("open");
  }
}

async function openItem(it){
  if (!it?.url) return;
  if (CURRENT_ALLOWED && !CURRENT_ALLOWED.has(it.id)) return;

  ACTIVE_ITEM_ID = it.id;

  // Mobile UX: en pantallas chicas la vista "Leads" tradicional se vuelve impracticable.
  // Abrimos una lista móvil (acciones rápidas) y dejamos "Vista completa" dentro de esa vista.
  try{
    const isNarrow = window.matchMedia && window.matchMedia("(max-width: 860px)").matches;
    if (isNarrow && (it.id === "leads_ver" || it.id === "leads_fil")){
      it = { ...it, url: "/web/views/leads_mobile.html" };
    }
  }catch(_){}

  if (it.external){
    window.open(it.url, "_blank", "noopener");
    return;
  }

  // set iframe
  const frame = qs("#mainFrame");
  const isDash = it.id === "dash_home" || (it.url || "").includes("dashboard.html");
  const cacheBust = !it.external;
  if (cacheBust){
    if ((it.url || "").includes("#")){
      const [base, hash] = it.url.split("#");
      frame.src = viewURL(`${base}${base.includes("?") ? "&" : "?"}v=${Date.now()}#${hash}`);
    } else {
      frame.src = viewURL(`${it.url}${it.url.includes("?") ? "&" : "?"}v=${Date.now()}`);
    }
  } else {
    frame.src = viewURL(it.url);
  }

  // highlight
  for (const b of document.querySelectorAll(".menu-item")){
    b.classList.toggle("active", b.dataset.item === it.id);
  }

  // abre el grupo padre
  const groupEl = findGroupByItemId(it.id);
  if (groupEl){
    closeAllGroups();
    groupEl.classList.add("open");
  }

  // si sidebar no está pinned, colapsa al elegir
  if (!isSidebarPinned()){
    collapseSidebarSoon();
  }

  // Mobile overlay: cerrar al navegar
  try{
    if (document.body.classList.contains("sb-open")){
      qs("#sidebar")?.classList.remove("open-mobile");
      document.body.classList.remove("sb-open");
    }
  }catch(_){}
}

function findGroupByItemId(itemId){
  for (const g of document.querySelectorAll(".menu-group")){
    if (g.querySelector(`.menu-item[data-item="${itemId}"]`)) return g;
  }
  return null;
}

/* =========================
   SIDEBAR UX
   - pinned: botón ☰
   - si no pinned: expand on hover, collapse on mouseleave
========================= */
function isSidebarPinned(){
  return localStorage.getItem("gd_sidebar_pinned") === "1";
}
function setSidebarPinned(v){
  localStorage.setItem("gd_sidebar_pinned", v ? "1" : "0");
}

let collapseTimer = null;
function collapseSidebarSoon(){
  if (collapseTimer) clearTimeout(collapseTimer);
  collapseTimer = setTimeout(() => {
    if (!isSidebarPinned()){
      qs("#sidebar").classList.add("collapsed");
      closeAllGroups();
    }
  }, 250);
}

function bindSidebarBehavior(){
  const sb = qs("#sidebar");
  const isMobile = () => window.matchMedia && window.matchMedia("(max-width: 980px)").matches;

  // initial
  if (isSidebarPinned()){
    sb.classList.remove("collapsed");
  }else{
    sb.classList.add("collapsed");
  }

  // hover expand/collapse (cuando NO pinned)
  sb.addEventListener("mouseenter", () => {
    if (isMobile()) return;
    if (!isSidebarPinned()){
      sb.classList.remove("collapsed");
      // abre grupo activo si existe
      const g = findGroupByItemId(ACTIVE_ITEM_ID);
      if (g) g.classList.add("open");
    }
  });
  sb.addEventListener("mouseleave", () => {
    if (isMobile()) return;
    if (!isSidebarPinned()){
      collapseSidebarSoon();
    }
  });

  // pin/unpin
  qs("#btnSidebar").addEventListener("click", () => {
    if (isMobile()){
      // Mobile: sidebar como overlay (no tocamos pin)
      const open = sb.classList.toggle("open-mobile");
      document.body.classList.toggle("sb-open", open);
      if (open){
        sb.classList.remove("collapsed");
        const g = findGroupByItemId(ACTIVE_ITEM_ID);
        if (g) g.classList.add("open");
      }else{
        closeAllGroups();
      }
      return;
    }

    const pinned = isSidebarPinned();
    setSidebarPinned(!pinned);
    if (!pinned){
      sb.classList.remove("collapsed");
    }else{
      sb.classList.add("collapsed");
      closeAllGroups();
    }
  });

  // Tap outside (backdrop) closes sidebar on mobile
  document.addEventListener("click", (ev) => {
    if (!isMobile()) return;
    if (!document.body.classList.contains("sb-open")) return;
    if (ev.target.closest("#sidebar") || ev.target.closest("#btnSidebar")) return;
    sb.classList.remove("open-mobile");
    document.body.classList.remove("sb-open");
  }, { capture:true });
}

/* =========================
   TOPBAR ACTIONS
========================= */
function bindTopbar(){
  // Brand click -> Dashboard
  qs("#brandHome")?.addEventListener("click", () => {
    if (CURRENT_ALLOWED && CURRENT_ALLOWED.has("dash_home")){
      openItem({ id:"dash_home", url:"/web/views/dashboard.html" });
    } else {
      const first = findFirstAllowedItem();
      if (first) openItem(first);
    }
  });

  // Weather open/close
  const openWx = () => {
    const m = qs("#wxModal");
    m.classList.add("open");
    m.setAttribute("aria-hidden","false");
  };
  const closeWx = () => {
    const m = qs("#wxModal");
    m.classList.remove("open");
    m.setAttribute("aria-hidden","true");
  };
  qs("#wxPill")?.addEventListener("click", openWx);
  qs("#wxClose")?.addEventListener("click", closeWx);
  qs("#wxModalBg")?.addEventListener("click", closeWx);

  // Logout confirm
  qs("#btnLogout").addEventListener("click", async () => {
    const ok = await Swal.fire({
      title: "Cerrar sesión",
      text: "¿Seguro que deseas cerrar sesión?",
      icon: "question",
      showCancelButton: true,
      confirmButtonText: "Sí, salir",
      cancelButtonText: "Cancelar",
      confirmButtonColor: "#19C37D",
    }).then(r => r.isConfirmed);

    if (!ok) return;

    await _serverLogout("manual");
    localStorage.removeItem("token");
    localStorage.removeItem("nombre");
    sessionStorage.removeItem("token");
    sessionStorage.removeItem("nombre");
    location.href = `${API_BASE}/web/login.html`;
  });

  // Refresh
  qs("#btnRefresh")?.addEventListener("click", async () => {
    const frame = qs("#mainFrame");
    try{
      frame?.contentWindow?.location?.reload();
    }catch(_){
      if (frame) frame.src = frame.src;
    }
    try{
      const data = await fetchNotifications();
      renderNotifications(data);
    }catch(_){}
  });
}

// (Tools removido) accesos rápidos y vista Tools se eliminan para mantener el CRM enfocado.

function initLetterGlitch(target, opts={}){
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
  const grid = { columns:0, rows:0 };
  const fontSize = 14;
  const charWidth = 10;
  const charHeight = 18;
  const glyphs = Array.from(characters);
  let last = Date.now();
  let raf = null;

  const rand = (arr) => arr[Math.floor(Math.random()*arr.length)];
  const randChar = () => rand(glyphs);
  const randColor = () => rand(glitchColors);

  const hexToRgb = (hex) => {
    const h = hex.replace("#","").trim();
    if (h.length !== 6) return null;
    return {
      r: parseInt(h.slice(0,2),16),
      g: parseInt(h.slice(2,4),16),
      b: parseInt(h.slice(4,6),16),
    };
  };
  const lerpColor = (a,b,f) => {
    const r = Math.round(a.r + (b.r - a.r)*f);
    const g = Math.round(a.g + (b.g - a.g)*f);
    const b2 = Math.round(a.b + (b.b - a.b)*f);
    return `rgb(${r},${g},${b2})`;
  };

  const calcGrid = (w,h) => ({ columns: Math.ceil(w/charWidth), rows: Math.ceil(h/charHeight) });
  const initLetters = (cols, rows) => {
    grid.columns = cols; grid.rows = rows;
    letters.length = cols * rows;
    for (let i=0;i<letters.length;i++){
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
    ctx.setTransform(dpr,0,0,dpr,0,0);
    const { columns, rows } = calcGrid(rect.width, rect.height);
    initLetters(columns, rows);
    draw();
  };

  const draw = () => {
    const rect = canvas.getBoundingClientRect();
    ctx.clearRect(0,0,rect.width,rect.height);
    ctx.font = `${fontSize}px monospace`;
    ctx.textBaseline = "top";
    letters.forEach((l, i) => {
      const x = (i % grid.columns) * charWidth;
      const y = Math.floor(i / grid.columns) * charHeight;
      ctx.fillStyle = l.color;
      ctx.fillText(l.char, x, y);
    });
  };

  const update = () => {
    const count = Math.max(1, Math.floor(letters.length * 0.05));
    for (let i=0;i<count;i++){
      const idx = Math.floor(Math.random()*letters.length);
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
    for (const l of letters){
      if (l.t < 1){
        l.t = Math.min(1, l.t + 0.05);
        const a = hexToRgb(l.color) || hexToRgb("#2b4539");
        const b = hexToRgb(l.target) || hexToRgb("#61dca3");
        if (a && b){
          l.color = lerpColor(a,b,l.t);
          needs = true;
        }
      }
    }
    if (needs) draw();
  };

  const animate = () => {
    const now = Date.now();
    if (now - last >= glitchSpeed){
      update(); draw(); last = now;
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

/* =========================
   INIT
========================= */
function findFirstAllowedItem(){
  const allowed = CURRENT_ALLOWED;
  for (const g of MENU){
    for (const it of g.items){
      if (it.sep || !it.url) continue;
      if (allowed && !allowed.has(it.id)) continue;
      return it;
    }
  }
  return null;
}

function openDefault(){
  // Roles Operador/Conductor: portal dedicado full-screen (sin sidebar).
  if (CURRENT_ROLE_ID === 6 || CURRENT_ROLE_ID === 7){
    openItem({ id:"ops_portal", url:"/web/views/portal_ops.html?v=20260305-opsportal2" });
    return;
  }
  if (CURRENT_ALLOWED && CURRENT_ALLOWED.has("dash_home")){
    openItem({ id:"dash_home", url:"/web/views/dashboard.html" });
    return;
  }
  const first = findFirstAllowedItem();
  if (first) openItem(first);
}

(function init(){
  if (!requireAuth()) return;

  setupIdleLogout();
  setupBackupJobWatch();

  buildMenu();
  bindSidebarBehavior();
  bindTopbar();
  bindNotifications();
  startClock();
  fetchMe().finally(() => {
    initThemeToggle();
    initUserMenu();
    setupChatWatch();
    openDefault();
    // Glitch en topbar y sidebar (solo visual, sin afectar UI)
    initLetterGlitch(document.querySelector(".topbar"), { glitchSpeed: 50 });
  });

  // allow children to request navigation
  window.addEventListener("message", (ev) => {
    if (ev.data?.type === "openItem" && ev.data?.url && ev.data?.id){
      openItem({ id: ev.data.id, url: ev.data.url });
      return;
    }
    if (ev.data?.type === "logout"){
      localStorage.removeItem("token");
      localStorage.removeItem("nombre");
      sessionStorage.removeItem("token");
      sessionStorage.removeItem("nombre");
      location.href = `${API_BASE}/web/login.html`;
    }
  });
})();
