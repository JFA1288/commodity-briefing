"""Pydantic models shared across modules."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class NewsItem(BaseModel):
    title: str
    url: str
    source: str
    published: Optional[datetime] = None
    summary: str = ""
    company: Optional[str] = None
    commodity: Optional[str] = None
    flags: list[str] = Field(default_factory=list)
    score: float = 0.0
    title_norm: str = ""
    consulting_label: str = ""
    why_it_matters: str = ""


class PriceRecord(BaseModel):
    commodity_id: str
    display: str
    unit: str
    group: str
    price: Optional[float] = None
    prev_close: Optional[float] = None
    change_pct: Optional[float] = None
    source: str = "yfinance"
    as_of: Optional[datetime] = None


class CompanyDigest(BaseModel):
    name: str
    sector: str
    country: str
    items: list[NewsItem] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    highlight: str = ""


class CommodityDigest(BaseModel):
    commodity_id: str
    display: str
    price: Optional[PriceRecord] = None
    items: list[NewsItem] = Field(default_factory=list)


class WatchBullet(BaseModel):
    category: str
    text: str


class OpportunityItem(BaseModel):
    company: str
    sector: str
    signal: str
    headline: str
    url: str
    why: str
    published: Optional[datetime] = None
    score: float = 0.0
    engagement_context: str = ""


class SectorSummary(BaseModel):
    sector: str
    sector_label: str
    top_signal: str = ""
    signal_counts: dict[str, int] = Field(default_factory=dict)
    active_companies: list[str] = Field(default_factory=list)
    quiet_companies: list[str] = Field(default_factory=list)
    pulse: str = ""
    narrative: str = ""


class ExecutiveBrief(BaseModel):
    narrative: str
    themes: list[str] = Field(default_factory=list)


# ── New: demand-driver opportunity intelligence ────────────────────────────────

class TriggerMatch(BaseModel):
    """A demand-driver keyword match detected in a news item."""
    driver: str                  # demand_driver key from config (e.g. "ma", "digital_tech")
    service_line: str            # display label (e.g. "Strategy & Transactions")
    suggested_angle: str         # templated next-step text
    keywords_matched: list[str] = Field(default_factory=list)
    materiality_weight: float = 1.0


class OpportunityCard(BaseModel):
    """Ranked consulting opportunity for the Opportunity Radar."""
    company: str
    sector: str
    country: str
    headline: str
    url: str
    driver: str                  # demand_driver key
    service_line: str            # display label
    suggested_angle: str
    score: float = 0.0
    published: Optional[datetime] = None


class AccountBrief(BaseModel):
    """Enriched account card for Account Intelligence section."""
    name: str
    sector: str
    country: str
    ticker: str = ""
    one_liner: str = ""
    active_triggers: list[TriggerMatch] = Field(default_factory=list)
    top_headlines: list[str] = Field(default_factory=list)
    top_urls: list[str] = Field(default_factory=list)
    consulting_angles: list[str] = Field(default_factory=list)
    talking_points: list[str] = Field(default_factory=list)
    has_news: bool = False


class SectorTheme(BaseModel):
    """Cross-account thematic roll-up derived from clustered triggers."""
    theme: str                   # human-readable theme name
    driver: str                  # dominant demand_driver key
    service_lines: list[str] = Field(default_factory=list)
    accounts: list[str] = Field(default_factory=list)
    description: str = ""


class WeeklyBrief(BaseModel):
    """Partner-level executive summary for the top of the dashboard."""
    period: str = ""             # e.g. "Week of 2026-06-06"
    top_opportunities: list[str] = Field(default_factory=list)   # narrative bullets
    hottest_accounts: list[str] = Field(default_factory=list)
    key_themes: list[str] = Field(default_factory=list)
    what_changed: list[str] = Field(default_factory=list)
    opportunity_count: int = 0
    active_account_count: int = 0


class DailyDigest(BaseModel):
    generated_at: datetime
    executive_brief: Optional[ExecutiveBrief] = None
    top_headlines: list[NewsItem] = Field(default_factory=list)
    companies: list[CompanyDigest] = Field(default_factory=list)
    commodities: list[CommodityDigest] = Field(default_factory=list)
    what_to_watch: list[WatchBullet] = Field(default_factory=list)
    prices: list[PriceRecord] = Field(default_factory=list)
    opportunities: list[OpportunityItem] = Field(default_factory=list)
    sector_summaries: list[SectorSummary] = Field(default_factory=list)
    # New fields — absent keys degrade gracefully in the dashboard
    opportunity_radar: list[OpportunityCard] = Field(default_factory=list)
    account_briefs: list[AccountBrief] = Field(default_factory=list)
    sector_themes: list[SectorTheme] = Field(default_factory=list)
    weekly_brief: Optional[WeeklyBrief] = None
