const qs = (s, el=document) => el.querySelector(s);

function token(){
  return window.parent?.GD?.getToken?.() || localStorage.getItem("token") || sessionStorage.getItem("token") || "";
}
function headers(extra={}){
  const t = token();
  return t ? { ...extra, Authorization:`Bearer ${t}` } : extra;
}
async function jfetch(url, opts={}){
  const r = await fetch(url, { ...opts, headers: headers({ "Content-Type":"application/json", ...(opts.headers||{}) }) });
  const t = await r.text();
  let j = null; try{ j = t ? JSON.parse(t) : null; }catch{ j = { raw:t }; }
  if (!r.ok) throw new Error(j?.detail || j?.message || t || `HTTP ${r.status}`);
  return j;
}

const PALETTE = ["#22c55e","#60a5fa","#a855f7","#f59e0b","#ef4444","#14b8a6","#e879f9","#f97316","#84cc16","#06b6d4"];

let CATS = null;
let LEADS = [];

function unwrapCats(x){
  return x?.catalogos || x?.data?.catalogos || x;
}

function arrPick(obj, keys){
  for (const k of keys){
    const v = obj?.[k];
    if (Array.isArray(v)) return v;
  }
  return [];
}

function catsEstados(){
  const c = unwrapCats(CATS) || {};
  return arrPick(c, ["estados_lead","estados","estadosLead"]);
}
function catsMarcas(){
  const c = unwrapCats(CATS) || {};
  return arrPick(c, ["marcas","marca","marcas_list"]);
}
function catsComunas(){
  const c = unwrapCats(CATS) || {};
  return arrPick(c, ["comunas","comuna"]);
}
function catsTiposCliente(){
  const c = unwrapCats(CATS) || {};
  return arrPick(c, ["tipos_cliente","tipocliente","tipo_cliente","tiposCliente"]);
}

function byId(arr, idKey){
  const m = new Map();
  for (const x of arr) m.set(x[idKey], x);
  return m;
}

async function loadCatalogos(){
  try{
    return await jfetch("/leads/catalogos");
  }catch{
    return await jfetch("/leads/catalogs");
  }
}

async function fallbackIfMissing(){
  // Si /leads/catalogos no trae arrays por el nesting o por compat, tomamos /settings/*
  const c = unwrapCats(CATS) || {};
  if (!catsEstados().length){
    try{
      const r = await jfetch("/settings/estados_lead?limit=500");
      c.estados_lead = r.items || [];
    }catch{}
  }
  if (!catsMarcas().length){
    try{
      const r = await jfetch("/settings/marcas?limit=500");
      c.marcas = r.items || [];
    }catch{}
  }
  if (!catsComunas().length){
    try{
      const r = await jfetch("/settings/comunas?limit=2000");
      c.comunas = r.items || [];
    }catch{}
  }
  if (!catsTiposCliente().length){
    try{
      const r = await jfetch("/settings/tipos_cliente?limit=500");
      c.tipos_cliente = r.items || [];
    }catch{}
  }
  CATS = { ok:true, catalogos:c };
}

async function loadLeads(){
  LEADS = await jfetch("/leads");
  return LEADS;
}

function estadoMap(){
  const est = catsEstados().slice();
  est.sort((a,b) => (a.orden??999)-(b.orden??999));
  est.forEach((e,i) => { if (!e.color) e.color = PALETTE[i % PALETTE.length]; });
  return byId(est, "id_estado");
}
function marcaMap(){ return byId(catsMarcas(), "id_marca"); }
function comunaMap(){ return byId(catsComunas(), "id_comuna"); }
function tipoClienteMap(){ return byId(catsTiposCliente(), "id_tipo_cliente"); }

function filt(){
  const q = (qs("#q").value || "").trim().toLowerCase();
  if (!q) return LEADS.slice();
  return LEADS.filter(x => {
    const s = `${x.nombre_cliente||""} ${x.telefono||""} ${x.email||""} ${x.direccion||""} ${x.codigo_cliente||""}`.toLowerCase();
    return s.includes(q);
  });
}

