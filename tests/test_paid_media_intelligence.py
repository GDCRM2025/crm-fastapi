import unittest
from datetime import date
from pathlib import Path

from backend.gd_intelligence.paid_media_intelligence import (
    assess_change_risk,
    assess_creative_fatigue,
    attribute_paid_media,
    classify_search_term,
    click_id_hash,
    click_ids_from_url,
)


ROOT = Path(__file__).resolve().parents[1]


class PaidMediaAttributionTests(unittest.TestCase):
    def test_click_ids_are_hashed_and_raw_value_is_not_returned(self):
        raw = "secret-looking-click-id-123"
        result = click_ids_from_url(f"https://example.test/?gclid={raw}")
        self.assertEqual(result["gclid"], click_id_hash(raw))
        self.assertNotIn(raw, str(result))

    def test_exact_click_match_beats_utm_match(self):
        session = {"landing_url": "https://example.test/?gclid=click-1", "utm_source": "google", "utm_campaign": "summer"}
        candidates = [
            {"platform": "GOOGLE_ADS", "account_id": 1, "campaign_external_id": "summer"},
            {"platform": "GOOGLE_ADS", "account_id": 2, "click_id_hash": click_id_hash("click-1"), "campaign_external_id": "other"},
        ]
        result = attribute_paid_media(session, candidates)
        self.assertEqual(result["method"], "EXACT")
        self.assertEqual(result["account_id"], 2)
        self.assertEqual(result["confidence"], 1.0)

    def test_strong_utm_and_ambiguous_candidates(self):
        session = {"utm_source": "instagram", "utm_campaign": "launch", "utm_content": "ad-7"}
        candidate = {"platform": "META_ADS", "account_id": 4, "campaign_name": "launch", "ad_external_id": "ad-7"}
        self.assertEqual(attribute_paid_media(session, [candidate])["method"], "STRONG")
        ambiguous = attribute_paid_media(session, [candidate, {**candidate, "account_id": 5}])
        self.assertEqual(ambiguous["method"], "UNKNOWN")

    def test_source_only_is_inferred_and_no_evidence_is_unknown(self):
        inferred = attribute_paid_media({"utm_source": "facebook"}, [{"platform": "META_ADS", "account_id": 2}])
        self.assertEqual(inferred["method"], "INFERRED")
        unknown = attribute_paid_media({}, [{"platform": "GOOGLE_ADS", "account_id": 1}])
        self.assertEqual(unknown["method"], "UNKNOWN")


class SearchTermTests(unittest.TestCase):
    def test_insufficient_high_value_waste_and_negative_candidate(self):
        insufficient = classify_search_term(spend=0.5, impressions=100, clicks=2, platform_conversions=0)
        self.assertEqual(insufficient["state"], "INSUFFICIENT_DATA")
        high = classify_search_term(spend=50, impressions=1000, clicks=20, platform_conversions=0, crm_sales=1, crm_revenue=500)
        self.assertEqual(high["state"], "HIGH_VALUE")
        waste = classify_search_term(spend=50, impressions=1000, clicks=10, platform_conversions=1, target_cpa=20)
        self.assertEqual(waste["state"], "WASTE")
        candidate = classify_search_term(spend=50, impressions=1000, clicks=20, platform_conversions=0)
        self.assertEqual(candidate["state"], "NEGATIVE_CANDIDATE")
        self.assertIn("revisar", candidate["reason"].lower())


class CreativeAndRiskTests(unittest.TestCase):
    def test_creative_fatigue_requires_volume_and_multiple_signals(self):
        low_data = assess_creative_fatigue(age_days=90, impressions=100, frequency=8, ctr_current=.01, ctr_previous=.02)
        self.assertEqual(low_data["state"], "INSUFFICIENT_DATA")
        likely = assess_creative_fatigue(
            age_days=45, impressions=10000, frequency=4, ctr_current=.008, ctr_previous=.012,
            cpa_crm_current=150, cpa_crm_previous=100,
            conversion_rate_current=.01, conversion_rate_previous=.02,
        )
        self.assertEqual(likely["state"], "FATIGUE_LIKELY")
        self.assertIn("CPA_CRM_INCREASE", likely["signals"])

    def test_change_risk_contract_has_reason_confidence_review_and_metrics(self):
        recent = assess_change_risk(hours_since_change=12, conversions_30d=100, learning=False, today=date(2026, 8, 10))
        self.assertEqual(recent["state"], "RECENT_CHANGE")
        self.assertEqual(recent["next_review_date"], date(2026, 8, 13))
        for field in ("reason", "confidence", "risk", "next_review_date", "metrics_to_watch"):
            self.assertIn(field, recent)

    def test_all_extended_change_states_are_reachable(self):
        cases = [
            (dict(hours_since_change=48, conversions_30d=100, learning=False), "COOLDOWN"),
            (dict(hours_since_change=100, conversions_30d=100, learning=True), "LEARNING"),
            (dict(hours_since_change=100, conversions_30d=3, learning=False), "LOW_DATA"),
            (dict(hours_since_change=100, conversions_30d=30, learning=False, limited_by_budget=True), "LIMITED_BY_BUDGET"),
            (dict(hours_since_change=100, conversions_30d=30, learning=False, evidence_complete=False), "INSUFFICIENT_EVIDENCE"),
            (dict(hours_since_change=100, conversions_30d=30, learning=False), "STABLE"),
        ]
        for kwargs, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(assess_change_risk(**kwargs)["state"], expected)

    def test_migration_is_additive_and_never_stores_raw_click_ids(self):
        sql = (ROOT / "migrations/2026_08_10_paid_media_intelligence.sql").read_text()
        upper = sql.upper()
        self.assertIn("CREATE TABLE IF NOT EXISTS PUBLIC.WI_PAID_MEDIA_ATTRIBUTION", upper)
        self.assertIn("ADD COLUMN IF NOT EXISTS GCLID_HASH", upper)
        self.assertNotIn(" GCLID VARCHAR", upper)
        self.assertNotIn(" FBCLID VARCHAR", upper)
        self.assertNotIn("DROP TABLE", upper)
        self.assertNotIn("DELETE FROM", upper)
        for state in ("EXACT", "STRONG", "INFERRED", "UNKNOWN", "NEGATIVE_CANDIDATE", "LIMITED_BY_BUDGET"):
            self.assertIn(state, upper)


if __name__ == "__main__":
    unittest.main()
