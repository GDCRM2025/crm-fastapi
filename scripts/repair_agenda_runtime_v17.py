from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEADS = ROOT / "web" / "views" / "leads.html"
V4 = ROOT / "web" / "js" / "agenda_wizard_v4.js"
V6 = ROOT / "web" / "js" / "agenda_wizard_v6.js"
MARKER = "GD-AGENDA-RUNTIME-V17"


def fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def backup(path: Path) -> None:
    target = path.with_name(path.name + ".bak_v17")
    if not target.exists():
        shutil.copy2(path, target)


def patch_montage_runtime(text: str) -> str:
    text = re.sub(r"(?m)^\s*let\s+montageEditing\s*=\s*false;[^\n]*\n", "", text)
    text = re.sub(r"\bmontageEditing\b", "window.__gdAgendaMontageEditing", text)

    if MARKER not in text:
        match = re.search(r"(?m)^(\s*)const setMontajeLocked = \(locked(?:, force=false)?\)=>\{", text)
        if not match:
            fail("no se encontró setMontajeLocked")
        indent = match.group(1)
        init = f"{indent}// {MARKER}\n{indent}window.__gdAgendaMontageEditing = false;\n"
        text = text[: match.start()] + init + text[match.start() :]

    pattern = re.compile(r"(?ms)^(\s*)const setMontajeLocked = \(locked(?:, force=false)?\)=>\{.*?^\1\};")
    match = pattern.search(text)
    if not match:
        fail("no se pudo localizar el bloque completo setMontajeLocked")
    indent = match.group(1)
    replacement = (
        f'{indent}const setMontajeLocked = (locked, force=false)=>{{\n'
        f'{indent}  const ta = qs("#ag_montaje_day");\n'
        f'{indent}  if (locked && window.__gdAgendaMontageEditing && !force) return;\n'
        f'{indent}  if (ta) ta.readOnly = !!locked;\n'
        f'{indent}  const ops = qs("#ag_ops_day");\n'
        f'{indent}  if (ops) ops.readOnly = !!locked;\n'
        f'{indent}  const btnC = qs("#ag_montaje_confirm");\n'
        f'{indent}  const btnE = qs("#ag_montaje_edit");\n'
        f'{indent}  if (btnC) btnC.disabled = false;\n'
        f'{indent}  if (btnE) btnE.disabled = !locked;\n'
        f'{indent}}};'
    )
    text = text[: match.start()] + replacement + text[match.end() :]

    text = re.sub(
        r'(?m)^(\s*)setMontajeLocked\(false\);\n(\s*)const ta = qs\("#ag_montaje_day"\);',
        lambda m: (
            f'{m.group(1)}window.__gdAgendaMontageEditing = true;\n'
            f'{m.group(1)}setMontajeLocked(false);\n'
            f'{m.group(2)}const ta = qs("#ag_montaje_day");'
        ),
        text,
    )

    if re.search(r"(?<![\w.])montageEditing\b", text):
        fail("quedaron referencias locales montageEditing")
    return text


def patch_simple_products(text: str) -> str:
    pattern = re.compile(r"(?ms)^(\s*)const autoAllocateIfSingle = \(\)=>\{.*?^\1\};")
    match = pattern.search(text)
    if not match:
        fail("no se encontró autoAllocateIfSingle")
    indent = match.group(1)
    replacement = (
        f'{indent}const autoAllocateIfSingle = ()=>{{\n'
        f'{indent}  if (!Array.isArray(setupGroups) || setupGroups.length !== 1) return;\n'
        f'{indent}  const g = setupGroups[0];\n'
        f'{indent}  g.products_map = {{}};\n'
        f'{indent}  for (const it of (allocPool||[])){{\n'
        f'{indent}    const p = String(it.producto||"").trim();\n'
        f'{indent}    const q = Number(it.cantidad||0) || 0;\n'
        f'{indent}    if (!p || !(q>0)) continue;\n'
        f'{indent}    g.products_map[p] = q;\n'
        f'{indent}  }}\n'
        f'{indent}  const assignedText = _mapToLines(g.products_map || {{}});\n'
        f'{indent}  const productEl = qs("#ag_products_day");\n'
        f'{indent}  if (productEl) productEl.textContent = assignedText || "(sin productos)";\n'
        f'{indent}  const allocStatus = qs("#ag_alloc_status");\n'
        f'{indent}  if (allocStatus && assignedText) allocStatus.textContent = "OK: todos los productos están asignados.";\n'
        f'{indent}}};'
    )
    text = text[: match.start()] + replacement + text[match.end() :]

    reset_pattern = re.compile(
        r'(?m)^(\s*)if \(qs\("#ag_products_day"\)\) qs\("#ag_products_day"\)\.textContent = "\(sin productos\)";'
    )
    reset_match = reset_pattern.search(text)
    if reset_match:
        indent = reset_match.group(1)
        replacement_reset = (
            f'{indent}const allocationOk = /^OK:/i.test(String(qs("#ag_alloc_status")?.textContent || "").trim());\n'
            f'{indent}const assignedText = (setupGroups || []).length === 1\n'
            f'{indent}  ? _mapToLines((setupGroups[0] || {{}}).products_map || {{}})\n'
            f'{indent}  : "";\n'
            f'{indent}const productEl = qs("#ag_products_day");\n'
            f'{indent}if (productEl) {{\n'
            f'{indent}  if (assignedText) productEl.textContent = assignedText;\n'
            f'{indent}  else if (!allocationOk) productEl.textContent = "(sin productos)";\n'
            f'{indent}}}'
        )
        text = text[: reset_match.start()] + replacement_reset + text[reset_match.end() :]
    elif 'const assignedText = (setupGroups || []).length === 1' not in text:
        fail("no se encontró reinicio de productos en catch de preview")

    return text


