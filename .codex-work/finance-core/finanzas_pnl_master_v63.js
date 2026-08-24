(function(){

  "use strict";

  const UNITS = [
    ["CAMALEON", "CAMALEÓN"],
    ["GOURMET", "GOURMET"],
    ["EXPRESS", "EXPRESS"],
    ["DEL SABOR", "DEL SABOR"],
    ["BRONTOS", "BRONTOS"],
    ["PETRAS", "PETRAS"],
    ["MAS FLOW", "MAS FLOW"],
    ["CORPORATIVO", "ADMINISTRACIÓN CENTRAL"],
    ["HOLDING", "HOLDING / GASTOS COMUNES"],
  ];

  const MONTHS = [
    "",
    "ENE",
    "FEB",
    "MAR",
    "ABR",
    "MAY",
    "JUN",
    "JUL",
    "AGO",
    "SEP",
    "OCT",
    "NOV",
    "DIC",
  ];

  let fullYear = false;
  let lastData = null;


  function money(value){

    const number =
      Number(value || 0);

    return new Intl.NumberFormat(
      "es-CL",
      {
        style:"currency",
        currency:"CLP",
        maximumFractionDigits:0,
      }
    ).format(number);
  }


  function esc(value){

    return String(
      value ?? ""
    )
    .replaceAll("&","&amp;")
    .replaceAll("<","&lt;")
    .replaceAll(">","&gt;")
    .replaceAll('"',"&quot;");
  }


  async function request(url){

    if(
      typeof window.api
      === "function"
    ){
      return await window.api(url);
    }

    let headers = {};

    if(
      typeof window.authHeaders
      === "function"
    ){
      headers =
        window.authHeaders();
    }else if(
      typeof authHeaders
      === "function"
    ){
      headers =
        authHeaders();
    }

    const response =
      await fetch(
        url,
        {
          headers,
          credentials:"same-origin",
        }
      );

    if(!response.ok){

      let text = "";

      try{
        text = await response.text();
      }catch(_){}

      throw new Error(
        text
        || (
          "HTTP "
          + response.status
        )
      );
    }

    return await response.json();
  }


  function selectedUnit(){

    return String(
      document.querySelector(
        "#pnlMasterUnit"
      )?.value
      || "CAMALEON"
    ).trim().toUpperCase();
  }


  function selectedYear(){

    return Number(
      document.querySelector(
        "#pnlMasterYear"
      )?.value
      || new Date().getFullYear()
    );
  }


  function selectedMonth(){

    return Number(
      document.querySelector(
        "#pnlMasterMonth"
      )?.value
      || (
        new Date().getMonth()
        + 1
      )
    );
  }


  function unitLabel(code){

    return (
      UNITS.find(
        item => item[0] === code
      )?.[1]
      || code
    );
  }


  function rowValue(
    data,
    key,
    month
  ){

    const row =
      (data.rows || [])
      .find(
        item =>
          String(item.key)
          === String(key)
      );

    if(!row){
      return 0;
    }

    return Number(
      row.values?.[month]
      ?? row.values?.[
           String(month)
         ]
      ?? 0
    );
  }


  function groupTotal(
    data,
    group,
    month
  ){

    return (
      (data.rows || [])
      .filter(
        row =>
          row.kind === "expense"
          && row.group === group
      )
      .reduce(
        (sum,row) =>
          sum
          +
          Number(
            row.values?.[month]
            ?? row.values?.[
                 String(month)
               ]
            ?? 0
          ),
        0
      )
    );
  }


  function accountRows(data){

    return (
      (data.rows || [])
      .filter(
        row =>
          row.kind === "expense"
      )
    );
  }


  function renderMonthly(data){

    const month =
      selectedMonth();

    const ingresos =
      rowValue(
        data,
        "INGRESOS",
        month
      );

    const gastos =
      rowValue(
        data,
        "TOTAL_GASTOS",
        month
      );

    const resultado =
      rowValue(
        data,
        "RESULTADO",
        month
      );

    const fixed =
      groupTotal(
        data,
        "GASTOS FIJOS",
        month
      );

    const groups = [];

    for(
      const row
      of accountRows(data)
    ){
      if(
        !groups.includes(
          row.group
        )
      ){
        groups.push(
          row.group
        );
      }
    }

    let body = `
      <tr class="gd63-revenue">
        <td>
          <strong>
            VENTAS / INGRESOS
          </strong>
        </td>
        <td class="num">
          <strong>
            ${money(ingresos)}
          </strong>
        </td>
        <td class="num">
          100,0%
        </td>
      </tr>
    `;

    groups.forEach(
      group => {

        const items =
          accountRows(data)
          .filter(
            row =>
              row.group === group
          );

        const total =
          items.reduce(
            (sum,row) =>
              sum
              +
              Number(
                row.values?.[month]
                ?? row.values?.[
                     String(month)
                   ]
                ?? 0
              ),
            0
          );

        body += `
          <tr class="gd63-group">
            <td>
              ${esc(group)}
            </td>
            <td class="num">
              ${money(total)}
            </td>
            <td class="num">
              ${
                ingresos
                ? (
                    (
                      total
                      / ingresos
                    )
                    * 100
                  ).toFixed(1)
                : "0.0"
              }%
            </td>
          </tr>
        `;

        items.forEach(
          row => {

            const value =
              Number(
                row.values?.[month]
                ?? row.values?.[
                     String(month)
                   ]
                ?? 0
              );

            body += `
              <tr>
                <td class="gd63-account">
                  <span>
                    ${esc(row.key)}
                  </span>
                  ${esc(row.label)}
                </td>

                <td class="num">
                  ${money(value)}
                </td>

                <td class="num">
                  ${
                    ingresos
                    ? (
                        (
                          value
                          / ingresos
                        )
                        * 100
                      ).toFixed(1)
                    : "0.0"
                  }%
                </td>
              </tr>
            `;
          }
        );

      }
    );

    body += `
      <tr class="gd63-total">
        <td>TOTAL GASTOS</td>
        <td class="num">
          ${money(gastos)}
        </td>
        <td class="num">
          ${
            ingresos
            ? (
                (
                  gastos
                  / ingresos
                )
                * 100
              ).toFixed(1)
            : "0.0"
          }%
        </td>
      </tr>

      <tr class="gd63-result">
        <td>RESULTADO OPERACIONAL</td>
        <td class="num">
          ${money(resultado)}
        </td>
        <td class="num">
          ${
            ingresos
            ? (
                (
                  resultado
                  / ingresos
                )
                * 100
              ).toFixed(1)
            : "0.0"
          }%
        </td>
      </tr>
    `;

    document.querySelector(
      "#pnlMasterContent"
    ).innerHTML = `
      <div class="gd63-kpis">

        <div class="gd63-kpi">
          <span>Ventas / ingresos</span>
          <strong>
            ${money(ingresos)}
          </strong>
        </div>

        <div class="gd63-kpi">
          <span>Gastos fijos</span>
          <strong>
            ${money(fixed)}
          </strong>
        </div>

        <div class="gd63-kpi">
          <span>Total gastos</span>
          <strong>
            ${money(gastos)}
          </strong>
        </div>

        <div class="gd63-kpi">
          <span>Resultado operacional</span>
          <strong>
            ${money(resultado)}
          </strong>
        </div>

      </div>

      <div class="gd63-table-wrap">
        <table class="gd63-table">
          <thead>
            <tr>
              <th>Cuenta</th>
              <th class="num">
                ${MONTHS[month]}
              </th>
              <th class="num">
                % venta
              </th>
            </tr>
          </thead>

          <tbody>
            ${body}
          </tbody>
        </table>
      </div>
    `;
  }


  function renderYear(data){

    const rows =
      data.rows || [];

    let body = "";

    rows.forEach(
      row => {

        if(
          row.key === "RESIDUAL"
          && Number(row.total || 0) === 0
        ){
          return;
        }

        const cls =
          row.key === "RESULTADO"
          ? "gd63-result"
          : (
              row.key === "TOTAL_GASTOS"
              ? "gd63-total"
              : (
                  row.kind === "revenue"
                  ? "gd63-revenue"
                  : ""
                )
            );

        body += `
          <tr class="${cls}">
            <td>
              ${
                row.key
                ? (
                    `<span class="gd63-code">`
                    + esc(row.key)
                    + `</span> `
                  )
                : ""
              }
              ${esc(row.label)}
            </td>

            ${
              Array.from(
                {length:12},
                (_,index) => {

                  const month =
                    index + 1;

                  const value =
                    Number(
                      row.values?.[month]
                      ?? row.values?.[
                           String(month)
                         ]
                      ?? 0
                    );

                  return `
                    <td class="num">
                      ${money(value)}
                    </td>
                  `;
                }
              ).join("")
            }

            <td class="num">
              <strong>
                ${money(row.total)}
              </strong>
            </td>
          </tr>
        `;
      }
    );

    document.querySelector(
      "#pnlMasterContent"
    ).innerHTML = `
      <div class="gd63-table-wrap">
        <table class="gd63-table gd63-year">
          <thead>
            <tr>
              <th>Cuenta</th>

              ${
                MONTHS
                .slice(1)
                .map(
                  month =>
                    `<th class="num">${month}</th>`
                )
                .join("")
              }

              <th class="num">
                TOTAL
              </th>
            </tr>
          </thead>

          <tbody>
            ${body}
          </tbody>
        </table>
      </div>
    `;
  }


  function render(data){

    lastData = data;

    const unit =
      selectedUnit();

    const month =
      selectedMonth();

    const title =
      document.querySelector(
        "#pnlMasterTitle"
      );

    if(title){
      title.textContent =
        "P&L "
        + unitLabel(unit);
    }

    const subtitle =
      document.querySelector(
        "#pnlMasterSubtitle"
      );

    if(subtitle){
      subtitle.textContent =
        fullYear
        ? (
            "Año "
            + selectedYear()
            + " · "
            + "vista individual"
          )
        : (
            MONTHS[month]
            + " "
            + selectedYear()
            + " · "
            + "sólo esta unidad de gestión"
          );
    }

    if(fullYear){
      renderYear(data);
    }else{
      renderMonthly(data);
    }
  }


  async function load(){

    const unit =
      selectedUnit();

    const year =
      selectedYear();

    const content =
      document.querySelector(
        "#pnlMasterContent"
      );

    if(content){
      content.innerHTML =
        `<div class="gd63-loading">Cargando P&L...</div>`;
    }

    const hiddenOriginal =
      document.querySelector(
        "#pnlBrand"
      );

    if(hiddenOriginal){
      hiddenOriginal.value =
        unit;
    }

    localStorage.setItem(
      "gd_pnl_selected",
      unit
    );

    try{

      const data =
        await request(
          "/finanzas/simple/pnl"
          + "?year="
          + encodeURIComponent(year)
          + "&brand="
          + encodeURIComponent(unit)
        );

      render(data);

    }catch(error){

      console.error(
        "P&L V6.3",
        error
      );

      if(content){
        content.innerHTML = `
          <div class="gd63-error">
            No se pudo cargar el P&L
            ${esc(unit)}.
            <br>
            ${esc(error.message || error)}
          </div>
        `;
      }
    }
  }


  function exportPnl(){

    const unit =
      selectedUnit();

    const year =
      selectedYear();

    let url =
      "/finanzas/simple/pnl/export"
      + "?year="
      + encodeURIComponent(year)
      + "&brand="
      + encodeURIComponent(unit);

    window.open(
      url,
      "_blank"
    );
  }


  function openExpenseClassifier(){

    const button =
      document.querySelector(
        "#pnlV6Shortcut"
      );

    if(button){
      button.click();
    }
  }


  function openIncomeClassifier(){

    const button =
      document.querySelector(
        "#pnlV6IncomeShortcut"
      );

    if(button){
      button.click();
    }
  }


  function install(){

    const section =
      document.querySelector(
        "#pnl"
      );

    if(!section){
      return false;
    }

    if(
      document.querySelector(
        "#pnlMasterV63"
      )
    ){
      return true;
    }

    const original =
      document.querySelector(
        "#pnlBrand"
      );

    let initialUnit =
      String(
        original?.value
        || localStorage.getItem(
             "gd_pnl_selected"
           )
        || "CAMALEON"
      ).toUpperCase();

    if(
      !UNITS.some(
        item =>
          item[0] === initialUnit
      )
    ){
      initialUnit =
        "CAMALEON";
    }

    const now =
      new Date();

    const year =
      Number(
        document.querySelector(
          "#pnlYear"
        )?.value
        || now.getFullYear()
      );

    const month =
      now.getMonth() + 1;

    const root =
      document.createElement(
        "div"
      );

    root.id =
      "pnlMasterV63";

    root.innerHTML = `
      <style>

        #pnl.gd-pnl-master-v63
        > :not(#pnlMasterV63){
          display:none !important;
        }

        #pnlMasterV63{
          display:block !important;
          width:100%;
        }

        .gd63-head{
          display:flex;
          justify-content:space-between;
          gap:20px;
          align-items:flex-start;
          flex-wrap:wrap;
          margin-bottom:18px;
        }

        .gd63-head h2{
          margin:0;
          font-size:30px;
        }

        .gd63-subtitle{
          opacity:.7;
          margin-top:3px;
        }

        .gd63-controls{
          display:flex;
          align-items:center;
          gap:10px;
          flex-wrap:wrap;
        }

        .gd63-controls select,
        .gd63-controls input{
          min-height:42px;
          border-radius:12px;
          padding:8px 12px;
        }

        .gd63-actions{
          display:flex;
          gap:10px;
          flex-wrap:wrap;
          margin-bottom:18px;
        }

        .gd63-btn{
          min-height:42px;
          border-radius:18px;
          padding:8px 16px;
          cursor:pointer;
          font-weight:700;
        }

        .gd63-kpis{
          display:grid;
          grid-template-columns:
            repeat(
              4,
              minmax(180px,1fr)
            );
          gap:12px;
          margin:18px 0;
        }

        .gd63-kpi{
          padding:16px;
          border:1px solid
            rgba(110,170,220,.25);
          border-radius:14px;
          background:
            rgba(255,255,255,.025);
        }

        .gd63-kpi span{
          display:block;
          opacity:.7;
          margin-bottom:8px;
        }

        .gd63-kpi strong{
          font-size:22px;
        }

        .gd63-table-wrap{
          width:100%;
          overflow:auto;
        }

        .gd63-table{
          width:100%;
          border-collapse:collapse;
        }

        .gd63-table th,
        .gd63-table td{
          padding:11px 13px;
          border-bottom:
            1px solid
            rgba(130,170,210,.14);
        }

        .gd63-table th{
          text-align:left;
          opacity:.8;
        }

        .gd63-table .num{
          text-align:right;
          white-space:nowrap;
        }

        .gd63-group td{
          font-weight:800;
          background:
            rgba(120,170,210,.09);
        }

        .gd63-account{
          padding-left:28px !important;
        }

        .gd63-account span,
        .gd63-code{
          display:inline-block;
          min-width:48px;
          opacity:.55;
          font-size:12px;
        }

        .gd63-revenue td{
          font-weight:800;
        }

        .gd63-total td{
          font-weight:800;
          border-top:
            2px solid
            rgba(150,190,230,.25);
        }

        .gd63-result td{
          font-weight:900;
          font-size:17px;
          border-top:
            2px solid
            rgba(150,190,230,.35);
        }

        .gd63-loading,
        .gd63-error{
          padding:30px;
          text-align:center;
          opacity:.8;
        }

        @media(max-width:900px){
          .gd63-kpis{
            grid-template-columns:
              repeat(
                2,
                minmax(150px,1fr)
              );
          }
        }

      </style>

      <div class="gd63-head">

        <div>
          <h2 id="pnlMasterTitle">
            P&L
          </h2>

          <div
            class="gd63-subtitle"
            id="pnlMasterSubtitle">
          </div>
        </div>

        <div class="gd63-controls">

          <label>
            P&L
            <select id="pnlMasterUnit">
              ${
                UNITS.map(
                  ([code,label]) => `
                    <option
                      value="${code}"
                      ${
                        code === initialUnit
                        ? "selected"
                        : ""
                      }>
                      ${label}
                    </option>
                  `
                ).join("")
              }
            </select>
          </label>

          <label>
            Mes
            <select id="pnlMasterMonth">
              ${
                MONTHS
                .slice(1)
                .map(
                  (label,index) => `
                    <option
                      value="${index + 1}"
                      ${
                        index + 1 === month
                        ? "selected"
                        : ""
                      }>
                      ${label}
                    </option>
                  `
                ).join("")
              }
            </select>
          </label>

          <label>
            Año
            <input
              id="pnlMasterYear"
              type="number"
              min="2020"
              max="2100"
              value="${year}"
              style="width:100px">
          </label>

          <button
            type="button"
            class="btn gd63-btn"
            id="pnlMasterReload">
            Actualizar
          </button>

        </div>
      </div>

      <div class="gd63-actions">

        <button
          type="button"
          class="btn secondary gd63-btn"
          id="pnlMasterToggleYear">
          Ver año completo
        </button>

        <button
          type="button"
          class="btn secondary gd63-btn"
          id="pnlMasterExport">
          Descargar XLSX
        </button>

        <button
          type="button"
          class="btn secondary gd63-btn"
          id="pnlMasterExpenses">
          Clasificar gastos
        </button>

        <button
          type="button"
          class="btn secondary gd63-btn"
          id="pnlMasterIncome">
          Clasificar ingresos
        </button>

      </div>

      <div id="pnlMasterContent">
      </div>
    `;

    section.insertBefore(
      root,
      section.firstChild
    );

    section.classList.add(
      "gd-pnl-master-v63"
    );

    document.querySelector(
      "#pnlMasterUnit"
    ).onchange =
      load;

    document.querySelector(
      "#pnlMasterMonth"
    ).onchange =
      () => {
        if(lastData){
          render(lastData);
        }else{
          load();
        }
      };

    document.querySelector(
      "#pnlMasterYear"
    ).onchange =
      load;

    document.querySelector(
      "#pnlMasterReload"
    ).onclick =
      load;

    document.querySelector(
      "#pnlMasterExport"
    ).onclick =
      exportPnl;

    document.querySelector(
      "#pnlMasterExpenses"
    ).onclick =
      openExpenseClassifier;

    document.querySelector(
      "#pnlMasterIncome"
    ).onclick =
      openIncomeClassifier;

    document.querySelector(
      "#pnlMasterToggleYear"
    ).onclick =
      () => {

        fullYear =
          !fullYear;

        document.querySelector(
          "#pnlMasterToggleYear"
        ).textContent =
          fullYear
          ? "Ver mes"
          : "Ver año completo";

        if(lastData){
          render(lastData);
        }
      };

    load();

    return true;
  }


  function boot(){

    if(install()){
      return;
    }

    let attempts = 0;

    const timer =
      setInterval(
        () => {

          attempts += 1;

          if(
            install()
            || attempts >= 30
          ){
            clearInterval(timer);
          }

        },
        250
      );
  }


  if(
    document.readyState
    === "loading"
  ){
    document.addEventListener(
      "DOMContentLoaded",
      boot
    );
  }else{
    boot();
  }

})();
