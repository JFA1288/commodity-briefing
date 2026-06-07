"""Orchestrator — run with: python -m src.main [--dry-run]"""

from __future__ import annotations

import argparse
import traceback
from datetime import datetime, timezone

from . import config
from .fetch_news import fetch_company_news, fetch_commodity_news, fetch_supplementary
from .fetch_prices import fetch_all as fetch_prices, fetch_macro, fetch_returns
from .fetch_fundamentals import fetch_all_fundamentals, build_news_fundamentals
from .process import dedup, filter_recent, filter_relevant, extract_regulatory
from .digest import build_digest
from .render import (
    write_latest_json,
    append_price_history,
    load_price_history,
    write_digest_html,
    update_archive_index,
)


def run(dry_run: bool = False) -> None:
    start = datetime.now(timezone.utc)
    print(f"\n{'='*60}")
    print(f"  SEA Commodity Trading — Sector Intelligence")
    print(f"  {start.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}\n")

    # ── 1. Prices ──────────────────────────────────────────────────────────────
    print("[1/5] Fetching commodity prices …")
    prices = []
    macro = None
    price_returns: dict = {}
    try:
        prices = fetch_prices()
        print(f"  → {len(prices)} commodities processed")
    except Exception:
        print("  ERROR fetching prices:")
        traceback.print_exc()

    try:
        cfg = config.load()
        price_returns = fetch_returns(cfg.get("commodities", []))
        print(f"  → {len(price_returns)} commodities with 1D/1W/1M returns")
    except Exception:
        print("  ERROR fetching price returns:")
        traceback.print_exc()

    try:
        macro = fetch_macro()
        print(f"  → {len(macro.tickers)} macro tickers fetched")
    except Exception:
        print("  ERROR fetching macro tickers:")
        traceback.print_exc()

    # ── 2. Fundamentals ────────────────────────────────────────────────────────
    print("\n[2/5] Fetching fundamentals …")
    eia_fundamentals = []
    try:
        eia_fundamentals = fetch_all_fundamentals()
        print(f"  → {len(eia_fundamentals)} EIA fundamentals items")
    except Exception:
        print("  ERROR fetching EIA fundamentals:")
        traceback.print_exc()

    # ── 3. News ────────────────────────────────────────────────────────────────
    print("\n[3/5] Fetching news …")
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

    # ── 4. Process ─────────────────────────────────────────────────────────────
    print("\n[4/5] Processing …")
    regulatory = []
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

        # Extract regulatory items from all processed news
        all_items = (
            [i for items in company_news.values() for i in items]
            + [i for items in commodity_news.values() for i in items]
            + supplementary
        )
        regulatory = extract_regulatory(all_items)

        # Add news-derived fundamentals
        news_fund = build_news_fundamentals(all_items)
        eia_fundamentals.extend(news_fund)

        total_items = (
            sum(len(v) for v in company_news.values())
            + sum(len(v) for v in commodity_news.values())
            + len(supplementary)
        )
        print(f"  → {total_items} items after dedup + filter")
        print(f"  → {len(regulatory)} regulatory items extracted")
    except Exception:
        print("  ERROR in processing:")
        traceback.print_exc()

    # ── 5. Digest + Render ────────────────────────────────────────────────────
    print("\n[5/5] Building digest and rendering …")
    try:
        # Load existing price history before writing today's data
        price_history = load_price_history()

        digest = build_digest(
            company_news,
            commodity_news,
            supplementary,
            prices,
            macro=macro,
            eia_fundamentals=eia_fundamentals,
            regulatory=regulatory,
            price_history=price_history,
            price_returns=price_returns,
        )
        print(
            f"  → {len(digest.top_headlines)} top headlines, "
            f"{len(digest.themes)} market themes, "
            f"{len(digest.risks)} risks, "
            f"{len(digest.events)} events"
        )

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
    parser = argparse.ArgumentParser(description="SEA Commodity Trading — Sector Intelligence Agent")
    parser.add_argument("--dry-run", action="store_true", help="Fetch data but skip writing output files")
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
