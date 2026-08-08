function apiBase(entidad){ return `/settings/${entidad}`; }

async function fetchList(entidad){
  try{
    const r = await fetch(apiBase(entidad));
    if(!r.ok) throw new Error("no api");
    return await r.json();
  }catch(_){
    // fallback para avanzar si el backend aún no está
    const seed = {
      usuarios:[{id:1,email:"admin@crm.local",rol:"admin",activo:true}],
      comunas:[{id:1,nombre:"Santiago",activo:true}],
      cartas:[{id:1,nombre:"Presentación",activo:true}],
      marcas:[{id:1,nombre:"Genérica",activo:true}],
      estados:[{id:1,nombre:"Nuevo",activo:true}]
    };
    return seed[entidad] || [];
  }
}

async function saveItem(entidad,item){
  const method = item.id ? "PUT" : "POST";
  const url = item.id ? `${apiBase(entidad)}/${item.id}` : apiBase(entidad);
  const r = await fetch(url,{method,headers:{"Content-Type":"application/json"},body:JSON.stringify(item)});
  if(!r.ok) throw new Error("save fail");
  return await r.json().catch(()=>item);
}
async function deleteItem(entidad,id){
  const r = await fetch(`${apiBase(entidad)}/${id}`,{method:"DELETE"});
  if(!r.ok) throw new Error("delete fail");
  return true;
}

function toolbarHTML(){
  return `
    <div class="toolbar">
      <input id="q" class="input" placeholder="Buscar..." style="min-width:220px">
      <button id="btn-new" class="btn" title="Nuevo">＋</button>
      <button id="btn-save" class="btn ok" title="Guardar">💾</button>
      <button id="btn-del" class="btn danger" title="Eliminar">��</button>
      <span class="spacer"></span>
      <button id="nav-first" class="btn" title="Primero">⏮</button>
      <button id="nav-prev" class="btn" title="Anterior">◀</button>
      <span id="nav-info" style="padding:0 6px;color:var(--muted)"></span>
      <button id="nav-next" class="btn" title="Siguiente">▶</button>
      <button id="nav-last" class="btn" title="Último">⏭</button>
    </div>
  `;
}

function formHTML(entidad, item={}){
  // define campos por entidad
  const map = {
    usuarios: `
      <div class="grid" style="grid-template-columns: 1fr 160px 120px; gap:.75rem">
        <div><label>Email</label><input id="f_email" class="input" type="email" value="${item.email||""}"></div>
        <div><label>Rol</label><input id="f_rol" class="input" value="${item.rol||""}"></div>
        <div style="display:flex;align-items:end;gap:.5rem">
          <label style="width:100%;">Activo</label>
          <input id="f_activo" type="checkbox" ${item.activo?"checked":""}>
        </div>
      </div>`,
    comunas: `
      <div class="grid" style="grid-template-columns: 2fr 120px; gap:.75rem">
        <div><label>Nombre</label><input id="f_nombre" class="input" value="${item.nombre||""}"></div>
        <div style="display:flex;align-items:end;gap:.5rem">
          <label style="width:100%;">Activo</label>
          <input id="f_activo" type="checkbox" ${item.activo?"checked":""}>
        </div>
      </div>`,
    cartas: `
      <div><label>Nombre</label><input id="f_nombre" class="input" value="${item.nombre||""}"></div>`,
    marcas: `
      <div><label>Nombre</label><input id="f_nombre" class="input" value="${item.nombre||""}"></div>`,
    estados: `
      <div><label>Nombre</label><input id="f_nombre" class="input" value="${item.nombre||""}"></div>`
  };
  return `
    <input id="f_id" type="hidden" value="${item.id||""}">
    ${map[entidad] || "<div>Entidad no soportada aún</div>"}
  `;
}

