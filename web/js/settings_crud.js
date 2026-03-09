const qs = (s, el=document) => el.querySelector(s);

function token(){
  return window.parent?.GD?.getToken?.()
    || window.top?.GD?.getToken?.()
    || localStorage.getItem("token")
    || sessionStorage.getItem("token")
    || "";
}
function headers(extra={}){
  const t = token();
  return t ? { ...extra, Authorization:`Bearer ${t}` } : extra;
}
function qp(name){
  const u = new URL(location.href);
  return u.searchParams.get(name) || "";
}
const entity = qp("entity") || "usuarios";
const title = decodeURIComponent(qp("title") || entity);

qs("#ttl").textContent = `Settings · ${title}`;

async function api(path, opts={}){
  const r = await fetch(path, { ...opts, headers: headers({ "Content-Type":"application/json", ...(opts.headers||{}) }) });
  const txt = await r.text();
  let j = null;
  try{ j = txt ? JSON.parse(txt) : null; }catch{ j = { raw:txt }; }
  if (!r.ok) throw new Error(j?.detail || j?.message || txt || `HTTP ${r.status}`);
  return j;
}

let MARCAS_CACHE = null;
async function loadMarcas(){
  if (MARCAS_CACHE) return MARCAS_CACHE;
  const data = await api("/catalogos");
  MARCAS_CACHE = data?.marcas || data?.catalogos?.marcas || [];
  return MARCAS_CACHE;
}
function marcasNameMap(){
  const map = new Map();
  (MARCAS_CACHE || []).forEach(m => {
    const id = String(m.id_marca || m.id);
    const name = m.nombre || m.marca || m.name || id;
    map.set(id, name);
  });
  return map;
}
async function buildUserBrands(items){
  try{
    await loadMarcas();
    const nameMap = marcasNameMap();
    const out = {};
    await Promise.all((items || []).map(async (it) => {
      const id = it?.[META?.pk];
      if (!id) return;
      try{
        const b = await api(`/users/${id}/brands`);
        const names = (b?.marcas || []).map(mid => nameMap.get(String(mid)) || String(mid));
        out[id] = names.join(", ");
      }catch(e){
        out[id] = "";
      }
    }));
    return out;
  }catch(e){
    return {};
  }
}
function marcasSelectorHtml(selectedIds=[]){
  const sel = new Set((selectedIds||[]).map(String));
  const items = (MARCAS_CACHE || []).map(m => {
    const id = m.id_marca || m.id;
    const name = m.nombre || m.marca || m.name || id;
    const checked = sel.has(String(id)) ? "checked" : "";
    return `<label style="display:flex; gap:8px; align-items:center; margin:4px 0">
      <input class="marca-opt" type="checkbox" value="${id}" ${checked}/>
      <span>${name}</span>
    </label>`;
  }).join("");
  return `
    <div style="margin-top:10px; padding:10px 12px; border:1px solid rgba(0,0,0,.12); border-radius:12px;">
      <div style="font-weight:900; margin-bottom:6px">Marcas asignadas (Ejecutivos)</div>
      <div class="marcas-hint" style="opacity:.7; font-size:12px; margin-bottom:6px">Asigna las marcas que podrá ver el ejecutivo.</div>
      <div style="max-height:160px; overflow:auto">${items || "Sin marcas"}</div>
    </div>
  `;
}
function getSelectedMarcas(){
  return Array.from(document.querySelectorAll(".marca-opt:checked"))
    .map(el => Number(el.value))
    .filter(n => !Number.isNaN(n));
}

function niceLabel(col){
  return col.replaceAll("_"," ").replace(/\b\w/g, c => c.toUpperCase());
}

const EXEC_ROLE_RE = /EJECUTIVO|VENDEDOR/i;

function isExecRole(role){
  return EXEC_ROLE_RE.test(role || "");
}

function slugify(s){
  return (s || "")
    .toString()
    .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "")
    .replace(/^_|_$/g, "");
}

function suggestUsername(nombre, rol){
  const n = slugify((nombre || "").split(" ")[0] || "");
  const r = (rol || "").toLowerCase();
  let prefix = "user";
  if (r.includes("ejecutivo")) prefix = "ejec";
  else if (r.includes("admin")) prefix = "admin";
  else if (r.includes("operaciones")) prefix = "ops";
  return `${prefix}_${n || "user"}`.slice(0, 20);
}

