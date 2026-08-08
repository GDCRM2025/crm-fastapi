from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


UTM_FIELDS = ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content")


def build_utm_url(base_url: str, **values: str | None) -> str:
    parsed = urlsplit(str(base_url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("La URL debe ser HTTP(S) y absoluta.")
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for field in UTM_FIELDS:
        value = str(values.get(field) or "").strip()
        if value:
            query[field] = value
        else:
            query.pop(field, None)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def campaign_identifier(site_code: str, sequence: int, at: datetime | None = None) -> str:
    code = re.sub(r"[^A-Z0-9]", "", str(site_code).upper())[:8]
    if not code or sequence < 1:
        raise ValueError("Código de sitio y secuencia deben ser válidos.")
    moment = at or datetime.now()
    return f"GD-{code}-MKT-{moment.year}-{sequence:05d}"