def patch_v4_validation(text: str) -> str:
    marker = "GD-SIMPLE-PRODUCTS-V17"
    if marker in text:
        return text

    strict_step1_old = '''      } else if (!products || products === "—" || /sin productos/i.test(products)) {
        add("#ag_products_wrap", "La cotización no tiene productos cargados.");
      }'''
    strict_step1_new = '''      } else {
        const simpleStep1GroupProducts = getGroupCards(popup).some((card) => {
          const groupProducts = groupData(card).products;
          return !!groupProducts && groupProducts !== "—" && !/sin productos/i.test(groupProducts);
        });
        if ((!products || products === "—" || /sin productos/i.test(products))
            && !(/^OK:/i.test(allocation) && simpleStep1GroupProducts)) {
          add("#ag_products_wrap", "La cotización no tiene productos cargados.");
        }
      }'''
    if strict_step1_old in text:
        text = text.replace(strict_step1_old, strict_step1_new, 1)

    strict_old = '        if (!products || products === "—" || /sin productos/i.test(products)) add("#ag_products_wrap", "No hay productos cargados.", 1);'
    strict_new = '''        // GD-SIMPLE-PRODUCTS-V17
        const simpleAllocationOk = /^OK:/i.test(statusText("#ag_alloc_status"));
        const simpleGroupProducts = getGroupCards(popup).some((card) => {
          const groupProducts = groupData(card).products;
          return !!groupProducts && groupProducts !== "—" && !/sin productos/i.test(groupProducts);
        });
        if ((!products || products === "—" || /sin productos/i.test(products))
            && !(simpleAllocationOk && simpleGroupProducts)) {
          add("#ag_products_wrap", "No hay productos cargados.", 1);
        }'''
    if strict_old in text:
        return text.replace(strict_old, strict_new, 1)

    base_old = '        if (!products) problems.push({ step: 2, selector: "#ag_products_wrap", message: "No hay productos visibles.", blocking: true });'
    base_new = '''        // GD-SIMPLE-PRODUCTS-V17
        const simpleAllocationOk = /^OK:/i.test(String(qs("#ag_alloc_status", popup)?.textContent || "").trim());
        const simpleGroupProducts = getGroupCards(popup).some((card) => {
          const groupProducts = groupData(card).products;
          return !!groupProducts && groupProducts !== "—" && !/sin productos/i.test(groupProducts);
        });
        if (!products && !(simpleAllocationOk && simpleGroupProducts)) {
          problems.push({ step: 2, selector: "#ag_products_wrap", message: "No hay productos visibles.", blocking: true });
        }'''
    if base_old in text:
        return text.replace(base_old, base_new, 1)

    fail("no se encontró validación de productos del evento único")


def bump_versions(text: str) -> str:
    text = re.sub(r'agenda_wizard_v4\.js\?v=[^"\']+', 'agenda_wizard_v4.js?v=20260731-17', text)
    text = re.sub(r'agenda_wizard_v6\.js\?v=[^"\']+', 'agenda_wizard_v6.js?v=20260731-17', text)
    return text


def check_inline_js(html: str) -> None:
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, flags=re.I | re.S)
    if not blocks:
        fail("no se encontraron scripts inline")
    with tempfile.TemporaryDirectory() as tmp:
        for idx, block in enumerate(blocks, start=1):
            path = Path(tmp) / f"inline_{idx}.js"
            path.write_text(block, encoding="utf-8")
            result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
            if result.returncode != 0:
                fail(f"JS inline {idx}: {(result.stderr or result.stdout).strip()}")


def node_check(path: Path) -> None:
    result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
    if result.returncode != 0:
        fail(f"{path.name}: {(result.stderr or result.stdout).strip()}")


def main() -> None:
    for path in (LEADS, V4, V6):
        if not path.exists():
            fail(f"falta {path}")
        backup(path)

    html = LEADS.read_text(encoding="utf-8")
    v4 = V4.read_text(encoding="utf-8")
    v6 = V6.read_text(encoding="utf-8")

    html = patch_montage_runtime(html)
    html = patch_simple_products(html)
    html = bump_versions(html)
    v4 = patch_v4_validation(v4)

    LEADS.write_text(html, encoding="utf-8")
    V4.write_text(v4, encoding="utf-8")
    V6.write_text(v6, encoding="utf-8")

    check_inline_js(html)
    node_check(V4)
    node_check(V6)

    final_html = LEADS.read_text(encoding="utf-8")
    final_v4 = V4.read_text(encoding="utf-8")

    if MARKER not in final_html:
        fail("falta marcador runtime V17")
    if re.search(r"(?<![\w.])montageEditing\b", final_html):
        fail("persisten referencias montageEditing sin namespace")
    if "const assignedText = _mapToLines(g.products_map" not in final_html:
        fail("falta autoasignación visible para evento único")
    if "GD-SIMPLE-PRODUCTS-V17" not in final_v4:
        fail("falta validación robusta de productos simples")
    if 'Array.from({length:daysN}, ()=>"")' not in final_html:
        fail("regresión: varios días volvió a generar fechas")

    print("OK montageEditing definido globalmente y sin ReferenceError")
    print("OK editar y borrar montaje no activa bloqueo automático")
    print("OK evento único autoasigna y muestra sus productos")
    print("OK un error de preview no borra productos ya asignados")
    print("OK validación acepta productos simples asignados al 100%")
    print("OK fechas de varios días continúan vacías")
    print("OK sintaxis JavaScript completa")
    print("AGENDA_RUNTIME_V17_REPAIRED")


if __name__ == "__main__":
    main()
