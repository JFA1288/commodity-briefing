"""Rule-based ranking, material-event flagging, consulting signal generation, and MI section builders."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import config
from .models import (
    AccountBrief,
    CompanyDigest,
    CommodityDigest,
    DailyDigest,
    EventItem,
    ExecutiveBrief,
    FundamentalsItem,
    HeatmapData,
    CorrelationData,
    MacroSection,
    NewsItem,
    OpportunityCard,
    OpportunityItem,
    OutlookItem,
    PriceRecord,
    RiskItem,
    RegulatoryItem,
    SectorSummary,
    SectorTheme,
    ThemeItem,
    TriggerMatch,
    WatchBullet,
    WeeklyBrief,
)
from .process import detect_triggers

# ── Consulting signal labels ──────────────────────────────────────────────────

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

_NOISE_FLAGS = {'price_shock'}

_OPP_PRIORITY = {
    'Integration': 0, 'Compliance Risk': 1, 'Enterprise Risk': 2,
    'Trading Risk': 3, 'Ops Resilience': 4, 'Performance': 5,
    'Growth Strategy': 6, 'Sustainability': 7, 'Leadership Change': 8,
}


def _assign_signal(flags: list[str]) -> tuple[str, str]:
    for cat in _SIGNAL_PRIORITY:
        if cat in flags:
            return _SIGNAL_MAP[cat]
    return '', ''


def _is_consulting_relevant(item: NewsItem) -> bool:
    return bool(set(item.flags) - _NOISE_FLAGS)


# ── Source / recency weights ──────────────────────────────────────────────────

def _source_weight(source: str) -> float:
    cfg = config.load()
    weights = cfg.get("source_weights", {})
    for name, w in weights.items():
        if name.lower() in source.lower():
            return float(w)
    # Fall back to supplementary_feeds weight map
    supp_weights = {f["name"]: f.get("weight", 1.0) for f in cfg.get("supplementary_feeds", [])}
    return supp_weights.get(source, float(cfg.get("source_weights", {}).get("default", 1.0)))


def _recency_score(published: Optional[datetime], halflife_h: float = 12.0) -> float:
    if published is None:
        return 0.3
    age_h = max(0, (datetime.now(timezone.utc) - published).total_seconds() / 3600)
    return math.exp(-math.log(2) * age_h / halflife_h)


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


def _score(item: NewsItem) -> float:
    halflife = config.get("scoring", {}).get("recency_halflife_hours", 12)
    s = _recency_score(item.published, halflife) * _source_weight(item.source)
    event_bonus = config.get("scoring", {}).get("material_event_bonus", 2.5)
    if item.flags:
        s += event_bonus
    return round(s, 4)


def _enrich(item: NewsItem) -> None:
    """Flag, score, and assign consulting label in place."""
    if not item.flags:
        item.flags = _flag_item(item)
    if item.score == 0.0:
        item.score = _score(item)
    if not item.consulting_label:
        item.consulting_label, item.why_it_matters = _assign_signal(item.flags)


# ── Company digests ───────────────────────────────────────────────────────────

def build_company_digests(company_news: dict[str, list[NewsItem]]) -> list[CompanyDigest]:
    from .summarize import summarize_company

    cfg = config.load()
    company_meta = {c["name"]: c for c in cfg.get("companies", [])}
    digests = []

    for name, items in company_news.items():
        for item in items:
            _enrich(item)

        relevant = [i for i in items if _is_consulting_relevant(i)]
        ranked = sorted(relevant, key=lambda x: x.score, reverse=True)[:5]

        if not ranked:
            continue

        all_flags = list({f for item in ranked for f in item.flags})
        meta = company_meta.get(name, {})

        # Try Claude summary
        headlines = [i.title for i in ranked]
        summary = summarize_company(name, meta.get("sector", ""), headlines) or ""

        digests.append(CompanyDigest(
            name=name,
            sector=meta.get("sector", "other"),
            country=meta.get("country", ""),
            ticker=meta.get("ticker", ""),
            items=ranked,
            flags=all_flags,
            summary=summary,
        ))

    return sorted(digests, key=lambda d: (d.sector, d.name))


# ── Commodity digests ─────────────────────────────────────────────────────────

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


# ── Opportunities ─────────────────────────────────────────────────────────────

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

    candidates.sort(key=lambda o: (_OPP_PRIORITY.get(o.signal, 99), -o.score))

    seen: set[str] = set()
    deduped: list[OpportunityItem] = []
    for o in candidates:
        if o.company not in seen:
            seen.add(o.company)
            deduped.append(o)

    return deduped[:8]


# ── Sector summaries ──────────────────────────────────────────────────────────

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
        return f"{label}: {top_sig} signal detected — {flagged_str}."
    second_sig = sorted_sigs[1][0]
    return f"{label}: {top_sig} and {second_sig} signals across {len(active)} active {'company' if len(active)==1 else 'companies'}."


def build_sector_summaries(company_digests: list[CompanyDigest]) -> list[SectorSummary]:
    from .summarize import summarize_sector

    cfg = config.load()
    all_by_sector: dict[str, list[str]] = {}
    for c in cfg.get("companies", []):
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


# ── What to watch ─────────────────────────────────────────────────────────────

def what_to_watch(prices: list[PriceRecord], company_digests: list[CompanyDigest]) -> list[WatchBullet]:
    bullets: list[WatchBullet] = []

    movers = [p for p in prices if p.change_pct is not None]
    movers.sort(key=lambda p: abs(p.change_pct), reverse=True)
    for p in movers[:3]:
        direction = "up" if p.change_pct > 0 else "down"
        bullets.append(WatchBullet(
            category="price_mover",
            text=f"{p.display} {direction} {abs(p.change_pct):.1f}% to {p.price:.2f} {p.unit}.",
        ))

    flagged = [(d.name, d.flags) for d in company_digests if d.flags]
    flagged.sort(key=lambda x: len(x[1]), reverse=True)
    for name, flags in flagged[:4]:
        flag_str = ", ".join(sorted(set(flags))[:3])
        bullets.append(WatchBullet(
            category="event",
            text=f"{name}: material events flagged ({flag_str}).",
        ))

    gaps = [p for p in prices if p.source in ("web", "unavailable")]
    if gaps:
        names = ", ".join(p.display for p in gaps[:4])
        bullets.append(WatchBullet(
            category="data_gap",
            text=f"Indicative/unavailable prices for: {names}.",
        ))

    return bullets


# ── Opportunity Radar ─────────────────────────────────────────────────────────

def build_opportunity_radar(company_news: dict[str, list[NewsItem]]) -> list[OpportunityCard]:
    cfg = config.load()
    company_meta = {c["name"]: c for c in cfg.get("companies", [])}
    opp_cfg = cfg.get("opportunity_scoring", {})
    max_cards = opp_cfg.get("max_opportunities", 12)
    halflife = opp_cfg.get("recency_halflife_hours", 18)

    cards: list[OpportunityCard] = []
    for company, items in company_news.items():
        meta = company_meta.get(company, {})
        for item in items:
            triggers = detect_triggers(item)
            if not triggers:
                continue
            trigger = max(triggers, key=lambda t: t.materiality_weight)
            recency = _recency_score(item.published, halflife)
            src_w = _source_weight(item.source)
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


def _build_talking_points(company: str, triggers: list[TriggerMatch], headlines: list[str]) -> list[str]:
    points: list[str] = []
    if triggers:
        top = triggers[0]
        points.append(f"{top.service_line} signal — {top.suggested_angle.lower()}.")
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

    briefs: list[AccountBrief] = []
    for company_cfg in cfg.get("companies", []):
        name = company_cfg["name"]
        items = company_news.get(name, [])

        all_triggers: list[TriggerMatch] = []
        for item in items[:10]:
            all_triggers.extend(detect_triggers(item))

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


# ── Sector themes (demand-driver clusters) ────────────────────────────────────

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


# ── Market themes (supply/demand/geopolitical clustering for MI) ──────────────

def build_market_themes(
    all_items: list[NewsItem],
    company_news: dict[str, list[NewsItem]],
) -> list[ThemeItem]:
    from .summarize import summarize_theme

    cfg = config.load()
    theme_cfg = cfg.get("themes", [])

    items_by_driver: dict[str, list[NewsItem]] = {}
    for item in all_items:
        for driver in (item.drivers or []):
            items_by_driver.setdefault(driver, []).append(item)

    results: list[ThemeItem] = []
    for tcfg in theme_cfg:
        driver_tags: list[str] = tcfg.get("driver_tags", [])
        min_items: int = tcfg.get("min_items", 2)
        extra_keywords: list[str] = tcfg.get("keywords", [])

        matching: list[NewsItem] = []
        for dt in driver_tags:
            matching.extend(items_by_driver.get(dt, []))

        # Also match on extra keywords
        if extra_keywords:
            for item in all_items:
                text = (item.title + " " + item.summary).lower()
                if any(kw.lower() in text for kw in extra_keywords):
                    matching.append(item)

        # Deduplicate by URL
        seen_urls: set[str] = set()
        unique_matching: list[NewsItem] = []
        for item in matching:
            url = item.url.split("?")[0]
            if url not in seen_urls:
                seen_urls.add(url)
                unique_matching.append(item)

        if len(unique_matching) < min_items:
            continue

        # Extract companies and commodities mentioned
        companies_set: set[str] = set()
        for item in unique_matching:
            if item.company:
                companies_set.add(item.company)
        companies = list(companies_set)[:6]

        commodities_set: set[str] = set()
        for item in unique_matching:
            if item.commodity:
                commodities_set.add(item.commodity)
        commodities = list(commodities_set)[:4]

        top_headlines = [
            {"title": i.title, "source": i.source, "url": i.url, "age_h": i.age_h}
            for i in sorted(unique_matching, key=lambda x: x.score, reverse=True)[:3]
        ]

        headline_texts = [i.title for i in unique_matching[:5]]
        narrative = summarize_theme(
            tcfg["name"], headline_texts, companies, commodities
        ) or tcfg.get("description", "")

        results.append(ThemeItem(
            name=tcfg["name"],
            commodities=commodities,
            companies=companies,
            headlines=top_headlines,
            narrative=narrative,
            driver_tags=driver_tags,
        ))

    return results


# ── Outlook items ─────────────────────────────────────────────────────────────

def build_outlook(
    commodity_news: dict[str, list[NewsItem]],
    fundamentals: list[FundamentalsItem],
) -> list[OutlookItem]:
    from .summarize import summarize_outlook

    cfg = config.load()
    fundamentals_by_commodity = {f.commodity: f for f in fundamentals}

    results: list[OutlookItem] = []
    key_commodities = ["Crude Oil", "Natural Gas", "Copper", "Iron Ore", "LNG"]

    for comm_name in key_commodities:
        fund = fundamentals_by_commodity.get(comm_name)
        eia_text = fund.balance_read if fund else ""
        wb_text = ""  # World Bank XLSX parsing out of scope; shown as placeholder

        # Find relevant news headlines
        headlines: list[dict] = []
        for group_id, items in commodity_news.items():
            for item in items:
                text = (item.title + " " + item.summary).lower()
                if comm_name.lower().split()[0] in text:
                    headlines.append({"title": item.title, "source": item.source, "url": item.url})
        headlines = headlines[:3]

        headline_texts = [h["title"] for h in headlines]
        narrative = summarize_outlook(comm_name, eia_text, wb_text, headline_texts) or ""

        # Determine consensus direction from news sentiment
        direction = "neutral"
        if headlines:
            all_text = " ".join(h["title"].lower() for h in headlines)
            bullish_words = ["surge", "rally", "rise", "gain", "tight", "deficit", "strong demand"]
            bearish_words = ["plunge", "fall", "drop", "surplus", "weak", "oversupply", "cut"]
            b = sum(1 for w in bullish_words if w in all_text)
            be = sum(1 for w in bearish_words if w in all_text)
            if b > be + 1:
                direction = "bullish"
            elif be > b + 1:
                direction = "bearish"

        results.append(OutlookItem(
            commodity=comm_name,
            eia_forecast=eia_text,
            wb_forecast=wb_text,
            horizon="Near-term",
            direction=direction,
            narrative=narrative,
            headlines=headlines,
        ))

    return results


# ── Risks & Events ────────────────────────────────────────────────────────────

def build_risks(
    company_news: dict[str, list[NewsItem]],
    prices: list[PriceRecord],
) -> list[RiskItem]:
    from .summarize import summarize_risks

    cfg = config.load()
    standing = cfg.get("standing_risks", [])

    # Detect triggered risks from news
    triggered: list[str] = []
    geo_kw = cfg.get("keyword_sets", {}).get("geopolitical_watch", [])
    for items in company_news.values():
        for item in items:
            text = (item.title + " " + item.summary).lower()
            for kw in geo_kw:
                if kw.lower() in text:
                    triggered.append(f"Geopolitical signal: {item.title[:80]}")
                    break

    # Add price-move risks
    movers = [p for p in prices if p.change_pct is not None and abs(p.change_pct) >= 2.0]
    for p in movers[:2]:
        direction = "upside" if p.change_pct > 0 else "downside"
        triggered.append(f"{p.display} {p.change_pct:+.1f}% today — watch for follow-through")

    triggered = list(dict.fromkeys(triggered))[:4]

    # Try Claude for richer risk bullets
    claude_risks = summarize_risks(standing, triggered)
    if claude_risks:
        return [RiskItem(direction=r["direction"], text=r["text"]) for r in claude_risks]

    # Heuristic fallback: standing risks + triggered
    risks: list[RiskItem] = [RiskItem(direction=r["direction"], text=r["text"]) for r in standing]
    for t in triggered:
        risks.append(RiskItem(direction="downside", text=t))

    return risks[:8]


def build_events(generated_at: datetime) -> list[EventItem]:
    """Generate upcoming events from config calendar."""
    cfg = config.load()
    events_cfg = cfg.get("events", [])

    _WEEKDAYS = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
                 "Friday": 4, "Saturday": 5, "Sunday": 6}

    results: list[EventItem] = []
    today = generated_at.date()
    window_end = today + timedelta(days=30)

    for ev in events_cfg:
        title = ev.get("title", "")
        ev_type = ev.get("type", "")
        related = ev.get("related", [])

        if "date" in ev:
            try:
                ev_date = datetime.strptime(ev["date"], "%Y-%m-%d").date()
                if ev_date >= today:
                    upcoming = (ev_date - today).days <= 7
                    results.append(EventItem(
                        date=str(ev_date),
                        title=title,
                        type=ev_type,
                        related=related,
                        upcoming=upcoming,
                    ))
            except ValueError:
                pass
        elif ev.get("recurrence") == "weekly" and ev.get("weekday"):
            target_wd = _WEEKDAYS.get(ev["weekday"], -1)
            if target_wd < 0:
                continue
            d = today
            while d <= window_end:
                if d.weekday() == target_wd:
                    upcoming = (d - today).days <= 7
                    results.append(EventItem(
                        date=str(d),
                        title=title,
                        type=ev_type,
                        related=related,
                        upcoming=upcoming,
                    ))
                    d += timedelta(weeks=1)
                else:
                    d += timedelta(days=1)

    results.sort(key=lambda e: e.date)
    return results[:15]


# ── Exec summary ──────────────────────────────────────────────────────────────

def build_exec_summary(
    prices: list[PriceRecord],
    headlines: list[NewsItem],
    themes: list[ThemeItem],
    risks: list[RiskItem],
) -> list[str]:
    from .summarize import summarize_exec_summary

    movers = [
        {"name": p.display, "change_pct": p.change_pct}
        for p in prices
        if p.change_pct is not None
    ]
    movers.sort(key=lambda m: abs(m["change_pct"]), reverse=True)

    top_flags = []
    for item in headlines[:8]:
        if item.consulting_label:
            top_flags.append({
                "company": item.company or item.source,
                "signal": item.consulting_label,
                "headline": item.title,
            })

    theme_names = [t.name for t in themes]
    risk_texts = [r.text for r in risks[:3]]

    return summarize_exec_summary(movers[:5], top_flags[:6], theme_names, risk_texts)


# ── Heatmap ───────────────────────────────────────────────────────────────────

def build_heatmap(price_history: list[dict], prices: list[PriceRecord]) -> Optional[HeatmapData]:
    """Compute 1D / 1W / 1M return matrix from price_history + today's prices."""
    if not price_history or not prices:
        return None

    commodity_ids = [p.commodity_id for p in prices if p.price is not None]
    if not commodity_ids:
        return None

    # Index history by date
    history_by_date: dict[str, dict] = {}
    for entry in price_history:
        history_by_date[entry.get("date", "")] = entry.get("prices", {})

    sorted_dates = sorted(history_by_date.keys())
    if not sorted_dates:
        return None

    today_prices = {p.commodity_id: p.price for p in prices if p.price is not None}
    today_str = sorted_dates[-1] if sorted_dates else ""

    def get_price(cid: str, n_days_ago: int) -> Optional[float]:
        if len(sorted_dates) <= n_days_ago:
            return None
        d = sorted_dates[-(n_days_ago + 1)]
        return history_by_date.get(d, {}).get(cid, {}).get("price")

    returns: dict[str, list] = {"1d": [], "1w": [], "1m": []}
    display_names: list[str] = []

    price_display = {p.commodity_id: p.display for p in prices}

    for cid in commodity_ids:
        current = today_prices.get(cid)
        if current is None:
            continue

        display_names.append(price_display.get(cid, cid))

        def _return(prev: Optional[float]) -> Optional[float]:
            if prev is None or prev == 0:
                return None
            return round((current - prev) / prev * 100, 2)

        returns["1d"].append(_return(get_price(cid, 1)))
        returns["1w"].append(_return(get_price(cid, 5)))
        returns["1m"].append(_return(get_price(cid, 21)))

    if not display_names:
        return None

    return HeatmapData(commodities=display_names, returns=returns)


