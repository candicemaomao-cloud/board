from __future__ import annotations

from datetime import date as Date
from datetime import datetime as DateTime

from pydantic import BaseModel, ConfigDict, Field

from app.models import Side


class SnapshotCreate(BaseModel):
    date: Date
    total_equity: float
    daily_pnl: float | None = None
    daily_return_pct: float | None = None
    cash_balance: float = 0
    notes: str | None = None


class SnapshotUpdate(BaseModel):
    total_equity: float | None = None
    daily_pnl: float | None = None
    daily_return_pct: float | None = None
    cash_balance: float | None = None
    notes: str | None = None


class SnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    date: Date
    total_equity: float
    daily_pnl: float
    daily_return_pct: float
    cash_balance: float
    notes: str | None = None
    created_at: DateTime


class TradeCreate(BaseModel):
    date: Date
    symbol: str = Field(min_length=1, max_length=16)
    side: Side
    pnl_amount: float
    pnl_pct: float | None = None
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None


class TradeUpdate(BaseModel):
    date: Date | None = None
    symbol: str | None = None
    side: Side | None = None
    pnl_amount: float | None = None
    pnl_pct: float | None = None
    tags: list[str] | None = None
    notes: str | None = None


class TradeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    date: Date
    symbol: str
    side: Side
    pnl_amount: float
    pnl_pct: float | None = None
    tags: list[str]
    notes: str | None = None
    source: str = "manual"
    created_at: DateTime


class TagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class PositionCreate(BaseModel):
    platform: str
    status: str = "未开始"
    name: str = Field(min_length=1, max_length=64)
    shares: float
    open_price: float
    fee: float = 0
    opened_on: Date | None = None
    expected_days: int = Field(21, ge=1, le=252)
    notes: str | None = None
    open_reason: str | None = None
    strategy_id: int | None = None


class PositionUpdate(BaseModel):
    platform: str | None = None
    status: str | None = None
    name: str | None = None
    shares: float | None = None
    open_price: float | None = None
    fee: float | None = None
    expected_days: int | None = Field(None, ge=1, le=252)
    notes: str | None = None
    open_reason: str | None = None
    strategy_id: int | None = None


class PositionClose(BaseModel):
    close_amount: float
    fee: float | None = None
    closed_on: Date | None = None
    notes: str | None = None
    close_reason: str | None = None


class PositionClosedRecordUpdate(BaseModel):
    """已结束的持仓只能改开仓记录和关仓记录这两块，其他字段（平台/状态/策略等）
    不允许通过这个接口改。"""

    shares: float
    open_price: float
    opened_on: Date
    open_reason: str | None = None
    close_amount: float
    closed_on: Date | None = None
    close_reason: str | None = None
    fee: float | None = None


class PositionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    platform: str
    status: str
    name: str
    quote_symbol: str = ""
    quote_source: str = "tradingview"
    shares: float
    open_price: float
    market_value: float
    open_amount: float
    fee: float = 0
    close_amount: float | None = None
    opened_on: Date
    closed_on: Date | None = None
    expected_days: int = 21
    notes: str | None = None
    open_reason: str | None = None
    close_reason: str | None = None
    strategy_id: int | None = None
    strategy_name: str | None = None
    strategy_side: str | None = None
    trade_id: int | None = None
    holding_days: int
    pnl_amount: float
    pnl_pct: float | None = None
    created_at: DateTime


class DailyWatchIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    prev_close: float | None = None
    prev_open: float | None = None
    prev_low: float | None = None
    prev_high: float | None = None
    current_price: float | None = None
    gamma_low: float | None = None
    gamma_high: float | None = None
    regression_line: float | None = None
    max_pain: float | None = None
    short_entry_price: float | None = None
    long_entry_price: float | None = None
    is_potential: bool = False
    market_direction: str = "横盘"
    sector: str = "其他"
    events: str | None = None


class DailyWatchOut(DailyWatchIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    updated_at: DateTime
