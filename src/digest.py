"""Rule-based ranking, material-event flagging, and 'what to watch' generation."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional

from . import config
from .models import (
    CompanyDigest,
    CommodityDigest,
    DailyDigest,
    NewsItem,
    PriceRecord,
    WatchBullet,
)


# ── Source weights ────────────────────────────────────────────────────────────

def _source_weight(source: str) -> float:
    weights = {f["name"]: f.get("weight", 1.0) for f in config.get("supplementary_feeds", [])}
    return weights.get(source, config.get("scoring", {}).get("source_weight_default", 1.0))


# ── Recency score (exponential decay) ────────────────────────────────────────

def _recency_score(published: Optional[datetime]) -> float:
    if published is None:
        return 0.3
    halflife = config.get("scoring", {}).get("recency_halflife_hours", 12)
    age_h = max(0, (datetime.now(timezone.utc) - published).total_seconds() / 3600)
    return math.exp(-math.log(2) * age_h / halflife)


# ── Material-event flagging ───────────────────────────────────────────────────

def _flag_item(item: NewsItem) -> list[str]:
    text = (item.title + " " + item.summary).lower()
    kw_map: dict[str, list[str]] = config.get("material_event_keywords", {})
    flags = []
    for category, keywords in kw_map.items():
        for kw in keywords:
            if kw.lower() in text:
                flags.append(category)
                break
    return flags


# ── Score an item ─────────────────────────────────────────────────────────────

def _score(item: NewsItem) -> float:
    s = _recency_score(item.published) * _source_weight(item.source)
    event_bonus = config.get("scoring", {}).get("material_event_bonus", 2.5)
    if item.flags:
        s += event_bonus
    return round(s, 4)


# ── Build company digests ─────────────────────────────────────────────────────

def build_company_digests(company_news: dict[str, list[NewsItem]]) -> list[CompanyDigest]:
    cfg = config.load()
    company_meta = {c["name"]: c for c in cfg.get("companies", [])}
    max_items = cfg.get("news", {}).get("max_per_company", 10)
    digests = []

    for name, items in company_news.items():
        # flag + score each item
        for item in items:
            item.flags = _flag_item(item)
            item.score = _score(item)

        ranked = sorted(items, key=lambda x: x.score, reverse=True)[:max_items]
        all_flags = list({f for item in ranked for f in item.flags})

        meta = company_meta.get(name, {})
        digests.append(CompanyDigest(
            name=name,
            sector=meta.get("sector", "other"),
            country=meta.get("country", ""),
            items=ranked,
            flags=all_flags,
        ))

    return sorted(digests, key=lambda d: d.sector)


# ── Build commodity digests ───────────────────────────────────────────────────

def build_commodity_digests(
    commodity_news: dict[str, list[NewsItem]],
    prices: list[PriceRecord],
) -> list[CommodityDigest]:
    cfg = config.load()
    max_items = cfg.get("news", {}).get("max_per_commodity", 6)
    price_by_group = {}
    for pr in prices:
        price_by_group.setdefault(pr.group, []).append(pr)

    digests = []
    for group_id, items in commodity_news.items():
        for item in items:
            item.flags = _flag_item(item)
            item.score = _score(item)

        ranked = sorted(items, key=lambda x: x.score, reverse=True)[:max_items]
        group_prices = price_by_group.get(group_id, [])
        lead_price = group_prices[0] if group_prices else None

        digests.append(CommodityDigest(
            commodity_id=group_id,
            display=group_id.replace("_", " ").title(),
            price=lead_price,
            items=ranked,
        ))

    return digests


# ── Top headlines (across all sources) ───────────────────────────────────────

def top_headlines(
    company_news: dict[str, list[NewsItem]],
    commodity_news: dict[str, list[NewsItem]],
    supplementary: list[NewsItem],
) -> list[NewsItem]:
    all_items: list[NewsItem] = []
    for items in company_news.values():
        all_items.extend(items)
    for items in commodity_news.values():
        all_items.extend(items)
    all_items.extend(supplementary)

    # flag + score if not already done
    for item in all_items:
        if not item.flags:
            item.flags = _flag_item(item)
        if item.score == 0.0:
            item.score = _score(item)

    # dedup by url before ranking
    seen: set[str] = set()
    unique: list[NewsItem] = []
    for item in all_items:
        key = item.url.split("?")[0].rstrip("/")
        if key not in seen:
            seen.add(key)
            unique.append(item)

    n = config.get("news", {}).get("top_headlines_count", 15)
    return sorted(unique, key=lambda x: x.score, reverse=True)[:n]


# ── "What to watch" bullets ───────────────────────────────────────────────────

def what_to_watch(
    prices: list[PriceRecord],
    company_digests: list[CompanyDigest],
) -> list[WatchBullet]:
    bullets: list[WatchBullet] = []

    # 1. Biggest price movers
    movers = [p for p in prices if p.change_pct is not None]
    movers.sort(key=lambda p: abs(p.change_pct), reverse=True)
    for p in movers[:3]:
        direction = "up" if p.change_pct > 0 else "down"
        bullets.append(WatchBullet(
            category="price_mover",
            text=f"{p.display} moved {direction} {abs(p.change_pct):.1f}% to {p.price:.2f} {p.unit} — watch for follow-through.",
        ))

    # 2. Companies with material event flags
    flagged = [(d.name, d.flags) for d in company_digests if d.flags]
    flagged.sort(key=lambda x: len(x[1]), reverse=True)
    for name, flags in flagged[:4]:
        flag_str = ", ".join(sorted(set(flags))[:3])
        bullets.append(WatchBullet(
            category="event",
            text=f"{name}: material events flagged ({flag_str}) — review headlines below.",
        ))

    # 3. Data gaps (web-sourced or unavailable prices)
    gaps = [p for p in prices if p.source in ("web", "unavailable")]
    if gaps:
        names = ", ".join(p.display for p in gaps[:4])
        bullets.append(WatchBullet(
            category="data_gap",
            text=f"Indicative/unavailable prices for: {names}. Verify with a live broker feed.",
        ))

    return bullets


# ── Assemble full digest ──────────────────────────────────────────────────────

def build_digest(
    company_news: dict[str, list[NewsItem]],
    commodity_news: dict[str, list[NewsItem]],
    supplementary: list[NewsItem],
    prices: list[PriceRecord],
) -> DailyDigest:
    company_digests = build_company_digests(company_news)
    commodity_digests = build_commodity_digests(commodity_news, prices)
    headlines = top_headlines(company_news, commodity_news, supplementary)
    watch = what_to_watch(prices, company_digests)

    return DailyDigest(
        generated_at=datetime.now(timezone.utc),
        top_headlines=headlines,
        companies=company_digests,
        commodities=commodity_digests,
        what_to_watch=watch,
        prices=prices,
    )
