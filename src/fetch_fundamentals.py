"""Fetch fundamentals from EIA (key required), World Bank, and FRED (both free/keyless)."""

from __future__ import annotations

import csv
import io
import os
from datetime import datetime, timezone
from typing import Optional

import httpx

from . import config
from .models import FundamentalsItem


_EIA_BASE = "https://api.eia.gov/v2"
_WB_BASE = "https://api.worldbank.org/v2/country/WLD/indicator"
_FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"

_WB_INDICATORS = {
    "Copper":      "PCOPP_USD",
    "Aluminium":   "PALUM_USD",
    "Iron Ore":    "PIORECR_USD",
    "Natural Gas": "PNGAS_USD",
    "Coal":        "PCOAL_AUS",
}

_FRED_SERIES = {
    "Crude Oil":   "DCOILWTICO",
    "Brent Crude": "DCOILBRENTEU",
    "Natural Gas": "MHHNGSP",
}


def _eia_key() -> str:
    return os.environ.get("EIA_API_KEY", "").strip()


# ── EIA helpers ───────────────────────────────────────────────────────────────

def _fetch_eia_series(series_id: str) -> list[dict]:
    key = _eia_key()
    if not key:
        return []
    try:
        url = f"{_EIA_BASE}/seriesid/{series_id}"
        resp = httpx.get(url, params={"api_key": key, "out": "json"}, timeout=15, follow_redirects=True)
        resp.raise_for_status()
        return resp.json().get("response", {}).get("data", [])
    except Exception as exc:
        print(f"  [fundamentals] EIA series {series_id} error: {exc}")
        return []


def _parse_inventory(series_data: list[dict]) -> Optional[dict]:
    if len(series_data) < 2:
        return None
    try:
        latest, prev = series_data[0], series_data[1]
        level = float(latest["value"])
        change = level - float(prev["value"])
        direction = "up" if change > 0.5 else ("down" if change < -0.5 else "flat")
        return {"level": round(level, 1), "change": round(change, 1), "direction": direction,
                "period": latest.get("period", ""), "unit": latest.get("units", "")}
    except (KeyError, ValueError, TypeError):
        return None


def _parse_production(series_data: list[dict]) -> Optional[dict]:
    if not series_data:
        return None
    try:
        latest = series_data[0]
        return {"level": round(float(latest["value"]), 1), "period": latest.get("period", ""),
                "unit": latest.get("units", "")}
    except (KeyError, ValueError, TypeError):
        return None


# ── World Bank helpers ────────────────────────────────────────────────────────

