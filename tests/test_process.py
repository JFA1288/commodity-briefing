"""Tests for dedup, relevance filter, and ranking."""

import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.models import NewsItem
from src.process import dedup, is_relevant, filter_recent
from src.digest import _flag_item, _recency_score, _score


def _item(title, url="http://example.com/1", source="Test", published=None, summary=""):
    return NewsItem(title=title, url=url, source=source, published=published, summary=summary)


# ── dedup ──────────────────────────────────────────────────────────────────

def test_dedup_removes_exact_duplicate_url():
    items = [_item("Oil price rises", url="http://a.com/1"),
             _item("Oil price rises again", url="http://a.com/1")]  # same url
    result = dedup(items)
    assert len(result) == 1


def test_dedup_removes_near_duplicate_title():
    items = [
        _item("Brent crude oil rises on supply fears", url="http://a.com/1"),
        _item("Brent crude oil rises on supply fears!", url="http://a.com/2"),  # near-dup
    ]
    result = dedup(items)
    assert len(result) == 1


def test_dedup_keeps_distinct_items():
    items = [
        _item("Shell reports record profits", url="http://a.com/1"),
        _item("BHP acquires copper mine", url="http://a.com/2"),
        _item("Brent oil drops 2%", url="http://a.com/3"),
    ]
    result = dedup(items)
    assert len(result) == 3


def test_dedup_strips_query_params():
    items = [
        _item("Oil rises", url="http://a.com/1?ref=feed"),
        _item("Oil rises", url="http://a.com/1?utm_source=twitter"),
    ]
    result = dedup(items)
    assert len(result) == 1


# ── relevance ──────────────────────────────────────────────────────────────

def test_relevant_commodity_term():
    item = _item("Brent crude oil rallies above $90")
    assert is_relevant(item)


def test_relevant_company_alias():
    item = _item("PETRONAS signs new LNG contract in Malaysia")
    assert is_relevant(item)


def test_relevant_general_energy():
    item = _item("Southeast Asia energy demand rises as coal consumption grows")
    assert is_relevant(item)


def test_not_relevant_noise():
    item = _item("Celebrity chef wins cooking award at film festival")
    assert not is_relevant(item)


def test_not_relevant_sports():
    item = _item("Football team wins championship after dramatic finale")
    assert not is_relevant(item)


# ── recency filter ─────────────────────────────────────────────────────────

def test_filter_recent_keeps_new():
    now = datetime.now(timezone.utc)
    item = _item("Fresh oil news", published=now)
    result = filter_recent([item], hours=24)
    assert len(result) == 1


def test_filter_recent_drops_old():
    from datetime import timedelta
    old = datetime.now(timezone.utc) - timedelta(hours=48)
    item = _item("Old oil news", published=old)
    result = filter_recent([item], hours=24)
    assert len(result) == 0


def test_filter_recent_keeps_no_date():
    item = _item("Unknown date news")
    result = filter_recent([item], hours=24)
    assert len(result) == 1  # items without dates are kept


# ── flag_item ──────────────────────────────────────────────────────────────

def test_flag_earnings():
    item = _item("Shell posts record quarterly earnings beating expectations")
    flags = _flag_item(item)
    assert "earnings" in flags


def test_flag_outage():
    item = _item("Force majeure declared at LNG terminal after explosion")
    flags = _flag_item(item)
    assert "outage" in flags


def test_flag_ma():
    item = _item("BHP announces acquisition of copper mining stake")
    flags = _flag_item(item)
    assert "ma" in flags


def test_flag_geopolitical():
    item = _item("OPEC+ meeting set to decide on production cuts")
    flags = _flag_item(item)
    assert "geopolitical" in flags


def test_flag_no_false_positive():
    item = _item("Iron ore price steady amid stable China demand")
    flags = _flag_item(item)
    # Should not have earnings or outage flags
    assert "earnings" not in flags
    assert "outage" not in flags


# ── scoring ────────────────────────────────────────────────────────────────

def test_score_higher_for_recent():
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    fresh = _item("Fresh news", published=now)
    fresh.flags = []
    old = _item("Old news", published=now - timedelta(hours=24))
    old.flags = []
    assert _score(fresh) > _score(old)


def test_score_higher_with_flags():
    now = datetime.now(timezone.utc)
    flagged = _item("Earnings beat", published=now)
    flagged.flags = ["earnings"]
    plain = _item("Steady prices", published=now)
    plain.flags = []
    assert _score(flagged) > _score(plain)


def test_recency_score_bounds():
    score_now = _recency_score(datetime.now(timezone.utc))
    assert 0.9 < score_now <= 1.0
    from datetime import timedelta
    score_old = _recency_score(datetime.now(timezone.utc) - timedelta(hours=100))
    assert score_old < 0.1


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
