(() => {
  "use strict";

  const BUILD = "GD-AGENDA-WIZARD-V4-20260730";
  const qs = (selector, root = document) => root.querySelector(selector);
  const qsa = (selector, root = document) => [...root.querySelectorAll(selector)];
  const value = (selector, root = document) => String(qs(selector, root)?.value || "").trim();
  const checked = (selector, root = document) => !!qs(selector, root)?.checked;
  const emit = (element, type = "change") => {
    if (element) element.dispatchEvent(new Event(type, { bubbles: true }));
  };
  const escapeHtml = (input) => String(input ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");

  function injectStyle() {
    if (qs("#gdAgendaWizardV4Style")) return;
    const style = document.createElement("style");
    style.id = "gdAgendaWizardV4Style";
    style.textContent = `
      .gdAgendaWizardV4{width:min(1160px,calc(100vw - 20px))!important;padding:18px!important;border-radius:20px!important}
      .gdAgendaWizardV4 .swal2-title{margin:0 0 12px!important;font-size:23px!important}
      .gdAgendaWizardV4 .swal2-html-container{max-height:73vh!important;overflow:auto!important;margin:0!important;padding:0 4px 4px!important}
      .gdW4{display:grid;gap:14px;text-align:left}
      .gdW4Steps{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;position:sticky;top:0;z-index:20;padding:4px 0 11px;background:var(--surface,#0c1b2b)}
      .gdW4Step{border:1px solid var(--border,#22384f);background:var(--surface2,#102235);color:var(--muted,#a9bbd2);border-radius:13px;padding:10px 9px;display:flex;align-items:center;justify-content:center;gap:8px;font-weight:950;font-size:12px;text-align:center;cursor:pointer}
      .gdW4Step b{display:inline-flex;align-items:center;justify-content:center;width:23px;height:23px;border-radius:999px;border:1px solid currentColor;flex:0 0 auto}
      .gdW4Step.active{color:var(--text,#eaf2ff);border-color:rgba(25,195,125,.76);background:rgba(25,195,125,.15)}
      .gdW4Step.done{color:#19c37d;border-color:rgba(25,195,125,.44)}
      .gdW4Panel{display:none;gap:13px}.gdW4Panel.active{display:grid}
      .gdW4Hero{display:flex;justify-content:space-between;gap:14px;border:1px solid rgba(96,165,250,.30);background:rgba(96,165,250,.08);border-radius:16px;padding:13px 15px}
      .gdW4Hero strong{display:block;font-size:15px;margin-bottom:3px}.gdW4Hero span{display:block;color:var(--muted,#a9bbd2);font-size:12px;line-height:1.45}
      .gdW4Badge{padding:7px 10px;border-radius:999px;border:1px solid var(--border,#22384f);font-size:11px;font-weight:950;white-space:nowrap;height:max-content}
      .gdW4Modes{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
      .gdW4Mode{border:1px solid var(--border,#22384f);background:var(--surface2,#102235);color:var(--text,#eaf2ff);border-radius:15px;padding:14px;min-height:112px;display:grid;align-content:start;gap:6px;cursor:pointer;text-align:left}
      .gdW4Mode.active{border-color:rgba(25,195,125,.76);background:rgba(25,195,125,.12);box-shadow:0 0 0 3px rgba(25,195,125,.07)}
      .gdW4Mode i{font-style:normal;font-size:22px}.gdW4Mode strong{font-size:14px}.gdW4Mode small{color:var(--muted,#a9bbd2);font-size:11px;line-height:1.42}
      .gdW4Switches{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
      .gdW4Switch{border:1px solid var(--border,#22384f);border-radius:14px;background:rgba(0,0,0,.07);padding:12px 13px;display:flex;align-items:center;justify-content:space-between;gap:10px}
      .gdW4Switch strong{font-size:13px}.gdW4Switch small{display:block;color:var(--muted,#a9bbd2);font-size:11px;margin-top:2px}.gdW4Switch input{width:20px;height:20px}
      .gdW4Alert{display:none;border:1px solid rgba(245,158,11,.50);background:rgba(245,158,11,.11);border-radius:14px;padding:11px 13px;font-size:12px;line-height:1.5}
      .gdW4Alert.show{display:block}.gdW4Alert button{display:block;width:100%;margin-top:7px;border:0;background:transparent;color:inherit;text-align:left;padding:0;font-weight:850;cursor:pointer}
      .gdW4Card{border:1px solid var(--border,#22384f)!important;background:rgba(0,0,0,.055)!important;border-radius:16px!important;padding:14px!important;display:grid!important;gap:12px!important}
      .gdW4LegacyMode,.gdW4Hidden{display:none!important}.gdAgendaWizardV4 #ag_setup_toggle{display:none!important}
      .gdW4Counts{display:grid!important;grid-template-columns:repeat(3,minmax(0,220px));gap:10px!important;border:1px solid var(--border,#22384f);border-radius:14px;padding:12px;background:rgba(0,0,0,.06)}
      .gdW4Counts>label:last-child{display:none!important}
      .gdW4Duration{display:flex;justify-content:space-between;gap:12px;border:1px solid rgba(25,195,125,.38);background:rgba(25,195,125,.08);border-radius:13px;padding:10px 12px;font-weight:950}
      .gdW4Duration.bad{border-color:rgba(245,158,11,.48);background:rgba(245,158,11,.09)}.gdW4Duration small{color:var(--muted,#a9bbd2);font-weight:800}
      .gdW4ModeHint{display:none;border:1px solid rgba(96,165,250,.30);background:rgba(96,165,250,.07);border-radius:13px;padding:10px 12px;color:var(--muted,#a9bbd2);font-size:12px;line-height:1.5}
      .gdW4ModeHint.show{display:block}.gdW4Sync{border:1px solid rgba(96,165,250,.28);background:rgba(96,165,250,.07);border-radius:12px;padding:9px 11px;font-size:11px;color:var(--muted,#a9bbd2)}
      .gdW4GroupDuration{margin-top:9px;border-radius:10px;padding:8px 10px;background:rgba(25,195,125,.08);border:1px solid rgba(25,195,125,.28);font-size:11px;font-weight:900}
      .gdW4GroupDuration.warn{background:rgba(245,158,11,.08);border-color:rgba(245,158,11,.34)}
      .gdW4Range{border:1px solid rgba(25,195,125,.34);background:rgba(25,195,125,.07);border-radius:13px;padding:10px 12px;font-size:12px;line-height:1.45}
      .gdW4Problem{outline:3px solid rgba(239,68,68,.40)!important;outline-offset:3px!important;border-color:rgba(239,68,68,.72)!important;animation:gdW4Pulse 1.1s ease 2}
      @keyframes gdW4Pulse{50%{box-shadow:0 0 0 7px rgba(239,68,68,.10)}}
      .gdW4Mount{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px;align-items:center;border:1px solid rgba(25,195,125,.34);background:rgba(25,195,125,.07);border-radius:14px;padding:11px 12px}
      .gdW4Mount small{display:block;color:var(--muted,#a9bbd2);font-size:11px;margin-top:3px}.gdW4Mount button{border:0;border-radius:10px;padding:9px 12px;background:#19c37d;color:#062016;font-weight:950;cursor:pointer}
      .gdAgendaWizardV4 #ag_day_montaje_wrap{border-color:rgba(25,195,125,.32)!important;background:rgba(25,195,125,.05)!important}
      .gdW4Review{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px}.gdW4Item{border:1px solid var(--border,#22384f);border-radius:12px;padding:9px 10px;background:rgba(0,0,0,.07);min-width:0}.gdW4Item small{display:block;color:var(--muted,#a9bbd2);font-weight:850;margin-bottom:3px}.gdW4Item div{font-weight:950;overflow-wrap:anywhere}.gdW4Item.wide{grid-column:span 3}
      .gdAgendaWizardV4 .swal2-actions{gap:9px!important;position:sticky;bottom:0;z-index:30;padding:12px 0 0!important;margin:8px 0 0!important;background:var(--surface,#0c1b2b)}
      .gdW4Back,.gdW4Next{border:1px solid var(--border,#22384f);border-radius:11px;padding:10px 16px;font-weight:950;cursor:pointer}.gdW4Back{background:transparent;color:var(--text,#eaf2ff)}.gdW4Next{background:#19c37d;color:#062016;border-color:transparent}.gdW4Footer{text-align:center;color:var(--muted,#a9bbd2);font-size:11px;font-weight:750}
      @media(max-width:820px){.gdW4Steps{grid-template-columns:repeat(2,minmax(0,1fr))}.gdW4Modes,.gdW4Switches,.gdW4Review{grid-template-columns:1fr}.gdW4Counts{grid-template-columns:1fr!important}.gdW4Item.wide{grid-column:span 1}.gdAgendaWizardV4{padding:13px!important}}
    `;
    document.head.appendChild(style);
  }

  function minutes(hhmm) {
    const match = String(hhmm || "").match(/^(\d{1,2}):(\d{2})$/);
    if (!match) return null;
    const hours = Number(match[1]);
    const mins = Number(match[2]);
    return hours >= 0 && hours < 24 && mins >= 0 && mins < 60 ? hours * 60 + mins : null;
  }

  function durationBetween(start, end) {
    const first = minutes(start);
    const last = minutes(end);
    if (first == null || last == null) return null;
    let total = last - first;
    if (total <= 0) total += 1440;
    const hours = Math.floor(total / 60);
    const mins = total % 60;
    return {
      minutes: total,
      text: `${hours} h${mins ? ` ${String(mins).padStart(2, "0")} min` : ""}`,
    };
  }

  function modeValue(popup) {
    return value("#ag_modality", popup) || "simple";
  }

  function setTopAlert(shell, problems, onSelect) {
    const box = qs(".gdW4Alert", shell);
    if (!box) return;
    const list = (problems || []).filter(Boolean);
    box.classList.toggle("show", list.length > 0);
    box.innerHTML = list.length
      ? `<strong>Revisa antes de continuar:</strong>${list.map((problem, index) => `<button type="button" data-problem="${index}">• ${escapeHtml(problem.message)}</button>`).join("")}`
      : "";
    qsa("[data-problem]", box).forEach((button) => {
      button.addEventListener("click", () => onSelect(list[Number(button.dataset.problem || 0)]));
    });
  }

  function getGroupCards(popup) {
    return qsa("#ag_group_editor [data-g-idx]", popup);
  }

  function groupData(card) {
    return {
      card,
      day: value("[data-f='day']", card),
      start: value("[data-f='start_time']", card),
      end: value("[data-f='end_time']", card),
      ops: Number(value("[data-f='ops']", card) || 0),
      comuna: value("[data-f='comuna']", card),
      direccion: value("[data-f='direccion']", card),
      label: value("[data-f='label']", card),
      products: String(qs("[data-f='plist']", card)?.textContent || "").trim(),
    };
  }

  function decorateGroups(popup) {
    const mode = modeValue(popup);
    const cards = getGroupCards(popup);
    cards.forEach((card, index) => {
      const data = groupData(card);
      let durationBox = qs(".gdW4GroupDuration", card);
      if (!durationBox) {
        durationBox = document.createElement("div");
        durationBox.className = "gdW4GroupDuration";
        card.append(durationBox);
      }
      const duration = durationBetween(data.start, data.end);
      durationBox.classList.toggle("warn", !duration);
      const name = mode === "blocks" ? `Bloque ${index + 1}` : `Día ${index + 1}`;
      durationBox.textContent = duration
        ? `${name}: duración ${duration.text}`
        : `${name}: HR TBD hasta completar inicio y término`;
    });

    const range = qs("#gdW4Range", popup);
    if (!range) return;
    if (mode === "blocks") {
      const valid = cards.map(groupData).filter((item) => item.day && item.start && item.end);
      if (!valid.length) {
        range.innerHTML = "<strong>Rango total del evento:</strong> HR TBD. Se creará un solo evento en Calendar.";
        return;
      }
      const ordered = valid.slice().sort((a, b) => `${a.day}T${a.start}`.localeCompare(`${b.day}T${b.start}`));
      const lastOrdered = valid.slice().sort((a, b) => `${a.day}T${a.end}`.localeCompare(`${b.day}T${b.end}`));
      const first = ordered[0];
      const last = lastOrdered[lastOrdered.length - 1];
      range.innerHTML = `<strong>Un solo evento en Calendar:</strong> inicia ${escapeHtml(first.day)} a las ${escapeHtml(first.start)} y termina ${escapeHtml(last.day)} a las ${escapeHtml(last.end)}.`;
    } else if (mode === "multiday") {
      range.innerHTML = `<strong>${cards.length} eventos en Calendar:</strong> uno por cada día configurado. Cada día tiene un único horario, productos, montaje y operadores.`;
    } else {
      range.textContent = "";
    }
  }

  function syncSimpleTime(popup) {
    const start = qs("#ag_hini", popup);
    const end = qs("#ag_hfin", popup);
    const tbd = qs("#ag_hrtbd", popup);
    const box = qs("#gdW4Duration", popup);
    const legacy = qs("#ag_duracion", popup);
    if (!start || !end || !tbd) return;
    const complete = !!String(start.value || "").trim() && !!String(end.value || "").trim();
    const previous = tbd.checked;
    tbd.checked = !complete;
    if (previous !== tbd.checked) emit(tbd, "change");
    const duration = durationBetween(start.value, end.value);
    if (box) {
      box.classList.toggle("bad", !duration);
      box.innerHTML = duration
        ? `<div>Duración calculada: ${escapeHtml(duration.text)}</div><small>${escapeHtml(start.value)} a ${escapeHtml(end.value)}</small>`
        : "<div>Horario pendiente</div><small>El evento quedará HR TBD</small>";
    }
    if (legacy) legacy.textContent = duration ? `Duración: ${duration.text}` : "Duración: —";
  }

  function triggerPreview(popup) {
    const status = qs("#ag_calc_status", popup);
    if (status) status.textContent = "Actualizando vista previa…";
    const notes = qs("#ag_notes", popup);
    if (notes) emit(notes, "input");
    else emit(qs("#ag_quote", popup), "change");
  }

  async function waitPreview(popup) {
    const started = Date.now();
    while (Date.now() - started < 7000) {
      const status = String(qs("#ag_calc_status", popup)?.textContent || "").trim();
      if (/^Listo$/i.test(status)) return true;
      if (/error|no hay|inválid|inval/i.test(status)) return false;
      await new Promise((resolve) => setTimeout(resolve, 150));
    }
    return false;
  }

  function focusProblem(problem, popup, shell, showStep) {
    if (!problem) return;
    if (Number.isInteger(problem.step)) showStep(problem.step, { preserveAlert: true });
    window.setTimeout(() => {
      let target = null;
      if (problem.element && document.contains(problem.element)) target = problem.element;
      if (!target && problem.selector) target = qs(problem.selector, popup);
      if (!target) return;
      qsa(".gdW4Problem", popup).forEach((element) => element.classList.remove("gdW4Problem"));
      const mark = target.closest("label,[data-g-idx],.gdW4Card") || target;
      mark.classList.add("gdW4Problem");
      try { mark.scrollIntoView({ behavior: "smooth", block: "center" }); } catch (_) {}
      window.setTimeout(() => {
        try { target.focus({ preventScroll: true }); } catch (_) { try { target.focus(); } catch (__){ } }
      }, 300);
      window.setTimeout(() => mark.classList.remove("gdW4Problem"), 3200);
    }, 120);
  }

  function validateStep(step, popup) {
    const mode = modeValue(popup);
    const problems = [];

    if (step === 0) {
      if (mode === "multiday" && Number(value("#ag_days_n", popup) || 0) < 2) {
        problems.push({ step: 0, selector: "#ag_days_n", message: "Indica cuántos días tiene el evento (mínimo 2).", blocking: true });
      }
      if (mode === "blocks" && Number(value("#ag_blocks_n", popup) || 0) < 1) {
        problems.push({ step: 0, selector: "#ag_blocks_n", message: "Indica cuántos bloques tiene el evento.", blocking: true });
      }
      if (checked("#ag_flag_multiloc", popup) && Number(value("#ag_locs_n", popup) || 0) < 2) {
        problems.push({ step: 0, selector: "#ag_locs_n", message: "Indica cuántas locaciones tiene el evento (mínimo 2).", blocking: true });
      }
    }

    if (step === 1) {
      if (mode === "simple") {
        syncSimpleTime(popup);
        const start = value("#ag_hini", popup);
        const end = value("#ag_hfin", popup);
        if (start && end && !durationBetween(start, end)) {
          problems.push({ step: 1, selector: "#ag_hini", message: "No se pudo calcular la duración.", blocking: true });
        }
      }
      if (!value("#ag_tel", popup)) {
        problems.push({ step: 1, selector: "#ag_tel", message: "El teléfono quedará por confirmar.", blocking: false });
      }
      if (!value("#ag_dir", popup)) {
        problems.push({ step: 1, selector: "#ag_dir", message: "La dirección quedará DIR TBD.", blocking: false });
      }
    }

    if (step === 2) {
      const distributed = mode === "multiday" || mode === "blocks" || checked("#ag_flag_multiloc", popup);
      if (distributed) {
        const cards = getGroupCards(popup);
        if (!cards.length) {
          problems.push({ step: 2, selector: "#ag_group_editor", message: "No se generaron los días o bloques del evento.", blocking: true });
        }
        cards.forEach((card, index) => {
          const data = groupData(card);
          const label = mode === "blocks" ? `Bloque ${index + 1}` : `Día ${index + 1}`;
          if (!data.day) problems.push({ step: 2, element: qs("[data-f='day']", card), message: `${label}: falta la fecha.`, blocking: true });
          if ((data.start && !data.end) || (!data.start && data.end)) {
            problems.push({ step: 2, element: qs("[data-f='start_time']", card), message: `${label}: completa ambas horas o déjalas vacías como HR TBD.`, blocking: true });
          }
          if (data.start && data.end && !durationBetween(data.start, data.end)) {
            problems.push({ step: 2, element: qs("[data-f='start_time']", card), message: `${label}: horario inválido.`, blocking: true });
          }
          if (!data.start && !data.end) {
            problems.push({ step: 2, element: qs("[data-f='start_time']", card), message: `${label}: quedará HR TBD.`, blocking: false });
          }
          if (!data.direccion) {
            problems.push({ step: 2, element: qs("[data-f='direccion']", card), message: `${label}: quedará DIR TBD.`, blocking: false });
          }
          if (!Number.isFinite(data.ops) || data.ops < 1) {
            problems.push({ step: 2, element: qs("[data-f='ops']", card), message: `${label}: debe tener al menos 1 operador.`, blocking: true });
          }
          if (!data.products || data.products === "—") {
            problems.push({ step: 2, element: card, message: `${label}: no tiene productos asignados.`, blocking: true });
          }
        });
        const allocation = String(qs("#ag_alloc_status", popup)?.textContent || "").trim();
        if (allocation && !/^OK:/i.test(allocation)) {
          problems.push({ step: 2, selector: "#ag_products_allocator", message: allocation, blocking: true });
        }
      } else {
        const products = String(qs("#ag_products_day", popup)?.textContent || "").trim();
        const montage = String(qs("#ag_montaje_day", popup)?.value || "").trim();
        if (!products) problems.push({ step: 2, selector: "#ag_products_wrap", message: "No hay productos visibles.", blocking: true });
        if (!montage) problems.push({ step: 2, selector: "#ag_day_montaje_wrap", message: "Aún no se calculó el montaje sugerido.", blocking: true });
        if (Number(value("#ag_ops_day", popup) || 0) < 1) problems.push({ step: 2, selector: "#ag_ops_day", message: "Debe existir al menos 1 operador.", blocking: true });
      }
      const status = String(qs("#ag_calc_status", popup)?.textContent || "").trim();
      if (/error|no hay|inválid|inval/i.test(status)) {
        problems.push({ step: 2, selector: "#ag_calc_status", message: status, blocking: true });
      }
    }

    return problems;
  }

  function review(popup) {
    const box = qs("#gdW4Review", popup);
    if (!box) return;
    const mode = modeValue(popup);
    const address = value("#ag_dir", popup);
    const phone = value("#ag_tel", popup) || "Por confirmar";
    const location = value("#ag_loc", popup) || "Por confirmar";
    const title = value("#ag_title", popup) || "Evento confirmado";
    const products = String(qs("#ag_products_day", popup)?.textContent || "").trim() || "Sin productos";
    const montage = String(qs("#ag_montaje_day", popup)?.value || "").trim() || String(qs("#ag_mont_manual", popup)?.value || "").trim() || "Sin montaje calculado";

    let scheduleHtml = "";
    if (mode === "simple") {
      const start = value("#ag_hini", popup);
      const end = value("#ag_hfin", popup);
      const duration = durationBetween(start, end);
      scheduleHtml = `
        <div class="gdW4Item"><small>Horario</small><div>${escapeHtml(start && end ? `${start} – ${end}` : "HR TBD")}</div></div>
        <div class="gdW4Item"><small>Duración</small><div>${escapeHtml(duration ? duration.text : "HR TBD")}</div></div>
        <div class="gdW4Item"><small>Operadores</small><div>${escapeHtml(value("#ag_ops_day", popup) || "1")}</div></div>
      `;
    } else {
      const cards = getGroupCards(popup).map(groupData);
      const details = cards.map((item, index) => {
        const label = mode === "blocks" ? (item.label || `Bloque ${index + 1}`) : `Día ${index + 1}`;
        const duration = durationBetween(item.start, item.end);
        return `<div class="gdW4Item wide"><small>${escapeHtml(label)}</small><div>${escapeHtml(item.day || "Sin fecha")} · ${escapeHtml(item.start && item.end ? `${item.start} – ${item.end}` : "HR TBD")} · ${escapeHtml(duration ? duration.text : "HR TBD")} · OPS ${escapeHtml(item.ops || 1)}<br>${escapeHtml(item.comuna || location)} · ${escapeHtml(item.direccion || "DIR TBD")}<br>${escapeHtml(item.products || "Sin productos")}</div></div>`;
      }).join("");
      const eventCount = mode === "multiday" ? `${cards.length} eventos, uno por día` : "1 evento con bloques";
      scheduleHtml = `<div class="gdW4Item wide"><small>Creación en Calendar</small><div>${escapeHtml(eventCount)}</div></div>${details}`;
    }

    box.innerHTML = `<div class="gdW4Review">
      <div class="gdW4Item wide"><small>Cliente / evento</small><div>${escapeHtml(title)}</div></div>
      <div class="gdW4Item"><small>Teléfono</small><div>${escapeHtml(phone)}</div></div>
      <div class="gdW4Item"><small>Comuna / lugar base</small><div>${escapeHtml(location)}</div></div>
      <div class="gdW4Item"><small>Dirección base</small><div>${escapeHtml(address || "DIR TBD")}</div></div>
      ${scheduleHtml}
      <div class="gdW4Item wide"><small>Productos de cotización</small><div style="white-space:pre-wrap">${escapeHtml(products)}</div></div>
      <div class="gdW4Item wide"><small>Montaje</small><div style="white-space:pre-wrap">${escapeHtml(montage)}</div></div>
    </div>`;
  }

  function configurePayment(popup) {
    const mode = qs("#ag_abono_mode", popup);
    const oc = qs("#ag_abono_oc", popup);
    if (!mode || !oc) return;
    let hint = qs("#gdW4OcHint", popup);
    if (!hint) {
      hint = document.createElement("div");
      hint.id = "gdW4OcHint";
      hint.className = "gdW4Sync";
      hint.style.display = "none";
      mode.closest(".agLabel")?.append(hint);
    }
    const sync = () => {
      const isOc = mode.value === "oc";
      if (isOc) {
        oc.value = "";
        oc.style.display = "none";
        hint.style.display = "block";
        hint.textContent = "OC seleccionada. El número de orden de compra se agregará cuando el cliente lo entregue; no es obligatorio para agendar.";
      } else {
        hint.style.display = "none";
      }
    };
    mode.addEventListener("change", sync);
    sync();
  }

  function mountUI(popup) {
    const heading = qs("#ag_day_montaje_wrap > .muted", popup);
    if (heading) heading.textContent = "Montaje y operadores sugeridos";
    const accept = qs("#ag_montaje_confirm", popup);
    if (accept) accept.textContent = "Aceptar sugerencia";
    const edit = qs("#ag_montaje_edit", popup);
    if (edit) edit.textContent = "Agregar / editar montaje";
    const wrap = qs("#ag_day_montaje_wrap", popup);
    if (wrap && !qs("#gdW4Mount", wrap)) {
      const guide = document.createElement("div");
      guide.className = "gdW4Mount";
      guide.id = "gdW4Mount";
      guide.innerHTML = `<div><strong>Sugerencia calculada según los productos</strong><small>Acepta la sugerencia o agrega carros, máquinas, mesas y operadores sin borrar el cálculo base.</small></div><button type="button">Agregar al montaje</button>`;
      wrap.prepend(guide);
      qs("button", guide).addEventListener("click", () => {
        edit?.click();
        const textarea = qs("#ag_montaje_day", popup);
        if (!textarea) return;
        const current = String(textarea.value || "").trimEnd();
        textarea.value = current ? `${current}\n` : "";
        textarea.focus();
        textarea.setSelectionRange(textarea.value.length, textarea.value.length);
      });
    }
  }

  function setMode(popup, mode) {
    const select = qs("#ag_modality", popup);
    if (!select) return;
    select.value = mode;
    emit(select, "change");
    qsa(".gdW4Mode", popup).forEach((button) => button.classList.toggle("active", button.dataset.mode === mode));
  }

  function syncModeUI(popup) {
    const mode = modeValue(popup);
    const globalStart = qs("#ag_hini", popup);
    const globalRow = globalStart?.closest(".agRow");
    const duration = qs("#gdW4Duration", popup);
    const hint = qs("#gdW4ModeHint", popup);
    const distributed = mode === "multiday" || mode === "blocks";
    if (globalRow) globalRow.style.display = distributed ? "none" : "flex";
    if (duration) duration.style.display = distributed ? "none" : "flex";
    if (hint) {
      hint.classList.toggle("show", distributed);
      hint.innerHTML = mode === "multiday"
        ? "<strong>Horario por día:</strong> se solicitará una sola vez dentro de cada día. Al confirmar se crearán tantos eventos en Calendar como días configurados."
        : mode === "blocks"
          ? "<strong>Horario por bloque:</strong> se creará un solo evento. Su inicio será el comienzo del primer bloque y su término será el final del último bloque."
          : "";
    }
    decorateGroups(popup);
  }

  function upgrade(popup) {
    if (!popup || popup.dataset.gdAgendaWizardV4 === "1") return;
    const html = qs(".swal2-html-container", popup);
    const root = html?.firstElementChild;
    const setup = qs("#ag_setup_body", popup);
    const grid = qs(".agGrid", popup);
    const left = grid?.children?.[0];
    const right = grid?.children?.[1];
    if (!html || !root || !setup || !grid || !left || !right) return;

    const children = [...setup.children];
    const legacyMode = children[0];
    const counts = children[1];
    const generalMontage = children[2];
    const priorMontage = children[3];
    const allocator = children[4];
    if (!legacyMode || !counts || !generalMontage || !priorMontage || !allocator) return;

    popup.dataset.gdAgendaWizardV4 = "1";
    popup.classList.remove("gdAgendaWizardV2", "gdAgendaWizardV3");
    popup.classList.add("gdAgendaWizardV4");

    const shell = document.createElement("div");
    shell.className = "gdW4";
    shell.innerHTML = `
      <div class="gdW4Steps">
        <div class="gdW4Step active" data-step="0"><b>1</b><span>Tipo de evento</span></div>
        <div class="gdW4Step" data-step="1"><b>2</b><span>Datos principales</span></div>
        <div class="gdW4Step" data-step="2"><b>3</b><span>Días, bloques y operación</span></div>
        <div class="gdW4Step" data-step="3"><b>4</b><span>Revisión final</span></div>
      </div>
      <div class="gdW4Alert"></div>
      <section class="gdW4Panel active" data-panel="0">
        <div class="gdW4Hero"><div><strong>Define cómo se realizará el evento</strong><span>Elige un formato principal. Varios días y bloques son flujos distintos.</span></div><div class="gdW4Badge">Paso 1 de 4</div></div>
        <div class="gdW4Modes">
          <button type="button" class="gdW4Mode active" data-mode="simple"><i>●</i><strong>Evento único</strong><small>Un día, un horario y una operación.</small></button>
          <button type="button" class="gdW4Mode" data-mode="multiday"><i>▦</i><strong>Varios días</strong><small>Un evento separado por día, usando una sola cotización.</small></button>
          <button type="button" class="gdW4Mode" data-mode="blocks"><i>▤</i><strong>Por bloques</strong><small>Un solo evento con varios tramos horarios.</small></button>
        </div>
        <div class="gdW4Switches">
          <label class="gdW4Switch"><span><strong>Montaje previo</strong><small>Crear un evento separado antes del evento principal.</small></span><input type="checkbox" id="gdW4Prior"></label>
          <label class="gdW4Switch"><span><strong>Varias locaciones</strong><small>Usar direcciones diferentes por día o bloque.</small></span><input type="checkbox" id="gdW4Locs"></label>
        </div>
      </section>
      <section class="gdW4Panel" data-panel="1">
        <div class="gdW4Hero"><div><strong>Datos principales</strong><span>Teléfono y dirección se actualizan también en la ficha del lead al confirmar.</span></div><div class="gdW4Badge">Paso 2 de 4</div></div>
        <div class="gdW4Duration" id="gdW4Duration"></div>
        <div class="gdW4ModeHint" id="gdW4ModeHint"></div>
        <div class="gdW4Sync">En varios días y bloques no se pedirá un horario global: se configurará una sola vez en cada día o bloque.</div>
      </section>
      <section class="gdW4Panel" data-panel="2">
        <div class="gdW4Hero"><div><strong>Configura la operación</strong><span>Distribuye los productos de la cotización y revisa horarios, montaje y operadores.</span></div><div class="gdW4Badge">Paso 3 de 4</div></div>
        <div id="gdW4Range" class="gdW4Range"></div>
        <div id="gdW4Calc" class="gdW4Card"></div>
        <div id="gdW4Dist" class="gdW4Card"></div>
        <div id="gdW4Ops" class="gdW4Card"></div>
      </section>
      <section class="gdW4Panel" data-panel="3">
        <div class="gdW4Hero"><div><strong>Revisión final</strong><span>Recién al confirmar se cambia el estado y se crean los eventos en Calendar.</span></div><div class="gdW4Badge">Paso 4 de 4</div></div>
        <div id="gdW4Review" class="gdW4Card"></div>
        <div id="gdW4Calendar" class="gdW4Card"></div>
      </section>
      <div class="gdW4Footer">No cierre esta ventana durante la confirmación.</div>
    `;

    const panels = qsa(".gdW4Panel", shell);
    legacyMode.classList.add("gdW4LegacyMode");
    counts.classList.add("gdW4Counts");
    panels[0].append(legacyMode, counts);
    left.classList.add("gdW4Card");
    panels[1].append(left);

    const segments = qs("#ag_segments_wrap", left);
    const blocks = qs("#ag_blocks_wrap", left);
    const products = qs("#ag_products_wrap", left);
    const dayEditor = qs("#ag_day_editor_wrap", right);
    const status = qs("#ag_calc_status", right);
    if (status) qs("#gdW4Calc", shell).append(status);
    if (segments) qs("#gdW4Dist", shell).append(segments);
    if (blocks) qs("#gdW4Dist", shell).append(blocks);
    if (products) qs("#gdW4Dist", shell).append(products);
    qs("#gdW4Dist", shell).append(allocator);
    qs("#gdW4Ops", shell).append(dayEditor, generalMontage, priorMontage);
    right.classList.add("gdW4Card");
    qs("#gdW4Calendar", shell).append(right);

    root.classList.add("gdW4Hidden");
    html.prepend(shell);
    const title = qs(".swal2-title", popup);
    if (title) title.textContent = "Configurar agendamiento";

    mountUI(popup);
    configurePayment(popup);

    const prior = qs("#gdW4Prior", popup);
    const priorLegacy = qs("#ag_flag_montaje_event", popup);
    if (prior && priorLegacy) {
      prior.checked = priorLegacy.checked;
      prior.addEventListener("change", () => {
        priorLegacy.checked = prior.checked;
        emit(priorLegacy, "change");
      });
    }

    const locations = qs("#gdW4Locs", popup);
    const locationsLegacy = qs("#ag_flag_multiloc", popup);
    if (locations && locationsLegacy) {
      locations.checked = locationsLegacy.checked;
      locations.addEventListener("change", () => {
        locationsLegacy.checked = locations.checked;
        emit(locationsLegacy, "change");
      });
    }

    qsa(".gdW4Mode", popup).forEach((button) => {
      button.addEventListener("click", () => {
        setMode(popup, button.dataset.mode || "simple");
        window.setTimeout(() => {
          syncModeUI(popup);
          triggerPreview(popup);
        }, 60);
      });
    });

    const timeChanged = () => {
      syncSimpleTime(popup);
      triggerPreview(popup);
    };
    qs("#ag_hini", popup)?.addEventListener("change", timeChanged);
    qs("#ag_hfin", popup)?.addEventListener("change", timeChanged);
    qs("#ag_tel", popup)?.addEventListener("input", () => triggerPreview(popup));
    qs("#ag_dir", popup)?.addEventListener("input", () => triggerPreview(popup));
    qs("#ag_loc", popup)?.addEventListener("input", () => triggerPreview(popup));

    const groupEditor = qs("#ag_group_editor", popup);
    const groupObserver = groupEditor ? new MutationObserver(() => window.setTimeout(() => decorateGroups(popup), 0)) : null;
    groupObserver?.observe(groupEditor, { childList: true, subtree: true });
    groupEditor?.addEventListener("change", () => window.setTimeout(() => decorateGroups(popup), 0));
    groupEditor?.addEventListener("input", () => window.setTimeout(() => decorateGroups(popup), 0));

    const actions = qs(".swal2-actions", popup);
    const confirm = qs(".swal2-confirm", popup);
    const cancel = qs(".swal2-cancel", popup);
    if (!actions || !confirm || !cancel) return;

    const back = document.createElement("button");
    const next = document.createElement("button");
    back.type = next.type = "button";
    back.className = "gdW4Back";
    next.className = "gdW4Next";
    back.textContent = "Volver";
    next.textContent = "Guardar y continuar";
    actions.insertBefore(back, confirm);
    actions.insertBefore(next, confirm);
    confirm.textContent = "Confirmar agendamiento";
    cancel.textContent = "Cancelar";

    let currentStep = 0;

    const showStep = async (targetStep, options = {}) => {
      currentStep = Math.max(0, Math.min(3, Number(targetStep) || 0));
      qsa(".gdW4Panel", shell).forEach((panel, index) => panel.classList.toggle("active", index === currentStep));
      qsa(".gdW4Step", shell).forEach((step, index) => {
        step.classList.toggle("active", index === currentStep);
        step.classList.toggle("done", index < currentStep);
      });
      back.style.display = currentStep ? "inline-flex" : "none";
      next.style.display = currentStep === 3 ? "none" : "inline-flex";
      confirm.style.display = currentStep === 3 ? "inline-flex" : "none";
      if (!options.preserveAlert) setTopAlert(shell, [], (problem) => focusProblem(problem, popup, shell, showStep));
      syncModeUI(popup);
      syncSimpleTime(popup);
      if (currentStep >= 2) {
        triggerPreview(popup);
        await waitPreview(popup);
        mountUI(popup);
        decorateGroups(popup);
      }
      if (currentStep === 3) review(popup);
      try { html.scrollTo({ top: 0, behavior: "smooth" }); } catch (_) {}
    };

    back.addEventListener("click", () => showStep(currentStep - 1));
    next.addEventListener("click", async () => {
      const problems = validateStep(currentStep, popup);
      setTopAlert(shell, problems, (problem) => focusProblem(problem, popup, shell, showStep));
      const blocking = problems.find((problem) => problem.blocking);
      if (blocking) {
        focusProblem(blocking, popup, shell, showStep);
        return;
      }
      triggerPreview(popup);
      await waitPreview(popup);
      await showStep(currentStep + 1);
    });

    qsa(".gdW4Step", shell).forEach((step) => {
      step.addEventListener("click", () => {
        const target = Number(step.dataset.step || 0);
        if (target <= currentStep) showStep(target);
      });
    });

    confirm.addEventListener("click", (event) => {
      const problems = validateStep(2, popup);
      setTopAlert(shell, problems, (problem) => focusProblem(problem, popup, shell, showStep));
      const blocking = problems.find((problem) => problem.blocking);
      if (blocking) {
        event.preventDefault();
        event.stopImmediatePropagation();
        focusProblem(blocking, popup, shell, showStep);
        return;
      }
      review(popup);
      confirm.textContent = "Agendando…";
    }, { capture: true });

    const validation = qs(".swal2-validation-message", popup);
    if (validation) {
      new MutationObserver(() => {
        const message = String(validation.textContent || "").trim();
        if (!message || validation.style.display === "none") return;
        let problem = { step: 2, selector: "#ag_group_editor", message, blocking: true };
        if (/día|bloque|fecha/i.test(message)) problem.selector = "#ag_group_editor";
        else if (/producto/i.test(message)) problem.selector = "#ag_products_allocator";
        else if (/operador|OPS/i.test(message)) problem.selector = "#ag_ops_day";
        else if (/abono|OC|crédito/i.test(message)) problem = { step: 1, selector: "#ag_abono_mode", message, blocking: true };
        else if (/direcci/i.test(message)) problem = { step: 1, selector: "#ag_dir", message, blocking: true };
        else if (/hora|horario/i.test(message)) problem = { step: modeValue(popup) === "simple" ? 1 : 2, selector: modeValue(popup) === "simple" ? "#ag_hini" : "#ag_group_editor", message, blocking: true };
        setTopAlert(shell, [problem], (item) => focusProblem(item, popup, shell, showStep));
        focusProblem(problem, popup, shell, showStep);
      }).observe(validation, { childList: true, subtree: true, attributes: true, attributeFilter: ["style", "class"] });
    }

    syncSimpleTime(popup);
    syncModeUI(popup);
    showStep(0);
  }

  function scan() {
    injectStyle();
    upgrade(qs(".swal2-popup.gdAgenda"));
  }

  const observer = new MutationObserver(() => window.setTimeout(scan, 0));
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      observer.observe(document.body, { childList: true, subtree: true });
      scan();
    });
  } else {
    observer.observe(document.body, { childList: true, subtree: true });
    scan();
  }

  window.__GD_AGENDA_WIZARD_BUILD__ = BUILD;
})();