def _fetch_wb_indicator(indicator: str, mrv: int = 4) -> list[dict]:
    try:
        resp = httpx.get(
            f"{_WB_BASE}/{indicator}",
            params={"format": "json", "mrv": mrv, "frequency": "Q"},
            timeout=15, follow_redirects=True,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and len(data) == 2:
            return [d for d in (data[1] or []) if d.get("value") is not None]
        return []
    except Exception as exc:
        print(f"  [fundamentals] World Bank {indicator} error: {exc}")
        return []


def fetch_world_bank_fundamentals() -> list[FundamentalsItem]:
    """Quarterly commodity price trends from World Bank API. Free, no key needed."""
    results: list[FundamentalsItem] = []
    for commodity_name, indicator in _WB_INDICATORS.items():
        rows = _fetch_wb_indicator(indicator)
        if len(rows) < 2:
            continue
        try:
            vals = [float(r["value"]) for r in rows if r.get("value")]
            if len(vals) < 2:
                continue
            latest, prev = vals[0], vals[1]
            pct = (latest - prev) / prev * 100 if prev else 0
            direction = "up" if pct > 2 else ("down" if pct < -2 else "flat")
            period = rows[0].get("date", "")
            word = "rising" if direction == "up" else ("falling" if direction == "down" else "stable")
            balance = (f"World Bank ({period}): {latest:.1f} ({pct:+.1f}% QoQ) — {word}")
            results.append(FundamentalsItem(
                commodity=commodity_name,
                inventory_direction=direction,
                balance_read=balance,
                source="World Bank",
                as_of=datetime.now(timezone.utc),
            ))
        except Exception:
            continue
    if results:
        print(f"  [fundamentals] World Bank: {len(results)} price trends")
    return results


# ── FRED helpers ──────────────────────────────────────────────────────────────

def fetch_fred_fundamentals() -> list[FundamentalsItem]:
    """30-day price trends from FRED CSV endpoint. Free, no key needed."""
    results: list[FundamentalsItem] = []
    for commodity_name, series_id in _FRED_SERIES.items():
        try:
            resp = httpx.get(_FRED_CSV, params={"id": series_id}, timeout=15, follow_redirects=True)
            resp.raise_for_status()
            reader = csv.reader(io.StringIO(resp.text))
            rows = [(r[0], r[1]) for r in reader if len(r) == 2 and r[1] not in (".", "VALUE", "")]
            if len(rows) < 2:
                continue
            latest_date, latest_val = rows[-1]
            lookback_idx = max(0, len(rows) - 22)
            _, past_val = rows[lookback_idx]
            latest = float(latest_val)
            past = float(past_val)
            pct = (latest - past) / past * 100 if past else 0
            direction = "up" if pct > 2 else ("down" if pct < -2 else "flat")
            word = "rising" if direction == "up" else ("falling" if direction == "down" else "flat")
            balance = f"FRED ({latest_date}): ${latest:.2f} ({pct:+.1f}% over 30 days) — {word} trend"
            results.append(FundamentalsItem(
                commodity=commodity_name,
                inventory_direction=direction,
                balance_read=balance,
                source="FRED",
                as_of=datetime.now(timezone.utc),
            ))
        except Exception as exc:
            print(f"  [fundamentals] FRED {series_id} error: {exc}")
    if results:
        print(f"  [fundamentals] FRED: {len(results)} price trends")
    return results


# ── Main entry point ──────────────────────────────────────────────────────────

def fetch_all_fundamentals() -> list[FundamentalsItem]:
    """Fetch fundamentals from all sources. World Bank + FRED are free/keyless; EIA needs a key."""
    results: list[FundamentalsItem] = []

    try:
        results.extend(fetch_world_bank_fundamentals())
    except Exception as exc:
        print(f"  [fundamentals] World Bank error: {exc}")

    try:
        results.extend(fetch_fred_fundamentals())
    except Exception as exc:
        print(f"  [fundamentals] FRED error: {exc}")

    cfg = config.load()
    eia_cfg = cfg.get("eia", {})
    if not eia_cfg.get("enabled", True) or not _eia_key():
        print("  [fundamentals] EIA_API_KEY absent — skipping EIA inventory data")
        return results

    series = eia_cfg.get("series", {})

    crude_inv_data = _fetch_eia_series(series.get("crude_inventory", "PET.WCRSTUS1.W"))
    crude_prod_data = _fetch_eia_series(series.get("crude_production", "PET.WCRFPUS2.W"))
    inv = _parse_inventory(crude_inv_data)
    prod = _parse_production(crude_prod_data)
    if inv or prod:
        parts: list[str] = []
        if inv:
            verb = "drew" if inv["direction"] == "down" else ("built" if inv["direction"] == "up" else "was flat at")
            parts.append(f"US crude inventories {verb} {abs(inv['change']):.1f}M bbl WoW ({inv['level']:.0f}M bbl total)")
        if prod:
            parts.append(f"production {prod['level']:.1f} kbd")
        results.append(FundamentalsItem(
            commodity="Crude Oil",
            inventory_level=inv["level"] if inv else None,
            inventory_change=inv["change"] if inv else None,
            inventory_direction=inv["direction"] if inv else "",
            production=prod["level"] if prod else None,
            balance_read="; ".join(parts) + " (EIA)" if parts else "",
            source="EIA",
            as_of=datetime.now(timezone.utc),
        ))

    ng_data = _fetch_eia_series(series.get("natgas_storage", "NG.NW2_EPG0_SWO_R48_BCF.W"))
    ng_inv = _parse_inventory(ng_data)
    if ng_inv:
        verb = "injection" if ng_inv["direction"] == "up" else "withdrawal"
        results.append(FundamentalsItem(
            commodity="Natural Gas",
            inventory_level=ng_inv["level"],
            inventory_change=ng_inv["change"],
            inventory_direction=ng_inv["direction"],
            balance_read=(f"US natgas storage: {abs(ng_inv['change']):.0f} Bcf {verb} WoW; total {ng_inv['level']:.0f} Bcf (EIA)"),
            source="EIA",
            as_of=datetime.now(timezone.utc),
        ))

    eia_count = sum(1 for r in results if r.source == "EIA")
    print(f"  [fundamentals] EIA: {eia_count} items; total: {len(results)}")
    return results


def build_news_fundamentals(all_items: list) -> list[FundamentalsItem]:
    """Build supply/demand signal fundamentals from tagged news items."""
    cfg = config.load()
    supply_kw = set(cfg.get("keyword_sets", {}).get("supply_side", []))
    demand_kw = set(cfg.get("keyword_sets", {}).get("demand_side", []))

    commodity_signals: dict[str, dict] = {}
    for item in all_items:
        commodity = item.commodity
        if not commodity:
            continue
        text = (item.title + " " + item.summary).lower()
        supply_hits = [kw for kw in supply_kw if kw.lower() in text]
        demand_hits = [kw for kw in demand_kw if kw.lower() in text]
        if not supply_hits and not demand_hits:
            continue
        if commodity not in commodity_signals:
            commodity_signals[commodity] = {"supply": [], "demand": []}
        commodity_signals[commodity]["supply"].extend(supply_hits)
        commodity_signals[commodity]["demand"].extend(demand_hits)

    results: list[FundamentalsItem] = []
    for commodity, signals in commodity_signals.items():
        supply = list(dict.fromkeys(signals["supply"]))[:3]
        demand = list(dict.fromkeys(signals["demand"]))[:3]
        parts: list[str] = []
        if supply:
            parts.append(f"Supply signals: {', '.join(supply)}")
        if demand:
            parts.append(f"Demand signals: {', '.join(demand)}")
        results.append(FundamentalsItem(
            commodity=commodity,
            supply_signals=supply,
            demand_signals=demand,
            balance_read=" | ".join(parts) if parts else "",
            source="News-derived",
            as_of=datetime.now(timezone.utc),
        ))

    return results
