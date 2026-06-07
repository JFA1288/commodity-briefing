import feedparser
import requests
import re
import html
from datetime import datetime, timezone
from time import mktime
from pathlib import Path

FEEDS = [
    {
        "name": "Google News - Crude Oil",
        "url": "https://news.google.com/rss/search?q=crude+oil&hl=en-US&gl=US&ceid=US:en",
    },
    {
        "name": "Google News - LNG",
        "url": "https://news.google.com/rss/search?q=LNG&hl=en-US&gl=US&ceid=US:en",
    },
    {
        "name": "Google News - Natural Gas",
        "url": "https://news.google.com/rss/search?q=natural+gas&hl=en-US&gl=US&ceid=US:en",
    },
    {
        "name": "Google News - Brent",
        "url": "https://news.google.com/rss/search?q=Brent+oil&hl=en-US&gl=US&ceid=US:en",
    },
    {
        "name": "Google News - WTI",
        "url": "https://news.google.com/rss/search?q=WTI+oil&hl=en-US&gl=US&ceid=US:en",
    },
    {
        "name": "Google News - Palm Oil",
        "url": "https://news.google.com/rss/search?q=palm+oil&hl=en-US&gl=US&ceid=US:en",
    },
    {
        "name": "Google News - Commodity Markets",
        "url": "https://news.google.com/rss/search?q=commodity+markets&hl=en-US&gl=US&ceid=US:en",
    },
    {
        "name": "OilPrice.com",
        "url": "https://oilprice.com/rss/main",
    },
    {
        "name": "EIA Today in Energy",
        "url": "https://www.eia.gov/rss/todayinenergy.xml",
    },
    {
        "name": "Google News - EIA Energy",
        "url": "https://news.google.com/rss/search?q=EIA+energy+oil+gas&hl=en-US&gl=US&ceid=US:en",
    },
    {
        "name": "Reuters Commodities & Energy",
        "url": "https://reutersbest.com/topic/commodities-energy/feed/",
    },
    {
        "name": "Google News - Reuters Crude/LNG/Brent",
        "url": "https://news.google.com/rss/search?q=crude+oil+OR+LNG+OR+Brent+site:reuters.com&hl=en-US&gl=US&ceid=US:en",
    },
]

KEYWORDS = [
    "oil", "gas", "lng", "crude", "brent", "wti",
    "commodity", "energy", "palm oil", "petrochemical", "opec",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    )
}


def fetch_feed(source: dict) -> feedparser.FeedParserDict:
    try:
        parsed = feedparser.parse(source["url"], request_headers=HEADERS)
        if parsed.bozo and not parsed.entries:
            # Fallback: fetch raw with requests then parse
            resp = requests.get(source["url"], headers=HEADERS, timeout=15)
            resp.raise_for_status()
            parsed = feedparser.parse(resp.text)
        return parsed
    except Exception as exc:
        print(f"  [WARN] Could not fetch {source['name']}: {exc}")
        return feedparser.FeedParserDict(entries=[])


def entry_to_utc(entry) -> datetime:
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        return datetime.fromtimestamp(mktime(entry.published_parsed), tz=timezone.utc)
    if hasattr(entry, "updated_parsed") and entry.updated_parsed:
        return datetime.fromtimestamp(mktime(entry.updated_parsed), tz=timezone.utc)
    return datetime.now(tz=timezone.utc)


def first_two_sentences(text: str) -> str:
    if not text:
        return ""
    # Strip HTML tags then decode entities
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = html.unescape(clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    # Split on sentence-ending punctuation followed by whitespace
    parts = re.split(r"(?<=[.!?])\s+", clean)
    return " ".join(parts[:2])


def passes_keyword_filter(title: str, summary: str) -> bool:
    haystack = (title + " " + summary).lower()
    return any(kw in haystack for kw in KEYWORDS)


def parse_feed(source: dict) -> list[dict]:
    feed = fetch_feed(source)
    articles = []
    for entry in feed.entries:
        title = getattr(entry, "title", "").strip()
        url = getattr(entry, "link", "").strip()
        raw_summary = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
        summary = first_two_sentences(raw_summary)
        pub_dt = entry_to_utc(entry)

        if not title or not url:
            continue
        if not passes_keyword_filter(title, summary):
            continue

        articles.append({
            "title": title,
            "source": source["name"],
            "published": pub_dt,
            "url": url,
            "summary": summary,
        })
    return articles


def deduplicate(articles: list[dict]) -> list[dict]:
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    unique = []
    for a in articles:
        norm_title = a["title"].lower().strip()
        if a["url"] in seen_urls or norm_title in seen_titles:
            continue
        seen_urls.add(a["url"])
        seen_titles.add(norm_title)
        unique.append(a)
    return unique


def write_output(articles: list[dict], run_dt: datetime) -> Path:
    filename = run_dt.strftime("%Y%m%d_%H%M") + "_commodity_news.txt"
    output_path = Path(__file__).parent / filename

    with open(output_path, "w", encoding="utf-8") as f:
        for a in articles:
            pub_str = a["published"].strftime("%Y-%m-%d %H:%M UTC")
            f.write(f"[{a['source']}] | {pub_str}\n")
            f.write(f"{a['title']}\n")
            f.write(f"{a['url']}\n")
            if a["summary"]:
                f.write(f"{a['summary']}\n")
            f.write("---\n")

    return output_path


def main():
    run_dt = datetime.now(tz=timezone.utc)
    print(f"Commodity News Fetcher — {run_dt.strftime('%Y-%m-%d %H:%M UTC')}\n")

    all_articles: list[dict] = []

    for source in FEEDS:
        articles = parse_feed(source)
        # Count before dedup for per-source reporting
        print(f"  {source['name']}: {len(articles)} articles passed filter")
        all_articles.extend(articles)

    all_articles = deduplicate(all_articles)
    all_articles.sort(key=lambda a: a["published"], reverse=True)

    output_path = write_output(all_articles, run_dt)

    print(f"\nTotal unique articles: {len(all_articles)}")
    print(f"Output written to: {output_path}")


if __name__ == "__main__":
    main()