function money(n){
  const v = Number(n||0);
  return v.toLocaleString("es-CL",{ style:"currency", currency:"CLP", maximumFractionDigits:0 });
}

function setView(mode){
  qs("#btnKanban").classList.toggle("active", mode==="kanban");
  qs("#btnTabla").classList.toggle("active", mode==="tabla");
  qs("#wrapKanban").classList.toggle("on", mode==="kanban");
  qs("#wrapTabla").classList.toggle("on", mode==="tabla");
}

function normalize(s){ return (s||"").toString().trim().toLowerCase(); }
function isConfirmStateName(name){
  const n = normalize(name);
  return n.includes("confirm");
}

function render(){
  const estM = estadoMap();
  const marM = marcaMap();
  const comM = comunaMap();

  const rows = filt();
  qs("#count").textContent = `Leads: ${rows.length}`;

  renderTabla(rows, estM, marM, comM);
  renderKanban(rows, estM, marM, comM);
}

function renderTabla(rows, estM, marM, comM){
  const tb = qs("#tb");
  tb.innerHTML = "";
  for (const l of rows){
    const tr = document.createElement("tr");
    const est = estM.get(l.id_estado);
    tr.innerHTML = `
      <td>${l.nombre_cliente||""}</td>
      <td>${l.fecha_evento||""}</td>
      <td>${comM.get(l.id_comuna)?.nombre || ""}</td>
      <td>${marM.get(l.id_marca)?.nombre || ""}</td>
      <td>${est?.nombre || "—"}</td>
      <td>${money(l.monto_cotizado)}</td>
    `;
    tr.onclick = () => openLeadModal(l);
    tb.appendChild(tr);
  }
}

function renderKanban(rows, estM, marM, comM){
  const board = qs("#board");
  board.innerHTML = "";

  const estados = Array.from(estM.values()).sort((a,b)=> (a.orden??999)-(b.orden??999));
  if (!estados.length){
    board.innerHTML = `<div style="opacity:.8; font-weight:900">No hay catálogo de estados. Revisa /leads/catalogos o /settings/estados_lead.</div>`;
    return;
  }

  const grouped = new Map();
  for (const e of estados) grouped.set(e.id_estado, []);
  for (const l of rows){
    if (!grouped.has(l.id_estado)) grouped.set(l.id_estado, []);
    grouped.get(l.id_estado).push(l);
  }

  for (const e of estados){
    const col = document.createElement("div");
    col.className = "col";
    col.dataset.estado = e.id_estado;

    col.innerHTML = `
      <h3>
        <span style="display:flex; gap:10px; align-items:center">
          <span style="width:10px;height:10px;border-radius:50%;background:${e.color}"></span>
          <span>${e.nombre}</span>
        </span>
        <span class="badge">${(grouped.get(e.id_estado)||[]).length}</span>
      </h3>
      <div class="drop"></div>
    `;

    const drop = col.querySelector(".drop");
    drop.ondragover = (ev) => { ev.preventDefault(); };
    drop.ondrop = async (ev) => {
      ev.preventDefault();
      const idLead = ev.dataTransfer.getData("text/id");
      const lead = LEADS.find(x => String(x.id_lead) === String(idLead));
      if (!lead) return;
      await changeEstado(lead, e, estM);
    };

    const list = (grouped.get(e.id_estado)||[]);
    for (const l of list){
      const card = document.createElement("div");
      card.className = "card";
      card.draggable = true;
      card.ondragstart = (ev) => ev.dataTransfer.setData("text/id", String(l.id_lead));
      card.onclick = () => openLeadModal(l);

      const comuna = comM.get(l.id_comuna)?.nombre || "";
      const marca = marM.get(l.id_marca)?.nombre || "";

      card.innerHTML = `
        <div class="t1">${l.nombre_cliente||""}</div>
        <div class="t2">${(l.fecha_evento||"")}${comuna ? " · "+comuna : ""}</div>
        <div class="t3">
          <span>🏷️ ${marca}</span>
          <span>💰 ${money(l.monto_cotizado)}</span>
          ${l.telefono ? `<span>📞 ${l.telefono}</span>`:""}
        </div>
      `;
      drop.appendChild(card);
    }

    board.appendChild(col);
  }
}

