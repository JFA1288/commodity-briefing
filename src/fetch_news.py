"""Fetch news from Google News RSS (per-company + per-commodity) and supplementary feeds."""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

import feedparser
import httpx

from . import config
from .models import NewsItem

_CACHE_DIR = Path(__file__).parent.parent / ".cache" / "feeds"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

_GNEWS_BASE = "https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={ceid}"


def _cache_key(url: str) -> Path:
    h = hashlib.md5(url.encode()).hexdigest()
    return _CACHE_DIR / f"{h}.json"


def _cache_load(url: str, ttl_min: int) -> Optional[list[dict]]:
    path = _cache_key(url)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        cached_at = datetime.fromisoformat(data["cached_at"])
        age_min = (datetime.now(timezone.utc) - cached_at).total_seconds() / 60
        if age_min < ttl_min:
            return data["entries"]
    except Exception:
        pass
    return None


def _cache_save(url: str, entries: list[dict]) -> None:
    path = _cache_key(url)
    path.write_text(json.dumps({
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "entries": entries,
    }))


def _first_two_sentences(text: str) -> str:
    if not text:
        return ""
    import html as _html
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = _html.unescape(clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    parts = re.split(r"(?<=[.!?])\s+", clean)
    return " ".join(parts[:2])


def _parse_dt(entry: feedparser.FeedParserDict) -> Optional[datetime]:
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def _entry_to_item(entry: feedparser.FeedParserDict, source: str) -> NewsItem:
    title = getattr(entry, "title", "") or ""
    link = getattr(entry, "link", "") or ""
    summary = getattr(entry, "summary", "") or ""
    summary = _first_two_sentences(summary)
    return NewsItem(
        title=title.strip(),
        url=link.strip(),
        source=source,
        published=_parse_dt(entry),
        summary=summary,
    )


def _fetch_feed(url: str, source_name: str, ttl_min: int, delay_s: float) -> list[NewsItem]:
    cached = _cache_load(url, ttl_min)
    if cached is not None:
        # reconstruct items from cached dicts
        items = []
        for d in cached:
            try:
                items.append(NewsItem(**d))
            except Exception:
                pass
        return items

    time.sleep(delay_s)
    headers = {"User-Agent": "Mozilla/5.0 (compatible; CommodityBot/1.0; +https://github.com)"}
    try:
        resp = httpx.get(url, headers=headers, timeout=config.get("news", {}).get("request_timeout_s", 15),
                         follow_redirects=True)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        if feed.bozo and not feed.entries:
            feed = feedparser.parse(resp.text)
    except Exception as exc:
        print(f"  [news] WARN: failed to fetch {source_name} ({url[:60]}…): {exc}")
        return []

    items = [_entry_to_item(e, source_name) for e in feed.entries]
    _cache_save(url, [i.model_dump(mode="json") for i in items])
    return items


def _gnews_url(query: str, region: dict) -> str:
    return _GNEWS_BASE.format(
        q=quote_plus(query),
        hl=region["hl"],
        gl=region["gl"],
        ceid=region["ceid"],
    )


def _pick_region(idx: int) -> dict:
    regions = config.get("google_news_regions", [{"gl": "SG", "ceid": "SG:en", "hl": "en-SG"}])
    return regions[idx % len(regions)]


def fetch_company_news() -> dict[str, list[NewsItem]]:
    """Return {company_name: [NewsItem, ...]} for all configured companies."""
    cfg = config.load()
    news_cfg = cfg.get("news", {})
    ttl = news_cfg.get("feed_cache_ttl_min", 60)
    delay = news_cfg.get("inter_request_delay_s", 1.2)

    results: dict[str, list[NewsItem]] = {}
    for idx, company in enumerate(cfg.get("companies", [])):
        name = company["name"]
        # Use explicit search_query if provided; otherwise fall back to aliases
        if company.get("search_query"):
            query = company["search_query"]
        else:
            aliases = company.get("aliases", [name])
            query_parts = list(dict.fromkeys(aliases[:3]))
            query = " OR ".join(f'"{p}"' for p in query_parts)

        region = _pick_region(idx)
        url = _gnews_url(query, region)
        print(f"  [news] Fetching Google News for {name} …")
        items = _fetch_feed(url, "Google News", ttl, delay)

        # tag each item with the company
        for item in items:
            item.company = name
        results[name] = items

    return results


def fetch_commodity_news() -> dict[str, list[NewsItem]]:
    """Return {commodity_id: [NewsItem, ...]} for commodity search queries."""
    cfg = config.load()
    news_cfg = cfg.get("news", {})
    ttl = news_cfg.get("feed_cache_ttl_min", 60)
    delay = news_cfg.get("inter_request_delay_s", 1.2)

    results: dict[str, list[NewsItem]] = {}
    commodity_groups = {
        "crude_oil": "crude oil Brent WTI",
        "natgas_lng": "LNG natural gas JKM price",
        "refined": "Singapore gasoil jet fuel refined products",
        "coal": "thermal coal Newcastle price",
        "base_metals": "copper nickel aluminium LME metals price",
        "bulk": "iron ore bauxite price",
        "agri": "soybeans wheat palm oil price",
    }
    for idx, (group_id, query_str) in enumerate(commodity_groups.items()):
        region = _pick_region(idx + 5)
        url = _gnews_url(query_str, region)
        print(f"  [news] Fetching Google News for commodity group '{group_id}' …")
        items = _fetch_feed(url, "Google News", ttl, delay)
        for item in items:
            item.commodity = group_id
        results[group_id] = items

    return results


def fetch_supplementary() -> list[NewsItem]:
    """Fetch all supplementary RSS feeds, return flat list with source weights embedded."""
    cfg = config.load()
    news_cfg = cfg.get("news", {})
    ttl = news_cfg.get("feed_cache_ttl_min", 60)
    delay = news_cfg.get("inter_request_delay_s", 1.2)

    all_items: list[NewsItem] = []
    for feed_def in cfg.get("supplementary_feeds", []):
        name = feed_def["name"]
        url = feed_def["url"]
        print(f"  [news] Fetching supplementary feed: {name} …")
        items = _fetch_feed(url, name, ttl, delay)
        all_items.extend(items)

    return all_items
