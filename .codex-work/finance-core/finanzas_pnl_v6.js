(() => {
  "use strict";

  const MARKER = "PNL_V6_20260821";
  let META = null;
  let ITEMS = [];

  function basePath(){
    return location.pathname
      .startsWith("/crm/")
      ? "/crm"
      : "";
  }

  function token(){
    return (
      localStorage.getItem("token")
      ||
      sessionStorage.getItem("token")
      ||
      localStorage.getItem(
        "access_token"
      )
      ||
      sessionStorage.getItem(
        "access_token"
      )
      ||
      ""
    );
  }

  function errorText(value){
    if(value === null ||
       value === undefined){
      return "Error desconocido.";
    }

    if(typeof value === "string"){
      return value;
    }

    if(Array.isArray(value)){
      return value
        .map(item => {
          if(typeof item === "string"){
            return item;
          }

          return (
            item?.msg
            ||
            item?.message
            ||
            item?.detail
            ||
            JSON.stringify(item)
          );
        })
        .join(" · ");
    }

    if(typeof value === "object"){
      return (
        value.message
        ||
        value.msg
        ||
        value.detail
        ||
        JSON.stringify(value)
      );
    }

    return String(value);
  }

  async function api(
    url,
    options = {}
  ){
    const headers = {
      ...(options.headers || {})
    };

    const auth = token();

    if(auth){
      headers.Authorization =
        "Bearer " + auth;
    }

    if(
      options.body
      &&
      !(options.body instanceof FormData)
    ){
      headers["Content-Type"] =
        "application/json";
    }

    const response =
      await fetch(
        basePath() + url,
        {
          ...options,
          headers
        }
      );

    let data = {};

    try{
      data = await response.json();
    }catch(_){}

    if(!response.ok){
      throw new Error(
        errorText(
          data.detail
          ?? data
          ?? `HTTP ${response.status}`
        )
      );
    }

    return data;
  }

  function esc(value){
    return String(value ?? "")
      .replace(/&/g,"&amp;")
      .replace(/</g,"&lt;")
      .replace(/>/g,"&gt;")
      .replace(/"/g,"&quot;")
      .replace(/'/g,"&#039;");
  }

  const CLP =
    new Intl.NumberFormat(
      "es-CL",
      {
        style: "currency",
        currency: "CLP",
        maximumFractionDigits: 0
      }
    );

  function money(value){
    return CLP.format(
      Math.abs(
        Number(value || 0)
      )
    );
  }

  function closeModal(){
    document
      .querySelector(
        "#pnlV6Modal"
      )
      ?.remove();
  }

  function modal(
    title,
    html
  ){
    closeModal();

    const shell =
      document.createElement(
        "div"
      );

    shell.id =
      "pnlV6Modal";

    shell.innerHTML = `
      <div class="pnl-v6-modal-card">
        <div class="pnl-v6-modal-head">
          <strong>
            ${esc(title)}
          </strong>

          <button
            type="button"
            class="btn secondary"
            id="pnlV6Close">
            Cerrar
          </button>
        </div>

        ${html}
      </div>
    `;

    document.body
      .appendChild(shell);

    document.querySelector(
      "#pnlV6Close"
    ).onclick = closeModal;

    shell.onclick = event => {
      if(event.target === shell){
        closeModal();
      }
    };
  }

  function installStyle(){
    if(
      document.querySelector(
        "#pnlV6Style"
      )
    ){
      return;
    }

    const style =
      document.createElement(
        "style"
      );

    style.id =
      "pnlV6Style";

    style.textContent = `
      #pnlV6Panel{
        margin-top:16px;
        border:
          1px solid
          rgba(255,255,255,.14);
        border-radius:12px;
        padding:14px;
        background:
          rgba(255,255,255,.025);
      }

      .pnl-v6-toolbar{
        display:flex;
        gap:8px;
        flex-wrap:wrap;
        align-items:center;
        margin-bottom:12px;
      }

      .pnl-v6-toolbar input{
        min-width:300px;
        flex:1 1 330px;
      }

      .pnl-v6-table{
        width:100%;
        border-collapse:collapse;
      }

      .pnl-v6-table th,
      .pnl-v6-table td{
        padding:10px 9px;
        border-bottom:
          1px solid
          rgba(255,255,255,.09);
        text-align:left;
      }

      .pnl-v6-row{
        cursor:pointer;
      }

      .pnl-v6-row:hover{
        background:
          rgba(255,255,255,.055);
      }

      .pnl-v6-amount{
        font-weight:700;
        white-space:nowrap;
      }

      #pnlV6Modal{
        position:fixed;
        inset:0;
        z-index:99999;
        background:
          rgba(0,0,0,.72);
        display:flex;
        align-items:flex-start;
        justify-content:center;
        overflow:auto;
        padding:18px;
      }

      .pnl-v6-modal-card{
        width:min(1040px,96vw);
        margin-top:2vh;
        padding:18px;
        border-radius:12px;
        border:
          1px solid
          rgba(255,255,255,.15);
        background:#112d46;
        box-shadow:
          0 22px 70px
          rgba(0,0,0,.45);
      }

      .pnl-v6-modal-head{
        display:flex;
        justify-content:
          space-between;
        align-items:center;
        gap:12px;
        margin-bottom:15px;
        font-size:17px;
      }

      .pnl-v6-grid{
        display:grid;
        grid-template-columns:
          repeat(
            2,
            minmax(0,1fr)
          );
        gap:13px;
      }

      .pnl-v6-grid label{
        display:flex;
        flex-direction:column;
        gap:6px;
      }

      .pnl-v6-grid input,
      .pnl-v6-grid select,
      .pnl-v6-grid textarea{
        width:100%;
        box-sizing:border-box;
      }

      .pnl-v6-full{
        grid-column:1 / -1;
      }

      .pnl-v6-readonly{
        opacity:.82;
      }

      .pnl-v6-account-results{
        min-height:180px;
      }

      .pnl-v6-actions{
        display:flex;
        justify-content:
          space-between;
        gap:10px;
        margin-top:16px;
        flex-wrap:wrap;
      }

      .pnl-v6-note{
        font-size:12px;
        opacity:.72;
        margin-top:5px;
      }

      .pnl-v6-manager-list{
        max-height:460px;
        overflow:auto;
        border:
          1px solid
          rgba(255,255,255,.12);
        border-radius:8px;
        padding:6px;
      }

      .pnl-v6-manager-row{
        display:grid;
        grid-template-columns:
          34px 1fr;
        gap:8px;
        align-items:center;
        padding:8px;
        border-bottom:
          1px solid
          rgba(255,255,255,.07);
      }

      .pnl-v6-click-amount{
        cursor:pointer;
        text-decoration:
          underline dotted;
        text-underline-offset:3px;
      }

      @media(max-width:760px){
        .pnl-v6-grid{
          grid-template-columns:1fr;
        }

        .pnl-v6-full{
          grid-column:auto;
        }
      }
    `;

    document.head
      .appendChild(style);
  }

  async function loadMeta(){
    META =
      await api(
        "/finanzas/simple/pnl-v6/meta"
      );

    return META;
  }

  async function loadRows(){
    const search =
      document.querySelector(
        "#pnlV6Search"
      );

    const needle =
      String(
        search?.value
        || ""
      ).trim();

    const data =
      await api(
        "/finanzas/simple/"
        + "pnl-v6/movements"
        + "?q="
        + encodeURIComponent(
            needle
          )
        + "&limit=1000"
      );

    ITEMS =
      data.items || [];

    renderRows();

    const shortcut =
      document.querySelector(
        "#pnlV6Shortcut"
      );

    if(shortcut){
      shortcut.textContent =
        `Clasificar gastos (${ITEMS.length})`;
    }
  }

  function renderRows(){
    const body =
      document.querySelector(
        "#pnlV6Rows"
      );

    if(!body){
      return;
    }

    body.innerHTML =
      ITEMS.length
      ? ITEMS.map(
          item => `
            <tr
              class="pnl-v6-row"
              data-movement="${esc(
                item.id_bank_movement
              )}">
              <td>
                ${esc(
                  item.tx_date
                  || ""
                )}
              </td>

              <td>
                ${esc(
                  [
                    item.bank_name,
                    item.bank_account
                  ]
                  .filter(Boolean)
                  .join(" · ")
                )}
              </td>

              <td>
                <strong>
                  ${esc(
                    item.legal_name
                    || "SIN SOCIEDAD"
                  )}
                </strong>
                <div class="pnl-v6-note">
                  ${esc(
                    item.rut
                    || ""
                  )}
                </div>
              </td>

              <td>
                ${esc(
                  item.description
                  || ""
                )}
              </td>

              <td
                class="pnl-v6-amount">
                ${money(
                  item.amount
                )}
              </td>
            </tr>
          `
        ).join("")
      : `
          <tr>
            <td colspan="5">
              No hay cargos pendientes
              con este filtro.
            </td>
          </tr>
        `;

    body
      .querySelectorAll(
        "[data-movement]"
      )
      .forEach(row => {
        row.onclick = () => {
          openMovement(
            Number(
              row.dataset.movement
            )
          );
        };
      });
  }

  function filterAccounts(
    needle
  ){
    const q =
      String(
        needle || ""
      )
      .trim()
      .toLowerCase();

    const items =
      META?.accounts
      || [];

    if(!q){
      return items;
    }

    return items.filter(
      item =>
        (
          `${item.code} `
          + `${item.label} `
          + `${item.group || ""} `
          + `${item.parent_code || ""}`
        )
        .toLowerCase()
        .includes(q)
    );
  }

  function fillAccountResults(){
    const input =
      document.querySelector(
        "#pnlV6AccountSearch"
      );

    const select =
      document.querySelector(
        "#pnlV6Account"
      );

    if(!input || !select){
      return;
    }

    const items =
      filterAccounts(
        input.value
      );

    select.innerHTML =
      items.map(
        item => `
          <option
            value="${esc(
              item.code
            )}">
            ${esc(item.code)}
            ·
            ${esc(
              item.label
            )}
            ·
            ${esc(
              item.group
              || ""
            )}
          </option>
        `
      ).join("");

    if(
      items.length === 1
    ){
      select.value =
        items[0].code;
    }
  }

  async function openMovement(
    id
  ){
    if(!META){
      await loadMeta();
    }

    const data =
      await api(
        "/finanzas/simple/"
        + "pnl-v6/movements"
        + "?movement_id="
        + encodeURIComponent(id)
        + "&limit=1"
      );

    const item =
      (data.items || [])[0];

    if(!item){
      alert(
        "El movimiento ya no está pendiente."
      );

      await loadRows();
      return;
    }

    const responsibles =
      META.responsibles
      || [];

    modal(
      "Clasificar gasto",
      `
        <div class="pnl-v6-grid">

          <label>
            Fecha
            <input
              class="pnl-v6-readonly"
              readonly
              value="${esc(
                item.tx_date
                || ""
              )}">
          </label>

          <label>
            Monto
            <input
              class="pnl-v6-readonly"
              readonly
              value="${esc(
                money(
                  item.amount
                )
              )}">
          </label>

          <label>
            Cuenta bancaria
            <input
              class="pnl-v6-readonly"
              readonly
              value="${esc(
                [
                  item.bank_name,
                  item.bank_account
                ]
                .filter(Boolean)
                .join(" · ")
              )}">
          </label>

          <label>
            Sociedad / RUT del gasto
            <input
              class="pnl-v6-readonly"
              readonly
              value="${esc(
                (
                  item.legal_name
                  || "SIN SOCIEDAD"
                )
                +
                (
                  item.rut
                  ? ` · ${item.rut}`
                  : ""
                )
              )}">
            <span class="pnl-v6-note">
              Se obtiene automáticamente
              de la cuenta/cartola.
            </span>
          </label>

          <label class="pnl-v6-full">
            Descripción original del banco
            <textarea
              readonly
              class="pnl-v6-readonly"
              rows="2">${esc(
                item.description
                || ""
              )}</textarea>
          </label>

          <label>
            Responsable del gasto
            <select id="pnlV6Responsible">
              <option value="">
                Seleccionar responsable...
              </option>
              ${
                responsibles.map(
                  person => `
                    <option
                      value="${esc(
                        person.id_usuario
                      )}">
                      ${esc(
                        person.nombre
                      )}
                      ${
                        person.centro_costo
                        ? ` · ${esc(
                            person.centro_costo
                          )}`
                        : ""
                      }
                    </option>
                  `
                ).join("")
              }
            </select>
          </label>

          <label>
            P&L destino
            <select id="pnlV6ManagementUnit">
              <option value="">
                Seleccionar P&L...
              </option>
              <option value="CAMALEON">CAMALEÓN</option>
              <option value="GOURMET">GOURMET</option>
              <option value="EXPRESS">EXPRESS</option>
              <option value="DEL SABOR">DEL SABOR</option>
              <option value="BRONTOS">BRONTOS</option>
              <option value="PETRAS">PETRAS</option>
              <option value="MAS FLOW">MAS FLOW</option>
              <option value="CORPORATIVO">CORPORATIVO</option>
              <option value="HOLDING">HOLDING</option>
            </select>
            <span class="pnl-v6-note">
              Define en qué P&L aparecerá este gasto.
              Es independiente de la sociedad que pagó.
            </span>
          </label>

          <label>
            Cuenta del Plan de Cuentas (PUC)
            <input
              id="pnlV6AccountSearch"
              placeholder="Buscar: carne, combustible, arriendo, sueldo...">
            <span class="pnl-v6-note">
              Escribe una palabra y selecciona
              la cuenta correcta abajo.
            </span>
          </label>

          <label class="pnl-v6-full">
            Resultados PUC
            <select
              id="pnlV6Account"
              class="pnl-v6-account-results"
              size="7">
            </select>
          </label>

          <label class="pnl-v6-full">
            Descripción interna
            <textarea
              id="pnlV6Description"
              maxlength="500"
              rows="3"
              placeholder="Ej: carne de la semana, reparación furgón, arriendo Local 4..."></textarea>
          </label>

        </div>

        <div class="pnl-v6-actions">

          <button
            type="button"
            class="btn secondary"
            id="pnlV6Ignore">
            No corresponde a gasto
          </button>

          <button
            type="button"
            class="btn"
            id="pnlV6Save">
            Guardar clasificación
          </button>

        </div>
      `
    );

    const accountSearch =
      document.querySelector(
        "#pnlV6AccountSearch"
      );

    accountSearch.oninput =
      fillAccountResults;

    fillAccountResults();

    document.querySelector(
      "#pnlV6Save"
    ).onclick =
      async () => {
        try{
          const responsible =
            document.querySelector(
              "#pnlV6Responsible"
            ).value;

          const managementUnit =
            document.querySelector(
              "#pnlV6ManagementUnit"
            ).value;

          const account =
            document.querySelector(
              "#pnlV6Account"
            ).value;

          const description =
            document.querySelector(
              "#pnlV6Description"
            ).value.trim();

          if(!managementUnit){
            alert(
              "Selecciona el P&L destino."
            );
            return;
          }

          if(!responsible){
            alert(
              "Selecciona al responsable."
            );
            return;
          }

          if(!account){
            alert(
              "Selecciona una cuenta del PUC."
            );
            return;
          }

          if(!description){
            alert(
              "Indica brevemente a qué corresponde el gasto."
            );
            return;
          }

          const button =
            document.querySelector(
              "#pnlV6Save"
            );

          button.disabled = true;
          button.textContent =
            "Guardando...";

          await api(
            "/finanzas/simple/"
            + "pnl-v6/classify-v62",
            {
              method:"POST",
              body:JSON.stringify({
                id_bank_movement:
                  item.id_bank_movement,
                marca:
                  managementUnit,
                responsable_id:
                  Number(responsible),
                cuenta_code:
                  account,
                descripcion_interna:
                  description
              })
            }
          );

          closeModal();
          await loadRows();
          refreshPnl();

        }catch(error){
          alert(
            errorText(
              error.message
              || error
            )
          );

          const button =
            document.querySelector(
              "#pnlV6Save"
            );

          if(button){
            button.disabled = false;
            button.textContent =
              "Guardar clasificación";
          }
        }
      };

    document.querySelector(
      "#pnlV6Ignore"
    ).onclick =
      async () => {
        if(
          !confirm(
            "¿Retirar este movimiento de gastos pendientes?"
          )
        ){
          return;
        }

        try{
          await api(
            "/finanzas/simple/"
            + "pnl-v6/ignore",
            {
              method:"POST",
              body:JSON.stringify({
                id_bank_movement:
                  item.id_bank_movement
              })
            }
          );

          closeModal();
          await loadRows();

        }catch(error){
          alert(
            errorText(
              error.message
              || error
            )
          );
        }
      };
  }

  function refreshPnl(){
    const button =
      document.querySelector(
        "#fv2Refresh"
      )
      ||
      document.querySelector(
        "#reloadPnl"
      );

    try{
      button?.click();
    }catch(_){}
  }

  async function openResponsibleManager(){
    await loadMeta();

    if(!META.can_manage){
      alert(
        "No tienes permiso para administrar responsables."
      );
      return;
    }

    const candidates =
      META.responsible_candidates
      || [];

    modal(
      "Responsables disponibles en Finanzas",
      `
        <div class="pnl-v6-toolbar">
          <input
            id="pnlV6ResponsibleSearch"
            placeholder="Buscar persona, cargo o centro de costo...">
        </div>

        <div
          id="pnlV6ResponsibleList"
          class="pnl-v6-manager-list">
        </div>

        <div class="pnl-v6-note">
          Activar aquí sólo controla si la persona
          puede ser elegida como responsable del gasto.
          No cambia sus permisos de acceso al CRM.
        </div>
      `
    );

    function render(){
      const q =
        String(
          document.querySelector(
            "#pnlV6ResponsibleSearch"
          ).value
          || ""
        )
        .trim()
        .toLowerCase();

      const visible =
        candidates.filter(
          person =>
            (
              `${person.nombre} `
              + `${person.rol || ""} `
              + `${person.cargo || ""} `
              + `${person.centro_costo || ""}`
            )
            .toLowerCase()
            .includes(q)
        );

      const target =
        document.querySelector(
          "#pnlV6ResponsibleList"
        );

      target.innerHTML =
        visible.map(
          person => `
            <label
              class="pnl-v6-manager-row">

              <input
                type="checkbox"
                data-user="${esc(
                  person.id_usuario
                )}"
                ${
                  person.enabled
                  ? "checked"
                  : ""
                }>

              <span>
                <strong>
                  ${esc(
                    person.nombre
                  )}
                </strong>

                <div class="pnl-v6-note">
                  ${esc(
                    [
                      person.cargo,
                      person.rol,
                      person.centro_costo
                    ]
                    .filter(Boolean)
                    .join(" · ")
                  )}
                </div>
              </span>

            </label>
          `
        ).join("");

      target
        .querySelectorAll(
          "[data-user]"
        )
        .forEach(input => {
          input.onchange =
            async () => {
              const desired =
                input.checked;

              input.disabled = true;

              try{
                await api(
                  "/finanzas/simple/"
                  + "pnl-v6/responsibles",
                  {
                    method:"POST",
                    body:JSON.stringify({
                      id_usuario:
                        Number(
                          input.dataset.user
                        ),
                      enabled:
                        desired
                    })
                  }
                );

                const person =
                  candidates.find(
                    value =>
                      Number(
                        value.id_usuario
                      )
                      ===
                      Number(
                        input.dataset.user
                      )
                  );

                if(person){
                  person.enabled =
                    desired;
                }

                await loadMeta();

              }catch(error){
                input.checked =
                  !desired;

                alert(
                  errorText(
                    error.message
                    || error
                  )
                );
              }finally{
                input.disabled =
                  false;
              }
            };
        });
    }

    document.querySelector(
      "#pnlV6ResponsibleSearch"
    ).oninput = render;

    render();
  }

  async function openPucManager(){
    await loadMeta();

    if(!META.can_manage){
      alert(
        "No tienes permiso para modificar el PUC."
      );
      return;
    }

    modal(
      "Plan de Cuentas (PUC)",
      `
        <div class="pnl-v6-grid">

          <label>
            Código nueva cuenta
            <input
              id="pnlV6PucCode"
              placeholder="Ej: 6311">
          </label>

          <label>
            Nombre
            <input
              id="pnlV6PucName"
              placeholder="Ej: Arriendo Local 1">
          </label>

          <label>
            Tipo
            <select id="pnlV6PucType">
              <option value="expense">
                Gasto
              </option>
              <option value="revenue">
                Ingreso
              </option>
              <option value="asset">
                Activo
              </option>
              <option value="liability">
                Pasivo
              </option>
              <option value="equity">
                Patrimonio
              </option>
            </select>
          </label>

          <label>
            Clasificación
            <select id="pnlV6PucClass">
              <option value="COGS">
                Costo de eventos
              </option>
              <option value="Operacional">
                Operacional
              </option>
              <option value="Administrativo">
                Administrativo
              </option>
              <option value="Fijo">
                Fijo
              </option>
              <option value="Financieros">
                Financiero
              </option>
              <option value="Impuestos">
                Impuestos
              </option>
            </select>
          </label>

          <label class="pnl-v6-full">
            Anidar bajo
            <select id="pnlV6PucParent">
              <option value="">
                Sin cuenta padre
              </option>
              ${
                (META.accounts || [])
                .map(
                  account => `
                    <option
                      value="${esc(
                        account.code
                      )}">
                      ${esc(
                        account.code
                      )}
                      ·
                      ${esc(
                        account.label
                      )}
                    </option>
                  `
                ).join("")
              }
            </select>
          </label>

          <label class="pnl-v6-full">
            Descripción opcional
            <textarea
              id="pnlV6PucDescription"
              rows="2"></textarea>
          </label>

        </div>

        <div class="pnl-v6-actions">
          <span></span>

          <button
            type="button"
            class="btn"
            id="pnlV6PucCreate">
            Crear cuenta
          </button>
        </div>
      `
    );

    document.querySelector(
      "#pnlV6PucCreate"
    ).onclick =
      async () => {
        try{
          const code =
            document.querySelector(
              "#pnlV6PucCode"
            ).value.trim();

          const name =
            document.querySelector(
              "#pnlV6PucName"
            ).value.trim();

          if(!code || !name){
            alert(
              "Código y nombre son obligatorios."
            );
            return;
          }

          await api(
            "/finanzas/simple/"
            + "pnl-v6/puc",
            {
              method:"POST",
              body:JSON.stringify({
                code,
                name,
                type:
                  document.querySelector(
                    "#pnlV6PucType"
                  ).value,
                classification:
                  document.querySelector(
                    "#pnlV6PucClass"
                  ).value,
                parent_code:
                  document.querySelector(
                    "#pnlV6PucParent"
                  ).value || null,
                description:
                  document.querySelector(
                    "#pnlV6PucDescription"
                  ).value.trim()
              })
            }
          );

          await loadMeta();

          alert(
            `Cuenta ${code} creada correctamente.`
          );

          closeModal();

        }catch(error){
          alert(
            errorText(
              error.message
              || error
            )
          );
        }
      };
  }

  function installPanel(){
    if(
      document.querySelector(
        "#pnlV6Panel"
      )
    ){
      return;
    }

    const oldRows =
      document.querySelector(
        "#movementRows"
      );

    if(!oldRows){
      return;
    }

    const oldWrap =
      oldRows.closest(
        ".table-wrap"
      )
      ||
      oldRows.closest(
        "table"
      );

    if(!oldWrap){
      return;
    }

    [
      "#expenseAccount",
      "#classify",
      "#ignore",
      "#reloadMovements"
    ].forEach(selector => {
      const node =
        document.querySelector(
          selector
        );

      if(node){
        node.style.display =
          "none";
      }
    });

    oldWrap.style.display =
      "none";

    const panel =
      document.createElement(
        "div"
      );

    panel.id =
      "pnlV6Panel";

    panel.innerHTML = `
      <div class="pnl-v6-toolbar">

        <strong>
          Gastos por clasificar
        </strong>

        <input
          id="pnlV6Search"
          placeholder="Buscar movimiento, proveedor, banco, sociedad o RUT...">

        <button
          type="button"
          class="btn secondary"
          id="pnlV6Reload">
          Actualizar
        </button>

        <button
          type="button"
          class="btn secondary"
          id="pnlV6Responsibles">
          Responsables
        </button>

        <button
          type="button"
          class="btn secondary"
          id="pnlV6Puc">
          Plan de Cuentas
        </button>

      </div>

      <div class="table-wrap">
        <table class="pnl-v6-table">
          <thead>
            <tr>
              <th>Fecha</th>
              <th>Banco / cuenta</th>
              <th>Sociedad / RUT</th>
              <th>Descripción original</th>
              <th>Monto</th>
            </tr>
          </thead>

          <tbody id="pnlV6Rows">
            <tr>
              <td colspan="5">
                Cargando...
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="pnl-v6-note">
        Haz click directamente sobre una fila
        para clasificar el gasto.
      </div>
    `;

    oldWrap.parentNode
      .insertBefore(
        panel,
        oldWrap
      );

    let timer = null;

    document.querySelector(
      "#pnlV6Search"
    ).oninput = () => {
      clearTimeout(timer);

      timer = setTimeout(
        () => {
          loadRows()
            .catch(console.error);
        },
        250
      );
    };

    document.querySelector(
      "#pnlV6Reload"
    ).onclick =
      () =>
        loadRows()
          .catch(error =>
            alert(
              errorText(
                error.message
                || error
              )
            )
          );

    document.querySelector(
      "#pnlV6Responsibles"
    ).onclick =
      () =>
        openResponsibleManager()
          .catch(error =>
            alert(
              errorText(
                error.message
                || error
              )
            )
          );

    document.querySelector(
      "#pnlV6Puc"
    ).onclick =
      () =>
        openPucManager()
          .catch(error =>
            alert(
              errorText(
                error.message
                || error
              )
            )
          );

    loadMeta()
      .then(loadRows)
      .catch(error => {
        document.querySelector(
          "#pnlV6Rows"
        ).innerHTML = `
          <tr>
            <td colspan="5">
              ${esc(
                errorText(
                  error.message
                  || error
                )
              )}
            </td>
          </tr>
        `;
      });
  }

  function installShortcut(){
    if(
      document.querySelector(
        "#pnlV6Shortcut"
      )
    ){
      return;
    }

    const root =
      document.querySelector(
        "#financeV2"
      );

    if(!root){
      return;
    }

    const toolbar =
      root.querySelector(
        ".fv2-toolbar"
      );

    if(!toolbar){
      return;
    }

    const button =
      document.createElement(
        "button"
      );

    button.id =
      "pnlV6Shortcut";

    button.type =
      "button";

    button.className =
      "btn secondary";

    button.textContent =
      "Clasificar gastos";

    button.onclick = () => {
      const tab =
        document.querySelector(
          '[data-tab="cartolas"]'
        );

      try{
        tab?.click();
      }catch(_){}

      setTimeout(
        () =>
          document.querySelector(
            "#pnlV6Panel"
          )
          ?.scrollIntoView({
            behavior:"smooth",
            block:"start"
          }),
        150
      );
    };

    toolbar.appendChild(
      button
    );
  }

  function renameConcepts(){
    const root =
      document.querySelector(
        "#financeV2"
      );

    if(!root){
      return;
    }

    root
      .querySelectorAll(
        "h1,h2,h3,h4,strong"
      )
      .forEach(node => {
        const text =
          String(
            node.textContent
            || ""
          ).trim();

        if(text === "P&L Gestión"){
          node.textContent =
            "Resultado mensual (P&L)";
        }

        if(
          text ===
          "Flujo de caja operativo · al día"
        ){
          node.textContent =
            "Resultado operativo · al día";
        }
      });
  }

  function makeAmountsClickable(){
    const root =
      document.querySelector(
        "#financeV2"
      );

    if(!root){
      return;
    }

    root
      .querySelectorAll(
        "button"
      )
      .forEach(button => {
        const label =
          String(
            button.textContent
            || ""
          )
          .trim()
          .toLowerCase();

        if(
          !label.includes(
            "ver movimientos"
          )
          &&
          !label.includes(
            "ver detalle"
          )
        ){
          return;
        }

        const row =
          button.closest("tr");

        if(!row){
          return;
        }

        const cells =
          Array.from(
            row.querySelectorAll("td")
          )
          .filter(
            cell =>
              String(
                cell.textContent
                || ""
              ).includes("$")
          );

        cells.forEach(cell => {
          if(
            cell.dataset
              .pnlV6Click === "1"
          ){
            return;
          }

          cell.dataset
            .pnlV6Click = "1";

          cell.classList.add(
            "pnl-v6-click-amount"
          );

          cell.onclick = event => {
            event.stopPropagation();

            try{
              button.click();
            }catch(_){}
          };
        });

        if(cells.length){
          button.style.display =
            "none";
        }
      });
  }

  function maintain(){
    installPanel();
    installShortcut();
    renameConcepts();
    makeAmountsClickable();
  }

  installStyle();

  if(
    document.readyState
    === "loading"
  ){
    document.addEventListener(
      "DOMContentLoaded",
      maintain,
      {
        once:true
      }
    );
  }else{
    maintain();
  }

  const observer =
    new MutationObserver(
      () => maintain()
    );

  observer.observe(
    document.documentElement,
    {
      childList:true,
      subtree:true
    }
  );

  setTimeout(
    maintain,
    300
  );

  window.PNL_V6_MARKER =
    MARKER;


  // === GD PNL V62 INCOME UI START ===

  let INCOME_V62_ITEMS = [];
  let INCOME_V62_ACCOUNTS = [];


  const PNL_V62_UNITS = [
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


  function pnlV62UnitOptions(
    selected
  ){
    const current =
      String(
        selected
        || document.querySelector(
             "#pnlBrand"
           )?.value
        || ""
      ).toUpperCase();

    return `
      <option value="">
        Seleccionar P&L...
      </option>
      ${
        PNL_V62_UNITS.map(
          ([code, label]) => `
            <option
              value="${esc(code)}"
              ${
                current === code
                ? "selected"
                : ""
              }>
              ${esc(label)}
            </option>
          `
        ).join("")
      }
    `;
  }


  async function refreshIncomeShortcut(){

    const button =
      document.querySelector(
        "#pnlV6IncomeShortcut"
      );

    if(!button){
      return;
    }

    try{

      const data =
        await api(
          "/finanzas/simple/"
          + "pnl-v6/income-movements"
          + "?limit=1000"
        );

      INCOME_V62_ITEMS =
        data.items || [];

      button.textContent =
        `Clasificar ingresos (${INCOME_V62_ITEMS.length})`;

    }catch(error){

      console.error(
        "income shortcut",
        error
      );

      button.textContent =
        "Clasificar ingresos";

    }
  }


  function installIncomeShortcut(){

    if(
      document.querySelector(
        "#pnlV6IncomeShortcut"
      )
    ){
      return true;
    }

    const expenseButton =
      document.querySelector(
        "#pnlV6Shortcut"
      );

    if(!expenseButton){
      return false;
    }

    const button =
      document.createElement(
        "button"
      );

    button.type = "button";
    button.id =
      "pnlV6IncomeShortcut";

    button.className =
      expenseButton.className;

    button.textContent =
      "Clasificar ingresos";

    button.onclick =
      openIncomeQueue;

    expenseButton.insertAdjacentElement(
      "afterend",
      button
    );

    refreshIncomeShortcut();

    return true;
  }


  function renderIncomeQueue(){

    const body =
      document.querySelector(
        "#pnlV6IncomeRows"
      );

    if(!body){
      return;
    }

    const needle =
      String(
        document.querySelector(
          "#pnlV6IncomeSearch"
        )?.value
        || ""
      )
      .trim()
      .toLowerCase();

    const rows =
      INCOME_V62_ITEMS.filter(
        item => {

          if(!needle){
            return true;
          }

          const haystack = [
            item.tx_date,
            item.bank_name,
            item.bank_account,
            item.legal_name,
            item.rut,
            item.description,
            item.reference,
            item.amount,
          ]
          .filter(
            value =>
              value !== null
              && value !== undefined
          )
          .join(" ")
          .toLowerCase();

          return haystack.includes(
            needle
          );
        }
      );

    body.innerHTML =
      rows.length
      ? rows.map(
          item => `
            <tr
              class="pnl-v6-row"
              data-income-movement="${
                esc(
                  item.id_bank_movement
                )
              }">

              <td>
                ${esc(item.tx_date || "")}
              </td>

              <td>
                ${esc(
                  [
                    item.bank_name,
                    item.bank_account
                  ]
                  .filter(Boolean)
                  .join(" · ")
                )}
              </td>

              <td>
                <strong>
                  ${esc(
                    item.legal_name
                    || "SIN SOCIEDAD"
                  )}
                </strong>
              </td>

              <td>
                ${esc(
                  item.description
                  || ""
                )}
              </td>

              <td
                class="pnl-v6-amount">
                ${money(
                  item.amount
                )}
              </td>
            </tr>
          `
        ).join("")
      : `
          <tr>
            <td colspan="5">
              No hay ingresos pendientes
              con este filtro.
            </td>
          </tr>
        `;

    body.querySelectorAll(
      "[data-income-movement]"
    )
    .forEach(
      row => {

        row.onclick = () => {

          openIncomeMovement(
            Number(
              row.dataset
                 .incomeMovement
            )
          );

        };

      }
    );
  }


  async function openIncomeQueue(){

    try{

      const [
        movements,
        accounts
      ] = await Promise.all([

        api(
          "/finanzas/simple/"
          + "pnl-v6/income-movements"
          + "?limit=1000"
        ),

        api(
          "/finanzas/simple/"
          + "pnl-v6/income-accounts"
        ),

      ]);

      INCOME_V62_ITEMS =
        movements.items || [];

      INCOME_V62_ACCOUNTS =
        accounts.items || [];

      modal(
        "Clasificar ingresos",
        `
          <div class="pnl-v6-toolbar">

            <input
              id="pnlV6IncomeSearch"
              placeholder="Buscar depósito, cliente, monto, cuenta...">

            <span class="pnl-v6-note">
              ${
                INCOME_V62_ITEMS.length
              }
              ingreso(s) pendiente(s)
            </span>

          </div>

          <div
            style="
              overflow:auto;
              max-height:65vh;
            ">

            <table class="pnl-v6-table">
              <thead>
                <tr>
                  <th>Fecha</th>
                  <th>Cuenta</th>
                  <th>Sociedad</th>
                  <th>Descripción</th>
                  <th>Monto</th>
                </tr>
              </thead>

              <tbody
                id="pnlV6IncomeRows">
              </tbody>
            </table>

          </div>

          <div class="pnl-v6-note">
            Los pagos de clientes deben ir
            por Conciliar depósitos.
            Esta bandeja es para abonos que
            requieren clasificación contable.
          </div>
        `
      );

      const search =
        document.querySelector(
          "#pnlV6IncomeSearch"
        );

      if(search){
        search.oninput =
          renderIncomeQueue;
      }

      renderIncomeQueue();

    }catch(error){

      alert(
        errorText(
          error.message
          || error
        )
      );

    }
  }


  function incomeAccountResults(
    needle
  ){

    const q =
      String(
        needle || ""
      )
      .trim()
      .toLowerCase();

    if(!q){
      return INCOME_V62_ACCOUNTS;
    }

    return INCOME_V62_ACCOUNTS
      .filter(
        account => {

          const text = [
            account.code,
            account.label,
            account.type,
            account.classification,
          ]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();

          return text.includes(q);
        }
      );
  }


  function fillIncomeAccounts(){

    const input =
      document.querySelector(
        "#pnlV6IncomeAccountSearch"
      );

    const select =
      document.querySelector(
        "#pnlV6IncomeAccount"
      );

    if(!select){
      return;
    }

    const items =
      incomeAccountResults(
        input?.value
        || ""
      );

    select.innerHTML =
      items.length
      ? items.map(
          account => `
            <option
              value="${esc(
                account.code
              )}">
              ${esc(
                account.code
              )}
              ·
              ${esc(
                account.label
              )}
              ·
              ${
                String(
                  account.type
                  || ""
                ).toLowerCase()
                === "revenue"
                ? "IMPACTA P&L"
                : "BALANCE"
              }
            </option>
          `
        ).join("")
      : `
          <option value="">
            Sin resultados
          </option>
        `;
  }


  async function openIncomeMovement(
    movementId
  ){

    try{

      if(
        !INCOME_V62_ACCOUNTS.length
      ){
        const accountData =
          await api(
            "/finanzas/simple/"
            + "pnl-v6/income-accounts"
          );

        INCOME_V62_ACCOUNTS =
          accountData.items || [];
      }

      const data =
        await api(
          "/finanzas/simple/"
          + "pnl-v6/income-movements"
          + "?movement_id="
          + encodeURIComponent(
              movementId
            )
          + "&limit=1"
        );

      const item =
        (data.items || [])[0];

      if(!item){
        throw new Error(
          "El ingreso ya no está pendiente."
        );
      }

      modal(
        "Clasificar ingreso",
        `
          <div class="pnl-v6-grid">

            <label>
              Fecha
              <div class="pnl-v6-readonly">
                ${esc(
                  item.tx_date
                  || ""
                )}
              </div>
            </label>

            <label>
              Monto
              <div class="pnl-v6-readonly">
                ${money(
                  item.amount
                )}
              </div>
            </label>

            <label class="pnl-v6-full">
              Banco / sociedad
              <div class="pnl-v6-readonly">
                ${esc(
                  [
                    item.bank_name,
                    item.bank_account,
                    item.legal_name
                  ]
                  .filter(Boolean)
                  .join(" · ")
                )}
              </div>
            </label>

            <label class="pnl-v6-full">
              Descripción cartola
              <div class="pnl-v6-readonly">
                ${esc(
                  item.description
                  || ""
                )}
              </div>
            </label>

            <label>
              P&L destino
              <select
                id="pnlV6IncomeUnit">
                ${
                  pnlV62UnitOptions()
                }
              </select>
            </label>

            <label>
              Cuenta PUC
              <input
                id="pnlV6IncomeAccountSearch"
                placeholder="Buscar venta, préstamo, transferencia...">
            </label>

            <label class="pnl-v6-full">
              Resultados PUC
              <select
                id="pnlV6IncomeAccount"
                class="pnl-v6-account-results"
                size="7">
              </select>
            </label>

            <label class="pnl-v6-full">
              Descripción interna
              <textarea
                id="pnlV6IncomeDescription"
                maxlength="500"
                rows="3"
                placeholder="Ej: otro ingreso Brontos, préstamo bancario, transferencia interna..."></textarea>
            </label>

          </div>

          <div class="pnl-v6-note">
            Sólo una cuenta PUC de tipo
            INGRESO aumenta el P&L.
            Activo, Pasivo o Patrimonio
            quedan clasificados sin aumentar ventas.
          </div>

          <div class="pnl-v6-actions">

            <button
              type="button"
              class="btn secondary"
              id="pnlV6IncomeBack">
              Volver
            </button>

            <button
              type="button"
              class="btn"
              id="pnlV6IncomeSave">
              Guardar clasificación
            </button>

          </div>
        `
      );

      const accountSearch =
        document.querySelector(
          "#pnlV6IncomeAccountSearch"
        );

      if(accountSearch){
        accountSearch.oninput =
          fillIncomeAccounts;
      }

      fillIncomeAccounts();

      document.querySelector(
        "#pnlV6IncomeBack"
      ).onclick =
        openIncomeQueue;

      document.querySelector(
        "#pnlV6IncomeSave"
      ).onclick =
        async () => {

          const unit =
            document.querySelector(
              "#pnlV6IncomeUnit"
            ).value;

          const account =
            document.querySelector(
              "#pnlV6IncomeAccount"
            ).value;

          const description =
            document.querySelector(
              "#pnlV6IncomeDescription"
            ).value.trim();

          if(!unit){
            alert(
              "Selecciona el P&L destino."
            );
            return;
          }

          if(!account){
            alert(
              "Selecciona la cuenta PUC."
            );
            return;
          }

          if(!description){
            alert(
              "Indica a qué corresponde el ingreso."
            );
            return;
          }

          const button =
            document.querySelector(
              "#pnlV6IncomeSave"
            );

          button.disabled = true;
          button.textContent =
            "Guardando...";

          try{

            const result =
              await api(
                "/finanzas/simple/"
                + "pnl-v6/classify-income",
                {
                  method:"POST",
                  body:JSON.stringify({
                    id_bank_movement:
                      item.id_bank_movement,
                    cuenta_code:
                      account,
                    marca:
                      unit,
                    descripcion_interna:
                      description
                  })
                }
              );

            if(
              result.affects_pnl
            ){
              alert(
                "Ingreso clasificado. "
                + "Impactará el P&L "
                + unit + "."
              );
            }else{
              alert(
                "Abono clasificado como "
                + "movimiento de balance. "
                + "No aumenta ventas."
              );
            }

            await refreshIncomeShortcut();

            closeModal();

            if(
              typeof refreshPnl
              === "function"
            ){
              refreshPnl();
            }

          }catch(error){

            alert(
              errorText(
                error.message
                || error
              )
            );

            button.disabled = false;
            button.textContent =
              "Guardar clasificación";
          }

        };

    }catch(error){

      alert(
        errorText(
          error.message
          || error
        )
      );

    }
  }


  function bootIncomeUi(){

    if(
      installIncomeShortcut()
    ){
      return;
    }

    let attempts = 0;

    const timer =
      setInterval(
        () => {

          attempts += 1;

          if(
            installIncomeShortcut()
            || attempts >= 20
          ){
            clearInterval(
              timer
            );
          }

        },
        300
      );
  }


  if(
    document.readyState
    === "loading"
  ){
    document.addEventListener(
      "DOMContentLoaded",
      bootIncomeUi
    );
  }else{
    bootIncomeUi();
  }

  // === GD PNL V62 INCOME UI END ===


})();
