const $ = (q, c=document) => c.querySelector(q);

const DEFAULTS = {
  // Preferimos sin-www (hay usuarios que trabajan en greendiamond.cl). Igual aceptamos www.
  crmBase: "https://greendiamond.cl/crm",
};

async function getStore(keys){ return await chrome.storage.local.get(keys); }
async function setStore(obj){ return await chrome.storage.local.set(obj); }
async function clearStore(keys){ await chrome.storage.local.remove(keys); }

function setStatus(txt){ $("#status").textContent = txt || "—"; }

function normalizeText(s){ return String(s || "").trim(); }

async function apiFetch(path, opt={}){
  const st = await getStore(["crmBase","token"]);
  const base = (st.crmBase || DEFAULTS.crmBase).replace(/\/$/,"");
  const headers = new Headers(opt.headers || {});
  if (st.token) headers.set("Authorization", "Bearer " + st.token);
  headers.set("Accept", "application/json");
  return await fetch(base + path, { ...opt, headers });
}

async function tryCaptureTokenFromCrmTab(){
  const st = await getStore(["crmBase"]);
  const base = (st.crmBase || DEFAULTS.crmBase).replace(/\/$/,"");
  let wantsHost = "";
  try{ wantsHost = new URL(base).host; }catch(_){}

  // Puede que el CRM esté abierto en otra ventana: buscamos en todas.
  const tabs = await chrome.tabs.query({});
  const candidates = (tabs || []).filter(t => {
    const u = String(t.url || "");
    if (!u) return false;
    // Importante: a veces el CRM se abre sin www (greendiamond.cl vs www.greendiamond.cl).
    // No bloqueamos por host estricto; solo exigimos que sea una URL del CRM.
    if (wantsHost){
      const relaxed =
        wantsHost.includes("greendiamond.cl")
          ? (u.includes("greendiamond.cl"))
          : u.includes(wantsHost);
      if (!relaxed) return false;
    }
    // cualquier página del CRM sirve, pero preferimos /crm/web
    return u.includes("/crm/");
  });
  if (!candidates.length) return false;

  candidates.sort((a,b) => (String(b.url||"").includes("/crm/web")?1:0) - (String(a.url||"").includes("/crm/web")?1:0));

  for (const tab of candidates){
    try{
      const resp = await chrome.tabs.sendMessage(tab.id, { cmd:"get-token" });
      const token = resp?.token || "";
      if (token){
        await setStore({ token, tokenAt: Date.now() });
        return true;
      }
    }catch(_){}

    // Fallback: si el content script no está inyectado (por URL distinta),
    // usamos executeScript para leer el token directo desde la pestaña del CRM.
    try{
      const [res] = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: () => {
          try{
            const t = localStorage.getItem("token") || sessionStorage.getItem("token") || "";
            return { ok: true, token: t };
          }catch(e){
            return { ok: false, error: String(e?.message || e) };
          }
        }
      });
      const token2 = res?.result?.token || "";
      if (token2){
        await setStore({ token: token2, tokenAt: Date.now() });
        return true;
      }
    }catch(_){}
  }
  return false;
}

async function ensureSession(){
  const st0 = await getStore(["crmBase","token"]);
  const base = (st0.crmBase || DEFAULTS.crmBase).replace(/\/$/,"");
  $("#crmBasePill").textContent = base;

  // si hay token, probamos /me
  if (st0.token){
    let r = await apiFetch("/auth/me");
    if (r.status === 404) r = await apiFetch("/me");
    if (r.ok){
      return true;
    }
    await clearStore(["token"]);
  }

  // no token: intentamos capturarlo desde pestaña CRM
  const ok = await tryCaptureTokenFromCrmTab();
  if (!ok){
    setStatus("Sin sesión. Abre el CRM, inicia sesión y vuelve a abrir la extensión.");
    return false;
  }

  return true;
}

async function fetchMe(){
  const r = await apiFetch("/me");
  if (r.status === 404){
    const r2 = await apiFetch("/auth/me");
    if (!r2.ok) throw new Error("No pude cargar /me (HTTP " + r2.status + ")");
    return await r2.json().catch(()=> ({}));
  }
  if (!r.ok) throw new Error("No pude cargar /me (HTTP " + r.status + ")");
  return await r.json().catch(()=> ({}));
}

