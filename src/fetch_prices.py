"""Fetch commodity prices via yfinance (free) with web-fallback for missing tickers."""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
import yfinance as yf

from . import config
from .models import PriceRecord

_cfg = config.load


def _yf_price(ticker: str, multiplier: float = 1.0) -> tuple[Optional[float], Optional[float], Optional[datetime]]:
    """Return (price, prev_close, as_of) or (None, None, None) on failure."""
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="5d", auto_adjust=True)
        if hist.empty or len(hist) < 1:
            return None, None, None
        price = float(hist["Close"].iloc[-1]) * multiplier
        prev_close = float(hist["Close"].iloc[-2]) * multiplier if len(hist) >= 2 else None
        as_of = hist.index[-1].to_pydatetime().replace(tzinfo=timezone.utc)
        return price, prev_close, as_of
    except Exception as exc:
        print(f"    [prices] yfinance error for {ticker}: {exc}")
        return None, None, None


def _extract_price_from_text(text: str) -> Optional[float]:
    """Pull the first USD/numeric price-like value from a text snippet."""
    patterns = [
        r"\$\s*([\d,]+\.?\d*)",
        r"([\d,]+\.?\d*)\s*(?:USD|usd|US\$)",
        r"(?:at|price|traded at|stands at|was)\s+([\d,]+\.?\d*)",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                pass
    return None


def _web_fallback(commodity: dict) -> tuple[Optional[float], str]:
    """
    Perform a DuckDuckGo instant-answer / lite search for indicative price.
    Returns (price_or_None, source_label).
    """
    query = commodity.get("web_search_query", "")
    if not query:
        return None, "unavailable"

    headers = {"User-Agent": "Mozilla/5.0 (compatible; CommodityBot/1.0)"}
    url = f"https://duckduckgo.com/html/?q={httpx.QueryParams({'q': query})}"
    try:
        resp = httpx.get(url, headers=headers, timeout=12, follow_redirects=True)
        if resp.status_code == 200:
            price = _extract_price_from_text(resp.text[:4000])
            if price:
                return price, "web"
    except Exception as exc:
        print(f"    [prices] web fallback error for '{query}': {exc}")
    return None, "unavailable"


def fetch_all() -> list[PriceRecord]:
    cfg = _cfg()
    commodities = cfg.get("commodities", [])
    records: list[PriceRecord] = []

    for c in commodities:
        cid = c["id"]
        ticker = c.get("yfinance_ticker")
        multiplier = c.get("yfinance_unit_multiplier", 1.0)

        price, prev_close, as_of = None, None, None
        source = "unavailable"

        if ticker:
            price, prev_close, as_of = _yf_price(ticker, multiplier)
            if price is not None:
                source = "yfinance"
            else:
                print(f"  [prices] yfinance returned no data for {ticker} ({cid}), trying web fallback")

        if price is None:
            price, source = _web_fallback(c)
            if price is not None:
                as_of = datetime.now(timezone.utc)
                print(f"  [prices] {cid}: indicative price {price} (web-sourced, treat as approximate)")
            else:
                print(f"  [prices] {cid}: no price available — marked unavailable")

        change_pct = None
        if price is not None and prev_close and prev_close != 0:
            change_pct = round((price - prev_close) / prev_close * 100, 2)

        records.append(PriceRecord(
            commodity_id=cid,
            display=c["display"],
            unit=c["unit"],
            group=c["group"],
            price=round(price, 4) if price else None,
            prev_close=round(prev_close, 4) if prev_close else None,
            change_pct=change_pct,
            source=source,
            as_of=as_of,
        ))

        time.sleep(0.3)

    return records
