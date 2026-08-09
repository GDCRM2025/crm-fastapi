from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Iterable
from urllib.parse import parse_qs, urlsplit


ATTRIBUTION_METHODS = ("EXACT", "STRONG", "INFERRED", "UNKNOWN")
SEARCH_TERM_STATES = ("HIGH_VALUE", "WASTE", "NEGATIVE_CANDIDATE", "INSUFFICIENT_DATA")
CREATIVE_STATES = ("FATIGUE_LIKELY", "FATIGUE_POSSIBLE", "HEALTHY", "INSUFFICIENT_DATA")
CHANGE_STATES = (
    "STABLE", "LEARNING", "RECENT_CHANGE", "COOLDOWN", "LOW_DATA",
    "LIMITED_BY_BUDGET", "INSUFFICIENT_EVIDENCE",
)


def _number(value: Any) -> float:
    try:
        return float(Decimal(str(value or 0)))
    except (ValueError, TypeError):
        return 0.0


def click_id_hash(value: Any) -> str | None:
    """Return a stable pseudonymous key; raw advertising click IDs are never persisted."""
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 500:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def click_ids_from_url(url: Any) -> dict[str, str | None]:
    query = parse_qs(urlsplit(str(url or "")).query, keep_blank_values=False)
    return {name: click_id_hash((query.get(name) or [None])[0]) for name in ("gclid", "fbclid")}


def _platform_from_source(source: Any) -> str | None:
    value = str(source or "").strip().lower()
    if value in {"google", "googleads", "google_ads", "adwords"}:
        return "GOOGLE_ADS"
    if value in {"facebook", "instagram", "meta", "fb", "ig"}:
        return "META_ADS"
    return None


