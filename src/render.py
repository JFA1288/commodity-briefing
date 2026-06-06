"""Write docs/data/latest.json, append price_history.json, write daily digest HTML."""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from . import config
from .models import DailyDigest, PriceRecord

_DOCS = Path(__file__).parent.parent / "docs"
_DATA = _DOCS / "data"
_DIGESTS = _DOCS / "digests"
_TEMPLATES = Path(__file__).parent.parent / "src" / "templates"

_DATA.mkdir(parents=True, exist_ok=True)
_DIGESTS.mkdir(parents=True, exist_ok=True)


def _serialise(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    raise TypeError(type(obj))


def write_latest_json(digest: DailyDigest) -> Path:
    out = _DATA / "latest.json"
    payload = json.loads(digest.model_dump_json())
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"  [render] wrote {out}")
    return out


def append_price_history(prices: list[PriceRecord]) -> Path:
    hist_path = _DATA / "price_history.json"
    cap_days = config.get("price_history_days", 90)
    cutoff = datetime.now(timezone.utc) - timedelta(days=cap_days)

    existing: list[dict] = []
    if hist_path.exists():
        try:
            existing = json.loads(hist_path.read_text())
        except Exception:
            existing = []

    # keep only entries within the cap window
    kept = [e for e in existing if _dt(e.get("date", "")) > cutoff]

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    entry = {
        "date": today_str,
        "prices": {p.commodity_id: {"price": p.price, "source": p.source, "unit": p.unit}
                   for p in prices},
    }
    # replace today's entry if it already exists
    kept = [e for e in kept if e.get("date") != today_str]
    kept.append(entry)
    kept.sort(key=lambda e: e.get("date", ""))

    hist_path.write_text(json.dumps(kept, ensure_ascii=False, indent=2))
    print(f"  [render] appended price history ({len(kept)} days) → {hist_path}")
    return hist_path


def _dt(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def write_digest_html(digest: DailyDigest) -> Path:
    date_str = digest.generated_at.strftime("%Y-%m-%d")
    out_path = _DIGESTS / f"{date_str}.html"

    env = Environment(loader=FileSystemLoader(str(_TEMPLATES)), autoescape=True)
    tmpl = env.get_template("digest.html.j2")

    sgt_offset = timedelta(hours=8)
    sgt_time = digest.generated_at + sgt_offset

    html = tmpl.render(
        digest=digest,
        date_str=date_str,
        utc_time=digest.generated_at.strftime("%Y-%m-%d %H:%M UTC"),
        sgt_time=sgt_time.strftime("%Y-%m-%d %H:%M SGT"),
        cfg=config.load(),
    )
    out_path.write_text(html, encoding="utf-8")
    print(f"  [render] wrote digest → {out_path}")
    return out_path


def update_archive_index(digest_dir: Path = _DIGESTS) -> None:
    """Write docs/data/archive.json listing all past digests."""
    files = sorted(digest_dir.glob("*.html"), reverse=True)
    entries = [{"date": f.stem, "path": f"digests/{f.name}"} for f in files]
    out = _DATA / "archive.json"
    out.write_text(json.dumps(entries, indent=2))
    print(f"  [render] archive index: {len(entries)} entries → {out}")
