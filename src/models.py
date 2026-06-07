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
    drivers: list[str] = Field(default_factory=list)   # market-driver tags
    score: float = 0.0
    title_norm: str = ""
    consulting_label: str = ""
    why_it_matters: str = ""
    age_h: float = 0.0                                  # hours since published
    snippet: str = ""


class PriceRecord(BaseModel):
    commodity_id: str
    display: str
    unit: str
    group: str
    price: Optional[float] = None
    prev_close: Optional[float] = None
    change_pct: Optional[float] = None
    change_pct_30d: Optional[float] = None   # 30-day price change %
    source: str = "yfinance"
    quality: str = "market"   # "market" | "indicative" | "web"
    as_of: Optional[datetime] = None


class MacroTickerRecord(BaseModel):
    name: str
    symbol: str
    unit: str = ""
    last: Optional[float] = None
    change_pct: Optional[float] = None
    as_of: Optional[datetime] = None


class GeopoliticalItem(BaseModel):
    title: str
    source: str
    url: str
    age_h: float = 0.0


class MacroSection(BaseModel):
    tickers: list[MacroTickerRecord] = Field(default_factory=list)
    geopolitical: list[GeopoliticalItem] = Field(default_factory=list)


class OutlookItem(BaseModel):
    commodity: str
    eia_forecast: str = ""
    wb_forecast: str = ""
    horizon: str = ""
    direction: str = "neutral"   # bullish | bearish | neutral
    narrative: str = ""
    headlines: list[dict] = Field(default_factory=list)


class FundamentalsItem(BaseModel):
    commodity: str
    inventory_level: Optional[float] = None
    inventory_change: Optional[float] = None
    inventory_direction: str = ""   # up | down | flat
    production: Optional[float] = None
    supply_signals: list[str] = Field(default_factory=list)
    demand_signals: list[str] = Field(default_factory=list)
    balance_read: str = ""
    source: str = ""
    as_of: Optional[datetime] = None


class ThemeItem(BaseModel):
    name: str
    commodities: list[str] = Field(default_factory=list)
    companies: list[str] = Field(default_factory=list)
    headlines: list[dict] = Field(default_factory=list)
    narrative: str = ""
    driver_tags: list[str] = Field(default_factory=list)


class RiskItem(BaseModel):
    direction: str   # upside | downside
    text: str


class EventItem(BaseModel):
    date: str        # ISO date string YYYY-MM-DD
    title: str
    type: str = ""
    related: list[str] = Field(default_factory=list)
    upcoming: bool = False   # within next 7 days


class RegulatoryItem(BaseModel):
    title: str
    jurisdiction: str
    commodities: list[str] = Field(default_factory=list)
    companies: list[str] = Field(default_factory=list)
    effective_date: str = ""
    source: str = ""
    url: str = ""


class HeatmapData(BaseModel):
    commodities: list[str] = Field(default_factory=list)
    returns: dict[str, list] = Field(default_factory=dict)   # "1d": [...], "1w": [...], "1m": [...]


class CorrelationData(BaseModel):
    commodities: list[str] = Field(default_factory=list)
    matrix: list[list] = Field(default_factory=list)


class CompanyDigest(BaseModel):
    name: str
    sector: str
    country: str
    ticker: str = ""
    items: list[NewsItem] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    highlight: str = ""
    summary: str = ""   # Claude-written 1-2 sentence synthesis
    last_news_date: Optional[str] = None   # ISO date of most recent news item
    one_liner: str = ""                    # standing context from config


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
    narrative: str = ""   # kept for backward compat
    bullets: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)


# ── Demand-driver opportunity intelligence ────────────────────────────────────

class TriggerMatch(BaseModel):
    """A demand-driver keyword match detected in a news item."""
    driver: str
    service_line: str
    suggested_angle: str
    keywords_matched: list[str] = Field(default_factory=list)
    materiality_weight: float = 1.0


class OpportunityCard(BaseModel):
    """Ranked consulting opportunity for the Opportunity Radar."""
    company: str
    sector: str
    country: str
    headline: str
    url: str
    driver: str
    service_line: str
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
    theme: str
    driver: str
    service_lines: list[str] = Field(default_factory=list)
    accounts: list[str] = Field(default_factory=list)
    description: str = ""


class WeeklyBrief(BaseModel):
    """Partner-level executive summary."""
    period: str = ""
    top_opportunities: list[str] = Field(default_factory=list)
    hottest_accounts: list[str] = Field(default_factory=list)
    key_themes: list[str] = Field(default_factory=list)
    what_changed: list[str] = Field(default_factory=list)
    opportunity_count: int = 0
    active_account_count: int = 0


class DailyDigest(BaseModel):
    generated_at: datetime
    # Legacy fields (kept for backward compat)
    executive_brief: Optional[ExecutiveBrief] = None
    what_to_watch: list[WatchBullet] = Field(default_factory=list)
    opportunities: list[OpportunityItem] = Field(default_factory=list)
    sector_summaries: list[SectorSummary] = Field(default_factory=list)
    opportunity_radar: list[OpportunityCard] = Field(default_factory=list)
    account_briefs: list[AccountBrief] = Field(default_factory=list)
    sector_themes: list[SectorTheme] = Field(default_factory=list)
    weekly_brief: Optional[WeeklyBrief] = None
    # Core data
    exec_summary: list[str] = Field(default_factory=list)
    top_headlines: list[NewsItem] = Field(default_factory=list)
    companies: list[CompanyDigest] = Field(default_factory=list)
    commodities: list[CommodityDigest] = Field(default_factory=list)
    prices: list[PriceRecord] = Field(default_factory=list)
    # New MI sections
    macro: Optional[MacroSection] = None
    outlook: list[OutlookItem] = Field(default_factory=list)
    fundamentals: list[FundamentalsItem] = Field(default_factory=list)
    themes: list[ThemeItem] = Field(default_factory=list)
    risks: list[RiskItem] = Field(default_factory=list)
    events: list[EventItem] = Field(default_factory=list)
    regulatory: list[RegulatoryItem] = Field(default_factory=list)
    heatmap: Optional[HeatmapData] = None
    correlations: Optional[CorrelationData] = None
    cross_portfolio_alerts: list[dict] = Field(default_factory=list)
    conversation_starter: str = ""
    regulatory_spotlight: list[RegulatoryItem] = Field(default_factory=list)
