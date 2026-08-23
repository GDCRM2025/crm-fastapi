(() => {
  "use strict";

  const BUILD = "GD-AGENDA-WIZARD-V6-20260731";
  const qs = (selector, root = document) => root.querySelector(selector);
  const qsa = (selector, root = document) => [...root.querySelectorAll(selector)];
  const emit = (element, type = "change") => {
    if (element) element.dispatchEvent(new Event(type, { bubbles: true }));
  };
  const text = (selector, root = document) => String(qs(selector, root)?.textContent || "").trim();
  const value = (selector, root = document) => String(qs(selector, root)?.value || "").trim();

  function injectStyle() {
    if (qs("#gdAgendaWizardV6Style")) return;
    const style = document.createElement("style");
    style.id = "gdAgendaWizardV6Style";
    style.textContent = `
      .gdAgendaWizardV4{width:min(1180px,calc(100vw - 18px))!important}
      .gdW6Tip{border-radius:14px;padding:11px 13px;border:1px solid;display:grid;grid-template-columns:auto 1fr;gap:10px;align-items:start;font-size:12px;line-height:1.48}
      .gdW6Tip b{font-size:16px;line-height:1}.gdW6Tip strong{display:block;margin-bottom:2px}
      .gdW6Tip.info{border-color:rgba(96,165,250,.42);background:rgba(96,165,250,.09)}
      .gdW6Tip.important{border-color:rgba(245,158,11,.52);background:rgba(245,158,11,.11)}
      .gdW6Tip.critical{border-color:rgba(239,68,68,.52);background:rgba(239,68,68,.10)}
      .gdW6Tip.success{border-color:rgba(25,195,125,.46);background:rgba(25,195,125,.09)}
      .gdW6ProductFlow{display:grid;gap:12px}
      .gdW6ProductBlock{border:1px solid var(--border,#22384f);border-radius:15px;padding:13px;background:rgba(0,0,0,.055);display:grid;gap:10px}
      .gdW6ProductBlock>header{display:flex;justify-content:space-between;gap:12px;align-items:center}
      .gdW6ProductBlock>header strong{font-size:14px}.gdW6ProductBlock>header span{font-size:10px;font-weight:950;padding:5px 8px;border-radius:999px;border:1px solid var(--border,#22384f);color:var(--muted,#a9bbd2)}
      .gdW6ProductBlock #ag_products_wrap,.gdW6ProductBlock #ag_products_allocator{margin:0!important;border:0!important;background:transparent!important;padding:0!important}
      .gdW6ProductBlock #ag_products_allocator{display:grid!important;gap:9px!important}
      .gdW6Status{border-radius:12px;padding:10px 12px;border:1px solid var(--border,#22384f);font-size:12px;font-weight:900}
      .gdW6Status.loading{border-color:rgba(96,165,250,.42);background:rgba(96,165,250,.09)}
      .gdW6Status.ok{border-color:rgba(25,195,125,.48);background:rgba(25,195,125,.10);color:#58dca7}
      .gdW6Status.warn{border-color:rgba(245,158,11,.52);background:rgba(245,158,11,.10);color:#fbbf24}
      .gdW6Status.error{border-color:rgba(239,68,68,.52);background:rgba(239,68,68,.10);color:#fca5a5}
      .gdW6ModeSummary{border:1px solid rgba(96,165,250,.36);background:rgba(96,165,250,.075);border-radius:13px;padding:10px 12px;font-size:12px;line-height:1.5}
      .gdW6DataCard{order:-3}.gdW6DataCard:before{content:"Datos de contacto, pago y ubicación";font-weight:950;font-size:14px}
      .gdW6Independent{border-color:rgba(96,165,250,.42)!important;background:linear-gradient(180deg,rgba(96,165,250,.065),rgba(0,0,0,.045))!important}
      .gdW6DayHeader{display:flex;justify-content:space-between;gap:10px;align-items:center;padding:8px 10px;border-radius:11px;border:1px solid rgba(96,165,250,.30);background:rgba(96,165,250,.075);font-size:11px;font-weight:950;margin-bottom:4px}
      .gdW6DayHeader span:last-child{color:var(--muted,#a9bbd2);font-weight:800}
      .gdW6Hidden{display:none!important}
      .gdW6StepReady{box-shadow:0 0 0 3px rgba(25,195,125,.08)}
      .gdW6LoadingNote{display:none}.gdW6LoadingNote.show{display:grid}
      .gdW4Panel[data-panel="1"] #gdW4Duration,.gdW4Panel[data-panel="1"] #gdW4ModeHint,.gdW4Panel[data-panel="1"] .gdW4Sync{display:none!important}
      .gdW4Panel[data-panel="2"]{align-content:start}
      @media(max-width:820px){.gdW6ProductBlock>header{align-items:flex-start;flex-direction:column}.gdW6Tip{grid-template-columns:1fr}.gdW6Tip b{display:none}}
    `;
    document.head.appendChild(style);
  }

  function mode(popup) {
    return value("#ag_modality", popup) || "simple";
  }

  function createTip(kind, icon, title, body, id = "") {
    const div = document.createElement("div");
    if (id) div.id = id;
    div.className = `gdW6Tip ${kind}`;
    div.innerHTML = `<b>${icon}</b><div><strong>${title}</strong><span>${body}</span></div>`;
    return div;
  }

  function closestField(input) {
    return input?.closest("label,.agLabel,.agField,.agRow") || input;
  }

  function moveProductsToStepTwo(popup) {
    const panelProducts = qs('.gdW4Panel[data-panel="1"]', popup);
    const panelOperation = qs('.gdW4Panel[data-panel="2"]', popup);
    if (!panelProducts || !panelOperation || qs("#gdW6ProductFlow", popup)) return;

    const dataCard = qsa(":scope > .gdW4Card", panelProducts).find((card) => qs("#ag_quote", card) || qs("#ag_tel", card));
    const quoteField = closestField(qs("#ag_quote", popup));
    const productsWrap = qs("#ag_products_wrap", popup);
    const allocator = qs("#ag_products_allocator", popup);
    const calcCard = qs("#gdW4Calc", popup);
    const distCard = qs("#gdW4Dist", popup);
    const range = qs("#gdW4Range", popup);

    const flow = document.createElement("div");
    flow.id = "gdW6ProductFlow";
    flow.className = "gdW6ProductFlow";

    const quoteBlock = document.createElement("section");
    quoteBlock.className = "gdW6ProductBlock";
    quoteBlock.innerHTML = '<header><strong>1. Selecciona la cotización</strong><span>OBLIGATORIO</span></header>';
    if (quoteField) quoteBlock.append(quoteField);

    const productBlock = document.createElement("section");
    productBlock.className = "gdW6ProductBlock";
    productBlock.innerHTML = '<header><strong>2. Revisa y distribuye los productos</strong><span>DEBE CUADRAR AL 100%</span></header>';
    if (productsWrap) productBlock.append(productsWrap);
    if (allocator) productBlock.append(allocator);

    const statusBlock = document.createElement("div");
    statusBlock.id = "gdW6ProductStatus";
    statusBlock.className = "gdW6Status loading";
    statusBlock.textContent = "Cargando productos de la cotización…";

    flow.append(
      createTip("important", "!", "Primero define los productos", "Antes de ingresar horarios o direcciones, confirma qué productos se utilizarán y cómo se reparten entre días o bloques."),
      quoteBlock,
      productBlock,
      statusBlock,
      createTip("info", "i", "Cómo funciona la distribución", "En varios días, cada producto se asigna al día correspondiente. En bloques, los productos pertenecen al mismo evento y pueden distribuirse por bloque.", "gdW6ModeTip")
    );

    const hero = qs(".gdW4Hero", panelProducts);
    if (hero?.nextSibling) panelProducts.insertBefore(flow, hero.nextSibling);
    else panelProducts.append(flow);

    if (calcCard) {
      calcCard.classList.add("gdW6Hidden");
      panelProducts.append(calcCard);
    }

    if (dataCard) {
      dataCard.classList.add("gdW6DataCard");
      if (range) panelOperation.insertBefore(dataCard, range);
      else panelOperation.prepend(dataCard);
    }

    if (distCard) {
      const empty = ![...distCard.children].some((child) => child.id !== "ag_products_wrap" && child.id !== "ag_products_allocator");
      distCard.classList.toggle("gdW6Hidden", empty);
    }
  }

  function updateLabels(popup) {
    const stepLabels = [
      "Tipo de evento",
      "Cotización y productos",
      "Horarios, direcciones y operación",
      "Confirmar y agendar",
    ];
    qsa(".gdW4Step span", popup).forEach((span, index) => {
      if (stepLabels[index]) span.textContent = stepLabels[index];
    });

    const panels = qsa(".gdW4Panel", popup);
    const copy = [
      ["Define el tipo de agendamiento", "Selecciona la estructura correcta. Esta decisión determina cuántos eventos se crearán en Calendar."],
      ["Selecciona la cotización y sus productos", "Este paso debe quedar resuelto antes de ingresar horarios, comunas y direcciones."],
      ["Completa la operación de cada evento", "Ingresa horarios, dirección, comuna, montaje y operadores. En varios días, completa cada día por separado."],
      ["Revisa antes de confirmar", "Verifica la información final. Solo al confirmar se cambiará el estado y se crearán los eventos en Calendar."],
    ];
    panels.forEach((panel, index) => {
      const hero = qs(".gdW4Hero", panel);
      const strong = qs("strong", hero || panel);
      const span = qs("span", hero || panel);
      if (strong && copy[index]) strong.textContent = copy[index][0];
      if (span && copy[index]) span.textContent = copy[index][1];
    });
  }

  function addStepTips(popup) {
    const panelType = qs('.gdW4Panel[data-panel="0"]', popup);
    const panelOperation = qs('.gdW4Panel[data-panel="2"]', popup);
    const panelReview = qs('.gdW4Panel[data-panel="3"]', popup);

    if (panelType && !qs("#gdW6TypeTip", popup)) {
      const tip = createTip("info", "i", "Elige según cómo aparecerá en Calendar", "Varios días crea un evento por día. Por bloques crea un solo evento desde el inicio del primer bloque hasta el término del último.", "gdW6TypeTip");
      qs(".gdW4Hero", panelType)?.after(tip);
    }
    if (panelOperation && !qs("#gdW6OperationTip", popup)) {
      const tip = createTip("important", "!", "Completa cada tarjeta de arriba hacia abajo", "Primero horario; luego comuna y dirección; finalmente montaje y operadores. Los campos con alerta se marcarán sin borrar el avance.", "gdW6OperationTip");
      qs(".gdW4Hero", panelOperation)?.after(tip);
    }
    if (panelReview && !qs("#gdW6ReviewTip", popup)) {
      const tip = createTip("success", "✓", "Último control", "Confirma que productos, fechas, horarios, direcciones, montaje y operadores coincidan con lo acordado con el cliente.", "gdW6ReviewTip");
      qs(".gdW4Hero", panelReview)?.after(tip);
    }
  }

  function updateModeCopy(popup) {
    const current = mode(popup);
    const tip = qs("#gdW6ModeTip", popup);
    const locToggle = qs("#gdW4Locs", popup);
    const locCard = locToggle?.closest(".gdW4Switch");
    const legacyLoc = qs("#ag_flag_multiloc", popup);

    if (tip) {
      const body = qs("span", tip);
      if (body) {
        body.textContent = current === "multiday"
          ? "Cada día es un evento independiente: productos, fecha, horario, comuna, dirección, montaje y operadores propios. Todos usan una sola cotización."
          : current === "blocks"
            ? "Los bloques pertenecen a un único evento. Calendar usará el inicio del primer bloque y el término del último bloque."
            : "Evento único: una fecha, un horario, una dirección y una sola operación.";
      }
    }

    const multiDay = current === "multiday";
    if (locCard) locCard.classList.toggle("gdW6Hidden", multiDay);
    if (multiDay) {
      if (locToggle?.checked) locToggle.checked = false;
      if (legacyLoc?.checked) {
        legacyLoc.checked = false;
        emit(legacyLoc, "change");
      }
    }
  }

  function syncLeadFromFirstDay(popup) {
    if (mode(popup) !== "multiday") return;
    const first = qs("#ag_group_editor [data-g-idx='0']", popup) || qs("#ag_group_editor [data-g-idx]", popup);
    if (!first) return;
    const pairs = [
      [qs("[data-f='direccion']", first), qs("#ag_dir", popup)],
      [qs("[data-f='comuna']", first), qs("#ag_loc", popup)],
    ];
    pairs.forEach(([source, target]) => {
      const next = String(source?.value || "").trim();
      if (!source || !target || !next || String(target.value || "").trim() === next) return;
      target.value = next;
      emit(target, "input");
    });
  }

  function decorateDayCards(popup) {
    const current = mode(popup);
    const multiDay = current === "multiday";
    qsa("#ag_group_editor [data-g-idx]", popup).forEach((card, index) => {
      card.classList.toggle("gdW6Independent", multiDay);
      let header = qs(".gdW6DayHeader", card);
      if (multiDay && !header) {
        header = document.createElement("div");
        header.className = "gdW6DayHeader";
        card.prepend(header);
      }
      if (header) {
        header.innerHTML = multiDay
          ? `<span>Día ${index + 1} · Evento independiente</span><span>Productos, horario y dirección propios</span>`
          : "";
        header.classList.toggle("gdW6Hidden", !multiDay);
      }
    });
    syncLeadFromFirstDay(popup);
  }

  function updateProductStatus(popup) {
    const box = qs("#gdW6ProductStatus", popup);
    if (!box) return;
    const calc = text("#ag_calc_status", popup);
    const allocation = text("#ag_alloc_status", popup);
    const quote = value("#ag_quote", popup);
    const combined = [allocation, calc].filter(Boolean).join(" · ");

    box.className = "gdW6Status";
    if (!quote) {
      box.classList.add("warn");
      box.textContent = "Selecciona una cotización para cargar los productos.";
    } else if (/error|inválid|inval|no hay/i.test(combined)) {
      box.classList.add("error");
      box.textContent = combined || "No fue posible cargar o distribuir los productos.";
    } else if (/^OK:/i.test(allocation) || (/^Listo$/i.test(calc) && allocation)) {
      box.classList.add("ok");
      box.textContent = allocation || "Productos cargados correctamente.";
    } else {
      box.classList.add("loading");
      box.textContent = combined || "Cargando productos de la cotización…";
    }
  }

  function attachTargetedListeners(popup) {
    qs("#ag_modality", popup)?.addEventListener("change", () => {
      updateModeCopy(popup);
      window.setTimeout(() => {
        decorateDayCards(popup);
        updateProductStatus(popup);
      }, 0);
    });
    qs("#ag_quote", popup)?.addEventListener("change", () => updateProductStatus(popup));

    const groupEditor = qs("#ag_group_editor", popup);
    groupEditor?.addEventListener("input", (event) => {
      if (mode(popup) !== "multiday") return;
      const card = event.target?.closest?.("[data-g-idx]");
      if (!card || card !== qs("#ag_group_editor [data-g-idx]", popup)) return;
      const field = String(event.target?.getAttribute?.("data-f") || "");
      if (field === "direccion" || field === "comuna") syncLeadFromFirstDay(popup);
    }, true);

    if (groupEditor) {
      const groupObserver = new MutationObserver(() => decorateDayCards(popup));
      groupObserver.observe(groupEditor, { childList: true });
    }

    [qs("#ag_alloc_status", popup), qs("#ag_calc_status", popup)].filter(Boolean).forEach((node) => {
      const observer = new MutationObserver(() => updateProductStatus(popup));
      observer.observe(node, { childList: true, subtree: true, characterData: true });
    });
  }

  function attach(popup) {
    if (!popup || popup.dataset.gdAgendaWizardV6 === "1") return;
    popup.dataset.gdAgendaWizardV6 = "1";
    popup.dataset.gdAgendaWizardBuild = BUILD;
    injectStyle();
    updateLabels(popup);
    addStepTips(popup);
    moveProductsToStepTwo(popup);
    updateModeCopy(popup);
    decorateDayCards(popup);
    updateProductStatus(popup);
    attachTargetedListeners(popup);
  }

  function waitForUpgrade(popup, attempt = 0) {
    if (!popup || !document.contains(popup)) return;
    if (popup.classList.contains("gdAgendaWizardV4")) {
      attach(popup);
      return;
    }
    if (attempt < 80) window.setTimeout(() => waitForUpgrade(popup, attempt + 1), 25);
  }

  function inspectAdded(node) {
    if (!(node instanceof Element)) return;
    if (node.matches(".swal2-popup")) waitForUpgrade(node);
    qsa(".swal2-popup", node).forEach((popup) => waitForUpgrade(popup));
  }

  function start() {
    injectStyle();
    qsa(".swal2-popup").forEach((popup) => waitForUpgrade(popup));
    const observer = new MutationObserver((mutations) => {
      mutations.forEach((mutation) => mutation.addedNodes.forEach(inspectAdded));
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();

  window.__GD_AGENDA_WIZARD_V6_BUILD__ = BUILD;
})();
