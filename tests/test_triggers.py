"""Tests for demand-driver trigger detection and opportunity scoring."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.models import NewsItem
from src.process import detect_triggers
from src.digest import build_opportunity_radar, build_sector_themes


def _item(title: str, summary: str = "", source: str = "Google News") -> NewsItem:
    return NewsItem(
        title=title,
        url="https://example.com/test",
        source=source,
        published=datetime.now(timezone.utc),
        summary=summary,
    )


class TestTriggerDetection:
    def test_ma_trigger(self):
        item = _item("Shell announces acquisition of Singapore LNG terminal")
        assert "ma" in [t.driver for t in detect_triggers(item)]

    def test_capital_projects_trigger(self):
        item = _item("Pertamina reaches FID on new refinery expansion")
        assert "capital_projects" in [t.driver for t in detect_triggers(item)]

    def test_digital_tech_trigger(self):
        item = _item("PTT launches digital transformation programme with ERP implementation")
        assert "digital_tech" in [t.driver for t in detect_triggers(item)]

    def test_cyber_trigger(self):
        item = _item("Pertamina suffers ransomware attack on trading systems")
        assert "cyber" in [t.driver for t in detect_triggers(item)]

    def test_no_trigger_for_noise(self):
        item = _item("Oil price surges on OPEC news")
        assert all(t.driver not in ("ma", "digital_tech", "cyber") for t in detect_triggers(item))

    def test_multiple_triggers(self):
        item = _item("BHP announces CEO resignation amid restructuring and cost cutting")
        drivers = [t.driver for t in detect_triggers(item)]
        assert "leadership" in drivers
        assert "cost_performance" in drivers

    def test_keywords_matched_populated(self):
        item = _item("Shell completes JV acquisition of Chevron stake in pipeline")
        ma = next((t for t in detect_triggers(item) if t.driver == "ma"), None)
        assert ma is not None
        assert len(ma.keywords_matched) > 0

    def test_materiality_weight_positive(self):
        item = _item("Rio Tinto announces major capital expenditure for copper expansion")
        for t in detect_triggers(item):
            assert t.materiality_weight > 0

    def test_service_line_populated(self):
        item = _item("PETRONAS faces regulatory investigation and fine over emissions compliance")
        reg = next((t for t in detect_triggers(item) if t.driver == "regulation_risk"), None)
        assert reg is not None
        assert reg.service_line

    def test_suggested_angle_populated(self):
        item = _item("Mercuria appoints new CEO after CFO steps down")
        leadership = next((t for t in detect_triggers(item) if t.driver == "leadership"), None)
        assert leadership is not None
        assert leadership.suggested_angle


class TestOpportunityScoring:
    def test_score_positive(self):
        cards = build_opportunity_radar({"Shell": [_item("Shell announces JV acquisition in Singapore")]})
        assert len(cards) > 0
        assert cards[0].score > 0

    def test_score_is_float(self):
        cards = build_opportunity_radar({"BHP": [_item("BHP reaches FID on new copper plant")]})
        assert len(cards) > 0
        assert isinstance(cards[0].score, float)

    def test_score_bounded(self):
        cards = build_opportunity_radar({"BHP": [_item("BHP reaches FID on new copper greenfield expansion")]})
        assert len(cards) > 0
        assert 0 < cards[0].score < 20

    def test_one_card_per_company(self):
        items = [
            _item("Shell acquires LNG terminal"),
            _item("Shell announces ERP digital transformation"),
            _item("Shell faces ransomware breach"),
        ]
        cards = build_opportunity_radar({"Shell": items})
        assert [c.company for c in cards].count("Shell") == 1

    def test_highest_materiality_per_item(self):
        # Each item uses its highest-materiality trigger; one card per company
        items = [
            _item("Shell acquires LNG terminal"),
            _item("Shell faces minor supply disruption"),
        ]
        cards = build_opportunity_radar({"Shell": items})
        assert len(cards) == 1

    def test_multiple_companies(self):
        news = {
            "Shell": [_item("Shell announces acquisition of LNG terminal")],
            "BHP": [_item("BHP reaches final investment decision on copper expansion")],
        }
        cards = build_opportunity_radar(news)
        companies = [c.company for c in cards]
        assert "Shell" in companies
        assert "BHP" in companies


class TestSectorThemes:
    def test_theme_requires_two_accounts(self):
        news = {"Shell": [_item("Shell completes acquisition of LNG terminal")]}
        cards = build_opportunity_radar(news)
        themes = build_sector_themes(cards)
        assert next((t for t in themes if t.driver == "ma"), None) is None

    def test_theme_created_for_two_accounts(self):
        news = {
            "Shell": [_item("Shell acquires stake in LNG terminal")],
            "BHP": [_item("BHP completes acquisition of copper miner")],
        }
        cards = build_opportunity_radar(news)
        themes = build_sector_themes(cards)
        ma_theme = next((t for t in themes if t.driver == "ma"), None)
        assert ma_theme is not None
        assert len(ma_theme.accounts) >= 2

    def test_theme_has_service_lines(self):
        news = {
            "PETRONAS": [_item("PETRONAS acquires upstream assets via JV")],
            "PTT": [_item("PTT completes acquisition of refinery stake")],
        }
        for theme in build_sector_themes(build_opportunity_radar(news)):
            assert theme.service_lines

    def test_theme_description_populated(self):
        news = {
            "Shell": [_item("Shell acquires LNG stake")],
            "TotalEnergies Trading Asia": [_item("TotalEnergies completes divestiture of assets")],
        }
        for theme in build_sector_themes(build_opportunity_radar(news)):
            assert theme.description