function generatePassword(len = 10){
  const chars = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789!@#$%";
  let out = "";
  for (let i=0;i<len;i++){
    out += chars[Math.floor(Math.random() * chars.length)];
  }
  return out;
}

function inputFor(col){
  const type = (col.data_type||"text").toLowerCase();
  const name = col.column_name;
  const ui = col.ui || {};
  if (name === "cargo"){
    return `<input id="f_cargo" type="text" style="display:none" />`;
  }
  if (ui.widget === "select" && ui.source && META?.choices?.[ui.source]){
    const opts = META.choices[ui.source] || [];
    const valueField = ui.valueField || "id";
    const labelField = ui.labelField || "nombre";
    const options = opts.map(o => `<option value="${o[valueField]}">${o[labelField]}</option>`).join("");
    return `<label style="display:block; margin:8px 0">
      <div style="font-weight:800; opacity:.9; margin-bottom:4px; text-transform:capitalize">${niceLabel(name)}</div>
      <select id="f_${name}" style="width:100%; padding:10px 12px; border-radius:12px; border:1px solid rgba(0,0,0,.12)">
        ${options}
      </select>
    </label>`;
  }
  const isBool = type.includes("bool");
  const isNum = ["int","numeric","double","real","decimal"].some(x => type.includes(x));
  const isDate = type.includes("date") && !type.includes("timestamp");
  const isTs = type.includes("timestamp");

  if (isBool){
    return `<label style="display:flex; gap:10px; align-items:center; margin:8px 0">
      <input id="f_${name}" type="checkbox" />
      <span style="text-transform:capitalize">${niceLabel(name)}</span>
    </label>`;
  }
  const t = isDate ? "date" : (isTs ? "datetime-local" : (isNum ? "number" : "text"));
  return `<label style="display:block; margin:8px 0">
    <div style="font-weight:800; opacity:.9; margin-bottom:4px; text-transform:capitalize">${niceLabel(name)}</div>
    <input id="f_${name}" type="${t}" style="width:100%; padding:10px 12px; border-radius:12px; border:1px solid rgba(0,0,0,.12)" />
  </label>`;
}

function collect(cols, excludePk=true){
  const out = {};
  for (const c of cols){
    const n = c.column_name;
    if (excludePk && n === META.pk) continue;

    const el = document.getElementById(`f_${n}`);
    if (!el) continue;

    if (el.type === "checkbox"){
      out[n] = !!el.checked;
    }else{
      const v = el.value;
      if (v === "") continue;
      out[n] = (el.type === "number") ? Number(v) : v;
    }
  }
  return out;
}

let META = null;

async function loadMeta(){
  META = await api(`/settings/meta/${entity}`, { method:"GET" });
  return META;
}

function visibleCols(cols){
  // oculta basura sensible por defecto
  const hidden = new Set(["hashed_password", "id_rol"]);
  return cols.filter(c => !hidden.has(c.column_name));
}

function orderCols(cols, order){
  const map = new Map(cols.map(c => [c.column_name, c]));
  const out = [];
  order.forEach(k => {
    if (map.has(k)){ out.push(map.get(k)); map.delete(k); }
  });
  for (const v of map.values()) out.push(v);
  return out;
}

async function list(q="", offset=0, limit=25){
  const u = new URL(`/settings/${entity}`, location.origin);
  u.searchParams.set("limit", String(limit));
  u.searchParams.set("offset", String(offset));
  if (q) u.searchParams.set("q", q);
  return api(u.toString(), { method:"GET" });
}

