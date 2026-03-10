const CRM = "http://127.0.0.1:8000";

const $ = (q)=>document.querySelector(q);
const els = {
  status: $("#status"),
  err: $("#err"),
  nombre: $("#nombre"),
  telefono: $("#telefono"),
  email: $("#email"),
  direccion: $("#direccion"),
  comuna: $("#comuna"),
  tipo: $("#tipo_cliente"),
  plataforma: $("#plataforma"),
  marca: $("#marca"),
  estado: $("#estado"),
  fecha: $("#fecha_evento"),
  notas: $("#notas"),
  create: $("#create"),
  saveDraft: $("#saveDraft"),
  close: $("#close")
};

function opt(sel, value, label){
  const o=document.createElement("option");
  o.value=String(value ?? "");
  o.textContent=String(label ?? "");
  sel.appendChild(o);
}

async function getLocal(keys){
  return await chrome.storage.local.get(keys);
}
async function setLocal(obj){
  return await chrome.storage.local.set(obj);
}

async function loadDraft(){
  const { draft = {} } = await getLocal(["draft"]);
  if (draft.name) els.nombre.value = draft.name;
  if (draft.phone) els.telefono.value = draft.phone;
  if (draft.email) els.email.value = draft.email;
  if (draft.address) els.direccion.value = draft.address;
  if (draft.comuna_text && !els.direccion.value) {
    // si el usuario usó comuna_text, al menos lo ponemos en notas como pista
    els.notas.value = (els.notas.value ? els.notas.value + "\n" : "") + "COMUNA (texto): " + draft.comuna_text;
  }
}

async function ensureToken(){
  const { token } = await getLocal(["token"]);
  return (token || "").trim();
}

function setStatus(t){ els.status.textContent = t; }
function setErr(t){ els.err.textContent = t || ""; }

async function loadCatalogos(){
  const res = await fetch(CRM + "/leads/catalogos");
  if (!res.ok) throw new Error("No pude cargar catálogos (HTTP "+res.status+")");
  const data = await res.json();

  // defaults
  els.comuna.innerHTML = ""; opt(els.comuna, "", "— Selecciona —");
  (data.comunas || data.catalogos?.comunas || []).forEach(r => opt(els.comuna, r.id_comuna, r.nombre));

  els.tipo.innerHTML = ""; opt(els.tipo, "", "— Selecciona —");
  (data.tipos_cliente || data.tipocliente || data.catalogos?.tipos_cliente || []).forEach(r => opt(els.tipo, r.id_tipo_cliente, r.nombre));

  els.plataforma.innerHTML = "";
  (data.plataformas || data.catalogos?.plataformas || [
    {id:"WHATSAPP",nombre:"WHATSAPP"},{id:"EMAIL",nombre:"EMAIL"},{id:"INSTAGRAM",nombre:"INSTAGRAM"}
  ]).forEach(r => opt(els.plataforma, r.id, r.nombre));
  els.plataforma.value = "WHATSAPP";

  els.marca.innerHTML = ""; opt(els.marca, "", "— Selecciona —");
  (data.marcas || data.catalogos?.marcas || []).forEach(r => opt(els.marca, r.id_marca, r.nombre));

  els.estado.innerHTML = ""; opt(els.estado, "", "— Selecciona —");
  (data.estados || data.catalogos?.estados || []).forEach(r => opt(els.estado, r.id_estado, r.nombre));
}

async function saveDraft(){
  const d = {
    name: els.nombre.value.trim(),
    phone: els.telefono.value.trim(),
    email: els.email.value.trim(),
    address: els.direccion.value.trim()
  };
  await setLocal({ draft: d });
  setStatus("Borrador guardado.");
}

async function createLead(){
  setErr("");
  const nombre = els.nombre.value.trim();
  if (!nombre) { setErr("Falta nombre del cliente."); return; }

  const payload = {
    nombre_cliente: nombre,
    telefono: els.telefono.value.trim() || null,
    email: els.email.value.trim() || null,
    direccion: els.direccion.value.trim() || null,
    id_comuna: els.comuna.value ? Number(els.comuna.value) : null,
    id_tipo_cliente: els.tipo.value ? Number(els.tipo.value) : null,
    plataforma: els.plataforma.value || null,
    id_marca: els.marca.value ? Number(els.marca.value) : null,
    id_estado: els.estado.value ? Number(els.estado.value) : null,
    fecha_evento: els.fecha.value ? String(els.fecha.value) : null,
    notas: els.notas.value.trim() || null
  };

  const token = await ensureToken();
  const headers = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = "Bearer " + token;

  const res = await fetch(CRM + "/leads", {
    method: "POST",
    headers,
    body: JSON.stringify(payload)
  });

  if (res.status === 401) {
    setErr("NO AUTORIZADO (401). Abre el CRM, inicia sesión y vuelve a intentar.");
    return;
  }
  if (!res.ok) {
    const txt = await res.text().catch(()=> "");
    setErr("Error creando lead: " + (txt || ("HTTP "+res.status)));
    return;
  }

  await setLocal({ draft: {} });
  setStatus("✅ Lead creado. Puedes cerrar.");
}

(async function boot(){
  els.close.addEventListener("click", ()=> window.close());
  els.saveDraft.addEventListener("click", saveDraft);
  els.create.addEventListener("click", createLead);

  try{
    setStatus("Cargando catálogos…");
    await loadCatalogos();
    await loadDraft();
    setStatus("Listo.");
  }catch(e){
    setErr(String(e.message || e));
    setStatus("Error.");
  }
})();
