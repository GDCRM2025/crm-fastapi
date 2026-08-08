from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator


SITE_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_-]{1,15}$")


def normalize_domain(value: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        raise ValueError("domain es obligatorio")
    parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("domain debe ser un dominio HTTP(S) válido")
    host = parsed.hostname.rstrip(".")
    if "." not in host and host != "localhost":
        raise ValueError("domain debe incluir un nombre de dominio válido")
    return host


class SiteCreate(BaseModel):
    code: str = Field(min_length=2, max_length=16)
    name: str = Field(min_length=2, max_length=160)
    domain: str = Field(min_length=3, max_length=253)
    timezone: str = Field(default="America/Santiago", max_length=64)
    currency: str = Field(default="CLP", min_length=3, max_length=3)
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        code = str(value).strip().upper()
        if not SITE_CODE_RE.fullmatch(code):
            raise ValueError("code sólo admite A-Z, 0-9, guion y underscore")
        return code

    @field_validator("name", "timezone")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return str(value).strip()

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return str(value).strip().upper()

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, value: str) -> str:
        return normalize_domain(value)


class SiteUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    domain: str | None = Field(default=None, min_length=3, max_length=253)
    timezone: str | None = Field(default=None, max_length=64)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    enabled: bool | None = None
    config: dict[str, Any] | None = None

    @field_validator("name", "timezone")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        return str(value).strip() if value is not None else None

    @field_validator("currency")
    @classmethod
    def normalize_optional_currency(cls, value: str | None) -> str | None:
        return str(value).strip().upper() if value is not None else None

    @field_validator("domain")
    @classmethod
    def validate_optional_domain(cls, value: str | None) -> str | None:
        return normalize_domain(value) if value is not None else None


class UTMBuildRequest(BaseModel):
    site_id: int = Field(gt=0)
    url: str = Field(min_length=8, max_length=2048)
    utm_source: str = Field(min_length=1, max_length=160)
    utm_medium: str = Field(min_length=1, max_length=160)
    utm_campaign: str = Field(min_length=1, max_length=200)
    utm_term: str | None = Field(default=None, max_length=200)
    utm_content: str | None = Field(default=None, max_length=200)


class RolePermissionsUpdate(BaseModel):
    permissions: dict[str, bool | None]

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, value: dict[str, bool | None]) -> dict[str, bool | None]:
        if len(value) > 100:
            raise ValueError("Demasiados permisos en una sola actualización")
        return {str(key).strip(): allowed for key, allowed in value.items() if str(key).strip()}