function tableHtml(items, cols, opts={}){
  const brandMap = opts.brandMap || null;
  const isUsers = !!brandMap;
  let head = cols.map(c => `<th style="text-align:left; padding:8px 10px; font-weight:900">${niceLabel(c.column_name)}</th>`).join("");
  if (isUsers){
    head += `<th style="text-align:left; padding:8px 10px; font-weight:900">Marcas</th>`;
  }
  const rows = items.map(it => {
    let tds = cols.map(c => `<td style="padding:8px 10px; border-top:1px solid rgba(0,0,0,.06)">${(it[c.column_name] ?? "")}</td>`).join("");
    if (isUsers){
      const id = it?.[META?.pk];
      tds += `<td style="padding:8px 10px; border-top:1px solid rgba(0,0,0,.06)">${brandMap?.[id] || ""}</td>`;
    }
    return `<tr data-id="${it[META.pk]}">${tds}</tr>`;
  }).join("");
  return `
    <div style="overflow:auto; max-height:55vh; border-radius:14px; border:1px solid rgba(0,0,0,.10);">
      <table style="width:100%; border-collapse:collapse; font-size:13px">
        <thead style="position:sticky; top:0; background:#fff">${head}</thead>
        <tbody>${rows || `<tr><td style="padding:14px">Sin registros</td></tr>`}</tbody>
      </table>
    </div>
  `;
}