async function reloadAndToast(msg){
  await loadLeads();
  render();
  Swal.fire({ toast:true, position:"top-end", timer:2200, showConfirmButton:false, icon:"success", title:msg });
}

async function tryCotizacionHint(id_lead){
  const candidates = [
    `/cotizaciones?id_lead=${id_lead}`,
    `/cotizaciones?lead_id=${id_lead}`,
    `/cotizaciones/historial?id_lead=${id_lead}`,
    `/quotes?id_lead=${id_lead}`,
  ];
  for (const u of candidates){
    try{
      const r = await jfetch(u);
      const items = Array.isArray(r) ? r : (r.items || r.cotizaciones || r.data || []);
      if (items?.length){
        const first = items[0];
        const total = first.total || first.monto_total || first.bruto || first.total_bruto || first.monto || 0;
        return `Cotización detectada (${items.length}). Total aprox: ${money(total)}.`;
      }
    }catch{}
  }
  return "Sin cotización detectada (igual puedes definir ops y pre-montaje).";
}

async function changeEstado(lead, estadoNewObj, estM){
  const oldName = estM.get(lead.id_estado)?.nombre || "—";
  const newName = estadoNewObj?.nombre || "—";

  if (lead.id_estado === estadoNewObj.id_estado) return;

  const confirm = isConfirmStateName(newName);
  let pre_ops = lead.pre_ops ?? 0;
  let pre_montaje_text = lead.pre_montaje_text ?? "";
  let movement_comment = "";

  if (confirm){
    const hint = await tryCotizacionHint(lead.id_lead);

    const r = await Swal.fire({
      title:"Confirmado → Pre-agenda",
      html: `
        <div style="text-align:left">
          <div style="opacity:.78; font-weight:900; margin-bottom:8px">${hint}</div>

          <label style="display:block; margin:8px 0">
            <div style="font-weight:900; margin-bottom:4px">Operadores (editable)</div>
            <input id="ops" type="number" value="${pre_ops||0}" style="width:100%; padding:10px 12px; border-radius:12px; border:1px solid rgba(0,0,0,.12)" />
          </label>

          <label style="display:block; margin:8px 0">
            <div style="font-weight:900; margin-bottom:4px">Pre-montaje (editable)</div>
            <input id="pm" type="text" value="${(pre_montaje_text||"").toString().replaceAll('"',"&quot;")}" style="width:100%; padding:10px 12px; border-radius:12px; border:1px solid rgba(0,0,0,.12)" />
          </label>

          <label style="display:block; margin:8px 0">
            <div style="font-weight:900; margin-bottom:4px">Comentario obligatorio</div>
            <textarea id="mv_comment" style="width:100%; min-height:90px; padding:10px 12px; border-radius:12px; border:1px solid rgba(0,0,0,.12)" placeholder="Qué hizo el ejecutivo, respuesta del cliente o próximo paso"></textarea>
          </label>

          <div style="opacity:.75; margin-top:10px">
            Queda pendiente de validación (SIMON / OSCAR) para subir a Google Calendar.
          </div>
        </div>
      `,
      showCancelButton:true,
      confirmButtonText:"Guardar y mover",
      confirmButtonColor:"#22c55e",
      cancelButtonText:"Cancelar",
      preConfirm: () => {
        const txt = String(document.getElementById("mv_comment")?.value || "").trim();
        if (txt.length < 8){
          Swal.showValidationMessage("Escribe un comentario claro. Mínimo 8 caracteres.");
          return false;
        }
        return {
          pre_ops: Number(document.getElementById("ops").value || 0),
          pre_montaje_text: document.getElementById("pm").value || "",
          movement_comment: txt,
        };
      }
    });
    if (!r.isConfirmed) return;
    pre_ops = r.value.pre_ops;
    pre_montaje_text = r.value.pre_montaje_text;
    movement_comment = r.value.movement_comment;
  }else{
    const r = await Swal.fire({
      title:"Mover lead",
      html: `
        <div style="font-weight:900;margin-bottom:8px">${oldName} → ${newName}</div>
        <textarea id="mv_comment" style="width:100%; min-height:100px; padding:10px 12px; border-radius:12px; border:1px solid rgba(0,0,0,.12)" placeholder="Qué se hizo, respuesta del cliente o próximo paso"></textarea>
      `,
      icon:"question",
      showCancelButton:true,
      confirmButtonText:"Mover",
      confirmButtonColor:"#22c55e",
      cancelButtonText:"Cancelar",
      preConfirm: () => {
        const txt = String(document.getElementById("mv_comment")?.value || "").trim();
        if (txt.length < 8){
          Swal.showValidationMessage("Escribe un comentario claro. Mínimo 8 caracteres.");
          return false;
        }
        return txt;
      }
    });
    if (!r.isConfirmed) return;
    movement_comment = String(r.value || "").trim();
  }

  // intenta estado_ex, fallback estado
  try{
    await jfetch(`/leads/${lead.id_lead}/estado_ex`, {
      method:"POST",
      body: JSON.stringify({ id_estado: estadoNewObj.id_estado, pre_ops, pre_montaje_text, movement_comment })
    });
    await reloadAndToast(`${lead.nombre_cliente}: ${oldName} → ${newName}`);
  }catch(e1){
    try{
      await jfetch(`/leads/${lead.id_lead}/estado`, {
        method:"PUT",
        body: JSON.stringify({ id_estado: estadoNewObj.id_estado, pre_ops, pre_montaje_text, movement_comment })
      });
      await reloadAndToast(`${lead.nombre_cliente}: ${oldName} → ${newName}`);
    }catch(e2){
      Swal.fire({ icon:"error", title:"No se pudo cambiar estado", text:String(e2.message||e2) });
    }
  }
}

