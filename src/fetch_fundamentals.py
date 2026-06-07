"""Fetch EIA fundamentals data and build news-derived supply/demand signals.

EIA Open Data API (free key required — register at eia.gov).
If EIA_API_KEY env var is absent, returns empty lists and logs a warning.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

import httpx

from . import config
from .models import FundamentalsItem, OutlookItem


_EIA_BASE = "https://api.eia.gov/v2"


def _eia_key() -> str:
    return os.environ.get("EIA_API_KEY", "").strip()


def _fetch_eia_series(series_id: str) -> list[dict]:
    key = _eia_key()
    if not key:
        return []
    try:
        url = f"{_EIA_BASE}/seriesid/{series_id}"
        resp = httpx.get(
            url,
            params={"api_key": key, "out": "json"},
            timeout=15,
            follow_redirects=True,
        )
        resp.raise_for_status()
        return resp.json().get("response", {}).get("data", [])
    except Exception as exc:
        print(f"  [fundamentals] EIA series {series_id} error: {exc}")
        return []


def _parse_inventory(series_data: list[dict]) -> Optional[dict]:
    if len(series_data) < 2:
        return None
    try:
        latest = series_data[0]
        prev = series_data[1]
        level = float(latest["value"])
        change = level - float(prev["value"])
        direction = "up" if change > 0.5 else ("down" if change < -0.5 else "flat")
        return {
            "level": round(level, 1),
            "change": round(change, 1),
            "direction": direction,
            "period": latest.get("period", ""),
            "unit": latest.get("units", ""),
        }
    except (KeyError, ValueError, TypeError):
        return None


def _parse_production(series_data: list[dict]) -> Optional[dict]:
    if not series_data:
        return None
    try:
        latest = series_data[0]
        return {
            "level": round(float(latest["value"]), 1),
            "period": latest.get("period", ""),
            "unit": latest.get("units", ""),
        }
    except (KeyError, ValueError, TypeError):
        return None


def fetch_all_fundamentals() -> list[FundamentalsItem]:
    """Fetch EIA fundamentals for key commodities.

    Returns empty list gracefully if EIA_API_KEY is absent.
    """
    cfg = config.load()
    eia_cfg = cfg.get("eia", {})

    if not eia_cfg.get("enabled", True) or not _eia_key():
        print("  [fundamentals] EIA_API_KEY absent — skipping EIA fundamentals")
        return []

    series = eia_cfg.get("series", {})
    results: list[FundamentalsItem] = []

    # ── Crude oil ──────────────────────────────────────────────────────────────
    crude_inv_data = _fetch_eia_series(series.get("crude_inventory", "PET.WCRSTUS1.W"))
    crude_prod_data = _fetch_eia_series(series.get("crude_production", "PET.WCRFPUS2.W"))

    inv = _parse_inventory(crude_inv_data)
    prod = _parse_production(crude_prod_data)

    if inv or prod:
        parts: list[str] = []
        if inv:
            verb = "drew" if inv["direction"] == "down" else ("built" if inv["direction"] == "up" else "was flat at")
            parts.append(f"US crude inventories {verb} {abs(inv['change']):.1f}M bbl WoW "
                         f"({inv['level']:.0f}M bbl total)")
        if prod:
            parts.append(f"production {prod['level']:.1f} kbd")
        results.append(FundamentalsItem(
            commodity="Crude Oil",
            inventory_level=inv["level"] if inv else None,
            inventory_change=inv["change"] if inv else None,
            inventory_direction=inv["direction"] if inv else "",
            production=prod["level"] if prod else None,
            balance_read="; ".join(parts) + " (US data — EIA)" if parts else "",
            source="EIA",
            as_of=datetime.now(timezone.utc),
        ))

    # ── Natural gas ────────────────────────────────────────────────────────────
    ng_data = _fetch_eia_series(series.get("natgas_storage", "NG.NW2_EPG0_SWO_R48_BCF.W"))
    ng_inv = _parse_inventory(ng_data)

    if ng_inv:
        verb = "injection" if ng_inv["direction"] == "up" else "withdrawal"
        results.append(FundamentalsItem(
            commodity="Natural Gas",
            inventory_level=ng_inv["level"],
            inventory_change=ng_inv["change"],
            inventory_direction=ng_inv["direction"],
            balance_read=(
                f"US natgas storage: {abs(ng_inv['change']):.0f} Bcf {verb} WoW; "
                f"total {ng_inv['level']:.0f} Bcf (EIA)"
            ),
            source="EIA",
            as_of=datetime.now(timezone.utc),
        ))

    print(f"  [fundamentals] fetched {len(results)} EIA fundamentals items")
    return results


def build_news_fundamentals(
    all_items: list,  # list[NewsItem]
) -> list[FundamentalsItem]:
    """Build supply/demand signal fundamentals from tagged news items.

    Returns one FundamentalsItem per commodity that has news signals.
    """
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
