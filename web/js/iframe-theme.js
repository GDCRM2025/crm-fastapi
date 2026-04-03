/*
  Recibe tema desde el padre (index) y lo aplica en el iframe.
  Compatible con:
   - { cmd:"apply-theme", theme:"day"|"night" }
   - { type:"theme", mode:"light"|"dark" }
*/
function _apply(theme){
  // RRHH "Luna Azul" debe mantenerse fijo (fuera del toggle día/noche).
  if (document.documentElement.classList.contains("rrhh-luna")){
    document.documentElement.classList.add("light");
    document.documentElement.setAttribute("data-theme", "day");
    return;
  }
  const isDay = (theme === "day" || theme === "light");
  document.documentElement.classList.toggle("light", isDay);
  document.documentElement.setAttribute("data-theme", isDay ? "day" : "night");
}

addEventListener("message", (ev) => {
  const d = ev.data || {};
  if (d.cmd === "apply-theme" && d.theme){
    _apply(d.theme);
  }
  if (d.type === "theme" && d.mode){
    _apply(d.mode);
  }
  if (d.type === "prefs" && d.prefs){
    const p = d.prefs;
    if (p.font) document.documentElement.style.setProperty("--font", p.font);
    if (p.accent) document.documentElement.style.setProperty("--accent", p.accent);
    if (p.text) document.documentElement.style.setProperty("--text", p.text);
    if (p.fontSize) document.documentElement.style.setProperty("font-size", `${p.fontSize}px`);
  }
});

// Bootstrap por si el iframe se abre directo (sin padre)
try{
  const t = localStorage.getItem("THEME") || "night";
  _apply(t);
}catch(_){}

// Click-to-sort para tablas (todas las vistas dentro de iframes).
// - Click en un <th> ordena el <tbody>.
// - Segundo click invierte el orden.
// - Best-effort: no toca tablas sin <tbody> o con 0/1 filas.
(function enableTableSort(){
  const state = new WeakMap(); // table -> { col, dir }
  const wired = new WeakSet(); // th -> true

  function norm(s){
    return String(s ?? "").replace(/\s+/g, " ").trim();
  }

  function parseVal(raw){
    const s0 = norm(raw);
    const s = s0
      .replace(/\u00A0/g, " ")
      .replace(/\s/g, " ")
      .trim();
    if (!s) return { t: "s", v: "" };

    // dd/mm/yyyy
    const m1 = s.match(/^(\d{2})\/(\d{2})\/(\d{4})(?:\s+(\d{2}):(\d{2}))?$/);
    if (m1){
      const dd = Number(m1[1]), mm = Number(m1[2]), yy = Number(m1[3]);
      const hh = Number(m1[4] || 0), mi = Number(m1[5] || 0);
      const ts = Date.UTC(yy, mm - 1, dd, hh, mi, 0);
      return { t: "n", v: ts };
    }

    // yyyy-mm-dd (o yyyy-mm-ddTHH:MM)
    const m2 = s.match(/^(\d{4})-(\d{2})-(\d{2})(?:[T\s](\d{2}):(\d{2}))?/);
    if (m2){
      const yy = Number(m2[1]), mm = Number(m2[2]), dd = Number(m2[3]);
      const hh = Number(m2[4] || 0), mi = Number(m2[5] || 0);
      const ts = Date.UTC(yy, mm - 1, dd, hh, mi, 0);
      return { t: "n", v: ts };
    }

    // moneda / número
    const sNum = s.replace(/[^\d.,-]/g, "");
    if (sNum && /[\d]/.test(sNum)){
      // Heurística: si hay coma y punto, el último separador es decimal.
      let n = sNum;
      const lastDot = n.lastIndexOf(".");
      const lastComma = n.lastIndexOf(",");
      if (lastDot >= 0 && lastComma >= 0){
        const dec = Math.max(lastDot, lastComma);
        const intPart = n.slice(0, dec).replace(/[.,]/g, "");
        const decPart = n.slice(dec + 1).replace(/[.,]/g, "");
        n = intPart + "." + decPart;
      } else if (lastComma >= 0 && lastDot < 0){
        // solo coma: suele ser decimal
        n = n.replace(/\./g, "").replace(",", ".");
      } else {
        // solo punto o nada: quitar comas
        n = n.replace(/,/g, "");
      }
      const v = Number(n);
      if (Number.isFinite(v)) return { t: "n", v };
    }

    return { t: "s", v: s.toLowerCase() };
  }

  function cellKey(tr, col){
    const td = tr.cells && tr.cells[col];
    if (!td) return { t: "s", v: "" };
    const ds = td.getAttribute && td.getAttribute("data-sort");
    return parseVal(ds != null ? ds : (td.textContent || ""));
  }

  function sortByHeader(th){
    if (!th) return;
    if (th.closest("[data-nosort='1']")) return;
    const table = th.closest("table");
    if (!table) return;
    const tbody = table.tBodies && table.tBodies[0];
    if (!tbody) return;
    const rows = Array.from(tbody.rows || []);
    if (rows.length <= 1) return;

    const trh = th.parentElement;
    if (!trh) return;
    const headers = Array.from(trh.children || []);
    const col = headers.indexOf(th);
    if (col < 0) return;

    const prev = state.get(table) || { col: -1, dir: 1 };
    const dir = (prev.col === col) ? (prev.dir * -1) : 1;
    state.set(table, { col, dir });

    const pairs = rows.map((r, idx) => ({ r, idx, k: cellKey(r, col) }));
    pairs.sort((a, b) => {
      if (a.k.t === "n" && b.k.t === "n"){
        if (a.k.v === b.k.v) return a.idx - b.idx;
        return (a.k.v < b.k.v ? -1 : 1) * dir;
      }
      const av = String(a.k.v), bv = String(b.k.v);
      if (av === bv) return a.idx - b.idx;
      return (av < bv ? -1 : 1) * dir;
    });
    for (const p of pairs) tbody.appendChild(p.r);
  }

  function wireTable(table){
    try{
      if (!table || table.getAttribute("data-nosort") === "1") return;
      const headRow = table.tHead && table.tHead.rows && table.tHead.rows[0];
      if (!headRow) return;
      const ths = Array.from(headRow.cells || []);
      for (const th of ths){
        if (!th || wired.has(th)) continue;
        wired.add(th);
        th.style.cursor = th.style.cursor || "pointer";
        th.addEventListener("click", (ev)=>{
          ev.preventDefault();
          ev.stopPropagation();
          sortByHeader(th);
        });
      }
    }catch(_){}
  }

  function scan(){
    try{
      document.querySelectorAll("table").forEach(wireTable);
    }catch(_){}
  }

  // Initial
  if (document.readyState === "loading"){
    document.addEventListener("DOMContentLoaded", scan, { once: true });
  }else{
    scan();
  }

  // Re-scan when DOM changes (tables are often rendered dynamically).
  try{
    const obs = new MutationObserver(()=> scan());
    obs.observe(document.documentElement, { childList:true, subtree:true });
  }catch(_){}
})();
