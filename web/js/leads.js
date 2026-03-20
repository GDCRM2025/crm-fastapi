/* GREEN DIAMOND — Shell controller (menu + theme + auth + topbar) */

"use strict";

const qs = (s, el = document) => el.querySelector(s);

function getToken() {
  return localStorage.getItem("token") || sessionStorage.getItem("token") || "";
}

function authHeaders(extra = {}) {
  const t = getToken();
  return t ? { ...extra, Authorization: `Bearer ${t}` } : extra;
}

function requireAuth() {
  if (!getToken()) {
    location.href = "/web/login.html";
    return false;
  }
  return true;
}

/* =========================
   THEME
========================= */
function getTheme() {
  return localStorage.getItem("gd_theme") || "dark";
}

function setTheme(mode) {
  localStorage.setItem("gd_theme", mode);
  applyTheme(mode);
}

function applyTheme(mode) {
  document.documentElement.classList.toggle("light", mode === "light");

  // Propaga a iframe (si el view escucha postMessage)
  const fr = qs("#mainFrame");
  try {
    fr?.contentWindow?.postMessage({ type: "theme", mode }, "*");
  } catch (_) {}
}

function initThemeToggle() {
  const tgl = qs("#themeToggle");
  const mode = getTheme();
  if (tgl) {
    tgl.checked = mode === "light";
    tgl.addEventListener("change", () => setTheme(tgl.checked ? "light" : "dark"));
  }
  applyTheme(mode);

  // al cargar cualquier view, re-propaga el tema
  qs("#mainFrame")?.addEventListener("load", () => {
    applyTheme(getTheme());
    // opcional: empuja token a la vista si quiere escucharlo
    try {
      qs("#mainFrame")?.contentWindow?.postMessage({ type: "auth", token: getToken() }, "*");
    } catch (_) {}
  });
}

/* =========================
   CLOCK
========================= */
function startClock() {
  const el = qs("#clockBox");
  if (!el) return;

  const fmt = new Intl.DateTimeFormat("es-CL", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });

  const tick = () => {
    el.textContent = fmt.format(new Date());
  };
  tick();
  setInterval(tick, 30_000);
}

/* =========================
   USER
========================= */
async function fetchMe() {
  try {
    const r = await fetch("/me", { headers: authHeaders() });
    if (!r.ok) throw new Error("me failed");
    const me = await r.json();
    qs("#userBox").textContent = `Usuario: ${me.username || me.nombre || "—"}`;
  } catch (_) {
    qs("#userBox").textContent = "Usuario: —";
  }
}