async function openCrud(){
  if (!META) await loadMeta();
  const isUsersEntity = (entity === "usuarios");
  if (isUsersEntity) await loadMarcas();

  const cols = visibleCols(META.columns).slice(0, 8); // tabla “humana”
  let q = "";
  let offset = 0;
  const limit = 25;

  const render = async () => {
    const data = await list(q, offset, limit);
    const brandMap = isUsersEntity ? await buildUserBrands(data.items) : null;
    return `
      <div style="display:flex; gap:10px; align-items:center; margin-bottom:10px">
        <input id="q" placeholder="Buscar..." value="${q.replaceAll('"',"&quot;")}"
               style="flex:1; padding:10px 12px; border-radius:12px; border:1px solid rgba(0,0,0,.12)" />
        <button id="btnSearch" class="swal2-confirm swal2-styled" style="background:#22c55e">Buscar</button>
      </div>

      <div style="display:flex; gap:10px; margin-bottom:10px; flex-wrap:wrap">
        <button id="btnCreate" class="swal2-styled" style="background:#3b82f6">Crear</button>
        <button id="btnEdit" class="swal2-styled" style="background:#a855f7">Editar</button>
        <button id="btnDelete" class="swal2-styled" style="background:#ef4444">Eliminar</button>
        <button id="btnMore" class="swal2-styled" style="background:#64748b">Ver más</button>
      </div>

      <div style="opacity:.75; font-weight:800; margin:0 0 8px 0">Total: ${data.total}</div>
      ${tableHtml(data.items, cols, { brandMap })}

      <div style="display:flex; justify-content:space-between; margin-top:10px; align-items:center">
        <button id="prev" class="swal2-styled" style="background:#111827">◀</button>
        <div style="opacity:.75">Página ${Math.floor(offset/limit)+1}</div>
        <button id="next" class="swal2-styled" style="background:#111827">▶</button>
      </div>
    `;
  };

  await Swal.fire({
    title: title,
    html: await render(),
    width: 900,
    showConfirmButton:false,
    didOpen: (popup) => {
      let selectedId = null;

      const bindSelection = () => {
        popup.querySelectorAll("tbody tr[data-id]").forEach(tr => {
          tr.style.cursor = "pointer";
          tr.onclick = () => {
            popup.querySelectorAll("tbody tr").forEach(x => x.style.background = "");
            tr.style.background = "rgba(34,197,94,.14)";
            selectedId = tr.getAttribute("data-id");
          };
        });
      };
      bindSelection();

      const rerender = async () => {
        popup.querySelector(".swal2-html-container").innerHTML = await render();
        bindHandlers();
        bindSelection();
      };

      const bindHandlers = () => {
        const qEl = popup.querySelector("#q");
        popup.querySelector("#btnSearch").onclick = async () => { q = qEl.value.trim(); offset=0; await rerender(); };
        popup.querySelector("#prev").onclick = async () => { offset = Math.max(0, offset-limit); await rerender(); };
        popup.querySelector("#next").onclick = async () => { offset = offset + limit; await rerender(); };

        popup.querySelector("#btnMore").onclick = async () => {
          offset = offset + limit;
          await rerender();
        };

        popup.querySelector("#btnCreate").onclick = async () => {
          let colsForm = visibleCols(META.columns).filter(c => c.column_name !== META.pk);
          const isUsers = (entity==="usuarios");
          if (isUsers){
            colsForm = orderCols(colsForm, ["nombre","email","username","telefono","rol","cargo","is_active"]);
          }

          const extraPass = isUsers ? `
            <div style="margin-top:10px; padding:10px 12px; border:1px solid rgba(0,0,0,.12); border-radius:12px;">
              <div style="font-weight:900; margin-bottom:6px">Credenciales</div>
              <label style="display:block; margin:6px 0">
                <div style="font-weight:800; opacity:.9; margin-bottom:4px">Nueva clave</div>
                <div style="display:flex; gap:8px; align-items:center">
                  <input id="f_password" type="password" style="flex:1; padding:10px 12px; border-radius:10px; border:1px solid rgba(148,163,184,.35); background:rgba(15,23,42,.35); color:#e2e8f0" />
                  <button type="button" id="toggle_pw" class="swal2-styled" style="background:#64748b">Ver</button>
                </div>
              </label>
              <label style="display:block; margin:6px 0">
                <div style="font-weight:800; opacity:.9; margin-bottom:4px">Confirmar contraseña</div>
                <input id="f_password2" type="password" style="width:100%; padding:10px 12px; border-radius:10px; border:1px solid rgba(148,163,184,.35); background:rgba(15,23,42,.35); color:#e2e8f0" />
              </label>
              <div style="display:flex; gap:10px; flex-wrap:wrap; margin-top:6px">
                <button type="button" id="btn_gen_pw" class="swal2-styled" style="background:#0ea5e9">Generar</button>
                <button type="button" id="btn_copy_pw" class="swal2-styled" style="background:#334155">Copiar</button>
              </div>
              <div style="display:flex; gap:14px; flex-wrap:wrap; margin-top:6px; font-weight:800">
                <label style="display:flex; gap:6px; align-items:center">
                  <input type="checkbox" id="send_email" checked /> Enviar por email
                </label>
                <label style="display:flex; gap:6px; align-items:center">
                  <input type="checkbox" id="send_wa" /> Enviar por WhatsApp
                </label>
              </div>
              <div style="opacity:.7; margin-top:6px; font-size:12px">Se generará una clave automática. El usuario podrá cambiarla al ingresar.</div>
            </div>` : "";

          const marcasHtml = isUsers ? `<div id="marcasWrap">${marcasSelectorHtml([])}</div>` : "";
          const html = colsForm.map(inputFor).join("")
            + extraPass
            + marcasHtml;

          const r = await Swal.fire({
            title:`Crear ${title}`,
            html,
            focusConfirm:false,
            showCancelButton:true,
            confirmButtonText:"Crear",
            confirmButtonColor:"#22c55e",
            didOpen: () => {
              if (!isUsers) return;
              const btn = document.getElementById("toggle_pw");
              const inp = document.getElementById("f_password");
              const inp2 = document.getElementById("f_password2");
              const genBtn = document.getElementById("btn_gen_pw");
              const copyBtn = document.getElementById("btn_copy_pw");
              const nameEl = document.getElementById("f_nombre");
              const roleEl = document.getElementById("f_rol");
              const userEl = document.getElementById("f_username");
              const cargoEl = document.getElementById("f_cargo");
              const activeEl = document.getElementById("f_is_active");
              let userTouched = false;

              if (activeEl) activeEl.checked = true;

              if (btn && inp){
                btn.onclick = () => {
                  inp.type = (inp.type === "password") ? "text" : "password";
                  btn.textContent = (inp.type === "password") ? "Ver" : "Ocultar";
                };
              }
              const applyAuto = () => {
                const rol = roleEl?.value || "";
                if (cargoEl) cargoEl.value = rol;
                if (!userTouched && userEl){
                  userEl.value = suggestUsername(nameEl?.value || "", rol);
                }
                const wrap = document.getElementById("marcasWrap");
                if (wrap){
                  wrap.style.display = "block";
                  const hint = wrap.querySelector(".marcas-hint");
                  if (hint){
                    hint.textContent = isExecRole(rol)
                      ? "Asigna las marcas que podrá ver el ejecutivo."
                      : "Solo aplica a Ejecutivos/Vendedores.";
                  }
                }
              };
              if (userEl){
                userEl.addEventListener("input", () => { userTouched = true; });
              }
              if (nameEl) nameEl.addEventListener("input", applyAuto);
              if (roleEl) roleEl.addEventListener("change", applyAuto);
              applyAuto();

              const seed = generatePassword();
              if (inp && inp2){
                inp.value = seed;
                inp2.value = seed;
              }
              if (genBtn){
                genBtn.onclick = () => {
                  const pwd = generatePassword();
                  if (inp && inp2){ inp.value = pwd; inp2.value = pwd; }
                };
              }
              if (copyBtn && inp){
                copyBtn.onclick = async () => {
                  try{ await navigator.clipboard.writeText(inp.value || ""); }catch(e){}
                };
              }
            },
            preConfirm: () => {
              const payload = collect(colsForm, true);
              if (isUsers){
                const pw = document.getElementById("f_password")?.value || "";
                const pw2 = document.getElementById("f_password2")?.value || "";
                if (!pw) {
                  Swal.showValidationMessage("Debes ingresar una contraseña.");
                  return false;
                }
                if (pw !== pw2){
                  Swal.showValidationMessage("Las contraseñas no coinciden.");
                  return false;
                }
                payload.password = pw;
                payload.__send_email = !!document.getElementById("send_email")?.checked;
                payload.__send_wa = !!document.getElementById("send_wa")?.checked;
                const rol = document.getElementById("f_rol")?.value || "";
                payload.__marcas = isExecRole(rol) ? getSelectedMarcas() : [];
              }
              return payload;
            }
          });
          if (!r.isConfirmed) return;

          try{
            const sendEmail = !!r.value.__send_email;
            const sendWa = !!r.value.__send_wa;
            const password = r.value.password;
            const marcasSel = r.value.__marcas || [];
            delete r.value.__send_email;
            delete r.value.__send_wa;
            delete r.value.__marcas;

            const created = await api(`/settings/${entity}`, { method:"POST", body: JSON.stringify(r.value) });
            if (isUsers && created?.id && Array.isArray(marcasSel)){
              try{
                await api(`/users/${created.id}/brands`, { method:"PUT", body: JSON.stringify({ marcas: marcasSel }) });
              }catch(e){}
            }
            // notificar credenciales si aplica
            if (isUsers && password){
              const id = created?.id;
              if (sendEmail && id){
                try{
                  await api(`/settings/usuarios/${id}/reset_password?new_password=${encodeURIComponent(password)}&send_email=true`, { method:"POST" });
                }catch(e){}
              }
              if (sendWa){
                const tel = String(r.value.telefono || r.value.phone || r.value.celular || "").replace(/\\D/g,"");
                const usuario = r.value.email || r.value.username || r.value.nombre || id;
                if (tel){
                  const msg = encodeURIComponent(
                    `Hola ${r.value.nombre || ""}, aquí van tus credenciales:\\nUsuario: ${usuario}\\nClave: ${password}\\nPor favor cambia tu clave al ingresar.`
                  );
                  window.open(`https://wa.me/${tel}?text=${msg}`, "_blank", "noopener,noreferrer");
                }
              }
            }
            await rerender();
          }catch(e){
            Swal.fire({ icon:"error", title:"Error", text:String(e.message||e) });
          }
        };

        popup.querySelector("#btnEdit").onclick = async () => {
          if (!selectedId) return Swal.fire({ icon:"info", title:"Selecciona un registro" });

          let colsForm = visibleCols(META.columns).filter(c => c.column_name !== META.pk);
          const current = (await api(`/settings/${entity}?limit=1&offset=0&q=${selectedId}`)).items?.[0] || null;

          const isUsers = (entity==="usuarios");
          if (isUsers){
            colsForm = orderCols(colsForm, ["nombre","email","username","telefono","rol","cargo","is_active"]);
          }
          let currentBrands = [];
          if (isUsers){
            try{
              const b = await api(`/users/${selectedId}/brands`);
              currentBrands = b?.marcas || [];
            }catch(e){}
          }
          const html = colsForm.map(c => {
            const name = c.column_name;
            const ui = c.ui || {};
            if (ui.widget === "select" && ui.source && META?.choices?.[ui.source]){
              const opts = META.choices[ui.source] || [];
              const valueField = ui.valueField || "id";
              const labelField = ui.labelField || "nombre";
              const options = opts.map(o => {
                const v = o[valueField];
                const sel = String(v) === String(current?.[name]) ? "selected" : "";
                return `<option value="${v}" ${sel}>${o[labelField]}</option>`;
              }).join("");
              return `<label style="display:block; margin:8px 0">
                <div style="font-weight:800; opacity:.9; margin-bottom:4px; text-transform:capitalize">${niceLabel(name)}</div>
                <select id="f_${name}" style="width:100%; padding:10px 12px; border-radius:12px; border:1px solid rgba(0,0,0,.12)">
                  ${options}
                </select>
              </label>`;
            }
            const type = (c.data_type||"text").toLowerCase();
            const isBool = type.includes("bool");
            const isNum = ["int","numeric","double","real","decimal"].some(x => type.includes(x));
            const isDate = type.includes("date") && !type.includes("timestamp");
            const isTs = type.includes("timestamp");
            const v = current?.[name];

            if (isBool){
              return `<label style="display:flex; gap:10px; align-items:center; margin:8px 0">
                <input id="f_${name}" type="checkbox" ${v ? "checked":""} />
                <span style="text-transform:capitalize">${niceLabel(name)}</span>
              </label>`;
            }
            const t = isDate ? "date" : (isTs ? "datetime-local" : (isNum ? "number" : "text"));
            return `<label style="display:block; margin:8px 0">
              <div style="font-weight:800; opacity:.9; margin-bottom:4px; text-transform:capitalize">${niceLabel(name)}</div>
              <input id="f_${name}" type="${t}" value="${(v ?? "").toString().replaceAll('"',"&quot;")}"
                     style="width:100%; padding:10px 12px; border-radius:12px; border:1px solid rgba(0,0,0,.12)" />
            </label>`;
          }).join("") + (isUsers ? `
            <div style="margin-top:10px; padding:10px 12px; border:1px solid rgba(0,0,0,.12); border-radius:12px;">
              <div style="font-weight:900; margin-bottom:6px">Cambiar contraseña</div>
              <label style="display:block; margin:6px 0">
                <div style="font-weight:800; opacity:.9; margin-bottom:4px">Nueva contraseña (opcional)</div>
                <div style="display:flex; gap:8px; align-items:center">
                  <input id="f_password" type="password" style="flex:1; padding:10px 12px; border-radius:10px; border:1px solid rgba(148,163,184,.35); background:rgba(15,23,42,.35); color:#e2e8f0" />
                  <button type="button" id="toggle_pw" class="swal2-styled" style="background:#64748b">Ver</button>
                </div>
              </label>
              <div style="display:flex; gap:14px; flex-wrap:wrap; margin-top:6px; font-weight:800">
                <label style="display:flex; gap:6px; align-items:center">
                  <input type="checkbox" id="send_email" checked /> Enviar por email
                </label>
                <label style="display:flex; gap:6px; align-items:center">
                  <input type="checkbox" id="send_wa" /> Enviar por WhatsApp
                </label>
              </div>
            </div>
            <div id="marcasWrap">${marcasSelectorHtml(currentBrands)}</div>
            ` : "");

          const r = await Swal.fire({
            title:`Editar ${title}`,
            html,
            focusConfirm:false,
            showCancelButton:true,
            confirmButtonText:"Guardar",
            confirmButtonColor:"#22c55e",
            didOpen: () => {
              if (!isUsers) return;
              const btn = document.getElementById("toggle_pw");
              const inp = document.getElementById("f_password");
              const nameEl = document.getElementById("f_nombre");
              const roleEl = document.getElementById("f_rol");
              const userEl = document.getElementById("f_username");
              const cargoEl = document.getElementById("f_cargo");
              let userTouched = false;
              if (btn && inp){
                btn.onclick = () => {
                  inp.type = (inp.type === "password") ? "text" : "password";
                  btn.textContent = (inp.type === "password") ? "Ver" : "Ocultar";
                };
              }
              const applyAuto = () => {
                const rol = roleEl?.value || "";
                if (cargoEl) cargoEl.value = rol;
                if (!userTouched && userEl){
                  userEl.value = suggestUsername(nameEl?.value || "", rol);
                }
                const wrap = document.getElementById("marcasWrap");
                if (wrap){
                  wrap.style.display = "block";
                  const hint = wrap.querySelector(".marcas-hint");
                  if (hint){
                    hint.textContent = isExecRole(rol)
                      ? "Asigna las marcas que podrá ver el ejecutivo."
                      : "Solo aplica a Ejecutivos/Vendedores.";
                  }
                }
              };
              if (userEl){
                userEl.addEventListener("input", () => { userTouched = true; });
              }
              if (nameEl) nameEl.addEventListener("input", applyAuto);
              if (roleEl) roleEl.addEventListener("change", applyAuto);
              applyAuto();
            },
            preConfirm: () => {
              const payload = collect(colsForm, true);
              if (isUsers){
                const pw = document.getElementById("f_password")?.value || "";
                if (pw) payload.password = pw;
                payload.__send_email = !!document.getElementById("send_email")?.checked;
                payload.__send_wa = !!document.getElementById("send_wa")?.checked;
                const rol = document.getElementById("f_rol")?.value || "";
                payload.__marcas = isExecRole(rol) ? getSelectedMarcas() : [];
              }
              return payload;
            }
          });
          if (!r.isConfirmed) return;

          try{
            const sendEmail = !!r.value.__send_email;
            const sendWa = !!r.value.__send_wa;
            const password = r.value.password;
            const marcasSel = r.value.__marcas || [];
            delete r.value.__send_email;
            delete r.value.__send_wa;
            delete r.value.__marcas;

            await api(`/settings/${entity}/${selectedId}`, { method:"PUT", body: JSON.stringify(r.value) });
            if (isUsers && Array.isArray(marcasSel)){
              try{
                await api(`/users/${selectedId}/brands`, { method:"PUT", body: JSON.stringify({ marcas: marcasSel }) });
              }catch(e){}
            }
            if (isUsers && password){
              if (sendEmail){
                try{
                  await api(`/settings/usuarios/${selectedId}/reset_password?new_password=${encodeURIComponent(password)}&send_email=true`, { method:"POST" });
                }catch(e){}
              }
              if (sendWa){
                const tel = String(r.value.telefono || current?.telefono || current?.phone || current?.celular || "").replace(/\\D/g,"");
                const usuario = r.value.email || current?.email || r.value.username || current?.username || current?.nombre || selectedId;
                if (tel){
                  const msg = encodeURIComponent(
                    `Hola ${r.value.nombre || current?.nombre || ""}, aquí van tus credenciales:\\nUsuario: ${usuario}\\nClave: ${password}\\nPor favor cambia tu clave al ingresar.`
                  );
                  window.open(`https://wa.me/${tel}?text=${msg}`, "_blank", "noopener,noreferrer");
                }
              }
            }
            await rerender();
          }catch(e){
            Swal.fire({ icon:"error", title:"Error", text:String(e.message||e) });
          }
        };

        popup.querySelector("#btnDelete").onclick = async () => {
          if (!selectedId) return Swal.fire({ icon:"info", title:"Selecciona un registro" });

          const ok = await Swal.fire({
            icon:"warning",
            title:"Eliminar",
            text:`¿Seguro que quieres eliminar ID ${selectedId}?`,
            showCancelButton:true,
            confirmButtonText:"Eliminar",
            confirmButtonColor:"#ef4444",
            cancelButtonText:"Cancelar"
          });
          if (!ok.isConfirmed) return;

          try{
            await api(`/settings/${entity}/${selectedId}`, { method:"DELETE" });
            selectedId = null;
            await rerender();
          }catch(e){
            Swal.fire({ icon:"error", title:"Error", text:String(e.message||e) });
          }
        };
      };

      bindHandlers();
    }
  });
}

qs("#openCrud").onclick = openCrud;

// theme bridge
window.addEventListener("message", (ev) => {
  if (ev.data?.type === "theme"){
    document.documentElement.classList.toggle("light", ev.data.mode === "light");
  }
});
