(() => {
  "use strict";
  const $ = (q, c = document) => c.querySelector(q);

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

  function authHeaders(extra = {}) {
    const GD = window.parent && window.parent.GD;
    return GD && GD.authHeaders ? GD.authHeaders(extra) : extra;
  }

  function getTab() {
    const u = new URL(location.href);
    return (u.searchParams.get("tab") || "comunas").toLowerCase();
  }

  const TABS = [
    { id:"usuarios", label:"Usuarios", hint:"(UI lista) falta endpoint CRUD en backend" },
    { id:"comunas", label:"Comunas", hint:"Cargado desde /leads/catalogos" },
    { id:"estados", label:"Estados", hint:"Cargado desde /leads/catalogos" },
    { id:"tipocliente", label:"Tipos cliente", hint:"Cargado desde /leads/catalogos" },
    { id:"marcas", label:"Marcas", hint:"Cargado desde /leads/catalogos" },
    { id:"segmentacion", label:"Segmentación", hint:"(UI lista) falta endpoint CRUD en backend" },
    { id:"productos", label:"Productos", hint:"(UI lista) falta endpoint CRUD en backend" },
    { id:"categorias", label:"Categorías", hint:"(UI lista) falta endpoint CRUD en backend" },
  ];

  function setHeader(tab) {
    const t = TABS.find(x => x.id === tab) || TABS[0];
    $("#h1").textContent = t.label;
    $("#sub").textContent = t.hint;
  }

  function renderTabs(active) {
    const el = $("#tabs");
    el.innerHTML = "";
    for (const t of TABS) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "s-tab" + (t.id === active ? " active" : "");
      b.textContent = t.label;
      b.addEventListener("click", () => {
        const u = new URL(location.href);
        u.searchParams.set("tab", t.id);
        location.href = u.toString();
      });
      el.appendChild(b);
    }
  }

  function showState(msg) {
    const box = $("#stateBox");
    box.style.display = "";
    box.textContent = msg;
  }

  function escapeHtml(str) {
    return String(str || "")
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
  }

  async function getCatalogos() {
    const res = await fetch(apiURL("/leads/catalogos"), { headers: authHeaders() });
    if (!res.ok) throw new Error(`HTTP ${res.status} /leads/catalogos`);
    const j = await res.json();
    return j.catalogos || j || {};
  }

  function pickList(tab, c) {
    if (tab === "comunas") return c.comunas || [];
    if (tab === "marcas") return c.marcas || [];
    if (tab === "estados") return c.estados || [];
    if (tab === "tipocliente") return c.tipocliente || c.tipos_cliente || [];
    return null;
  }

  function renderTable(tab, items, q="") {
    const thead = $("#thead");
    const tbody = $("#tbody");

    const norm = (s) =>
      String(s || "").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").trim();

    const filtered = (items || []).filter((x) => {
      if (!q) return true;
      const blob = norm(JSON.stringify(x));
      return blob.includes(norm(q));
    });

    const cols =
      tab === "estados" ? [{k:"id_estado",label:"ID"},{k:"nombre",label:"Nombre"}] :
      tab === "comunas" ? [{k:"id_comuna",label:"ID"},{k:"nombre",label:"Nombre"}] :
      tab === "marcas" ? [{k:"id_marca",label:"ID"},{k:"nombre",label:"Nombre"}] :
      tab === "tipocliente" ? [{k:"id_tipo_cliente",label:"ID"},{k:"nombre",label:"Nombre"}] :
      [{k:"id",label:"ID"},{k:"nombre",label:"Nombre"}];

    thead.innerHTML = cols.map(c => `<th>${escapeHtml(c.label)}</th>`).join("") + `<th style="text-align:right">Acciones</th>`;

    tbody.innerHTML = filtered.map((row) => {
      const tds = cols.map((c) => `<td>${escapeHtml(row[c.k] ?? "")}</td>`).join("");
      return `
        <tr class="s-row">
          ${tds}
          <td>
            <div class="s-actionsCell">
              <button class="s-mini" data-act="edit">Editar</button>
              <button class="s-mini danger" data-act="del">Eliminar</button>
            </div>
          </td>
        </tr>
      `;
    }).join("");

    tbody.querySelectorAll("button[data-act='edit']").forEach((b) => {
      b.addEventListener("click", () => Swal.fire({ icon:"info", title:"Edición", text:"UI lista. Falta endpoint CRUD en backend." }));
    });
    tbody.querySelectorAll("button[data-act='del']").forEach((b) => {
      b.addEventListener("click", () => Swal.fire({ icon:"warning", title:"Eliminar", text:"UI lista. Falta endpoint CRUD en backend." }));
    });
  }

  async function reload() {
    const tab = getTab();
    const q = $("#q").value || "";
    $("#thead").innerHTML = "";
    $("#tbody").innerHTML = "";

    if (["comunas","marcas","estados","tipocliente"].includes(tab)) {
      try {
        showState("Cargando desde /leads/catalogos…");
        const c = await getCatalogos();
        const items = pickList(tab, c) || [];
        showState(`OK: ${items.length} items. (CRUD pendiente)`);
        renderTable(tab, items, q);
      } catch (e) {
        console.error(e);
        showState("❌ Error cargando. ¿Backend corriendo en 127.0.0.1:8000?");
      }
      return;
    }

    showState("Este módulo está listo en UI, pero falta endpoint backend (CRUD).");
  }

  document.addEventListener("DOMContentLoaded", async () => {
    const tab = getTab();
    setHeader(tab);
    renderTabs(tab);

    $("#btnReload").addEventListener("click", reload);
    $("#btnNew").addEventListener("click", () => Swal.fire({ icon:"info", title:"Nuevo", text:"UI lista. Falta endpoint CRUD en backend." }));
    $("#q").addEventListener("input", reload);

    await reload();
  });
})();
