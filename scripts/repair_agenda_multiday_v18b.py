from __future__ import annotations

import re

import repair_agenda_multiday_v18 as base


def patch_submit_unified_robust(text: str) -> str:
    if f"// {base.MARKER}: SUBMIT UNIFICADO" in text:
        print("OK submit unificado ya aplicado")
        return text

    pattern = re.compile(
        r'''^  const out = await apiJSON\(`/leads/\$\{lead\.id_lead\}/move`, \{.*?^  const gcalLead = \(gcal && typeof gcal === "object" && gcal\.lead && typeof gcal\.lead === "object"\) \? gcal\.lead : \{\};''',
        re.S | re.M,
    )
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        base.fail(f"submit final: se esperaba 1 bloque exacto y se encontraron {len(matches)}")
    match = matches[0]

    new = r'''  // GD-AGENDA-MULTIDAY-V18: SUBMIT UNIFICADO
  // Un solo endpoint realiza preparacion, Calendar y cambio a Confirmado de forma atomica.
  const idempotencyKey = `agenda:${lead.id_lead}:${Date.now()}:${Math.random().toString(36).slice(2)}`;
  let out = null;
  try{
    Swal.fire({
      title: "Agendando evento",
      html: "Creando Calendar y confirmando el lead...",
      allowOutsideClick: false,
      allowEscapeKey: false,
      showConfirmButton: false,
      didOpen: ()=> Swal.showLoading(),
    });
    out = await apiJSON(`/leads/${lead.id_lead}/confirmar_agendamiento`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Idempotency-Key": idempotencyKey,
      },
      body: JSON.stringify(payload),
    });
    Swal.close();
  }catch(err){
    Swal.close();
    await Swal.fire({
      icon: "error",
      title: "No se pudo confirmar y agendar",
      text: String(err?.message || err),
    });
    return false;
  }

  const ev = out?.evento || {};
  const gcal = out || {};
  const gcalLink = String(gcal?.calendar_html_link || (Array.isArray(gcal?.calendar_html_links) ? gcal.calendar_html_links[0] : "") || "");
  const gcalEid = String(gcal?.calendar_event_id || (Array.isArray(gcal?.calendar_event_ids) ? gcal.calendar_event_ids[0] : "") || "");
  const gcalErr = String(gcal?.gcal_error || "");
  const gcalCalId = (Array.isArray(gcal?.calendar_ids) && gcal.calendar_ids.length) ? String(gcal.calendar_ids[0] || "") : "";
  const gcalLead = (gcal && typeof gcal === "object" && gcal.lead && typeof gcal.lead === "object") ? gcal.lead : {};'''

    text = text[: match.start()] + new + text[match.end() :]
    print("OK submit final exacto reemplazado sin tocar preview")
    return text


base.patch_submit_unified = patch_submit_unified_robust

if __name__ == "__main__":
    base.main()
