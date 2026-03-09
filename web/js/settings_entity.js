const qs  = (s, el=document) => el.querySelector(s);
const qsa = (s, el=document) => Array.from(el.querySelectorAll(s));

function getToken(){
  return window.parent?.GD?.getToken?.()
    || window.top?.GD?.getToken?.()
    || localStorage.getItem("token")
    || sessionStorage.getItem("token")
    || "";
}
function authHeaders(extra={}){
  const t = getToken();
  return t ? { ...extra, Authorization: `Bearer ${t}` } : extra;
}

function param(name){
  return new URLSearchParams(location.search).get(name) || "";
}

const ENTITY = param("e") || param("entity");
const titleEl = qs("#seTitle");
const subEl   = qs("#seSub");
const msgEl   = qs("#seMsg");

let META = null;
let ITEMS = [];
let OFFSET = 0;
let LIMIT = 25;
let SELECTED = null;

function prettyEntity(e){
  return (e||"").replaceAll("_"," ").replace(/\b\w/g, m => m.toUpperCase());
}

function isColorCol(col){
  const n = (col?.column_name||"").toLowerCase();
  return n === "color" || n.endsWith("_color");
}

function colLabel(colName){
  return (colName||"").replaceAll("_"," ");
}

function normalizeMetaPayload(d){
  // Esperamos {entity, table, pk, columns:[{column_name,data_type,is_nullable},...]}
  return d;
}

async function apiJson(url, opts={}){
  const r = await fetch(url, { ...opts, headers: authHeaders({ "Content-Type":"application/json", ...(opts.headers||{}) }) });
  const txt = await r.text();
  let data = null;
  try{ data = txt ? JSON.parse(txt) : null; }catch(_){}
  if (!r.ok){
    const msg = (data && (data.detail || data.message)) ? (data.detail || data.message) : txt || `HTTP ${r.status}`;
    throw new Error(msg);
  }
  return data;
}

async function loadMeta(){
  if (!ENTITY){
    titleEl.textContent = "Settings";
    subEl.textContent = "Falta parámetro ?e=usuarios (por ejemplo).";
    throw new Error("ENTITY vacío");
  }
  const d = await apiJson(`/settings/meta/${encodeURIComponent(ENTITY)}`, { method:"GET" });
  META = normalizeMetaPayload(d);
  titleEl.textContent = `Settings · ${prettyEntity(ENTITY)}`;
  subEl.textContent = `Tabla: ${META.table} · PK: ${META.pk}`;
}

function renderTable(){
  const th = qs("#seTh");
  const tb = qs("#seTb");

  const norm = (v) => String(v || "")
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "");

  const cols = (META?.columns || []).map(c => c.column_name);
  th.innerHTML = cols.map(c => `<th>${colLabel(c)}</th>`).join("");

  const q = norm(qs("#seQ").value || "");
  const filtered = !q ? ITEMS : ITEMS.filter(row => {
    return cols.some(k => norm(row?.[k] ?? "").includes(q));
  });

  tb.innerHTML = "";
  for (const row of filtered){
    const tr = document.createElement("tr");
    tr.dataset.pk = row[META.pk];
    if (SELECTED && String(SELECTED[META.pk]) === String(row[META.pk])) tr.classList.add("sel");

    tr.onclick = () => { SELECTED = row; renderTable(); };

    for (const c of cols){
      const td = document.createElement("td");
      const v = row?.[c];

      // swatch para color
      if (isColorCol({column_name:c}) && v){
        const span = document.createElement("span");
        span.className = "swatch";
        span.style.background = String(v);
        td.appendChild(span);
        td.appendChild(document.createTextNode(String(v)));
      }else{
        td.textContent = (v === null || v === undefined) ? "" : String(v);
      }
      tr.appendChild(td);
    }
    tb.appendChild(tr);
  }

  msgEl.textContent = `Registros: ${filtered.length}${filtered.length !== ITEMS.length ? ` (filtrado desde ${ITEMS.length})` : ""}`;
}

async function loadItems(reset=false){
  if (reset){ OFFSET = 0; ITEMS = []; SELECTED = null; }

  const d = await apiJson(`/settings/${encodeURIComponent(ENTITY)}?limit=${LIMIT}&offset=${OFFSET}`, { method:"GET" });
  const newItems = d.items || d.rows || d.data || [];
  const total = d.total ?? null;

  ITEMS = reset ? newItems : ITEMS.concat(newItems);
  OFFSET += newItems.length;

  renderTable();
  if (total !== null) msgEl.textContent += ` · Total: ${total}`;
}

