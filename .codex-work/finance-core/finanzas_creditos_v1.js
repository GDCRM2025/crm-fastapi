(() => {
  "use strict";

  const API_BASE = String(location.pathname || "").startsWith("/crm/") ? "/crm" : "";
  const money = value => new Intl.NumberFormat("es-CL", {
    style: "currency", currency: "CLP", maximumFractionDigits: 0,
  }).format(Number(value || 0));
  const esc = value => String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;");

  function headers(extra = {}) {
    try {
      if (window.parent?.GD?.authHeaders) return window.parent.GD.authHeaders(extra);
    } catch (_) {}
    const token = localStorage.getItem("token") || localStorage.getItem("gd_token") || "";
    return token ? { ...extra, Authorization: `Bearer ${token}` } : extra;
  }

  async function api(path, options = {}) {
    const response = await fetch(API_BASE + "/finanzas/simple" + path, {
      ...options,
      headers: headers({ Accept: "application/json", ...(options.headers || {}) }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = data?.detail;
      throw new Error(typeof detail === "string" ? detail : detail?.message || `HTTP ${response.status}`);
    }
    return data;
  }

  function install() {
    const tabs = document.querySelector(".tabs");
    const host = document.querySelector(".wrap");
    if (!tabs || !host || document.querySelector("#loansV1")) return;

    const tab = document.createElement("button");
    tab.type = "button";
    tab.className = "tab";
    tab.id = "tabLoansV1";
    tab.textContent = "Créditos / Simulador";
    tabs.appendChild(tab);

    const section = document.createElement("section");
    section.id = "loansV1";
    section.className = "card hidden";
    section.innerHTML = `
      <style>
        #loansV1 .loan-kpis{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:10px;margin:14px 0}
        #loansV1 .loan-kpi{border:1px solid rgba(255,255,255,.12);border-radius:14px;padding:13px;background:rgba(255,255,255,.035)}
        #loansV1 .loan-kpi span{display:block;font-size:11px;opacity:.7;text-transform:uppercase}#loansV1 .loan-kpi strong{display:block;font-size:20px;margin-top:5px}
        #loansV1 .loan-grid{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(340px,.8fr);gap:16px}
        #loansV1 .loan-form{display:grid;grid-template-columns:1fr 1fr;gap:10px}#loansV1 .loan-form label{display:flex;flex-direction:column;gap:5px}
        #loansV1 .loan-form input{width:100%}#loansV1 .loan-result{margin-top:12px;padding:12px;border:1px solid rgba(255,255,255,.12);border-radius:12px}
        @media(max-width:900px){#loansV1 .loan-grid{grid-template-columns:1fr}#loansV1 .loan-kpis{grid-template-columns:repeat(2,1fr)}}
      </style>
      <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap">
        <div><h2 style="margin:0">CRÉDITOS · GREEN DIAMOND</h2><div class="muted">Cuotas, deuda vigente y simulación de nuevos créditos.</div></div>
        <button type="button" class="btn secondary" id="loanReload">Actualizar</button>
      </div>
      <div class="loan-kpis">
        <div class="loan-kpi"><span>Cuotas mensuales</span><strong id="loanMonthly">—</strong></div>
        <div class="loan-kpi"><span>Monto original</span><strong id="loanPrincipal">—</strong></div>
        <div class="loan-kpi"><span>Pagado</span><strong id="loanPaid">—</strong></div>
        <div class="loan-kpi"><span>Saldo</span><strong id="loanBalance">—</strong></div>
      </div>
      <div class="loan-grid">
        <div>
          <div class="table-wrap"><table><thead><tr><th>Crédito</th><th class="money">Cuota</th><th>Cuotas</th><th class="money">Monto</th><th class="money">Pagado</th><th class="money">Saldo</th><th>Sociedad</th></tr></thead><tbody id="loanRows"><tr><td colspan="7" class="muted">Cargando...</td></tr></tbody></table></div>
          <div class="notice muted">La cuota completa afecta flujo de caja. En P&L sólo corresponden intereses, seguros y comisiones; el capital reduce el pasivo.</div>
        </div>
        <div>
          <h3 style="margin-top:0">SIMULADOR DE CRÉDITO</h3>
          <div class="loan-form">
            <label>Capital<input id="simPrincipal" type="number" min="1" value="50000000"></label>
            <label>Tasa anual %<input id="simRate" type="number" min="0" step="0.01" value="12"></label>
            <label>Número de cuotas<input id="simInstallments" type="number" min="1" max="600" value="36"></label>
            <label>Seguro mensual<input id="simInsurance" type="number" min="0" value="0"></label>
            <label>Comisión mensual<input id="simFees" type="number" min="0" value="0"></label>
          </div>
          <button type="button" class="btn primary" id="simulateLoan" style="margin-top:12px">Calcular crédito</button>
          <div class="loan-result muted" id="loanSimulation">Ingresa los parámetros y presiona calcular.</div>
        </div>
      </div>`;
    host.appendChild(section);

    function open() {
      host.querySelectorAll("section.card").forEach(item => item.classList.add("hidden"));
      tabs.querySelectorAll(".tab").forEach(item => item.classList.remove("active"));
      section.classList.remove("hidden");
      tab.classList.add("active");
      location.hash = "creditos";
      load();
    }
    tab.onclick = open;
    tabs.addEventListener("click", event => {
      const clicked = event.target.closest(".tab");
      if (clicked && clicked !== tab) section.classList.add("hidden");
    });

    async function load() {
      const data = await api("/loans-v1");
      section.querySelector("#loanMonthly").textContent = money(data.monthly_installments);
      section.querySelector("#loanPrincipal").textContent = money(data.principal_total);
      section.querySelector("#loanPaid").textContent = money(data.paid_total);
      section.querySelector("#loanBalance").textContent = money(data.balance_total);
      section.querySelector("#loanRows").innerHTML = (data.items || []).map(item => `
        <tr><td><strong>${esc(item.lender)}</strong></td><td class="money">${money(item.installment_amount)}</td>
        <td>${Number(item.paid_installments)}/${Number(item.total_installments)}</td><td class="money">${money(item.principal)}</td>
        <td class="money">${money(item.paid_amount)}</td><td class="money"><strong>${money(item.balance)}</strong></td><td>${esc(item.legal_name)}</td></tr>`).join("") || '<tr><td colspan="7">Sin créditos.</td></tr>';
    }

    section.querySelector("#loanReload").onclick = () => load().catch(error => alert(error.message));
    section.querySelector("#simulateLoan").onclick = async () => {
      const output = section.querySelector("#loanSimulation");
      output.textContent = "Calculando...";
      try {
        const data = await api("/loans-v1/simulate", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            principal: Number(section.querySelector("#simPrincipal").value || 0),
            annual_rate: Number(section.querySelector("#simRate").value || 0),
            installments: Number(section.querySelector("#simInstallments").value || 0),
            monthly_insurance: Number(section.querySelector("#simInsurance").value || 0),
            monthly_fees: Number(section.querySelector("#simFees").value || 0),
          }),
        });
        output.innerHTML = `<strong>Cuota estimada: ${money(data.monthly_total)}</strong><br>Intereses totales: ${money(data.total_interest)}<br>Costo total: ${money(data.total_cost)}<br><small>${esc(data.assumption)}</small>`;
      } catch (error) { output.textContent = error.message; }
    };

    if ((location.hash || "").toLowerCase().includes("creditos")) open();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", install);
  else install();
})();
