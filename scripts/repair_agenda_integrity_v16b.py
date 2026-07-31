from __future__ import annotations

import repair_agenda_integrity_v16 as base


def patch_v4_robust(text_in: str) -> str:
    text_out = text_in

    old_step1 = 'if (/error|inválid|inval|no hay productos|sin productos/i.test(`${calc} ${allocation}`)) {'
    new_step1 = 'if (!/^OK:/i.test(allocation) && /error|inválid|inval|no hay productos|sin productos/i.test(`${calc} ${allocation}`)) {'
    if old_step1 in text_out:
        text_out = text_out.replace(old_step1, new_step1, 1)
        print("OK asignación OK prevalece sobre mensaje stale en paso productos")
    elif new_step1 in text_out:
        print("OK prioridad de asignación ya aplicada")

    if "const staleProductMessage" in text_out:
        print("OK validación stale ya aplicada")
        return text_out

    old_status = '''      const status = String(qs("#ag_calc_status", popup)?.textContent || "").trim();
      if (/error|no hay|inválid|inval/i.test(status)) {
        problems.push({ step: 2, selector: "#ag_calc_status", message: status, blocking: true });
      }'''
    new_status = '''      const status = String(qs("#ag_calc_status", popup)?.textContent || "").trim();
      const allocationNow = String(qs("#ag_alloc_status", popup)?.textContent || "").trim();
      const allocationOk = /^OK:/i.test(allocationNow);
      const cardsHaveProducts = getGroupCards(popup).every((card) => {
        const products = groupData(card).products;
        return !!products && products !== "—" && !/sin productos/i.test(products);
      });
      const staleProductMessage = /faltan productos|no hay productos|sin productos/i.test(status);
      if (/error|no hay|inválid|inval|faltan productos/i.test(status)
          && !(allocationOk && cardsHaveProducts && staleProductMessage)) {
        problems.push({ step: 2, selector: "#ag_calc_status", message: status, blocking: true });
      }'''
    if old_status in text_out:
        text_out = text_out.replace(old_status, new_status, 1)
        print("OK validación base ignora error stale si productos están 100% asignados")
        return text_out

    strict_old = '''      const calc = statusText("#ag_calc_status");
      if (/error|inválid|inval|no hay/i.test(calc)) add("#ag_calc_status", calc);'''
    strict_new = '''      const calc = statusText("#ag_calc_status");
      const allocationNow = statusText("#ag_alloc_status");
      const allocationOk = /^OK:/i.test(allocationNow);
      const cardsHaveProducts = getGroupCards(popup).every((card) => {
        const products = groupData(card).products;
        return !!products && products !== "—" && !/sin productos/i.test(products);
      });
      const staleProductMessage = /faltan productos|no hay productos|sin productos/i.test(calc);
      if (/error|inválid|inval|no hay|faltan productos/i.test(calc)
          && !(allocationOk && cardsHaveProducts && staleProductMessage)) {
        add("#ag_calc_status", calc);
      }'''
    if strict_old in text_out:
        text_out = text_out.replace(strict_old, strict_new, 1)
        print("OK validación V8 ignora error stale si productos están 100% asignados")
        return text_out

    base.fail("agenda_wizard_v4: no se encontró validación de estado de cálculo")


base.patch_v4 = patch_v4_robust

if __name__ == "__main__":
    base.main()
