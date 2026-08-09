from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable


def _clamp(value: float, low: float = 0, high: float = 100) -> float:
    return max(low, min(high, float(value)))


def seo_opportunity_score(
    *,
    impressions: int | None = None,
    clicks: int | None = None,
    ctr: float | None = None,
    position: float | None = None,
    sessions: float | None = None,
    conversions: float | None = None,
    revenue: float | None = None,
    technical_score: float | None = None,
) -> dict[str, Any]:
    """Score available evidence only; unavailable sources never become zero-valued evidence."""
    components: dict[str, float] = {}
    if impressions is not None and position is not None:
        demand = _clamp((float(impressions) / 1000) * 35)
        rank_window = 100 if 4 <= float(position) <= 20 else 55 if float(position) < 4 else 35
        expected_ctr = 0.08 if float(position) <= 3 else 0.04 if float(position) <= 10 else 0.015
        actual_ctr = float(ctr) if ctr is not None else (float(clicks or 0) / float(impressions) if impressions else 0)
        ctr_gap = _clamp((expected_ctr - actual_ctr) / max(expected_ctr, 0.001) * 100)
        components["search_demand"] = demand * 0.45 + rank_window * 0.25 + ctr_gap * 0.30
    if sessions is not None:
        conversions_value = float(conversions or 0)
        rate = conversions_value / float(sessions) if sessions else 0
        revenue_signal = _clamp(float(revenue or 0) / 10000)
        components["business_value"] = _clamp(rate * 600 + revenue_signal * 0.4)
    if technical_score is not None:
        components["technical_gap"] = 100 - _clamp(float(technical_score))
    if not components:
        return {"score": 0, "state": "INSUFFICIENT_DATA", "components": {}, "reason": "No existen fuentes suficientes para calcular una oportunidad."}
    score = round(sum(components.values()) / len(components))
    if "search_demand" in components and "business_value" in components and "technical_gap" in components:
        state = "READY"
    elif set(components) == {"technical_gap"}:
        state = "TECHNICAL_ONLY"
    else:
        state = "PARTIAL"
    available = ", ".join(key.replace("_", " ") for key in components)
    return {"score": int(_clamp(score)), "state": state, "components": components, "reason": f"Score calculado sólo con evidencia disponible: {available}."}


def alert_key(category: str, entity_type: str, entity_id: Any, reason_code: str) -> str:
    raw = "|".join((str(category).upper(), str(entity_type).upper(), str(entity_id), str(reason_code).upper()))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def actionable_alerts(
    *,
    integrations: Iterable[dict[str, Any]] = (),
    site_health: Iterable[dict[str, Any]] = (),
    conversations: Iterable[dict[str, Any]] = (),
    ads: Iterable[dict[str, Any]] = (),
    seo: Iterable[dict[str, Any]] = (),
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    output: list[dict[str, Any]] = []

    def add(category: str, severity: str, entity_type: str, entity_id: Any, code: str, title: str, reason: str, action: str, target: str | None, evidence: dict[str, Any], cooldown_hours: int = 24):
        output.append({
            "alert_key": alert_key(category, entity_type, entity_id, code), "category": category,
            "severity": severity, "entity_type": entity_type, "entity_id": str(entity_id),
            "title": title, "reason": reason, "action_label": action, "action_target": target,
            "evidence": evidence, "next_review_at": now + timedelta(hours=cooldown_hours),
        })

    for item in integrations:
        status = str(item.get("status") or "").upper()
        if status in {"ERROR", "WARNING"}:
            add("INTEGRATION", "CRITICAL" if status == "ERROR" else "IMPORTANT", "integration", item.get("id"), status,
                f"{item.get('provider_name') or item.get('provider')} requiere atención", str(item.get("last_error_safe") or item.get("missing") or "La conexión necesita revisión."),
                "REVISAR INTEGRACIÓN", "#integrations", {"status": status, "last_verified_at": item.get("last_verified_at")})
    for item in site_health:
        status = str(item.get("status") or "").upper()
        if status in {"DOWN", "WARNING"}:
            add("SITE_HEALTH", "CRITICAL" if status == "DOWN" else "IMPORTANT", "site", item.get("site_id"), status,
                "Sitio sin disponibilidad" if status == "DOWN" else "Sitio con hallazgos técnicos", str(item.get("error_safe") or "Revisa los hallazgos priorizados de Site Health."),
                "VER SITE HEALTH", "#health", {"status": status, "http_status": item.get("http_status")}, 6 if status == "DOWN" else 24)
    for item in conversations:
        unattended = int(item.get("unattended_minutes") or 0)
        if unattended >= int(item.get("threshold_minutes") or 30):
            add("CONVERSATION", "CRITICAL" if unattended >= 120 else "IMPORTANT", "conversation", item.get("id"), "UNATTENDED",
                "Conversación pendiente de respuesta", f"El cliente lleva {unattended} minutos sin atención.", "ABRIR CONVERSACIÓN", item.get("action_target"),
                {"channel": item.get("channel"), "unattended_minutes": unattended}, 1)
    for item in ads:
        if str(item.get("risk") or "").upper() in {"HIGH", "CRITICAL"}:
            add("ADS", "CRITICAL" if str(item.get("risk")).upper() == "CRITICAL" else "IMPORTANT", "ad_entity", item.get("entity_id"), str(item.get("state") or "RISK"),
                "Riesgo detectado en Paid Media", str(item.get("reason") or "Las métricas requieren revisión humana."), "REVISAR EVIDENCIA", "#paid-media",
                {key: item.get(key) for key in ("state", "risk", "confidence", "metrics")}, 24)
    for item in seo:
        if int(item.get("score") or 0) >= 65:
            add("SEO", "IMPORTANT", "seo_opportunity", item.get("id") or item.get("opportunity_key"), "HIGH_SCORE",
                "Oportunidad SEO prioritaria", str(item.get("reason") or "Existe una oportunidad basada en las fuentes disponibles."), "VER OPORTUNIDAD", "#seo",
                {"score": item.get("score"), "state": item.get("state")}, 72)
    return output


def executive_readiness(*, source_freshness: dict[str, bool], minimum_sources: int = 3) -> dict[str, Any]:
    available = sorted(key for key, ready in source_freshness.items() if ready)
    missing = sorted(key for key, ready in source_freshness.items() if not ready)
    ready = len(available) >= minimum_sources and "crm_sales" in available
    return {"ready": ready, "state": "READY" if ready else "INSUFFICIENT_REAL_SOURCES", "available_sources": available, "missing_sources": missing}


AI_CONTEXT_FIELDS = {
    "executive": {"sales", "leads", "quotes", "conversations"},
    "marketing": {"sales", "leads", "quotes", "paid_media", "seo", "site_health", "alerts"},
    "admin": {"sales", "leads", "quotes", "conversations", "paid_media", "seo", "site_health", "alerts", "integrations"},
}


def safe_ai_context(role: str, requested: Iterable[str], services: dict[str, Any]) -> dict[str, Any]:
    normalized = str(role or "").lower().replace(" ", "_")
    group = "admin" if "admin" in normalized else "marketing" if "market" in normalized else "executive"
    allowed = AI_CONTEXT_FIELDS[group]
    selected = [key for key in requested if key in allowed and key in services]
    context = {key: services[key] for key in selected}
    return {"mode": "READ_ANALYZE_SUGGEST", "role_scope": group, "sources": selected, "context": context, "arbitrary_sql": False, "write_capability": False}


def evidence_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)
