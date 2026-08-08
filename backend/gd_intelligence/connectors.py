from __future__ import annotations

from datetime import date
from typing import Any, Iterable


def _number(value: Any, cast=float, default=0):
    try:
        return cast(value)
    except (TypeError, ValueError):
        return default


def parse_ga4_report(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten an Analytics Data API runReport response without losing dimensions."""
    dimensions = [str(item.get("name") or "") for item in payload.get("dimensionHeaders", [])]
    metrics = [str(item.get("name") or "") for item in payload.get("metricHeaders", [])]
    parsed: list[dict[str, Any]] = []
    for row in payload.get("rows", []) or []:
        item: dict[str, Any] = {}
        for name, value in zip(dimensions, row.get("dimensionValues", []) or []):
            item[name] = value.get("value")
        for name, value in zip(metrics, row.get("metricValues", []) or []):
            raw = value.get("value")
            item[name] = _number(raw, float, 0.0)
        parsed.append(item)
    return parsed


def parse_search_console_rows(payload: dict[str, Any], dimensions: Iterable[str]) -> list[dict[str, Any]]:
    names = list(dimensions)
    parsed: list[dict[str, Any]] = []
    for row in payload.get("rows", []) or []:
        keys = list(row.get("keys") or [])
        item = {name: keys[index] if index < len(keys) else None for index, name in enumerate(names)}
        item.update(
            {
                "clicks": _number(row.get("clicks"), int),
                "impressions": _number(row.get("impressions"), int),
                "ctr": _number(row.get("ctr"), float, 0.0),
                "position": _number(row.get("position"), float, 0.0),
            }
        )
        parsed.append(item)
    return parsed


def _audit_value(audits: dict[str, Any], key: str) -> float | None:
    value = (audits.get(key) or {}).get("numericValue")
    return _number(value, float, None) if value is not None else None


def parse_pagespeed(payload: dict[str, Any], strategy: str) -> dict[str, Any]:
    lighthouse = payload.get("lighthouseResult") or {}
    categories = lighthouse.get("categories") or {}
    audits = lighthouse.get("audits") or {}
    loading = payload.get("loadingExperience") or {}
    return {
        "strategy": str(strategy).upper(),
        "fetch_time": lighthouse.get("fetchTime"),
        "final_url": lighthouse.get("finalUrl"),
        "lab": {
            "performance": _number((categories.get("performance") or {}).get("score"), float, None),
            "accessibility": _number((categories.get("accessibility") or {}).get("score"), float, None),
            "best_practices": _number((categories.get("best-practices") or {}).get("score"), float, None),
            "seo": _number((categories.get("seo") or {}).get("score"), float, None),
            "lcp_ms": _audit_value(audits, "largest-contentful-paint"),
            "cls": _audit_value(audits, "cumulative-layout-shift"),
            "tbt_ms": _audit_value(audits, "total-blocking-time"),
            "fcp_ms": _audit_value(audits, "first-contentful-paint"),
            "speed_index_ms": _audit_value(audits, "speed-index"),
            "ttfb_ms": _audit_value(audits, "server-response-time"),
        },
        "field": parse_crux_metrics(loading),
    }


def parse_crux_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    metrics = payload.get("metrics") or {}
    output: dict[str, Any] = {}
    mapping = {
        "LARGEST_CONTENTFUL_PAINT_MS": "lcp_ms",
        "INTERACTION_TO_NEXT_PAINT": "inp_ms",
        "CUMULATIVE_LAYOUT_SHIFT_SCORE": "cls",
        "FIRST_CONTENTFUL_PAINT_MS": "fcp_ms",
        "EXPERIMENTAL_TIME_TO_FIRST_BYTE": "ttfb_ms",
    }
    for source, target in mapping.items():
        metric = metrics.get(source) or {}
        percentile = metric.get("percentile")
        if percentile is not None:
            output[target] = _number(percentile, float, None)
        if metric.get("category"):
            output[f"{target}_category"] = metric["category"]
    return output


def iso_date(value: Any) -> str:
    """Validate provider dates before persistence."""
    return date.fromisoformat(str(value)).isoformat()
