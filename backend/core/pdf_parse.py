from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass
class PdfParseResult:
    ok: bool
    used: str | None = None
    text: str = ""
    error: str | None = None


def _run_pdftotext(pdf_path: Path) -> PdfParseResult:
    """
    Extrae texto usando `pdftotext` (poppler-utils). Es el camino más liviano en servidor.
    Retorna texto vacío si el PDF no tiene capa de texto (escaneado).
    """
    exe = shutil.which("pdftotext")
    if not exe:
        return PdfParseResult(ok=False, error="pdftotext no está instalado en el servidor")
    try:
        # -layout preserva columnas lo mejor posible.
        p = subprocess.run(
            [exe, "-layout", str(pdf_path), "-"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if p.returncode != 0:
            err = (p.stderr or "").strip() or f"pdftotext exit={p.returncode}"
            return PdfParseResult(ok=False, used="pdftotext", error=err)
        return PdfParseResult(ok=True, used="pdftotext", text=p.stdout or "")
    except Exception as e:
        return PdfParseResult(ok=False, used="pdftotext", error=str(e))


def extract_text(pdf_path: Path) -> PdfParseResult:
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        return PdfParseResult(ok=False, error="PDF no existe")
    return _run_pdftotext(pdf_path)


def _norm_qty(raw: str) -> float:
    s = (raw or "").strip().replace(".", "").replace(",", ".")
    try:
        return float(s)
    except Exception:
        return 0.0


_RE_LINE = re.compile(
    r"^\s*(?P<qty>\d{1,5}(?:[.,]\d{1,3})?)\s*(?:x|X)?\s+(?P<name>[^$]{3,120})\s*$"
)


def parse_items_from_text(text: str) -> list[dict]:
    """
    Heurística simple:
    - líneas que empiezan con cantidad + nombre
    - evita líneas con $ (precios)
    """
    out: list[dict] = []
    if not text:
        return out
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or "$" in ln:
            continue
        m = _RE_LINE.match(ln)
        if not m:
            continue
        qty = _norm_qty(m.group("qty"))
        if qty <= 0:
            continue
        name = m.group("name").strip()
        # limpieza suave
        name = re.sub(r"\s{2,}", " ", name)
        # evita capturar cosas como "TOTAL" "SUBTOTAL"
        if name.upper().startswith(("TOTAL", "SUBTOTAL", "IVA", "NETO")):
            continue
        out.append({"producto": name, "cantidad": qty, "source": "pdf"})
    # merge por producto exacto
    merged: dict[str, float] = {}
    for it in out:
        key = it["producto"]
        merged[key] = merged.get(key, 0.0) + float(it["cantidad"] or 0)
    items = [{"producto": k, "cantidad": v, "source": "pdf"} for k, v in merged.items() if v > 0]
    # orden por cantidad desc para que sea legible en preview
    items.sort(key=lambda x: (-float(x["cantidad"]), x["producto"]))
    return items[:200]


def sample_lines(text: str, n: int = 40) -> list[str]:
    lines = []
    for ln in (text or "").splitlines():
        ln = ln.strip()
        if ln:
            lines.append(ln)
        if len(lines) >= n:
            break
    return lines

