"""Fetch commodity and macro prices via yfinance (free)."""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
import yfinance as yf

from . import config
from .models import PriceRecord, MacroTickerRecord, MacroSection


def _yf_price(ticker: str, multiplier: float = 1.0) -> tuple[Optional[float], Optional[float], Optional[datetime]]:
    """Return (price, prev_close, as_of) or (None, None, None) on failure."""
    try:
        t = yf.Ticker(ticker)
        fi = t.fast_info
        price_raw = getattr(fi, "last_price", None) or getattr(fi, "regular_market_price", None)
        prev_raw = getattr(fi, "previous_close", None) or getattr(fi, "regular_market_previous_close", None)

        if price_raw is not None:
            price = float(price_raw) * multiplier
            prev_close = float(prev_raw) * multiplier if prev_raw is not None else None
            return price, prev_close, datetime.now(timezone.utc)

        hist = t.history(period="5d", auto_adjust=True)
        if hist.empty:
            return None, None, None
        price = float(hist["Close"].iloc[-1]) * multiplier
        prev_close = float(hist["Close"].iloc[-2]) * multiplier if len(hist) >= 2 else None
        as_of = hist.index[-1].to_pydatetime().replace(tzinfo=timezone.utc)
        return price, prev_close, as_of

    except Exception as exc:
        print(f"    [prices] yfinance error for {ticker}: {exc}")
        return None, None, None


def _extract_price_from_text(text: str) -> Optional[float]:
    patterns = [
        r"\$\s*([\d,]+\.?\d*)",
        r"([\d,]+\.?\d*)\s*(?:USD|usd|US\$)",
        r"(?:at|price|traded at|stands at|was)\s+([\d,]+\.?\d*)",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            try:
                val = float(m.group(1).replace(",", ""))
                if val > 0:
                    return val
            except ValueError:
                pass
    return None


def _web_fallback(commodity: dict) -> tuple[Optional[float], str]:
    """DuckDuckGo HTML search for an indicative price."""
    query = commodity.get("web_search_query", "")
    if not query:
        return None, "unavailable"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        resp = httpx.get(
            "https://duckduckgo.com/html/",
            params={"q": query},
            headers=headers,
            timeout=14,
            follow_redirects=True,
        )
        if resp.status_code == 200:
            price = _extract_price_from_text(resp.text[:6000])
            if price:
                return price, "web"
    except Exception as exc:
        print(f"    [prices] web fallback error for '{query}': {exc}")
    return None, "unavailable"


def fetch_all() -> list[PriceRecord]:
    cfg = config.load()
    commodities = cfg.get("commodities", [])
    records: list[PriceRecord] = []

    for c in commodities:
        cid = c["id"]
        ticker = c.get("yfinance_ticker")
        multiplier = c.get("yfinance_unit_multiplier", 1.0)
        quality = c.get("quality", "market")

        price, prev_close, as_of = None, None, None
        source = "unavailable"

        if ticker:
            price, prev_close, as_of = _yf_price(ticker, multiplier)
            if price is not None:
                source = "yfinance"
            else:
                print(f"  [prices] yfinance no data for {ticker} ({cid}), trying web fallback")

        if price is None:
            price, source = _web_fallback(c)
            if price is not None:
                as_of = datetime.now(timezone.utc)
                quality = "indicative"
                print(f"  [prices] {cid}: indicative price {price} (web-sourced)")
            else:
                print(f"  [prices] {cid}: no price available")

        change_pct = None
        if price is not None and prev_close and prev_close != 0:
            change_pct = round((price - prev_close) / prev_close * 100, 2)

        records.append(PriceRecord(
            commodity_id=cid,
            display=c["display"],
            unit=c["unit"],
            group=c["group"],
            price=round(price, 4) if price is not None else None,
            prev_close=round(prev_close, 4) if prev_close is not None else None,
            change_pct=change_pct,
            source=source,
            quality=quality,
            as_of=as_of,
        ))

        time.sleep(0.4)

    return records


def fetch_macro() -> MacroSection:
    """Fetch macro tickers (DXY, US 10Y yield, Hang Seng)."""
    cfg = config.load()
    macro_cfg = cfg.get("macro_tickers", [])
    tickers: list[MacroTickerRecord] = []

    for m in macro_cfg:
        symbol = m.get("symbol", "")
        name = m.get("name", symbol)
        unit = m.get("unit", "")
        if not symbol:
            continue
        price, prev_close, as_of = _yf_price(symbol)
        change_pct = None
        if price is not None and prev_close and prev_close != 0:
            change_pct = round((price - prev_close) / prev_close * 100, 2)
        tickers.append(MacroTickerRecord(
            name=name,
            symbol=symbol,
            unit=unit,
            last=round(price, 3) if price is not None else None,
            change_pct=change_pct,
            as_of=as_of,
        ))
        time.sleep(0.3)

    return MacroSection(tickers=tickers, geopolitical=[])
