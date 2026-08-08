#!/usr/bin/env python3
"""Run one Site Health cycle. Schedule every 15 minutes with the existing scheduler."""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.database import engine
from backend.gd_intelligence.site_health import scan_site, store_result


LOCK_KEY = 8_082_026_15


def main() -> int:
    with engine.connect() as lock_conn:
        locked = bool(lock_conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_KEY}).scalar())
        if not locked:
            print("SITE_HEALTH_SKIPPED_ALREADY_RUNNING")
            return 0
        sites = lock_conn.execute(
            text("SELECT id,code,domain FROM public.wi_sites WHERE enabled ORDER BY code")
        ).mappings().all()
        try:
            for site in sites:
                result = scan_site(str(site["domain"]))
                with engine.begin() as conn:
                    store_result(conn, int(site["id"]), result, "scheduler")
                print(f"SITE_HEALTH {site['code']} {result['status']} {result.get('http_status') or '-'}")
            print(f"SITE_HEALTH_COMPLETE count={len(sites)}")
            return 0
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY})


if __name__ == "__main__":
    raise SystemExit(main())
