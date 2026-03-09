// web/settings/_base.js
export function initCrud(cfg) {
  const $ = (q, c = document) => c.querySelector(q);

  // Prod mounts the app under /crm (Passenger). Settings pages live under /crm/web/settings/...
  // If we call /productos instead of /crm/productos, everything 404s.
  const API_BASE = (() => {
    try {
      const h = String(location.hostname || "").toLowerCase();
      const isLocal = h === "localhost" || h === "127.0.0.1";
      return isLocal ? "" : "/crm";
    } catch (_) {
      return "";
    }
  })();
  const apiURL = (p) => {
    const s = String(p || "");
    if (!s) return s;
    if (s.startsWith("http://") || s.startsWith("https://")) return s;
    if (!API_BASE) return s.startsWith("/crm/") ? s.slice(4) : s;
    if (s.startsWith("/crm/")) return s;
    return s.startsWith("/") ? (API_BASE + s) : (API_BASE + "/" + s);
  };

  // --- quick style (solo si falta)
  if (!document.getElementById("gd_settings_style")) {
    const st = document.createElement("style");
    st.id = "gd_settings_style";
    st.textContent = `
      body{ padding:10px; }
      .toolbar{ display:flex; gap:10px; align-items:center; padding:12px; margin:10px 12px; border-radius:16px;
        border:1px solid rgba(148,163,184,.14); background: rgba(2,6,23,.25); backdrop-filter: blur(10px); }
      .title{ margin: 12px; font-size: 18px; letter-spacing:.06em; }
      .pager{ display:flex; gap:10px; align-items:center; justify-content:center; padding:12px 0; }
    `;
    document.head.appendChild(st);
  }

  const els = {
    title: $("#title"),
    tbody: $("#tbody"),
    q: $("#q"),
    per: $("#per"),
    btnNew: $("#btnNew"),
    prev: $("#prev"),
    next: $("#next"),
    info: $("#info"),
  };

  if (els.title) els.title.textContent = cfg.title || "CRUD";

  const state = {
    page: 1,
    per: Number(els.per?.value || 20),
    q: "",
    total: 0,
    items: [],
    loading: false,
  };

  function headers() {
    const t = localStorage.getItem("token") || sessionStorage.getItem("token") || "";
    return t ? { Authorization: "Bearer " + t } : {};
  }

  function esc(s) {
    return String(s ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  async function apiList() {
    const url = apiURL(cfg.base); // la mayoría de tus endpoints devuelven array completo
    const res = await fetch(url, { headers: headers() });

    if (res.status === 401) {
      if (typeof Swal !== "undefined") {
        await Swal.fire({ icon: "warning", title: "Sesión expirada", text: "Vuelve a iniciar sesión." });
      }
      window.top.location.href = apiURL("/web/login.html");
      return;
    }

    if (!res.ok) throw new Error("HTTP " + res.status);

    const data = await res.json().catch(() => []);
    const all = Array.isArray(data) ? data : (data.items || data.rows || data.data || []);

    const filtered = !state.q
      ? all
      : all.filter((row) =>
          (cfg.searchKeys || []).some((k) => String(row[k] ?? "").toLowerCase().includes(state.q.toLowerCase()))
        );

    state.total = filtered.length;
    const start = (state.page - 1) * state.per;
    state.items = filtered.slice(start, start + state.per);
  }

  function render() {
    if (!els.tbody) return;
    els.tbody.innerHTML = "";

    if (!state.items.length) {
      els.tbody.innerHTML = `<tr><td colspan="99" style="padding:18px;color:var(--muted)">Sin resultados</td></tr>`;
      return;
    }

    for (const row of state.items) {
      const tr = document.createElement("tr");
      const idVal = row[cfg.id];

      const colsHtml = cfg.columns
        .map((col) => `<td>${esc(row[col.key])}</td>`)
        .join("");

      tr.innerHTML = `
        <td>${esc(idVal)}</td>
        ${colsHtml}
        <td style="white-space:nowrap">
          <button class="btn ghost" data-act="edit">Editar</button>
          <button class="btn ghost" data-act="del">Eliminar</button>
        </td>
      `;

      tr.querySelector('[data-act="edit"]')?.addEventListener("click", () => openForm("edit", row));
      tr.querySelector('[data-act="del"]')?.addEventListener("click", () => doDelete(idVal));
      els.tbody.appendChild(tr);
    }
  }

  function renderPager() {
    const pages = Math.max(1, Math.ceil(state.total / state.per));
    if (els.info) els.info.textContent = `Página ${state.page} de ${pages} · Total ${state.total}`;
    if (els.prev) els.prev.disabled = state.page <= 1;
    if (els.next) els.next.disabled = state.page >= pages;
  }

  async function reload() {
    if (state.loading) return;
    state.loading = true;
    try {
      await apiList();
      render();
      renderPager();
    } catch (e) {
      console.error(e);
      if (typeof Swal !== "undefined") Swal.fire({ icon: "error", title: "Error", text: String(e.message || e) });
    } finally {
      state.loading = false;
    }
  }

  function buildFormHtml(data = {}) {
    return `
      <div style="text-align:left">
        ${cfg.columns
          .map((col) => {
            const v = data[col.key] ?? "";
            const req = col.required ? "required" : "";
            return `
              <label style="display:block;margin:10px 0 6px;font-weight:800">${esc(col.label)}</label>
              <input id="f_${esc(col.key)}" class="swal2-input" value="${esc(v)}" ${req} style="width:100%"/>
            `;
          })
          .join("")}
      </div>
    `;
  }

  function readFormData() {
    const out = {};
    for (const col of cfg.columns) {
      const el = document.getElementById(`f_${col.key}`);
      out[col.key] = el ? el.value.trim() : null;
      if (col.required && !out[col.key]) throw new Error(`Falta: ${col.label}`);
    }
    return out;
  }

  async function doCreate(payload) {
    const res = await fetch(apiURL(cfg.base), {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify(payload),
    });
    if (res.status === 401) throw new Error("401 No autorizado");
    if (!res.ok) throw new Error(await res.text().catch(() => "HTTP " + res.status));
  }

  async function doUpdate(idVal, payload) {
    const res = await fetch(`${apiURL(cfg.base)}/${encodeURIComponent(idVal)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify(payload),
    });
    if (res.status === 401) throw new Error("401 No autorizado");
    if (!res.ok) throw new Error(await res.text().catch(() => "HTTP " + res.status));
  }

  async function doDelete(idVal) {
    const ok = await Swal.fire({
      icon: "warning",
      title: "Eliminar?",
      text: `ID: ${idVal}`,
      showCancelButton: true,
      confirmButtonText: "Sí, eliminar",
    });
    if (!ok.isConfirmed) return;

    const res = await fetch(`${apiURL(cfg.base)}/${encodeURIComponent(idVal)}`, {
      method: "DELETE",
      headers: headers(),
    });
    if (res.status === 401) throw new Error("401 No autorizado");
    if (!res.ok) throw new Error(await res.text().catch(() => "HTTP " + res.status));
    await reload();
  }

  async function openForm(mode, data = {}) {
    const isEdit = mode === "edit";
    const title = isEdit ? `Editar ${cfg.title}` : `Crear ${cfg.title}`;

    const r = await Swal.fire({
      title,
      html: buildFormHtml(data),
      focusConfirm: false,
      showCancelButton: true,
      confirmButtonText: isEdit ? "Guardar" : "Crear",
      preConfirm: () => {
        try { return readFormData(); }
        catch (e) { Swal.showValidationMessage(String(e.message || e)); return false; }
      },
    });

    if (!r.isConfirmed) return;

    try {
      if (isEdit) await doUpdate(data[cfg.id], r.value);
      else await doCreate(r.value);
      await reload();
      Swal.fire({ icon: "success", title: "OK", timer: 900, showConfirmButton: false });
    } catch (e) {
      Swal.fire({ icon: "error", title: "Error", text: String(e.message || e) });
    }
  }

  // wire
  els.btnNew?.addEventListener("click", () => openForm("new", {}));
  els.q?.addEventListener("input", () => { state.q = els.q.value.trim(); state.page = 1; reload(); });
  els.per?.addEventListener("change", () => { state.per = Number(els.per.value || 20); state.page = 1; reload(); });
  els.prev?.addEventListener("click", () => { state.page = Math.max(1, state.page - 1); reload(); });
  els.next?.addEventListener("click", () => {
    const pages = Math.max(1, Math.ceil(state.total / state.per));
    state.page = Math.min(pages, state.page + 1);
    reload();
  });

  reload();
}