/* =========================
   MENU (SIN HOME)
========================= */
const MENU = [
  {
    id: "leads",
    ico: "📌",
    title: "Leads",
    items: [
      { id: "leads_ver", label: "Ver Leads", url: "/web/views/leads.html" },
      { id: "leads_fil", label: "Filtrar Leads", url: "/web/views/filtro_leads.html" },
    ],
  },
  {
    id: "cotizador",
    ico: "🧾",
    title: "Cotizador",
    items: [
      { id: "cotizador_view", label: "Cotizador", url: "/web/views/cotizador.html" },
      { id: "historial", label: "Historial", url: "/web/views/historial_cotizaciones.html" },
    ],
  },
  {
    id: "reportes",
    ico: "📊",
    title: "Reportes",
    items: [
      { id: "rep_funnel", label: "Funnel de ventas", url: "/web/views/reportes.html?only=funnel#funnel" },
      { id: "rep_cierre", label: "% de cierre", url: "/web/views/reportes.html?only=cierre#cierre" },
      { id: "rep_total", label: "Total venta", url: "/web/views/reportes.html?only=total#total" },

      { id: "rep_sep1", label: "—", url: null, sep: true },

      { id: "rep_com", label: "Comunas más vendidas", url: "/web/views/reportes.html?only=comunas#comunas" },
      { id: "rep_prod", label: "Productos más vendidos", url: "/web/views/reportes.html?only=productos#productos" },
      { id: "rep_cli", label: "Clientes más frecuentes", url: "/web/views/reportes.html?only=clientes#clientes" },

      { id: "rep_sep2", label: "—", url: null, sep: true },

      { id: "rep_hoy", label: "Leads creados hoy", url: "/web/views/reportes.html?only=leads_hoy#leads_hoy" },
      { id: "rep_dia", label: "Venta diaria", url: "/web/views/reportes.html?only=venta_diaria#venta_diaria" },
    ],
  },
  {
    id: "operaciones",
    ico: "🛠️",
    title: "Operaciones",
    items: [
      { id: "op_mice", label: "Mice and Place", url: "/web/views/operaciones_mice.html" },
      { id: "op_rec", label: "Recetas", url: "/web/views/operaciones_recetas.html" },
      { id: "op_cos", label: "Costeo", url: "/web/views/operaciones_costeo.html" },

      { id: "op_sep1", label: "—", url: null, sep: true },

      { id: "op_inv_m", label: "Inventario Mercancía", url: "/web/views/inventario_mercancia.html" },
      { id: "op_inv_c", label: "Inventario Carritos", url: "/web/views/inventario_carritos.html" },

      { id: "op_sep2", label: "—", url: null, sep: true },

      { id: "op_ruta", label: "Ruta", url: "/web/views/ruta.html" },
    ],
  },
  {
    id: "tools",
    ico: "🧰",
    title: "Tools",
    items: [
      // pre-agenda eliminada
      { id: "tool_cal", label: "Calendario", url: "/web/views/calendar.html" },
      { id: "tool_calc", label: "Calculadora", url: "/web/views/calculadora.html" },
    ],
  },
  {
    id: "conductores",
    ico: "🚚",
    title: "Conductores",
    items: [
      { id: "gps", label: "Conectar GPS", url: "/web/views/conductores_gps.html" },
      { id: "vruta", label: "Ver Ruta", url: "/web/views/conductores_ruta.html" },
    ],
  },
  {
    id: "finanzas",
    ico: "💰",
    title: "Finanzas",
    items: [
      { id: "pl", label: "P&L", url: "/web/views/finanzas_pl.html" },
      { id: "gast", label: "Cargar Gastos", url: "/web/views/finanzas_gastos.html" },
      { id: "evt", label: "Registrar Evento", url: "/web/views/finanzas_evento.html" },
    ],
  },
  {
    id: "settings",
    ico: "⚙️",
    title: "Settings",
    items: [
      { id: "set_users", label: "Usuarios", url: "/web/views/settings.html?entity=usuarios" },
      { id: "set_marcas", label: "Marcas", url: "/web/views/settings.html?entity=marcas" },
      { id: "set_prod", label: "Productos", url: "/web/views/settings.html?entity=productos" },
      { id: "set_com", label: "Comunas", url: "/web/views/settings.html?entity=comunas" },
      { id: "set_tc", label: "Tipos Cliente", url: "/web/views/settings.html?entity=tipos_cliente" },
      { id: "set_el", label: "Estados Lead", url: "/web/views/settings.html?entity=estados_lead" },
      { id: "set_roles", label: "Roles", url: "/web/views/settings.html?entity=roles" },
    ],
  },
];

let ACTIVE_ITEM_ID = null;

function buildMenu() {
  const nav = qs("#sideMenu");
  if (!nav) return;

  nav.innerHTML = "";

  for (const g of MENU) {
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

    for (const it of g.items) {
      if (it.sep) {
        const sep = document.createElement("div");
        sep.style.height = "8px";
        items.appendChild(sep);
        continue;
      }

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
      const isOpen = group.classList.contains("open");
      closeAllGroups();
      if (!isOpen) group.classList.add("open");
    });

    group.appendChild(head);
    group.appendChild(items);
    nav.appendChild(group);
  }
}

function closeAllGroups() {
  for (const el of document.querySelectorAll(".menu-group.open")) {
    el.classList.remove("open");
  }
}

function rememberLastView(itemId, url) {
  try {
    localStorage.setItem("gd_last_view_id", itemId || "");
    localStorage.setItem("gd_last_view_url", url || "");
  } catch (_) {}
}

function loadLastView() {
  try {
    const id = localStorage.getItem("gd_last_view_id") || "";
    const url = localStorage.getItem("gd_last_view_url") || "";
    if (id && url) return { id, url };
  } catch (_) {}
  return null;
}

async function openItem(it) {
  if (!it?.url) return;

  ACTIVE_ITEM_ID = it.id;

  // set iframe
  const frame = qs("#mainFrame");
  if (frame) {
    // cache-busting suave para que no te deje pegado con HTML viejo
    const u = new URL(it.url, location.origin);
    u.searchParams.set("_", String(Date.now()).slice(0, 10));
    frame.src = u.pathname + u.search + u.hash;
  }

  rememberLastView(it.id, it.url);

  // highlight
  for (const b of document.querySelectorAll(".menu-item")) {
    b.classList.toggle("active", b.dataset.item === it.id);
  }

  // abre el grupo padre
  const groupEl = findGroupByItemId(it.id);
  if (groupEl) {
    closeAllGroups();
    groupEl.classList.add("open");
  }

  // si sidebar no está pinned, colapsa al elegir
  if (!isSidebarPinned()) {
    collapseSidebarSoon();
  }
}

