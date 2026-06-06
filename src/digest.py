"""Rule-based ranking, material-event flagging, consulting signal and opportunity generation."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import config
from .models import (
    AccountBrief,
    CompanyDigest,
    CommodityDigest,
    DailyDigest,
    ExecutiveBrief,
    NewsItem,
    OpportunityCard,
    OpportunityItem,
    PriceRecord,
    SectorSummary,
    SectorTheme,
    TriggerMatch,
    WatchBullet,
    WeeklyBrief,
)
from .process import detect_triggers

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


# ── Opportunity Radar ─────────────────────────────────────────────────────────

def _recency_score_opp(published: Optional[datetime]) -> float:
    if published is None:
        return 0.3
    halflife = config.get("opportunity_scoring", {}).get("recency_halflife_hours", 18)
    age_h = max(0, (datetime.now(timezone.utc) - published).total_seconds() / 3600)
    return math.exp(-math.log(2) * age_h / halflife)


def build_opportunity_radar(
    company_news: dict[str, list[NewsItem]],
) -> list[OpportunityCard]:
    cfg = config.load()
    company_meta = {c["name"]: c for c in cfg.get("companies", [])}
    opp_cfg = cfg.get("opportunity_scoring", {})
    max_cards = opp_cfg.get("max_opportunities", 12)
    source_weights = {f["name"]: f.get("weight", 1.0) for f in cfg.get("supplementary_feeds", [])}

    cards: list[OpportunityCard] = []
    for company, items in company_news.items():
        meta = company_meta.get(company, {})

        for item in items:
            triggers = detect_triggers(item)
            if not triggers:
                continue
            # pick the highest-materiality trigger per item
            trigger = max(triggers, key=lambda t: t.materiality_weight)
            recency = _recency_score_opp(item.published)
            src_w = source_weights.get(item.source, cfg.get("scoring", {}).get("source_weight_default", 1.0))
            score = round(trigger.materiality_weight * recency * src_w, 3)

            cards.append(OpportunityCard(
                company=company,
                sector=meta.get("sector", "other"),
                country=meta.get("country", ""),
                headline=item.title,
                url=item.url,
                driver=trigger.driver,
                service_line=trigger.service_line,
                suggested_angle=trigger.suggested_angle,
                score=score,
                published=item.published,
            ))

    # one card per company (highest score), then sort by score descending
    seen: set[str] = set()
    deduped: list[OpportunityCard] = []
    for card in sorted(cards, key=lambda c: c.score, reverse=True):
        if card.company not in seen:
            seen.add(card.company)
            deduped.append(card)

    return deduped[:max_cards]


# ── Account Intelligence briefs ───────────────────────────────────────────────

_SECTOR_DISPLAY = {
    'energy': 'Energy',
    'metals_mining': 'Metals & Mining',
    'power_utilities': 'Power & Utilities',
    'trading': 'Trading',
}


def _build_talking_points(
    company: str,
    triggers: list[TriggerMatch],
    headlines: list[str],
) -> list[str]:
    points: list[str] = []
    if triggers:
        top = triggers[0]
        points.append(f"{top.service_line} signal detected — {top.suggested_angle.lower()}.")
    if len(triggers) > 1:
        others = ", ".join(t.service_line for t in triggers[1:3])
        points.append(f"Additional signals: {others}.")
    if headlines:
        points.append(f"Key development: \"{headlines[0]}\"")
    return points[:4]


def build_account_briefs(
    company_news: dict[str, list[NewsItem]],
    opportunity_cards: list[OpportunityCard],
) -> list[AccountBrief]:
    cfg = config.load()
    sl_labels: dict = cfg.get("service_lines", {})

    briefs: list[AccountBrief] = []
    for company_cfg in cfg.get("companies", []):
        name = company_cfg["name"]
        items = company_news.get(name, [])

        # collect all triggers across all items
        all_triggers: list[TriggerMatch] = []
        for item in items[:10]:
            all_triggers.extend(detect_triggers(item))

        # deduplicate triggers by driver, keep highest materiality
        seen_drivers: dict[str, TriggerMatch] = {}
        for t in sorted(all_triggers, key=lambda x: x.materiality_weight, reverse=True):
            if t.driver not in seen_drivers:
                seen_drivers[t.driver] = t
        unique_triggers = list(seen_drivers.values())[:5]

        headlines = [i.title for i in items[:5]]
        urls = [i.url for i in items[:5]]

        consulting_angles = [f"{t.service_line}: {t.suggested_angle}" for t in unique_triggers[:3]]
        talking_points = _build_talking_points(name, unique_triggers, headlines)

        briefs.append(AccountBrief(
            name=name,
            sector=company_cfg.get("sector", "other"),
            country=company_cfg.get("country", ""),
            ticker=company_cfg.get("ticker", ""),
            one_liner=company_cfg.get("one_liner", ""),
            active_triggers=unique_triggers,
            top_headlines=headlines,
            top_urls=urls,
            consulting_angles=consulting_angles,
            talking_points=talking_points,
            has_news=bool(items),
        ))

    briefs.sort(key=lambda b: (0 if b.has_news else 1, b.name))
    return briefs


# ── Sector themes ─────────────────────────────────────────────────────────────

_THEME_NAMES: dict[str, str] = {
    "ma":               "M&A / Transaction Activity",
    "capital_projects": "Capital Project Pipeline",
    "cost_performance": "Cost & Margin Pressure",
    "leadership":       "Leadership Transition",
    "regulation_risk":  "Regulatory & Sanctions Exposure",
    "sustainability":   "Energy Transition & ESG",
    "digital_tech":     "Digital & Technology Transformation",
    "cyber":            "Cyber & Operational Resilience",
    "tax":              "Tax & Fiscal Risk",
    "supply_chain":     "Supply Chain Disruption",
}


def build_sector_themes(opportunity_cards: list[OpportunityCard]) -> list[SectorTheme]:
    driver_map: dict[str, list[OpportunityCard]] = {}
    for card in opportunity_cards:
        driver_map.setdefault(card.driver, []).append(card)

    themes: list[SectorTheme] = []
    for driver, cards in sorted(driver_map.items(), key=lambda x: -len(x[1])):
        if len(cards) < 2:
            continue
        accounts = list(dict.fromkeys(c.company for c in cards))
        service_lines = list(dict.fromkeys(c.service_line for c in cards))
        sectors = list(dict.fromkeys(_SECTOR_DISPLAY.get(c.sector, c.sector) for c in cards))
        description = (
            f"{len(accounts)} accounts show {_THEME_NAMES.get(driver, driver)} signals "
            f"across {', '.join(sectors)} — potential cross-sector engagement opportunity."
        )
        themes.append(SectorTheme(
            theme=_THEME_NAMES.get(driver, driver.replace("_", " ").title()),
            driver=driver,
            service_lines=service_lines,
            accounts=accounts,
            description=description,
        ))

    return themes[:6]


# ── Partner Weekly Brief ──────────────────────────────────────────────────────

def build_weekly_brief(
    opportunity_cards: list[OpportunityCard],
    account_briefs: list[AccountBrief],
    themes: list[SectorTheme],
    generated_at: datetime,
) -> WeeklyBrief:
    period = generated_at.strftime("Week of %d %b %Y")

    top_opps: list[str] = []
    for card in opportunity_cards[:5]:
        top_opps.append(f"{card.company} [{card.service_line}] — {card.suggested_angle.split('—')[-1].strip() if '—' in card.suggested_angle else card.suggested_angle}")

    hottest = [c.company for c in opportunity_cards[:5]]
    key_themes = [t.theme for t in themes[:4]]

    what_changed: list[str] = [f"Top signals: {', '.join(hottest[:4])}."] if hottest else ["No signals detected."]

    return WeeklyBrief(
        period=period,
        top_opportunities=top_opps,
        hottest_accounts=hottest,
        key_themes=key_themes,
        what_changed=what_changed,
        opportunity_count=len(opportunity_cards),
        active_account_count=sum(1 for b in account_briefs if b.has_news),
    )


# ── Assemble full digest ──────────────────────────────────────────────────────

def build_digest(
    company_news: dict[str, list[NewsItem]],
    commodity_news: dict[str, list[NewsItem]],
    supplementary: list[NewsItem],
    prices: list[PriceRecord],
) -> DailyDigest:
    from .summarize import enrich_opportunities, summarize_company_highlights, summarize_executive_brief

    now = datetime.now(timezone.utc)

    company_digests = build_company_digests(company_news)
    commodity_digests = build_commodity_digests(commodity_news, prices)
    headlines = top_headlines(company_news, commodity_news, supplementary)
    watch = what_to_watch(prices, company_digests)
    opps = build_opportunities(company_digests)
    sector_sums = build_sector_summaries(company_digests)

    # Enrich opportunities with engagement context (Haiku)
    opp_dicts = [{"company": o.company, "signal": o.signal, "headline": o.headline} for o in opps]
    contexts = enrich_opportunities(opp_dicts)
    if contexts:
        for opp, ctx in zip(opps, contexts):
            opp.engagement_context = ctx

    # Add highlight to top 3 companies by opportunity priority (Haiku)
    top_co_names = list(dict.fromkeys(o.company for o in opps))[:3]
    top_co_digests = [d for d in company_digests if d.name in top_co_names]
    if top_co_digests:
        co_dicts = [
            {
                "name": d.name,
                "sector": d.sector,
                "signal": d.items[0].consulting_label if d.items else "",
                "headlines": [i.title for i in d.items],
            }
            for d in top_co_digests
        ]
        highlights = summarize_company_highlights(co_dicts)
        if highlights:
            co_map = {d.name: d for d in top_co_digests}
            for name, hl in zip(top_co_names, highlights):
                if name in co_map:
                    co_map[name].highlight = hl

    # Build executive brief (Sonnet) using sector narratives + opportunities
    brief: Optional[ExecutiveBrief] = None
    sector_dicts = [
        {"label": s.sector_label, "narrative": s.narrative, "active_count": len(s.active_companies)}
        for s in sector_sums if s.narrative
    ]
    if sector_dicts and opp_dicts:
        result = summarize_executive_brief(sector_dicts, opp_dicts)
        if result:
            narrative, themes = result
            brief = ExecutiveBrief(narrative=narrative, themes=themes)

    # ── New: demand-driver intelligence layer ─────────────────────────────────
    opportunity_radar = build_opportunity_radar(company_news)
    account_briefs = build_account_briefs(company_news, opportunity_radar)
    sector_themes = build_sector_themes(opportunity_radar)
    weekly_brief = build_weekly_brief(
        opportunity_radar, account_briefs, sector_themes, now
    )

    return DailyDigest(
        generated_at=now,
        executive_brief=brief,
        top_headlines=headlines,
        companies=company_digests,
        commodities=commodity_digests,
        what_to_watch=watch,
        prices=prices,
        opportunities=opps,
        sector_summaries=sector_sums,
        opportunity_radar=opportunity_radar,
        account_briefs=account_briefs,
        sector_themes=sector_themes,
        weekly_brief=weekly_brief,
    )
