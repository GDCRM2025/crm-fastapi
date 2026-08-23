(() => {
  "use strict";

  const BUILD = "GD-AGENDA-MULTIDAY-V19-20260731";
  const qs = (selector, root = document) => root?.querySelector?.(selector) || null;
  const qsa = (selector, root = document) => [...(root?.querySelectorAll?.(selector) || [])];
  const value = (selector, root = document) => String(qs(selector, root)?.value || "").trim();
  let lastCalendarSuccess = null;
  let successShownFor = "";

  function injectStyle() {
    if (qs("#gdAgendaMultiV19Style")) return;
    const style = document.createElement("style");
    style.id = "gdAgendaMultiV19Style";
    style.textContent = `
      .gdV19DayStatus{margin-top:10px;padding:10px 12px;border-radius:11px;border:1px solid;font-size:12px;font-weight:900;line-height:1.45}
      .gdV19DayStatus.ok{border-color:rgba(25,195,125,.48);background:rgba(25,195,125,.10);color:#58dca7}
      .gdV19DayStatus.warn{border-color:rgba(245,158,11,.52);background:rgba(245,158,11,.10);color:#fbbf24}
      .gdV19DayStatus.error{border-color:rgba(239,68,68,.62);background:rgba(239,68,68,.12);color:#fca5a5}
      #ag_group_editor [data-g-idx].gdV19Missing{border-color:rgba(239,68,68,.82)!important;box-shadow:0 0 0 3px rgba(239,68,68,.12)!important}
      .gdV19TopAlert{display:block!important;border-color:rgba(239,68,68,.60)!important;background:rgba(239,68,68,.11)!important}
    `;
    document.head.appendChild(style);
  }

  function isMultiDay(popup) {
    return value("#ag_modality", popup) === "multiday";
  }

  function cardInfo(card, index) {
    const day = value("[data-f='day']", card);
    const comuna = value("[data-f='comuna']", card);
    const direccion = value("[data-f='direccion']", card);
    const start = value("[data-f='start_time']", card);
    const end = value("[data-f='end_time']", card);
    const ops = Number(value("[data-f='ops']", card) || 0);
    const products = String(qs("[data-f='plist']", card)?.textContent || "").trim();
    const missing = [];
    const warnings = [];

    if (!day) missing.push("fecha");
    if (!comuna) missing.push("comuna");
    if (!Number.isFinite(ops) || ops < 1) missing.push("OPS mínimo 1");
    if (!products || products === "—" || /sin productos/i.test(products)) missing.push("productos");
    if (!start || !end) warnings.push("HR TBD");
    if (!direccion) warnings.push("DIR TBD");

    return { card, index, day, comuna, direccion, start, end, ops, products, missing, warnings };
  }

  function ensureStatus(card) {
    let status = qs(".gdV19DayStatus", card);
    if (!status) {
      status = document.createElement("div");
      status.className = "gdV19DayStatus";
      card.append(status);
    }
    return status;
  }

  function renderDayStatuses(popup) {
    if (!popup || !isMultiDay(popup)) return [];
    const results = qsa("#ag_group_editor [data-g-idx]", popup).map(cardInfo);
    results.forEach((result) => {
      const status = ensureStatus(result.card);
      result.card.classList.toggle("gdV19Missing", result.missing.length > 0);
      if (result.missing.length) {
        status.className = "gdV19DayStatus error";
        status.textContent = `Día ${result.index + 1}: falta ${result.missing.join(", ")}.`;
      } else if (result.warnings.length) {
        status.className = "gdV19DayStatus warn";
        status.textContent = `Día ${result.index + 1} listo para agendar. ${result.warnings.join(" · ")}.`;
      } else {
        status.className = "gdV19DayStatus ok";
        status.textContent = `Día ${result.index + 1} listo para agendar.`;
      }
    });
    return results;
  }

  function normalizeOptionalFields(popup) {
    if (!popup || !isMultiDay(popup)) return;
    qsa("#ag_group_editor [data-g-idx]", popup).forEach((card) => {
      const direction = qs("[data-f='direccion']", card);
      const start = qs("[data-f='start_time']", card);
      const end = qs("[data-f='end_time']", card);
      if (direction && !String(direction.value || "").trim()) direction.value = "DIR TBD";
      const startValue = String(start?.value || "").trim();
      const endValue = String(end?.value || "").trim();
      if (!startValue || !endValue) {
        if (start) start.value = "";
        if (end) end.value = "";
      }
    });
    const phone = qs("#ag_tel", popup);
    if (phone && !String(phone.value || "").trim()) phone.value = "TBD";
  }

  function showTopProblems(popup, results) {
    const shell = qs(".gdW4", popup) || popup;
    const alert = qs(".gdW4Alert", shell);
    const bad = results.filter((item) => item.missing.length);
    if (alert) {
      alert.classList.add("show", "gdV19TopAlert");
      alert.innerHTML = `<strong>No puedes continuar todavía:</strong>${bad.map((item) => `<button type="button" data-v19-day="${item.index}">• Día ${item.index + 1}: falta ${item.missing.join(", ")}.</button>`).join("")}`;
      qsa("[data-v19-day]", alert).forEach((button) => {
        button.addEventListener("click", () => {
          const item = bad.find((candidate) => candidate.index === Number(button.dataset.v19Day));
          item?.card?.scrollIntoView?.({ behavior: "smooth", block: "center" });
        });
      });
    }
    bad[0]?.card?.scrollIntoView?.({ behavior: "smooth", block: "center" });
  }

  function currentWizardPopup() {
    return qsa(".swal2-popup").find((popup) => qs("#ag_modality", popup)) || null;
  }

  function captureWizardClick(event) {
    const button = event.target?.closest?.(".gdW4Next,.swal2-confirm");
    if (!button) return;
    const popup = button.closest?.(".swal2-popup") || currentWizardPopup();
    if (!popup || !isMultiDay(popup)) return;

    normalizeOptionalFields(popup);
    const results = renderDayStatuses(popup);
    const currentPanel = Number(qs(".gdW4Panel.active", popup)?.dataset?.panel || -1);
    const isStepAdvance = button.classList.contains("gdW4Next") && currentPanel === 2;
    const isFinalConfirm = button.classList.contains("swal2-confirm") && currentPanel >= 2;
    if ((isStepAdvance || isFinalConfirm) && results.some((item) => item.missing.length)) {
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
      showTopProblems(popup, results);
    }
  }

  function attachPopup(popup) {
    if (!popup || popup.dataset.gdAgendaV19 === "1" || !qs("#ag_modality", popup)) return;
    popup.dataset.gdAgendaV19 = "1";
    const editor = qs("#ag_group_editor", popup);
    editor?.addEventListener("input", () => renderDayStatuses(popup), true);
    editor?.addEventListener("change", () => renderDayStatuses(popup), true);
    qs("#ag_modality", popup)?.addEventListener("change", () => window.setTimeout(() => renderDayStatuses(popup), 0));
    if (editor) {
      const observer = new MutationObserver(() => renderDayStatuses(popup));
      observer.observe(editor, { childList: true, subtree: true, characterData: true });
    }
    renderDayStatuses(popup);
  }

  function inspect(node) {
    if (!(node instanceof Element)) return;
    if (node.matches(".swal2-popup")) attachPopup(node);
    qsa(".swal2-popup", node).forEach(attachPopup);
  }

  function successKey(data) {
    const leadId = String(data?.id_lead || "");
    const ids = Array.isArray(data?.calendar_event_ids) ? data.calendar_event_ids : [];
    return `${leadId}:${String(data?.calendar_event_id || ids[0] || "")}`;
  }

  function existingSuccessModal() {
    const title = String(qs(".swal2-popup .swal2-title")?.textContent || "");
    return /Evento calendarizado|Evento agendado correctamente|Confirmado \(sin Calendar\)/i.test(title);
  }

  async function showSuccessFallback(data, reason = "fallback") {
    if (!data || existingSuccessModal()) return;
    const key = successKey(data);
    if (!key || successShownFor === key) return;
    successShownFor = key;
    const ids = Array.isArray(data.calendar_event_ids)
      ? data.calendar_event_ids.filter((item) => String(item || "").trim())
      : (data.calendar_event_id ? [data.calendar_event_id] : []);
    const links = Array.isArray(data.calendar_html_links)
      ? data.calendar_html_links.filter((item) => String(item || "").trim())
      : (data.calendar_html_link ? [data.calendar_html_link] : []);
    if (!window.Swal?.fire) return;
    await window.Swal.fire({
      icon: "success",
      title: "Evento agendado correctamente",
      html: `
        <div style="display:grid;gap:10px;text-align:left">
          <div>El lead quedó en <b>Confirmado</b>.</div>
          <div>Eventos creados en Calendar: <b>${ids.length || 1}</b>.</div>
          ${links[0] ? `<a href="${String(links[0]).replace(/"/g, "&quot;")}" target="_blank" rel="noopener" style="color:#60a5fa;font-weight:1000">Abrir en Google Calendar</a>` : ""}
          ${reason === "error" ? '<div class="muted">El agendamiento se completó; solo falló la construcción del resumen extendido.</div>' : ""}
        </div>
      `,
      confirmButtonText: "Cerrar",
    });
  }

  function rememberCalendarSuccess(data) {
    const ids = Array.isArray(data?.calendar_event_ids) ? data.calendar_event_ids : [];
    const hasId = String(data?.calendar_event_id || "").trim() || ids.some((item) => String(item || "").trim());
    if (!hasId) return;
    lastCalendarSuccess = { ...data, _savedAt: Date.now() };
    window.setTimeout(() => showSuccessFallback(lastCalendarSuccess, "fallback"), 1800);
  }

  function installFetchObserver() {
    if (window.__gdAgendaFetchV19 || typeof window.fetch !== "function") return;
    window.__gdAgendaFetchV19 = true;
    const originalFetch = window.fetch.bind(window);
    window.fetch = async (...args) => {
      const response = await originalFetch(...args);
      try {
        const input = args[0];
        const url = String(typeof input === "string" ? input : input?.url || "");
        const method = String(args[1]?.method || input?.method || "GET").toUpperCase();
        const agendaResponse = (
          (/\/leads\/\d+\/confirmar_agendamiento(?:\?|$)/.test(url) && method === "POST")
          || (/\/tools\/agenda\/\d+\/approve(?:\?|$)/.test(url) && method === "PUT")
        );
        if (agendaResponse && response.ok) {
          const data = await response.clone().json();
          rememberCalendarSuccess(data);
        }
      } catch (_) {}
      return response;
    };
  }

  function installErrorFallback() {
    const react = () => {
      if (lastCalendarSuccess && Date.now() - Number(lastCalendarSuccess._savedAt || 0) < 15000) {
        window.setTimeout(() => showSuccessFallback(lastCalendarSuccess, "error"), 0);
      }
    };
    window.addEventListener("error", react);
    window.addEventListener("unhandledrejection", react);
  }

  function start() {
    injectStyle();
    installFetchObserver();
    installErrorFallback();
    document.addEventListener("click", captureWizardClick, true);
    qsa(".swal2-popup").forEach(attachPopup);
    const observer = new MutationObserver((mutations) => {
      mutations.forEach((mutation) => mutation.addedNodes.forEach(inspect));
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();

  window.__GD_AGENDA_MULTIDAY_V19_BUILD__ = BUILD;
})();
