const API_BASE = "http://127.0.0.1:8000";
const $ = (id) => document.getElementById(id);

function toast(msg, ok=true){
  const el = $("toast");
  el.textContent = msg;
  el.style.color = ok ? "rgba(39,228,139,.95)" : "rgba(239,68,68,.95)";
}

function setStatus(on){
  const pill = $("statusPill");
  pill.textContent = on ? "ON" : "OFF";
  pill.classList.toggle("on", !!on);
}

function optDefault(sel, label="SELECCIONAR"){
  sel.innerHTML = "";
  const o = document.createElement("option");
  o.value = "";
  o.textContent = label;
  sel.appendChild(o);
}

function addOpts(sel, arr, idKey, nameKey){
  (arr||[]).forEach((x) => {
    const id = x[idKey];
    const nm = x[nameKey];
    if (id == null || !nm) return;
    const o = document.createElement("option");
    o.value = String(id);
    o.textContent = String(nm).toUpperCase();
    sel.appendChild(o);
  });
}

function fillPlatforms(sel, plataformas){
  optDefault(sel);
  const list = (plataformas && plataformas.length)
    ? plataformas.map(p => (p.nombre || p.id || p.plataforma || p).toString())
    : ["WHATSAPP","INSTAGRAM","EMAIL","FORMULARIO","REFERIDO","LLAMADA","OTRO"];

  list.forEach((v) => {
    const o = document.createElement("option");
    o.value = v.toUpperCase();
    o.textContent = v.toUpperCase();
    sel.appendChild(o);
  });
}

function getToken(){
  return new Promise((resolve) => chrome.storage.local.get(["gdToken","gdTokenAt"], (r)=>resolve(r)));
}

function getDraft(){
  return new Promise((resolve) => chrome.storage.local.get(["gdDraft"], (r)=>resolve(r.gdDraft||{})));
}

function clearDraft(){
  return new Promise((resolve) => chrome.storage.local.set({ gdDraft: {} }, ()=>resolve()));
}

async function loadCatalogs(token){
  const res = await fetch(`${API_BASE}/leads/catalogos`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {}
  });
  if (!res.ok) throw new Error(`Catálogos HTTP ${res.status}`);
  const j = await res.json();
  const c = j.catalogos || j;
  return {
    marcas: c.marcas || [],
    comunas: c.comunas || [],
    tipos: c.tipocliente || c.tipos_cliente || [],
    plataformas: c.plataformas || []
  };
}

function val(id){ return ($(id).value || "").trim(); }
function up(s){ return String(s||"").trim().toUpperCase(); }
function numOrNull(x){
  const n = Number(x);
  return Number.isFinite(n) && n > 0 ? n : null;
}

async function hydrate(){
  const tok = await getToken();
  const token = tok.gdToken || "";
  setStatus(!!token);
  $("tokenAt").textContent = tok.gdTokenAt ? new Date(tok.gdTokenAt).toLocaleString() : "—";

  let cats = { marcas:[], comunas:[], tipos:[], plataformas:[] };
  if (token) {
    try{
      cats = await loadCatalogs(token);
      $("hint").style.display = "none";
    } catch(e){
      $("hint").style.display = "";
      toast("Token OK, pero no pude cargar catálogos (API).", false);
    }
  } else {
    $("hint").style.display = "";
  }

  const selMarca = $("id_marca");
  const selTipo  = $("id_tipo_cliente");
  const selComuna= $("id_comuna");
  const selPlat  = $("plataforma");

  optDefault(selMarca); optDefault(selTipo); optDefault(selComuna);
  addOpts(selMarca, cats.marcas, "id_marca", "nombre");
  addOpts(selTipo,  cats.tipos,  "id_tipo_cliente", "nombre");
  addOpts(selComuna,cats.comunas,"id_comuna", "nombre");
  fillPlatforms(selPlat, cats.plataformas);

  const d = await getDraft();
  if (d.nombre_cliente) $("nombre_cliente").value = up(d.nombre_cliente);
  if (d.telefono) $("telefono").value = String(d.telefono);
  if (d.email) $("email").value = String(d.email);
  if (d.direccion) $("direccion").value = up(d.direccion);
  if (d.plataforma) $("plataforma").value = up(d.plataforma);
  if (d.fecha_evento && /^\d{4}-\d{2}-\d{2}$/.test(String(d.fecha_evento))) $("fecha_evento").value = String(d.fecha_evento);

  if (d.comuna_text && cats.comunas && cats.comunas.length) {
    const t = up(d.comuna_text);
    const found = cats.comunas.find(x => up(x.nombre) === t);
    if (found && found.id_comuna != null) $("id_comuna").value = String(found.id_comuna);
  }

  toast(token ? "Listo. Crea el lead." : "Sin token. Abre CRM y logéate.", !!token);
}

async function createLead(){
  const btn = $("btnCreate");
  btn.disabled = true;

  const tok = await getToken();
  const token = tok.gdToken || "";

  if (!token) {
    toast("NO AUTORIZADO: abre el CRM y logéate (127.0.0.1:8000).", false);
    btn.disabled = false;
    return;
  }

  const nombre = up(val("nombre_cliente"));
  if (!nombre) {
    toast("NOMBRE es obligatorio.", false);
    btn.disabled = false;
    return;
  }

  const payload = {
    nombre_cliente: nombre,
    telefono: val("telefono"),
    email: val("email"),
    direccion: up(val("direccion")),
    fecha_evento: val("fecha_evento") || null,
    plataforma: up(val("plataforma")),
    id_marca: numOrNull(val("id_marca")),
    id_tipo_cliente: numOrNull(val("id_tipo_cliente")),
    id_comuna: numOrNull(val("id_comuna")),
    notas: up(val("notas"))
  };

  try{
    const res = await fetch(`${API_BASE}/leads`, {
      method: "POST",
      headers: {
        "Content-Type":"application/json",
        "Authorization": `Bearer ${token}`
      },
      body: JSON.stringify(payload)
    });

    if (!res.ok) {
      const j = await res.json().catch(()=> ({}));
      throw new Error(j.detail || `HTTP ${res.status}`);
    }

    toast("LEAD CREADO ✅", true);
    await clearDraft();
    setTimeout(() => window.close(), 650);
  }catch(e){
    toast(`Error creando lead: ${e.message}`, false);
    btn.disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  $("btnCreate").addEventListener("click", createLead);

  $("btnClear").addEventListener("click", async () => {
    await clearDraft();
    location.reload();
  });

  $("btnOpenCRM").addEventListener("click", async () => {
    chrome.tabs.create({ url: `${API_BASE}/web/login.html` });
    toast("Abrí el CRM. Logéate y luego vuelve al popup.", true);
  });

  await hydrate();
});
