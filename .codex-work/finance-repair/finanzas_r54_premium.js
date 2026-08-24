(() => {
  "use strict";

  /* GD_FINANCE_R543_BOOTSTRAP_FIX */
  const API_BASE = ["localhost", "127.0.0.1"].includes(
    String(location.hostname || "").toLowerCase()
  ) ? "" : "/crm";

  const apiURL = path => {
    const value = String(path || "");
    if (!API_BASE) return value.replace(/^\/crm/, "");
    if (value.startsWith("/crm/")) return value;
    return API_BASE + (value.startsWith("/") ? value : "/" + value);
  };

  const ENTITY_STORAGE_KEY =
    "gdFinanceLegalEntityId";

  function storedEntityId() {
    try {
      return Number(
        window.localStorage.getItem(
          ENTITY_STORAGE_KEY
        ) || 0
      );
    } catch (_error) {
      return 0;
    }
  }

  function rememberEntityId(value) {
    try {
      window.localStorage.setItem(
        ENTITY_STORAGE_KEY,
        String(Number(value || 0))
      );
    } catch (_error) {
      // La navegación financiera sigue funcionando sin almacenamiento local.
    }
  }

  const $ = selector =>
    document.querySelector(
      selector
    );

  const $$ = selector =>
    Array.from(
      document.querySelectorAll(
        selector
      )
    );

  const state = {
    accounts: [],
    entities: [],
    entityId: 0,
    period: "",
    payables: [],
    resolutionPayables: [],
    selectedPayable: null,
    selectedMovement: null,
    movementCandidates: [],
    classification: null,
    puc: [],
    bankMovements: [],
  };

  const CLP =
    new Intl.NumberFormat(
      "es-CL",
      {
        style: "currency",
        currency: "CLP",
        minimumFractionDigits: 0,
        maximumFractionDigits: 0,
      }
    );

  const NUMBER =
    new Intl.NumberFormat(
      "es-CL",
      {
        maximumFractionDigits: 0,
      }
    );

  function money(value) {
    return CLP.format(
      Math.round(
        Number(
          value || 0
        )
      )
    );
  }

  function num(value) {
    const clean =
      String(
        value ?? ""
      ).replace(
        /[^\d-]/g,
        ""
      );

    const n =
      Number(
        clean || 0
      );

    return Number.isFinite(n)
      ? Math.round(n)
      : 0;
  }

  function esc(value) {
    return String(
      value ?? ""
    )
      .replaceAll(
        "&",
        "&amp;"
      )
      .replaceAll(
        "<",
        "&lt;"
      )
      .replaceAll(
        ">",
        "&gt;"
      )
      .replaceAll(
        '"',
        "&quot;"
      );
  }

  function fmtDate(value) {
    if (!value) {
      return "—";
    }

    try {
      return new Intl.DateTimeFormat(
        "es-CL"
      ).format(
        new Date(
          String(value)
          + (
            String(value).length <= 10
              ? "T12:00:00"
              : ""
          )
        )
      );
    } catch (_) {
      return String(value);
    }
  }

  function currentToken() {
    const keys = [
      "access_token",
      "token",
      "gd_token",
      "auth_token",
      "jwt",
    ];

    for (
      const storage
      of [
        localStorage,
        sessionStorage,
      ]
    ) {
      for (
        const key
        of keys
      ) {
        const value =
          storage.getItem(
            key
          );

        if (value) {
          return value;
        }
      }
    }

    return "";
  }

  function authHeaders(
    extra = {}
  ) {
    try {
      if (
        window.parent
        &&
        window.parent !== window
        &&
        window.parent.GD
        &&
        typeof window.parent
          .GD.authHeaders
          === "function"
      ) {
        return window.parent
          .GD.authHeaders(
            extra
          );
      }
    } catch (_) {}

    const token =
      currentToken();

    return token
      ? {
          ...extra,
          Authorization:
            "Bearer " + token,
        }
      : {
          ...extra,
        };
  }

  function currentRoleKey() {
    try {
      const me = window.parent?.GD?.me || window.GD?.me || {};
      const role = me.role || me.rol || "";
      return String(role).toUpperCase().replace(/[^A-Z0-9]/g, "");
    } catch (_) {
      return "";
    }
  }

  function isControlReadOnly() {
    return false;
  }

  function errorMessage(
    data,
    fallback
  ) {
    if (
      typeof data
      === "string"
    ) {
      return data;
    }

    if (
      typeof data?.detail
      === "string"
    ) {
      return data.detail;
    }

    if (
      typeof data?.detail?.message
      === "string"
    ) {
      return data.detail.message;
    }

    if (
      typeof data?.message
      === "string"
    ) {
      return data.message;
    }

    return fallback;
  }

  async function api(
    url,
    options = {}
  ) {
    const method = String(options.method || "GET").toUpperCase();
    if (isControlReadOnly() && !["GET", "HEAD", "OPTIONS"].includes(method)) {
      throw new Error("Tu perfil de Control de Gestión es sólo de consulta.");
    }

    const response =
      await fetch(
        apiURL(url),
        {
          ...options,
          headers:
            authHeaders({
              Accept:
                "application/json",
              ...(
                options.headers
                || {}
              ),
            }),
        }
      );

    const type =
      response.headers.get(
        "content-type"
      )
      || "";

    let data;

    if (
      type.includes(
        "application/json"
      )
    ) {
      data =
        await response.json();
    } else {
      data =
        await response.text();
    }

    if (!response.ok) {
      throw new Error(
        errorMessage(
          data,
          `HTTP ${response.status}`
        )
      );
    }

    return data;
  }

  function toast(
    message,
    error = false
  ) {
    const node =
      $("#toast");

    node.textContent =
      String(message);

    node.classList.toggle(
      "error",
      error
    );

    node.style.display =
      "block";

    clearTimeout(
      toast.timer
    );

    toast.timer =
      setTimeout(
        () => {
          node.style.display =
            "none";
        },
        3500
      );
  }

  function entityAccounts() {
    return state.accounts
      .filter(
        item =>
          Number(
            item.id_legal_entity
          )
          ===
          Number(
            state.entityId
          )
      );
  }

  function monthParts() {
    const [
      year,
      month,
    ] = String(
      state.period || ""
    ).split("-");

    return {
      year:
        Number(year || 0),
      month:
        Number(month || 0),
    };
  }

  function samePeriod(
    value
  ) {
    if (!value) {
      return false;
    }

    const {
      year,
      month,
    } = monthParts();

    if (
      !year
      ||
      !month
    ) {
      return true;
    }

    const parts =
      String(value)
      .slice(
        0,
        10
      )
      .split("-");

    return (
      Number(parts[0])
      === year
      &&
      Number(parts[1])
      === month
    );
  }

  function activateView(
    name
  ) {
    $$(".finance-nav button")
      .forEach(
        button => {
          button.classList.toggle(
            "active",
            button.dataset.view
            === name
          );
        }
      );

    $$(".finance-view")
      .forEach(
        view => {
          view.classList.toggle(
            "active",
            view.id
            ===
            `view-${name}`
          );
        }
      );

    if (
      name === "summary"
    ) {
      loadSummary();
    }

    if (
      name === "invoices"
    ) {
      loadInvoices();
    }

    if (
      name === "movements"
    ) {
      loadBankMovements();
    }

    if (
      name === "suppliers"
    ) {
      loadSuppliers();
    }

    if (
      name === "puc"
    ) {
      loadPuc();
    }

    if (
      name === "pnl"
    ) {
      loadPnl();
    }

    if (
      name === "settings"
    ) {
      loadConnections();
    }
  }

  function requestedView() {
    const hash = String(location.hash || "")
      .replace(/^#/, "")
      .trim()
      .toLowerCase();

    const aliases = {
      "": "summary",
      summary: "summary",
      invoices: "invoices",
      payables: "invoices",
      reconciliation: "movements",
      movements: "movements",
      suppliers: "suppliers",
      pnl: "pnl",
      puc: "puc",
      sii: "settings",
      settings: "settings",
    };

    const view = aliases[hash] || "summary";
    return isControlReadOnly() && view === "settings" ? "summary" : view;
  }

  async function loadContext() {
    const [connectionsResponse, accountsResponse] = await Promise.all([
      api("/api/finance/sii/connections"),
      api("/api/finance/sii/reconciliation/accounts"),
    ]);

    state.accounts =
      accountsResponse.items
      || [];

    state.entities = (connectionsResponse.items || [])
      .map(item => ({
        id: Number(item.id_legal_entity || 0),
        name: item.name || item.legal_name || `Sociedad ${item.id_legal_entity}`,
        rut: item.rut_normalized || item.rut || "",
      }))
      .filter(item => item.id > 0)
      .sort((a, b) => String(a.name).localeCompare(String(b.name), "es"));

    const validIds = new Set(state.entities.map(item => item.id));
    const rememberedId = storedEntityId();
    const primaryBankAccount = state.accounts
      .filter(
        item => validIds.has(Number(item.id_legal_entity || 0))
      )
      .sort(
        (a, b) => Number(b.pending_movements || 0) - Number(a.pending_movements || 0)
      )[0];
    const firstBankEntityId = Number(
      primaryBankAccount?.id_legal_entity || 0
    );

    if (!state.entityId || !validIds.has(Number(state.entityId))) {
      state.entityId = validIds.has(rememberedId)
        ? rememberedId
        : firstBankEntityId || (state.entities.length ? state.entities[0].id : 0);
    }

    const select =
      $("#globalEntity");

    select.innerHTML =
      state.entities
        .map(
          item => `
            <option
              value="${item.id}"
            >
              ${esc(item.name)}
              ·
              ${esc(item.rut)}
            </option>
          `
        )
        .join("");

    if (state.entityId) {
      select.value = String(state.entityId);
    }

    if (
      !state.entityId
      &&
      state.entities.length
    ) {
      state.entityId =
        state.entities[0].id;
    }

    select.value =
      String(
        state.entityId
      );

    fillBankAccounts();

    updateContextStatus();
  }

  function fillBankAccounts() {
    const accounts =
      entityAccounts();

    const options =
      `<option value="">
        Seleccionar cuenta bancaria...
      </option>`
      +
      accounts
        .map(
          account => `
            <option
              value="${
                Number(
                  account.id_bank_account
                )
              }"
            >
              ${esc(
                account.bank_name
                || "Banco"
              )}
              ·
              ${esc(
                account.label
                || "Cuenta"
              )}
              ·
              ${
                Number(
                  account.pending_movements
                  || 0
                )
              }
              pendientes
            </option>
          `
        )
        .join("");

    const movementSelect =
      $("#movementBankAccount");

    const uploadSelect =
      $("#statementBankAccount");

    movementSelect.innerHTML = options;
    if (uploadSelect) uploadSelect.innerHTML = options;

    if (
      accounts.length
      === 1
    ) {
      movementSelect.value =
        String(
          accounts[0]
            .id_bank_account
        );

      if (uploadSelect) {
        uploadSelect.value = movementSelect.value;
      }
    }
  }

  async function uploadBankStatement() {
    const accountId = Number(
      $("#statementBankAccount")?.value || 0
    );
    const file = $("#statementFilePremium")?.files?.[0];
    const status = $("#statementUploadStatus");
    const button = $("#uploadStatementPremium");

    if (!accountId || !file) {
      toast("Selecciona la cuenta bancaria y el archivo de cartola.", true);
      return;
    }

    const form = new FormData();
    form.append("id_bank_account", String(accountId));
    form.append("file", file);
    button.disabled = true;
    if (status) status.textContent = "Procesando cartola…";

    try {
      const response = await api(
        "/finanzas/simple/cartolas/upload",
        { method: "POST", body: form }
      );
      const message = response.duplicate_file
        ? "Esta cartola ya estaba cargada; no se duplicaron movimientos."
        : `Cartola cargada: ${Number(response.imported_rows || 0)} movimiento(s) nuevo(s).`;
      if (status) status.textContent = message;
      $("#statementFilePremium").value = "";
      await loadContext();
      $("#movementBankAccount").value = String(accountId);
      $("#statementBankAccount").value = String(accountId);
      await Promise.all([loadBankMovements(), loadSummary()]);
      toast(message);
    } catch (error) {
      if (status) status.textContent = error.message;
      toast(error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  function updateContextStatus() {
    const entity =
      state.entities.find(
        item =>
          item.id
          ===
          Number(
            state.entityId
          )
      );

    $("#contextStatus")
      .textContent =
      entity
        ? `${entity.name} · ${entity.rut}`
        : "Selecciona una empresa";
  }

  async function ensureMeta() {
    if (
      state.classification
    ) {
      return;
    }

    state.classification =
      await api(
        "/api/finance/sii/reconciliation/classification-meta"
      );
  }

  async function loadSummary() {
    if (
      !state.entityId
    ) {
      return;
    }

    try {
      const [
        payables,
        puc,
      ] = await Promise.all([
        api(
          "/api/finance/sii/reconciliation/open-payables"
          + `?legal_entity_id=${state.entityId}`
          + "&limit=1000"
        ),

        api(
          "/api/finance/sii/chart-of-accounts"
        ),
      ]);

      const invoiceItems =
        payables.items
        || [];

      const totalPayable =
        invoiceItems.reduce(
          (
            total,
            item
          ) =>
            total
            +
            Number(
              item.balance
              || 0
            ),
          0
        );

      const today =
        new Date();

      today.setHours(
        0,
        0,
        0,
        0
      );

      const overdue =
        invoiceItems.filter(
          item => {
            if (
              !item.due_date
            ) {
              return false;
            }

            return (
              new Date(
                item.due_date
                + "T12:00:00"
              )
              <
              today
            );
          }
        );

      const accounts =
        entityAccounts();

      const bankPending =
        accounts.reduce(
          (
            total,
            account
          ) =>
            total
            +
            Number(
              account.pending_movements
              || 0
            ),
          0
        );

      const bankAmount =
        accounts.reduce(
          (
            total,
            account
          ) =>
            total
            +
            Number(
              account.pending_amount
              || 0
            ),
          0
        );

      $("#summaryCards")
        .innerHTML = `
          <article class="metric-card">
            <span>
              Facturas por pagar
            </span>

            <strong>
              ${
                NUMBER.format(
                  invoiceItems.length
                )
              }
            </strong>

            <small>
              ${money(totalPayable)}
            </small>
          </article>

          <article class="metric-card">
            <span>
              Facturas vencidas
            </span>

            <strong>
              ${
                NUMBER.format(
                  overdue.length
                )
              }
            </strong>

            <small>
              Requieren revisión
            </small>
          </article>

          <article class="metric-card">
            <span>
              Movimientos pendientes
            </span>

            <strong>
              ${
                NUMBER.format(
                  bankPending
                )
              }
            </strong>

            <small>
              ${money(bankAmount)}
            </small>
          </article>

          <article class="metric-card">
            <span>
              Cuentas PUC
            </span>

            <strong>
              ${
                NUMBER.format(
                  puc.total
                  || 0
                )
              }
            </strong>

            <small>
              Estructura contable activa
            </small>
          </article>
        `;

    } catch (error) {
      toast(
        error.message,
        true
      );
    }
  }

  async function loadInvoices() {
    if (
      !state.entityId
    ) {
      return;
    }

    const q =
      $("#invoiceSearch")
        .value
        .trim();

    let url =
      "/api/finance/sii/reconciliation/open-payables"
      + `?legal_entity_id=${state.entityId}`
      + "&limit=1000";

    if (q) {
      url +=
        "&q="
        + encodeURIComponent(
            q
          );
    }

    try {
      const response =
        await api(url);

      state.payables =
        (
          response.items
          || []
        ).filter(
          item =>
            samePeriod(
              item.issue_date
            )
        );

      $("#invoiceCount")
        .textContent =
        `${
          state.payables.length
        } documentos`;

      renderInvoiceList();

    } catch (error) {
      toast(
        error.message,
        true
      );
    }
  }

  function renderInvoiceList() {
    const list =
      $("#invoiceList");

    if (
      !state.payables.length
    ) {
      list.innerHTML = `
        <div class="empty-state">
          <strong>
            Sin facturas abiertas
          </strong>

          <span>
            No hay CxP abiertas
            en este período.
          </span>
        </div>
      `;

      return;
    }

    list.innerHTML =
      state.payables
        .map(
          item => `
            <button
              class="invoice-row ${
                Number(
                  state.selectedPayable
                    ?.payable_id
                )
                ===
                Number(
                  item.payable_id
                )
                  ? "active"
                  : ""
              }"
              data-payable="${
                Number(
                  item.payable_id
                )
              }"
            >

              <div class="invoice-top">
                <span>
                  ${fmtDate(
                    item.issue_date
                  )}
                </span>

                <strong class="money">
                  ${money(
                    item.balance
                  )}
                </strong>
              </div>

              <div class="supplier">
                ${esc(
                  item.supplier_name
                )}
              </div>

              <div class="meta">
                ${
                  esc(
                    item.document_type
                    || "DTE"
                  )
                }
                · Folio
                ${
                  esc(
                    item.folio
                    || "—"
                  )
                }
                ·
                ${
                  esc(
                    item.payable_status
                    || "PENDING"
                  )
                }
              </div>

            </button>
          `
        )
        .join("");

    $$(
      "[data-payable]"
    ).forEach(
      button => {
        button.onclick =
          () => selectInvoice(
            Number(
              button.dataset.payable
            )
          );
      }
    );
  }

  async function selectInvoice(
    payableId
  ) {
    state.selectedPayable =
      state.payables.find(
        item =>
          Number(
            item.payable_id
          )
          === payableId
      )
      || null;

    state.selectedMovement =
      null;

    renderInvoiceList();

    if (
      !state.selectedPayable
    ) {
      return;
    }

    await ensureMeta();

    try {
      const response =
        await api(
          `/api/finance/sii/reconciliation/payables/${payableId}/movement-candidates`
          + "?limit=80"
        );

      state.selectedPayable =
        response.payable;

      state.movementCandidates =
        response.items
        || [];

      renderInvoiceDetail();

    } catch (error) {
      toast(
        error.message,
        true
      );
    }
  }

  function purchaseAccountOptions(
    selected = ""
  ) {
    const accounts =
      state.classification
        ?.accounts
      || [];

    return (
      `<option value="">
        Seleccionar cuenta PUC...
      </option>`
      +
      accounts
        .filter(
          account => {
            const type =
              String(
                account.type
                || ""
              ).toLowerCase();

            return (
              type !== "income"
              &&
              type !== "revenue"
            );
          }
        )
        .map(
          account => `
            <option
              value="${
                esc(
                  account.code
                )
              }"
              ${
                String(
                  account.code
                )
                ===
                String(selected)
                  ? "selected"
                  : ""
              }
            >
              ${
                esc(
                  account.code
                )
              }
              ·
              ${
                esc(
                  account.name
                )
              }
            </option>
          `
        )
        .join("")
    );
  }

  function renderInvoiceDetail() {
    const item =
      state.selectedPayable;

    const host =
      $("#invoiceDetail");

    const paid =
      Math.max(
        0,
        Number(
          item.amount_original
          || item.total_amount
          || 0
        )
        -
        Number(
          item.balance
          || 0
        )
      );

    host.className = "";

    host.innerHTML = `
      <div class="invoice-hero">

        <div>
          <span class="eyebrow">
            FACTURA SELECCIONADA
          </span>

          <h3>
            ${esc(
              item.supplier_name
            )}
          </h3>

          <p>
            RUT
            ${esc(
              item.supplier_rut
              || "—"
            )}
            · Folio
            ${esc(
              item.folio
              || "—"
            )}
          </p>
        </div>

        <div class="amount">
          ${money(
            item.balance
          )}
        </div>

      </div>


      <div class="invoice-facts">

        <div class="fact">
          <span>Total documento</span>
          <strong>
            ${money(
              item.total_amount
            )}
          </strong>
        </div>

        <div class="fact">
          <span>Pagado</span>
          <strong>
            ${money(paid)}
          </strong>
        </div>

        <div class="fact">
          <span>Saldo</span>
          <strong>
            ${money(
              item.balance
            )}
          </strong>
        </div>

        <div class="fact">
          <span>Vencimiento</span>
          <strong>
            ${fmtDate(
              item.due_date
            )}
          </strong>
        </div>

      </div>


      <label class="account-field">
        <span>
          Cuenta PUC / clasificación del gasto
        </span>

        <select id="invoicePuc">
          ${
            purchaseAccountOptions(
              item.cuenta_code
              || ""
            )
          }
        </select>
      </label>


      <div class="invoice-accounting-notice">
        <strong>UNA SOLA ACCIÓN</strong>
        <span>
          Al confirmar, el pago se concilia contra esta factura
          y la CxP queda imputada a la cuenta PUC seleccionada.
          No se crea un segundo gasto.
        </span>
      </div>

      <div class="candidate-title">
        PAGOS BANCARIOS CANDIDATOS · MISMO RUT/EMPRESA
      </div>


      <div id="movementCandidates">
        ${renderMovementCandidates()}
      </div>


      <div class="invoice-action-bar">

        <div class="invoice-action-numbers">

          <div>
            <span>
              Saldo factura
            </span>
            <strong>
              ${money(
                item.balance
              )}
            </strong>
          </div>

          <div>
            <span>
              Aplicado
            </span>
            <strong id="invoiceApplied">
              $0
            </strong>
          </div>

          <div>
            <span>
              Diferencia
            </span>
            <strong
              id="invoiceDifference"
              class="warning"
            >
              ${money(
                item.balance
              )}
            </strong>
          </div>

        </div>

        <button
          id="confirmInvoice"
          class="button primary"
          type="button"
          disabled
        >
          CONCILIAR + CLASIFICAR GASTO
        </button>

      </div>
    `;

    $$(
      "[data-movement-candidate]"
    ).forEach(
      card => {
        card.onclick =
          event => {
            if (
              event.target.closest(
                "input"
              )
            ) {
              return;
            }

            selectMovementCandidate(
              Number(
                card.dataset
                  .movementCandidate
              )
            );
          };
      }
    );

    $$(
      "[data-candidate-amount]"
    ).forEach(
      input => {
        input.oninput =
          updateInvoiceTotals;
      }
    );

    $("#confirmInvoice")
      .onclick =
      confirmInvoice;
  }

  function renderMovementCandidates() {
    if (
      !state.movementCandidates.length
    ) {
      return `
        <div class="empty-state">
          <strong>
            No encontramos pagos candidatos
          </strong>

          <span>
            La factura seguirá pendiente
            y podrás revisar la cartola después.
          </span>
        </div>
      `;
    }

    return state.movementCandidates
      .map(
        movement => {
          const score =
            Number(
              movement.score
              || 0
            );

          const cls =
            score >= 95
              ? "high"
              : (
                  score >= 75
                    ? "medium"
                    : ""
                );

          const selected =
            Number(
              state.selectedMovement
                ?.id_bank_movement
            )
            ===
            Number(
              movement.id_bank_movement
            );

          return `
            <div
              class="bank-candidate ${
                selected
                  ? "selected"
                  : ""
              }"
              data-movement-candidate="${
                Number(
                  movement.id_bank_movement
                )
              }"
            >

              <div
                class="score ${cls}"
              >
                ${score}%
              </div>

              <div class="candidate-main">

                <strong>
                  ${esc(
                    movement.description
                    ||
                    movement.reference
                    ||
                    "Movimiento bancario"
                  )}
                </strong>

                <span>
                  ${fmtDate(
                    movement.tx_date
                  )}
                  ·
                  ${esc(
                    movement.bank_name
                    || "Banco"
                  )}
                  ·
                  ${esc(
                    movement.bank_account
                    || "Cuenta"
                  )}
                </span>

                <div class="reason-row">
                  ${
                    (
                      movement.reasons
                      || []
                    )
                    .map(
                      reason => `
                        <span class="reason">
                          ${esc(reason)}
                        </span>
                      `
                    )
                    .join("")
                  }
                </div>

              </div>

              <input
                class="candidate-amount"
                type="text"
                inputmode="numeric"
                data-candidate-amount="${
                  Number(
                    movement.id_bank_movement
                  )
                }"
                value="${
                  money(
                    movement.suggested_amount
                  )
                }"
                ${
                  selected
                    ? ""
                    : "disabled"
                }
              >

            </div>
          `;
        }
      )
      .join("");
  }

  function selectMovementCandidate(
    movementId
  ) {
    state.selectedMovement =
      state.movementCandidates.find(
        item =>
          Number(
            item.id_bank_movement
          )
          === movementId
      )
      || null;

    renderInvoiceDetail();

    updateInvoiceTotals();
  }

  function selectedCandidateAmount() {
    if (
      !state.selectedMovement
    ) {
      return 0;
    }

    const input =
      $(
        `[data-candidate-amount="${state.selectedMovement.id_bank_movement}"]`
      );

    return num(
      input?.value
      || state.selectedMovement
        .suggested_amount
      || 0
    );
  }

  function updateInvoiceTotals() {
    const applied =
      selectedCandidateAmount();

    const invoiceBalance =
      Number(
        state.selectedPayable
          ?.balance
        || 0
      );

    const difference =
      invoiceBalance
      -
      applied;

    const appliedNode =
      $("#invoiceApplied");

    const diffNode =
      $("#invoiceDifference");

    if (appliedNode) {
      appliedNode.textContent =
        money(applied);
    }

    if (diffNode) {
      diffNode.textContent =
        money(difference);

      diffNode.classList.toggle(
        "good",
        difference === 0
      );

      diffNode.classList.toggle(
        "warning",
        difference !== 0
      );
    }

    const button =
      $("#confirmInvoice");

    if (button) {
      button.disabled =
        !state.selectedMovement
        ||
        applied <= 0
        ||
        applied >
        Number(
          state.selectedMovement
            .remaining_amount
          || 0
        )
        ||
        applied >
        invoiceBalance
        ||
        !$("#invoicePuc")
          ?.value;
    }
  }

  async function confirmInvoice() {
    if (
      !state.selectedPayable
      ||
      !state.selectedMovement
    ) {
      return;
    }

    const account =
      $("#invoicePuc")
        .value;

    const amount =
      selectedCandidateAmount();

    if (!account) {
      toast(
        "Selecciona la cuenta PUC.",
        true
      );
      return;
    }

    const accepted =
      window.confirm(
        "¿Confirmar conciliación?\n\n"
        +
        `Factura: ${
          state.selectedPayable.folio
        }\n`
        +
        `Aplicado: ${money(amount)}`
      );

    if (!accepted) {
      return;
    }

    try {
      const response =
        await api(
          `/api/finance/sii/reconciliation/movements/${state.selectedMovement.id_bank_movement}/resolve`,
          {
            method:
              "POST",

            headers: {
              "Content-Type":
                "application/json",
            },

            body:
              JSON.stringify({
                mode:
                  "INVOICE",

                allocations: [
                  {
                    payable_id:
                      Number(
                        state.selectedPayable
                          .payable_id
                      ),

                    amount:
                      amount,

                    account_code:
                      account,
                  },
                ],

                description:
                  "Conciliación desde Facturas CxP",
              }),
          }
        );

      toast(
        response.message
        ||
        "Factura conciliada y gasto clasificado."
      );

      state.selectedPayable =
        null;

      state.selectedMovement =
        null;

      $("#invoiceDetail")
        .className =
        "empty-state";

      $("#invoiceDetail")
        .innerHTML = `
          <div class="empty-icon">
            ✓
          </div>

          <strong>
            Conciliación registrada
          </strong>

          <span>
            Selecciona la siguiente factura.
          </span>
        `;

      await Promise.all([
        loadContext(),
        loadInvoices(),
        loadSummary(),
      ]);

    } catch (error) {
      toast(
        error.message,
        true
      );
    }
  }

  async function loadBankMovements() {
    const accountId =
      Number(
        $("#movementBankAccount")
          .value
        || 0
      );

    if (!accountId) {
      $("#movementList")
        .innerHTML = `
          <div class="empty-state">
            <strong>
              Selecciona una cuenta bancaria
            </strong>
            <span>
              Trabajaremos una cartola a la vez.
            </span>
          </div>
        `;

      return;
    }

    try {
      const response =
        await api(
          `/api/finance/sii/reconciliation/accounts/${accountId}/movements?limit=1000`
        );

      state.bankMovements =
        (
          response.items
          || []
        ).filter(
          movement =>
            samePeriod(
              movement.tx_date
            )
        );

      state.selectedMovement =
        null;

      renderBankMovements();

    } catch (error) {
      toast(
        error.message,
        true
      );
    }
  }

  function renderBankMovements() {
    $("#movementCount")
      .textContent =
      `${state.bankMovements.length} movimientos`;

    const host =
      $("#movementList");

    if (
      !state.bankMovements.length
    ) {
      host.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon">
            ✓
          </div>
          <strong>
            Cuenta resuelta
          </strong>
          <span>
            No quedan egresos pendientes
            en este período.
          </span>
        </div>
      `;
      return;
    }

    host.innerHTML =
      state.bankMovements
        .map(
          movement => `
            <button
              class="movement-row ${
                Number(
                  state.selectedMovement
                    ?.id_bank_movement
                )
                ===
                Number(
                  movement.id_bank_movement
                )
                  ? "active"
                  : ""
              }"
              data-resolve-movement="${
                Number(
                  movement.id_bank_movement
                )
              }"
            >

              <div class="movement-top">
                <span>
                  ${fmtDate(
                    movement.tx_date
                  )}
                </span>

                <strong class="money">
                  -${
                    money(
                      Math.abs(
                        Number(
                          movement.remaining_amount
                          || 0
                        )
                      )
                    )
                    .replace(
                      "-",
                      ""
                    )
                  }
                </strong>
              </div>

              <div class="description">
                ${esc(
                  movement.description
                  ||
                  movement.reference
                  ||
                  "Movimiento bancario"
                )}
              </div>

              <div class="meta">
                ${esc(
                  movement.bank_name
                  || "Banco"
                )}
                ·
                ${esc(
                  movement.label
                  || "Cuenta"
                )}
              </div>

            </button>
          `
        )
        .join("");

    $$(
      "[data-resolve-movement]"
    ).forEach(
      button => {
        button.onclick =
          () => openMovementResolution(
            Number(
              button.dataset
                .resolveMovement
            )
          );
      }
    );
  }

  function accountingHint(
    movement
  ) {
    const text =
      String(
        movement.description
        ||
        movement.reference
        ||
        ""
      ).toLowerCase();

    if (
      /sii\.cl|tesorer|tgr|iva|impuesto/.test(
        text
      )
    ) {
      return {
        mode:
          "OBLIGATION",

        text:
          "Probable pago tributario. "
          +
          "No lo registres como gasto "
          +
          "si la obligación ya fue devengada.",
      };
    }

    if (
      /previred|afp|fonasa|isapre|mutual|imposic/.test(
        text
      )
    ) {
      return {
        mode:
          "OBLIGATION",

        text:
          "Probable pago previsional. "
          +
          "Normalmente liquida una obligación.",
      };
    }

    if (
      /sueldo|remuner|quincena/.test(
        text
      )
    ) {
      return {
        mode:
          "OBLIGATION",

        text:
          "Probable remuneración. "
          +
          "Si ya fue devengada, "
          +
          "la cartola sólo paga la obligación.",
      };
    }

    if (
      /comision|cargo bancario|mantencion|interes/.test(
        text
      )
    ) {
      return {
        mode:
          "OPERATING_EXPENSE",

        text:
          "Probable gasto operacional o financiero.",
      };
    }

    if (/calvac|maquin|equipo|activo fijo/.test(text)) {
      return {
        mode: "ASSET_PURCHASE",
        text: "Probable compra de maquinaria o equipo. Regístrala como activo, no como COGS.",
      };
    }

    if (/food|alimento|gasco|gas don|combustible|insumo/.test(text)) {
      return {
        mode: "COGS",
        text: "Probable costo directo del evento. Si existe factura, concíliala primero.",
      };
    }

    return {
      mode:
        "INVOICE",

      text:
        "Primero revisa si este pago corresponde a una factura pendiente.",
    };
  }

  async function openMovementResolution(
    movementId
  ) {
    state.selectedMovement =
      state.bankMovements.find(
        item =>
          Number(
            item.id_bank_movement
          )
          === movementId
      )
      || null;

    renderBankMovements();

    await ensureMeta();

    if (!state.resolutionPayables.length) {
      try {
        const response = await api(
          "/api/finance/sii/reconciliation/open-payables"
          + `?legal_entity_id=${state.entityId}`
          + "&limit=1000"
        );
        state.resolutionPayables = response.items || [];
      } catch (_error) {
        state.resolutionPayables = [];
      }
    }

    const movement =
      state.selectedMovement;

    const hint =
      accountingHint(
        movement
      );

    $("#movementResolution")
      .className = "";

    $("#movementResolution")
      .innerHTML = `
        <div class="resolution-summary">

          <span class="eyebrow">
            MOVIMIENTO SELECCIONADO
          </span>

          <strong>
            ${esc(
              movement.description
              ||
              movement.reference
              ||
              "Movimiento"
            )}
          </strong>

          <span class="amount">
            ${money(
              Math.abs(
                Number(
                  movement.remaining_amount
                  || 0
                )
              )
            )}
          </span>

          <span>
            ${fmtDate(
              movement.tx_date
            )}
          </span>

        </div>


        <div class="accounting-warning">
          ${esc(hint.text)}
        </div>


        <div class="resolution-form">

          <label>
            <span>
              Naturaleza contable
            </span>

            <select id="resolutionMode">

              <option value="INVOICE">
                Pago de una factura / CxP
              </option>

              <option value="COGS">
                Costo directo del evento / COGS
              </option>

              <option value="OPERATING_EXPENSE">
                Gasto operacional, administrativo o fijo
              </option>

              <option value="ASSET_PURCHASE">
                Compra de maquinaria, equipos u otro activo
              </option>

              <option value="OBLIGATION">
                Pago de obligación / impuesto / nómina
              </option>

              <option value="TRANSFER">
                Transferencia / movimiento patrimonial
              </option>

            </select>
          </label>


          <div id="resolutionModeHelp" class="accounting-warning"></div>


          <label id="resolutionInvoiceField" hidden>
            <span>
              Factura pendiente
            </span>

            <select id="resolutionInvoice"></select>
          </label>


          <label>
            <span>
              Cuenta PUC / destino contable
            </span>

            <select id="resolutionAccount"></select>
          </label>


          <div id="resolutionAccountHelp" class="context-status"></div>


          <label>
            <span>
              Responsable
            </span>

            <select id="resolutionResponsible">
              ${responsibleOptions()}
            </select>
          </label>


          <label>
            <span>
              Descripción interna
            </span>

            <input
              id="resolutionDescription"
              placeholder="Ej: IVA agosto, comisión bancaria..."
            >
          </label>


          <button
            id="resolveMovement"
            class="button primary"
            type="button"
          >
            Resolver movimiento
          </button>

        </div>
      `;

    $("#resolutionMode")
      .value =
      hint.mode;

    refreshResolutionAccounts();

    $("#resolutionMode")
      .onchange =
      refreshResolutionAccounts;

    $("#resolveMovement")
      .onclick =
      submitMovementResolution;
  }

  function responsibleOptions() {
    const users =
      state.classification
        ?.responsibles
      || [];

    return (
      `<option value="">
        Seleccionar...
      </option>`
      +
      users.map(
        user => `
          <option
            value="${
              Number(
                user.id_usuario
              )
            }"
          >
            ${esc(
              user.nombre
              ||
              user.username
              ||
              "Usuario"
            )}
          </option>
        `
      ).join("")
    );
  }

  function accountTypeLabel(type) {
    const labels = {
      asset: "Activo (bien, equipo, banco o cuenta por cobrar)",
      liability: "Pasivo (deuda u obligación por pagar)",
      equity: "Patrimonio",
      expense: "Costo o gasto (afecta el P&L)",
      income: "Ingreso (afecta el P&L)",
      revenue: "Ingreso (afecta el P&L)",
      "contra-asset": "Contra-activo",
      other: "Otra cuenta patrimonial",
    };
    return labels[String(type || "").toLowerCase()] || String(type || "Sin tipo");
  }

  function accountGroupLabel(account) {
    const type = String(account.type || "").toLowerCase();
    const classification = String(account.classification || "").toUpperCase();
    if (classification === "COGS") return "Costo directo / COGS";
    if (type === "expense") return "Gasto operacional";
    if (type === "asset") return "Activo";
    if (type === "liability") return "Pasivo";
    if (type === "equity") return "Patrimonio";
    return accountTypeLabel(type);
  }

  function resolutionModeHelp(mode) {
    const messages = {
      INVOICE: "Úsalo cuando existe un DTE pendiente: el pago liquida la cuenta por pagar y evita duplicar el costo o gasto.",
      COGS: "Costo directamente atribuible al evento o producto, por ejemplo alimentos, gas o insumos consumidos.",
      OPERATING_EXPENSE: "Gasto de administración, estructura o funcionamiento que no pertenece a un evento específico.",
      ASSET_PURCHASE: "Maquinaria o equipos: se registran como activo y no como gasto completo. La depreciación afectará el P&L después.",
      OBLIGATION: "Pago de impuestos, nómina, crédito u otra deuda que ya estaba registrada como obligación.",
      TRANSFER: "Movimiento entre bancos, sociedades o cuentas patrimoniales. No genera ingreso, costo ni gasto.",
    };
    return messages[mode] || "Selecciona la naturaleza real del movimiento.";
  }

  function refreshResolutionAccounts() {
    const mode =
      $("#resolutionMode")
        .value;

    const accounts =
      state.classification
        ?.accounts
      || [];

    const filtered = accounts.filter(account => {
      const type = String(account.type || "").toLowerCase();
      const classification = String(account.classification || "").toUpperCase();

      if (mode === "COGS") return type === "expense" && classification === "COGS";
      if (mode === "OPERATING_EXPENSE" || mode === "DIRECT_EXPENSE") {
        return type === "expense" && classification !== "COGS";
      }
      if (mode === "ASSET_PURCHASE") return type === "asset";
      if (mode === "OBLIGATION") return type === "liability" || type === "equity";
      if (mode === "TRANSFER") {
        return !["expense", "income", "revenue"].includes(type);
      }
      if (mode === "INVOICE") {
        return ["expense", "asset"].includes(type);
      }
      return true;
    });

    const modeHelp = $("#resolutionModeHelp");
    if (modeHelp) modeHelp.textContent = resolutionModeHelp(mode);

    const invoiceField = $("#resolutionInvoiceField");
    if (invoiceField) invoiceField.hidden = mode !== "INVOICE";

    const invoiceSelect = $("#resolutionInvoice");
    if (invoiceSelect) {
      const amount = Math.abs(Number(state.selectedMovement?.remaining_amount || 0));
      const payables = [...state.resolutionPayables].sort((a, b) =>
        Math.abs(Number(a.balance || 0) - amount) - Math.abs(Number(b.balance || 0) - amount)
      );
      invoiceSelect.innerHTML = `<option value="">Seleccionar factura pendiente...</option>` +
        payables.map(item => `
          <option value="${Number(item.payable_id)}">
            ${esc(item.supplier_name || item.razon_social || "Proveedor")}
            · Folio ${esc(item.folio || item.document_number || "—")}
            · saldo ${money(item.balance || 0)}
          </option>
        `).join("");
    }

    $("#resolutionAccount")
      .innerHTML =
      `<option value="">
        Seleccionar cuenta...
      </option>`
      +
      filtered
        .map(
          account => `
            <option value="${
              esc(
                account.code
              )
            }">
              [${esc(accountGroupLabel(account))}]
              ${esc(account.code)} · ${esc(account.name)}
            </option>
          `
        )
        .join("");

    const accountSelect = $("#resolutionAccount");
    const accountHelp = $("#resolutionAccountHelp");
    const updateAccountHelp = () => {
      const selected = accounts.find(item => String(item.code) === accountSelect.value);
      accountHelp.textContent = selected
        ? `${accountTypeLabel(selected.type)}. Clasificación: ${selected.classification || "sin clasificación adicional"}.`
        : "La cuenta disponible cambia según la naturaleza contable seleccionada.";
    };
    accountSelect.onchange = updateAccountHelp;
    updateAccountHelp();
  }

  async function submitMovementResolution() {
    const movement =
      state.selectedMovement;

    if (!movement) {
      return;
    }

    const mode =
      $("#resolutionMode")
        .value;

    const account =
      $("#resolutionAccount")
        .value;

    const responsible =
      Number(
        $("#resolutionResponsible")
          .value
        || 0
      )
      || null;

    const description =
      $("#resolutionDescription")
        .value
      .trim();

    const payableId = Number($("#resolutionInvoice")?.value || 0) || null;

    if (!account) {
      toast(
        "Selecciona una cuenta PUC.",
        true
      );
      return;
    }

    if (
      ["DIRECT_EXPENSE", "COGS", "OPERATING_EXPENSE"].includes(mode)
      &&
      !responsible
    ) {
      toast(
        "Selecciona responsable.",
        true
      );
      return;
    }

    if (
      ["DIRECT_EXPENSE", "COGS", "OPERATING_EXPENSE", "ASSET_PURCHASE"].includes(mode)
      &&
      !description
    ) {
      toast(
        "Describe el gasto.",
        true
      );
      return;
    }

    if (mode === "INVOICE" && !payableId) {
      toast("Selecciona la factura pendiente que estás pagando.", true);
      return;
    }

    const selectedAccount = (state.classification?.accounts || [])
      .find(item => String(item.code) === account);
    const selectedInvoice = state.resolutionPayables
      .find(item => Number(item.payable_id) === payableId);
    const confirmation = mode === "INVOICE"
      ? `¿Conciliar ${money(Math.abs(movement.remaining_amount))} con la factura ${selectedInvoice?.folio || "seleccionada"} usando ${account}?`
      : `¿Clasificar ${money(Math.abs(movement.remaining_amount))} como ${resolutionModeHelp(mode)}\n\nCuenta: ${account} · ${selectedAccount?.name || ""}?`;

    if (
      !window.confirm(
        confirmation
      )
    ) {
      return;
    }

    try {
      const response =
        await api(
          `/api/finance/sii/reconciliation/movements/${movement.id_bank_movement}/resolve`,
          {
            method:
              "POST",

            headers: {
              "Content-Type":
                "application/json",
            },

            body:
              JSON.stringify({
                mode,
                allocations: mode === "INVOICE" ? [{
                  payable_id: payableId,
                  amount: Math.min(
                    Math.abs(Number(movement.remaining_amount || 0)),
                    Number(selectedInvoice?.balance || 0)
                  ),
                  account_code: account,
                }] : [],
                account_code: mode === "INVOICE" ? null : account,
                responsible_id:
                  responsible,
                description,
              }),
          }
        );

      toast(
        response.message
        ||
        "Movimiento resuelto."
      );

      $("#movementResolution")
        .className =
        "empty-state";

      $("#movementResolution")
        .innerHTML = `
          <div class="empty-icon">
            ✓
          </div>

          <strong>
            Movimiento resuelto
          </strong>

          <span>
            Selecciona el siguiente.
          </span>
        `;

      await Promise.all([
        loadContext(),
        loadBankMovements(),
        loadSummary(),
      ]);

    } catch (error) {
      toast(
        error.message,
        true
      );
    }
  }

  async function loadSuppliers() {
    const q =
      $("#supplierSearch")
        .value
        .trim();

    let url =
      "/api/finance/sii/suppliers"
      + "?page=1&page_size=200";

    if (q) {
      url +=
        "&q="
        + encodeURIComponent(q);
    }

    try {
      const response =
        await api(url);

      $("#supplierRows")
        .innerHTML =
        (
          response.items
          || []
        ).map(
          item => `
            <tr>
              <td>
                <strong>
                  ${esc(
                    item.razon_social
                    ||
                    item.nombre
                    ||
                    "Proveedor"
                  )}
                </strong>
              </td>

              <td>
                ${esc(
                  item.rut_normalized
                  || "—"
                )}
              </td>

              <td>
                ${fmtDate(
                  item.ultima_factura
                )}
              </td>

              <td class="money">
                ${money(
                  item.total_comprado
                )}
              </td>

              <td class="money">
                ${money(
                  item.saldo_pendiente
                )}
              </td>

              <td class="money">
                ${
                  NUMBER.format(
                    Number(
                      item.cantidad_documentos
                      || 0
                    )
                  )
                }
              </td>
            </tr>
          `
        ).join("")
        ||
        `
          <tr>
            <td colspan="6">
              Sin resultados.
            </td>
          </tr>
        `;

    } catch (error) {
      toast(
        error.message,
        true
      );
    }
  }

  function loadPnl() {
    const frame =
      $("#pnlFrame");

    if (
      !frame.src
      ||
      frame.src ===
      "about:blank"
    ) {
      frame.src =
        apiURL("/web/views/finanzas_simple.html?v=20260824-selector3#pnl");

      frame.onload =
        () => {
          try {
            const doc =
              frame.contentDocument;

            const button =
              doc.querySelector(
                '[data-tab="pnl"]'
              );

            button?.click();

            const h1 =
              doc.querySelector(
                "h1"
              );

            h1
              ?.closest(
                "header"
              )
              ?.style
              && (
                h1.closest(
                  "header"
                ).style.display =
                  "none"
              );

          } catch (_) {}
        };
    }
  }

  async function loadPuc() {
    try {
      const response =
        await api(
          "/api/finance/sii/chart-of-accounts"
        );

      state.puc =
        response.items
        || [];

      renderPucFilters();

      renderPuc();

    } catch (error) {
      toast(
        error.message,
        true
      );
    }
  }

  function renderPucFilters() {
    const types =
      Array.from(
        new Set(
          state.puc
            .map(
              item =>
                item.type
            )
            .filter(Boolean)
        )
      ).sort();

    const select =
      $("#pucTypeFilter");

    const current =
      select.value;

    select.innerHTML =
      `<option value="">
        Todos los tipos
      </option>`
      +
      types.map(
        type => `
          <option
            value="${esc(type)}"
          >
            ${esc(type)}
          </option>
        `
      ).join("");

    if (
      types.includes(
        current
      )
    ) {
      select.value =
        current;
    }
  }

  function renderPuc() {
    const q =
      $("#pucSearch")
        .value
        .trim()
        .toLowerCase();

    const type =
      $("#pucTypeFilter")
        .value;

    const map =
      new Map(
        state.puc.map(
          item => [
            String(
              item.code
            ),
            item,
          ]
        )
      );

    function depth(
      item
    ) {
      let d = 0;
      let current = item;
      const visited =
        new Set();

      while (
        current
        &&
        current.parent_code
        &&
        d < 10
      ) {
        if (
          visited.has(
            current.code
          )
        ) {
          break;
        }

        visited.add(
          current.code
        );

        current =
          map.get(
            String(
              current.parent_code
            )
          );

        d += 1;
      }

      return d;
    }

    const rows =
      state.puc
        .filter(
          item => {
            if (
              type
              &&
              String(
                item.type
              )
              !== type
            ) {
              return false;
            }

            if (!q) {
              return true;
            }

            return (
              String(
                [
                  item.code,
                  item.name,
                  item.classification,
                ].join(" ")
              )
              .toLowerCase()
              .includes(q)
            );
          }
        )
        .sort(
          (
            a,
            b
          ) =>
            String(a.code)
            .localeCompare(
              String(b.code),
              "es",
              {
                numeric: true,
              }
            )
        );

    $("#pucRows")
      .innerHTML =
      rows.map(
        item => `
          <tr>
            <td>
              <strong>
                ${esc(
                  item.code
                )}
              </strong>
            </td>

            <td
              style="
                padding-left:${
                  13
                  +
                  depth(item)
                  * 18
                }px
              "
            >
              ${
                depth(item)
                  ? "↳ "
                  : ""
              }
              ${esc(
                item.name
              )}
            </td>

            <td>
              ${esc(
                item.type
                || "—"
              )}
            </td>

            <td>
              ${esc(
                item.classification
                || "—"
              )}
            </td>

            <td>
              ${esc(
                item.parent_code
                || "—"
              )}
            </td>
          </tr>
        `
      ).join("");
  }

  function openPucDrawer() {
    const drawer =
      $("#pucDrawer");

    drawer.hidden =
      false;

    const sorted =
      [...state.puc]
      .sort(
        (
          a,
          b
        ) =>
          String(a.code)
          .localeCompare(
            String(b.code),
            "es",
            {
              numeric: true,
            }
          )
      );

    $("#pucParent")
      .innerHTML =
      `<option value="">
        Sin cuenta padre
      </option>`
      +
      sorted.map(
        item => `
          <option
            value="${esc(
              item.code
            )}"
          >
            ${esc(
              item.code
            )}
            ·
            ${esc(
              item.name
            )}
          </option>
        `
      ).join("");

    const types =
      Array.from(
        new Set(
          state.puc
            .map(
              item =>
                item.type
            )
            .filter(Boolean)
        )
      ).sort();

    $("#pucType")
      .innerHTML =
      types.map(
        type => `
          <option
            value="${esc(type)}"
          >
            ${esc(accountTypeLabel(type))}
          </option>
        `
      ).join("");

    const classes =
      Array.from(
        new Set(
          state.puc
            .map(
              item =>
                item.classification
            )
            .filter(Boolean)
        )
      ).sort();

    $("#pucClassificationList")
      .innerHTML =
      classes.map(
        value => `
          <option
            value="${esc(value)}"
          ></option>
        `
      ).join("");

    $("#pucCode").value = "";
    $("#pucName").value = "";
    $("#pucClassification").value = "";
    $("#pucDescription").value = "";

    syncParentAccount();

    $("#pucName")
      .focus();
  }

  async function syncParentAccount() {
    const code =
      $("#pucParent")
        .value;

    const parent =
      state.puc.find(
        item =>
          String(
            item.code
          )
          === code
      );

    if (parent) {
      $("#pucType")
        .value =
        String(
          parent.type
        );

      $("#pucClassification")
        .value =
        String(
          parent.classification
          || ""
        );
    }

    $("#pucType").disabled = Boolean(parent);
    const typeHelp = $("#pucTypeHelp");
    if (typeHelp) {
      typeHelp.textContent = parent
        ? `Hereda el tipo de la cuenta padre: ${accountTypeLabel(parent.type)}.`
        : `${accountTypeLabel($("#pucType").value)}. El tipo define si la cuenta afecta el P&L o el balance.`;
    }

    try {
      const params = new URLSearchParams({
        account_type: $("#pucType").value || "expense",
      });
      if (code) params.set("parent_code", code);
      const response = await api(
        "/api/finance/sii/chart-of-accounts/next-code?" + params
      );
      $("#pucCode").value = response.code || "";
    } catch (error) {
      $("#pucCode").value = "";
      toast(error.message, true);
    }

    updatePucPreview();
  }

  function updatePucPreview() {
    const parent =
      $("#pucParent")
        .value;

    const code =
      $("#pucCode")
        .value
        .trim()
      || "—";

    const name =
      $("#pucName")
        .value
        .trim()
      || "Nueva cuenta";

    $("#pucPreview")
      .textContent =
      parent
        ? `${parent} > ${code} · ${name}`
        : `${code} · ${name}`;
  }

  async function savePuc() {
    const code =
      $("#pucCode")
        .value
        .trim();

    const name =
      $("#pucName")
        .value
        .trim();

    if (
      !name
    ) {
      toast(
        "El nombre es obligatorio.",
        true
      );
      return;
    }

    const button =
      $("#savePuc");

    button.disabled =
      true;

    try {
      await api(
        "/api/finance/sii/chart-of-accounts",
        {
          method:
            "POST",

          headers: {
            "Content-Type":
              "application/json",
          },

          body:
            JSON.stringify({
              code,
              name,

              type:
                $("#pucType")
                  .value,

              classification:
                $("#pucClassification")
                  .value
                  .trim()
                || null,

              parent_code:
                $("#pucParent")
                  .value
                || null,

              description:
                $("#pucDescription")
                  .value
                  .trim()
                || null,
            }),
        }
      );

      toast(
        `Cuenta ${code} creada.`
      );

      $("#pucDrawer")
        .hidden =
        true;

      await loadPuc();

    } catch (error) {
      toast(
        error.message,
        true
      );
    } finally {
      button.disabled =
        false;
    }
  }

  async function loadConnections() {
    try {
      const response =
        await api(
          "/api/finance/sii/connections"
        );

      $("#connectionCards")
        .innerHTML =
        (
          response.items
          || []
        ).map(
          item => {
            const ok =
              String(
                item.status
                || ""
              )
              === "CONNECTED";

            return `
              <article class="connection-card">
                <strong>
                  ${esc(
                    item.name
                    || item.legal_name
                    || "Entidad"
                  )}
                </strong>

                <div>
                  ${esc(
                    item.rut_normalized
                    || item.rut
                    || ""
                  )}
                </div>

                <span
                  class="status-pill ${
                    ok
                      ? "green"
                      : "amber"
                  }"
                >
                  ${
                    ok
                      ? "CONECTADO"
                      : "REVISAR"
                  }
                </span>
              </article>
            `;
          }
        ).join("");

    } catch (error) {
      toast(
        error.message,
        true
      );
    }
  }

  async function importFile(
    kind
  ) {
    const input =
      kind === "rcv"
        ? $("#rcvFile")
        : $("#xmlFile");

    const file =
      input.files?.[0];

    if (!file) {
      toast(
        "Selecciona un archivo.",
        true
      );
      return;
    }

    if (!state.entityId) {
      toast(
        "Selecciona empresa/RUT.",
        true
      );
      return;
    }

    const form =
      new FormData();

    form.append(
      "legal_entity_id",
      String(
        state.entityId
      )
    );

    form.append(
      "file",
      file
    );

    const endpoint =
      kind === "rcv"
        ? "/api/finance/sii/import/rcv"
        : "/api/finance/sii/import/xml";

    try {
      await api(
        endpoint,
        {
          method:
            "POST",
          body:
            form,
        }
      );

      toast(
        kind === "rcv"
          ? "RCV importado correctamente."
          : "XML importado correctamente."
      );

      input.value = "";

      await Promise.all([
        loadInvoices(),
        loadSummary(),
      ]);

    } catch (error) {
      toast(
        error.message,
        true
      );
    }
  }

  async function refreshCurrent() {
    await loadContext();

    const active =
      $(".finance-nav button.active")
        ?.dataset.view
      || "summary";

    activateView(
      active
    );
  }

  async function init() {
    const today =
      new Date();

    $("#globalPeriod")
      .value =
      `${today.getFullYear()}-${String(
        today.getMonth() + 1
      ).padStart(2, "0")}`;

    state.period =
      $("#globalPeriod")
        .value;

    $$(".finance-nav button")
      .forEach(
        button => {
          button.onclick =
            () => activateView(
              button.dataset.view
            );
        }
      );

    window.addEventListener("hashchange", () => activateView(requestedView()));

    $("#globalEntity")
      .onchange =
      async event => {
        state.entityId =
          Number(
            event.target.value
            || 0
          );

        rememberEntityId(
          state.entityId
        );

        state.selectedPayable =
          null;

        state.selectedMovement =
          null;

        state.resolutionPayables =
          [];

        fillBankAccounts();

        updateContextStatus();

        await refreshCurrent();
      };

    $("#globalPeriod")
      .onchange =
      async event => {
        state.period =
          event.target.value;

        await refreshCurrent();
      };

    $("#refreshFinance")
      .onclick =
      refreshCurrent;

    $("#invoiceSearchButton")
      .onclick =
      loadInvoices;

    $("#invoiceSearch")
      .onkeydown =
      event => {
        if (
          event.key
          === "Enter"
        ) {
          loadInvoices();
        }
      };

    $("#movementBankAccount")
      .onchange =
      loadBankMovements;

    $("#uploadStatementPremium")
      .onclick =
      uploadBankStatement;

    $("#statementBankAccount")
      .onchange =
      event => {
        $("#movementBankAccount").value = event.target.value;
      };

    $("#supplierSearch")
      .oninput =
      () => {
        clearTimeout(
          loadSuppliers.timer
        );

        loadSuppliers.timer =
          setTimeout(
            loadSuppliers,
            300
          );
      };

    $("#pucSearch")
      .oninput =
      renderPuc;

    $("#pucTypeFilter")
      .onchange =
      renderPuc;

    $("#newAccount")
      .onclick =
      openPucDrawer;

    $("#closePucDrawer")
      .onclick =
      () => {
        $("#pucDrawer")
          .hidden =
          true;
      };

    $("#pucParent")
      .onchange =
      syncParentAccount;

    $("#pucType")
      .onchange =
      syncParentAccount;

    $("#pucCode")
      .oninput =
      updatePucPreview;

    $("#pucName")
      .oninput =
      updatePucPreview;

    $("#savePuc")
      .onclick =
      savePuc;

    $("#importRcv")
      .onclick =
      () => importFile(
        "rcv"
      );

    $("#importXml")
      .onclick =
      () => importFile(
        "xml"
      );

    if (isControlReadOnly()) {
      document.body.classList.add("finance-read-only");
      const note = $("#contextStatus");
      if (note) note.dataset.readOnly = "true";
    }

    try {
      await loadContext();
      await ensureMeta();
      activateView(requestedView());
      console.info("GD_FINANCE_R543_BOOTSTRAP=READY");
    } catch (error) {
      console.error("GD Finance bootstrap:", error);
      const status = $("#contextStatus");
      if (status) {
        status.textContent = "ERROR: " + (error.message || "No se pudo cargar Finanzas");
        status.style.color = "#ff8f95";
      }
      toast(error.message || "No se pudo cargar Finanzas.", true);
      activateView(requestedView());
    }
  }

  init().catch(
    error => {
      console.error(
        error
      );

      toast(
        error.message
        || "No fue posible iniciar Finanzas.",
        true
      );
    }
  );
})();