function waLink(phone, msg){
  const p = (phone||"").replace(/[^\d+]/g,"");
  const u = new URL("https://wa.me/" + p.replace("+",""));
  if (msg) u.searchParams.set("text", msg);
  return u.toString();
}

function marcaCodeFromName(name){
  const s = (name||"").toString().trim().toUpperCase().replace(/[^A-ZÁÉÍÓÚÑ]/g,"");
  return (s.slice(0,3) || "CLI");
}
function initialsFromClient(name){
  const parts = (name||"").toString().trim().split(/\s+/).filter(Boolean);
  const a = parts[0]?.[0] || "X";
  const b = parts[1]?.[0] || parts[parts.length-1]?.[0] || "X";
  return (a+b).toUpperCase();
}
function nextCorrelativo(prefix){
  // busca códigos existentes tipo PREFIX-##
  let max = 0;
  for (const l of LEADS){
    const c = (l.codigo_cliente||"").toString().toUpperCase();
    if (c.startsWith(prefix+"-")){
      const n = parseInt(c.split("-").pop(),10);
      if (!Number.isNaN(n)) max = Math.max(max, n);
    }
  }
  return String(max+1).padStart(2,"0");
}
function generateCodigoCliente(id_marca, nombre_cliente){
  const mar = marcaMap().get(id_marca)?.nombre || "CLIENTE";
  const m = marcaCodeFromName(mar);
  const ini = initialsFromClient(nombre_cliente);
  const pref = `${m}-${ini}`;
  const cor = nextCorrelativo(pref);
  return `${pref}-${cor}`;
}

