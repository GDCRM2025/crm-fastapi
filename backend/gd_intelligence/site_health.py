from __future__ import annotations

import ipaddress
import json
import socket
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests
from sqlalchemy import text


USER_AGENT = "GreenDiamond-SiteHealth/1.0"
MAX_HTML_BYTES = 2_000_000
MAX_INTERNAL_LINKS = 12
MAX_REDIRECTS = 5


class PageSignals(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = False
        self.h1 = False
        self.meta_description = False
        self.viewport = False
        self.canonical = False
        self.schema_org = False
        self.links: list[str] = []
        self.resources: list[str] = []
        self.missing_alt_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {str(key).lower(): str(value or "") for key, value in attrs}
        lower = tag.lower()
        if lower == "title":
            self.title = True
        elif lower == "h1":
            self.h1 = True
        elif lower == "meta":
            name = values.get("name", "").lower()
            if name == "description" and values.get("content", "").strip():
                self.meta_description = True
            if name == "viewport" and values.get("content", "").strip():
                self.viewport = True
        elif lower == "link":
            rel = values.get("rel", "").lower().split()
            href = values.get("href", "").strip()
            if "canonical" in rel and href:
                self.canonical = True
            if href:
                self.resources.append(href)
        elif lower == "a":
            href = values.get("href", "").strip()
            if href:
                self.links.append(href)
        elif lower == "img":
            src = values.get("src", "").strip()
            if src:
                self.resources.append(src)
            if not values.get("alt", "").strip():
                self.missing_alt_count += 1
        elif lower in {"script", "iframe", "source", "video", "audio"}:
            src = values.get("src", "").strip()
            if src:
                self.resources.append(src)
        if "itemscope" in values or "schema.org" in values.get("itemtype", "").lower():
            self.schema_org = True

    def handle_data(self, data: str) -> None:
        if "schema.org" in str(data).lower():
            self.schema_org = True


def _assert_public_host(hostname: str) -> None:
    infos = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    if not infos:
        raise ValueError("El dominio no resolvió DNS")
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise ValueError("El dominio resuelve a una red no pública")


def _assert_public_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL de Site Health no permitida")
    _assert_public_host(parsed.hostname)


def _request(
    session: requests.Session,
    method: str,
    url: str,
    *,
    timeout: float,
    stream: bool = False,
) -> requests.Response:
    current = url
    history: list[requests.Response] = []
    for _ in range(MAX_REDIRECTS + 1):
        _assert_public_url(current)
        response = session.request(
            method,
            current,
            timeout=timeout,
            allow_redirects=False,
            stream=stream,
        )
        if response.status_code not in {301, 302, 303, 307, 308}:
            response.history = history
            return response
        location = response.headers.get("location")
        if not location:
            return response
        history.append(response)
        response.close()
        current = urljoin(current, location)
        if response.status_code == 303:
            method = "GET"
    raise ValueError("Demasiados redirects en Site Health")


def _get(session: requests.Session, url: str, timeout: float = 8.0) -> requests.Response:
    response = _request(session, "GET", url, timeout=timeout, stream=True)
    response.raw.decode_content = True
    return response


def _small_body(response: requests.Response) -> str:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=65536):
        total += len(chunk)
        if total > MAX_HTML_BYTES:
            break
        chunks.append(chunk)
    raw = b"".join(chunks)
    encoding = response.encoding or "utf-8"
    return raw.decode(encoding, "replace")


def scan_site(domain: str) -> dict[str, Any]:
    host = str(domain or "").strip().lower().rstrip(".")
    _assert_public_host(host)
    started_at = datetime.now(timezone.utc)
    started = time.monotonic()
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    result: dict[str, Any] = {
        "url": f"https://{host}/",
        "status": "DOWN",
        "http_status": None,
        "response_ms": None,
        "https_ok": False,
        "robots_ok": False,
        "sitemap_ok": False,
        "canonical_ok": False,
        "title_ok": False,
        "meta_description_ok": False,
        "h1_ok": False,
        "viewport_ok": False,
        "schema_org_ok": False,
        "not_found_ok": False,
        "redirect_count": 0,
        "broken_internal_links": 0,
        "mixed_content_count": 0,
        "missing_alt_count": 0,
        "error_safe": None,
        "details": {},
    }
    try:
        request_started = time.monotonic()
        response = _get(session, result["url"])
        result["response_ms"] = round((time.monotonic() - request_started) * 1000)
        result["http_status"] = int(response.status_code)
        result["https_ok"] = urlsplit(response.url).scheme == "https"
        result["redirect_count"] = len(response.history)
        result["url"] = response.url
        body = _small_body(response) if "html" in response.headers.get("content-type", "").lower() else ""
        parser = PageSignals()
        parser.feed(body)
        result.update(
            {
                "canonical_ok": parser.canonical,
                "title_ok": parser.title,
                "meta_description_ok": parser.meta_description,
                "h1_ok": parser.h1,
                "viewport_ok": parser.viewport,
                "schema_org_ok": parser.schema_org,
                "missing_alt_count": parser.missing_alt_count,
            }
        )
        result["mixed_content_count"] = sum(
            1 for item in parser.resources if str(item).strip().lower().startswith("http://")
        ) if result["https_ok"] else 0

        base_host = urlsplit(response.url).hostname
        internal: list[str] = []
        for href in parser.links:
            absolute = urljoin(response.url, href)
            parsed = urlsplit(absolute)
            if parsed.scheme in {"http", "https"} and parsed.hostname == base_host:
                clean = parsed._replace(fragment="").geturl()
                if clean not in internal:
                    internal.append(clean)
            if len(internal) >= MAX_INTERNAL_LINKS:
                break
        broken = 0
        checked: list[dict[str, Any]] = []
        for link in internal:
            try:
                link_response = _request(session, "HEAD", link, timeout=4)
                if link_response.status_code in {405, 501}:
                    link_response = _request(session, "GET", link, timeout=4, stream=True)
                code = int(link_response.status_code)
                if code >= 400:
                    broken += 1
                checked.append({"url": link, "status": code})
            except requests.RequestException:
                broken += 1
                checked.append({"url": link, "status": None})
        result["broken_internal_links"] = broken
        result["details"]["internal_links_checked"] = checked

        for key, path in (("robots_ok", "/robots.txt"), ("sitemap_ok", "/sitemap.xml")):
            try:
                check = _request(session, "GET", urljoin(response.url, path), timeout=5)
                result[key] = check.status_code < 400
                result["details"][path] = int(check.status_code)
            except requests.RequestException:
                result[key] = False
        try:
            missing = _request(session, "GET", urljoin(response.url, "/gd-health-not-found-check-404"), timeout=5)
            result["not_found_ok"] = missing.status_code in {404, 410}
            result["details"]["not_found_status"] = int(missing.status_code)
        except requests.RequestException:
            result["not_found_ok"] = False

        critical = (
            result["http_status"] == 200
            and result["https_ok"]
            and result["title_ok"]
            and result["h1_ok"]
        )
        healthy = all(
            result[key]
            for key in (
                "robots_ok",
                "sitemap_ok",
                "canonical_ok",
                "meta_description_ok",
                "viewport_ok",
                "not_found_ok",
            )
        ) and not any(
            result[key] for key in ("broken_internal_links", "mixed_content_count", "missing_alt_count")
        )
        result["status"] = "ONLINE" if critical and healthy else "WARNING" if result["http_status"] else "DOWN"
    except (requests.RequestException, OSError, ValueError) as exc:
        result["error_safe"] = f"{type(exc).__name__}: {str(exc)[:240]}"
        result["status"] = "DOWN"
    finally:
        session.close()
    finished_at = datetime.now(timezone.utc)
    result["started_at"] = started_at
    result["finished_at"] = finished_at
    result["duration_ms"] = round((time.monotonic() - started) * 1000)
    return result


