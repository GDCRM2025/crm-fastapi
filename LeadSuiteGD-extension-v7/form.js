import { toast, modalConfirm } from "./ui.js";

const $ = (q) => document.querySelector(q);
async function msg(m){ return await chrome.runtime.sendMessage(m); }

function setAuthBadge(has){
  $("#dot").classList.toggle("on", !!has);
  $("#authTxt").textContent = has ? "Autenticado (token OK)" : "No autenticado (token faltante)";
}

const norm = (s) => String(s ?? "").trim();
const low  = (s) => norm(s).toLowerCase();

function fillSelect(sel, items, idKey, labelKey){
  sel.innerHTML = `<option value="">—</option>` + items.map(it =>
    `<option value="${String(it[idKey])}">${String(it[labelKey])}</option>`
  ).join("");
}

function mapComunaTextToId(comunaText, comunas){
  const t = low(comunaText);
  if (!t) return "";
  const exact = comunas.find(c => low(c.nombre) === t);
  if (exact) return String(exact.id_comuna);
  const inc = comunas.find(c => low(c.nombre).includes(t) || t.includes(low(c.nombre)));
  return inc ? String(inc.id_comuna) : "";
}

function preventTypingDate(el){
  el.addEventListener("keydown", (e) => {
    const allow = ["Tab","Shift","Escape","Enter","ArrowLeft","ArrowRight","ArrowUp","ArrowDown"];
    if (allow.includes(e.key)) return;
    e.preventDefault();
  });
}

async function load(){
  $("#x").addEventListener("click", ()=>window.close());

  const a = await msg({cmd:"getAuth"});
  setAuthBadge(!!(a?.auth?.token));

  const catsRes = await msg({cmd:"fetchCatalogos"});
  const cats = catsRes?.data || {};
  const comunas = cats.comunas || [];
  const tipos   = cats.tipocliente || cats.tipos_cliente || [];
  const marcas  = cats.marcas || [];
  const plats   = cats.plataformas || [];

  fillSelect($("#id_comuna"), comunas, "id_comuna", "nombre");
  fillSelect($("#id_tipo_cliente"), tipos, "id_tipo_cliente", "nombre");
  fillSelect($("#id_marca"), marcas, "id_marca", "nombre");

  $("#plataforma").innerHTML = `<option value="">—</option>` + plats.map(p =>
    `<option value="${String(p.id)}">${String(p.nombre)}</option>`
  ).join("");

  const d = await msg({cmd:"getDraft"});
  const draft = d?.draft || {};

  $("#nombre_cliente").value = draft.nombre_cliente || "";
  $("#telefono").value       = draft.telefono || "";
  $("#email").value          = draft.email || "";
  $("#direccion").value      = draft.direccion || "";
  $("#codigo_cliente").value = draft.codigo_cliente || "";
  $("#notas").value          = draft.notas || "";
  if (draft.fecha_evento) $("#fecha_evento").value = String(draft.fecha_evento).slice(0,10);

  if (draft.comuna_texto && !draft.id_comuna){
    const id = mapComunaTextToId(draft.comuna_texto, comunas);
    if (id) $("#id_comuna").value = id;
  } else if (draft.id_comuna) {
    $("#id_comuna").value = String(draft.id_comuna);
  }

  if (draft.id_tipo_cliente) $("#id_tipo_cliente").value = String(draft.id_tipo_cliente);
  if (draft.id_marca)        $("#id_marca").value        = String(draft.id_marca);
  if (draft.plataforma)      $("#plataforma").value      = String(draft.plataforma);

  const dateEl = $("#fecha_evento");
  preventTypingDate(dateEl);
  $("#pick").addEventListener("click", () => {
    try { if (typeof dateEl.showPicker === "function") dateEl.showPicker(); else dateEl.focus(); }
    catch { dateEl.focus(); }
  });

  const sync = async () => {
    await msg({cmd:"setDraft", patch:{
      nombre_cliente: norm($("#nombre_cliente").value),
      telefono: norm($("#telefono").value),
      email: norm($("#email").value),
      direccion: norm($("#direccion").value),
      codigo_cliente: norm($("#codigo_cliente").value),
      notas: norm($("#notas").value),
      fecha_evento: norm($("#fecha_evento").value),
      id_comuna: $("#id_comuna").value ? Number($("#id_comuna").value) : null,
      id_tipo_cliente: $("#id_tipo_cliente").value ? Number($("#id_tipo_cliente").value) : null,
      id_marca: $("#id_marca").value ? Number($("#id_marca").value) : null,
      plataforma: norm($("#plataforma").value)
    }});
  };

  ["nombre_cliente","telefono","email","direccion","codigo_cliente","notas","fecha_evento"]
    .forEach(id => $("#"+id).addEventListener("input", sync));
  ["id_comuna","id_tipo_cliente","id_marca","plataforma"]
    .forEach(id => $("#"+id).addEventListener("change", sync));

  $("#btnClear").addEventListener("click", async ()=>{
    await msg({cmd:"setDraft", patch:{}});
    window.location.reload();
  });

  $("#btnCreate").addEventListener("click", async ()=>{
    const nombre = norm($("#nombre_cliente").value);
    if (!nombre){ toast("Falta NOMBRE", "err"); return; }

    const payload = {
      codigo_cliente: norm($("#codigo_cliente").value) || null,
      nombre_cliente: nombre,
      email: norm($("#email").value) || null,
      telefono: norm($("#telefono").value) || null,
      direccion: norm($("#direccion").value) || null,
      id_marca: $("#id_marca").value ? Number($("#id_marca").value) : null,
      id_estado: null,
      id_comuna: $("#id_comuna").value ? Number($("#id_comuna").value) : null,
      id_tipo_cliente: $("#id_tipo_cliente").value ? Number($("#id_tipo_cliente").value) : null,
      plataforma: norm($("#plataforma").value) || null,
      fecha_evento: norm($("#fecha_evento").value) || null,
      notas: norm($("#notas").value) || null
    };

    const ok = await modalConfirm({ title:"Crear lead?", text:nombre, okText:"Crear", cancelText:"Cancelar" });
    if (!ok) return;

    const btn = $("#btnCreate");
    btn.disabled = true;
    btn.textContent = "Creando...";

    try{
      const r = await msg({cmd:"createLead", payload, clearDraft:true});
      if (!r?.ok){
        const st = r?.status;
        if (st === 401 || st === 403) toast("No autorizado. Loguea en CRM.", "err");
        else toast("Error creando lead (" + st + ")", "err");
        btn.disabled = false;
        btn.textContent = "Crear lead";
        return;
      }

      toast("Lead creado ✔", "ok");
      setTimeout(()=>window.close(), 650);
    }catch(e){
      toast("Error: " + String(e?.message || e), "err");
      btn.disabled = false;
      btn.textContent = "Crear lead";
    }
  });
}

load();
