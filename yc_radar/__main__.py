"""yc-radar — daily hiring and growth signal for recent YC batches.

    python -m yc_radar --batches "Winter 2025,Spring 2025,Summer 2025"
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

from . import algolia, digest, founders as founders_mod, news
from .store import Store

DEFAULT_BATCHES = [
    "Summer 2024", "Fall 2024",
    "Winter 2025", "Spring 2025", "Summer 2025", "Fall 2025",
    "Winter 2026", "Spring 2026", "Summer 2026",
]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="yc-radar")
    p.add_argument("--batches", help="comma-separated, e.g. 'Winter 2025,Summer 2025'")
    p.add_argument("--db", default="yc_radar.db")
    p.add_argument("--out", default="digests")
    p.add_argument("--skip-founders", action="store_true",
                   help="skip per-company profile fetches (much faster)")
    p.add_argument("--max-founder-fetches", type=int, default=150,
                   help="cap profile fetches per run to keep the crawl polite")
    p.add_argument("--skip-funding-news", action="store_true",
                   help="skip the Google News funding sweep")
    p.add_argument("--max-funding-checks", type=int, default=200,
                   help="cap company funding-news lookups per run (rotates "
                        "oldest-checked-first, so coverage still completes "
                        "over a few days)")
    args = p.parse_args(argv)

    batches = [b.strip() for b in args.batches.split(",")] if args.batches else DEFAULT_BATCHES

    session = algolia.make_session()
    creds = algolia.discover_creds(session)
    print(f"[creds] app={creds.app_id} source={creds.source}", file=sys.stderr)

    index = algolia.YCIndex(creds, session)
    store = Store(args.db)

    print(f"[yc] sweeping {len(batches)} batches...", file=sys.stderr)
    known_before = store.known_company_ids()
    companies = index.companies(batches)
    print(f"[yc] {len(companies)} companies", file=sys.stderr)
    company_delta = store.sync_companies(companies)

    new_founders = []
    if not args.skip_founders:
        # Only enrich companies we haven't seen before -- founder rosters are
        # near-static, so re-crawling 2,000 profiles daily buys nothing.
        targets = [c for c in companies if c["id"] not in known_before and c["yc_url"]]
        targets = targets[: args.max_founder_fetches]
        print(f"[yc] enriching {len(targets)} new company profiles...", file=sys.stderr)
        for c in targets:
            found = founders_mod.fetch_founders(session, c["yc_url"])
            new_founders.extend(store.sync_founders(c["id"], found))

    print("[yc] sweeping job board...", file=sys.stderr)
    try:
        jobs = index.jobs(batches)
        job_delta = store.sync_jobs(jobs)
    except Exception as e:  # job index is the flakiest surface
        print(f"[warn] job sweep failed: {e}", file=sys.stderr)
        job_delta = {"new": [], "closed": []}

    new_funding_mentions = []
    if not args.skip_funding_news:
        # Not a YC data source -- see news.py. Rotates through companies
        # oldest-checked-first so a capped run still reaches everyone
        # eventually instead of favoring alphabetically-early names.
        due = store.companies_due_for_funding_check(args.max_funding_checks)
        print(f"[yc] checking funding news for {len(due)} companies...", file=sys.stderr)
        for c in due:
            mentions = news.search_funding_news(session, c["name"])
            new_funding_mentions.extend(store.sync_funding_mentions(c["id"], c["name"], mentions))
            store.mark_funding_checked(c["id"])
            time.sleep(0.5)  # be a polite guest

    md = digest.render(company_delta, job_delta, new_founders, batches, new_funding_mentions)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{date.today():%Y-%m-%d}.md").write_text(md)
    (out_dir / "latest.md").write_text(md)
    store.close()

    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