async function openLeadModal(lead){
  const estM = estadoMap();
  const marM = marcaMap();
  const comM = comunaMap();
  const tcM  = tipoClienteMap();

  const estados = Array.from(estM.values()).sort((a,b)=> (a.orden??999)-(b.orden??999));
  const marcas = Array.from(marM.values()).sort((a,b)=> (a.nombre||"").localeCompare(b.nombre||""));
  const comunas = Array.from(comM.values()).sort((a,b)=> (a.nombre||"").localeCompare(b.nombre||""));
  const tipos = Array.from(tcM.values()).sort((a,b)=> (a.nombre||"").localeCompare(b.nombre||""));

  const html = `
    <div style="text-align:left">
      <div style="display:flex; gap:10px; flex-wrap:wrap; margin-bottom:10px">
        <button id="bEdit" class="swal2-styled" style="background:#a855f7">Editar</button>
        <button id="bPhone" class="swal2-styled" style="background:#64748b">Teléfono</button>
        <button id="bWA" class="swal2-styled" style="background:#22c55e">WhatsApp</button>
        <button id="bCot" class="swal2-styled" style="background:#3b82f6">Cotizar</button>
      </div>

      <label style="display:block; margin:8px 0">
        <div style="font-weight:900; margin-bottom:4px">Cliente</div>
        <input id="nombre" type="text" value="${(lead.nombre_cliente||"").replaceAll('"',"&quot;")}" style="width:100%; padding:10px 12px; border-radius:12px; border:1px solid rgba(0,0,0,.12)" />
      </label>

      <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px">
        <label style="display:block; margin:8px 0">
          <div style="font-weight:900; margin-bottom:4px">Teléfono</div>
          <input id="tel" type="text" value="${(lead.telefono||"").replaceAll('"',"&quot;")}" style="width:100%; padding:10px 12px; border-radius:12px; border:1px solid rgba(0,0,0,.12)" />
        </label>
        <label style="display:block; margin:8px 0">
          <div style="font-weight:900; margin-bottom:4px">Email</div>
          <input id="mail" type="text" value="${(lead.email||"").replaceAll('"',"&quot;")}" style="width:100%; padding:10px 12px; border-radius:12px; border:1px solid rgba(0,0,0,.12)" />
        </label>
      </div>

      <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px">
        <label style="display:block; margin:8px 0">
          <div style="font-weight:900; margin-bottom:4px">Marca</div>
          <select id="marca" style="width:100%; padding:10px 12px; border-radius:12px">
            ${marcas.map(m => `<option value="${m.id_marca}" ${m.id_marca===lead.id_marca?"selected":""}>${m.nombre}</option>`).join("")}
          </select>
        </label>
        <label style="display:block; margin:8px 0">
          <div style="font-weight:900; margin-bottom:4px">Comuna</div>
          <select id="comuna" style="width:100%; padding:10px 12px; border-radius:12px">
            ${comunas.map(c => `<option value="${c.id_comuna}" ${c.id_comuna===lead.id_comuna?"selected":""}>${c.nombre}</option>`).join("")}
          </select>
        </label>
      </div>

      <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px">
        <label style="display:block; margin:8px 0">
          <div style="font-weight:900; margin-bottom:4px">Tipo Cliente</div>
          <select id="tc" style="width:100%; padding:10px 12px; border-radius:12px">
            ${tipos.map(t => `<option value="${t.id_tipo_cliente}" ${t.id_tipo_cliente===lead.id_tipo_cliente?"selected":""}>${t.nombre}</option>`).join("")}
          </select>
        </label>
        <label style="display:block; margin:8px 0">
          <div style="font-weight:900; margin-bottom:4px">Fecha evento</div>
          <input id="fecha" type="date" value="${lead.fecha_evento||""}" style="width:100%; padding:10px 12px; border-radius:12px; border:1px solid rgba(0,0,0,.12)" />
        </label>
      </div>

      <label style="display:block; margin:8px 0">
        <div style="font-weight:900; margin-bottom:4px">Estado</div>
        <select id="estado" style="width:100%; padding:10px 12px; border-radius:12px">
          ${estados.map(e => `<option value="${e.id_estado}" ${e.id_estado===lead.id_estado?"selected":""}>${e.nombre}</option>`).join("")}
        </select>
      </label>

      <label style="display:block; margin:8px 0">
        <div style="font-weight:900; margin-bottom:4px">Comentario cambio de estado</div>
        <textarea id="mv_comment" style="width:100%; min-height:78px; padding:10px 12px; border-radius:12px; border:1px solid rgba(0,0,0,.12)" placeholder="Solo obligatorio si cambias el estado"></textarea>
      </label>

      <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px">
        <label style="display:block; margin:8px 0">
          <div style="font-weight:900; margin-bottom:4px">Operadores (pre)</div>
          <input id="ops" type="number" value="${lead.pre_ops ?? 0}" style="width:100%; padding:10px 12px; border-radius:12px; border:1px solid rgba(0,0,0,.12)" />
        </label>
        <label style="display:block; margin:8px 0">
          <div style="font-weight:900; margin-bottom:4px">Código cliente</div>
          <input id="cod" type="text" value="${(lead.codigo_cliente||"").replaceAll('"',"&quot;")}" style="width:100%; padding:10px 12px; border-radius:12px; border:1px solid rgba(0,0,0,.12)" />
        </label>
      </div>

      <label style="display:block; margin:8px 0">
        <div style="font-weight:900; margin-bottom:4px">Pre-montaje</div>
        <input id="pm" type="text" value="${(lead.pre_montaje_text||"").toString().replaceAll('"',"&quot;")}" style="width:100%; padding:10px 12px; border-radius:12px; border:1px solid rgba(0,0,0,.12)" />
      </label>

      <label style="display:block; margin:8px 0">
        <div style="font-weight:900; margin-bottom:4px">Dirección</div>
        <input id="dir" type="text" value="${(lead.direccion||"").replaceAll('"',"&quot;")}" style="width:100%; padding:10px 12px; border-radius:12px; border:1px solid rgba(0,0,0,.12)" />
      </label>

      <label style="display:block; margin:8px 0">
        <div style="font-weight:900; margin-bottom:4px">Notas</div>
        <input id="notas" type="text" value="${(lead.notas||"").replaceAll('"',"&quot;")}" style="width:100%; padding:10px 12px; border-radius:12px; border:1px solid rgba(0,0,0,.12)" />
      </label>
    </div>
  `;

  await Swal.fire({
    title:"Lead",
    html,
    width: 760,
    showCancelButton:true,
    confirmButtonText:"Guardar",
    confirmButtonColor:"#22c55e",
    cancelButtonText:"Cerrar",
    didOpen: (pop) => {
      pop.querySelector("#bPhone").onclick = () => { if (lead.telefono) window.open(`tel:${lead.telefono}`, "_blank"); };
      pop.querySelector("#bWA").onclick = () => { if (lead.telefono) window.open(waLink(lead.telefono, `Hola ${lead.nombre_cliente||""} 👋`), "_blank"); };
      pop.querySelector("#bCot").onclick = () => window.open(`/web/views/cotizador.html?id_lead=${lead.id_lead}`, "_blank");
      pop.querySelector("#bEdit").onclick = () => Swal.fire({ toast:true, position:"top-end", timer:1300, showConfirmButton:false, icon:"info", title:"Edita aquí mismo y guarda" });
    },
    preConfirm: async () => {
      const statusChanged = Number(document.getElementById("estado").value) !== Number(lead.id_estado || 0);
      const movement_comment = String(document.getElementById("mv_comment")?.value || "").trim();
      if (statusChanged && movement_comment.length < 8){
        Swal.showValidationMessage("Para cambiar el estado debes escribir un comentario claro. Mínimo 8 caracteres.");
        return false;
      }
      const payload = {
        nombre_cliente: document.getElementById("nombre").value,
        telefono: document.getElementById("tel").value,
        email: document.getElementById("mail").value,
        id_marca: Number(document.getElementById("marca").value),
        id_comuna: Number(document.getElementById("comuna").value),
        id_tipo_cliente: Number(document.getElementById("tc").value),
        fecha_evento: document.getElementById("fecha").value,
        id_estado: Number(document.getElementById("estado").value),
        pre_ops: Number(document.getElementById("ops").value || 0),
        pre_montaje_text: document.getElementById("pm").value || "",
        codigo_cliente: document.getElementById("cod").value || "",
        direccion: document.getElementById("dir").value || "",
        notas: document.getElementById("notas").value || "",
        movement_comment: movement_comment || null,
      };

      // 1) update full si existe
      try{
        await jfetch(`/leads/${lead.id_lead}`, { method:"PUT", body: JSON.stringify(payload) });
        return payload;
      }catch{
        // 2) al menos estado_ex + preagenda
        await jfetch(`/leads/${lead.id_lead}/estado_ex`, {
          method:"POST",
          body: JSON.stringify({ id_estado: payload.id_estado, pre_ops: payload.pre_ops, pre_montaje_text: payload.pre_montaje_text, movement_comment: payload.movement_comment })
        });
        return payload;
      }
    }
  });

  await reloadAndToast("Lead actualizado");
}