def attribute_paid_media(session: dict[str, Any], candidates: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Rank read-only Ads candidates using deterministic evidence, never time-only guessing."""
    landing_ids = click_ids_from_url(session.get("landing_url"))
    session_ids = {
        "GOOGLE_ADS": session.get("gclid_hash") or landing_ids["gclid"],
        "META_ADS": session.get("fbclid_hash") or landing_ids["fbclid"],
    }
    expected_platform = _platform_from_source(session.get("utm_source"))
    campaign = str(session.get("utm_campaign") or "").strip().casefold()
    content = str(session.get("utm_content") or "").strip().casefold()
    ranked: list[tuple[int, dict[str, Any], str, float, list[str]]] = []
    for candidate in candidates:
        platform = str(candidate.get("platform") or "").upper()
        candidate_click_hash = candidate.get("click_id_hash") or click_id_hash(candidate.get("click_id"))
        evidence: list[str] = []
        if session_ids.get(platform) and session_ids[platform] == candidate_click_hash:
            ranked.append((400, candidate, "EXACT", 1.0, ["CLICK_ID_HASH_MATCH"]))
            continue
        candidate_campaign = str(candidate.get("campaign_name") or candidate.get("campaign_external_id") or "").strip().casefold()
        candidate_content = str(candidate.get("utm_content") or candidate.get("ad_external_id") or "").strip().casefold()
        if expected_platform == platform:
            evidence.append("SOURCE_PLATFORM_MATCH")
        if campaign and campaign == candidate_campaign:
            evidence.append("CAMPAIGN_MATCH")
        if content and content == candidate_content:
            evidence.append("CONTENT_AD_MATCH")
        if len(evidence) >= 3:
            ranked.append((300, candidate, "STRONG", 0.9, evidence))
        elif "SOURCE_PLATFORM_MATCH" in evidence and "CAMPAIGN_MATCH" in evidence:
            ranked.append((250, candidate, "STRONG", 0.82, evidence))
        elif expected_platform == platform and not campaign:
            ranked.append((100, candidate, "INFERRED", 0.55, evidence))
    if not ranked:
        return {
            "method": "UNKNOWN", "confidence": 0.0, "platform": expected_platform,
            "account_id": None, "campaign_external_id": None, "ad_external_id": None,
            "reason": "No existe evidencia suficiente para relacionar la sesión con Ads.",
            "evidence": [],
        }
    ranked.sort(key=lambda item: item[0], reverse=True)
    best = ranked[0]
    if len(ranked) > 1 and ranked[1][0] == best[0] and best[2] != "EXACT":
        return {
            "method": "UNKNOWN", "confidence": 0.0, "platform": expected_platform,
            "account_id": None, "campaign_external_id": None, "ad_external_id": None,
            "reason": "Hay más de un candidato con la misma evidencia; no se atribuye automáticamente.",
            "evidence": ["AMBIGUOUS_CANDIDATES"],
        }
    candidate, method, confidence, evidence = best[1], best[2], best[3], best[4]
    return {
        "method": method, "confidence": confidence, "platform": str(candidate.get("platform") or "").upper(),
        "account_id": candidate.get("account_id"),
        "campaign_external_id": candidate.get("campaign_external_id"),
        "ad_external_id": candidate.get("ad_external_id"),
        "reason": {
            "EXACT": "El identificador de clic coincide de forma exacta.",
            "STRONG": "Fuente, campaña y señales del anuncio son consistentes.",
            "INFERRED": "Sólo la fuente permite inferir la plataforma; requiere cautela.",
        }[method],
        "evidence": evidence,
    }


def classify_search_term(
    *, spend: Any, impressions: Any, clicks: Any, platform_conversions: Any,
    crm_leads: Any = 0, crm_sales: Any = 0, crm_revenue: Any = 0,
    min_clicks: int = 8, min_spend: float = 1, target_cpa: float | None = None,
) -> dict[str, Any]:
    spend_f, impressions_f, clicks_f = _number(spend), _number(impressions), _number(clicks)
    conversions, leads, sales, revenue = map(_number, (platform_conversions, crm_leads, crm_sales, crm_revenue))
    metrics = {"spend": spend_f, "impressions": impressions_f, "clicks": clicks_f,
               "platform_conversions": conversions, "crm_leads": leads, "crm_sales": sales,
               "crm_revenue": revenue}
    if clicks_f < min_clicks or spend_f < min_spend:
        return {"state": "INSUFFICIENT_DATA", "confidence": 0.25, "reason": "Aún no alcanza el mínimo de clics y gasto configurado.", "metrics": metrics}
    if sales > 0 or revenue > 0 or leads > 0:
        return {"state": "HIGH_VALUE", "confidence": 0.95 if sales > 0 else 0.85,
                "reason": "Existe resultado CRM atribuible al término.", "metrics": metrics}
    if conversions > 0:
        cpa = spend_f / conversions
        if target_cpa is None or cpa <= target_cpa:
            metrics["platform_cpa"] = cpa
            return {"state": "HIGH_VALUE", "confidence": 0.7,
                    "reason": "La plataforma registra conversiones; falta confirmar su resultado en CRM.", "metrics": metrics}
        metrics["platform_cpa"] = cpa
        return {"state": "WASTE", "confidence": 0.75,
                "reason": "El CPA supera el objetivo configurado y no existe resultado CRM.", "metrics": metrics}
    if clicks_f >= max(min_clicks * 2, 15):
        return {"state": "NEGATIVE_CANDIDATE", "confidence": 0.85,
                "reason": "Acumula suficiente interacción sin conversiones ni resultados CRM; revisar antes de excluir.", "metrics": metrics}
    return {"state": "WASTE", "confidence": 0.65,
            "reason": "Consume gasto sin resultados observables; requiere revisión, no exclusión automática.", "metrics": metrics}


def assess_creative_fatigue(
    *, age_days: int, impressions: Any, frequency: Any, ctr_current: Any, ctr_previous: Any,
    cpa_crm_current: Any = None, cpa_crm_previous: Any = None,
    conversion_rate_current: Any = None, conversion_rate_previous: Any = None,
    min_impressions: int = 1000, frequency_threshold: float = 3.0,
) -> dict[str, Any]:
    metrics = {"age_days": int(age_days), "impressions": _number(impressions), "frequency": _number(frequency),
               "ctr_current": _number(ctr_current), "ctr_previous": _number(ctr_previous),
               "cpa_crm_current": _number(cpa_crm_current), "cpa_crm_previous": _number(cpa_crm_previous),
               "conversion_rate_current": _number(conversion_rate_current),
               "conversion_rate_previous": _number(conversion_rate_previous)}
    if metrics["impressions"] < min_impressions or metrics["ctr_previous"] <= 0:
        return {"state": "INSUFFICIENT_DATA", "confidence": 0.2,
                "reason": "No hay volumen o periodo comparativo suficiente.", "signals": [], "metrics": metrics}
    signals: list[str] = []
    if metrics["frequency"] >= frequency_threshold:
        signals.append("HIGH_FREQUENCY")
    if metrics["ctr_current"] <= metrics["ctr_previous"] * 0.8:
        signals.append("CTR_DECLINE")
    if metrics["cpa_crm_previous"] > 0 and metrics["cpa_crm_current"] >= metrics["cpa_crm_previous"] * 1.2:
        signals.append("CPA_CRM_INCREASE")
    if metrics["conversion_rate_previous"] > 0 and metrics["conversion_rate_current"] <= metrics["conversion_rate_previous"] * 0.8:
        signals.append("CONVERSION_DECLINE")
    if age_days >= 30:
        signals.append("CREATIVE_AGE")
    performance_signals = len(set(signals) & {"CTR_DECLINE", "CPA_CRM_INCREASE", "CONVERSION_DECLINE"})
    if "HIGH_FREQUENCY" in signals and performance_signals >= 2 and "CREATIVE_AGE" in signals:
        return {"state": "FATIGUE_LIKELY", "confidence": min(0.95, 0.65 + len(signals) * 0.06),
                "reason": "Frecuencia alta y deterioro sostenido de resultados sugieren fatiga.", "signals": signals, "metrics": metrics}
    if "HIGH_FREQUENCY" in signals and performance_signals >= 1:
        return {"state": "FATIGUE_POSSIBLE", "confidence": 0.7,
                "reason": "Hay señales compatibles con fatiga; observar antes de cambiar.", "signals": signals, "metrics": metrics}
    return {"state": "HEALTHY", "confidence": 0.7,
            "reason": "No se observa una combinación suficiente de frecuencia y deterioro.", "signals": signals, "metrics": metrics}


def assess_change_risk(
    *, hours_since_change: float | None, conversions_30d: Any, learning: bool,
    cooldown_hours: float = 72, limited_by_budget: bool = False,
    evidence_complete: bool = True, budget_change_pct: Any = 0, today: date | None = None,
) -> dict[str, Any]:
    base_date = today or date.today()
    metrics = {"hours_since_change": hours_since_change, "conversions_30d": _number(conversions_30d),
               "budget_change_pct": _number(budget_change_pct), "learning": bool(learning),
               "limited_by_budget": bool(limited_by_budget)}
    if not evidence_complete:
        state, risk, confidence, wait_hours = "INSUFFICIENT_EVIDENCE", "HIGH", 0.2, 24
        reason = "Faltan métricas autorizadas para evaluar el cambio con seguridad."
    elif hours_since_change is not None and hours_since_change < cooldown_hours:
        state, risk, confidence = ("RECENT_CHANGE" if hours_since_change < 24 else "COOLDOWN"), "HIGH", 0.95
        wait_hours = max(1, cooldown_hours - hours_since_change)
        reason = "Existe un cambio reciente; corresponde observar hasta completar el periodo de enfriamiento."
    elif learning:
        state, risk, confidence, wait_hours = "LEARNING", "HIGH", 0.95, 72
        reason = "La plataforma reporta aprendizaje activo; no conviene encadenar modificaciones."
    elif _number(conversions_30d) < 15:
        state, risk, confidence, wait_hours = "LOW_DATA", "MEDIUM", 0.8, 168
        reason = "El volumen de conversiones no permite separar señal de variación normal."
    elif limited_by_budget:
        state, risk, confidence, wait_hours = "LIMITED_BY_BUDGET", "MEDIUM", 0.85, 72
        reason = "La entrega está limitada por presupuesto; revisar rentabilidad antes de recomendar escala."
    else:
        state, confidence, wait_hours = "STABLE", 0.85, 72
        risk = "HIGH" if abs(_number(budget_change_pct)) > 30 else "LOW"
        reason = "La entidad cuenta con evidencia estable para análisis." if risk == "LOW" else "El cambio presupuestario propuesto supera el umbral configurado."
    return {
        "state": state, "risk": risk, "confidence": confidence, "reason": reason,
        "recommendation": "OBSERVE" if state != "STABLE" else ("REVIEW_REQUIRED" if risk == "HIGH" else "ANALYZE"),
        "next_review_date": base_date + timedelta(days=max(1, int((wait_hours + 23) // 24))),
        "metrics_to_watch": ["spend", "conversions", "cpa_crm", "roas_crm"], "metrics": metrics,
    }