function fieldInputHTML(col, value){
  const name = col.column_name;
  const type = (col.data_type || "").toLowerCase();

  const v = (value === null || value === undefined) ? "" : String(value);

  // Color picker real
  if (isColorCol(col)){
    const safe = v && /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(v) ? v : "#22c55e";
    return `
      <label style="display:flex; align-items:center; gap:10px; justify-content:space-between;">
        <span style="font-weight:800">${colLabel(name)}</span>
        <input type="color" name="${name}" value="${safe}" style="width:56px; height:34px; border:0; background:transparent; padding:0; cursor:pointer;">
      </label>
    `;
  }

  // Boolean
  if (type.includes("bool")){
    const checked = (value === true || value === "true") ? "checked" : "";
    return `
      <label style="display:flex; align-items:center; gap:10px; justify-content:space-between;">
        <span style="font-weight:800">${colLabel(name)}</span>
        <input type="checkbox" name="${name}" ${checked}>
      </label>
    `;
  }

  // Numbers
  const isNum = type.includes("int") || type.includes("numeric") || type.includes("double") || type.includes("real") || type.includes("float");
  if (isNum){
    return `
      <label style="display:flex; flex-direction:column; gap:6px;">
        <span style="font-weight:800">${colLabel(name)}</span>
        <input name="${name}" type="number" value="${v}" class="swal2-input" style="margin:0">
      </label>
    `;
  }

  // Dates
  if (type === "date"){
    return `
      <label style="display:flex; flex-direction:column; gap:6px;">
        <span style="font-weight:800">${colLabel(name)}</span>
        <input name="${name}" type="date" value="${v}" class="swal2-input" style="margin:0">
      </label>
    `;
  }

  // Default text
  const long = name.toLowerCase().includes("descripcion") || name.toLowerCase().includes("notas");
  if (long){
    return `
      <label style="display:flex; flex-direction:column; gap:6px;">
        <span style="font-weight:800">${colLabel(name)}</span>
        <textarea name="${name}" class="swal2-textarea" style="margin:0; min-height:90px;">${v}</textarea>
      </label>
    `;
  }

  return `
    <label style="display:flex; flex-direction:column; gap:6px;">
      <span style="font-weight:800">${colLabel(name)}</span>
      <input name="${name}" value="${v}" class="swal2-input" style="margin:0">
    </label>
  `;
}

function collectForm(container){
  const data = {};
  const inputs = container.querySelectorAll("[name]");
  inputs.forEach(inp => {
    if (inp.type === "checkbox") data[inp.name] = !!inp.checked;
    else data[inp.name] = (inp.value ?? "").toString();
  });
  return data;
}

function setupUserPasswordUI(container){
  const show = container.querySelector('[name="__pwd_show"]');
  const p1 = container.querySelector('[name="__password"]');
  const p2 = container.querySelector('[name="__password2"]');
  if (!show || !p1 || !p2) return;
  show.addEventListener("change", () => {
    const t = show.checked ? "text" : "password";
    p1.type = t;
    p2.type = t;
  });
}

function setupMarcaPreview(container){
  const map = {
    logo_url: "#pv_logo",
    pdf_portada_url: "#pv_portada",
    pdf_cotizacion_url: "#pv_cot",
    pdf_terminos_url: "#pv_terms",
    pdf_banco_url: "#pv_bank",
  };
  const update = () => {
    for (const [field, sel] of Object.entries(map)){
      const inp = container.querySelector(`[name=\"${field}\"]`);
      const img = container.querySelector(sel);
      if (inp && img){
        img.src = inp.value || "";
      }
    }
  };
  container.addEventListener("input", (e) => {
    if (e.target && e.target.name && map[e.target.name]){
      update();
    }
  });
  update();
}

function coerceTypes(payload){
  // Ajuste simple: convierte "" a null y números a number cuando corresponde
  const out = { ...payload };
  for (const col of (META?.columns || [])){
    const k = col.column_name;
    if (!(k in out)) continue;

    if (k === META.pk) continue;

    const t = (col.data_type||"").toLowerCase();
    if (out[k] === "") out[k] = null;

    const isNum = t.includes("int") || t.includes("numeric") || t.includes("double") || t.includes("real") || t.includes("float");
    if (isNum && out[k] !== null && out[k] !== undefined && out[k] !== ""){
      const n = Number(out[k]);
      if (!Number.isNaN(n)) out[k] = n;
    }

    if (t.includes("bool") && typeof out[k] !== "boolean"){
      out[k] = String(out[k]) === "true";
    }
  }
  return out;
}