async function createLead(){
  const marM = marcaMap();
  const comM = comunaMap();
  const tcM  = tipoClienteMap();
  const estM = estadoMap();

  const marcas = Array.from(marM.values()).sort((a,b)=> (a.nombre||"").localeCompare(b.nombre||""));
  const comunas = Array.from(comM.values()).sort((a,b)=> (a.nombre||"").localeCompare(b.nombre||""));
  const tipos = Array.from(tcM.values()).sort((a,b)=> (a.nombre||"").localeCompare(b.nombre||""));
  const estados = Array.from(estM.values()).sort((a,b)=> (a.orden??999)-(b.orden??999));

  const defaultMarca = marcas[0]?.id_marca ?? 0;
  const defaultComuna = comunas[0]?.id_comuna ?? 0;
  const defaultTC = tipos[0]?.id_tipo_cliente ?? 1;
  const defaultEstado = estados[0]?.id_estado ?? 0;

  const html = `
    <div style="text-align:left">
      <label style="display:block; margin:8px 0">
        <div style="font-weight:900; margin-bottom:4px">Cliente</div>
        <input id="n" type="text" style="width:100%; padding:10px 12px; border-radius:12px; border:1px solid rgba(0,0,0,.12)" />
      </label>

      <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px">
        <label style="display:block; margin:8px 0">
          <div style="font-weight:900; margin-bottom:4px">Teléfono</div>
          <input id="t" type="text" style="width:100%; padding:10px 12px; border-radius:12px; border:1px solid rgba(0,0,0,.12)" />
        </label>
        <label style="display:block; margin:8px 0">
          <div style="font-weight:900; margin-bottom:4px">Email</div>
          <input id="e" type="text" style="width:100%; padding:10px 12px; border-radius:12px; border:1px solid rgba(0,0,0,.12)" />
        </label>
      </div>

      <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px">
        <label style="display:block; margin:8px 0">
          <div style="font-weight:900; margin-bottom:4px">Marca</div>
          <select id="m" style="width:100%; padding:10px 12px; border-radius:12px">
            ${marcas.map(x=>`<option value="${x.id_marca}" ${x.id_marca===defaultMarca?"selected":""}>${x.nombre}</option>`).join("")}
          </select>
        </label>
        <label style="display:block; margin:8px 0">
          <div style="font-weight:900; margin-bottom:4px">Comuna</div>
          <select id="c" style="width:100%; padding:10px 12px; border-radius:12px">
            ${comunas.map(x=>`<option value="${x.id_comuna}" ${x.id_comuna===defaultComuna?"selected":""}>${x.nombre}</option>`).join("")}
          </select>
        </label>
      </div>

      <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px">
        <label style="display:block; margin:8px 0">
          <div style="font-weight:900; margin-bottom:4px">Tipo Cliente</div>
          <select id="tc" style="width:100%; padding:10px 12px; border-radius:12px">
            ${tipos.map(x=>`<option value="${x.id_tipo_cliente}" ${x.id_tipo_cliente===defaultTC?"selected":""}>${x.nombre}</option>`).join("")}
          </select>
        </label>
        <label style="display:block; margin:8px 0">
          <div style="font-weight:900; margin-bottom:4px">Fecha evento</div>
          <input id="f" type="date" style="width:100%; padding:10px 12px; border-radius:12px; border:1px solid rgba(0,0,0,.12)" />
        </label>
      </div>

      <label style="display:block; margin:8px 0">
        <div style="font-weight:900; margin-bottom:4px">Estado inicial</div>
        <select id="st" style="width:100%; padding:10px 12px; border-radius:12px">
          ${estados.map(x=>`<option value="${x.id_estado}" ${x.id_estado===defaultEstado?"selected":""}>${x.nombre}</option>`).join("")}
        </select>
      </label>

      <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px">
        <label style="display:block; margin:8px 0">
          <div style="font-weight:900; margin-bottom:4px">Código cliente</div>
          <input id="cod" type="text" readonly style="width:100%; padding:10px 12px; border-radius:12px; border:1px solid rgba(0,0,0,.12); background:rgba(0,0,0,.04)" />
        </label>
        <button id="regen" type="button" class="swal2-styled" style="background:#64748b; height:42px; align-self:end">Regenerar</button>
      </div>

      <label style="display:block; margin:8px 0">
        <div style="font-weight:900; margin-bottom:4px">Dirección</div>
        <input id="dir" type="text" style="width:100%; padding:10px 12px; border-radius:12px; border:1px solid rgba(0,0,0,.12)" />
      </label>

      <label style="display:block; margin:8px 0">
        <div style="font-weight:900; margin-bottom:4px">Plataforma</div>
        <select id="pl" style="width:100%; padding:10px 12px; border-radius:12px">
          <option value="FORMULARIO">FORMULARIO</option>
          <option value="MANUAL" selected>MANUAL</option>
          <option value="WHATSAPP">WHATSAPP</option>
          <option value="INSTAGRAM">INSTAGRAM</option>
        </select>
      </label>

      <label style="display:block; margin:8px 0">
        <div style="font-weight:900; margin-bottom:4px">Notas</div>
        <input id="x" type="text" style="width:100%; padding:10px 12px; border-radius:12px; border:1px solid rgba(0,0,0,.12)" />
      </label>
    </div>
  `;

  const r = await Swal.fire({
    title:"Crear Lead",
    html,
    showCancelButton:true,
    confirmButtonText:"Crear",
    confirmButtonColor:"#22c55e",
    didOpen: () => {
      const recalc = () => {
        const idm = Number(document.getElementById("m").value);
        const nom = document.getElementById("n").value;
        document.getElementById("cod").value = generateCodigoCliente(idm, nom);
      };
      document.getElementById("n").addEventListener("input", recalc);
      document.getElementById("m").addEventListener("change", recalc);
      document.getElementById("regen").onclick = recalc;
      recalc();
    },
    preConfirm: () => ({
      nombre_cliente: document.getElementById("n").value,
      telefono: document.getElementById("t").value,
      email: document.getElementById("e").value,
      id_marca: Number(document.getElementById("m").value),
      id_comuna: Number(document.getElementById("c").value),
      id_tipo_cliente: Number(document.getElementById("tc").value),
      fecha_evento: document.getElementById("f").value,
      id_estado: Number(document.getElementById("st").value),
      codigo_cliente: document.getElementById("cod").value,
      direccion: document.getElementById("dir").value,
      plataforma: document.getElementById("pl").value,
      notas: document.getElementById("x").value
    })
  });

  if (!r.isConfirmed) return;

  try{
    await jfetch("/leads", { method:"POST", body: JSON.stringify(r.value) });
    await reloadAndToast("Lead creado");
  }catch(e){
    Swal.fire({ icon:"error", title:"No se pudo crear", text:String(e.message||e) });
  }
}

async function boot(){
  CATS = await loadCatalogos();
  await fallbackIfMissing();

  await loadLeads();
  render();

  qs("#q").addEventListener("input", render);
  qs("#btnKanban").onclick = () => setView("kanban");
  qs("#btnTabla").onclick = () => setView("tabla");
  qs("#btnNew").onclick = createLead;
}

boot();
