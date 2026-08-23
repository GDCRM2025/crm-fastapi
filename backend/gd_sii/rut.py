from __future__ import annotations

import re

from .exceptions import SupplierRUTInvalid


def calculate_dv(body: str) -> str:
    total = 0
    multiplier = 2
    for digit in reversed(body):
        total += int(digit) * multiplier
        multiplier = 2 if multiplier == 7 else multiplier + 1
    value = 11 - (total % 11)
    return "0" if value == 11 else "K" if value == 10 else str(value)


def normalize_chilean_rut(value: str, *, validate: bool = True) -> str:
    compact = re.sub(r"[^0-9kK]", "", str(value or "")).upper()
    if len(compact) < 2 or not compact[:-1].isdigit():
        raise SupplierRUTInvalid("RUT chileno invalido")
    body = compact[:-1].lstrip("0") or "0"
    dv = compact[-1]
    if validate and calculate_dv(body) != dv:
        raise SupplierRUTInvalid("Digito verificador de RUT invalido")
    return f"{body}-{dv}"


def split_rut(value: str) -> tuple[str, str]:
    normalized = normalize_chilean_rut(value)
    return tuple(normalized.split("-", 1))  # type: ignore[return-value]
