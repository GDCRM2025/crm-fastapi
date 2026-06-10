(async function () {
  const qs = new URLSearchParams(location.search);
  const leadId = +(qs.get("id_lead") || qs.get("lead") || qs.get("lead_id") || 0);

  // API base (prod: /crm). Many views are served under /crm/web/... but fetch() must hit /crm/*.
  const API_BASE = (() => {
    try {
      const host = String(location.hostname || "").toLowerCase();
      const isLocal = host === "127.0.0.1" || host === "localhost";
      return isLocal ? "" : "/crm";
    } catch (_) {
      return "";
    }
  })();
  function apiURL(u) {
    const s = String(u || "");
    if (!s) return s;
    if (s.startsWith("/crm/")) return s;
    if (s.startsWith("/")) return API_BASE + s;
    return API_BASE + "/" + s;
  }

  // BLOQUEO: solo se usa con id_lead; si no viene, redirige a Leads.
  if (!leadId) {
    location.href = apiURL("/web/views/leads.html");
    return;
  }

  const $ = (id) => document.getElementById(id);
  const fmt = (n) => new Intl.NumberFormat("es-CL", { maximumFractionDigits: 0 }).format(n || 0);

  const selCot = $("selCotizacion");
  const nroCot = $("nroCot");
  const fechaCot = $("fechaCot");
  const logoMarca = $("logoMarca");
  const cliente = $("cliente");
  const email = $("email");
  const telefono = $("telefono");
  const comuna = $("comuna");
  const direccion = $("direccion");
  const fechaEvento = $("fechaEvento");

  const dlComunas = $("dlComunas");
  const dlProductos = $("dlProductos");
  const txtProducto = $("txtProducto");
  const cant = $("cant");
  const precio = $("precio");
  const btnAddItem = $("btnAddItem");
  const tblItems = $("tblItems").querySelector("tbody");

  const tProductos = $("tProductos");
  const traslado = $("traslado");
  const tipoDesc = $("tipoDesc");
  const descuento = $("descuento");
  const tNeto = $("tNeto");
  const tIva = $("tIva");
  const tTotal = $("tTotal");
  const ivaLegend = $("ivaLegend");

  const btnGuardar = $("btnGuardar");
  const btnPreview = $("btnPreview");
  const btnEnviar = $("btnEnviar");
  const btnCerrar = $("btnCerrar");

  let lead = null;
  let catalogos = { comunas: [], tipos_cliente: [], marcas: [] };
  let productosIndex = [];
  let productosByKey = new Map();
  let items = [];
  let tipoCliente = "Persona";
  let currentQuoteId = null;
  let currentQuoteNumero = null;

  fechaCot.valueAsDate = new Date();

  function enableEditRow(row, textEl, input, btn, { onStop } = {}){
    if (!row || !textEl || !input || !btn) return;
    const fmtText = () => {
      const v = String(input.value || "").trim();
      if (input.type === "date" && /^\d{4}-\d{2}-\d{2}$/.test(v)){
        return `${v.slice(8,10)}/${v.slice(5,7)}/${v.slice(0,4)}`;
      }
      return v || "—";
    };
    const stop = () => {
      row.classList.remove("editing");
      textEl.textContent = fmtText();
      try{ if (typeof onStop === "function") onStop(); }catch(_){}
    };
    const start = () => {
      row.classList.add("editing");
      try{ input.focus(); }catch(_){}
    };
    btn.addEventListener("click", ()=>{
      if (row.classList.contains("editing")) stop();
      else start();
    });
    input.addEventListener("blur", stop);
    input.addEventListener("keydown", (e)=>{
      if (e.key === "Enter") { e.preventDefault(); stop(); }
      if (e.key === "Escape") { e.preventDefault(); stop(); }
    });
    stop();
  }

  function normKey(s){
    return String(s || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toUpperCase()
      .replace(/[^A-Z0-9]+/g, "");
  }

  function getToken(){
    return (
      localStorage.getItem("token") ||
      localStorage.getItem("gd_token") ||
      sessionStorage.getItem("token") ||
      sessionStorage.getItem("gd_token") ||
      ""
    );
  }
  function authHeaders(extra = {}) {
    const t = getToken();
    return t ? { ...extra, Authorization: `Bearer ${t}` } : extra;
  }

  async function fetchJson(u, opts = {}) {
    const url = apiURL(u);
    const r = await fetch(url, { ...opts, headers: authHeaders(opts.headers || {}) });
    if (!r.ok) {
      const txt = await r.text();
      throw new Error(`HTTP ${r.status} @ ${url} :: ${txt.substring(0,200)}`);
    }
    return r.json();
  }

  async function getLead() {
    lead = await fetchJson(`/leads/${leadId}`);
    cliente.value =
      lead.cliente ||
      lead.nombre_cliente ||
      lead.nombre_contacto ||
      lead.contacto ||
      lead.nombre ||
      lead.name ||
      lead.razon_social ||
      "";
    email.value = lead.email || lead.mail || lead.correo || lead.email_cliente || "";
    telefono.value =
      lead.telefono ||
      lead.telefono1 ||
      lead.celular ||
      lead.telefono_cliente ||
      lead.ventas_telefono ||
      "";
    comuna.value = lead.comuna_nombre || lead.comuna || lead.comuna_txt || "";
    direccion.value = lead.direccion || lead.direccion1 || lead.direccion_txt || "";
    fechaEvento.value = (lead.fecha_evento || "").substring(0,10);
    tipoCliente = (lead.tipo_cliente_nombre || lead.tipo_cliente || "Persona");
    if (lead.logo_url) logoMarca.src = lead.logo_url;
    try{
      $("clienteText").textContent = String(cliente.value || "").trim() || "—";
      $("emailText").textContent = String(email.value || "").trim() || "—";
      $("telefonoText").textContent = String(telefono.value || "").trim() || "—";
      $("comunaText").textContent = String(comuna.value || "").trim() || "—";
      $("direccionText").textContent = String(direccion.value || "").trim() || "—";
      const fe = String(fechaEvento.value || "").trim();
      $("fechaEventoText").textContent = (/^\d{4}-\d{2}-\d{2}$/.test(fe)) ? `${fe.slice(8,10)}/${fe.slice(5,7)}/${fe.slice(0,4)}` : (fe || "—");
      $("tipoClienteLbl").textContent = String(tipoCliente || "—");
    }catch(_){}
  }

  async function getCatalogos() {
    catalogos = await fetchJson(`/leads/catalogos`);
    dlComunas.innerHTML = "";
    for (const c of catalogos.comunas || []) {
      const opt = document.createElement("option");
      opt.value = c.nombre;
      const base = (c.neto ?? c.costo_traslado ?? 0);
      opt.label = `${c.nombre} - $${fmt(base)}`;
      dlComunas.appendChild(opt);
    }
    if ((!logoMarca.src || logoMarca.src.endsWith("/")) && lead?.marca_nombre) {
      const m = (catalogos.marcas || []).find(x => (x.marca || x.nombre || "").toString().toUpperCase() === lead.marca_nombre.toString().toUpperCase());
      if (m && m.logo_path) logoMarca.src = m.logo_path;
    }
  }

  async function getProductos() {
    // Cache por sesión para acelerar abrir el cotizador (muy usado por ejecutivos).
    const cacheKey = `gd_cotizador_products_${(lead?.marca_nombre || lead?.marca || "ALL").toString().trim().toUpperCase()}`;
    let cachedArr = null;
    try{
      const cached = sessionStorage.getItem(cacheKey);
      if (cached){
        const parsed = JSON.parse(cached);
        // compat: antes guardábamos solo array
        const arr = Array.isArray(parsed) ? parsed : (parsed && Array.isArray(parsed.items) ? parsed.items : []);
        if (Array.isArray(arr) && arr.length){
          cachedArr = arr;
          // Pintamos rápido, pero igual refrescamos desde servidor para no “perder” productos nuevos.
          productosIndex = arr;
          dlProductos.innerHTML = "";
          for (const p of productosIndex) {
            const opt = document.createElement("option");
            opt.value = p.producto;
            opt.label = p.producto;
            dlProductos.appendChild(opt);
          }
        }
      }
    }catch(_){}

    productosIndex = [];
    productosByKey = new Map();
    const marcaLead = (lead?.marca_nombre || lead?.marca || "").trim();
    // Para performance: preferimos traer todo en 1 request (en este sistema son ~600 items).
    // Evita el loop paginado que a veces se siente "lento" al abrir cotizador.
    const limit = 5000;

    async function fetchOnce(endpoint){
      const url = endpoint.includes("?")
        ? `${endpoint}&limit=${limit}&offset=0`
        : `${endpoint}?limit=${limit}&offset=0`;
      const data = await fetchJson(url);
      const chunk = (data.items || data || []);
      return Array.isArray(chunk) ? chunk : [];
    }

    let chunk = [];
    if (marcaLead){
      try{
        chunk = await fetchOnce(`/productos?marca=${encodeURIComponent(marcaLead)}`);
      }catch(_){}
    }
    if (!chunk.length){
      // Fallback seguro: /productos (filtra por rol/marcas del usuario).
      // IMPORTANTE: no dependemos de /settings/* (a veces un deploy parcial deja /settings 404).
      try{
        chunk = await fetchOnce(`/productos?only_active=true`);
      }catch(_){
        // último fallback: settings endpoint (best-effort)
        try{
          chunk = await fetchOnce(`/settings/productos?active_only=true`);
        }catch(__){
          chunk = [];
        }
      }
    }

    for (const p of chunk) {
      const row = {
        id_producto: p.id_producto,
        producto: p.producto || "",
        descripcion: p.descripcion || p.ingredientes || "",
        ingredientes: "",
        costo: +p.costo || 0,
        marca: p.marca || "",
      };
      productosIndex.push(row);
      const k = normKey(row.producto);
      if (k && !productosByKey.has(k)) productosByKey.set(k, row);
    }
    dlProductos.innerHTML = "";
    for (const p of productosIndex) {
      const opt = document.createElement("option");
      opt.value = p.producto;
      opt.label = p.producto;
      dlProductos.appendChild(opt);
    }

    if (!productosIndex.length && cachedArr && cachedArr.length){
      // Si falló el fetch (o filtró todo), mantenemos el cache previo para no dejar cotizador vacío.
      productosIndex = cachedArr;
      try{
        productosByKey = new Map();
        for (const p of productosIndex){
          const k = normKey(p?.producto || "");
          if (k && !productosByKey.has(k)) productosByKey.set(k, p);
        }
      }catch(_){}
      return;
    }
    try{
      if (productosIndex.length) sessionStorage.setItem(cacheKey, JSON.stringify({ ts: Date.now(), items: productosIndex }));
    }catch(_){}
  }

  async function getQuotesHistory() {
    const data = await fetchJson(`/quotes/history?id_lead=${leadId}`);
    selCot.innerHTML = "";
    const optNew = document.createElement("option");
    optNew.value = "";
    optNew.textContent = "Nueva cotización";
    selCot.appendChild(optNew);
    for (const q of data.items || []) {
      const opt = document.createElement("option");
      opt.value = q.id_cotizacion;
      const num = q.numero ? `N°${q.numero}` : `#${q.id_cotizacion}`;
      const rev = (q.revision != null) ? Number(q.revision) : Number(q.version || 0);
      const ver = rev > 0 ? ` (${rev})` : "";
      const fechaRaw = q.fecha_evento || q.fecha_emision || "";
      const fecha = fechaRaw ? String(fechaRaw).slice(0,10) : "";
      opt.textContent = `${num}${ver}${fecha ? " · " + fecha : ""}`;
      selCot.appendChild(opt);
    }
    if (nroCot) nroCot.textContent = "—";
  }

  function comunaBase(nombre) {
    const c = (catalogos.comunas || []).find(x => (x.nombre || "").toLowerCase() === (nombre || "").toLowerCase());
    if (!c) return 0;
    return Number(c.neto ?? c.costo_traslado ?? 0) || 0;
  }

  function isEmpresa(){
    return String(tipoCliente || "").toLowerCase().includes("empresa");
  }

  function calcTraslado(){
    const base = comunaBase(comuna.value || "");
    // Traslado no incluye IVA. Si corresponde IVA (EMPRESA), se aplica al final sobre el total neto.
    return Math.round(base);
  }

  function drawItems() {
    tblItems.innerHTML = "";
    for (const it of items) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>
          <div>${it.producto}</div>
          <div class="muted" style="font-size:12px;">${it.descripcion || ""}</div>
        </td>
        <td class="right">${fmt(it.cantidad)}</td>
        <td class="right">$${fmt(it.precio)}</td>
        <td class="right">$${fmt(it.subtotal)}</td>
        <td class="right">
          <div class="controls" style="justify-content:flex-end">
            <button class="btn" data-edit="${it.id_detalle}">Editar</button>
            <button class="btn danger" data-del="${it.id_detalle}">Eliminar</button>
          </div>
        </td>
      `;
      tblItems.appendChild(tr);
    }
    calcTotals();
  }

  function calcTotals() {
    const subtotal = items.reduce((s, it) => s + (+it.subtotal || 0), 0);
    tProductos.textContent = "$" + fmt(subtotal);

    const mov = +traslado.value || 0;
    let desc = +descuento.value || 0;
    if (tipoDesc.value === "%") desc = Math.round((desc / 100) * subtotal);

    // Regla negocio:
    // - Pipeline ejecutivo = TOTAL PRODUCTOS - DESCUENTO (sin traslado, sin IVA)
    // - Neto afecto a IVA = (TOTAL PRODUCTOS - DESCUENTO) + TRASLADO
    // - IVA (si EMPRESA) se calcula sobre productos netos + traslado
    const productosNeto = Math.max(0, subtotal - desc);
    const neto = productosNeto + mov; // "Neto afecto a IVA" (incluye traslado)
    tNeto.textContent = "$" + fmt(neto);

    const iva = isEmpresa() ? Math.round(neto * 0.19) : 0;
    tIva.textContent = "$" + fmt(iva);

    const total = neto + iva;
    tTotal.textContent = "$" + fmt(total);
    if (ivaLegend) {
      ivaLegend.textContent = isEmpresa()
        ? `IVA 19% calculado sobre productos netos + traslado ($${fmt(productosNeto)} + $${fmt(mov)} = $${fmt(neto)}): $${fmt(iva)}.`
        : `Sin IVA (Particular).`;
    }
  }

  // --- listeners ---
  $("btnAddItem").addEventListener("click", async () => {
    const prodName = (txtProducto.value || "").trim();
    const qty = parseInt(String(cant.value || "").trim(), 10) || 0;
    const price = +precio.value || 0;
    if (!prodName) return;
    if (qty <= 0){
      alert("Indica una cantidad.");
      try{ cant.focus(); }catch(_){}
      return;
    }
    if (price < 0) return;

    let prod = productosIndex.find(p => String(p.producto||"").toLowerCase() === prodName.toLowerCase());
    if (!prod){
      const k = normKey(prodName);
      prod = (k && productosByKey.get(k)) || null;
    }
    if (!prod){
      const k = normKey(prodName);
      if (k){
        const sug = [];
        for (const p of productosIndex){
          const pk = normKey(p?.producto || "");
          if (pk && (pk.includes(k) || k.includes(pk))) sug.push(p);
          if (sug.length >= 8) break;
        }
        if (sug.length){
          alert("Producto no encontrado. ¿Quizás quisiste decir?\n- " + sug.map(x=>x.producto).join("\n- "));
        } else {
          alert("Producto inválido");
        }
      } else {
        alert("Producto inválido");
      }
      return;
    }
    const it = {
      id_detalle: Date.now(),
      id_producto: prod.id_producto,
      producto: prod.producto,
      descripcion: prod.descripcion || prod.ingredientes || "",
      cantidad: qty,
      precio: price || prod.costo || 0,
      subtotal: (price || prod.costo || 0) * qty
    };
    items.push(it);
    txtProducto.value = "";
    cant.value = "";
    precio.value = 0;
    drawItems();
  });

  $("tblItems").addEventListener("click", async (ev) => {
    const btnDel = ev.target.closest("button[data-del]");
    if (btnDel){
      const idd = +btnDel.dataset.del;
      items = items.filter(x => x.id_detalle !== idd);
      drawItems();
      return;
    }
    const btnEdit = ev.target.closest("button[data-edit]");
    if (btnEdit){
      const idd = +btnEdit.dataset.edit;
      const it = items.find(x => x.id_detalle === idd);
      if (!it) return;
      if (window.Swal){
        const res = await Swal.fire({
          title: "Editar ítem",
          html: `
            <div style="text-align:left;display:grid;gap:10px">
              <label>Cantidad <input id="sw_qty" class="swal2-input" type="number" min="1" value="${it.cantidad || 1}"></label>
              <label>Valor unitario <input id="sw_price" class="swal2-input" type="number" min="0" value="${it.precio || 0}"></label>
            </div>
          `,
          showCancelButton: true,
          confirmButtonText: "Guardar",
          cancelButtonText: "Cancelar",
          preConfirm: ()=>{
            const q = Number(document.getElementById("sw_qty").value || 1);
            const p = Number(document.getElementById("sw_price").value || 0);
            if (q <= 0) return Swal.showValidationMessage("Cantidad inválida");
            if (p < 0) return Swal.showValidationMessage("Precio inválido");
            return { q, p };
          }
        });
        if (!res.isConfirmed) return;
        const nq = Math.max(1, parseInt(res.value.q, 10) || 1);
        const np = Math.max(0, parseInt(res.value.p, 10) || 0);
        it.cantidad = nq;
        it.precio = np;
        it.subtotal = nq * np;
        drawItems();
      } else {
        const q = prompt("Cantidad", String(it.cantidad || 1));
        if (q === null) return;
        const p = prompt("Valor unitario", String(it.precio || 0));
        if (p === null) return;
        const nq = Math.max(1, parseInt(q, 10) || 1);
        const np = Math.max(0, parseInt(p, 10) || 0);
        it.cantidad = nq;
        it.precio = np;
        it.subtotal = nq * np;
        drawItems();
      }
    }
  });

  ["traslado","descuento","tipoDesc"].forEach(id => {
    const el = $(id);
    el.addEventListener("input", calcTotals);
    el.addEventListener("change", calcTotals);
  });

  $("comuna").addEventListener("change", () => {
    traslado.value = calcTraslado();
    calcTotals();
  });

  $("selCotizacion").addEventListener("change", async () => {
    const val = selCot.value;
    if (!val) {
      currentQuoteId = null;
      currentQuoteNumero = null;
      if (nroCot) nroCot.textContent = "—";
      items = [];
      drawItems();
      return;
    }
    try{
      const q = await fetchJson(`/quotes/${val}`);
      const info = q.cotizacion || {};
      currentQuoteId = Number(info.id_cotizacion || val);
      currentQuoteNumero = info.numero || null;
      const rev = (info.revision != null) ? Number(info.revision) : Number(info.version || 0);
      const ver = rev > 0 ? ` (${rev})` : "";
      if (nroCot) nroCot.textContent = currentQuoteNumero ? String(currentQuoteNumero) + ver : String(currentQuoteId);

      if (info.fecha) fechaCot.value = String(info.fecha).slice(0,10);
      if (info.fecha_evento) fechaEvento.value = String(info.fecha_evento).slice(0,10);
      if (info.nombre_cliente && !cliente.value) cliente.value = info.nombre_cliente;
      if (info.traslado != null) traslado.value = Number(info.traslado);
      if (info.descuento_valor != null) descuento.value = Number(info.descuento_valor);
      if (info.descuento_tipo) tipoDesc.value = info.descuento_tipo;

      const it = await fetchJson(`/quotes/${val}/items`);
      items = (it.items || []).map((x, idx)=>({
        id_detalle: Date.now() + idx,
        id_producto: x.id_producto,
        producto: x.producto,
        descripcion: x.descripcion || "",
        cantidad: Number(x.cantidad || 0),
        precio: Number(x.precio_unitario || 0),
        subtotal: Number(x.total_linea || ((x.cantidad||0) * (x.precio_unitario||0)) || 0),
      }));
      drawItems();
      calcTotals();
    }catch(e){
      console.error(e);
      alert("No pude cargar la cotización seleccionada.");
    }
  });

  function safeFilename(s){
    return String(s || "")
      .replace(/[\\/:*?"<>|]+/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 140);
  }

  async function downloadQuotePdf(idCot, numero, nombreCliente){
    const id = Number(idCot || 0);
    if (!id) throw new Error("id_cotizacion inválido");
    const numTxt = numero ? String(numero) : String(id);
    const cliTxt = safeFilename(nombreCliente || lead?.cliente || lead?.nombre_cliente || "cliente");
    const filename = `Cotizacion ${numTxt} - ${cliTxt}.pdf`;

    const url = apiURL(`/quotes/${id}/pdf?download=1&v=${Date.now()}`);
    const r = await fetch(url, { headers: authHeaders() });
    if (!r.ok){
      const txt = await r.text();
      throw new Error(`No pude descargar PDF (HTTP ${r.status}). ${txt.substring(0,160)}`);
    }
    const blob = await r.blob();
    const obj = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = obj;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(()=>{ try{ URL.revokeObjectURL(obj); }catch(_){} }, 60_000);
  }

  $("btnGuardar").addEventListener("click", async () => {
    if (!items.length){
      if (window.Swal) Swal.fire({ icon:"warning", title:"Falta info", text:"Agrega al menos un producto." });
      else alert("Agrega al menos un producto.");
      return;
    }

    const subtotal = items.reduce((s, it) => s + (+it.subtotal || 0), 0);
    let desc = +descuento.value || 0;
    if (tipoDesc.value === "%") desc = Math.round((desc / 100) * subtotal);
    const neto = Math.max(0, subtotal - desc);

    const payload = {
      id_lead: leadId,
      cliente: cliente.value || lead?.cliente,
      nombre_cliente: cliente.value || lead?.cliente,
      email: email.value || lead?.email || null,
      telefono: telefono.value || lead?.telefono || null,
      comuna: comuna.value || lead?.comuna || null,
      direccion: direccion.value || lead?.direccion || null,
      marca: lead?.marca_nombre || lead?.marca || "",
      fecha_evento: fechaEvento.value || lead?.fecha_evento || null,
      tipo_cliente: tipoCliente || "",
      traslado: +traslado.value || 0,
      descuento_valor: +descuento.value || 0,
      descuento_tipo: tipoDesc.value,
      items: items.map(it => ({
        id_producto: it.id_producto,
        cantidad: it.cantidad,
        precio_unitario: it.precio,
      })),
    };

    const oldTxt = btnGuardar?.textContent || "Guardar";
    try{
      btnGuardar.disabled = true;
      btnGuardar.textContent = "Guardando…";
      btnGuardar.style.opacity = ".85";

      let r = null;
      if (currentQuoteId){
        r = await fetchJson(`/cotizador/cotizaciones/${currentQuoteId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        currentQuoteId = r.id_cotizacion || currentQuoteId;
        if (r.version != null && currentQuoteNumero != null){
          const ver = Number(r.version || 0);
          if (nroCot) nroCot.textContent = ver > 0 ? `${currentQuoteNumero} (${ver})` : String(currentQuoteNumero);
        }
      } else {
        r = await fetchJson(`/cotizador/cotizar`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        currentQuoteId = r.id_cotizacion;
        if (r.numero) {
          currentQuoteNumero = r.numero;
          if (nroCot) nroCot.textContent = String(r.numero);
        }
      }

      // feedback breve y seguimos al historial
      const msg = `Total $${fmt(r?.total || 0)}`;
      if (window.Swal) {
        await Swal.fire({ icon:"success", title:"Cotización guardada", text: msg, timer: 900, showConfirmButton:false });
      } else {
        alert("Cotización guardada. " + msg);
      }

      // Descargar PDF de inmediato (sin abrir pestañas).
      if (currentQuoteId){
        try{
          await downloadQuotePdf(currentQuoteId, currentQuoteNumero, cliente.value || lead?.cliente || "");
        }catch(err){
          console.error(err);
          if (window.Swal){
            await Swal.fire({
              icon:"info",
              title:"PDF listo",
              text:"No pude iniciar la descarga automática. Puedes descargar desde Historial.",
              timer: 1800,
              showConfirmButton: false
            });
          }
        }
      }

      setTimeout(() => {
        location.href = apiURL(`/web/views/historial_cotizaciones.html?id_lead=${leadId}`);
      }, 220);
    }catch(e){
      console.error(e);
      const raw = String(e?.message || e || "");
      const msg =
        raw.includes("401") ? "Tu sesión venció. Vuelve a iniciar sesión y reintenta." :
        raw.includes("403") ? "No tienes permiso para guardar cotizaciones en esta marca." :
        raw || "No pude guardar la cotización.";
      if (window.Swal) Swal.fire({ icon:"error", title:"No se pudo guardar", text: msg.slice(0, 300) });
      else alert("No se pudo guardar: " + msg);
    }finally{
      btnGuardar.disabled = false;
      btnGuardar.textContent = oldTxt;
      btnGuardar.style.opacity = "1";
    }
  });

  $("btnPreview").addEventListener("click", () => {
    if (currentQuoteId){
      // Muestra loader mientras el PDF se genera/abre.
      // No forzar refresh por defecto: si no, regeneramos el PDF/Drive assets cada vez y se vuelve lento.
      // Para refrescar assets manualmente, usa ?refresh=1 (desde Historial/acciones).
      const pdf = apiURL(`/quotes/${currentQuoteId}/pdf?rebuild=1&v=${Date.now()}`);
      const wait = apiURL(`/web/views/pdf_wait.html?u=${encodeURIComponent(pdf)}&t=${encodeURIComponent("Abriendo PDF…")}`);
      window.open(wait, "_blank", "noopener,noreferrer");
      return;
    }
    if (!items.length){ alert("Agrega al menos un producto."); return; }
    const w = window.open("", "_blank");
    if (!w) { alert("Popup bloqueado"); return; }
    const rows = items.map(it => `
      <tr>
        <td>${it.producto}<div style="opacity:.7;font-size:12px">${it.descripcion||""}</div></td>
        <td style="text-align:right">${fmt(it.cantidad)}</td>
        <td style="text-align:right">$${fmt(it.precio)}</td>
        <td style="text-align:right">$${fmt(it.subtotal)}</td>
      </tr>`).join("");
    w.document.write(`
      <html><head><title>Cotización</title>
      <style>body{font-family:sans-serif} table{width:100%;border-collapse:collapse} td,th{border-bottom:1px solid #ddd;padding:6px}</style>
      </head><body>
      <h2>Cotización</h2>
      <div><b>Cliente:</b> ${cliente.value}</div>
      <div><b>Comuna:</b> ${comuna.value}</div>
      <div><b>Fecha evento:</b> ${fechaEvento.value || ""}</div>
      <table><thead><tr><th>Producto</th><th>Cant</th><th>Precio</th><th>Total</th></tr></thead><tbody>${rows}</tbody></table>
      </body></html>
    `);
    w.document.close();
  });

  $("btnEnviar").addEventListener("click", () => {
    const tel = String(telefono.value || "").replace(/\\D/g, "");
    if (!tel){ alert("Lead sin teléfono"); return; }
    if (!currentQuoteId){
      alert("Primero guarda la cotización para poder enviarla.");
      return;
    }
    const link = `${location.origin}${apiURL(`/quotes/${currentQuoteId}/pdf`)}`;
    const nro = currentQuoteNumero ? `N° ${currentQuoteNumero}` : `ID ${currentQuoteId}`;
    const marcaTxt = (lead?.marca_nombre || lead?.marca || "").trim();
    const fechaTxt = (fechaEvento.value || "").trim();
    const totalTxt = tTotal?.textContent ? tTotal.textContent.replace(/\\s+/g," ") : "";
    const itemsTxt = (items || []).slice(0, 8).map(it => `• ${it.producto} x${it.cantidad}`).join("\\n");
    const msg = encodeURIComponent(
      `Hola ${cliente.value}, te envío la cotización ${nro}.\\n` +
      `${marcaTxt ? "Marca: " + marcaTxt + "\\n" : ""}` +
      `${fechaTxt ? "Fecha evento: " + fechaTxt + "\\n" : ""}` +
      `${totalTxt ? "Total: " + totalTxt + "\\n" : ""}` +
      `${itemsTxt ? "Productos:\\n" + itemsTxt + "\\n" : ""}` +
      `PDF: ${link}`
    );
    window.open(`https://wa.me/${tel}?text=${msg}`, "_blank", "noopener,noreferrer");
  });
  $("btnCerrar").addEventListener("click", () => { location.href = apiURL("/web/views/leads.html"); });

  // --- bootstrap ---
  try {
    await getLead();
    await getCatalogos();
    traslado.value = calcTraslado();
    await getQuotesHistory();
    await getProductos();
    enableEditRow(cliente.closest(".fieldRow"), $("clienteText"), cliente, $("editCliente"));
    enableEditRow(email.closest(".fieldRow"), $("emailText"), email, $("editEmail"));
    enableEditRow(telefono.closest(".fieldRow"), $("telefonoText"), telefono, $("editTelefono"));
    enableEditRow(fechaEvento.closest(".fieldRow"), $("fechaEventoText"), fechaEvento, $("editFechaEvento"));
    enableEditRow(comuna.closest(".fieldRow"), $("comunaText"), comuna, $("editComuna"), {
      onStop: () => {
        try{ traslado.value = calcTraslado(); }catch(_){}
        try{ calcTotals(); }catch(_){}
      },
    });
    enableEditRow(direccion.closest(".fieldRow"), $("direccionText"), direccion, $("editDireccion"));
    calcTotals();
  } catch (e) {
    console.error(e);
    alert("Error inicializando el cotizador: " + (e.message || e));
  }
})();
