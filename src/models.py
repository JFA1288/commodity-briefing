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
    company: Optional[str] = None      # matched company name
    commodity: Optional[str] = None    # matched commodity id
    flags: list[str] = Field(default_factory=list)
    score: float = 0.0
    # normalised title used for dedup
    title_norm: str = ""


class PriceRecord(BaseModel):
    commodity_id: str
    display: str
    unit: str
    group: str
    price: Optional[float] = None
    prev_close: Optional[float] = None
    change_pct: Optional[float] = None
    source: str = "yfinance"          # "yfinance" | "web" | "unavailable"
    as_of: Optional[datetime] = None


class CompanyDigest(BaseModel):
    name: str
    sector: str
    country: str
    items: list[NewsItem] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)   # union of item flags


class CommodityDigest(BaseModel):
    commodity_id: str
    display: str
    price: Optional[PriceRecord] = None
    items: list[NewsItem] = Field(default_factory=list)


class WatchBullet(BaseModel):
    category: str    # "price_mover" | "event" | "data_gap"
    text: str


class DailyDigest(BaseModel):
    generated_at: datetime
    top_headlines: list[NewsItem] = Field(default_factory=list)
    companies: list[CompanyDigest] = Field(default_factory=list)
    commodities: list[CommodityDigest] = Field(default_factory=list)
    what_to_watch: list[WatchBullet] = Field(default_factory=list)
    prices: list[PriceRecord] = Field(default_factory=list)