# ── Correlation matrix ────────────────────────────────────────────────────────

def build_correlation(price_history: list[dict], prices: list[PriceRecord]) -> Optional[CorrelationData]:
    """Compute pairwise daily-return correlation matrix. Requires ≥20 days of history."""
    if len(price_history) < 20:
        return None

    commodity_ids = [p.commodity_id for p in prices if p.price is not None]
    if len(commodity_ids) < 2:
        return None

    sorted_history = sorted(price_history, key=lambda e: e.get("date", ""))

    # Build price series per commodity
    series: dict[str, list[float]] = {cid: [] for cid in commodity_ids}
    for entry in sorted_history:
        ps = entry.get("prices", {})
        for cid in commodity_ids:
            p = ps.get(cid, {}).get("price")
            series[cid].append(float(p) if p is not None else float("nan"))

    # Compute daily returns
    def daily_returns(prices_list: list[float]) -> list[float]:
        rets: list[float] = []
        for i in range(1, len(prices_list)):
            prev, curr = prices_list[i-1], prices_list[i]
            if prev and curr and prev != 0 and not (prev != prev) and not (curr != curr):
                rets.append((curr - prev) / prev)
            else:
                rets.append(float("nan"))
        return rets

    returns_map = {cid: daily_returns(series[cid]) for cid in commodity_ids}

    # Filter to commodities with sufficient data
    valid_ids = [cid for cid, rets in returns_map.items()
                 if sum(1 for r in rets if r == r) >= 15]

    if len(valid_ids) < 2:
        return None

    def pearson(xs: list[float], ys: list[float]) -> Optional[float]:
        pairs = [(x, y) for x, y in zip(xs, ys) if x == x and y == y]
        n = len(pairs)
        if n < 5:
            return None
        mean_x = sum(p[0] for p in pairs) / n
        mean_y = sum(p[1] for p in pairs) / n
        num = sum((p[0] - mean_x) * (p[1] - mean_y) for p in pairs)
        den_x = math.sqrt(sum((p[0] - mean_x) ** 2 for p in pairs))
        den_y = math.sqrt(sum((p[1] - mean_y) ** 2 for p in pairs))
        if den_x == 0 or den_y == 0:
            return None
        return round(num / (den_x * den_y), 2)

    matrix: list[list] = []
    for cid_a in valid_ids:
        row: list = []
        for cid_b in valid_ids:
            if cid_a == cid_b:
                row.append(1.0)
            else:
                row.append(pearson(returns_map[cid_a], returns_map[cid_b]))
        matrix.append(row)

    price_display = {p.commodity_id: p.display for p in prices}
    display_names = [price_display.get(cid, cid) for cid in valid_ids]

    return CorrelationData(commodities=display_names[:12], matrix=matrix[:12])


