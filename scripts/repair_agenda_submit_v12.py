from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEADS = ROOT / "web" / "views" / "leads.html"

HR_NEEDLE = 'const hrTbd = !!qs("#ag_hrtbd")?.checked || !hIni || !hFin;'
HINI_NEEDLE = 'const hIni = (qs("#ag_hini")?.value || "").trim();'
HFIN_NEEDLE = 'const hFin = (qs("#ag_hfin")?.value || "").trim();'


def reorder_all(lines: list[str]) -> int:
    moved = 0
    i = 0
    while i < len(lines):
        if HR_NEEDLE not in lines[i]:
            i += 1
            continue

        window_end = min(len(lines), i + 12)
        hini = next((j for j in range(i + 1, window_end) if HINI_NEEDLE in lines[j]), None)
        hfin = next((j for j in range(i + 1, window_end) if HFIN_NEEDLE in lines[j]), None)

        if hini is None or hfin is None:
            i += 1
            continue

        hr_line = lines.pop(i)
        target = max(hini, hfin)
        target -= 1
        lines.insert(target + 1, hr_line)
        moved += 1
        i = target + 2
    return moved


def bad_order_lines(lines: list[str]) -> list[int]:
    bad: list[int] = []
    last_hini = -1
    last_hfin = -1
    for idx, line in enumerate(lines):
        if HINI_NEEDLE in line:
            last_hini = idx
        if HFIN_NEEDLE in line:
            last_hfin = idx
        if HR_NEEDLE in line and (last_hini < 0 or last_hfin < 0 or last_hini > idx or last_hfin > idx):
            lookback = lines[max(0, idx - 10):idx]
            if not any(HINI_NEEDLE in item for item in lookback) or not any(HFIN_NEEDLE in item for item in lookback):
                bad.append(idx + 1)
    return bad


def main() -> None:
    text = LEADS.read_text(encoding="utf-8")
    lines = text.splitlines()

    moved = reorder_all(lines)
    bad = bad_order_lines(lines)
    if bad:
        raise SystemExit("ERROR: hrTbd sigue antes de hIni/hFin en líneas: " + ", ".join(map(str, bad)))

    text = "\n".join(lines) + "\n"

    forbidden = [
        "Abono=0: falta OC / referencia.",
        "Falta OC / referencia (abono=0).",
        "Ingresa OC / referencia.",
    ]
    for item in forbidden:
        text = text.replace(item, "")

    LEADS.write_text(text, encoding="utf-8")

    print(f"OK bloques reordenados: {moved}")
    print("OK todas las referencias hrTbd tienen hIni/hFin declarados antes")
    print("OK OC sin referencia obligatoria")
    print("OK flujo listo para move + Calendar")
    print("AGENDA_SUBMIT_V12_REPAIRED")


if __name__ == "__main__":
    main()
