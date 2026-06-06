"""Rule-based ranking, material-event flagging, and consulting signal generation."""

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
    OpportunityItem,
    PriceRecord,
    SectorSummary,
    WatchBullet,
)

# ── Consulting signal labels (priority order — first match wins) ───────────────

_SIGNAL_PRIORITY = [
    'ma', 'enterprise_risk', 'trading_risk', 'regulatory', 'outage',
    'earnings', 'growth_strategy', 'esg', 'trading_moves', 'geopolitical',
]

_SIGNAL_MAP: dict[str, tuple[str, str]] = {
    'ma':             ('Integration',       'M&A or JV activity — potential integration, due diligence, or PMI mandate.'),
    'earnings':       ('Performance',       'Earnings or guidance release — CFO advisory, margin improvement, or FP&A opportunity.'),
    'outage':         ('Ops Resilience',    'Operational disruption — business continuity or resilience advisory opportunity.'),
    'regulatory':     ('Compliance Risk',   'Regulatory action — compliance advisory, remediation, or sanctions screening.'),
    'esg':            ('Sustainability',    'ESG or decarbonisation signal — sustainability strategy or reporting mandate.'),
    'trading_moves':  ('Leadership Change', 'Senior leadership transition — change management or org design opportunity.'),
    'geopolitical':   ('Compliance Risk',   'Geopolitical exposure — sanctions risk or trade compliance advisory.'),
    'trading_risk':   ('Trading Risk',      'Trading or market risk exposure — hedging framework, VaR, or desk controls review.'),
    'enterprise_risk':('Enterprise Risk',   'Enterprise risk or governance signal — ERM framework or internal audit advisory.'),
    'growth_strategy':('Growth Strategy',   'Expansion or investment signal — market entry, feasibility, or strategy advisory.'),
}

# flags that alone carry no consulting insight
_NOISE_FLAGS = {'price_shock'}

# signal display order for the Opportunities section
_OPP_PRIORITY = {
    'Integration': 0, 'Compliance Risk': 1, 'Enterprise Risk': 2,
    'Trading Risk': 3, 'Ops Resilience': 4, 'Performance': 5,
    'Growth Strategy': 6, 'Sustainability': 7, 'Leadership Change': 8,
}


def _assign_signal(flags: list[str]) -> tuple[str, str]:
    for cat in _SIGNAL_PRIORITY:
        if cat in flags:
            label, why = _SIGNAL_MAP[cat]
            return label, why
    return '', ''


def _is_consulting_relevant(item: NewsItem) -> bool:
    return bool(set(item.flags) - _NOISE_FLAGS)


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


def _enrich(item: NewsItem) -> None:
    """Flag, score, and assign consulting label to an item in place."""
    if not item.flags:
        item.flags = _flag_item(item)
    if item.score == 0.0:
        item.score = _score(item)
    if not item.consulting_label:
        item.consulting_label, item.why_it_matters = _assign_signal(item.flags)


# ── Build company digests ─────────────────────────────────────────────────────

def build_company_digests(company_news: dict[str, list[NewsItem]]) -> list[CompanyDigest]:
    cfg = config.load()
    company_meta = {c["name"]: c for c in cfg.get("companies", [])}
    digests = []

    for name, items in company_news.items():
        for item in items:
            _enrich(item)

        # keep only consulting-relevant items, top 5 by score
        relevant = [i for i in items if _is_consulting_relevant(i)]
        ranked = sorted(relevant, key=lambda x: x.score, reverse=True)[:5]

        if not ranked:
            continue

        all_flags = list({f for item in ranked for f in item.flags})
        meta = company_meta.get(name, {})
        digests.append(CompanyDigest(
            name=name,
            sector=meta.get("sector", "other"),
            country=meta.get("country", ""),
            items=ranked,
            flags=all_flags,
        ))

    return sorted(digests, key=lambda d: (d.sector, d.name))


# ── Build commodity digests ───────────────────────────────────────────────────

def build_commodity_digests(
    commodity_news: dict[str, list[NewsItem]],
    prices: list[PriceRecord],
) -> list[CommodityDigest]:
    cfg = config.load()
    max_items = cfg.get("news", {}).get("max_per_commodity", 6)
    price_by_group: dict[str, list[PriceRecord]] = {}
    for pr in prices:
        price_by_group.setdefault(pr.group, []).append(pr)

    digests = []
    for group_id, items in commodity_news.items():
        for item in items:
            _enrich(item)

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


# ── Opportunities roll-up (consulting intel feed) ─────────────────────────────

