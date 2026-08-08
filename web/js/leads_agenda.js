(function () {
  function qs(sel, el = document) { return el.querySelector(sel); }
  function qsa(sel, el = document) { return Array.from(el.querySelectorAll(sel)); }

  async function api(path, opts = {}) {
    const token = window.localStorage.getItem("token") || window.TOKEN || "";
    const headers = Object.assign(
      { "Content-Type": "application/json" },
      opts.headers || {},
      token ? { "Authorization": "Bearer " + token } : {}
    );
    const res = await fetch(path, Object.assign({}, opts, { headers }));
    const txt = await res.text();
    let data = null;
    try { data = txt ? JSON.parse(txt) : null; } catch (e) { data = { raw: txt }; }
    if (!res.ok) {
      const msg = (data && (data.detail || data.error)) ? (data.detail || data.error) : ("HTTP " + res.status);
      throw new Error(msg);
    }
    return data;
  }

  function ensureModal() {
    if (qs("#agendaConfirmModal")) return;

    const html = `
<div id="agendaConfirmModal" style="position:fixed; inset:0; background:rgba(0,0,0,.55); display:none; align-items:center; justify-content:center; z-index:9999;">
  <div style="width:min(820px, 96vw); background:#0b1220; color:#e5e7eb; border:1px solid rgba(255,255,255,.12); border-radius:16px; overflow:hidden;">
    <div style="display:flex; justify-content:space-between; align-items:center; padding:16px 18px; border-bottom:1px solid rgba(255,255,255,.10);">
      <div>
        <div style="font-size:18px; font-weight:700;">Agendar Lead Confirmado</div>
        <div id="agLeadMeta" style="font-size:12px; opacity:.75; margin-top:2px;"></div>
      </div>
      <button id="agClose" style="background:transparent; border:0; color:#e5e7eb; font-size:18px; cursor:pointer;">✕</button>
    </div>

    <div style="display:grid; grid-template-columns: 1fr 1fr; gap:14px; padding:16px 18px;">
      <div style="display:flex; flex-direction:column; gap:10px;">
        <label style="font-size:12px; opacity:.75;">Cotización aprobada</label>
        <select id="agCot" style="padding:10px; border-radius:10px; border:1px solid rgba(255,255,255,.12); background:#0f172a; color:#e5e7eb;"></select>

        <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">
          <div>
            <label style="font-size:12px; opacity:.75;">Inicio (HH:MM)</label>
            <input id="agStart" placeholder="22:00" style="width:100%; padding:10px; border-radius:10px; border:1px solid rgba(255,255,255,.12); background:#0f172a; color:#e5e7eb;" />
          </div>
          <div>
            <label style="font-size:12px; opacity:.75;">Fin (HH:MM)</label>
            <input id="agEnd" placeholder="02:00" style="width:100%; padding:10px; border-radius:10px; border:1px solid rgba(255,255,255,.12); background:#0f172a; color:#e5e7eb;" />
          </div>
        </div>

        <label style="display:flex; gap:8px; align-items:center; font-size:12px; opacity:.85;">
          <input type="checkbox" id="agHrTbd" />
          Hora por confirmar (HR TBD)
        </label>

        <label style="font-size:12px; opacity:.75;">Teléfono</label>
        <input id="agTel" placeholder="+569..." style="padding:10px; border-radius:10px; border:1px solid rgba(255,255,255,.12); background:#0f172a; color:#e5e7eb;" />

        <label style="font-size:12px; opacity:.75;">Dirección</label>
        <input id="agDir" placeholder="Salida Hipódromo..." style="padding:10px; border-radius:10px; border:1px solid rgba(255,255,255,.12); background:#0f172a; color:#e5e7eb;" />

        <div style="display:flex; gap:10px; margin-top:8px;">
          <button id="agNo" style="flex:1; padding:10px 12px; border-radius:10px; border:1px solid rgba(255,255,255,.14); background:rgba(255,255,255,.06); color:#e5e7eb; cursor:pointer;">No agendar</button>
          <button id="agYes" style="flex:1; padding:10px 12px; border-radius:10px; border:0; background:#4f46e5; color:white; font-weight:700; cursor:pointer;">Agendar</button>
        </div>

        <div id="agErr" style="color:#fca5a5; font-size:12px; display:none;"></div>
      </div>

      <div style="background:#0f172a; border:1px solid rgba(255,255,255,.10); border-radius:14px; padding:12px;">
        <div style="font-size:13px; font-weight:700; margin-bottom:8px;">Preview</div>
        <pre id="agPreview" style="white-space:pre-wrap; font-family:ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size:12px; line-height:1.35; margin:0; opacity:.92;"></pre>
      </div>
    </div>
  </div>
</div>`;
    document.body.insertAdjacentHTML("beforeend", html);

    qs("#agClose").addEventListener("click", close);
    qs("#agNo").addEventListener("click", async () => {
      try {
        const st = window.__AG_STATE;
        await api(`/leads/${st.id_lead}/move`, { method: "POST", body: JSON.stringify({ id_estado: st.id_estado, agendar: false }) });
        close();
      } catch (e) {
        showErr(e.message);
      }
    });

    qs("#agYes").addEventListener("click", async () => {
      try {
        const st = window.__AG_STATE;
        const id_cotizacion = parseInt(qs("#agCot").value || "0", 10) || null;
        const payload = {
          id_estado: st.id_estado,
          agendar: true,
          id_cotizacion,
          telefono: qs("#agTel").value || "",
          direccion: qs("#agDir").value || "",
          start_time: qs("#agStart").value || null,
          end_time: qs("#agEnd").value || null,
          hr_tbd: qs("#agHrTbd").checked
        };
        const r = await api(`/leads/${st.id_lead}/move`, { method: "POST", body: JSON.stringify(payload) });
        close();
        // opcional: refrescar board
        if (window.reloadLeads) window.reloadLeads();
      } catch (e) {
        showErr(e.message);
      }
    });

    ["#agCot", "#agStart", "#agEnd", "#agHrTbd", "#agTel", "#agDir"].forEach(sel => {
      qs(sel).addEventListener("change", refreshPreview);
      qs(sel).addEventListener("keyup", refreshPreview);
    });
  }

  function showErr(msg) {
    const el = qs("#agErr");
    el.style.display = "block";
    el.textContent = msg;
  }
  function clearErr() {
    const el = qs("#agErr");
    el.style.display = "none";
    el.textContent = "";
  }
  function open() {
    qs("#agendaConfirmModal").style.display = "flex";
  }
  function close() {
    qs("#agendaConfirmModal").style.display = "none";
    clearErr();
  }

  async function refreshPreview() {
    try {
      clearErr();
      const st = window.__AG_STATE;
      if (!st) return;

      const id_cotizacion = parseInt(qs("#agCot").value || "0", 10) || null;
      if (!id_cotizacion) return;

      // Preview: hacemos un “dry run” llamando al mismo endpoint pero sin persistir? (no existe)
      // Para no meter endpoints extra: construimos preview del lado del server SOLO cuando agendamos.
      // Aquí dejamos un preview mínimo a partir del input.
      const hr = qs("#agHrTbd").checked ? "HR TBD" : ((qs("#agStart").value || "??:??") + " - " + (qs("#agEnd").value || "??:??"));
      qs("#agPreview").textContent =
`Cotización: ${id_cotizacion}
Teléfono: ${qs("#agTel").value || "(vacío)"}
Dirección: ${qs("#agDir").value || "(vacío)"}
Horario: ${hr}

Al confirmar, el sistema generará:
- PRODUCTOS (agrupados)
- 🛠️ Montaje sugerido
- 👥 OPS
- Descripción formato Calendar`;
    } catch (e) {
      showErr(e.message);
    }
  }

  async function onMoveLead(id_lead, id_estado) {
    ensureModal();
    clearErr();

    const r = await api(`/leads/${id_lead}/move`, {
      method: "POST",
      body: JSON.stringify({ id_estado })
    });

    if (!r.ask_agendar) return r;

    // preparar modal
    window.__AG_STATE = { id_lead, id_estado };

    const lead = r.lead || {};
    const cotizaciones = r.cotizaciones || [];

    qs("#agLeadMeta").textContent =
      `${lead.nombre_cliente || ""} • Fecha: ${lead.fecha_evento || ""}`;

    // llenar selects
    const sel = qs("#agCot");
    sel.innerHTML = "";
    if (!cotizaciones.length) {
      sel.insertAdjacentHTML("beforeend", `<option value="">(Sin cotizaciones)</option>`);
    } else {
      cotizaciones.forEach(c => {
        const idc = c.id_cotizacion || c.id || "";
        const total = (c.total !== undefined) ? ` • Total: ${c.total}` : "";
        const fecha = (c.created_at || c.fecha || c.fecha_cotizacion || "");
        sel.insertAdjacentHTML("beforeend", `<option value="${idc}">#${idc}${fecha ? (" • " + fecha) : ""}${total}</option>`);
      });
    }

    qs("#agTel").value = lead.telefono || "";
    qs("#agDir").value = lead.direccion || "";
    qs("#agStart").value = "";
    qs("#agEnd").value = "";
    qs("#agHrTbd").checked = false;

    open();
    await refreshPreview();
    return r;
  }

  window.LeadsAgenda = { onMoveLead };
})();
