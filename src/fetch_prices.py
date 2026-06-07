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


def _investing_com_price(url: str) -> Optional[float]:
    """Scrape last price from an Investing.com instrument page."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.google.com/",
    }
    try:
        resp = httpx.get(url, headers=headers, timeout=20, follow_redirects=True)
        if resp.status_code != 200:
            return None
        # Pattern 1: data-test attribute
        m = re.search(r'data-test="instrument-price-last"[^>]*>([0-9,]+\.?[0-9]*)', resp.text)
        if m:
            return float(m.group(1).replace(",", ""))
        # Pattern 2: json-ld or meta price
        m = re.search(r'"price"\s*:\s*"?([0-9]+\.?[0-9]*)"?', resp.text[:10000])
        if m:
            val = float(m.group(1))
            if val > 0:
                return val
        # Pattern 3: og:description
        m = re.search(r'<meta[^>]+property="og:description"[^>]+content="([^"]*)"', resp.text)
        if m:
            price = _extract_price_from_text(m.group(1))
            if price:
                return price
    except Exception as exc:
        print(f"    [prices] investing.com error for {url}: {exc}")
    return None


def _tradingview_price(symbol: str) -> Optional[float]:
    """Fetch last price from TradingView's public symbol-overview endpoint."""
    try:
        resp = httpx.get(
            "https://symbol-overview.tradingview.com/symbol_overview",
            params={"symbols": symbol, "fields": "close,lp,last_close"},
            headers={"User-Agent": "Mozilla/5.0", "Origin": "https://www.tradingview.com"},
            timeout=15,
            follow_redirects=True,
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and data:
                item = data[0]
                price = item.get("lp") or item.get("close") or item.get("last_close")
                if price:
                    return float(price)
    except Exception as exc:
        print(f"    [prices] TradingView error for {symbol}: {exc}")
    return None


def _web_fallback(commodity: dict) -> tuple[Optional[float], str]:
    """DuckDuckGo HTML search for an indicative price (last resort)."""
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
    """Fetch commodity prices. Source priority: TradingView → yfinance → Investing.com → web."""
    cfg = config.load()
    commodities = cfg.get("commodities", [])
    records: list[PriceRecord] = []

    for c in commodities:
        cid = c["id"]
        quality = c.get("quality", "market")
        price: Optional[float] = None
        prev_close: Optional[float] = None
        as_of: Optional[datetime] = None
        source = "unavailable"

        # 1. TradingView (primary)
        tv_symbol = c.get("tradingview_symbol", "")
        if tv_symbol:
            tv_price = _tradingview_price(tv_symbol)
            if tv_price is not None:
                multiplier = c.get("tradingview_unit_multiplier", 1.0)
                price = tv_price * multiplier
                source = "TradingView"
                as_of = datetime.now(timezone.utc)
                print(f"  [prices] {cid}: {price:.2f} (TradingView {tv_symbol})")

        # 2. yfinance (fallback)
        if price is None:
            ticker = c.get("yfinance_ticker", "")
            if ticker:
                multiplier = c.get("yfinance_unit_multiplier", 1.0)
                yf_price, yf_prev, yf_as_of = _yf_price(ticker, multiplier)
                if yf_price is not None:
                    price = yf_price
                    prev_close = yf_prev
                    as_of = yf_as_of
                    source = "yfinance"
                    print(f"  [prices] {cid}: {price:.2f} (yfinance fallback {ticker})")
                else:
                    print(f"  [prices] {cid}: yfinance failed for {ticker}")

        # 3. Investing.com (fallback)
        if price is None:
            inv_url = c.get("investing_url", "")
            if inv_url:
                inv_price = _investing_com_price(inv_url)
                if inv_price is not None:
                    price = inv_price
                    source = "Investing.com"
                    as_of = datetime.now(timezone.utc)
                    quality = "indicative"
                    print(f"  [prices] {cid}: {price:.2f} (Investing.com)")

        # 4. DuckDuckGo web search (last resort)
        if price is None:
            price, source = _web_fallback(c)
            if price is not None:
                as_of = datetime.now(timezone.utc)
                quality = "indicative"
                print(f"  [prices] {cid}: {price:.2f} (web fallback)")
            else:
                print(f"  [prices] {cid}: no price available from any source")

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

        time.sleep(0.3)

    return records


def fetch_returns(commodities: list[dict]) -> dict[str, dict[str, Optional[float]]]:
    """Fetch 1D/1W/1M price returns for each commodity via yfinance history.

    Returns {commodity_id: {"1d": pct, "1w": pct, "1m": pct}}.
    Only commodities with a yfinance_ticker are included.
    """
    results: dict[str, dict[str, Optional[float]]] = {}
    for c in commodities:
        cid = c["id"]
        ticker = c.get("yfinance_ticker", "")
        multiplier = c.get("yfinance_unit_multiplier", 1.0)
        if not ticker:
            continue
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="1mo", auto_adjust=True)
            if hist.empty or len(hist) < 2:
                continue
            closes = (hist["Close"] * multiplier).dropna()
            if len(closes) < 2:
                continue

            latest = float(closes.iloc[-1])

            def _pct(prev: float) -> Optional[float]:
                if prev == 0:
                    return None
                return round((latest - prev) / prev * 100, 2)

            r1d = _pct(float(closes.iloc[-2])) if len(closes) >= 2 else None
            r1w = _pct(float(closes.iloc[-6])) if len(closes) >= 6 else None
            r1m = _pct(float(closes.iloc[0]))

            results[cid] = {"1d": r1d, "1w": r1w, "1m": r1m}
            time.sleep(0.2)
        except Exception as exc:
            print(f"    [returns] {ticker} error: {exc}")
    return results


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
