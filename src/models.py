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
    highlight: str = ""        # Claude-generated strategic insight for top companies


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
    engagement_context: str = ""   # Claude-generated scope/team/duration hint


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