function fillSelect(sel, items, idKey, labelKey){
  sel.innerHTML = "";
  const o0 = document.createElement("option");
  o0.value = "";
  o0.textContent = "—";
  sel.appendChild(o0);

  for (const it of (items || [])){
    const o = document.createElement("option");
    o.value = it?.[idKey] ?? "";
    // compat: algunas tablas usan "tipo" o "comuna"/"marca"
    const lbl =
      it?.[labelKey] ??
      it?.nombre ??
      it?.tipo ??
      it?.comuna ??
      it?.marca ??
      "";
    o.textContent = String(lbl || "");
    sel.appendChild(o);
  }
}

let cachedCatalogos = null;

async function loadCatalogos(){
  const r = await apiFetch("/leads/catalogos");
  if (!r.ok){
    // Intentamos dar un mensaje más útil que "cargando…"
    if (r.status === 401){
      throw new Error("Sin sesión en CRM (token). Abre el CRM, inicia sesión y vuelve a abrir la extensión.");
    }
    const t = await r.text().catch(()=> "");
    throw new Error((t || ("HTTP " + r.status)).slice(0, 180));
  }
  const data = await r.json().catch(()=> ({}));
  const cats = data.catalogos || data;
  cachedCatalogos = cats;

  // Importante: respetamos el orden entregado por backend (mismo orden del CRM).
  const marcas = cats.marcas || [];
  const comunas = cats.comunas || [];
  const tipos = cats.tipos_cliente || cats.tipocliente || cats.tipos || [];

  fillSelect($("#id_marca"), marcas, "id_marca", "nombre");
  fillSelect($("#id_comuna"), comunas, "id_comuna", "nombre");
  fillSelect($("#id_tipo_cliente"), tipos, "id_tipo_cliente", "nombre");
}

