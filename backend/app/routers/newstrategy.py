"""新策略（VWAP 趋势 + 放量 + 二阶导数反转）的回测 API。

逻辑在 app/user_stocks/_new_strategy_probe.py 里，这里只是薄薄一层 HTTP 包装。
"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.ohlc import OhlcError
from app.user_stocks._new_strategy_probe import (
    ALLOW_SHORT,
    BACKTEST_TRADING_DAYS,
    CAPITAL_PER_TRADE,
    CLOSE_NO_TRADE_MINUTES,
    COMPOUND,
    CONTEXT_DAYS,
    DAILY_ATR_N,
    LOOKBACK,
    MIN_SLOT_SAMPLES,
    SHAPE_CONFIDENCE_THRESHOLD,
    SHAPE_MIN_TRAIN_DAYS,
    SMA_LONG_PERIOD,
    SMA_SHORT_PERIOD,
    STOP_LOSS_ATR_MULT,
    TRADE_EVERY_SIGNAL,
    UPTREND_MARKET_SYMBOL,
    USE_SMA_PRECONDITION,
    VOLUME_DIST_DAYS,
    VOLUME_HIGH_PCT,
    VOLUME_LOW_PCT,
    VOLUME_MAX_WINDOW_DAYS,
    VWAP_TREND_LOOKBACK,
    run_backtest,
)

router = APIRouter()


class NewStrategyBtBody(BaseModel):
    symbol: str
    range_start: str | None = None  # YYYY-MM-DD，留空则用 backtest_trading_days
    range_end: str | None = None
    lookback: int = LOOKBACK
    context_days: int = CONTEXT_DAYS
    vwap_trend_lookback: int = VWAP_TREND_LOOKBACK
    volume_dist_days: int = VOLUME_DIST_DAYS
    volume_high_pct: float = VOLUME_HIGH_PCT
    volume_low_pct: float = VOLUME_LOW_PCT
    min_slot_samples: int = MIN_SLOT_SAMPLES
    volume_max_window_days: int = VOLUME_MAX_WINDOW_DAYS
    sma_short_period: int = SMA_SHORT_PERIOD
    sma_long_period: int = SMA_LONG_PERIOD
    use_sma_precondition: bool = USE_SMA_PRECONDITION
    allow_short: bool = ALLOW_SHORT
    short_symbol: str | None = None  # 主标的不方便融券时，填一个 ETF 代码改用它做空
    daily_atr_n: int = DAILY_ATR_N
    stop_loss_atr_mult: float = STOP_LOSS_ATR_MULT
    backtest_trading_days: int = BACKTEST_TRADING_DAYS
    capital_per_trade: float = CAPITAL_PER_TRADE
    compound: bool = COMPOUND
    trade_every_signal: bool = TRADE_EVERY_SIGNAL
    use_shape_filter: bool = False
    shape_confidence_threshold: float = SHAPE_CONFIDENCE_THRESHOLD
    shape_min_train_days: int = SHAPE_MIN_TRAIN_DAYS
    close_no_trade_minutes: int = CLOSE_NO_TRADE_MINUTES
    market_symbol: str | None = UPTREND_MARKET_SYMBOL


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"日期格式不对（要 YYYY-MM-DD）：{s}") from exc


@router.post("/backtest")
def backtest(payload: NewStrategyBtBody):
    range_start = _parse_date(payload.range_start)
    range_end = _parse_date(payload.range_end)
    try:
        return run_backtest(
            payload.symbol,
            range_start,
            range_end,
            lookback=payload.lookback,
            context_days=payload.context_days,
            vwap_trend_lookback=payload.vwap_trend_lookback,
            volume_dist_days=payload.volume_dist_days,
            volume_high_pct=payload.volume_high_pct,
            volume_low_pct=payload.volume_low_pct,
            min_slot_samples=payload.min_slot_samples,
            volume_max_window_days=payload.volume_max_window_days,
            sma_short_period=payload.sma_short_period,
            sma_long_period=payload.sma_long_period,
            use_sma_precondition=payload.use_sma_precondition,
            allow_short=payload.allow_short,
            short_symbol=payload.short_symbol,
            daily_atr_n=payload.daily_atr_n,
            stop_loss_atr_mult=payload.stop_loss_atr_mult,
            backtest_trading_days=payload.backtest_trading_days,
            capital_per_trade=payload.capital_per_trade,
            compound=payload.compound,
            trade_every_signal=payload.trade_every_signal,
            use_shape_filter=payload.use_shape_filter,
            shape_confidence_threshold=payload.shape_confidence_threshold,
            shape_min_train_days=payload.shape_min_train_days,
            close_no_trade_minutes=payload.close_no_trade_minutes,
            market_symbol=payload.market_symbol,
        )
    except OhlcError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