async function createItem(){
  const cols = (META.columns || []).filter(c => c.column_name !== META.pk);
  const wrap = document.createElement("div");
  wrap.style.display = "grid";
  wrap.style.gridTemplateColumns = "1fr 1fr";
  wrap.style.gap = "10px";

  for (const c of cols) wrap.insertAdjacentHTML("beforeend", fieldInputHTML(c, ""));

  if (ENTITY === "usuarios"){
    const extra = document.createElement("div");
    extra.style.gridColumn = "1 / -1";
    extra.innerHTML = `
      <div style="border-top:1px solid rgba(255,255,255,.15); margin-top:4px; padding-top:8px;">
        <div style="font-weight:900; margin-bottom:6px;">Clave de acceso</div>
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">
          <label style="display:flex; flex-direction:column; gap:6px;">
            <span style="font-weight:800">Nueva clave</span>
            <input name="__password" type="password" class="swal2-input" style="margin:0">
          </label>
          <label style="display:flex; flex-direction:column; gap:6px;">
            <span style="font-weight:800">Confirmar clave</span>
            <input name="__password2" type="password" class="swal2-input" style="margin:0">
          </label>
        </div>
        <label style="display:flex; align-items:center; gap:8px; margin-top:6px;">
          <input type="checkbox" name="__pwd_show">
          <span>Mostrar clave</span>
        </label>
        <div style="display:flex; gap:12px; margin-top:6px; font-size:12px;">
          <label style="display:flex; align-items:center; gap:6px;"><input type="checkbox" name="__send_email"> Enviar por correo</label>
          <label style="display:flex; align-items:center; gap:6px;"><input type="checkbox" name="__send_wa"> Enviar por WhatsApp</label>
        </div>
      </div>
    `;
    wrap.appendChild(extra);
  }

  if (ENTITY === "marcas"){
    const preview = document.createElement("div");
    preview.style.gridColumn = "1 / -1";
    preview.innerHTML = `
      <div style="border-top:1px solid rgba(255,255,255,.15); margin-top:4px; padding-top:8px;">
        <div style="font-weight:900; margin-bottom:6px;">Vista previa</div>
        <div style="display:grid; grid-template-columns:repeat(5, minmax(0,1fr)); gap:8px;">
          <img id="pv_logo" style="width:100%; height:80px; object-fit:contain; background:rgba(255,255,255,.05); border-radius:8px;">
          <img id="pv_portada" style="width:100%; height:80px; object-fit:cover; background:rgba(255,255,255,.05); border-radius:8px;">
          <img id="pv_cot" style="width:100%; height:80px; object-fit:cover; background:rgba(255,255,255,.05); border-radius:8px;">
          <img id="pv_terms" style="width:100%; height:80px; object-fit:cover; background:rgba(255,255,255,.05); border-radius:8px;">
          <img id="pv_bank" style="width:100%; height:80px; object-fit:cover; background:rgba(255,255,255,.05); border-radius:8px;">
        </div>
        <div class="muted" style="margin-top:6px; font-size:12px;">Pega URLs públicas (Drive directo funciona). Se verá miniatura arriba.</div>
      </div>
    `;
    wrap.appendChild(preview);
  }

  const r = await Swal.fire({
    title: `Crear · ${prettyEntity(ENTITY)}`,
    html: wrap,
    focusConfirm: false,
    showCancelButton: true,
    confirmButtonText: "Crear",
    cancelButtonText: "Cancelar",
    didOpen: () => {
      if (ENTITY === "usuarios") setupUserPasswordUI(wrap);
      if (ENTITY === "marcas") setupMarcaPreview(wrap);
    },
    preConfirm: () => {
      const data = coerceTypes(collectForm(wrap));
      if (ENTITY === "usuarios"){
        const p1 = (data.__password || "").trim();
        const p2 = (data.__password2 || "").trim();
        if (p1.length < 4) throw new Error("Clave mínima 4 caracteres");
        if (p1 !== p2) throw new Error("Las claves no coinciden");
        data.password = p1;
        data.__send_email = !!data.__send_email;
        data.__send_wa = !!data.__send_wa;
        delete data.__password;
        delete data.__password2;
        delete data.__pwd_show;
      }
      return data;
    }
  });
  if (!r.isConfirmed) return;

  const sendEmail = r.value?.__send_email;
  const sendWa = r.value?.__send_wa;
  delete r.value?.__send_email;
  delete r.value?.__send_wa;

  const resp = await apiJson(`/settings/${encodeURIComponent(ENTITY)}`, {
    method:"POST",
    body: JSON.stringify(r.value)
  });

  if (ENTITY === "usuarios" && r.value?.password){
    const id = resp?.id;
    if (sendEmail && id){
      try{
        await apiJson(`/settings/usuarios/${id}/reset_password?new_password=${encodeURIComponent(r.value.password)}&send_email=true`, {method:"POST"});
      }catch(_){}
    }
    if (sendWa){
      const tel = (r.value.telefono || r.value.phone || r.value.celular || "").toString().replace(/\\D/g,"");
      const usuario = r.value.email || r.value.username || r.value.nombre || id;
      if (tel){
        const msg = `Hola ${r.value.nombre || ""}, tu nueva clave es: ${r.value.password}\\nUsuario: ${usuario}`;
        window.open(`https://wa.me/${tel}?text=${encodeURIComponent(msg)}`, "_blank", "noopener,noreferrer");
      } else {
        Swal.fire("WhatsApp", "El usuario no tiene teléfono registrado.", "info");
      }
    }
  }
  await loadItems(true);
}