async function createLead(){
  const nombre = normalizeText($("#nombre").value);
  if (!nombre) throw new Error("Falta: Nombre cliente");

  const idMarcaSel = $("#id_marca").value ? Number($("#id_marca").value) : 0;
  // Para evitar "creé lead pero no lo veo", exigimos marca para no-admin en backend,
  // y en front damos feedback inmediato.
  if (!idMarcaSel){
    // Puede ser Admin, pero igual conviene seleccionar marca.
    // Si es Admin, backend permite 0; si no, backend autocompleta o rechaza.
  }

  const payload = {
    // backend acepta cliente o nombre_cliente
    cliente: nombre,
    email: normalizeText($("#email").value) || null,
    telefono: normalizeText($("#telefono").value) || null,
    id_marca: idMarcaSel,
    id_comuna: $("#id_comuna").value ? Number($("#id_comuna").value) : 0,
    id_tipo_cliente: $("#id_tipo_cliente").value ? Number($("#id_tipo_cliente").value) : null,
    plataforma: "EXTENSION",
    fecha_evento: normalizeText($("#fecha_evento").value) || null,
    notas: normalizeText($("#notas").value) || null,
  };

  const r = await apiFetch("/leads", {
    method: "POST",
    headers: { "Content-Type":"application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok){
    const t = await r.text().catch(()=> "");
    throw new Error((t || ("HTTP " + r.status)).slice(0, 200));
  }
  return await r.json().catch(()=> ({}));
}

async function boot(){
  setStatus("Iniciando…");

  // Si alguien cambió a local, respetamos; si no, default prod.
  const st = await getStore(["crmBase"]);
  if (!st.crmBase) await setStore({ crmBase: DEFAULTS.crmBase });

  const ok = await ensureSession();
  if (!ok) return;

  let me = {};
  try{
    me = await fetchMe();
  }catch(e){
    setStatus("Sesión: " + String(e?.message || e));
  }

  try{
    setStatus("Cargando catálogos…");
    await loadCatalogos();
    // Marca por defecto según usuario (excepto Admin)
    const role = String(me?.role || me?.rol || "").toUpperCase();
    const isAdmin = role.includes("ADMIN") || role.includes("SUPERADMIN") || role === "ADMIN" || role === "SUPERADMIN";
    // Compat: EJECUTIVO/EJECUTIVA
    const isSales = role.includes("EJECUTIV") || role.includes("VENTAS") || role.includes("VENDEDOR");
    if (!isAdmin && !isSales){
      // La extensión es solo para Admin y Ejecutivos de venta.
      $("#btnCreate").disabled = true;
      setStatus("Sin permiso: esta extensión es solo para Admin y Ejecutivos.");
      return;
    }
    const marcas = Array.isArray(me?.marcas) ? me.marcas.map(Number).filter(n => Number.isFinite(n) && n > 0) : [];
    const selMarca = $("#id_marca");
    if (selMarca){
      if (!isAdmin){
        // Si el usuario tiene marcas asignadas, limitamos el selector a esas marcas.
        // Solo bloqueamos el selector cuando el usuario tiene EXACTAMENTE 1 marca.
        const allowed = new Set(marcas.map(m => String(m)));
        if (allowed.size){
          Array.from(selMarca.options).forEach(o => {
            if (!o.value || o.value === "0") return;
            // Deshabilitamos marcas fuera del set del usuario
            if (!allowed.has(String(o.value))) o.disabled = true;
          });
        }

        // Preferimos la primera marca asignada (si no hay una ya elegida válida).
        const pref = marcas.length ? String(marcas[0]) : "";
        const currentOk = selMarca.value && (!allowed.size || allowed.has(String(selMarca.value)));
        if (!currentOk){
          const firstAllowed =
            (pref && Array.from(selMarca.options).some(o => o.value === pref) ? pref : "") ||
            Array.from(selMarca.options).find(o => o.value && o.value !== "0" && (!allowed.size || allowed.has(String(o.value))))?.value ||
            "";
          if (firstAllowed) selMarca.value = firstAllowed;
        }

        selMarca.disabled = (allowed.size === 1);
      } else {
        selMarca.disabled = false;
      }
    }
    setStatus("Listo");
  }catch(e){
    setStatus("Catálogos: " + String(e?.message || e));
  }

  // Aplica borrador desde menú contextual (click derecho)
  try{
    const stD = await getStore(["draft"]);
    const d = stD.draft || {};
    if (d && Object.keys(d).length){
      if (d.nombre_cliente) $("#nombre").value = String(d.nombre_cliente).toUpperCase();
      if (d.telefono) $("#telefono").value = String(d.telefono);
      if (d.email) $("#email").value = String(d.email);
      if (d.direccion) $("#direccion").value = String(d.direccion).toUpperCase();
      if (d.plataforma) $("#plataforma").value = String(d.plataforma).toUpperCase();
      if (d.fecha_evento) $("#fecha_evento").value = String(d.fecha_evento);
      if (d.comuna_text && cachedCatalogos?.comunas){
        const t = String(d.comuna_text).trim().toLowerCase();
        const hit = (cachedCatalogos.comunas || []).find(c => String(c.nombre||"").trim().toLowerCase() === t);
        if (hit?.id_comuna != null) $("#id_comuna").value = String(hit.id_comuna);
      }
      setStatus("Borrador aplicado ✔");
    }
  }catch(_){}

  $("#btnClear").addEventListener("click", async () => {
    await clearStore(["draft"]);
    $("#nombre").value = "";
    $("#email").value = "";
    $("#telefono").value = "";
    $("#notas").value = "";
    $("#fecha_evento").value = "";
    $("#id_comuna").value = "";
    $("#id_tipo_cliente").value = "";
    setStatus("Limpio ✔");
  });

  async function openCrmLeads(){
    const st = await getStore(["crmBase"]);
    const base = (st.crmBase || DEFAULTS.crmBase).replace(/\/$/,"");
    const targets = [
      base + "/web/index.html#leads",
      base + "/web/index.html",
      base + "/web/views/leads.html",
    ];

    const wantsHost = (()=>{ try{ return new URL(base).host; }catch(_){ return ""; } })();
    const tabs = await chrome.tabs.query({});
    const hit = (tabs || []).find(t => String(t.url||"").includes(wantsHost) && String(t.url||"").includes("/crm/"));
    if (hit?.id){
      await chrome.tabs.update(hit.id, { active: true, url: targets[0] });
      return;
    }
    await chrome.tabs.create({ url: targets[0] });
  }

  $("#btnOpenLeads").addEventListener("click", async () => {
    try{ await openCrmLeads(); }catch(_){}
  });

  $("#btnCreate").addEventListener("click", async () => {
    try{
      setStatus("Creando lead…");
      const ok3 = await ensureSession();
      if (!ok3) return;
      const data = await createLead();
      setStatus("Lead creado ✔ (ID " + (data?.id_lead || data?.lead?.id_lead || "?") + ")");
      // limpiamos un poco para el siguiente
      $("#nombre").value = "";
      $("#email").value = "";
      $("#telefono").value = "";
      $("#notas").value = "";
      $("#fecha_evento").value = "";
      await clearStore(["draft"]);
      // al crear, lo llevamos directo a Leads
      try{ await openCrmLeads(); }catch(_){}
    }catch(e){
      setStatus(String(e?.message || e));
    }
  });
}

boot();
