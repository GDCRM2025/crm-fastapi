(() => {
  "use strict";

  const BUILD = "GD-AGENDA-WIZARD-V5-20260730";
  const qs = (selector, root = document) => root.querySelector(selector);
  const qsa = (selector, root = document) => [...root.querySelectorAll(selector)];
  const emit = (element, type = "change") => {
    if (element) element.dispatchEvent(new Event(type, { bubbles: true }));
  };

  function injectStyle() {
    if (qs("#gdAgendaWizardV5Style")) return;
    const style = document.createElement("style");
    style.id = "gdAgendaWizardV5Style";
    style.textContent = `
      .gdW5IndependentNotice{
        display:none;
        border:1px solid rgba(96,165,250,.36);
        background:rgba(96,165,250,.09);
        border-radius:14px;
        padding:11px 13px;
        font-size:12px;
        line-height:1.5;
      }
      .gdW5IndependentNotice.show{display:block}
      .gdW5IndependentNotice strong{display:block;margin-bottom:3px;color:var(--text,#eaf2ff)}
      .gdW5DayCard{
        border-color:rgba(96,165,250,.42)!important;
        background:linear-gradient(180deg,rgba(96,165,250,.06),rgba(0,0,0,.055))!important;
      }
      .gdW5DayBadge{
        display:flex;
        justify-content:space-between;
        align-items:center;
        gap:10px;
        margin-bottom:2px;
        padding:8px 10px;
        border-radius:11px;
        border:1px solid rgba(96,165,250,.28);
        background:rgba(96,165,250,.07);
        font-size:11px;
        font-weight:900;
      }
      .gdW5DayBadge span:last-child{color:var(--muted,#a9bbd2);font-weight:800}
      .gdW5AddressRequired .muted:first-child::after{
        content:" · propio de este día";
        color:#60a5fa;
        font-size:10px;
        font-weight:900;
      }
      .gdW5LeadBaseHint{
        display:block;
        margin-top:5px;
        color:var(--muted,#a9bbd2);
        font-size:10px;
        line-height:1.35;
      }
      .gdW5Hidden{display:none!important}
    `;
    document.head.appendChild(style);
  }

  function mode(popup) {
    return String(qs("#ag_modality", popup)?.value || "simple").trim();
  }

  function ensureNotice(popup, panelSelector, id, title, text) {
    const panel = qs(panelSelector, popup);
    if (!panel) return null;
    let notice = qs(`#${id}`, popup);
    if (!notice) {
      notice = document.createElement("div");
      notice.id = id;
      notice.className = "gdW5IndependentNotice";
      notice.innerHTML = `<strong>${title}</strong><span>${text}</span>`;
      const hero = qs(".gdW4Hero", panel);
      if (hero && hero.nextSibling) panel.insertBefore(notice, hero.nextSibling);
      else panel.prepend(notice);
    }
    return notice;
  }

  function setLabelHint(input, title, hintText) {
    const label = input?.closest("label");
    if (!label) return;
    const heading = qs(":scope > .muted", label) || qs(".muted", label);
    if (heading && !heading.dataset.gdW5Original) {
      heading.dataset.gdW5Original = heading.textContent || "";
    }
    if (heading) heading.textContent = title;
    let hint = qs(".gdW5LeadBaseHint", label);
    if (!hint) {
      hint = document.createElement("small");
      hint.className = "gdW5LeadBaseHint";
      label.append(hint);
    }
    hint.textContent = hintText;
  }

  function restoreLabel(input) {
    const label = input?.closest("label");
    const heading = label ? (qs(":scope > .muted", label) || qs(".muted", label)) : null;
    if (heading?.dataset.gdW5Original) heading.textContent = heading.dataset.gdW5Original;
    qs(".gdW5LeadBaseHint", label || document)?.remove();
  }

  function syncLeadBaseFromFirstDay(popup) {
    if (mode(popup) !== "multiday") return;
    const first = qs("#ag_group_editor [data-g-idx='0']", popup)
      || qs("#ag_group_editor [data-g-idx]", popup);
    if (!first) return;

    const dayAddress = qs("[data-f='direccion']", first);
    const dayComuna = qs("[data-f='comuna']", first);
    const leadAddress = qs("#ag_dir", popup);
    const leadLocation = qs("#ag_loc", popup);

    if (dayAddress && leadAddress && String(dayAddress.value || "").trim()) {
      const next = String(dayAddress.value || "").trim();
      if (String(leadAddress.value || "").trim() !== next) {
        leadAddress.value = next;
        emit(leadAddress, "input");
      }
    }

    if (dayComuna && leadLocation && String(dayComuna.value || "").trim()) {
      const next = String(dayComuna.value || "").trim();
      if (String(leadLocation.value || "").trim() !== next) {
        leadLocation.value = next;
        emit(leadLocation, "input");
      }
    }
  }

  function decorateDayCards(popup) {
    const isMultiDay = mode(popup) === "multiday";
    const cards = qsa("#ag_group_editor [data-g-idx]", popup);

    cards.forEach((card, index) => {
      card.classList.toggle("gdW5DayCard", isMultiDay);
      const address = qs("[data-f='direccion']", card);
      const comuna = qs("[data-f='comuna']", card);
      address?.closest("label")?.classList.toggle("gdW5AddressRequired", isMultiDay);
      comuna?.closest("label")?.classList.toggle("gdW5AddressRequired", isMultiDay);

      let badge = qs(".gdW5DayBadge", card);
      if (isMultiDay && !badge) {
        badge = document.createElement("div");
        badge.className = "gdW5DayBadge";
        badge.innerHTML = `<span>Día ${index + 1} · Evento independiente</span><span>Horario y dirección propios</span>`;
        card.prepend(badge);
      } else if (isMultiDay && badge) {
        badge.innerHTML = `<span>Día ${index + 1} · Evento independiente</span><span>Horario y dirección propios</span>`;
      } else if (!isMultiDay) {
        badge?.remove();
      }
    });

    if (isMultiDay) syncLeadBaseFromFirstDay(popup);
  }

  function disableExtraLocationsForMultiDay(popup) {
    const isMultiDay = mode(popup) === "multiday";
    const visibleToggle = qs("#gdW4Locs", popup);
    const legacyToggle = qs("#ag_flag_multiloc", popup);
    const card = visibleToggle?.closest(".gdW4Switch");

    if (card) {
      card.classList.toggle("gdW5Hidden", isMultiDay);
      if (!isMultiDay) {
        const strong = qs("strong", card);
        const small = qs("small", card);
        if (strong) strong.textContent = "Varias locaciones";
        if (small) small.textContent = "Usar más de una dirección dentro de un evento único o por bloques.";
      }
    }

    if (isMultiDay) {
      let changed = false;
      if (visibleToggle?.checked) {
        visibleToggle.checked = false;
        changed = true;
      }
      if (legacyToggle?.checked) {
        legacyToggle.checked = false;
        changed = true;
      }
      if (changed && legacyToggle) emit(legacyToggle, "change");
    }
  }

  function syncCopy(popup) {
    const isMultiDay = mode(popup) === "multiday";
    const hint = qs("#gdW4ModeHint", popup);
    const stepOneMode = qs(".gdW4Mode[data-mode='multiday'] small", popup);
    const stepTwoNotice = ensureNotice(
      popup,
      ".gdW4Panel[data-panel='1']",
      "gdW5StepTwoNotice",
      "La dirección del lead será la del primer día",
      "Cada día puede tener una comuna y dirección distinta. En el paso 3 se preguntará una sola vez el horario y la dirección de cada día."
    );
    const stepThreeNotice = ensureNotice(
      popup,
      ".gdW4Panel[data-panel='2']",
      "gdW5StepThreeNotice",
      "Cada día se agenda como un evento independiente",
      "Completa fecha, horario, comuna, dirección, productos, montaje y operadores para cada día. Todos los eventos usan la misma cotización."
    );

    stepTwoNotice?.classList.toggle("show", isMultiDay);
    stepThreeNotice?.classList.toggle("show", isMultiDay);

    if (stepOneMode) {
      stepOneMode.textContent = "Un evento independiente por día, con horario y dirección propios, usando una sola cotización.";
    }

    if (isMultiDay && hint) {
      hint.classList.add("show");
      hint.innerHTML = "<strong>Configuración por día:</strong> cada día tendrá fecha, horario, comuna y dirección propios. Al confirmar se crearán tantos eventos en Calendar como días configurados.";
    }

    const address = qs("#ag_dir", popup);
    const location = qs("#ag_loc", popup);
    if (isMultiDay) {
      setLabelHint(address, "Dirección principal del lead", "Se sincroniza automáticamente con la dirección del Día 1.");
      setLabelHint(location, "Comuna principal del lead", "Se sincroniza automáticamente con la comuna del Día 1.");
    } else {
      restoreLabel(address);
      restoreLabel(location);
    }
  }

  function sync(popup) {
    if (!popup || !popup.classList.contains("gdAgendaWizardV4")) return;
    popup.dataset.gdAgendaWizardV5 = "1";
    popup.dataset.gdAgendaWizardBuild = BUILD;
    disableExtraLocationsForMultiDay(popup);
    syncCopy(popup);
    decorateDayCards(popup);
  }

  function attach(popup) {
    if (!popup || popup.dataset.gdAgendaWizardV5Attached === "1") return;
    popup.dataset.gdAgendaWizardV5Attached = "1";

    const modality = qs("#ag_modality", popup);
    modality?.addEventListener("change", () => window.setTimeout(() => sync(popup), 80));

    const groupEditor = qs("#ag_group_editor", popup);
    groupEditor?.addEventListener("input", (event) => {
      if (mode(popup) !== "multiday") return;
      const card = event.target?.closest?.("[data-g-idx]");
      if (!card || card !== qs("#ag_group_editor [data-g-idx]", popup)) return;
      const field = String(event.target?.getAttribute?.("data-f") || "");
      if (field === "direccion" || field === "comuna") {
        window.setTimeout(() => syncLeadBaseFromFirstDay(popup), 0);
      }
    }, true);
    groupEditor?.addEventListener("change", () => window.setTimeout(() => sync(popup), 30), true);

    const observer = new MutationObserver(() => window.setTimeout(() => sync(popup), 0));
    observer.observe(popup, { childList: true, subtree: true });

    popup.addEventListener("swal2:close", () => observer.disconnect(), { once: true });
    sync(popup);
  }

  function scan() {
    injectStyle();
    qsa(".swal2-popup.gdAgendaWizardV4").forEach(attach);
  }

  const pageObserver = new MutationObserver(() => window.setTimeout(scan, 0));
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      pageObserver.observe(document.body, { childList: true, subtree: true });
      scan();
    });
  } else {
    pageObserver.observe(document.body, { childList: true, subtree: true });
    scan();
  }

  window.__GD_AGENDA_WIZARD_V5_BUILD__ = BUILD;
})();
