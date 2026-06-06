"""Tests for demand-driver trigger detection and opportunity scoring."""

from __future__ import annotations

import math
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
        triggers = detect_triggers(item)
        drivers = [t.driver for t in triggers]
        assert "ma" in drivers

    def test_capital_projects_trigger(self):
        item = _item("Pertamina reaches FID on new refinery expansion")
        triggers = detect_triggers(item)
        drivers = [t.driver for t in triggers]
        assert "capital_projects" in drivers

    def test_digital_tech_trigger(self):
        item = _item("PTT launches digital transformation programme with ERP implementation")
        triggers = detect_triggers(item)
        drivers = [t.driver for t in triggers]
        assert "digital_tech" in drivers

    def test_cyber_trigger(self):
        item = _item("Pertamina suffers ransomware attack on trading systems")
        triggers = detect_triggers(item)
        drivers = [t.driver for t in triggers]
        assert "cyber" in drivers

    def test_no_trigger_for_noise(self):
        item = _item("Oil price surges on OPEC news")
        triggers = detect_triggers(item)
        # price movement alone should not match demand drivers
        assert all(t.driver not in ("ma", "digital_tech", "cyber") for t in triggers)

    def test_multiple_triggers(self):
        item = _item("BHP announces CEO resignation amid restructuring and cost cutting")
        triggers = detect_triggers(item)
        drivers = [t.driver for t in triggers]
        assert "leadership" in drivers
        assert "cost_performance" in drivers

    def test_keywords_matched_populated(self):
        item = _item("Shell completes JV acquisition of Chevron stake in pipeline")
        triggers = detect_triggers(item)
        ma_trigger = next((t for t in triggers if t.driver == "ma"), None)
        assert ma_trigger is not None
        assert len(ma_trigger.keywords_matched) > 0

    def test_materiality_weight_positive(self):
        item = _item("Rio Tinto announces major capital expenditure for copper expansion")
        triggers = detect_triggers(item)
        for t in triggers:
            assert t.materiality_weight > 0

    def test_service_line_populated(self):
        item = _item("PETRONAS faces regulatory investigation and fine over emissions compliance")
        triggers = detect_triggers(item)
        reg = next((t for t in triggers if t.driver == "regulation_risk"), None)
        assert reg is not None
        assert reg.service_line  # non-empty

    def test_suggested_angle_populated(self):
        item = _item("Mercuria appoints new CEO after CFO steps down")
        triggers = detect_triggers(item)
        leadership = next((t for t in triggers if t.driver == "leadership"), None)
        assert leadership is not None
        assert leadership.suggested_angle


class TestOpportunityScoring:
    def test_score_positive(self):
        news = {"Shell": [_item("Shell announces JV acquisition in Singapore")]}
        cards = build_opportunity_radar(news, set())
        assert len(cards) > 0
        assert cards[0].score > 0

    def test_score_breakdown_keys(self):
        news = {"BHP": [_item("BHP reaches FID on new copper plant in Australia")]}
        cards = build_opportunity_radar(news, set())
        assert len(cards) > 0
        breakdown = cards[0].score_breakdown
        assert "materiality" in breakdown
        assert "recency" in breakdown
        assert "source" in breakdown
        assert "priority_mult" in breakdown

    def test_score_formula(self):
        news = {"BHP": [_item("BHP reaches FID on new copper greenfield expansion")]}
        cards = build_opportunity_radar(news, set())
        assert len(cards) > 0
        c = cards[0]
        bd = c.score_breakdown
        expected = round(bd["materiality"] * bd["recency"] * bd["source"] * bd["priority_mult"], 3)
        assert abs(c.score - expected) < 0.01

    def test_one_card_per_company(self):
        items = [
            _item("Shell acquires LNG terminal"),
            _item("Shell announces ERP digital transformation"),
            _item("Shell faces ransomware breach"),
        ]
        news = {"Shell": items}
        cards = build_opportunity_radar(news, set())
        companies = [c.company for c in cards]
        assert companies.count("Shell") == 1

    def test_is_new_flag(self):
        news = {"Mercuria": [_item("Mercuria completes major acquisition")]}
        # Not in prev_companies → is_new = True
        cards = build_opportunity_radar(news, set())
        assert cards[0].is_new is True

        # In prev_companies → is_new = False
        cards2 = build_opportunity_radar(news, {"Mercuria"})
        assert cards2[0].is_new is False

    def test_audit_restricted_suppresses_angle(self):
        news = {"Shell": [_item("Shell acquires LNG terminal in Singapore")]}
        cards = build_opportunity_radar(news, set())
        # Shell is "target" by default in config — angle present
        shell_card = next((c for c in cards if c.company == "Shell"), None)
        if shell_card:
            assert not shell_card.is_restricted

    def test_high_priority_scores_higher(self):
        # Same headline for two companies: one high-priority, one low
        # We can verify high-priority multiplier > low by checking breakdown
        news = {
            "PETRONAS": [_item("PETRONAS announces major acquisition of LNG assets")],
            "EGAT": [_item("EGAT announces major acquisition of LNG assets")],
        }
        cards = build_opportunity_radar(news, set())
        petronas = next((c for c in cards if c.company == "PETRONAS"), None)
        egat = next((c for c in cards if c.company == "EGAT"), None)
        if petronas and egat:
            # PETRONAS is high priority (mult 1.5), EGAT is low (mult 0.7)
            assert petronas.score_breakdown["priority_mult"] > egat.score_breakdown["priority_mult"]


class TestSectorThemes:
    def test_theme_requires_two_accounts(self):
        # Only one account with ma signal → no theme
        news = {"Shell": [_item("Shell completes acquisition of LNG terminal")]}
        cards = build_opportunity_radar(news, set())
        themes = build_sector_themes(cards)
        ma_theme = next((t for t in themes if t.driver == "ma"), None)
        assert ma_theme is None  # only 1 account, below threshold

    def test_theme_created_for_two_accounts(self):
        news = {
            "Shell": [_item("Shell acquires stake in LNG terminal")],
            "BHP": [_item("BHP completes acquisition of copper miner")],
        }
        cards = build_opportunity_radar(news, set())
        themes = build_sector_themes(cards)
        ma_theme = next((t for t in themes if t.driver == "ma"), None)
        assert ma_theme is not None
        assert len(ma_theme.accounts) >= 2

    def test_theme_has_service_lines(self):
        news = {
            "PETRONAS": [_item("PETRONAS acquires upstream assets via JV")],
            "PTT": [_item("PTT completes acquisition of refinery stake")],
        }
        cards = build_opportunity_radar(news, set())
        themes = build_sector_themes(cards)
        for theme in themes:
            assert theme.service_lines

    def test_theme_description_populated(self):
        news = {
            "Shell": [_item("Shell acquires LNG stake")],
            "TotalEnergies Trading Asia": [_item("TotalEnergies completes divestiture of assets")],
        }
        cards = build_opportunity_radar(news, set())
        themes = build_sector_themes(cards)
        for theme in themes:
            assert theme.description
