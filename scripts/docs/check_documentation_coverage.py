#!/usr/bin/env python3
"""Report catalog/documentation gaps; strict mode is suitable for a future CI gate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "docs/user-guide/source/screen_catalog.json"
CONTEXTUAL = {
    "leads_ver", "gdi_utm", "gdi_integrations", "gdi_paid_media", "tool_wapp",
    "gdi_search_terms", "gdi_seo", "gdi_alerts", "gdi_executive", "tool_inbox",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true", help="fail while any discovered screen lacks a specific contextual article")
    args = parser.parse_args()
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    missing_files = [x["id"] for x in data["screens"] if not x["exists"]]
    missing_articles = sorted({x["id"] for x in data["screens"] if x["id"] not in CONTEXTUAL})
    print(f"DOC_COVERAGE screens={len(data['screens'])} routes={len(data['routes'])} missing_files={len(missing_files)} contextual_pending={len(missing_articles)}")
    if missing_files: print("MISSING_FILES=" + ",".join(sorted(set(missing_files))))
    if missing_articles: print("CONTEXTUAL_PENDING=" + ",".join(missing_articles))
    return 1 if args.strict and (missing_files or missing_articles) else 0


if __name__ == "__main__": raise SystemExit(main())