function findGroupByItemId(itemId) {
  for (const g of document.querySelectorAll(".menu-group")) {
    if (g.querySelector(`.menu-item[data-item="${itemId}"]`)) return g;
  }
  return null;
}

/* =========================
   SIDEBAR UX
   - pinned: botón ☰
   - si no pinned: expand on hover, collapse on mouseleave
========================= */
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
      qs("#sidebar")?.classList.add("collapsed");
      closeAllGroups();
    }
  }, 250);
}

function bindSidebarBehavior() {
  const sb = qs("#sidebar");
  if (!sb) return;

  // initial
  if (isSidebarPinned()) sb.classList.remove("collapsed");
  else sb.classList.add("collapsed");

  // hover expand/collapse (cuando NO pinned)
  sb.addEventListener("mouseenter", () => {
    if (!isSidebarPinned()) {
      sb.classList.remove("collapsed");
      const g = findGroupByItemId(ACTIVE_ITEM_ID);
      if (g) g.classList.add("open");
    }
  });

  sb.addEventListener("mouseleave", () => {
    if (!isSidebarPinned()) collapseSidebarSoon();
  });

  // pin/unpin
  qs("#btnSidebar")?.addEventListener("click", () => {
    const pinned = isSidebarPinned();
    setSidebarPinned(!pinned);
    if (!pinned) {
      sb.classList.remove("collapsed");
    } else {
      sb.classList.add("collapsed");
      closeAllGroups();
    }
  });
}

/* =========================
   TOPBAR ACTIONS
========================= */
function bindTopbar() {
  // Weather open/close
  const openWx = () => {
    const m = qs("#wxModal");
    m?.classList.add("open");
    m?.setAttribute("aria-hidden", "false");
  };
  const closeWx = () => {
    const m = qs("#wxModal");
    m?.classList.remove("open");
    m?.setAttribute("aria-hidden", "true");
  };

  qs("#wxPill")?.addEventListener("click", openWx);
  qs("#wxClose")?.addEventListener("click", closeWx);
  qs("#wxModalBg")?.addEventListener("click", closeWx);

  // Logout confirm
  qs("#btnLogout")?.addEventListener("click", async () => {
    const ok = await Swal.fire({
      title: "Cerrar sesión",
      text: "¿Seguro que deseas cerrar sesión?",
      icon: "question",
      showCancelButton: true,
      confirmButtonText: "Sí, salir",
      cancelButtonText: "Cancelar",
      confirmButtonColor: "#19C37D",
    }).then((r) => r.isConfirmed);

    if (!ok) return;

    localStorage.removeItem("token");
    localStorage.removeItem("nombre");
    sessionStorage.removeItem("token");
    sessionStorage.removeItem("nombre");
    location.href = "/web/login.html";
  });
}

/* =========================
   IFRAME ERROR HANDLING
========================= */
function bindFrameGuards() {
  const frame = qs("#mainFrame");
  if (!frame) return;

  frame.addEventListener("error", () => {
    console.error("Iframe load error");
    try {
      Swal.fire("Error", "No se pudo cargar la vista.", "error");
    } catch (_) {
      alert("No se pudo cargar la vista.");
    }
  });

  // Si las vistas quieren avisar cosas:
  // postMessage({type:'auth:expired'}) o {type:'toast', level:'error', message:'...'}
  window.addEventListener("message", (ev) => {
    const data = ev?.data || {};
    if (!data || typeof data !== "object") return;

    if (data.type === "auth:expired") {
      localStorage.removeItem("token");
      sessionStorage.removeItem("token");
      location.href = "/web/login.html";
      return;
    }

    if (data.type === "toast" && data.message) {
      const level = data.level || "info";
      try {
        Swal.fire(level === "error" ? "Error" : "Info", String(data.message), level);
      } catch (_) {
        alert(String(data.message));
      }
    }
  });
}

/* =========================
   INIT
========================= */
(function init() {
  if (!requireAuth()) return;

  buildMenu();
  bindSidebarBehavior();
  bindTopbar();
  bindFrameGuards();
  initThemeToggle();
  startClock();
  fetchMe();

  // Vista inicial: última vista o Ver Leads
  const last = loadLastView();
  if (last) openItem(last);
  else openItem({ id: "leads_ver", url: "/web/views/leads.html" });
})();