# ── Weekly brief ──────────────────────────────────────────────────────────────

def build_weekly_brief(
    opportunity_cards: list[OpportunityCard],
    account_briefs: list[AccountBrief],
    themes: list[SectorTheme],
    generated_at: datetime,
) -> WeeklyBrief:
    period = generated_at.strftime("Week of %d %b %Y")
    top_opps = [
        f"{card.company} [{card.service_line}] — "
        + (card.suggested_angle.split("—")[-1].strip() if "—" in card.suggested_angle else card.suggested_angle)
        for card in opportunity_cards[:5]
    ]
    hottest = [c.company for c in opportunity_cards[:5]]
    key_themes = [t.theme for t in themes[:4]]
    what_changed = [f"Top signals: {', '.join(hottest[:4])}."] if hottest else ["No signals detected."]

    return WeeklyBrief(
        period=period,
        top_opportunities=top_opps,
        hottest_accounts=hottest,
        key_themes=key_themes,
        what_changed=what_changed,
        opportunity_count=len(opportunity_cards),
        active_account_count=sum(1 for b in account_briefs if b.has_news),
    )


# ── Full digest assembly ──────────────────────────────────────────────────────

def build_digest(
    company_news: dict[str, list[NewsItem]],
    commodity_news: dict[str, list[NewsItem]],
    supplementary: list[NewsItem],
    prices: list[PriceRecord],
    macro: Optional[MacroSection] = None,
    eia_fundamentals: Optional[list[FundamentalsItem]] = None,
    regulatory: Optional[list[RegulatoryItem]] = None,
    price_history: Optional[list[dict]] = None,
) -> DailyDigest:
    from .summarize import enrich_opportunities, summarize_company_highlights, summarize_executive_brief
    from .process import tag_drivers, annotate_age

    now = datetime.now(timezone.utc)
    eia_fundamentals = eia_fundamentals or []
    regulatory = regulatory or []
    price_history = price_history or []

    # Annotate age and tag drivers on all items
    all_news_items: list[NewsItem] = []
    for items in company_news.values():
        all_news_items.extend(items)
    for items in commodity_news.values():
        all_news_items.extend(items)
    all_news_items.extend(supplementary)

    annotate_age(all_news_items)
    tag_drivers(all_news_items)

    # Core builders
    company_digests = build_company_digests(company_news)
    commodity_digests = build_commodity_digests(commodity_news, prices)
    headlines = top_headlines(company_news, commodity_news, supplementary)
    watch = what_to_watch(prices, company_digests)
    opps = build_opportunities(company_digests)
    sector_sums = build_sector_summaries(company_digests)

    # Enrich opportunities
    opp_dicts = [{"company": o.company, "signal": o.signal, "headline": o.headline} for o in opps]
    contexts = enrich_opportunities(opp_dicts)
    if contexts:
        for opp, ctx in zip(opps, contexts):
            opp.engagement_context = ctx

    # Company highlights
    top_co_names = list(dict.fromkeys(o.company for o in opps))[:3]
    top_co_digests = [d for d in company_digests if d.name in top_co_names]
    if top_co_digests:
        co_dicts = [
            {"name": d.name, "sector": d.sector,
             "signal": d.items[0].consulting_label if d.items else "",
             "headlines": [i.title for i in d.items]}
            for d in top_co_digests
        ]
        highlights = summarize_company_highlights(co_dicts)
        if highlights:
            co_map = {d.name: d for d in top_co_digests}
            for name, hl in zip(top_co_names, highlights):
                if name in co_map:
                    co_map[name].highlight = hl

    # Executive brief (legacy)
    brief: Optional[ExecutiveBrief] = None
    sector_dicts = [
        {"label": s.sector_label, "narrative": s.narrative, "active_count": len(s.active_companies)}
        for s in sector_sums if s.narrative
    ]
    if sector_dicts and opp_dicts:
        result = summarize_executive_brief(sector_dicts, opp_dicts)
        if result:
            narrative, themes_list = result
            brief = ExecutiveBrief(narrative=narrative, themes=themes_list)

    # New MI sections
    market_themes = build_market_themes(all_news_items, company_news)
    risks = build_risks(company_news, prices)
    events = build_events(now)
    outlook = build_outlook(commodity_news, eia_fundamentals)
    exec_summary = build_exec_summary(prices, headlines, market_themes, risks)

    # Heatmap & correlation
    heatmap = build_heatmap(price_history, prices) if price_history else None
    correlations = build_correlation(price_history, prices) if price_history else None

    # Macro geo signals
    if macro and all_news_items:
        from .process import extract_geopolitical
        geo = extract_geopolitical(all_news_items)
        macro.geopolitical = geo

    # Demand-driver intel
    opportunity_radar = build_opportunity_radar(company_news)
    account_briefs = build_account_briefs(company_news, opportunity_radar)
    sector_themes = build_sector_themes(opportunity_radar)
    weekly_brief = build_weekly_brief(opportunity_radar, account_briefs, sector_themes, now)

    return DailyDigest(
        generated_at=now,
        executive_brief=brief,
        exec_summary=exec_summary,
        macro=macro,
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
        outlook=outlook,
        fundamentals=eia_fundamentals,
        themes=market_themes,
        risks=risks,
        events=events,
        regulatory=regulatory,
        heatmap=heatmap,
        correlations=correlations,
    )
