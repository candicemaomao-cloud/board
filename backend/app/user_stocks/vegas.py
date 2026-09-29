"""顺势突破 + Vegas + 日本蜡烛图形态 v2（Pine Script 翻译版）

从 TradingView Pine Script 指标翻译而来，逻辑完全对齐：
- EMA5/10/20 三线排列判断趋势
- SuperTrend 判断多空方向
- 布林带判断价格位置
- RSI 判断超买超卖
- Vegas 通道（144/169 短通道，576/676 长通道）判断大级别位置
- 单根/双根/三根以上/持续性 日本蜡烛图形态识别
- 综合以上条件生成做多/做空信号，并给出理想入场/止损/止盈1/止盈2

用法：
    bars = wash_bars(fetch_closes(symbol, "1d"))  # 或周线/其他周期
    signals = compute_signals(bars)
    print_latest(bars, signals)
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

backend = Path(__file__).resolve().parents[2]
if str(backend) not in sys.path:
    sys.path.insert(0, str(backend))

from app.services.ohlc import fetch_closes, fetch_closes_covering
from app.services.user_research import wash_bars
from app.user_stocks.tsla_golden import gradient

# ---------------------------------------------------------------------------
# 参数（对应 Pine 里的 input）
# ---------------------------------------------------------------------------

EMA5_LEN = 5
EMA10_LEN = 10
EMA20_LEN = 20
ST_FACTOR = 3.0
ST_PERIOD = 14
BB_LEN = 20
BB_MULT = 2.0
RSI_LEN = 14
RSI_OB = 70
RSI_OS = 30
BODY_MULT = 2.0  # 大K线实体倍数
BODY_STR = 1.5  # 强势实体阈值倍数
SHADOW_THR = 0.4  # 长影线占比阈值
DOJI_THR = 0.1  # 十字星实体阈值
SIDEWAYS_SPREAD_PCT = 0.15  # EMA5/EMA20 价差占比小于此值算横盘

# ---- 出场逻辑改用导数策略那套（止损=日线ATR，止盈=f''反转+放量）----
DAILY_ATR_N = 14  # 日线 ATR 窗口期数
STOP_LOSS_ATR_MULT = 0.5  # 止损空间 = 此倍数 × 交易发生当天的日线 ATR(美元)
DERIV_LOOKBACK = 5  # f'/f'' 滑窗根数（跟主图周期无关，就是当前周期的根数）
VOLUME_AVG_LOOKBACK = 12  # 计算"此前 N 根"平均成交量的窗口
VOLUME_HIGH_MULT = 1.5  # 当根成交量 >= 均量 * 此倍数，算"放量"（触发止盈确认）


# ---------------------------------------------------------------------------
# 基础指标
# ---------------------------------------------------------------------------

def ema_series(closes: list[float], period: int) -> list[float | None]:
    """指数移动平均（对应 Pine 的 ta.ema）。

    Pine 的 ta.ema 从第一根就开始输出（用第一个值做种子，逐根递推），
    不像常见实现那样要求先攒够 period 根再输出，这里保持一致。

    Args:
        closes: 收盘价序列，按时间升序。
        period: EMA 周期。

    Returns:
        与 closes 等长的列表，第 0 根就有值（种子 = closes[0]）。
    """
    if not closes:
        return []
    k = 2 / (period + 1)
    out: list[float | None] = [closes[0]]
    ema = closes[0]
    for c in closes[1:]:
        ema = c * k + ema * (1 - k)
        out.append(ema)
    return out


def true_range(bars: list[dict], i: int) -> float:
    """计算第 i 根 bar 的 True Range。"""
    bar = bars[i]
    if i < 1:
        return bar["high"] - bar["low"]
    prev_c = bars[i - 1]["close"]
    return max(bar["high"] - bar["low"], abs(bar["high"] - prev_c), abs(bar["low"] - prev_c))


def atr_series(bars: list[dict], period: int) -> list[float | None]:
    """ATR（对应 Pine 的 ta.atr，内部用 Wilder 平滑/RMA）。

    Args:
        bars: OHLC bar 列表。
        period: ATR 周期。

    Returns:
        与 bars 等长的列表，前 period-1 根为 None。
    """
    trs = [true_range(bars, i) for i in range(len(bars))]
    out: list[float | None] = [None] * len(bars)
    if len(bars) < period:
        return out
    seed = sum(trs[:period]) / period
    out[period - 1] = seed
    atr = seed
    for i in range(period, len(bars)):
        atr = (atr * (period - 1) + trs[i]) / period
        out[i] = atr
    return out


def supertrend_series(bars: list[dict], factor: float, period: int) -> tuple[list[float | None], list[int | None]]:
    """SuperTrend 指标（对应 Pine 的 ta.supertrend）。

    Args:
        bars: OHLC bar 列表。
        factor: ATR 倍数。
        period: ATR 周期。

    Returns:
        (supertrend_values, directions)：directions 中 -1 表示多头（对应 Pine 的 dir<0），
        1 表示空头；前 period-1 根为 None（ATR 未就绪）。
    """
    atr = atr_series(bars, period)
    n = len(bars)
    st: list[float | None] = [None] * n
    direction: list[int | None] = [None] * n

    for i in range(n):
        if atr[i] is None:
            continue
        hl2 = (bars[i]["high"] + bars[i]["low"]) / 2
        upper_basic = hl2 + factor * atr[i]
        lower_basic = hl2 - factor * atr[i]

        if st[i - 1] is None if i > 0 else True:
            # 第一根有效值：用 basic band 初始化，默认方向为空头（Pine 默认从 up trend 开始判断收盘位置）
            upper = upper_basic
            lower = lower_basic
            direction[i] = -1 if bars[i]["close"] > upper else 1
            st[i] = lower if direction[i] == -1 else upper
            continue

        prev_close = bars[i - 1]["close"]
        prev_st = st[i - 1]
        prev_dir = direction[i - 1]

        upper = upper_basic if (upper_basic < st[i - 1] or prev_close > st[i - 1]) and prev_dir == 1 else (
            upper_basic if prev_dir != 1 else min(upper_basic, prev_st if prev_dir == 1 else upper_basic)
        )
        # 简化实现：按标准 SuperTrend 递推规则处理上/下轨的"锁定"逻辑
        if prev_dir == 1:
            upper = min(upper_basic, prev_st) if bars[i - 1]["close"] <= prev_st else upper_basic
            lower = lower_basic
        else:
            lower = max(lower_basic, prev_st) if bars[i - 1]["close"] >= prev_st else lower_basic
            upper = upper_basic

        close = bars[i]["close"]
        if prev_dir == 1:
            new_dir = -1 if close > upper else 1
        else:
            new_dir = 1 if close < lower else -1

        direction[i] = new_dir
        st[i] = lower if new_dir == -1 else upper

    return st, direction


def bollinger_bands(closes: list[float], period: int, mult: float) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """布林带（对应 Pine 的 ta.bb）。

    Returns:
        (middle, upper, lower)，前 period-1 根为 None。
    """
    n = len(closes)
    middle: list[float | None] = [None] * n
    upper: list[float | None] = [None] * n
    lower: list[float | None] = [None] * n
    for i in range(n):
        if i < period - 1:
            continue
        window = closes[i - period + 1 : i + 1]
        m = sum(window) / period
        var = sum((x - m) ** 2 for x in window) / period
        sd = var ** 0.5
        middle[i] = m
        upper[i] = m + mult * sd
        lower[i] = m - mult * sd
    return middle, upper, lower


def rsi_series(closes: list[float], period: int) -> list[float | None]:
    """RSI（对应 Pine 的 ta.rsi，Wilder 平滑）。"""
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < period + 1:
        return out
    gains = []
    losses = []
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    out[period] = 100 - 100 / (1 + avg_gain / avg_loss) if avg_loss > 0 else 100.0
    for i in range(period + 1, n):
        diff = closes[i] - closes[i - 1]
        gain = max(diff, 0)
        loss = max(-diff, 0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = 100 - 100 / (1 + avg_gain / avg_loss) if avg_loss > 0 else 100.0
    return out


# ---------------------------------------------------------------------------
# 单根 bar 的形态/信号计算（对应 Pine 逐根计算的部分）
# ---------------------------------------------------------------------------

def _safe(bars: list[dict], i: int, key: str) -> float | None:
    """安全取值：下标越界（比如没有前3根）时返回 None。"""
    if i < 0 or i >= len(bars):
        return None
    return bars[i][key]


def compute_bar_signal(
    bars: list[dict], i: int,
    ema5: list[float | None], ema10: list[float | None], ema20: list[float | None],
    st_val: list[float | None], st_dir: list[int | None],
    bb_mid: list[float | None], bb_up: list[float | None], bb_low: list[float | None],
    rsi: list[float | None],
    vegas144: list[float | None], vegas169: list[float | None],
    vegas576: list[float | None], vegas676: list[float | None],
    avg_body: list[float | None],
) -> dict:
    """计算第 i 根 bar 的全部形态标记、趋势判断和多空信号。

    完全对照 Pine Script 的变量命名和判断逻辑，只是把"当根/前1/前2/前3根"
    显式取出来，而不是用 Pine 的 [1][2][3] 语法。

    Args:
        bars: OHLC bar 列表（需含 open/high/low/close，可选 volume）。
        i: 当前 bar 下标（需要 i>=3 才有完整的三根以上形态可判断，
           不足时相关形态标记按"不成立"处理，不会报错）。
        ema5/ema10/ema20: 对应周期的 EMA 序列。
        st_val/st_dir: SuperTrend 序列。
        bb_mid/bb_up/bb_low: 布林带序列。
        rsi: RSI 序列。
        vegas144/169/576/676: Vegas 通道用的四条 EMA 序列。
        avg_body: 20 期平均实体大小序列（sma of |close-open|）。

    Returns:
        包含所有中间标记（形态、趋势）以及最终 long_signal/short_signal、
        建议价位（entry/stop/tp1/tp2/rr）的字典。
    """
    o, h, l, c = bars[i]["open"], bars[i]["high"], bars[i]["low"], bars[i]["close"]
    o1, h1, l1, c1 = _safe(bars, i - 1, "open"), _safe(bars, i - 1, "high"), _safe(bars, i - 1, "low"), _safe(bars, i - 1, "close")
    o2, h2, l2, c2 = _safe(bars, i - 2, "open"), _safe(bars, i - 2, "high"), _safe(bars, i - 2, "low"), _safe(bars, i - 2, "close")
    o3, h3, l3 = _safe(bars, i - 3, "open"), _safe(bars, i - 3, "high"), _safe(bars, i - 3, "low")
    c3 = _safe(bars, i - 3, "close")
    l3_val = l3

    has1 = c1 is not None
    has2 = c2 is not None
    has3 = c3 is not None

    body_size = abs(c - o)
    body_size1 = abs(c1 - o1) if has1 else 0.0
    body_size2 = abs(c2 - o2) if has2 else 0.0
    body_size3 = abs(c3 - o3) if has3 else 0.0
    ab = avg_body[i] if avg_body[i] is not None else body_size  # 数据不够时退化，避免 None 参与比较报错

    total_range = h - l
    upper_shadow = h - max(o, c)
    lower_shadow = min(o, c) - l

    body_ratio = body_size / total_range if total_range > 0 else 0.0
    upper_ratio = upper_shadow / total_range if total_range > 0 else 0.0
    lower_ratio = lower_shadow / total_range if total_range > 0 else 0.0

    is_yang, is_yin = c > o, c < o
    is_yang1, is_yin1 = (c1 > o1, c1 < o1) if has1 else (False, False)
    is_yang2, is_yin2 = (c2 > o2, c2 < o2) if has2 else (False, False)
    is_yang3, is_yin3 = (c3 > o3, c3 < o3) if has3 else (False, False)

    is_big_body = body_size > ab * BODY_MULT
    is_big_body1 = body_size1 > ab * BODY_MULT
    is_big_body2 = body_size2 > ab * BODY_MULT
    is_small_body = body_size < ab * 0.5

    bb_l = bb_low[i]
    bb_u = bb_up[i]
    bb_m = bb_mid[i]
    is_near_bottom = bb_l is not None and l <= bb_l * 1.02
    is_near_top = bb_u is not None and h >= bb_u * 0.98

    max_bear_body = max(body_size if is_yin else 0.0, body_size1 if is_yin1 else 0.0, body_size2 if is_yin2 else 0.0)
    max_bull_body = max(body_size if is_yang else 0.0, body_size1 if is_yang1 else 0.0, body_size2 if is_yang2 else 0.0)

    bear_body_strong = is_yin and body_size > ab * BODY_STR
    bull_body_strong = is_yang and body_size > ab * BODY_STR
    bull_overcome_bear = is_yang and body_size > max_bear_body * 0.9
    bear_overcome_bull = is_yin and body_size > max_bull_body * 0.9

    # ---- 单根形态 ----
    is_shooting_star = is_near_top and upper_ratio > 0.6 and body_ratio < 0.2 and lower_shadow < body_size * 0.3
    is_one_line_doji = (h == l) or (abs(h - l) < ab * 0.05)
    is_cross_doji = body_ratio < DOJI_THR and total_range > 0 and upper_shadow > 0 and lower_shadow > 0
    is_lower_shadow_yin = is_yin and lower_ratio > SHADOW_THR and upper_ratio < 0.1
    is_upper_shadow_yin = is_yin and upper_ratio > SHADOW_THR and lower_ratio < 0.1
    is_big_yang = is_yang and is_big_body
    is_big_yin = is_yin and is_big_body
    is_small_yang = is_yang and is_small_body
    is_small_yin = is_yin and is_small_body
    is_upper_shadow_yang = is_yang and upper_ratio > SHADOW_THR and lower_ratio < 0.15
    is_lower_shadow_yang = is_yang and lower_ratio > SHADOW_THR and upper_ratio < 0.15

    # ---- 双根形态 ----
    is_double_crows = has1 and is_yin and is_yin1 and o > c1 and c < c1 and is_near_top
    is_bullish_piercing = has1 and is_yin1 and is_yang and o < l1 and c > (o1 + c1) / 2 and c < o1 and is_near_bottom
    is_dark_cloud_cover = has1 and is_yang1 and is_yin and o > h1 and c < (o1 + c1) / 2 and c > o1 and is_near_top
    is_bullish_engulfing = has1 and is_yin1 and is_yang and o <= c1 and c >= o1 and is_near_bottom
    is_bearish_engulfing = has1 and is_yang1 and is_yin and o >= c1 and c <= o1 and is_near_top

    # ---- 三根以上形态 ----
    recent_high1 = max((bars[j]["high"] for j in range(max(0, i - 9), i + 1)), default=h)
    recent_high2 = max((bars[j]["high"] for j in range(max(0, i - 14), max(0, i - 4) + 1)), default=h) if i >= 5 else h
    is_double_top = (
        is_near_top and bb_m is not None and c < bb_m and is_yin
        and recent_high1 and abs(recent_high1 - recent_high2) / recent_high1 < 0.005
    )
    is_head_shoulders = (
        has3 and h2 is not None and h2 > h3 and h2 > h1 and h1 < h3 * 1.02 and h1 > h3 * 0.95
        and is_yin and c < (l1 + (bars[i - 2]["low"] if has2 else l1)) / 2
    )
    is_three_white_soldiers = (
        has2 and is_yang and is_yang1 and is_yang2 and c > c1 and c1 > c2
        and body_size > ab * 0.7 and body_size1 > ab * 0.7 and body_size2 > ab * 0.7
    )
    is_morning_star = (
        has2 and is_yin2 and is_big_body2 and (body_size1 < ab * 0.3) and is_yang and is_big_body
        and c > (o2 + c2) / 2 and is_near_bottom
    )
    is_evening_star = (
        has2 and is_yang2 and is_big_body2 and (body_size1 < ab * 0.3) and is_yin and is_big_body
        and c < (o2 + c2) / 2 and is_near_top
    )
    is_three_black_crows = (
        has2 and is_yin and is_yin1 and is_yin2 and c < c1 and c1 < c2
        and is_big_body and is_big_body1 and is_big_body2
    )

    # ---- 持续形态 ----
    is_gap_down_side_by_side = (
        has2 and is_yin2 and h < l2 and is_yin1 and is_yang
        and abs(o - o1) < body_size1 * 0.3 and abs(c - c1) < body_size1 * 0.3
    )
    is_rising_three_methods = (
        has3 and is_yang3 and is_yin2 and is_yin1 and is_yin and False  # 占位，见下方修正
    )
    # 修正：Pine 原文 isRisingThreeMethods 用的是 isYang3/isYin2/isYin1/isYang（当根阳线），这里重写一次保证对齐
    is_rising_three_methods = (
        has3 and is_yang3 and is_yin2 and is_yin1 and is_yang and is_big_body
        and c > h1 and l1 is not None and l3_val is not None and l1 > l3_val
    )
    is_falling_three_methods = (
        has3 and is_yin3 and is_yang2 and is_yang1 and is_yang and is_yin and is_big_body
        and c < l1 and h1 is not None and h3 is not None and h1 < h3
    )
    is_bullish_separating_line = (
        has1 and is_yin1 and is_yang and o1 != 0 and abs(o - o1) / o1 < 0.002 and is_big_body and is_near_bottom
    )
    is_bearish_separating_line = (
        has1 and is_yang1 and is_yin and o1 != 0 and abs(o - o1) / o1 < 0.002 and is_big_body and is_near_top
    )

    bull_pattern = (
        is_bullish_piercing or is_bullish_engulfing or is_three_white_soldiers or is_morning_star
        or is_rising_three_methods or is_bullish_separating_line or is_lower_shadow_yang
        or (is_lower_shadow_yin and is_near_bottom)
    )
    bear_pattern = (
        is_shooting_star or is_double_crows or is_dark_cloud_cover or is_bearish_engulfing
        or is_double_top or is_head_shoulders or is_evening_star or is_three_black_crows
        or is_gap_down_side_by_side or is_falling_three_methods or is_bearish_separating_line
        or is_upper_shadow_yang
    )

    # ---- 趋势 & 位置判断 ----
    e5, e10, e20 = ema5[i], ema10[i], ema20[i]
    is_bull_ema = e5 is not None and e10 is not None and e20 is not None and e5 > e10 > e20
    is_bear_ema = e5 is not None and e10 is not None and e20 is not None and e5 < e10 < e20
    dir_i = st_dir[i]
    is_bull_st = dir_i is not None and dir_i < 0
    is_bear_st = dir_i is not None and dir_i > 0
    is_bull_trend = is_bull_ema and is_bull_st
    is_bear_trend = is_bear_ema and is_bear_st
    is_at_middle_band = bb_m is not None and bb_u is not None and c > bb_m and c < bb_u * 0.995

    v144, v169 = vegas144[i], vegas169[i]
    v576, v676 = vegas576[i], vegas676[i]
    v1_top = max(v144, v169) if v144 is not None and v169 is not None else None
    v1_bot = min(v144, v169) if v144 is not None and v169 is not None else None
    v2_top = max(v576, v676) if v576 is not None and v676 is not None else None
    v2_bot = min(v576, v676) if v576 is not None and v676 is not None else None

    is_above_v1 = v1_top is not None and c > v1_top
    is_below_v1 = v1_top is not None and c < v1_top * 0.998
    is_above_v2 = v2_top is not None and c > v2_top
    is_below_v2 = v2_bot is not None and c < v2_bot

    r = rsi[i]
    is_overbought = r is not None and r >= RSI_OB
    is_oversold = r is not None and r <= RSI_OS

    ema_spread = abs(e5 - e20) / c * 100 if e5 is not None and e20 is not None and c else 0.0
    is_sideways = ema_spread < SIDEWAYS_SPREAD_PCT

    long_signal = (
        (
            (is_bull_trend and is_at_middle_band and is_above_v1 and (not is_overbought)
             and (not bear_pattern) and is_yang and bull_overcome_bear and (not bear_body_strong))
            or (bull_pattern and is_bull_st and (not is_overbought) and bull_body_strong)
        ) and (not is_sideways)
    )
    short_signal = (
        (
            (is_bear_trend and (not is_at_middle_band) and is_below_v1 and (not is_oversold)
             and (not bull_pattern) and is_yin and bear_body_strong and bear_overcome_bull)
            or (bear_pattern and is_bear_st and (not is_oversold) and bear_body_strong)
        ) and (not is_sideways)
    )

    # ---- 建议价格 ----
    atr_val = None  # 由外层传入的 atr_series 决定，这里独立算一份供止损用
    result = {
        "bull_pattern": bull_pattern, "bear_pattern": bear_pattern,
        "is_bull_trend": is_bull_trend, "is_bear_trend": is_bear_trend,
        "is_bull_st": is_bull_st, "is_bear_st": is_bear_st,
        "is_at_middle_band": is_at_middle_band,
        "is_above_v1": is_above_v1, "is_below_v1": is_below_v1,
        "is_above_v2": is_above_v2, "is_below_v2": is_below_v2,
        "is_overbought": is_overbought, "is_oversold": is_oversold,
        "is_sideways": is_sideways,
        "long_signal": long_signal, "short_signal": short_signal,
        "v1_top": v1_top, "v1_bot": v1_bot, "v2_top": v2_top, "v2_bot": v2_bot,
        "bb_mid": bb_m, "bb_upper": bb_u, "bb_lower": bb_l,
        "ema5": e5, "ema10": e10, "ema20": e20,
        "rsi": r,
    }
    return result


def compute_signals(bars: list[dict]) -> list[dict]:
    """对整个 bars 序列逐根计算指标和信号，返回与 bars 等长的结果列表。

    Args:
        bars: OHLC bar 列表，按时间升序，需含 open/high/low/close（volume 可选）。

    Returns:
        与 bars 等长的字典列表，每个元素是 compute_bar_signal() 的结果，
        并额外附带该根的止损/止盈建议价位。
    """
    closes = [b["close"] for b in bars]
    ema5 = ema_series(closes, EMA5_LEN)
    ema10 = ema_series(closes, EMA10_LEN)
    ema20 = ema_series(closes, EMA20_LEN)
    st_val, st_dir = supertrend_series(bars, ST_FACTOR, ST_PERIOD)
    bb_mid, bb_up, bb_low = bollinger_bands(closes, BB_LEN, BB_MULT)
    rsi = rsi_series(closes, RSI_LEN)
    atr14 = atr_series(bars, 14)

    vegas144 = ema_series(closes, 144)
    vegas169 = ema_series(closes, 169)
    vegas576 = ema_series(closes, 576)
    vegas676 = ema_series(closes, 676)

    body_sizes = [abs(b["close"] - b["open"]) for b in bars]
    avg_body: list[float | None] = []
    for i in range(len(bars)):
        if i < 19:
            avg_body.append(None)
        else:
            avg_body.append(sum(body_sizes[i - 19 : i + 1]) / 20)

    out = []
    for i in range(len(bars)):
        sig = compute_bar_signal(
            bars, i, ema5, ema10, ema20, st_val, st_dir, bb_mid, bb_up, bb_low,
            rsi, vegas144, vegas169, vegas576, vegas676, avg_body,
        )
        atr_i = atr14[i]
        if atr_i is not None and sig["v1_top"] is not None and sig["v1_bot"] is not None and sig["bb_upper"] is not None:
            long_entry = min(sig["ema5"], sig["v1_top"])
            long_stop = sig["v1_bot"] - atr_i * 0.5
            long_tp1 = sig["bb_upper"]
            long_tp2 = sig["bb_upper"] + (sig["bb_upper"] - sig["v1_bot"])
            long_rr = ((long_tp1 - long_entry) / (long_entry - long_stop)
                       if long_entry > long_stop and long_tp1 > long_entry else 0.0)

            short_entry = max(sig["ema5"], sig["v1_bot"])
            short_stop = sig["v1_top"] + atr_i * 0.5
            short_tp1 = sig["bb_lower"]
            short_tp2 = sig["bb_lower"] - (sig["v1_top"] - sig["bb_lower"])
            short_rr = ((short_entry - short_tp1) / (short_stop - short_entry)
                        if short_entry > short_tp1 and short_stop > short_entry else 0.0)

            sig.update({
                "long_entry": long_entry, "long_stop": long_stop, "long_tp1": long_tp1, "long_tp2": long_tp2, "long_rr": long_rr,
                "short_entry": short_entry, "short_stop": short_stop, "short_tp1": short_tp1, "short_tp2": short_tp2, "short_rr": short_rr,
            })
        out.append(sig)
    return out


def print_latest(bars: list[dict], signals: list[dict]) -> None:
    """打印最新一根 bar 的完整信息面板（对应 Pine 图上那张信息表）。"""
    if not bars or not signals:
        print("没有数据")
        return
    i = len(bars) - 1
    s = signals[i]
    bar = bars[i]

    print(f"===== 最新一根：{bar.get('ts')}  收盘={bar['close']:.3f} =====")
    print(f"EMA三线：{'多头排列' if s['is_bull_trend'] or (s['ema5'] and s['ema10'] and s['ema20'] and s['ema5']>s['ema10']>s['ema20']) else '—'}"
          f"  EMA5={s['ema5']:.3f}  EMA10={s['ema10']:.3f}  EMA20={s['ema20']:.3f}" if s['ema5'] else "EMA 数据不足")
    print(f"SuperTrend：{'看多' if s['is_bull_st'] else '看空' if s['is_bear_st'] else '—'}")
    if s['bb_mid'] is not None:
        pos = "中轨上方" if s['is_at_middle_band'] else ("触及上轨" if bar['close'] >= s['bb_upper'] else ("触及下轨" if bar['close'] <= s['bb_lower'] else "中轨下方"))
        print(f"布林带：{pos}（上={s['bb_upper']:.3f} 中={s['bb_mid']:.3f} 下={s['bb_lower']:.3f}）")
    if s['rsi'] is not None:
        print(f"RSI：{'超买' if s['is_overbought'] else '超卖' if s['is_oversold'] else '正常'} ({s['rsi']:.1f})")
    if s['v1_top'] is not None:
        print(f"Vegas144/169：{'通道上方' if s['is_above_v1'] else '通道下方' if s['is_below_v1'] else '通道内部'}"
              f"（{s['v1_bot']:.3f} ~ {s['v1_top']:.3f}）")
    if s['v2_top'] is not None:
        print(f"Vegas576/676：{'通道上方' if s['is_above_v2'] else '通道下方' if s['is_below_v2'] else '通道内部'}"
              f"（{s['v2_bot']:.3f} ~ {s['v2_top']:.3f}）")
    print(f"市场状态：{'横盘震荡' if s['is_sideways'] else '趋势行情'}")
    print(f"看涨形态：{'是' if s['bull_pattern'] else '否'}  看跌形态：{'是' if s['bear_pattern'] else '否'}")
    print()
    if s["long_signal"]:
        print(f"【建议做多】理想入场={s['long_entry']:.3f}  止损={s['long_stop']:.3f}  "
              f"止盈1={s['long_tp1']:.3f}  止盈2={s['long_tp2']:.3f}  盈亏比={s['long_rr']:.2f}:1")
    elif s["short_signal"]:
        print(f"【建议做空】理想入场={s['short_entry']:.3f}  止损={s['short_stop']:.3f}  "
              f"止盈1={s['short_tp1']:.3f}  止盈2={s['short_tp2']:.3f}  盈亏比={s['short_rr']:.2f}:1")
    else:
        print("当前无明确信号，观望")


# ---------------------------------------------------------------------------
# 回测（出场逻辑替换成导数策略：止损=日线ATR，止盈=f''反转+放量）
# ---------------------------------------------------------------------------

CAPITAL_PER_TRADE = 10000.0  # 每笔交易名义本金，不复利


def daily_atr_pct(daily_bars: list[dict], n: int = DAILY_ATR_N) -> float | None:
    """计算最近 n 期日线 ATR 占最新收盘价的百分比（简单平均，非 Wilder）。"""
    if len(daily_bars) < n + 1:
        return None
    trs = [true_range(daily_bars, i) for i in range(len(daily_bars))]
    atr = sum(trs[-n:]) / n
    last_price = daily_bars[-1]["close"]
    return atr / last_price * 100 if last_price else None


def build_daily_atr_series(daily_bars: list[dict], n: int = DAILY_ATR_N) -> tuple[list, dict]:
    """把日线 bar 列表转换成"逐日滚动 ATR%"，供查询"该日之前"的止损基准用。

    对每个下标 idx（要求 idx>=n），用 daily_bars[:idx+1] 这个截止到当天
    （含当天）的窗口算一次 ATR%，得到"这一天收盘时"的 ATR% 快照。

    Args:
        daily_bars: 已清洗的日线 bar 列表，按时间升序，需含 ts/high/low/close。
        n: ATR 窗口期数。

    Returns:
        (sorted_dates, date_to_record)：sorted_dates 升序日期列表；
        date_to_record 是 {日期: {"atr_pct", "close"}}。
    """
    sorted_dates: list = []
    date_to_record: dict = {}
    for idx in range(n, len(daily_bars)):
        window = daily_bars[: idx + 1]
        pct = daily_atr_pct(window, n)
        if pct is None:
            continue
        ts = daily_bars[idx]["ts"]
        d = ts.date() if hasattr(ts, "date") else datetime.fromtimestamp(ts).date()
        sorted_dates.append(d)
        date_to_record[d] = {"atr_pct": pct, "close": daily_bars[idx]["close"]}
    return sorted_dates, date_to_record


def atr_dollar_asof(sorted_dates: list, date_to_record: dict, target_day: date, mult: float) -> float | None:
    """查询"严格早于 target_day"的最近一条 ATR 记录，换算成美元止损空间。

    用"早于"而不是"截至"，避免用到当天（乃至未来）的收盘数据决定当天的止损空间。
    """
    import bisect
    pos = bisect.bisect_left(sorted_dates, target_day)
    if pos == 0:
        return None
    d = sorted_dates[pos - 1]
    rec = date_to_record[d]
    return rec["atr_pct"] / 100 * rec["close"] * mult


def stop_hit_price(side: str, stop_price: float, bar: dict) -> float | None:
    """判断这根 bar 是否触及止损，返回实际应成交的价格（考虑跳空打滑）。

    一开盘就已经越过止损线（跳空）时，按开盘价成交，不假设能拿到理想止损价。
    """
    if side == "多":
        if bar["open"] <= stop_price:
            return bar["open"]
        if bar["low"] <= stop_price:
            return stop_price
    else:
        if bar["open"] >= stop_price:
            return bar["open"]
        if bar["high"] >= stop_price:
            return stop_price
    return None


def compute_derivative_exit_signals(bars: list[dict]) -> list[dict]:
    """逐根计算 f'/f'' 转向信号 + 成交量放大标记，用于止盈判断（原导数策略的出场逻辑）。

    Args:
        bars: OHLC bar 列表，按时间升序，需含 close/volume。

    Returns:
        与 bars 等长的列表，每个元素含 turning（""/"转多"/"转空"）和 vol_high（bool）。
    """
    closes = [b["close"] for b in bars]
    out = []
    for i in range(len(bars)):
        turning = ""
        if i >= DERIV_LOOKBACK - 1:
            window = closes[i - DERIV_LOOKBACK + 1 : i + 1]
            speed = gradient(window)
            accel = gradient(speed)
            f1 = speed[-1]
            f2 = accel[-1]
            prev = speed[-2] if len(speed) >= 2 else 0.0
            if prev < 0 and f1 >= 0 and f2 > 0:
                turning = "转多"
            elif prev > 0 and f1 <= 0 and f2 < 0:
                turning = "转空"

        vol_high = False
        if i >= VOLUME_AVG_LOOKBACK:
            hist_vols = [float(bars[j].get("volume") or 0.0) for j in range(i - VOLUME_AVG_LOOKBACK, i)]
            avg_vol = sum(hist_vols) / len(hist_vols) if hist_vols else 0.0
            cur_vol = float(bars[i].get("volume") or 0.0)
            vol_high = avg_vol > 0 and cur_vol >= avg_vol * VOLUME_HIGH_MULT

        out.append({"turning": turning, "vol_high": vol_high})
    return out


def backtest(bars: list[dict], signals: list[dict], daily_bars: list[dict]) -> list[dict]:
    """进场用 Vegas+形态信号，出场换成导数策略那套：止损=日线ATR，止盈=f''反转+放量。

    规则：
        - 进场：出现 long_signal 时若空仓，按当根收盘价开多；
          出现 short_signal 时若空仓，按当根收盘价开空（跟之前一致，未改动）。
        - 止损：入场后每根检查 high/low 是否触及止损线
          （= STOP_LOSS_ATR_MULT × 交易发生当天的日线ATR美元），触及即平仓；
          若发生跳空导致开盘就已越过止损线，按跳空后的开盘价成交。
        - 止盈：不再用固定的 TP1，改成"f''反转信号 + 成交量放大"才出场，
          不设时间限制，让盈利单充分走。
        - 同一时间只持有一个仓位。

    Args:
        bars: 用于进场信号和出场判断的 K 线（跟 signals 对应的那个周期）。
        signals: compute_signals(bars) 的返回值。
        daily_bars: 独立拉取的日线数据（已清洗），只用来算止损基准的日线ATR，
            不管 bars 本身是什么周期都用这份日线数据。

    Returns:
        交易记录列表，每条含 side/entry_idx/entry_price/exit_idx/exit_price/
        exit_reason/pnl。
    """
    atr_sorted_dates, atr_date_to_record = build_daily_atr_series(daily_bars, DAILY_ATR_N)
    exit_signals = compute_derivative_exit_signals(bars)

    def bar_date(b: dict) -> date:
        ts = b["ts"]
        return ts.date() if hasattr(ts, "date") else datetime.fromtimestamp(ts).date()

    position: dict | None = None
    trades: list[dict] = []

    for i in range(len(bars)):
        bar = bars[i]
        sig = signals[i]
        esig = exit_signals[i]

        if position is not None:
            side = position["side"]
            fill = stop_hit_price(side, position["stop"], bar)
            if fill is not None:
                shares = CAPITAL_PER_TRADE / position["entry_price"]
                pnl = (
                    shares * (fill - position["entry_price"]) if side == "多"
                    else shares * (position["entry_price"] - fill)
                )
                trades.append({
                    "side": side, "entry_idx": position["entry_idx"],
                    "entry_price": position["entry_price"], "exit_idx": i,
                    "exit_price": fill, "exit_reason": "止损", "pnl": pnl,
                })
                position = None
            elif (side == "多" and esig["turning"] == "转空") or (side == "空" and esig["turning"] == "转多"):
                if esig["vol_high"]:
                    shares = CAPITAL_PER_TRADE / position["entry_price"]
                    pnl = (
                        shares * (bar["close"] - position["entry_price"]) if side == "多"
                        else shares * (position["entry_price"] - bar["close"])
                    )
                    trades.append({
                        "side": side, "entry_idx": position["entry_idx"],
                        "entry_price": position["entry_price"], "exit_idx": i,
                        "exit_price": bar["close"], "exit_reason": "真反转+放量", "pnl": pnl,
                    })
                    position = None

        if position is None:
            stop_dollar = atr_dollar_asof(atr_sorted_dates, atr_date_to_record, bar_date(bar), STOP_LOSS_ATR_MULT)
            if sig.get("long_signal") and stop_dollar is not None:
                position = {
                    "side": "多", "entry_idx": i, "entry_price": bar["close"],
                    "stop": bar["close"] - stop_dollar,
                }
            elif sig.get("short_signal") and stop_dollar is not None:
                position = {
                    "side": "空", "entry_idx": i, "entry_price": bar["close"],
                    "stop": bar["close"] + stop_dollar,
                }

    if position is not None:
        trades.append({
            "side": position["side"], "entry_idx": position["entry_idx"],
            "entry_price": position["entry_price"], "exit_idx": None,
            "exit_price": None, "exit_reason": "区间结束仍持仓（未实现）", "pnl": 0.0,
        })

    return trades


def fmt_ts(ts) -> str:
    """把 bar 的时间戳格式化成可读日期时间，兼容"已经是 datetime"和"unix 秒数"两种情况。

    之前打印交易记录时直接输出了原始 ts（一串秒数，如 1780666200），
    不方便核对具体日期；这里统一转换成 "YYYY-MM-DD HH:MM" 格式。

    Args:
        ts: bar["ts"]，可能是 int/float（unix 秒）或已经是 datetime 对象。

    Returns:
        可读的日期时间字符串；无法解析时原样转字符串返回。
    """
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
        except (ValueError, OSError):
            return str(ts)
    if hasattr(ts, "strftime"):
        return ts.strftime("%Y-%m-%d %H:%M")
    return str(ts)


def print_backtest_result(bars: list[dict], trades: list[dict]) -> None:
    """打印回测交易明细和汇总统计（时间统一显示为可读日期，不是原始时间戳）。"""
    if not trades:
        print("回测期内没有产生任何交易（信号从未触发）。")
        return

    print(f"交易记录（共 {len(trades)} 条）：")
    for t in trades:
        entry_ts = fmt_ts(bars[t["entry_idx"]].get("ts"))
        if t["exit_idx"] is None:
            print(f"   {entry_ts}  {t['side']}  买入@{t['entry_price']:.3f} -> {t['exit_reason']}（无已实现盈亏）")
        else:
            exit_ts = fmt_ts(bars[t["exit_idx"]].get("ts"))
            print(f"   {t['side']}  {entry_ts}@{t['entry_price']:.3f} -> "
                  f"{exit_ts}@{t['exit_price']:.3f}  {t['exit_reason']}  盈亏=${t['pnl']:.2f}")

    realized = [t for t in trades if t["exit_idx"] is not None]
    if not realized:
        print("\n没有已实现的交易。")
        return

    total_pnl = sum(t["pnl"] for t in realized)
    wins = [t for t in realized if t["pnl"] > 0]
    losses = [t for t in realized if t["pnl"] <= 0]
    win_rate = len(wins) / len(realized) * 100
    long_n = sum(1 for t in realized if t["side"] == "多")
    short_n = sum(1 for t in realized if t["side"] == "空")
    stop_n = sum(1 for t in realized if t["exit_reason"] == "止损")
    tp_n = sum(1 for t in realized if t["exit_reason"] == "真反转+放量")

    print()
    print("汇总统计：")
    print(f"   已实现交易笔数：{len(realized)}（多 {long_n} / 空 {short_n}；止损 {stop_n} / 真反转止盈 {tp_n}）")
    print(f"   胜率：{win_rate:.1f}%（{len(wins)} 胜 / {len(losses)} 负）")
    print(f"   总盈亏（不复利，每笔均按 ${CAPITAL_PER_TRADE:,.0f} 本金）：${total_pnl:,.2f}")
    print(f"   累计收益率：{total_pnl / CAPITAL_PER_TRADE * 100:.2f}%")
    unrealized = [t for t in trades if t["exit_idx"] is None]
    if unrealized:
        u = unrealized[0]
        print(f"   ⚠️  区间结束时仍持仓：{u['side']}  @{u['entry_price']:.3f}（未计入上面统计）")


def parse_date_arg(s: str) -> date:
    """解析命令行传入的日期字符串（YYYY-MM-DD）。"""
    return datetime.strptime(s, "%Y-%m-%d").date()


def filter_bars_by_date(bars: list[dict], range_start: date | None, range_end: date | None) -> list[dict]:
    """按日期区间截取 bars（含首尾）。

    注意：这里只是从已经拉取到的全量 bars 里做"事后截取"，不会让
    fetch_closes 去重新按日期范围请求数据——数据源本身能覆盖多久，
    仍然取决于 fetch_closes 内部的实现和保留深度，这个函数只负责
    "在已有数据里挑出你要的这一段"，并打印实际截取到的范围。

    Args:
        bars: 完整的 bar 列表，按时间升序，需含 ts。
        range_start: 起始日期（含），None 表示不限制。
        range_end: 结束日期（含），None 表示不限制。

    Returns:
        截取后的 bar 列表。
    """
    if range_start is None and range_end is None:
        return bars

    def to_date(ts) -> date:
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts).date()
        if hasattr(ts, "date"):
            return ts.date()
        return ts

    out = []
    for b in bars:
        d = to_date(b["ts"])
        if range_start is not None and d < range_start:
            continue
        if range_end is not None and d > range_end:
            continue
        out.append(b)
    return out


if __name__ == "__main__":
    args = sys.argv[1:]
    sym = "MU"
    timeframe = "1d"
    mode = "backtest"
    range_start = None
    range_end = None

    if args:
        sym = args[0].strip().upper()
        args = args[1:]
    if args:
        timeframe = args[0]
        args = args[1:]
    # 支持两种用法：
    #   MU 30min latest                     -> 第三个参数是 mode
    #   MU 30min 2026-06-01 2026-06-30       -> 第三、四个参数是日期区间（mode 默认 backtest）
    if args and args[0] in ("latest", "backtest"):
        mode = args[0]
        args = args[1:]
    if len(args) >= 1:
        range_start = parse_date_arg(args[0])
    if len(args) >= 2:
        range_end = parse_date_arg(args[1])

    # 分钟级周期（5m/30m 这类）必须用 fetch_closes_covering + start 参数才能拿到
    # 真正连续的历史K线；fetch_closes（不带 _covering）对分钟线支持不完整，
    # 之前用它拿 30min 数据时实际返回的是按天聚合的数据（根数跟日线对不上）。
    # 日线（1d/1w 等）继续用 fetch_closes 即可。
    if timeframe in ("1d", "1w", "1mo"):
        raw = fetch_closes(sym, timeframe, apply_live=False)
    else:
        # 往前拉足够久的历史（数据源能给多少是多少，这里只是给一个"尽量早"的起点）
        fetch_start = date(2000, 1, 1)
        raw = fetch_closes_covering(sym, timeframe, start=fetch_start)
    bars_full = wash_bars(raw)

    if bars_full:
        full_start = fmt_ts(bars_full[0].get("ts"))
        full_end = fmt_ts(bars_full[-1].get("ts"))
        print(f"数据源实际能覆盖的范围：{full_start} ~ {full_end}（共 {len(bars_full)} 根）")

    bars = filter_bars_by_date(bars_full, range_start, range_end)
    if range_start or range_end:
        if not bars:
            print(f"⚠️  指定区间 {range_start} ~ {range_end} 在现有数据里截取不到任何 bar，"
                  f"请检查区间是否在上面打印的数据源范围内")
        else:
            print(f"本次实际使用区间：{fmt_ts(bars[0]['ts'])} ~ {fmt_ts(bars[-1]['ts'])}（共 {len(bars)} 根）")
    print()

    signals = compute_signals(bars)

    if mode == "latest":
        print_latest(bars, signals)
    else:
        print(f"===== {sym} 回测（{timeframe}，进场=Vegas+形态，出场=导数策略：ATR止损+f''反转放量止盈） =====")
        # 止损基准固定用日线 ATR，不管主图周期是什么，都单独拉一份日线数据
        raw_daily = fetch_closes(sym, "1d", apply_live=False)
        daily_bars = wash_bars(raw_daily)
        if not daily_bars:
            print("没有拿到日线数据，无法计算止损基准，回测终止。")
        else:
            trades = backtest(bars, signals, daily_bars)
            print_backtest_result(bars, trades)