function tableHTML(entidad, rows){
  const cols = {
    usuarios: ["id","email","rol","activo"],
    comunas: ["id","nombre","activo"],
    cartas: ["id","nombre"],
    marcas: ["id","nombre"],
    estados: ["id","nombre"]
  }[entidad] || Object.keys(rows[0]||{});
  const head = cols.map(c=>`<th>${c.toUpperCase()}</th>`).join("");
  const body = rows.map(r=>`<tr data-id="${r.id||""}">${cols.map(c=>`<td>${r[c]??""}</td>`).join("")}</tr>`).join("");
  return `<table class="table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

export function Settings(entidadArg=""){
  const entidad = entidadArg || "usuarios"; // landing en usuarios
  const state = { rows:[], idx:0, filtered:[] };

  const render = () => `
    <section class="card">
      <h1 style="margin:0 0 10px 0">⚙️ ${entidad[0].toUpperCase()+entidad.slice(1)}</h1>
      ${toolbarHTML()}
      <div class="card" style="margin:12px 0">${formHTML(entidad, state.filtered[state.idx] || {})}</div>
      <div class="card">${tableHTML(entidad, state.filtered)}</div>
    </section>
  `;

  // carga asincrónica
  fetchList(entidad).then(rows=>{
    state.rows = rows;
    state.filtered = rows.slice();
    document.getElementById("app").innerHTML = render();
    wire();
  });

  // wiring de eventos
  function wire(){
    const $ = s => document.querySelector(s);
    const $$ = s => Array.from(document.querySelectorAll(s));
    const apply = () => {
      $("#nav-info").textContent = state.filtered.length
        ? `${state.idx+1} / ${state.filtered.length}`
        : `0 / 0`;
      // pintar selección en tabla
      $$(".table tbody tr").forEach((tr,i)=>{
        tr.style.background = i===state.idx ? "#0b1016" : "";
        tr.onclick = ()=>{ state.idx=i; fillForm(); apply(); };
      });
    };
    const fillForm = () => {
      const row = state.filtered[state.idx] || {};
      if($("#f_id")) $("#f_id").value = row.id || "";
      if($("#f_email")) $("#f_email").value = row.email || "";
      if($("#f_rol")) $("#f_rol").value = row.rol || "";
      if($("#f_nombre")) $("#f_nombre").value = row.nombre || "";
      if($("#f_activo")) $("#f_activo").checked = !!row.activo;
    };

    // navegación
    $("#nav-first").onclick = ()=>{ state.idx=0; fillForm(); apply(); };
    $("#nav-prev").onclick  = ()=>{ state.idx=Math.max(0, state.idx-1); fillForm(); apply(); };
    $("#nav-next").onclick  = ()=>{ state.idx=Math.min(state.filtered.length-1, state.idx+1); fillForm(); apply(); };
    $("#nav-last").onclick  = ()=>{ state.idx=Math.max(0, state.filtered.length-1); fillForm(); apply(); };

    // búsqueda
    $("#q").oninput = e=>{
      const q = e.target.value.toLowerCase();
      state.filtered = state.rows.filter(r => JSON.stringify(r).toLowerCase().includes(q));
      state.idx = 0;
      document.querySelector(".card:nth-of-type(3)").innerHTML = tableHTML(entidad, state.filtered);
      fillForm(); apply();
    };

    // nuevo
    $("#btn-new").onclick = ()=>{
      const blank = {id:"",activo:true};
      state.filtered = [blank, ...state.filtered];
      state.rows = [blank, ...state.rows];
      state.idx = 0;
      document.querySelector(".card:nth-of-type(3)").innerHTML = tableHTML(entidad, state.filtered);
      fillForm(); apply();
    };

    // guardar
    $("#btn-save").onclick = async ()=>{
      const payload = {};
      const id = $("#f_id")?.value || "";
      if($("#f_email")) payload.email = $("#f_email").value;
      if($("#f_rol")) payload.rol = $("#f_rol").value;
      if($("#f_nombre")) payload.nombre = $("#f_nombre").value;
      if($("#f_activo")) payload.activo = $("#f_activo").checked;
      if(id) payload.id = id;

      const saved = await saveItem(entidad, payload).catch(()=>payload);
      // refresco en memoria
      if(!saved.id) saved.id = id || Date.now();
      state.filtered[state.idx] = saved;
      const pos = state.rows.findIndex(r=>r.id==saved.id);
      if(pos>=0) state.rows[pos]=saved; else state.rows.unshift(saved);

      document.querySelector(".card:nth-of-type(3)").innerHTML = tableHTML(entidad, state.filtered);
      fillForm(); apply();
    };

    // eliminar
    $("#btn-del").onclick = async ()=>{
      const row = state.filtered[state.idx];
      if(!row) return;
      await deleteItem(entidad, row.id).catch(()=>true);
      state.rows = state.rows.filter(r=>r.id!==row.id);
      state.filtered = state.filtered.filter(r=>r.id!==row.id);
      state.idx = 0;
      document.querySelector(".card:nth-of-type(3)").innerHTML = tableHTML(entidad, state.filtered);
      fillForm(); apply();
    };

    // inicial
    fillForm(); apply();
  }

  // render inicial (con cascarón)
  return render();
}
