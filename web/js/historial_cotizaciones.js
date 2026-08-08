const qs = (s, el=document)=> el.querySelector(s);

// API base (prod: /crm). Avoid hardcoding /quotes/* without prefix.
const API_BASE = (() => {
  try {
    const host = String(location.hostname || "").toLowerCase();
    const isLocal = host === "127.0.0.1" || host === "localhost";
    return isLocal ? "" : "/crm";
  } catch (_) {
    return "";
  }
})();
function apiURL(u){
  const s = String(u || "");
  if (!s) return s;
  if (s.startsWith("/crm/")) return s;
  if (s.startsWith("/")) return API_BASE + s;
  return API_BASE + "/" + s;
}

function themeSync(){
  const m = (localStorage.getItem("gd_theme")||"dark")==="light";
  document.documentElement.classList.toggle("light", m);
  window.addEventListener("message",(ev)=>{
    if(ev.data?.type==="theme") document.documentElement.classList.toggle("light", ev.data.mode==="light");
  });
}
themeSync();

function getToken(){ return localStorage.getItem("token") || sessionStorage.getItem("token") || ""; }
function authHeaders(extra={}){ const t=getToken(); return t?{...extra,Authorization:`Bearer ${t}`}:extra; }

function toast(title, msg, kind="ok"){
  const host = qs("#toastHost");
  const el = document.createElement("div");
  el.className = "toast";
  const ico = kind==="ok" ? "✅" : kind==="warn" ? "⚠️" : "❌";
  el.innerHTML = `
    <div>${ico}</div>
    <div><b>${title}</b><div style="color:var(--muted); font-weight:700">${msg}</div></div>
    <div class="x">✕</div>`;
  el.querySelector(".x").onclick = ()=> el.remove();
  host.appendChild(el);
  setTimeout(()=> el.remove(), 4200);
}

const ICONS = {
  pdf: `<svg viewBox="0 0 24 24" fill="none" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 13h8"/><path d="M8 17h6"/></svg>`,
  send:`<svg viewBox="0 0 24 24" fill="none" stroke-width="2"><path d="M22 2L11 13"/><path d="M22 2l-7 20-4-9-9-4z"/></svg>`
};
function iconBtn(svg, title, onClick){
  const b = document.createElement("button");
  b.type="button";
  b.className="icon";
  b.title=title;
  b.innerHTML=svg;
  b.onclick=(e)=>{ e.stopPropagation(); onClick?.(); };
  return b;
}

async function fetchHistory(){
  const params = new URLSearchParams(location.search);
  const idLead = params.get("id_lead") || params.get("lead_id") || "";
  const url = idLead ? apiURL(`/quotes/history?id_lead=${encodeURIComponent(idLead)}`) : apiURL("/quotes/history");
  const r = await fetch(url, { headers: authHeaders() });
  if(!r.ok) throw new Error(await r.text());
  const j = await r.json();
  return Array.isArray(j) ? j : (j.items || []);
}

// intenta encontrar detalle por varios endpoints sin romper
async function fetchItemsMaybe(q){
  if (q.items && Array.isArray(q.items)) return q.items;

  const ids = [q.id_cotizacion, q.id, q.quote_id].filter(Boolean);
  for (const id of ids){
    const tries = [
      apiURL(`/quotes/${id}/items`),
      apiURL(`/quotes/${id}`),
      apiURL(`/cotizaciones/${id}/items`),
      apiURL(`/cotizaciones/${id}/detalle`),
    ];
    for (const u of tries){
      try{
        const r = await fetch(u, { headers: authHeaders() });
        if(!r.ok) continue;
        const j = await r.json();
        if(Array.isArray(j)) return j;
        if(Array.isArray(j.items)) return j.items;
        if(Array.isArray(j.detalle)) return j.detalle;
      }catch(_){}
    }
  }
  return null;
}

function money(n){
  const x = Number(n||0);
  try{ return x.toLocaleString("es-CL"); }catch(_){ return String(x); }
}

function showTooltip(anchorRect, title, items){
  const tt = qs("#tt");
  tt.style.display = "block";
  const lines = (items||[]).slice(0,10).map(it=>{
    const n = it.nombre || it.producto || it.descripcion || it.name || "Item";
    const c = it.cantidad ?? it.qty ?? 1;
    return `<li>${n} × ${c}</li>`;
  }).join("");

  tt.innerHTML = `
    <h4>${title}</h4>
    ${items?.length ? `<ul>${lines}${items.length>10?`<li>… ${items.length-10} más</li>`:""}</ul>` : `<div style="color:var(--muted);font-weight:800">Sin detalle disponible.</div>`}
  `;

  const pad = 10;
  const x = Math.min(window.innerWidth - tt.offsetWidth - pad, anchorRect.left);
  const y = Math.min(window.innerHeight - tt.offsetHeight - pad, anchorRect.bottom + 8);
  tt.style.left = `${Math.max(pad,x)}px`;
  tt.style.top  = `${Math.max(pad,y)}px`;
}
function hideTooltip(){ qs("#tt").style.display="none"; }

function render(rows){
  qs("#countBox").textContent = `${rows.length} cotizaciones`;
  const out = qs("#out");
  if(!rows.length){
    out.innerHTML = `<div class="muted">Sin cotizaciones.</div>`;
    return;
  }

  const wrap = document.createElement("div");
  wrap.innerHTML = `
    <table>
      <thead><tr>
        <th>Fecha</th><th>Cliente</th><th>Marca</th><th>Total</th><th class="muted">Acciones</th>
      </tr></thead>
      <tbody></tbody>
    </table>`;
  const tb = wrap.querySelector("tbody");

  for (const q of rows){
    const tr = document.createElement("tr");
    const fecha = (q.created_at||q.fecha||q.fecha_creacion||"").slice(0,19).replace("T"," ");
    const cliente = q.nombre_cliente || q.cliente || q.customer || "";
    const marca = q.marca || q.nombre_marca || "";
    const total = q.total ?? q.monto_total ?? q.bruto ?? q.neto ?? 0;

    tr.innerHTML = `
      <td class="muted">${fecha}</td>
      <td style="font-weight:900">${cliente}</td>
      <td>${marca}</td>
      <td><span class="pill">$ ${money(total)}</span></td>
      <td><div class="actions"></div></td>
    `;

    // acciones: PDF / enviar (si existe link)
    const acts = tr.querySelector(".actions");

    const pdf = q.pdf_url || q.pdf_link || q.pdf || q.url_pdf || q.link_pdf;
    if (pdf){
      acts.appendChild(iconBtn(ICONS.pdf, "Ver PDF", ()=> window.open(pdf, "_blank", "noopener,noreferrer")));
    }
    acts.appendChild(iconBtn(ICONS.send, "Enviar (WhatsApp)", ()=>{
      const msg = encodeURIComponent(`Cotización ${cliente} — Total $${money(total)}`);
      window.open(`https://wa.me/?text=${msg}`, "_blank", "noopener,noreferrer");
    }));

    // tooltip hover
    tr.addEventListener("mouseenter", async ()=>{
      const rect = tr.getBoundingClientRect();
      const items = await fetchItemsMaybe(q);
      showTooltip(rect, "Productos cotizados", items);
    });
    tr.addEventListener("mouseleave", hideTooltip);

    tb.appendChild(tr);
  }

  out.innerHTML = "";
  out.appendChild(wrap);
}

(async function init(){
  try{
    const rows = await fetchHistory();
    render(rows);
  }catch(e){
    qs("#out").innerHTML = `<div class="muted">No se pudo cargar.</div>`;
    toast("Historial", "Error cargando historial de cotizaciones.", "err");
  }
})();