def store_result(conn, site_id: int, result: dict[str, Any], actor: str) -> dict[str, Any]:
    row = conn.execute(
        text(
            """
            INSERT INTO public.wi_site_health_runs(
              site_id,url,status,http_status,response_ms,https_ok,robots_ok,sitemap_ok,
              canonical_ok,title_ok,meta_description_ok,h1_ok,viewport_ok,schema_org_ok,
              not_found_ok,redirect_count,broken_internal_links,mixed_content_count,
              missing_alt_count,started_at,finished_at,duration_ms,error_safe,result,created_by
            ) VALUES (
              :site_id,:url,:status,:http_status,:response_ms,:https_ok,:robots_ok,:sitemap_ok,
              :canonical_ok,:title_ok,:meta_description_ok,:h1_ok,:viewport_ok,:schema_org_ok,
              :not_found_ok,:redirect_count,:broken_internal_links,:mixed_content_count,
              :missing_alt_count,:started_at,:finished_at,:duration_ms,:error_safe,CAST(:result AS jsonb),:actor
            )
            RETURNING id,site_id,url,status,http_status,response_ms,https_ok,robots_ok,sitemap_ok,
                      canonical_ok,title_ok,meta_description_ok,h1_ok,viewport_ok,schema_org_ok,
                      not_found_ok,redirect_count,broken_internal_links,mixed_content_count,
                      missing_alt_count,started_at,finished_at,duration_ms,error_safe
            """
        ),
        {
            **{key: result.get(key) for key in (
                "url", "status", "http_status", "response_ms", "https_ok", "robots_ok", "sitemap_ok",
                "canonical_ok", "title_ok", "meta_description_ok", "h1_ok", "viewport_ok", "schema_org_ok",
                "not_found_ok", "redirect_count", "broken_internal_links", "mixed_content_count",
                "missing_alt_count", "started_at", "finished_at", "duration_ms", "error_safe",
            )},
            "site_id": site_id,
            "result": json.dumps(result.get("details") or {}, ensure_ascii=False),
            "actor": actor,
        },
    ).mappings().one()
    return dict(row)


def latest_results(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            """
            SELECT DISTINCT ON (s.id)
              s.id AS site_id,s.code,s.name,s.domain,
              h.id,h.url,h.status,h.http_status,h.response_ms,h.https_ok,
              h.robots_ok,h.sitemap_ok,h.canonical_ok,h.title_ok,
              h.meta_description_ok,h.h1_ok,h.viewport_ok,h.schema_org_ok,
              h.not_found_ok,h.redirect_count,h.broken_internal_links,
              h.mixed_content_count,h.missing_alt_count,h.started_at,
              h.finished_at,h.duration_ms,h.error_safe
            FROM public.wi_sites s
            LEFT JOIN public.wi_site_health_runs h ON h.site_id=s.id
            WHERE s.enabled
            ORDER BY s.id,h.started_at DESC NULLS LAST
            """
        )
    ).mappings()
    return [dict(row) for row in rows]


def history(conn, site_id: int, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            """
            SELECT h.*,s.code,s.name,s.domain
            FROM public.wi_site_health_runs h
            JOIN public.wi_sites s ON s.id=h.site_id
            WHERE h.site_id=:site_id
            ORDER BY h.started_at DESC
            LIMIT :limit
            """
        ),
        {"site_id": site_id, "limit": min(max(limit, 1), 500)},
    ).mappings()
    return [dict(row) for row in rows]
