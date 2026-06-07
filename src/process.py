"""Dedup, relevance filter, driver tagging, supply/demand classification, regulatory extraction."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import config
from .models import NewsItem, TriggerMatch, RegulatoryItem, GeopoliticalItem

# ── Dedup ─────────────────────────────────────────────────────────────────────

_STOPWORDS = frozenset(
    "a an the in on at of to for and or is are was were by with from be this that it its "
    "has have had will would could should been being s".split()
)


def _normalize_title(title: str) -> str:
    t = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    t = t.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    tokens = [w for w in t.split() if w not in _STOPWORDS and len(w) > 2]
    return " ".join(sorted(tokens))


def dedup(items: list[NewsItem]) -> list[NewsItem]:
    """Remove near-duplicate headlines; prefer higher-weight source."""
    seen_urls: set[str] = set()
    seen_norms: dict[str, NewsItem] = {}
    out: list[NewsItem] = []

    source_weights = {f["name"]: f.get("weight", 1.0)
                      for f in config.get("supplementary_feeds", [])}
    source_weights["Google News"] = 1.0

    def weight(item: NewsItem) -> float:
        return source_weights.get(item.source, 1.0)

    for item in items:
        url = item.url.split("?")[0].rstrip("/")
        if url in seen_urls:
            continue
        seen_urls.add(url)

        norm = _normalize_title(item.title)
        item.title_norm = norm

        if norm in seen_norms:
            existing = seen_norms[norm]
            if weight(item) > weight(existing):
                seen_norms[norm] = item
                out = [item if x is existing else x for x in out]
        else:
            seen_norms[norm] = item
            out.append(item)

    return out


# ── Relevance filter ──────────────────────────────────────────────────────────

def _build_entity_terms() -> list[str]:
    cfg = config.load()
    terms: list[str] = []
    for company in cfg.get("companies", []):
        terms.extend(a.lower() for a in company.get("aliases", []))
    for c in cfg.get("commodities", []):
        terms.append(c["display"].lower())
        terms.append(c["id"].replace("_", " ").lower())
    terms += [
        "crude oil", "brent", "wti", "tapis", "dubai", "oman",
        "lng", "liquefied natural gas", "jkm", "henry hub",
        "gasoil", "diesel", "jet fuel", "gasoline",
        "coal", "newcastle",
        "iron ore", "copper", "nickel", "aluminium", "aluminum",
        "tin", "bauxite", "palm oil", "soybeans", "wheat",
        "commodity", "commodities", "energy", "oil", "gas",
        "refinery", "tanker", "cargo", "shipment", "pipeline",
        "upstream", "downstream", "LME", "CBOT", "ICE", "NYMEX",
        "OPEC", "trading", "Singapore commodity",
    ]
    return list(dict.fromkeys(t for t in terms if t))


_ENTITY_TERMS: Optional[list[str]] = None


def _entity_terms() -> list[str]:
    global _ENTITY_TERMS
    if _ENTITY_TERMS is None:
        _ENTITY_TERMS = _build_entity_terms()
    return _ENTITY_TERMS


_NOISE_PATTERNS = [
    re.compile(r"\b(horoscope|sports|football|cricket|celebrity|recipe|travel|fashion)\b", re.I),
    re.compile(r"\b(covid|pandemic|vaccine|hospital|medical)\b", re.I),
    re.compile(r"\b(movie|film|song|music|actor|actress)\b", re.I),
]


def is_relevant(item: NewsItem) -> bool:
    text = (item.title + " " + item.summary).lower()
    for pat in _NOISE_PATTERNS:
        if pat.search(text):
            return False
    for term in _entity_terms():
        if term in text:
            return True
    return False


def filter_relevant(items: list[NewsItem]) -> list[NewsItem]:
    return [i for i in items if is_relevant(i)]


# ── Recency filter ────────────────────────────────────────────────────────────

def filter_recent(items: list[NewsItem], hours: Optional[int] = None) -> list[NewsItem]:
    if hours is None:
        hours = config.get("news", {}).get("lookback_hours", 36)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out = []
    for item in items:
        if item.published is None or item.published >= cutoff:
            out.append(item)
    return out


# ── Age annotation ────────────────────────────────────────────────────────────

def annotate_age(items: list[NewsItem]) -> list[NewsItem]:
    now = datetime.now(timezone.utc)
    for item in items:
        if item.published:
            item.age_h = round((now - item.published).total_seconds() / 3600, 1)
    return items


# ── Market driver tagging ─────────────────────────────────────────────────────

def tag_drivers(items: list[NewsItem]) -> list[NewsItem]:
    """Attach market-driver tags (china_demand, opec_supply, etc.) to each item."""
    cfg = config.load()
    driver_kw: dict[str, list[str]] = cfg.get("keyword_sets", {}).get("drivers", {})

    for item in items:
        text = (item.title + " " + item.summary).lower()
        item.drivers = []
        for driver_key, keywords in driver_kw.items():
            for kw in keywords:
                if kw.lower() in text:
                    item.drivers.append(driver_key)
                    break

    return items


# ── Supply/demand classification ──────────────────────────────────────────────

def classify_supply_demand(item: NewsItem) -> tuple[list[str], list[str]]:
    """Return (supply_signals, demand_signals) keyword lists found in the item."""
    cfg = config.load()
    kw_sets = cfg.get("keyword_sets", {})
    supply_kw = kw_sets.get("supply_side", [])
    demand_kw = kw_sets.get("demand_side", [])
    text = (item.title + " " + item.summary).lower()
    supply = [kw for kw in supply_kw if kw.lower() in text]
    demand = [kw for kw in demand_kw if kw.lower() in text]
    return supply, demand


# ── Regulatory extraction ─────────────────────────────────────────────────────

_JURISDICTION_TERMS = {
    "Indonesia": ["indonesia", "indonesian", "jakarta", "ojk", "esdm"],
    "Malaysia": ["malaysia", "malaysian", "kuala lumpur", "sc malaysia", "petronas"],
    "Thailand": ["thailand", "thai", "bangkok", "egat", "set thailand"],
    "Singapore": ["singapore", "mas singapore", "ies", "edb singapore"],
    "Regional / Global": ["asean", "opec", "iea", "world bank", "g20", "un", "imo"],
}


def extract_regulatory(items: list[NewsItem]) -> list[RegulatoryItem]:
    """Extract regulatory/policy items and classify them by jurisdiction."""
    cfg = config.load()
    reg_keywords = cfg.get("material_event_keywords", {}).get("regulatory", [])

    regulatory: list[RegulatoryItem] = []
    for item in items:
        text = (item.title + " " + item.summary).lower()
        if not any(kw.lower() in text for kw in reg_keywords):
            continue

        # Determine jurisdiction
        jurisdiction = "Other"
        for jur, terms in _JURISDICTION_TERMS.items():
            if any(t in text for t in terms):
                jurisdiction = jur
                break

        # Extract affected commodities and companies
        commodities: list[str] = []
        for c in cfg.get("commodities", []):
            if c["display"].lower() in text or c["id"].replace("_", " ") in text:
                commodities.append(c["display"])

        companies: list[str] = []
        for comp in cfg.get("companies", []):
            if any(a.lower() in text for a in comp.get("aliases", [])):
                companies.append(comp["name"])

        regulatory.append(RegulatoryItem(
            title=item.title,
            jurisdiction=jurisdiction,
            commodities=commodities[:3],
            companies=companies[:3],
            source=item.source,
            url=item.url,
        ))

    return regulatory


# ── Geopolitical detection ────────────────────────────────────────────────────

def extract_geopolitical(items: list[NewsItem]) -> list[GeopoliticalItem]:
    """Extract geopolitical/macro watch items from news."""
    cfg = config.load()
    geo_keywords = cfg.get("keyword_sets", {}).get("geopolitical_watch", [])
    now = datetime.now(timezone.utc)

    geo: list[GeopoliticalItem] = []
    seen: set[str] = set()
    for item in items:
        text = (item.title + " " + item.summary).lower()
        if not any(kw.lower() in text for kw in geo_keywords):
            continue
        url = item.url.split("?")[0].rstrip("/")
        if url in seen:
            continue
        seen.add(url)
        age_h = 0.0
        if item.published:
            age_h = round((now - item.published).total_seconds() / 3600, 1)
        geo.append(GeopoliticalItem(
            title=item.title,
            source=item.source,
            url=item.url,
            age_h=age_h,
        ))

    return geo[:10]


# ── Demand-driver trigger detection ──────────────────────────────────────────

def detect_triggers(item: NewsItem) -> list[TriggerMatch]:
    """Match demand-driver keywords; return all matching triggers."""
    cfg = config.load()
    drivers: dict = cfg.get("demand_drivers", {})
    sl_labels: dict = cfg.get("service_lines", {})
    text = (item.title + " " + item.summary).lower()

    matches: list[TriggerMatch] = []
    for driver_key, driver_cfg in drivers.items():
        keywords: list[str] = driver_cfg.get("keywords", [])
        matched = [kw for kw in keywords if kw.lower() in text]
        if matched:
            sl_key = driver_cfg.get("service_line", driver_key)
            matches.append(TriggerMatch(
                driver=driver_key,
                service_line=sl_labels.get(sl_key, sl_key.replace("_", " ").title()),
                suggested_angle=driver_cfg.get("suggested_angle", ""),
                keywords_matched=matched[:5],
                materiality_weight=float(driver_cfg.get("materiality_weight", 1.0)),
            ))
    return matches


# ── Sector grouping ───────────────────────────────────────────────────────────

def group_by_sector(company_news: dict[str, list[NewsItem]]) -> dict[str, list[tuple[str, list[NewsItem]]]]:
    """Returns {sector: [(company_name, [items]), ...]}"""
    cfg = config.load()
    sector_map: dict[str, str] = {c["name"]: c["sector"] for c in cfg.get("companies", [])}
    groups: dict[str, list] = {}
    for name, items in company_news.items():
        sector = sector_map.get(name, "other")
        groups.setdefault(sector, []).append((name, items))
    return groups
