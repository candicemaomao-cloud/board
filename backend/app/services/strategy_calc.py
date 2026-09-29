from __future__ import annotations

from datetime import datetime, timezone

from app.services.indicator_code import IndicatorCodeError, is_code_formula, last_signal
from app.services.indicators import snapshot
from app.services.ohlc import OhlcError, fetch_closes
from app.services.strategy_spec import buy_suggestions, eval_formula, explain_formula, flags_from_snapshot

TIMEFRAMES = ("1d", "4h", "30m", "5m")


class CalcError(ValueError):
    pass


def _px(value):
    if value is None:
        return None
    return round(float(value), 4)


def pack_prices(indicators: dict, last) -> dict:
    ind = indicators or {}
    bb = ind.get("bollinger") or {}
    res = ind.get("resistance") or {}
    sup = ind.get("support") or {}
    return {
        "last": _px(last),
        "ema5": _px(ind.get("ema5")),
        "ema10": _px(ind.get("ema10")),
        "ema20": _px(ind.get("ema20")),
        "ema144": _px(ind.get("ema144")),
        "ema169": _px(ind.get("ema169")),
        "support": _px(sup.get("price")),
        "resistance": _px(res.get("price")),
        "break_level": _px(ind.get("break_level")),
        "hold_level": _px(ind.get("hold_level")),
        "recent_support": [_px(row.get("price")) for row in ind.get("recent_support") or [] if row.get("price") is not None],
        "recent_resistance": [_px(row.get("price")) for row in ind.get("recent_resistance") or [] if row.get("price") is not None],
        "bb_upper": _px(bb.get("upper")),
        "bb_middle": _px(bb.get("middle")),
        "bb_lower": _px(bb.get("lower")),
        "range_low": _px(ind.get("range_low")),
        "range_high": _px(ind.get("range_high")),
        "rsi": ind.get("rsi"),
        "macd": _px(ind.get("macd")),
        "macd_signal": _px(ind.get("macd_signal")),
        "adx": ind.get("adx"),
        "atr": _px(ind.get("atr")),
        "vwap": _px(ind.get("vwap")),
        "mfi": ind.get("mfi"),
        "stoch_k": ind.get("stoch_k"),
        "stoch_d": ind.get("stoch_d"),
        "kdj_j": ind.get("kdj_j"),
        "sma20": _px(ind.get("sma20")),
        "sma50": _px(ind.get("sma50")),
        "sma200": _px(ind.get("sma200")),
        "keltner_upper": _px(ind.get("keltner_upper")),
        "keltner_lower": _px(ind.get("keltner_lower")),
        "donchian5_high": _px(ind.get("donchian5_high")),
        "donchian5_low": _px(ind.get("donchian5_low")),
        "donchian_high": _px(ind.get("donchian_high")),
        "donchian_low": _px(ind.get("donchian_low")),
        "hi5": _px(ind.get("hi5")),
        "hi20": _px(ind.get("hi20")),
        "hi50": _px(ind.get("hi50")),
        "lo5": _px(ind.get("lo5")),
        "low5": _px(ind.get("low5")),
        "low10": _px(ind.get("low10")),
        "low20": _px(ind.get("low20")),
        "high5": _px(ind.get("high5")),
        "high10": _px(ind.get("high10")),
        "high20": _px(ind.get("high20")),
        "week_low": _px(ind.get("week_low")),
        "month_low": _px(ind.get("month_low")),
        "lo20": _px(ind.get("lo20")),
        "lo50": _px(ind.get("lo50")),
    }


def calculate(
    symbol: str,
    timeframe: str,
    formula: dict | None = None,
    library: dict | None = None,
    names: dict | None = None,
    trade_side: str | None = None,
) -> dict:
    tf = timeframe if timeframe in TIMEFRAMES else "1d"
    ohlc = fetch_closes(symbol, tf)
    extra = {}
    for need in ("4h", "1d"):
        if ohlc.get("timeframe") == need:
            extra[need] = ohlc
            continue
        try:
            extra[need] = fetch_closes(symbol, need)
        except OhlcError:
            extra[need] = None
    if ohlc.get("source") != "binance":
        for bench in ("SPY", "QQQ"):
            try:
                extra[bench.lower()] = fetch_closes(bench, tf)
            except OhlcError:
                extra[bench.lower()] = None
    indicators = snapshot(ohlc, extra)
    flags = flags_from_snapshot(indicators)
    snap = {**indicators, "last": ohlc.get("price")}
    bars = ohlc.get("ohlc_bars") or []
    asof = None
    if bars and bars[-1].get("ts"):
        asof = datetime.fromtimestamp(int(bars[-1]["ts"]), tz=timezone.utc).date().isoformat()

    if is_code_formula(formula):
        try:
            match = last_signal(str(formula.get("code") or ""), bars)
        except IndicatorCodeError as exc:
            raise CalcError(str(exc)) from exc
        explain = {
            "join": "and",
            "text": "代码指标 · True/False",
            "hit": match,
            "groups": [
                {
                    "join": "and",
                    "hit": match,
                    "clauses": [
                        {
                            "kind": "code",
                            "id": "compute",
                            "name": "compute(bars)",
                            "hit": match,
                            "not": False,
                            "note": "用户自写函数，返回 True/False",
                            "levels": [],
                        }
                    ],
                }
            ],
        }
        return {
            "symbol": ohlc["symbol"],
            "source": ohlc["source"],
            "timeframe": ohlc["timeframe"],
            "price": ohlc["price"],
            "bars": ohlc["bars"],
            "asof": asof,
            "prices": pack_prices(indicators, ohlc.get("price")),
            "match": match,
            "explain": explain,
            "buys": None,
            "flags": flags,
        }

    explain = explain_formula(formula, flags, library, names, snapshot=snap) if formula else None
    match = eval_formula(formula, flags, library) if formula else None
    return {
        "symbol": ohlc["symbol"],
        "source": ohlc["source"],
        "timeframe": ohlc["timeframe"],
        "price": ohlc["price"],
        "bars": ohlc["bars"],
        "asof": asof,
        "prices": pack_prices(indicators, ohlc.get("price")),
        "match": match,
        "explain": explain,
        "buys": buy_suggestions(
            explain, snap, match, (formula or {}).get("targets"), trade_side
        )
        if explain
        else None,
        "flags": flags,
    }


class StrategyCalcError(OhlcError):
    pass
