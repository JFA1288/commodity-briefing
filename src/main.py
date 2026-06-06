"""Orchestrator — run with: python -m src.main [--dry-run]"""

from __future__ import annotations

import argparse
import sys
import traceback
from datetime import datetime, timezone

from . import config
from .fetch_news import fetch_company_news, fetch_commodity_news, fetch_supplementary
from .fetch_prices import fetch_all as fetch_prices
from .process import dedup, filter_recent, filter_relevant
from .digest import build_digest
from .render import write_latest_json, append_price_history, write_digest_html, update_archive_index


def run(dry_run: bool = False) -> None:
    start = datetime.now(timezone.utc)
    print(f"\n{'='*60}")
    print(f"  SEA Commodity Briefing — {start.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}\n")

    # ── 1. Prices ──────────────────────────────────────────────────────────
    print("[1/4] Fetching commodity prices …")
    try:
        prices = fetch_prices()
        print(f"  → {len(prices)} commodities processed")
    except Exception:
        print("  ERROR fetching prices:")
        traceback.print_exc()
        prices = []

    # ── 2. News ────────────────────────────────────────────────────────────
    print("\n[2/4] Fetching news …")
    company_news: dict = {}
    commodity_news: dict = {}
    supplementary: list = []

    try:
        company_news = fetch_company_news()
        raw_count = sum(len(v) for v in company_news.values())
        print(f"  → {raw_count} raw company items across {len(company_news)} companies")
    except Exception:
        print("  ERROR fetching company news:")
        traceback.print_exc()

    try:
        commodity_news = fetch_commodity_news()
        raw_count = sum(len(v) for v in commodity_news.values())
        print(f"  → {raw_count} raw commodity items across {len(commodity_news)} groups")
    except Exception:
        print("  ERROR fetching commodity news:")
        traceback.print_exc()

    try:
        supplementary = fetch_supplementary()
        print(f"  → {len(supplementary)} supplementary items")
    except Exception:
        print("  ERROR fetching supplementary feeds:")
        traceback.print_exc()

    # ── 3. Process ─────────────────────────────────────────────────────────
    print("\n[3/4] Processing …")
    try:
        lookback = config.get("news", {}).get("lookback_hours", 36)

        for name in list(company_news.keys()):
            items = company_news[name]
            items = dedup(filter_recent(filter_relevant(items), lookback))
            company_news[name] = items

        for group in list(commodity_news.keys()):
            items = commodity_news[group]
            items = dedup(filter_recent(filter_relevant(items), lookback))
            commodity_news[group] = items

        supplementary = dedup(filter_recent(filter_relevant(supplementary), lookback))

        total_items = (
            sum(len(v) for v in company_news.values())
            + sum(len(v) for v in commodity_news.values())
            + len(supplementary)
        )
        print(f"  → {total_items} items after dedup + filter")
    except Exception:
        print("  ERROR in processing:")
        traceback.print_exc()

    # ── 4. Digest + Render ────────────────────────────────────────────────
    print("\n[4/4] Building digest and rendering …")
    try:
        digest = build_digest(company_news, commodity_news, supplementary, prices)
        print(f"  → {len(digest.top_headlines)} top headlines, {len(digest.what_to_watch)} watch bullets")

        if not dry_run:
            write_latest_json(digest)
            append_price_history(prices)
            write_digest_html(digest)
            update_archive_index()
        else:
            print("  [dry-run] skipping file writes")
    except Exception:
        print("  ERROR in digest/render:")
        traceback.print_exc()

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    print(f"\nDone in {elapsed:.1f}s\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="SEA Commodity Briefing Agent")
    parser.add_argument("--dry-run", action="store_true", help="Fetch data but skip writing output files")
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
