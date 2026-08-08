(() => {
  "use strict";

  const BUILD = "GD-AGENDA-WIZARD-V2-20260730";

  const qs = (selector, root = document) => root.querySelector(selector);
  const qsa = (selector, root = document) => [...root.querySelectorAll(selector)];

  function injectStyle() {
    if (document.getElementById("gdAgendaWizardV2Style")) return;
    const style = document.createElement("style");
    style.id = "gdAgendaWizardV2Style";
    style.textContent = `
      .gdAgendaWizardV2{width:min(1120px,calc(100vw - 24px))!important;padding:20px!important}
      .gdAgendaWizardV2 .swal2-title{margin:0 0 12px!important;font-size:24px!important}
      .gdAgendaWizardV2 .swal2-html-container{max-height:72vh!important;overflow:auto!important;margin:0!important;padding:0 4px!important}
      .gdAwShell{display:grid;gap:14px;text-align:left}
      .gdAwSteps{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;position:sticky;top:0;z-index:6;padding:4px 0 10px;background:var(--surface,#0c1b2b)}
      .gdAwStep{border:1px solid var(--border,#22384f);background:var(--surface2,#102235);color:var(--muted,#a9bbd2);border-radius:12px;padding:10px 9px;display:flex;gap:8px;align-items:center;justify-content:center;font-weight:900;font-size:12px;line-height:1.15;text-align:center}
      .gdAwStep b{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:999px;border:1px solid currentColor;flex:0 0 auto}
      .gdAwStep.active{background:rgba(25,195,125,.14);border-color:rgba(25,195,125,.72);color:var(--text,#eaf2ff)}
      .gdAwStep.done{border-color:rgba(25,195,125,.45);color:#19c37d}
      .gdAwPanel{display:none;gap:12px}
      .gdAwPanel.active{display:grid}
      .gdAwIntro{border:1px solid rgba(96,165,250,.30);background:rgba(96,165,250,.08);border-radius:14px;padding:11px 13px;display:grid;gap:4px}
      .gdAwIntro strong{font-size:14px}
      .gdAwIntro span{font-size:12px;color:var(--muted,#a9bbd2)}
      .gdAwAlert{display:none;border:1px solid rgba(245,158,11,.48);background:rgba(245,158,11,.10);border-radius:14px;padding:11px 13px;color:var(--text,#eaf2ff);font-weight:850}
      .gdAwAlert.show{display:block}
      .gdAwPreviewHead{border:1px solid rgba(25,195,125,.34);background:rgba(25,195,125,.08);border-radius:14px;padding:12px;display:grid;gap:8px}
      .gdAwPreviewGrid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
      .gdAwPreviewItem{border:1px solid var(--border,#22384f);border-radius:10px;padding:8px 10px;background:rgba(0,0,0,.08)}
      .gdAwPreviewItem small{display:block;color:var(--muted,#a9bbd2);font-weight:800;margin-bottom:3px}
      .gdAwPreviewItem div{font-weight:950;overflow-wrap:anywhere}
      .gdAwFooterHint{text-align:center;color:var(--muted,#a9bbd2);font-size:12px;font-weight:750}
      .gdAgendaWizardV2 .swal2-actions{gap:9px!important;position:sticky;bottom:0;z-index:8;padding:12px 0 0!important;margin:8px 0 0!important;background:var(--surface,#0c1b2b)}
      .gdAgendaWizardV2 .swal2-actions button{min-width:128px}
      .gdAwBack,.gdAwNext{border:1px solid var(--border,#22384f);border-radius:10px;padding:10px 16px;font-weight:900;cursor:pointer}
      .gdAwBack{background:transparent;color:var(--text,#eaf2ff)}
      .gdAwNext{background:#19c37d;color:#062016;border-color:transparent}
      .gdAwHiddenHost{display:none!important}
      .gdAgendaWizardV2 #ag_setup_toggle{display:none!important}
      .gdAgendaWizardV2 .agGrid{display:block!important}
      .gdAgendaWizardV2 .agCard{margin:0!important}
      .gdAgendaWizardV2 [title]{cursor:help}
      @media(max-width:760px){
        .gdAwSteps{grid-template-columns:repeat(2,minmax(0,1fr))}
        .gdAwPreviewGrid{grid-template-columns:1fr}
        .gdAgendaWizardV2{padding:14px!important}
      }
    `;
    document.head.appendChild(style);
  }

  function value(id) {
    return String(qs(id)?.value || "").trim();
  }

  function checked(id) {
    return !!qs(id)?.checked;
  }

  function setAlert(shell, messages) {
    const alert = qs(".gdAwAlert", shell);
    if (!alert) return;
    const items = (messages || []).filter(Boolean);
    alert.classList.toggle("show", items.length > 0);
    alert.innerHTML = items.length
      ? `<strong>Atención antes de continuar</strong><br>${items.map((item) => `• ${String(item)}`).join("<br>")}`
      : "";
  }

  function validateStep(step, shell) {
    const errors = [];

    if (step === 0) {
      if (checked("#ag_flag_multiday")) {
        const days = Number(value("#ag_days_n") || 0);
        if (!Number.isFinite(days) || days < 2) errors.push("Indica cuántos días tiene el evento (mínimo 2).");
      }
      if (checked("#ag_flag_blocks")) {
        const blocks = Number(value("#ag_blocks_n") || 0);
        if (!Number.isFinite(blocks) || blocks < 1) errors.push("Indica cuántos bloques tiene el evento.");
      }
      if (checked("#ag_flag_multiloc")) {
        const locations = Number(value("#ag_locs_n") || 0);
        if (!Number.isFinite(locations) || locations < 2) errors.push("Indica cuántas locaciones tiene el evento (mínimo 2).");
      }
    }

    if (step === 1) {
      const start = value("#ag_hini");
      const end = value("#ag_hfin");
      const address = value("#ag_dir");
      const hrTbd = checked("#ag_hrtbd") || !start || !end;
      const dirTbd = !address;

      if (hrTbd && qs("#ag_hrtbd")) qs("#ag_hrtbd").checked = true;
      if (hrTbd && dirTbd) errors.push("El evento quedará marcado HR Y DIR TBD.");
      else if (hrTbd) errors.push("El evento quedará marcado HR TBD.");
      else if (dirTbd) errors.push("El evento quedará marcado DIR TBD.");

      if (start && end && end <= start) errors.push("La hora de término debe ser posterior a la hora de inicio.");
    }

    if (step === 2 && (checked("#ag_flag_multiday") || checked("#ag_flag_blocks") || checked("#ag_flag_multiloc"))) {
      const status = String(qs("#ag_alloc_status")?.textContent || "").trim();
      if (status && !/^OK:/i.test(status)) errors.push(status);
    }

    setAlert(shell, errors);

    if (step === 1) {
      // Los TBD son advertencias deliberadas, no bloqueo.
      return !errors.some((item) => item.includes("posterior"));
    }
    return errors.length === 0;
  }

  function buildPreviewHeader(panel) {
    let box = qs(".gdAwPreviewHead", panel);
    if (!box) {
      box = document.createElement("div");
      box.className = "gdAwPreviewHead";
      panel.prepend(box);
    }

    const start = value("#ag_hini");
    const end = value("#ag_hfin");
    const address = value("#ag_dir");
    const hrTbd = checked("#ag_hrtbd") || !start || !end;
    const dirTbd = !address;
    const status = hrTbd && dirTbd ? "HR Y DIR TBD" : hrTbd ? "HR TBD" : dirTbd ? "DIR TBD" : "Datos completos";
    const title = value("#ag_title") || "Evento confirmado";
    const phone = value("#ag_tel") || "Por confirmar";
    const location = value("#ag_loc") || "Por confirmar";
    const schedule = hrTbd ? "HR TBD" : `${start}–${end}`;

    box.innerHTML = `
      <strong>Revisa antes de crear la agenda</strong>
      <div class="gdAwPreviewGrid">
        <div class="gdAwPreviewItem"><small>Cliente / evento</small><div>${escapeHtml(title)}</div></div>
        <div class="gdAwPreviewItem"><small>Teléfono</small><div>${escapeHtml(phone)}</div></div>
        <div class="gdAwPreviewItem"><small>Comuna / lugar</small><div>${escapeHtml(location)}</div></div>
        <div class="gdAwPreviewItem"><small>Dirección</small><div>${escapeHtml(address || "DIR TBD")}</div></div>
        <div class="gdAwPreviewItem"><small>Horario</small><div>${escapeHtml(schedule)}</div></div>
        <div class="gdAwPreviewItem"><small>Estado operativo</small><div>${escapeHtml(status)}</div></div>
      </div>
    `;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function upgradePopup(popup) {
    if (!popup || popup.dataset.gdAgendaWizardV2 === "1") return;

    const html = qs(".swal2-html-container", popup);
    const originalRoot = html?.firstElementChild;
    const setupBody = qs("#ag_setup_body", popup);
    const grid = qs(".agGrid", popup);
    const leftCard = grid?.children?.[0];
    const rightCard = grid?.children?.[1];

    if (!html || !originalRoot || !setupBody || !grid || !leftCard || !rightCard) return;

    popup.dataset.gdAgendaWizardV2 = "1";
    popup.dataset.build = BUILD;
    popup.classList.add("gdAgendaWizardV2");

    const setupChildren = [...setupBody.children];
    const modeRow = setupChildren[0];
    const countsRow = setupChildren[1];
    const generalMount = setupChildren[2];
    const previousMount = setupChildren[3];
    const allocationEditor = setupChildren[4];

    if (!modeRow || !countsRow || !generalMount || !previousMount || !allocationEditor) return;

    const shell = document.createElement("div");
    shell.className = "gdAwShell";
    shell.innerHTML = `
      <div class="gdAwSteps">
        <div class="gdAwStep active" data-step="0"><b>1</b><span>Tipo de evento</span></div>
        <div class="gdAwStep" data-step="1"><b>2</b><span>Datos principales</span></div>
        <div class="gdAwStep" data-step="2"><b>3</b><span>Distribución y operación</span></div>
        <div class="gdAwStep" data-step="3"><b>4</b><span>Revisión final</span></div>
      </div>
      <div class="gdAwAlert"></div>
      <section class="gdAwPanel active" data-panel="0">
        <div class="gdAwIntro"><strong>¿Cómo se realizará este evento?</strong><span>Selecciona solamente lo que aplica. El sistema adaptará los pasos siguientes.</span></div>
      </section>
      <section class="gdAwPanel" data-panel="1">
        <div class="gdAwIntro"><strong>Completa los datos principales</strong><span>Si aún no tienes horario o dirección, el evento quedará identificado como TBD y se mostrará una alerta antes de confirmar.</span></div>
      </section>
      <section class="gdAwPanel" data-panel="2">
        <div class="gdAwIntro"><strong>Distribuye productos y define la operación</strong><span>En varios días o bloques, la suma distribuida debe coincidir con la cotización.</span></div>
      </section>
      <section class="gdAwPanel" data-panel="3">
        <div class="gdAwIntro"><strong>Preview final</strong><span>Comprueba cliente, dirección, horario, productos, montaje y operadores antes de crear Calendar.</span></div>
      </section>
      <div class="gdAwFooterHint">Los datos no se guardan como Confirmado hasta terminar el último paso.</div>
    `;

    const panels = qsa(".gdAwPanel", shell);
    panels[0].append(modeRow, countsRow);
    panels[1].append(leftCard);
    panels[2].append(generalMount, previousMount, allocationEditor);
    panels[3].append(rightCard);

    originalRoot.classList.add("gdAwHiddenHost");
    html.prepend(shell);

    const title = qs(".swal2-title", popup);
    if (title) title.textContent = "Configurar agendamiento";

    // Ayudas puntuales.
    qs("#ag_modality", popup)?.setAttribute("title", "Elige evento único, varios días, bloques o múltiples locaciones.");
    qs("#ag_flag_montaje_event", popup)?.setAttribute("title", "Crea un bloque separado de montaje antes del evento principal.");
    qs("#ag_hrtbd", popup)?.setAttribute("title", "Actívalo cuando el horario aún no esté confirmado.");
    qs("#ag_dir", popup)?.setAttribute("title", "Si queda vacío, la agenda se marcará DIR TBD.");
    qs("#ag_alloc_status", popup)?.setAttribute("title", "Debe indicar OK antes de confirmar un evento distribuido.");

    const actions = qs(".swal2-actions", popup);
    const confirm = qs(".swal2-confirm", popup);
    const cancel = qs(".swal2-cancel", popup);
    if (!actions || !confirm || !cancel) return;

    const back = document.createElement("button");
    back.type = "button";
    back.className = "gdAwBack";
    back.textContent = "Volver";

    const next = document.createElement("button");
    next.type = "button";
    next.className = "gdAwNext";
    next.textContent = "Siguiente";

    actions.insertBefore(back, confirm);
    actions.insertBefore(next, confirm);
    confirm.textContent = "Confirmar agendamiento";
    cancel.textContent = "Cancelar";

    let step = 0;

    const showStep = (target) => {
      step = Math.max(0, Math.min(3, target));
      qsa(".gdAwPanel", shell).forEach((panel, index) => panel.classList.toggle("active", index === step));
      qsa(".gdAwStep", shell).forEach((item, index) => {
        item.classList.toggle("active", index === step);
        item.classList.toggle("done", index < step);
      });
      back.style.display = step === 0 ? "none" : "inline-flex";
      next.style.display = step === 3 ? "none" : "inline-flex";
      confirm.style.display = step === 3 ? "inline-flex" : "none";
      setAlert(shell, []);
      if (step === 3) buildPreviewHeader(panels[3]);
      try { qs(".swal2-html-container", popup)?.scrollTo({ top: 0, behavior: "smooth" }); } catch (_) {}
    };

    back.addEventListener("click", () => showStep(step - 1));
    next.addEventListener("click", () => {
      if (!validateStep(step, shell)) return;
      showStep(step + 1);
    });

    qsa(".gdAwStep", shell).forEach((item) => {
      item.addEventListener("click", () => {
        const target = Number(item.dataset.step || 0);
        if (target <= step) showStep(target);
      });
    });

    confirm.addEventListener("click", () => {
      if (!validateStep(2, shell)) {
        showStep(2);
        return;
      }
      confirm.disabled = true;
      confirm.textContent = "Agendando…";
      next.disabled = true;
      back.disabled = true;
    }, { capture: true });

    showStep(0);
  }

  function scan() {
    injectStyle();
    const popup = qs(".swal2-popup.gdAgenda");
    if (popup) upgradePopup(popup);
  }

  const observer = new MutationObserver(() => {
    window.setTimeout(scan, 0);
  });

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
