"""股票回归线分析。

用 Theil–Sen 趋势、MAD 稳健残差尺度、回归通道、多周期对比和历史相似状态，
描述当前价格相对所选区间价格趋势的位置。它不是企业估值或未来价格预测模型。

⚠️ 普通最小二乘（OLS）+ 标准差的组合有个致命短板：暴涨暴跌这种极端单日
波动，会通过"杠杆效应"把整条趋势线的斜率带偏（越靠近样本边缘的点，在最小
二乘里权重天生越大），同时标准差里的平方项会让这一天的巨大残差把方差撑大，
反而让当天的 z-score"看起来没那么极端"——工具最该报警的时候，恰恰最容易
被这个副作用钝化。

所以这里换成一套对异常值稳健的方法：
1. Theil-Sen 估计量算斜率/截距——用所有两两点连线斜率的中位数代表整体
   趋势，中位数对离群点不敏感，不会被一两天的极端波动带偏（最多能容忍
   接近一半的样本点是离群值，斜率依然大致准确）。
2. MAD（绝对中位差，乘 1.4826 换算成跟标准差同量纲）代替标准差算"正常
   波动有多大"，同样不会被极端残差撑大。
3. 单日涨跌幅明显超出近期稳健波动的交易日标记为事件日。默认仍保留在拟合
   样本中，用户可选择排除；排除当天也不能消除事件后价格水平变化的影响。
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

from app.services import sec_edgar
from app.services.ohlc import OhlcError, fetch_closes_covering

EVENT_MOVE_MULT = 3.0  # 单日涨跌幅超过"稳健波动率"的这么多倍，标记成事件日


def _sma(values: list[float], window: int) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(values)):
        if i + 1 < window:
            out.append(None)
        else:
            out.append(sum(values[i + 1 - window : i + 1]) / window)
    return out


def _median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    if n == 0:
        return 0.0
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _mad_sigma(values: list[float]) -> float:
    """MAD 换算成跟标准差可比的稳健波动估计（乘 1.4826 是让它在正态分布假设
    下跟标准差同一量纲，统计学里的标准做法）。中位数本身对离群点免疫，所以
    这个值不会像标准差那样被单日暴涨暴跌直接撑大。"""
    if not values:
        return 0.0
    med = _median(values)
    return _median([abs(v - med) for v in values]) * 1.4826


def _flag_event_days(closes: list[float]) -> list[bool]:
    """单日涨跌幅超过 EVENT_MOVE_MULT 倍稳健波动率的交易日标记为「事件日」
    （index 对齐 closes，第 0 天永远是 False，因为没有前一天可比）。"""
    n = len(closes)
    flags = [False] * n
    if n < 3:
        return flags
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, n)]
    sigma = _mad_sigma(rets)
    if sigma <= 0:
        return flags
    for i in range(1, n):
        if abs(rets[i - 1]) > EVENT_MOVE_MULT * sigma:
            flags[i] = True
    return flags


def _theil_sen(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """稳健回归：斜率取所有两两点连线斜率的中位数，截距取 (y_i - slope*x_i)
    的中位数。比普通最小二乘更抗异常值，不会被样本里一两天的极端波动带偏。"""
    n = len(xs)
    slopes = []
    for i in range(n):
        for j in range(i + 1, n):
            dx = xs[j] - xs[i]
            if dx:
                slopes.append((ys[j] - ys[i]) / dx)
    slope = _median(slopes) if slopes else 0.0
    intercept = _median([ys[i] - slope * xs[i] for i in range(n)])
    return slope, intercept


def _fit(values: list[float], *, exclude_events: bool = False) -> dict:
    flags = _flag_event_days(values)
    idx = [i for i in range(len(values)) if not (exclude_events and flags[i])]
    if len(idx) < 3:
        idx = list(range(len(values)))
    slope, intercept = _theil_sen([float(i) for i in idx], [values[i] for i in idx])
    fitted = [intercept + slope * i for i in range(len(values))]
    sigma = _mad_sigma([values[i] - fitted[i] for i in idx])
    residuals = [values[i] - fitted[i] for i in range(len(values))]
    deviations = [value / sigma if sigma else 0.0 for value in residuals]
    return {"slope": slope, "intercept": intercept, "fitted": fitted, "sigma": sigma,
            "residuals": residuals, "deviations": deviations, "event_flags": flags}


def _aggregate_weekly(rows: list[tuple[date, float]]) -> list[tuple[date, float]]:
    weeks = {}
    for day, close in rows:
        weeks[day.isocalendar()[:2]] = (day, close)
    return [weeks[key] for key in sorted(weeks)]


def _deviation_duration(deviations: list[float]) -> int:
    if not deviations or deviations[-1] == 0:
        return 0
    sign = 1 if deviations[-1] > 0 else -1
    count = 0
    for value in reversed(deviations):
        if value == 0 or (1 if value > 0 else -1) != sign:
            break
        count += 1
    return count


def _period_comparison(values: list[float], periods: list[int], *, exclude_events: bool, log_price: bool) -> list[dict]:
    out = []
    for period in periods:
        if len(values) < period:
            out.append({"period": period, "available": False})
            continue
        sample = values[-period:]
        fit = _fit(sample, exclude_events=exclude_events)
        fitted_now = fit["fitted"][-1]
        change20 = ((math.exp(fit["slope"] * 20) - 1) * 100 if log_price
                    else (fit["slope"] * 20 / fitted_now * 100 if fitted_now else None))
        out.append({
            "period": period, "available": True,
            "direction": "上升" if fit["slope"] > 0 else "下降" if fit["slope"] < 0 else "横盘",
            "change_per_20": change20,
            "deviation_pct": ((sample[-1] / fitted_now - 1) * 100 if not log_price and fitted_now else
                              (math.exp(sample[-1] - fitted_now) - 1) * 100 if log_price else None),
            "robust_deviation": fit["deviations"][-1],
            "deviation_duration": _deviation_duration(fit["deviations"]),
        })
    return out


def _rolling_slope(values: list[float], window: int, *, exclude_events: bool, log_price: bool) -> list[float | None]:
    output = [None] * len(values)
    for end in range(window - 1, len(values)):
        fit = _fit(values[end - window + 1:end + 1], exclude_events=exclude_events)
        fitted_now = fit["fitted"][-1]
        output[end] = ((math.exp(fit["slope"] * 20) - 1) * 100 if log_price
                       else (fit["slope"] * 20 / fitted_now * 100 if fitted_now else None))
    return output


def _similar_history(values: list[float], current_z: float, current_slope: float, *, exclude_events: bool, log_price: bool) -> dict:
    window = min(120, max(40, len(values) // 3))
    horizon = 20
    samples = []
    for end in range(window - 1, len(values) - horizon, 4):
        sample = values[end - window + 1:end + 1]
        fit = _fit(sample, exclude_events=exclude_events)
        z = fit["deviations"][-1]
        if (fit["slope"] >= 0) != (current_slope >= 0) or abs(z - current_z) > 0.4:
            continue
        start_price = math.exp(values[end]) if log_price else values[end]
        future_price = math.exp(values[end + horizon]) if log_price else values[end + horizon]
        samples.append((future_price / start_price - 1) * 100)
    return {
        "count": len(samples), "horizon": horizon,
        "average_return": sum(samples) / len(samples) if samples else None,
        "positive_rate": sum(1 for value in samples if value > 0) / len(samples) * 100 if samples else None,
        "worst_return": min(samples) if samples else None,
        "walk_forward": True,
    }


def _annual_series(symbol: str, row_name: str) -> dict | None:
    try:
        cik = sec_edgar.cik_for(symbol)
        financials = sec_edgar.build_financials(cik)
        annual = financials["annual"]
        by_name = {r["name"]: r for r in annual["income"]["rows"]}
        row = by_name.get(row_name)
        if not row:
            return None
        periods = annual["income"]["periods"]
        # build_financials() 按时间倒序排（最近的在前），图表要从左到右由旧到新。
        return {"periods": list(reversed(periods)), "values": list(reversed(row["values"]))}
    except Exception:
        return None


def analyze_regression(
    symbol: str,
    *,
    ma1: int = 7,
    ma2: int = 8,
    start: date | None = None,
    end: date | None = None,
    timeframe: str = "daily",
    price_mode: str = "price",
    exclude_events: bool = False,
) -> dict:
    symbol = (symbol or "").strip().upper()
    if not symbol:
        raise OhlcError("请输入股票代码")
    ma1 = max(2, min(int(ma1 or 7), 200))
    ma2 = max(2, min(int(ma2 or 8), 200))

    end = end or date.today()
    start = start or (end - timedelta(days=60))
    if start > end:
        raise OhlcError("开始日期不能晚于结束日期")

    timeframe = timeframe if timeframe in {"daily", "weekly"} else "daily"
    price_mode = price_mode if price_mode in {"price", "log"} else "price"
    # 均线要在展示区间之前就有足够的历史数据打底，不然窗口前几天的均线全是空的；
    # 交易日大约是日历天数的 5/7，多拉一截铺垫，避免刚好卡在边界不够用。
    pad_days = int(max(ma1, ma2) * 1.6) + 10
    history_days = 2200 if timeframe == "weekly" else 700
    fetch_start = min(start - timedelta(days=pad_days), end - timedelta(days=history_days))

    raw = fetch_closes_covering(symbol, "1d", start=fetch_start)
    bars = raw.get("ohlc_bars") or []
    if not bars:
        raise OhlcError(f"{symbol} 拉不到日线数据")

    seen: set = set()
    rows: list[tuple[date, float]] = []
    for b in bars:
        d = datetime.fromtimestamp(int(b["ts"]), tz=timezone.utc).date()
        if d in seen:
            continue
        seen.add(d)
        rows.append((d, float(b["close"])))
    rows.sort(key=lambda r: r[0])
    if not rows:
        raise OhlcError(f"{symbol} 没有可用的日线数据")

    rows = [(day, close) for day, close in rows if day <= end]
    if timeframe == "weekly":
        rows = _aggregate_weekly(rows)
    all_dates = [r[0] for r in rows]
    all_closes = [r[1] for r in rows]
    ma1_full = _sma(all_closes, ma1)
    ma2_full = _sma(all_closes, ma2)

    idxs = [i for i, d in enumerate(all_dates) if start <= d <= end]
    if not idxs:
        raise OhlcError(f"{symbol} 在 {start}~{end} 这段区间没有数据，试试放宽日期范围")

    dates = [all_dates[i] for i in idxs]
    closes = [all_closes[i] for i in idxs]
    ma1_series = [ma1_full[i] for i in idxs]
    ma2_series = [ma2_full[i] for i in idxs]

    n = len(closes)
    model_values = [math.log(value) for value in closes] if price_mode == "log" else closes
    fit = _fit(model_values, exclude_events=exclude_events)
    fitted_model = fit["fitted"]
    fitted = [math.exp(value) for value in fitted_model] if price_mode == "log" else fitted_model
    channel1_low_model = [value - fit["sigma"] for value in fitted_model]
    channel1_high_model = [value + fit["sigma"] for value in fitted_model]
    channel2_low_model = [value - 2 * fit["sigma"] for value in fitted_model]
    channel2_high_model = [value + 2 * fit["sigma"] for value in fitted_model]
    convert = (lambda seq: [math.exp(value) for value in seq]) if price_mode == "log" else (lambda seq: seq)

    current_price = closes[-1]
    predicted_price = fitted[-1]
    z_score = fit["deviations"][-1]
    latest_is_event = bool(fit["event_flags"][-1]) if fit["event_flags"] else False
    event_dates = [dates[i].isoformat() for i in range(n) if fit["event_flags"][i]]
    full_values = [math.log(value) for value in all_closes] if price_mode == "log" else all_closes
    periods = [26, 52, 104] if timeframe == "weekly" else [60, 120, 250]
    comparisons = _period_comparison(full_values, periods, exclude_events=exclude_events, log_price=price_mode == "log")
    rolling_window = 26 if timeframe == "weekly" else 60
    effective_window = min(rolling_window, max(10, len(model_values) // 2), len(model_values))
    rolling = _rolling_slope(model_values, effective_window,
                             exclude_events=exclude_events, log_price=price_mode == "log")
    similar = _similar_history(full_values, z_score, fit["slope"], exclude_events=exclude_events,
                               log_price=price_mode == "log")

    return {
        "symbol": symbol,
        "ma1_window": ma1,
        "ma2_window": ma2,
        "dates": [d.isoformat() for d in dates],
        "closes": closes,
        "ma1": ma1_series,
        "ma2": ma2_series,
        "regression": fitted,
        "channel_1_low": convert(channel1_low_model), "channel_1_high": convert(channel1_high_model),
        "channel_2_low": convert(channel2_low_model), "channel_2_high": convert(channel2_high_model),
        "deviation_history": fit["deviations"], "rolling_slope": rolling,
        "current_price": current_price,
        "predicted_price": predicted_price,
        "z_score": z_score,
        "robust_scale": fit["sigma"], "mad_scaled": True,
        "timeframe": timeframe, "price_mode": price_mode, "exclude_events": exclude_events,
        "period_comparison": comparisons, "similar_history": similar,
        "event_dates": event_dates,
        "latest_is_event": latest_is_event,
        "sample_start": dates[0].isoformat(),
        "sample_end": dates[-1].isoformat(),
        "revenue": _annual_series(symbol, "Total Revenue"),
        "gross_profit": _annual_series(symbol, "Gross Profit"),
    }