async function editItem(){
  if (!SELECTED){
    return Swal.fire({ icon:"warning", title:"Selecciona un registro" });
  }

  const cols = (META.columns || []).filter(c => c.column_name !== META.pk);
  const wrap = document.createElement("div");
  wrap.style.display = "grid";
  wrap.style.gridTemplateColumns = "1fr 1fr";
  wrap.style.gap = "10px";

  for (const c of cols) wrap.insertAdjacentHTML("beforeend", fieldInputHTML(c, SELECTED[c.column_name]));

  if (ENTITY === "usuarios"){
    const extra = document.createElement("div");
    extra.style.gridColumn = "1 / -1";
    extra.innerHTML = `
      <div style="border-top:1px solid rgba(255,255,255,.15); margin-top:4px; padding-top:8px;">
        <div style="font-weight:900; margin-bottom:6px;">Cambiar clave (opcional)</div>
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">
          <label style="display:flex; flex-direction:column; gap:6px;">
            <span style="font-weight:800">Nueva clave</span>
            <input name="__password" type="password" class="swal2-input" style="margin:0">
          </label>
          <label style="display:flex; flex-direction:column; gap:6px;">
            <span style="font-weight:800">Confirmar clave</span>
            <input name="__password2" type="password" class="swal2-input" style="margin:0">
          </label>
        </div>
        <label style="display:flex; align-items:center; gap:8px; margin-top:6px;">
          <input type="checkbox" name="__pwd_show">
          <span>Mostrar clave</span>
        </label>
        <div style="display:flex; gap:12px; margin-top:6px; font-size:12px;">
          <label style="display:flex; align-items:center; gap:6px;"><input type="checkbox" name="__send_email"> Enviar por correo</label>
          <label style="display:flex; align-items:center; gap:6px;"><input type="checkbox" name="__send_wa"> Enviar por WhatsApp</label>
        </div>
      </div>
    `;
    wrap.appendChild(extra);
  }

  if (ENTITY === "marcas"){
    const preview = document.createElement("div");
    preview.style.gridColumn = "1 / -1";
    preview.innerHTML = `
      <div style="border-top:1px solid rgba(255,255,255,.15); margin-top:4px; padding-top:8px;">
        <div style="font-weight:900; margin-bottom:6px;">Vista previa</div>
        <div style="display:grid; grid-template-columns:repeat(5, minmax(0,1fr)); gap:8px;">
          <img id="pv_logo" style="width:100%; height:80px; object-fit:contain; background:rgba(255,255,255,.05); border-radius:8px;">
          <img id="pv_portada" style="width:100%; height:80px; object-fit:cover; background:rgba(255,255,255,.05); border-radius:8px;">
          <img id="pv_cot" style="width:100%; height:80px; object-fit:cover; background:rgba(255,255,255,.05); border-radius:8px;">
          <img id="pv_terms" style="width:100%; height:80px; object-fit:cover; background:rgba(255,255,255,.05); border-radius:8px;">
          <img id="pv_bank" style="width:100%; height:80px; object-fit:cover; background:rgba(255,255,255,.05); border-radius:8px;">
        </div>
        <div class="muted" style="margin-top:6px; font-size:12px;">Pega URLs públicas (Drive directo funciona). Se verá miniatura arriba.</div>
      </div>
    `;
    wrap.appendChild(preview);
  }

  const r = await Swal.fire({
    title: `Editar · ${prettyEntity(ENTITY)}`,
    html: wrap,
    focusConfirm: false,
    showCancelButton: true,
    confirmButtonText: "Guardar",
    cancelButtonText: "Cancelar",
    didOpen: () => {
      if (ENTITY === "usuarios") setupUserPasswordUI(wrap);
      if (ENTITY === "marcas") setupMarcaPreview(wrap);
    },
    preConfirm: () => {
      const data = coerceTypes(collectForm(wrap));
      if (ENTITY === "usuarios"){
        const p1 = (data.__password || "").trim();
        const p2 = (data.__password2 || "").trim();
        if (p1 || p2){
          if (p1.length < 4) throw new Error("Clave mínima 4 caracteres");
          if (p1 !== p2) throw new Error("Las claves no coinciden");
          data.password = p1;
        }
        data.__send_email = !!data.__send_email;
        data.__send_wa = !!data.__send_wa;
        delete data.__password;
        delete data.__password2;
        delete data.__pwd_show;
      }
      return data;
    }
  });
  if (!r.isConfirmed) return;

  const id = SELECTED[META.pk];
  const sendEmail = r.value?.__send_email;
  const sendWa = r.value?.__send_wa;
  delete r.value?.__send_email;
  delete r.value?.__send_wa;

  // Preferido: PUT /settings/{entity}/{id}
  try{
    await apiJson(`/settings/${encodeURIComponent(ENTITY)}/${encodeURIComponent(id)}`, {
      method:"PUT",
      body: JSON.stringify(r.value)
    });
  }catch(e){
    // Fallback: PUT /settings/{entity} con pk en body
    await apiJson(`/settings/${encodeURIComponent(ENTITY)}`, {
      method:"PUT",
      body: JSON.stringify({ ...r.value, [META.pk]: id })
    });
  }

  if (ENTITY === "usuarios" && r.value?.password){
    if (sendEmail){
      try{
        await apiJson(`/settings/usuarios/${id}/reset_password?new_password=${encodeURIComponent(r.value.password)}&send_email=true`, {method:"POST"});
      }catch(_){}
    }
    if (sendWa){
      const tel = (r.value.telefono || r.value.phone || r.value.celular || "").toString().replace(/\\D/g,"");
      const usuario = r.value.email || r.value.username || r.value.nombre || id;
      if (tel){
        const msg = `Hola ${r.value.nombre || ""}, tu nueva clave es: ${r.value.password}\\nUsuario: ${usuario}`;
        window.open(`https://wa.me/${tel}?text=${encodeURIComponent(msg)}`, "_blank", "noopener,noreferrer");
      } else {
        Swal.fire("WhatsApp", "El usuario no tiene teléfono registrado.", "info");
      }
    }
  }
  await loadItems(true);
}

