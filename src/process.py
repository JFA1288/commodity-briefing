"""Dedup, relevance filter, sector grouping, and demand-driver trigger detection."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import config
from .models import NewsItem, TriggerMatch

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
    return " ".join(sorted(tokens))  # sorted bag-of-words fingerprint


def dedup(items: list[NewsItem]) -> list[NewsItem]:
    """Remove near-duplicate headlines; prefer the item from a higher-weight source."""
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
            # keep whichever has a higher source weight, or earlier publish date
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
    # add commodity group terms
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
    ]
    return list(dict.fromkeys(t for t in terms if t))


_ENTITY_TERMS: Optional[list[str]] = None


def _entity_terms() -> list[str]:
    global _ENTITY_TERMS
    if _ENTITY_TERMS is None:
        _ENTITY_TERMS = _build_entity_terms()
    return _ENTITY_TERMS


# Obvious noise patterns – skip items where title matches
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


# ── Sector grouping ───────────────────────────────────────────────────────────

# ── Demand-driver trigger detection ──────────────────────────────────────────

def detect_triggers(item: NewsItem) -> list[TriggerMatch]:
    """Match demand-driver keywords against a news item; return all matching triggers."""
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


def group_by_sector(company_news: dict[str, list[NewsItem]]) -> dict[str, list[tuple[str, list[NewsItem]]]]:
    """Returns {sector: [(company_name, [items]), ...]}"""
    cfg = config.load()
    sector_map: dict[str, str] = {c["name"]: c["sector"] for c in cfg.get("companies", [])}
    groups: dict[str, list] = {}
    for name, items in company_news.items():
        sector = sector_map.get(name, "other")
        groups.setdefault(sector, []).append((name, items))
    return groups
