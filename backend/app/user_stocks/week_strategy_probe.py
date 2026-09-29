"""周线策略：对比 SMA / EMA / VWMA 三种均线作为"回踩支撑线"的效果。

思路：
1. 用日线数据聚合成周线 bar（日线历史比原生周线接口通常保留更长）。
2. 分别用 SMA20 / EMA20 / VWMA20（周线）当趋势/支撑主线，要求均线本身向上。
3. 通道 = 均线 ± CHANNEL_ATR_MULT × 周线 ATR，价格回踩触及通道
   （本周最低价 <= 通道上沿）、且收盘没跌破通道下沿，算"回踩不破"。
4. 成交量确认：回踩这一周的成交量要低于近 VOLUME_MA_WEEKS 周均量（缩量回踩）。
5. 止损：收盘跌破通道下沿。止盈：收盘跌破均线本身（简单版本）。
6. 三种均线用完全相同的通道/成交量/止盈止损规则跑一遍，最后对比胜率、
   总盈亏、交易笔数，方便直接判断哪种均线在这只票上更合适。

命令行用法：
    python3 backend/app/user_stocks/_weekly_channel_probe_compare.py MU
    python3 backend/app/user_stocks/_weekly_channel_probe_compare.py MU 2023-01-01 2026-08-20
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

backend = Path(__file__).resolve().parents[2]
if str(backend) not in sys.path:
    sys.path.insert(0, str(backend))

from app.services.ohlc import fetch_closes
from app.services.user_research import wash_bars
from app.user_stocks.tsla_golden import et_day

SYMBOL = "MU"

MA_PERIOD = 20  # 均线周期（周）
ATR_PERIOD = 14  # 周线 ATR 周期
CHANNEL_ATR_MULT = 1.0  # 通道宽度 = 此倍数 × 周线 ATR
MA_SLOPE_LOOKBACK = 4  # 判断均线是否向上，用当前值对比 N 周前
VOLUME_MA_WEEKS = 10  # 成交量均值窗口（周）
CAPITAL_PER_TRADE = 10000.0

MA_TYPES = ["SMA", "EMA", "VWMA"]


def build_weekly_bars(daily_bars: list[dict]) -> list[dict]:
    """把日线 bar 按 ISO 周聚合成周线 bar。

    Args:
        daily_bars: 已清洗的日线 bar 列表，按时间升序，需含 ts/open/high/low/close/volume。

    Returns:
        周线 bar 列表，按时间升序，每根带 "week_end" 字段（周内最后一个交易日）。
    """
    weekly: dict[tuple, list[dict]] = {}
    for bar in daily_bars:
        d = et_day(bar["ts"])
        iso_year, iso_week, _ = d.isocalendar()
        weekly.setdefault((iso_year, iso_week), []).append(bar)

    out = []
    for key in sorted(weekly.keys()):
        days_in_week = weekly[key]
        out.append({
            "open": days_in_week[0]["open"],
            "high": max(b["high"] for b in days_in_week),
            "low": min(b["low"] for b in days_in_week),
            "close": days_in_week[-1]["close"],
            "volume": sum(float(b.get("volume") or 0.0) for b in days_in_week),
            "week_end": et_day(days_in_week[-1]["ts"]),
        })
    return out


def true_range(bars: list[dict], i: int) -> float:
    """计算第 i 根 bar 的 True Range。"""
    bar = bars[i]
    if i < 1:
        return max(bar["high"] - bar["low"], 1e-9)
    prev_c = bars[i - 1]["close"]
    return max(bar["high"] - bar["low"], abs(bar["high"] - prev_c), abs(bar["low"] - prev_c))


def rolling_atr(bars: list[dict], period: int) -> list[float | None]:
    """计算滚动 ATR（简单平均），窗口不足 period 根返回 None。"""
    trs = [true_range(bars, i) for i in range(len(bars))]
    out: list[float | None] = []
    for i in range(len(bars)):
        out.append(None if i < period - 1 else sum(trs[i - period + 1 : i + 1]) / period)
    return out


def rolling_sma(values: list[float], period: int) -> list[float | None]:
    """简单移动平均：过去 period 根算术平均。"""
    out: list[float | None] = []
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= period:
            s -= values[i - period]
        out.append(s / period if i >= period - 1 else None)
    return out


def rolling_ema(values: list[float], period: int) -> list[float | None]:
    """指数移动平均：最近的值权重更高。

    前 period-1 根为 None；第 period-1 根用简单平均作初始值，
    之后按 EMA_t = v_t*k + EMA_{t-1}*(1-k) 递推，k = 2/(period+1)。
    """
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    ema = seed
    for i in range(period, len(values)):
        ema = values[i] * k + ema * (1 - k)
        out[i] = ema
    return out


def rolling_vwma(closes: list[float], volumes: list[float], period: int) -> list[float | None]:
    """成交量加权移动平均：过去 period 根按各自成交量加权平均收盘价。

    VWMA_t = sum(close_i * volume_i for i in window) / sum(volume_i for i in window)
    成交量全为 0 的窗口（理论上不该出现）会返回 None，避免除零。

    Args:
        closes: 收盘价序列。
        volumes: 对应的成交量序列，长度与 closes 相同。
        period: 窗口期数。

    Returns:
        与 closes 等长的列表，窗口不足 period 根时为 None。
    """
    out: list[float | None] = []
    for i in range(len(closes)):
        if i < period - 1:
            out.append(None)
            continue
        window_c = closes[i - period + 1 : i + 1]
        window_v = volumes[i - period + 1 : i + 1]
        vol_sum = sum(window_v)
        if vol_sum <= 0:
            out.append(None)
        else:
            out.append(sum(c * v for c, v in zip(window_c, window_v)) / vol_sum)
    return out


def rolling_volume_avg(volumes: list[float], weeks: int) -> list[float | None]:
    """计算"此前 N 周"（不含当前周）成交量均值。"""
    out: list[float | None] = []
    for i in range(len(volumes)):
        out.append(None if i < weeks else sum(volumes[i - weeks : i]) / weeks)
    return out


def run_one_ma_backtest(
    weekly: list[dict], ma_series: list[float | None], atr_series: list[float | None],
    vol_avg_series: list[float | None], min_idx: int, eff_start: date, eff_end: date,
) -> dict:
    """用给定的均线序列，跑一遍"回踩通道+缩量确认"策略，返回交易记录和汇总。

    Args:
        weekly: 周线 bar 列表。
        ma_series: 对应的均线序列（SMA/EMA/VWMA 任一）。
        atr_series: 周线 ATR 序列。
        vol_avg_series: 成交量均值序列。
        min_idx: 开始产生有效信号的最小下标（均线/ATR/成交量均值都已就绪）。
        eff_start: 回测区间起始日期。
        eff_end: 回测区间结束日期。

    Returns:
        {"trades": [...], "total_pnl": float, "win_rate": float, "n_realized": int}
    """
    position: dict | None = None
    trades: list[dict] = []

    for i, w in enumerate(weekly):
        if w["week_end"] < eff_start or w["week_end"] > eff_end or i < min_idx:
            continue

        ma = ma_series[i]
        atr = atr_series[i]
        vol_avg = vol_avg_series[i]
        if ma is None or atr is None:
            continue

        ma_ref = ma_series[i - MA_SLOPE_LOOKBACK]
        ma_rising = ma_ref is not None and ma > ma_ref

        lower = ma - CHANNEL_ATR_MULT * atr
        upper = ma + CHANNEL_ATR_MULT * atr
        touched_channel = w["low"] <= upper
        held_support = w["close"] >= lower
        volume_light = vol_avg is not None and w["volume"] < vol_avg

        exited = False
        if position is not None and w["close"] < lower:
            shares = CAPITAL_PER_TRADE / position["price"]
            pnl = shares * (w["close"] - position["price"])
            trades.append({"entry": position["week_end"], "exit": w["week_end"],
                            "entry_price": position["price"], "exit_price": w["close"],
                            "reason": "止损(跌破通道)", "pnl": pnl})
            position = None
            exited = True
        elif position is not None and w["close"] < ma:
            shares = CAPITAL_PER_TRADE / position["price"]
            pnl = shares * (w["close"] - position["price"])
            trades.append({"entry": position["week_end"], "exit": w["week_end"],
                            "entry_price": position["price"], "exit_price": w["close"],
                            "reason": "止盈(跌破均线)", "pnl": pnl})
            position = None
            exited = True

        if position is None and not exited:
            if ma_rising and touched_channel and held_support and volume_light:
                position = {"week_end": w["week_end"], "price": w["close"]}

    if position is not None:
        trades.append({"entry": position["week_end"], "exit": None,
                        "entry_price": position["price"], "exit_price": None,
                        "reason": "区间结束仍持仓（未实现）", "pnl": 0.0})

    realized = [t for t in trades if t["exit"] is not None]
    total_pnl = sum(t["pnl"] for t in realized)
    wins = [t for t in realized if t["pnl"] > 0]
    win_rate = len(wins) / len(realized) * 100 if realized else 0.0

    return {"trades": trades, "total_pnl": total_pnl, "win_rate": win_rate, "n_realized": len(realized), "n_wins": len(wins)}


def backtest_compare(symbol: str, range_start: date | None = None, range_end: date | None = None) -> None:
    """对比 SMA/EMA/VWMA 三种均线在同一套通道+成交量规则下的回测表现。"""
    print(f"===== {symbol} 周线回踩通道策略：SMA / EMA / VWMA 对比 =====")
    print(f"（均线周期{MA_PERIOD}周，通道=均线±{CHANNEL_ATR_MULT}×ATR{ATR_PERIOD}，"
          f"成交量确认=回踩周低于近{VOLUME_MA_WEEKS}周均量，${CAPITAL_PER_TRADE:,.0f}/笔）")

    raw_d = fetch_closes(symbol, "1d", apply_live=False)
    daily = wash_bars(raw_d)
    if not daily:
        print("没有拿到任何日线数据，请检查代码/网络/数据源。")
        return

    weekly = build_weekly_bars(daily)
    need_min = max(MA_PERIOD, ATR_PERIOD, VOLUME_MA_WEEKS) + MA_SLOPE_LOOKBACK + 2
    if len(weekly) < need_min:
        print(f"周线数据不够（聚合出 {len(weekly)} 根，需要至少约 {need_min} 根）")
        return

    data_first, data_last = weekly[0]["week_end"], weekly[-1]["week_end"]
    print(f"日线聚合出周线数据范围：{data_first} ~ {data_last}（共 {len(weekly)} 根周线）")

    eff_start = range_start or data_first
    eff_end = range_end or data_last
    if (range_start and range_start < data_first) or (range_end and range_end > data_last):
        print(f"⚠️  数据源实际能覆盖的范围是 {data_first} ~ {data_last}，"
              f"本次实际回测区间调整为 {max(eff_start, data_first)} ~ {min(eff_end, data_last)}")
    eff_start = max(eff_start, data_first)
    eff_end = min(eff_end, data_last)

    closes = [w["close"] for w in weekly]
    volumes = [w["volume"] for w in weekly]
    atr_series = rolling_atr(weekly, ATR_PERIOD)
    vol_avg_series = rolling_volume_avg(volumes, VOLUME_MA_WEEKS)

    ma_series_map = {
        "SMA": rolling_sma(closes, MA_PERIOD),
        "EMA": rolling_ema(closes, MA_PERIOD),
        "VWMA": rolling_vwma(closes, volumes, MA_PERIOD),
    }

    min_idx = need_min - 2  # 与 MA_PERIOD/ATR_PERIOD/VOLUME_MA_WEEKS 对齐的最小可用下标

    results = {}
    for ma_type in MA_TYPES:
        results[ma_type] = run_one_ma_backtest(
            weekly, ma_series_map[ma_type], atr_series, vol_avg_series, min_idx, eff_start, eff_end,
        )

    print()
    print(f"实际回测区间：{eff_start} ~ {eff_end}")
    print()

    for ma_type in MA_TYPES:
        r = results[ma_type]
        print(f"--- {ma_type} ---")
        if not r["trades"]:
            print("   无交易信号")
            print()
            continue
        for t in r["trades"]:
            if t["exit"] is None:
                print(f"   {t['entry']}  买入@{t['entry_price']:.3f} -> {t['reason']}（无已实现盈亏）")
            else:
                print(f"   {t['entry']} -> {t['exit']}  {t['entry_price']:.3f} -> {t['exit_price']:.3f}  "
                      f"{t['reason']}  盈亏=${t['pnl']:.2f}")
        print()

    print("========== 对比汇总 ==========")
    header = f"{'均线类型':<8} {'已实现笔数':>8} {'胜率':>8} {'总盈亏':>14} {'累计收益率':>10}"
    print(header)
    for ma_type in MA_TYPES:
        r = results[ma_type]
        n = r["n_realized"]
        wr = f"{r['win_rate']:.1f}%" if n else "—"
        pnl_str = f"${r['total_pnl']:,.2f}"
        ret_str = f"{r['total_pnl'] / CAPITAL_PER_TRADE * 100:.2f}%" if n else "—"
        print(f"{ma_type:<8} {n:>8} {wr:>8} {pnl_str:>14} {ret_str:>10}")


def parse_date_arg(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


if __name__ == "__main__":
    args = sys.argv[1:]
    sym = SYMBOL
    range_start = None
    range_end = None
    if args:
        sym = args[0].strip().upper()
        args = args[1:]
    if len(args) >= 1:
        range_start = parse_date_arg(args[0])
    if len(args) >= 2:
        range_end = parse_date_arg(args[1])
    backtest_compare(sym, range_start, range_end)