async function deleteItem(){
  if (!SELECTED){
    return Swal.fire({ icon:"warning", title:"Selecciona un registro" });
  }
  const id = SELECTED[META.pk];

  const ok = await Swal.fire({
    icon:"warning",
    title:"¿Eliminar?",
    text:`Se eliminará ID ${id}.`,
    showCancelButton:true,
    confirmButtonText:"Eliminar",
    cancelButtonText:"Cancelar",
    reverseButtons:true
  });
  if (!ok.isConfirmed) return;

  try{
    await apiJson(`/settings/${encodeURIComponent(ENTITY)}/${encodeURIComponent(id)}`, { method:"DELETE" });
  }catch(_){
    await apiJson(`/settings/${encodeURIComponent(ENTITY)}`, {
      method:"DELETE",
      body: JSON.stringify({ [META.pk]: id })
    });
  }

  await loadItems(true);
}

(function init(){
  if (!getToken()){
    location.href = "/web/login.html";
    return;
  }

  const Swal = window.Swal;
  if (!Swal){
    alert("Falta SweetAlert2 en settings_entity.html");
    return;
  }

  loadMeta()
    .then(() => loadItems(true))
    .catch(err => {
      msgEl.textContent = `Error: ${err.message || err}`;
    });

  qs("#seQ").addEventListener("input", renderTable);
  qs("#btnReload").onclick = () => loadItems(true);
  qs("#btnMore").onclick = () => loadItems(false);
  qs("#btnCreate").onclick = createItem;
  qs("#btnEdit").onclick = editItem;
  qs("#btnDelete").onclick = deleteItem;

  // tema desde parent
  window.addEventListener("message", (ev) => {
    if (ev.data?.type === "theme"){
      document.documentElement.classList.toggle("light", ev.data.mode === "light");
    }
  });
})();