def build_opportunities(company_digests: list[CompanyDigest]) -> list[OpportunityItem]:
    candidates: list[OpportunityItem] = []
    for co in company_digests:
        for item in co.items:
            if not item.consulting_label:
                continue
            candidates.append(OpportunityItem(
                company=co.name,
                sector=co.sector,
                signal=item.consulting_label,
                headline=item.title,
                url=item.url,
                why=item.why_it_matters,
                published=item.published,
                score=item.score,
            ))

    # sort by signal priority, then score descending
    candidates.sort(key=lambda o: (_OPP_PRIORITY.get(o.signal, 99), -o.score))

    # one opportunity per company (highest-priority item)
    seen: set[str] = set()
    deduped: list[OpportunityItem] = []
    for o in candidates:
        if o.company not in seen:
            seen.add(o.company)
            deduped.append(o)

    return deduped[:8]


# ── Sector summaries ─────────────────────────────────────────────────────────

_SECTOR_LABELS = {
    'energy': 'Energy',
    'metals_mining': 'Metals & Mining',
    'power_utilities': 'Power & Utilities',
    'trading': 'Trading',
}


def _make_pulse(label: str, signal_counts: dict[str, int], company_signals: dict[str, str], active: list[str]) -> str:
    if not signal_counts:
        return f"{label} sector quiet today — no material signals detected."

    sorted_sigs = sorted(signal_counts.items(), key=lambda x: -x[1])
    top_sig = sorted_sigs[0][0]
    flagged = [co for co, sig in company_signals.items() if sig == top_sig]
    flagged_str = ", ".join(flagged[:3])

    if len(sorted_sigs) == 1:
        count = sorted_sigs[0][1]
        suffix = f"{flagged_str}" if len(flagged) <= 2 else f"{len(flagged)} companies"
        return f"{label}: {top_sig} signal detected — {suffix}."
    else:
        second_sig = sorted_sigs[1][0]
        return f"{label}: {top_sig} and {second_sig} signals across {len(active)} active {'company' if len(active)==1 else 'companies'}."


def build_sector_summaries(company_digests: list[CompanyDigest]) -> list[SectorSummary]:
    from .summarize import summarize_sector

    cfg = config.load()
    cfg_companies = cfg.get("companies", [])

    all_by_sector: dict[str, list[str]] = {}
    for c in cfg_companies:
        all_by_sector.setdefault(c["sector"], []).append(c["name"])

    active_by_sector: dict[str, list[CompanyDigest]] = {}
    for d in company_digests:
        active_by_sector.setdefault(d.sector, []).append(d)

    summaries: list[SectorSummary] = []
    for sector, label in _SECTOR_LABELS.items():
        active_digests = active_by_sector.get(sector, [])
        all_names = all_by_sector.get(sector, [])
        active_names = [d.name for d in active_digests]
        quiet_names = [n for n in all_names if n not in active_names]

        signal_counts: dict[str, int] = {}
        company_signals: dict[str, str] = {}
        api_signals: list[dict] = []
        for d in active_digests:
            sig = d.items[0].consulting_label if d.items else ""
            headline = d.items[0].title if d.items else ""
            if sig:
                signal_counts[sig] = signal_counts.get(sig, 0) + 1
                company_signals[d.name] = sig
            if sig and headline:
                api_signals.append({"company": d.name, "signal": sig, "headline": headline})

        top_signal = max(signal_counts, key=lambda k: signal_counts[k]) if signal_counts else ""
        pulse = _make_pulse(label, signal_counts, company_signals, active_names)

        # Try Claude; fall back to rule-based pulse on failure or missing key
        narrative = None
        if api_signals:
            narrative = summarize_sector(label, api_signals, quiet_names)
        narrative = narrative or pulse

        summaries.append(SectorSummary(
            sector=sector,
            sector_label=label,
            top_signal=top_signal,
            signal_counts=signal_counts,
            active_companies=active_names,
            quiet_companies=quiet_names,
            pulse=pulse,
            narrative=narrative,
        ))

    return summaries


# ── Top headlines ─────────────────────────────────────────────────────────────

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

    for item in all_items:
        _enrich(item)

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

    movers = [p for p in prices if p.change_pct is not None]
    movers.sort(key=lambda p: abs(p.change_pct), reverse=True)
    for p in movers[:3]:
        direction = "up" if p.change_pct > 0 else "down"
        bullets.append(WatchBullet(
            category="price_mover",
            text=f"{p.display} moved {direction} {abs(p.change_pct):.1f}% to {p.price:.2f} {p.unit} — watch for follow-through.",
        ))

    flagged = [(d.name, d.flags) for d in company_digests if d.flags]
    flagged.sort(key=lambda x: len(x[1]), reverse=True)
    for name, flags in flagged[:4]:
        flag_str = ", ".join(sorted(set(flags))[:3])
        bullets.append(WatchBullet(
            category="event",
            text=f"{name}: material events flagged ({flag_str}) — review headlines below.",
        ))

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
    opps = build_opportunities(company_digests)
    sector_sums = build_sector_summaries(company_digests)

    return DailyDigest(
        generated_at=datetime.now(timezone.utc),
        top_headlines=headlines,
        companies=company_digests,
        commodities=commodity_digests,
        what_to_watch=watch,
        prices=prices,
        opportunities=opps,
        sector_summaries=sector_sums,
    )
